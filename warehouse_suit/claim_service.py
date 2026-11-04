"""Claim-specific transaction helpers shared by formal and temporary stock."""

from __future__ import annotations

import json

from warehouse_suit.claim_allocation_service import allocate_claim_items, insert_claim_allocations
from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_TEMPORARY
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.numbering import next_form_no
from warehouse_suit.purchase_project import apply_purpose_payload, require_same_purchase_project
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    resolve_department_leader,
    workflow_generated_title,
)

# 申领单流程属性。实物交接是默认；流程流转只作标记，不改审批和出库。
CLAIM_FLOW_HANDOVER = "实物交接"
CLAIM_FLOW_TRANSFER = "流程流转"
CLAIM_FLOW_ATTRIBUTES = (CLAIM_FLOW_HANDOVER, CLAIM_FLOW_TRANSFER)


def claim_flow_attribute(value):
    text = str(value or "").strip()
    if not text:
        return CLAIM_FLOW_HANDOVER
    if text not in CLAIM_FLOW_ATTRIBUTES:
        raise ValueError("流程属性只能是实物交接或流程流转")
    return text


def create_claim_workflow(
    cursor,
    applicant,
    allocations,
    leader_id,
    *,
    leader_ids=None,
    purpose="办公",
    rd_item_kind="",
    project_material_kind="",
    project_code="",
    purchase_project="",
    origin_type="manual",
    origin_ref_id=None,
    metadata=None,
    immutable=False,
):
    """Create one claim form inside the caller-owned transaction."""
    if not allocations:
        raise ValueError("申领单至少需要一行物料")
    leader_ids = list(dict.fromkeys(int(value) for value in (leader_ids or [leader_id])))
    if not leader_ids:
        leader_ids = [int(leader_id)]
    form_no = next_form_no(cursor, "CK")
    data = {
        "department": applicant.get("department") or "",
        "leader_ids": leader_ids,
        "purpose": str(purpose or "办公"),
        "rd_item_kind": str(rd_item_kind or ""),
        "project_material_kind": str(project_material_kind or ""),
        "project_code": str(project_code or ""),
        "purchase_project": str(purchase_project or ""),
        "auto_generated": origin_type != "manual",
        "immutable": bool(immutable),
    }
    data.update(dict(metadata or {}))
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, leader_id,
             data_json, origin_type, origin_ref_id, created_at, updated_at)
        VALUES (?, 'claim', ?, 'leader_claim', 'leader_claim', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            workflow_generated_title(applicant, form_no),
            int(applicant["id"]),
            int(leader_id),
            json.dumps(data, ensure_ascii=False),
            str(origin_type or "manual"),
            int(origin_ref_id) if origin_ref_id is not None else None,
            now_text(),
            now_text(),
        ),
    )
    form_id = int(cursor.lastrowid)
    item_ids = insert_claim_allocations(cursor, form_id, allocations)
    create_workflow_tasks(cursor, form_id, "claim", "leader_claim", leader_ids)
    return {
        "form_id": form_id,
        "form_no": form_no,
        "item_ids": item_ids,
    }


def create_temporary_issue_obligations(cursor, form, item, consumed_batches):
    if item.get("stock_source") != STOCK_SOURCE_TEMPORARY:
        return []

    obligation_ids = []
    for consumed in consumed_batches:
        quantity = float(consumed.get("quantity") or 0)
        batch_id = int(consumed.get("batch_id") or 0)
        stock_record_id = int(consumed.get("stock_record_id") or 0)
        if quantity <= 0 or not batch_id or not stock_record_id:
            raise ValueError("临时领用出库结果缺少批次、流水或实际数量")
        # 同一批次允许分多次出库，流水号才是一次实际出库的唯一边界。
        operation_key = (
            f"claim_out:{int(form['id'])}:{int(item['id'])}:{batch_id}:{stock_record_id}"
        )
        cursor.execute(
            """
            SELECT *
            FROM temporary_issue_obligations
            WHERE operation_key = ? OR stock_record_id = ?
            """,
            (operation_key, stock_record_id),
        )
        existing = cursor.fetchone()
        if existing:
            existing = dict(existing)
            if (
                int(existing["claim_form_id"]) != int(form["id"])
                or int(existing["claim_item_id"]) != int(item["id"])
                or int(existing["source_batch_id"]) != batch_id
                or abs(float(existing["issued_quantity"]) - quantity) > 1e-6
            ):
                raise ValueError("临时领用待结算幂等键已被其他业务使用")
            obligation_ids.append(existing["id"])
            continue

        cursor.execute(
            """
            INSERT INTO temporary_issue_obligations
                (applicant_id, material_id, source_batch_id, claim_form_id,
                 claim_item_id, stock_record_id, issued_quantity, settled_quantity,
                 status, operation_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?, ?)
            """,
            (
                form["applicant_id"],
                item["material_id"],
                batch_id,
                form["id"],
                item["id"],
                stock_record_id,
                quantity,
                operation_key,
                now_text(),
                now_text(),
            ),
        )
        obligation_ids.append(cursor.lastrowid)
    return obligation_ids


def execute_create_claim(cursor, user, data):
    """Create a claim workflow from a submit payload without committing."""
    items = data.get("items") or []
    if not items:
        raise ValueError("申领单至少需要一行物料")
    begin_inventory_transaction(cursor.connection)
    from warehouse_suit.usage_kind import USAGE_RD, validate_usage_kind

    usage_kind = validate_usage_kind(data.get("usage_kind") or USAGE_RD)
    allocations = allocate_claim_items(
        cursor,
        items,
        user,
        include_temporary=temporary_inventory_enabled(cursor),
        usage_kind=usage_kind,
    )
    requested_leader_ids = [int(value) for value in data.get("leader_ids") or [] if int(value)]
    requested_leader_id = int(data.get("leader_id") or (requested_leader_ids[0] if requested_leader_ids else 0))
    leader_id = resolve_department_leader(cursor, user, "claim", "leader_claim", requested_leader_id)
    leader_ids = []
    for requested in requested_leader_ids or [leader_id]:
        resolved = resolve_department_leader(cursor, user, "claim", "leader_claim", requested)
        if resolved not in leader_ids:
            leader_ids.append(resolved)
    purpose = str(data.get("purpose") or "").strip()
    rd_item_kind = str(data.get("rd_item_kind") or "").strip()
    project_code = data.get("project_code")
    purchase_project = require_same_purchase_project(
        item.get("purchase_project") for item in items
    )
    purpose_fields = apply_purpose_payload(
        cursor,
        {
            "purpose": purpose,
            "rd_item_kind": rd_item_kind,
            "project_code": project_code,
        },
        purchase_project,
    )
    created = create_claim_workflow(
        cursor,
        user,
        allocations,
        leader_id,
        leader_ids=leader_ids or [leader_id],
        purpose=purpose_fields["purpose"],
        rd_item_kind=purpose_fields["rd_item_kind"],
        project_material_kind="",
        project_code=purpose_fields["project_code"],
        purchase_project=purpose_fields["purchase_project"],
        metadata={
            "usage_kind": usage_kind,
            "flow_attribute": claim_flow_attribute(data.get("flow_attribute")),
        },
    )
    return created["form_id"]


def claim_has_actual_outbound(cursor, form_id):
    cursor.execute(
        """
        SELECT 1
        FROM stock_records sr
        JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        WHERE wi.form_id = ? AND sr.operation_type = 'out'
        LIMIT 1
        """,
        (form_id,),
    )
    return bool(cursor.fetchone())
