# -*- coding: utf-8 -*-
"""System bootstrap route registration."""

from flask import jsonify

from warehouse_suit.auth_service import public_user
from warehouse_suit.db import next_stocktake_due_date, today_text
from warehouse_suit.inventory_constants import INVENTORY_STATUS_AVAILABLE, STOCK_SOURCE_FORMAL
from warehouse_suit.permissions import role_permissions
from warehouse_suit.settings import (
    data_validation_settings,
    get_setting,
    temporary_inventory_enabled,
    workflow_settings,
)
from warehouse_suit.temporary_inventory_visibility import temporary_workflow_sql
from warehouse_suit.todo_service import cleanup_notifications, pending_tasks_for_user, unread_notification_count
from warehouse_suit.workflow_service import user_has_permission


def register_system_bootstrap_routes(app, *, get_db, current_user_provider, ai_enabled, permission_keys):
    """Register system bootstrap endpoint used by the Web UI."""

    def system_bootstrap():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        cleanup_notifications(cursor)
        cursor.execute(
            """
            SELECT u.id, u.username, u.display_name, u.department, u.is_active, u.must_change_password,
                   u.created_at, u.updated_at,
                   GROUP_CONCAT(r.code) AS role_codes, GROUP_CONCAT(r.name) AS role_names
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = 1
            GROUP BY u.id
            ORDER BY u.id
            """
        )
        users = [dict(row) for row in cursor.fetchall()]
        permissions = role_permissions(cursor)
        settings = workflow_settings(cursor)
        temporary_enabled = temporary_inventory_enabled(cursor)
        if user and "admin" not in user.get("role_codes", []):
            can_pick_people = user_has_permission(cursor, user, "start_acceptance") or user_has_permission(cursor, user, "start_claim")
            allowed_people_roles = {"leader", "warehouse"}
            users = [
                item
                for item in users
                if item["id"] == user["id"]
                or (
                    "admin" not in set(str(item.get("role_codes") or "").split(","))
                    and (can_pick_people or allowed_people_roles.intersection(set(str(item.get("role_codes") or "").split(","))))
                )
            ]
        cursor.execute("SELECT * FROM roles ORDER BY id")
        roles = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM departments ORDER BY id")
        departments = [dict(row) for row in cursor.fetchall()]
        can_view_stats = bool(user)
        active_forms = active_batches = total_quantity = total_amount = material_count = 0
        rd_quantity = office_quantity = today_in = today_out = month_in = month_out = 0
        if can_view_stats:
            active_where = ["f.status NOT IN (?, ?)"]
            active_params = ["completed", "cancelled"]
            if not temporary_enabled:
                temporary_sql, temporary_params = temporary_workflow_sql("f")
                active_where.append(f"NOT {temporary_sql}")
                active_params.extend(temporary_params)
            cursor.execute(
                f"SELECT COUNT(*) FROM workflow_forms f WHERE {' AND '.join(active_where)}",
                active_params,
            )
            active_forms = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM material_batches WHERE stock_source = ? AND inventory_status = ? AND quantity > 0", (STOCK_SOURCE_FORMAL, INVENTORY_STATUS_AVAILABLE))
            active_batches = cursor.fetchone()[0]
            cursor.execute("SELECT COALESCE(SUM(quantity), 0), COALESCE(SUM(amount), 0) FROM inventory")
            total_quantity, total_amount = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM materials")
            material_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT COALESCE(SUM(i.quantity), 0)
                FROM inventory i
                JOIN materials m ON m.id = i.material_id
                LEFT JOIN material_positions mp ON mp.material_id = m.id
                LEFT JOIN shelves s ON s.id = mp.shelf_id
                WHERE s.warehouse_type = ?
                """,
                ("rd",),
            )
            rd_quantity = cursor.fetchone()[0] or 0
            cursor.execute(
                """
                SELECT COALESCE(SUM(i.quantity), 0)
                FROM inventory i
                JOIN materials m ON m.id = i.material_id
                LEFT JOIN material_positions mp ON mp.material_id = m.id
                LEFT JOIN shelves s ON s.id = mp.shelf_id
                WHERE COALESCE(s.warehouse_type, ?) = ?
                """,
                ("office", "office"),
            )
            office_quantity = cursor.fetchone()[0] or 0
            month_start = today_text()[:7] + "-01"
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = ? AND operation_date = ?", (STOCK_SOURCE_FORMAL, "in", today_text()))
            today_in = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = ? AND operation_date = ?", (STOCK_SOURCE_FORMAL, "out", today_text()))
            today_out = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = ? AND operation_date >= ?", (STOCK_SOURCE_FORMAL, "in", month_start))
            month_in = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = ? AND operation_date >= ?", (STOCK_SOURCE_FORMAL, "out", month_start))
            month_out = cursor.fetchone()[0] or 0
        todos = pending_tasks_for_user(cursor, user)
        recent_sql = """
            SELECT f.*, u.display_name AS applicant_name
            FROM workflow_forms f
            LEFT JOIN users u ON u.id = f.applicant_id
        """
        recent_where = []
        recent_params = []
        if user and "admin" not in user.get("role_codes", []):
            recent_where.append(
                """
                (
                    f.applicant_id = ?
                    OR f.leader_id = ?
                    OR f.warehouse_user_id = ?
                    OR EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.form_id = f.id AND t.assignee_id = ?)
                )
                """
            )
            recent_params = [user["id"], user["id"], user["id"], user["id"]]
        if not temporary_enabled:
            temporary_sql, temporary_params = temporary_workflow_sql("f")
            recent_where.append(f"NOT {temporary_sql}")
            recent_params.extend(temporary_params)
        if recent_where:
            recent_sql += " WHERE " + " AND ".join(recent_where)
        recent_sql += " ORDER BY f.id DESC LIMIT 12"
        cursor.execute(recent_sql, recent_params)
        recent_forms = [dict(row) for row in cursor.fetchall()]
        validation_settings = data_validation_settings(cursor)
        user_permissions = {key: user_has_permission(cursor, user, key) for key in permission_keys}
        next_stocktake_date = get_setting(cursor, "next_stocktake_date", next_stocktake_due_date(settings.get("default_stocktake_reminder_day", 25)))
        unread_notifications = unread_notification_count(cursor, user["id"]) if user else 0
        conn.commit()
        conn.close()
        return jsonify(
            {
                "user": public_user(user),
                "users": users,
                "roles": roles,
                "departments": departments,
                "ai_enabled": ai_enabled,
                "workflow_settings": settings,
                "data_validation_settings": validation_settings,
                "next_stocktake_date": next_stocktake_date,
                "role_permissions": permissions,
                "user_permissions": user_permissions,
                "stats": {
                    "active_forms": active_forms,
                    "active_batches": active_batches,
                    "total_quantity": total_quantity or 0,
                    "total_amount": total_amount or 0,
                    "materials": material_count,
                    "rd_quantity": rd_quantity,
                    "office_quantity": office_quantity,
                    "today_in": today_in,
                    "today_out": today_out,
                    "month_in": month_in,
                    "month_out": month_out,
                },
                "todos": todos,
                "unread_notifications": unread_notifications,
                "recent_forms": recent_forms,
            }
        )

    app.add_url_rule("/api/system/bootstrap", "system_bootstrap", system_bootstrap, methods=["GET"])
