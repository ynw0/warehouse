# -*- coding: utf-8 -*-
"""Workflow todo and notification query services."""

import queue
import sqlite3
from datetime import datetime, timedelta

from warehouse_suit.db import now_text
from warehouse_suit.settings import parse_json, temporary_inventory_enabled, workflow_settings
from warehouse_suit.workflow_service import user_has_permission
from warehouse_suit.temporary_inventory_visibility import (
    append_notification_visibility,
    append_workflow_visibility,
)


_db_provider = None
_user_by_id_provider = None
_sse_clients_snapshot_provider = None


def configure_todo_service(db_provider, user_by_id_provider, sse_clients_snapshot_provider):
    global _db_provider, _user_by_id_provider, _sse_clients_snapshot_provider
    _db_provider = db_provider
    _user_by_id_provider = user_by_id_provider
    _sse_clients_snapshot_provider = sse_clients_snapshot_provider


def _get_db():
    if _db_provider is None:
        raise RuntimeError("database provider is not configured")
    return _db_provider()


def _user_by_id(cursor, user_id):
    if _user_by_id_provider is None:
        raise RuntimeError("user lookup provider is not configured")
    return _user_by_id_provider(cursor, user_id)


def _sse_clients_snapshot():
    if _sse_clients_snapshot_provider is None:
        return []
    return _sse_clients_snapshot_provider()


def cleanup_notifications(cursor):
    try:
        days = int(workflow_settings(cursor).get("notification_retention_days") or 90)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("DELETE FROM notifications WHERE created_at <= ?", (cutoff,))
    except sqlite3.Error:
        pass

def notify_todos_changed():
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO workflow_events (event_type, created_at) VALUES ('todos', ?)", (now_text(),))
        cursor.execute(
            "DELETE FROM workflow_events WHERE id NOT IN (SELECT id FROM workflow_events ORDER BY id DESC LIMIT 1000)"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    clients = _sse_clients_snapshot()
    for client in clients:
        try:
            client.put_nowait("changed")
        except queue.Full:
            pass


def latest_workflow_event_id():
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM workflow_events")
        value = int(cursor.fetchone()[0] or 0)
        conn.close()
        return value
    except Exception:
        return 0


def _temporary_transfer_todos(cursor, user, limit):
    if not temporary_inventory_enabled(cursor) or not user_has_permission(
        cursor, user, "process_temporary_transfer"
    ):
        return []
    where = [
        "t.status IN ('awaiting_purchase', 'acceptance_failed', "
        "'formal_inbound_partial', 'exception')"
    ]
    params = []
    if "admin" not in user.get("role_codes", []):
        where.append("(t.assigned_buyer_id IS NULL OR t.assigned_buyer_id = ?)")
        params.append(int(user["id"]))
    cursor.execute(
        f"""
        SELECT t.id AS task_id, 'temporary_transfer' AS step_code,
               t.id AS form_id, t.transfer_no AS form_no,
               'temporary_transfer' AS form_type,
               ('临时物料转正式库：' || m.name) AS title,
               t.status, t.updated_at,
               requester.display_name AS applicant_name
        FROM inventory_transfer_tasks t
        JOIN materials m ON m.id = t.material_id
        LEFT JOIN users requester ON requester.id = t.requested_by
        WHERE {' AND '.join(where)}
        ORDER BY t.updated_at DESC, t.id DESC
        LIMIT ?
        """,
        [*params, int(limit)],
    )
    return [dict(row) for row in cursor.fetchall()]


def pending_tasks_for_user(cursor, user, limit=50):
    if not user:
        return []
    params = []
    where = ["t.status = 'pending'"]
    if "admin" not in user.get("role_codes", []):
        where.append("t.assignee_id = ?")
        params.append(user["id"])
    append_workflow_visibility(cursor, where, params, "f")
    cursor.execute(
        f"""
        SELECT t.id AS task_id, t.step_code, f.id AS form_id, f.form_no, f.form_type,
               f.title, f.status, f.updated_at, u.display_name AS applicant_name
        FROM workflow_tasks t
        JOIN workflow_forms f ON f.id = t.form_id
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE {' AND '.join(where)}
        ORDER BY f.updated_at DESC, t.id DESC
        LIMIT ?
        """,
        params + [limit],
    )
    todos = [dict(row) for row in cursor.fetchall()]
    stock_where = ["status = 'supervisor'"]
    stock_params = []
    if "admin" not in user.get("role_codes", []):
        stock_where.append("supervisor_id = ?")
        stock_params.append(user["id"])
    cursor.execute(
        f"""
        SELECT id AS task_id, 'stocktake_supervisor' AS step_code, id AS form_id, form_no,
               'stocktake' AS form_type, form_no AS title, status, updated_at,
               checker_signature AS applicant_name
        FROM stocktake_forms
        WHERE {' AND '.join(stock_where)}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        stock_params + [limit],
    )
    todos.extend(dict(row) for row in cursor.fetchall())
    todos.extend(_temporary_transfer_todos(cursor, user, limit))
    return sorted(todos, key=lambda item: item.get("updated_at") or "", reverse=True)[:limit]


def todos_payload_for_user_id(user_id):
    conn = _get_db()
    cursor = conn.cursor()
    user = _user_by_id(cursor, user_id)
    items = pending_tasks_for_user(cursor, user)
    for item in items:
        item["notification_title"] = f"{item['form_no']} {item.get('title') or ''}".strip()
        item["notification_body"] = f"待办理：{item['step_code']}，当前状态：{item['status']}"
    notifications = notification_rows(cursor, user_id, read=False, limit=50)
    conn.close()
    return {"items": items, "notifications": notifications, "count": len(items), "server_time": now_text()}


def unread_notification_count(cursor, user_id):
    where = ["n.user_id = ?", "n.is_read = 0"]
    params = [user_id]
    append_notification_visibility(cursor, where, params, "n")
    cursor.execute(
        f"SELECT COUNT(*) FROM notifications n WHERE {' AND '.join(where)}",
        params,
    )
    return int(cursor.fetchone()[0] or 0)


def notification_rows(cursor, user_id, read=False, limit=100):
    where = ["n.user_id = ?", "n.is_read = ?"]
    params = [user_id, 1 if read else 0]
    append_notification_visibility(cursor, where, params, "n")
    cursor.execute(
        f"""
        SELECT n.*
        FROM notifications n
        WHERE {' AND '.join(where)}
        ORDER BY n.id DESC
        LIMIT ?
        """,
        [*params, int(limit or 100)],
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(row)
        item["data"] = parse_json(item.pop("data_json", "{}"), {})
        item["is_read"] = bool(item.get("is_read"))
        rows.append(item)
    return rows
