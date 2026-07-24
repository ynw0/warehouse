"""Claim-specific transaction helpers shared by formal and temporary stock."""

from __future__ import annotations

import json

from warehouse_suit.claim_allocation_service import insert_claim_allocations
from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_TEMPORARY
from warehouse_suit.numbering import next_form_no
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    workflow_generated_title,
)


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
    create_workflow_tasks(cursor, form_id, "claim", "leader_claim", [int(leader_id)])
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
        operation_key = f"claim_out:{int(form['id'])}:{int(item['id'])}:{batch_id}"
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
