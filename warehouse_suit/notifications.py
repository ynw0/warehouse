"""Notification creation and formatting helpers."""

from __future__ import annotations

import json

from .db import now_text
from .settings import workflow_settings


NOTIFICATION_CATEGORIES = ("inbound", "stock_warning", "supply", "transfer", "assistant")

_TITLE_CATEGORIES = {
    "物料入库通知": "inbound",
    "常用物料低库存预警": "stock_warning",
    "供货结清逾期提醒": "supply",
    "转借请求": "transfer",
    "转借已接受": "transfer",
    "转借已拒绝": "transfer",
}


def create_notification(cursor, user_id, title, body, data=None, category=""):
    category = str(category or "").strip()
    if category not in NOTIFICATION_CATEGORIES:
        raise ValueError("通知分类不正确")
    created_at = now_text()
    cursor.execute(
        """
        INSERT INTO notifications (user_id, title, body, data_json, category, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            str(title or ""),
            str(body or ""),
            json.dumps(data or {}, ensure_ascii=False),
            category,
            created_at,
        ),
    )
    try:
        cursor.execute("INSERT INTO workflow_events (event_type, created_at) VALUES ('notifications', ?)", (created_at,))
        cursor.execute(
            "DELETE FROM workflow_events WHERE id NOT IN (SELECT id FROM workflow_events ORDER BY id DESC LIMIT 1000)"
        )
    except Exception:
        pass


def backfill_notification_categories(cursor):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notifications'")
    if not cursor.fetchone():
        return
    cursor.execute('PRAGMA table_info("notifications")')
    columns = {row[1] for row in cursor.fetchall()}
    if "category" not in columns or "title" not in columns:
        return
    for title, category in _TITLE_CATEGORIES.items():
        cursor.execute(
            "UPDATE notifications SET category = ? WHERE title = ? AND COALESCE(category, '') = ''",
            (category, title),
        )
    cursor.execute(
        "SELECT COUNT(*) FROM notifications WHERE COALESCE(category, '') = '' AND TRIM(COALESCE(title, '')) <> ''"
    )
    leftover = int(cursor.fetchone()[0] or 0)
    if leftover:
        raise ValueError("存在无法归类的历史通知，不能继续升级")


def format_notification_template(template, item):
    values = {
        "name": item.get("material_name") or item.get("name") or "",
        "material_name": item.get("material_name") or item.get("name") or "",
        "material_code": item.get("material_code") or "",
        "brand_model": item.get("brand_model") or "",
        "spec": item.get("spec") or "",
        "unit": item.get("unit") or "",
    }
    try:
        return str(template or "").format(**values)
    except Exception:
        return f"{values['material_name']} {values['brand_model']} {values['spec']} 已入库，请按需领取。".strip()


def acceptance_participant_ids(cursor, form_id):
    cursor.execute(
        """
        SELECT DISTINCT assignee_id
        FROM workflow_tasks
        WHERE form_id = ? AND step_code = 'acceptance' AND assignee_id IS NOT NULL
        """,
        (form_id,),
    )
    return {int(row["assignee_id"]) for row in cursor.fetchall()}


def notify_material_inbound(cursor, form_id, item):
    settings = workflow_settings(cursor)
    normal_template = settings.get("notification_inbound_template") or "有新的物料：“{name}”“{brand_model}”“{spec}”入库了，请按需领取。"
    participant_template = settings.get("notification_inbound_participant_template") or "您验收的物料：“{name}”“{brand_model}”“{spec}”已入库，请按需领取。"
    participant_ids = acceptance_participant_ids(cursor, form_id)
    cursor.execute("SELECT id FROM users WHERE is_active = 1")
    for user_row in cursor.fetchall():
        target_user_id = int(user_row["id"])
        template = participant_template if target_user_id in participant_ids else normal_template
        create_notification(
            cursor,
            target_user_id,
            "物料入库通知",
            format_notification_template(template, item),
            {"form_id": form_id, "material_id": item.get("material_id"), "material_code": item.get("material_code")},
            category="inbound",
        )

