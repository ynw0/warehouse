# -*- coding: utf-8 -*-
"""System settings and permission route registration."""

import json

from flask import g, has_request_context, jsonify, request

from warehouse_suit.permissions import role_permissions
from warehouse_suit.temporary_inventory_service import write_audit_log
from warehouse_suit.transfer_service import (
    pause_active_transfer_tasks,
    resume_paused_transfer_tasks,
)
from warehouse_suit.settings import (
    bool_setting,
    data_validation_settings,
    normalize_data_validation_settings,
    normalize_workflow_step_assignees,
    password_policy,
    set_setting,
    workflow_settings,
)


def register_system_settings_routes(app, *, get_db, require_admin, permission_keys):
    """Register workflow, validation, password, and role-permission setting endpoints."""

    def get_workflow_settings():
        conn = get_db()
        cursor = conn.cursor()
        settings = workflow_settings(cursor)
        conn.close()
        return jsonify(settings)

    def save_workflow_settings():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_admin(cursor, "admin")
            settings = workflow_settings(cursor)
            previous_temporary_enabled = bool(settings.get("temporary_inventory_enabled"))
            for key in [
                "claim_leader_same_department",
                "acceptance_leader_locked_after_first_inspect",
                "default_stocktake_reminder_day",
                "allow_multi_claim_leaders",
                "dashboard_metrics",
                "card_button_roles",
                "ai_welcome_message",
                "notification_inbound_template",
                "notification_inbound_participant_template",
                "single_item_inbound_enabled",
                "acceptance_material_photo_required",
                "acceptance_document_required",
                "temporary_inventory_material_photo_required",
                "temporary_inventory_document_required",
                "recycle_retention_days",
                "notification_retention_days",
                "duplicate_acceptance_check_days",
                "allow_manual_approval_leader",
                "temporary_inventory_enabled",
                "project_codes",
            ]:
                if key in data:
                    settings[key] = data[key]
            if "workflow_step_assignees" in data:
                settings["workflow_step_assignees"] = normalize_workflow_step_assignees(data.get("workflow_step_assignees"))
            settings["default_stocktake_reminder_day"] = int(settings.get("default_stocktake_reminder_day") or 25)
            settings["single_item_inbound_enabled"] = bool(settings.get("single_item_inbound_enabled"))
            settings["acceptance_material_photo_required"] = bool(settings.get("acceptance_material_photo_required"))
            settings["acceptance_document_required"] = bool(settings.get("acceptance_document_required"))
            settings["recycle_retention_days"] = int(settings.get("recycle_retention_days") or 30)
            settings["temporary_inventory_material_photo_required"] = bool(settings.get("temporary_inventory_material_photo_required"))
            settings["temporary_inventory_document_required"] = bool(settings.get("temporary_inventory_document_required"))
            if settings["recycle_retention_days"] not in {7, 15, 30, 60, 90}:
                settings["recycle_retention_days"] = 30
            settings["notification_retention_days"] = int(settings.get("notification_retention_days") or 90)
            if settings["notification_retention_days"] not in {7, 15, 30, 90, 180}:
                settings["notification_retention_days"] = 90
            settings["duplicate_acceptance_check_days"] = int(settings.get("duplicate_acceptance_check_days") or 7)
            if settings["duplicate_acceptance_check_days"] not in {7, 15, 30, 90}:
                settings["duplicate_acceptance_check_days"] = 7
            settings["allow_manual_approval_leader"] = bool(settings.get("allow_manual_approval_leader"))
            settings["temporary_inventory_enabled"] = bool_setting(settings.get("temporary_inventory_enabled"))
            if isinstance(settings.get("project_codes"), str):
                settings["project_codes"] = [item.strip() for item in settings["project_codes"].replace("；", ",").split(",") if item.strip()]
            set_setting(cursor, "workflow_settings", json.dumps(settings, ensure_ascii=False))
            if previous_temporary_enabled != settings["temporary_inventory_enabled"]:
                ip_address = request.headers.get(
                    "X-Forwarded-For", request.remote_addr or ""
                )
                if settings["temporary_inventory_enabled"]:
                    resume_paused_transfer_tasks(cursor, user, ip_address)
                else:
                    pause_active_transfer_tasks(cursor, user, ip_address)
                write_audit_log(
                    cursor,
                    user,
                    "temporary_inventory.toggle",
                    "app_setting",
                    "workflow_settings",
                    "启用临时库功能" if settings["temporary_inventory_enabled"] else "关闭临时库功能",
                    {
                        "temporary_inventory_enabled": settings["temporary_inventory_enabled"],
                        "previous_value": previous_temporary_enabled,
                    },
                    ip_address,
                )
            conn.commit()
        except PermissionError as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 403
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "settings": settings})

    def get_data_validation_settings():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        settings = data_validation_settings(cursor)
        conn.close()
        return jsonify(settings)

    def save_data_validation_settings():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            settings = normalize_data_validation_settings(data if isinstance(data, dict) else {})
            set_setting(cursor, "data_validation_settings", json.dumps(settings, ensure_ascii=False))
            conn.commit()
            if has_request_context():
                g._data_validation_settings = settings
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "settings": settings})

    def get_password_policy():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        policy = password_policy(cursor)
        conn.close()
        return jsonify(policy)

    def save_password_policy():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            policy = {
                "min_length": max(1, int(float(data.get("min_length") or 6))),
                "require_digit": bool(data.get("require_digit")),
                "require_lower": bool(data.get("require_lower")),
                "require_upper": bool(data.get("require_upper")),
                "require_symbol": bool(data.get("require_symbol")),
                "force_change_on_first_login": bool(data.get("force_change_on_first_login")),
            }
            set_setting(cursor, "password_policy", json.dumps(policy, ensure_ascii=False))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "policy": policy})

    def get_role_permissions():
        conn = get_db()
        cursor = conn.cursor()
        permissions = role_permissions(cursor)
        conn.close()
        return jsonify({"permissions": permissions, "permission_keys": permission_keys})

    def save_role_permissions():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            permissions = {}
            incoming = data.get("permissions") or {}
            cursor.execute("SELECT code FROM roles")
            role_codes = [row[0] for row in cursor.fetchall()]
            for role in role_codes:
                permissions[role] = {}
                for key in permission_keys:
                    permissions[role][key] = bool((incoming.get(role) or {}).get(key))
                if permissions[role].get("write_semifinished_inventory"):
                    permissions[role]["read_semifinished_inventory"] = False
                if permissions[role].get("write_finished_inventory"):
                    permissions[role]["read_finished_inventory"] = False
                if permissions[role].get("manage_temporary_inventory"):
                    permissions[role]["view_temporary_inventory"] = True
            permissions["admin"] = {key: True for key in permission_keys}
            set_setting(cursor, "role_permissions", json.dumps(permissions, ensure_ascii=False))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "permissions": permissions})

    app.add_url_rule("/api/system/workflow-settings", "get_workflow_settings", get_workflow_settings, methods=["GET"])
    app.add_url_rule("/api/system/workflow-settings", "save_workflow_settings", save_workflow_settings, methods=["POST"])
    app.add_url_rule("/api/system/data-validation", "get_data_validation_settings", get_data_validation_settings, methods=["GET"])
    app.add_url_rule("/api/system/data-validation", "save_data_validation_settings", save_data_validation_settings, methods=["POST"])
    app.add_url_rule("/api/system/password-policy", "get_password_policy", get_password_policy, methods=["GET"])
    app.add_url_rule("/api/system/password-policy", "save_password_policy", save_password_policy, methods=["POST"])
    app.add_url_rule("/api/system/role-permissions", "get_role_permissions", get_role_permissions, methods=["GET"])
    app.add_url_rule("/api/system/role-permissions", "save_role_permissions", save_role_permissions, methods=["POST"])
