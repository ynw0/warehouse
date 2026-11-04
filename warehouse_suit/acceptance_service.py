"""Acceptance workflow creation shared by HTTP and transfer-task callers."""

from __future__ import annotations

import json

from warehouse_suit.attachments import bind_material_attachments
from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY, validate_stock_source
from warehouse_suit.inventory_service import ensure_material_from_payload
from warehouse_suit.material_repository import material_snapshot
from warehouse_suit.numbering import next_form_no
from warehouse_suit.purchase_project import validate_purchase_project
from warehouse_suit.settings import workflow_settings
from warehouse_suit.validation import quantity_value, unit_price_from_total_amount
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    validate_validator_users,
    workflow_assignees,
    workflow_generated_title,
)


def create_acceptance_workflow(
    cursor,
    creator,
    items,
    validator_ids=None,
    origin_type="manual",
    origin_ref_id=None,
    form_data=None,
    stock_source=STOCK_SOURCE_FORMAL,
):
    """Create an acceptance workflow without committing the caller's transaction."""
    rows = list(items or [])
    stock_source = validate_stock_source(stock_source)
    if not rows:
        raise ValueError("验收单至少需要一行物料")
    validators = [int(value) for value in (validator_ids or []) if int(value)]
    if not validators:
        validators = [int(creator["id"])]
    validators = validate_validator_users(cursor, validators, "acceptance")
    validators = workflow_assignees(cursor, "acceptance", "acceptance", validators)

    form_no = next_form_no(cursor, "YS")
    title = workflow_generated_title(creator, form_no)
    payload = dict(form_data or {})
    payload["validator_ids"] = validators
    timestamp = now_text()
    cursor.execute(
        """
        INSERT INTO workflow_forms (
            form_no, form_type, title, status, current_step, applicant_id, leader_id,
            total_amount, data_json, origin_type, origin_ref_id, created_at, updated_at
        ) VALUES (?, 'acceptance', ?, 'acceptance', 'acceptance', ?, NULL, 0, ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            title,
            int(creator["id"]),
            json.dumps(payload, ensure_ascii=False),
            str(origin_type or "manual"),
            int(origin_ref_id) if origin_ref_id is not None else None,
            timestamp,
            timestamp,
        ),
    )
    form_id = int(cursor.lastrowid)
    total = 0.0
    item_ids = []
    for item in rows:
        material_id = ensure_material_from_payload(cursor, item)
        purchase_applicant = str(item.get("purchase_applicant") or "").strip()
        purchase_project = validate_purchase_project(cursor, item.get("purchase_project"))
        cursor.execute(
            "UPDATE materials SET purchase_project = ?, updated_at = ? WHERE id = ?",
            (purchase_project, timestamp, material_id),
        )
        if purchase_applicant:
            cursor.execute(
                "UPDATE materials SET purchase_applicant = ?, updated_at = ? WHERE id = ?",
                (purchase_applicant, timestamp, material_id),
            )
        snapshot = material_snapshot(cursor, material_id)
        purchase_quantity = quantity_value(
            item.get("purchase_quantity") or item.get("request_quantity"),
            "采购数量",
            positive=True,
        )
        arrival_quantity = quantity_value(item.get("arrival_quantity"), "到货数量")
        from warehouse_suit.usage_kind import USAGE_RD, actual_quantity, pack_quantity_value, validate_usage_kind

        pack_quantity = pack_quantity_value(item.get("pack_quantity") or 1)
        usage_kind = validate_usage_kind(item.get("usage_kind") or USAGE_RD)
        arrival_unit = str(item.get("arrival_unit") or item.get("unit") or "").strip()
        stock_quantity = actual_quantity(arrival_quantity, pack_quantity)
        unit_price = unit_price_from_total_amount(
            item.get("total_amount"), stock_quantity, "验收总金额", "实际数量"
        )
        total += stock_quantity * unit_price
        item_data = dict(item.get("data") or {})
        purchase_item_id = int(item.get("purchase_item_id") or 0)
        if purchase_item_id:
            item_data["purchase_item_id"] = purchase_item_id
        item_data.update(
            {
                "pack_quantity": pack_quantity,
                "arrival_unit": arrival_unit,
                "usage_kind": usage_kind,
                "actual_quantity": stock_quantity,
            }
        )
        cursor.execute(
            """
            INSERT INTO workflow_items (
                form_id, material_id, material_code, material_name, brand_model, spec, unit,
                request_quantity, arrival_quantity, unit_price, purchase_applicant, purchase_project,
                stock_source, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_id,
                material_id,
                snapshot["material_code"],
                snapshot["name"],
                snapshot["brand_model"],
                snapshot["spec"],
                snapshot["unit"],
                purchase_quantity,
                arrival_quantity,
                unit_price,
                purchase_applicant or snapshot.get("purchase_applicant") or "",
                purchase_project,
                stock_source,
                json.dumps(item_data, ensure_ascii=False),
            ),
        )
        workflow_item_id = int(cursor.lastrowid)
        item_ids.append(workflow_item_id)
        bind_material_attachments(
            cursor,
            item.get("attachment_tokens") or [],
            material_id=material_id,
            workflow_form_id=form_id,
            workflow_item_id=workflow_item_id,
        )
    create_workflow_tasks(cursor, form_id, "acceptance", "acceptance", validators)
    cursor.execute(
        "UPDATE workflow_forms SET total_amount = ? WHERE id = ?",
        (total, form_id),
    )
    return {
        "form_id": form_id,
        "form_no": form_no,
        "item_ids": item_ids,
        "validator_ids": validators,
        "total_amount": total,
    }


def execute_create_acceptance(cursor, user, data):
    """Create an acceptance workflow from a submit payload without committing."""
    temporary_enabled = bool(workflow_settings(cursor).get("temporary_inventory_enabled"))
    has_purchase_order = data.get("has_purchase_order")
    if temporary_enabled:
        if has_purchase_order not in {True, False}:
            raise ValueError("请选择是否有采购订单")
    else:
        has_purchase_order = True
    stock_source = STOCK_SOURCE_FORMAL if has_purchase_order else STOCK_SOURCE_TEMPORARY
    created = create_acceptance_workflow(
        cursor,
        user,
        data.get("items") or [],
        validator_ids=data.get("validator_ids") or [],
        form_data={"has_purchase_order": has_purchase_order},
        stock_source=stock_source,
    )
    return created["form_id"]
