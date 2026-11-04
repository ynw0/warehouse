"""Unsubmitted start-page drafts stored outside workflow_forms."""

from __future__ import annotations

import json

from warehouse_suit.acceptance_service import execute_create_acceptance
from warehouse_suit.borrow_service import execute_create_borrow
from warehouse_suit.claim_service import execute_create_claim
from warehouse_suit.db import now_text
from warehouse_suit.extended_service import create_supply_order
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.production_service import execute_create_production
from warehouse_suit.settings import parse_json
from warehouse_suit.temporary_inventory_service import write_audit_log
from warehouse_suit.workflow_service import require_any_permission, user_has_permission


DRAFT_FORM_TYPES = (
    "acceptance",
    "semifinished",
    "finished",
    "claim",
    "borrow",
    "supply",
    "purchase_request",
    "special_claim",
)
DRAFT_PERMISSIONS = {
    "acceptance": "start_acceptance",
    "semifinished": "start_acceptance",
    "finished": "start_acceptance",
    "claim": "start_claim",
    "borrow": "start_borrow",
    "supply": "start_supply",
    "purchase_request": "start_purchase_request",
    "special_claim": "start_special_claim",
}


def allowed_draft_form_types(cursor, user):
    """当前用户能读写的草稿类型。"""
    return [
        form_type
        for form_type, permission in DRAFT_PERMISSIONS.items()
        if user_has_permission(cursor, user, permission)
    ]


def require_draft_access(cursor):
    return require_any_permission(
        cursor,
        "start_acceptance",
        "start_claim",
        "start_borrow",
        "start_supply",
        "start_purchase_request",
        "start_special_claim",
    )


def validate_form_type(form_type):
    value = str(form_type or "").strip()
    if value not in DRAFT_FORM_TYPES:
        raise ValueError("草稿类型不正确")
    return value


def validate_payload(payload):
    if payload is None:
        raise ValueError("草稿内容必须是对象")
    if not isinstance(payload, dict):
        raise ValueError("草稿内容必须是对象")
    return payload


def derive_draft_title(form_type, payload):
    """列表标题从报文推导，不单独让用户填写。"""
    payload = payload or {}
    title = str(payload.get("title") or "").strip()
    if title:
        return title[:200]
    if form_type == "supply":
        recipient = payload.get("recipient")
        if not isinstance(recipient, dict):
            recipient = {}
        company = str(recipient.get("company") or payload.get("company") or "").strip()
        if company:
            return company[:200]
    if form_type in {"semifinished", "finished"}:
        name = str(payload.get("name") or payload.get("product_name") or "").strip()
        if name:
            return name[:200]
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        items = payload.get("components") or []
    if isinstance(items, list) and items:
        first = items[0] if isinstance(items[0], dict) else {}
        name = str(
            first.get("material_name")
            or first.get("item_name")
            or first.get("name")
            or ""
        ).strip()
        if name:
            return name[:200]
    return ""


def serialize_draft(row):
    data = dict(row)
    payload = parse_json(data.get("payload_json"), {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": int(data["id"]),
        "applicant_id": int(data["applicant_id"]),
        "form_type": data.get("form_type") or "",
        "title": data.get("title") or "",
        "payload": payload,
        "created_at": data.get("created_at") or "",
        "updated_at": data.get("updated_at") or "",
    }


def _load_draft(cursor, draft_id):
    cursor.execute("SELECT * FROM workflow_drafts WHERE id = ?", (int(draft_id),))
    row = cursor.fetchone()
    if not row:
        raise LookupError("草稿不存在")
    return dict(row)


def require_owned_draft(cursor, user, draft_id):
    row = _load_draft(cursor, draft_id)
    if int(row["applicant_id"]) != int(user["id"]):
        raise PermissionError("无权操作该草稿")
    return row


def require_form_type_permission(cursor, user, form_type):
    permission = DRAFT_PERMISSIONS[validate_form_type(form_type)]
    if not user_has_permission(cursor, user, permission):
        raise PermissionError("当前账号没有该操作权限")
    return permission


def list_drafts(cursor, user):
    form_types = allowed_draft_form_types(cursor, user)
    if not form_types:
        return []
    placeholders = ",".join("?" for _ in form_types)
    cursor.execute(
        f"""
        SELECT *
        FROM workflow_drafts
        WHERE applicant_id = ? AND form_type IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        """,
        (int(user["id"]), *form_types),
    )
    return [serialize_draft(row) for row in cursor.fetchall()]


def get_draft(cursor, user, draft_id):
    row = require_owned_draft(cursor, user, draft_id)
    require_form_type_permission(cursor, user, row["form_type"])
    return serialize_draft(row)


def save_draft(cursor, user, form_type, payload, draft_id=None):
    form_type = validate_form_type(form_type)
    require_form_type_permission(cursor, user, form_type)
    payload = validate_payload(payload)
    title = derive_draft_title(form_type, payload)
    payload_json = json.dumps(payload, ensure_ascii=False)
    timestamp = now_text()
    if draft_id:
        row = require_owned_draft(cursor, user, draft_id)
        require_form_type_permission(cursor, user, row["form_type"])
        cursor.execute(
            """
            UPDATE workflow_drafts
            SET form_type = ?, title = ?, payload_json = ?, updated_at = ?
            WHERE id = ? AND applicant_id = ?
            """,
            (form_type, title, payload_json, timestamp, int(draft_id), int(user["id"])),
        )
        if cursor.rowcount != 1:
            raise LookupError("草稿不存在")
    else:
        cursor.execute(
            """
            INSERT INTO workflow_drafts
                (applicant_id, form_type, title, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(user["id"]), form_type, title, payload_json, timestamp, timestamp),
        )
        draft_id = int(cursor.lastrowid)
    write_audit_log(
        cursor,
        user,
        "draft.save",
        "workflow_draft",
        draft_id,
        title or form_type,
        {"form_type": form_type, "title": title},
    )
    return get_draft(cursor, user, draft_id)


def delete_draft(cursor, user, draft_id):
    row = require_owned_draft(cursor, user, draft_id)
    require_form_type_permission(cursor, user, row["form_type"])
    cursor.execute(
        "DELETE FROM workflow_drafts WHERE id = ? AND applicant_id = ?",
        (int(draft_id), int(user["id"])),
    )
    if cursor.rowcount != 1:
        raise LookupError("草稿不存在")
    write_audit_log(
        cursor,
        user,
        "draft.delete",
        "workflow_draft",
        draft_id,
        row.get("title") or row.get("form_type") or "",
        {"form_type": row.get("form_type") or ""},
    )


def _create_from_draft(cursor, user, form_type, payload, optional_active_user_id):
    if form_type == "acceptance":
        return execute_create_acceptance(cursor, user, payload)
    if form_type == "claim":
        return execute_create_claim(cursor, user, payload)
    if form_type == "borrow":
        return execute_create_borrow(cursor, user, payload)
    if form_type == "supply":
        # 提交走现有供货创建，返回 workflow_forms.id
        form = create_supply_order(cursor, user, payload, payload.get("leader_id"))
        return int(form["id"])
    if form_type == "purchase_request":
        from warehouse_suit.purchase_request_service import create_purchase_request

        return create_purchase_request(cursor, user, payload)
    if form_type == "special_claim":
        from warehouse_suit.special_claim_service import create_special_claim

        return create_special_claim(cursor, user, payload)
    return execute_create_production(cursor, user, form_type, payload, optional_active_user_id)


def submit_draft(cursor, user, draft_id, optional_active_user_id):
    """同一事务走现有创建逻辑，成功后删除草稿。"""
    row = require_owned_draft(cursor, user, draft_id)
    form_type = validate_form_type(row["form_type"])
    require_form_type_permission(cursor, user, form_type)
    payload = parse_json(row.get("payload_json"), {})
    payload = validate_payload(payload)
    begin_inventory_transaction(cursor.connection)
    form_id = _create_from_draft(cursor, user, form_type, payload, optional_active_user_id)
    cursor.execute(
        "DELETE FROM workflow_drafts WHERE id = ? AND applicant_id = ?",
        (int(draft_id), int(user["id"])),
    )
    write_audit_log(
        cursor,
        user,
        "draft.submit",
        "workflow_draft",
        draft_id,
        row.get("title") or form_type,
        {"form_type": form_type, "form_id": form_id},
    )
    return form_id
