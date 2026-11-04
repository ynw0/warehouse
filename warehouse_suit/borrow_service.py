"""Borrow allocation and active temporary-borrow helpers."""

from __future__ import annotations

import json

from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import (
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import begin_inventory_transaction, borrowable_item_snapshot
from warehouse_suit.material_utils import stock_snapshot_payload
from warehouse_suit.purchase_project import (
    apply_purpose_payload,
    require_same_purchase_project,
    require_stored_purchase_project,
    validate_purchase_project,
)
from warehouse_suit.issued_material_service import take_batch_slice
from warehouse_suit.numbering import next_form_no
from warehouse_suit.settings import parse_json, temporary_inventory_enabled
from warehouse_suit.stock_allocation_service import (
    allocate_stock_sources,
    occupancy_text,
    stock_source_quantities,
)
from warehouse_suit.validation import quantity_value, validation_rule_enabled
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    resolve_department_leader,
    serialize_form,
    workflow_generated_title,
)


def _requested_quantity(raw):
    value = raw.get("request_quantity")
    if value is None:
        value = raw.get("requested_quantity")
    if value is None:
        value = raw.get("quantity")
    return quantity_value(value, "借用数量", positive=True)


def allocate_borrow_items(cursor, requested_items, applicant, include_temporary, exclude_form_id=None):
    """Build server-owned, formal-first workflow-item allocations."""
    if not requested_items:
        raise ValueError("借用申请至少需要一项物料")

    from warehouse_suit.usage_kind import USAGE_RD, validate_usage_kind

    allocations = []
    available_by_item = {}
    applicant_name = applicant.get("display_name") or applicant.get("username") or ""
    row_projects = []
    usage_kind = None

    for index, requested in enumerate(requested_items):
        item_type = str(requested.get("item_type") or "material").strip()
        item_ref_id = int(
            requested.get("item_ref_id")
            or requested.get("material_id")
            or 0
        )
        if item_ref_id <= 0:
            raise ValueError("借用物料不存在")
        requested_quantity = _requested_quantity(requested)
        if item_type == "material":
            purchase_project = validate_purchase_project(cursor, requested.get("purchase_project"))
        else:
            snapshot_for_project = borrowable_item_snapshot(
                cursor,
                item_type,
                item_ref_id,
                purchase_project=requested.get("purchase_project"),
            )
            purchase_project = validate_purchase_project(
                cursor,
                requested.get("purchase_project") or snapshot_for_project.get("purchase_project"),
            )
        row_projects.append(purchase_project)
        row_usage = validate_usage_kind(requested.get("usage_kind") or USAGE_RD)
        if usage_kind is None:
            usage_kind = row_usage
        elif row_usage != usage_kind:
            raise ValueError("一张借用单只允许一种用料形式")
        group_key = str(requested.get("allocation_group_key") or "").strip()
        if not group_key:
            group_key = f"borrow-row:{index + 1}:{item_type}:{item_ref_id}:{purchase_project}"

        if item_type == "material":
            cache_key = (item_type, item_ref_id, purchase_project, usage_kind)
            if cache_key not in available_by_item:
                available_by_item[cache_key] = stock_source_quantities(
                    cursor,
                    item_ref_id,
                    temporary_enabled=include_temporary,
                    purchase_project=purchase_project,
                    usage_kind=usage_kind,
                    exclude_form_id=exclude_form_id,
                )
            available = available_by_item[cache_key]
            source_allocation = allocate_stock_sources(
                cursor,
                item_ref_id,
                requested_quantity,
                include_temporary,
                available_quantities=available,
            )
            if source_allocation["shortfall"] > 1e-9:
                snapshot = borrowable_item_snapshot(
                    cursor,
                    item_type,
                    item_ref_id,
                    stock_source=STOCK_SOURCE_FORMAL,
                    purchase_project=purchase_project,
                    usage_kind=usage_kind,
                )
                raise ValueError(
                    f"{snapshot.get('item_name') or '物料'} 借用数量不能大于可用库存"
                    f"（正式库 {available['formal']:g}，临时库 {available['temporary']:g}）"
                    f"{occupancy_text(cursor, item_ref_id, purchase_project, usage_kind, exclude_form_id)}"
                )

            for stock_source, allocated_quantity in (
                (STOCK_SOURCE_FORMAL, source_allocation["formal"]),
                (STOCK_SOURCE_TEMPORARY, source_allocation["temporary"]),
            ):
                if allocated_quantity <= 1e-9:
                    continue
                snapshot = borrowable_item_snapshot(
                    cursor,
                    item_type,
                    item_ref_id,
                    stock_source=stock_source,
                    purchase_project=purchase_project,
                    usage_kind=usage_kind,
                )
                item_data = {
                    "borrow_item_type": item_type,
                    "borrow_ref_id": item_ref_id,
                    "borrow_applicant_id": applicant["id"],
                    "borrow_applicant_name": applicant_name,
                    "allocation_group_key": group_key,
                    "purchase_project": purchase_project,
                    "usage_kind": usage_kind,
                    "requested_quantity_snapshot": requested_quantity,
                    "formal_available_quantity_snapshot": available["formal"],
                    "temporary_available_quantity_snapshot": available["temporary"],
                    "total_available_quantity_snapshot": available["total"],
                    **stock_snapshot_payload(available[stock_source]),
                }
                allocations.append(
                    {
                        "snapshot": snapshot,
                        "request_quantity": allocated_quantity,
                        "stock_source": stock_source,
                        "purchase_project": purchase_project,
                        "data": item_data,
                    }
                )
            available["formal"] -= source_allocation["formal"]
            available["temporary"] -= source_allocation["temporary"]
            available["total"] = available["formal"] + available["temporary"]
            continue

        cache_key = (item_type, item_ref_id, purchase_project)
        if cache_key not in available_by_item:
            snapshot = borrowable_item_snapshot(
                cursor, item_type, item_ref_id, purchase_project=purchase_project
            )
            available_by_item[cache_key] = {
                "snapshot": snapshot,
                "formal": float(snapshot.get("available_quantity") or 0),
            }
        available = available_by_item[cache_key]
        if (
            validation_rule_enabled("workflow_bounds")
            and requested_quantity > available["formal"] + 1e-9
        ):
            raise ValueError(
                f"{available['snapshot'].get('item_name') or '物料'} 借用数量不能大于可用数量"
            )
        item_data = {
            "borrow_item_type": item_type,
            "borrow_ref_id": item_ref_id,
            "borrow_applicant_id": applicant["id"],
            "borrow_applicant_name": applicant_name,
            "allocation_group_key": group_key,
            "purchase_project": purchase_project,
            "requested_quantity_snapshot": requested_quantity,
            "formal_available_quantity_snapshot": available["formal"],
            "temporary_available_quantity_snapshot": 0.0,
            "total_available_quantity_snapshot": available["formal"],
            **stock_snapshot_payload(available["formal"]),
        }
        allocations.append(
            {
                "snapshot": available["snapshot"],
                "request_quantity": requested_quantity,
                "stock_source": STOCK_SOURCE_FORMAL,
                "purchase_project": purchase_project,
                "data": item_data,
            }
        )
        available["formal"] -= requested_quantity

    require_same_purchase_project(row_projects)
    return allocations


def insert_borrow_allocations(cursor, form_id, allocations):
    item_ids = []
    for allocation in allocations:
        snapshot = allocation["snapshot"]
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, brand_model,
                 spec, unit, request_quantity, data_json, stock_source, purchase_project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_id,
                snapshot.get("material_id"),
                snapshot.get("item_code") or "",
                snapshot.get("item_name") or "",
                snapshot.get("brand_model") or "",
                snapshot.get("spec") or "",
                snapshot.get("unit") or "",
                allocation["request_quantity"],
                json.dumps(allocation["data"], ensure_ascii=False),
                allocation["stock_source"],
                allocation.get("purchase_project")
                or allocation["data"].get("purchase_project")
                or snapshot.get("purchase_project")
                or "",
            ),
        )
        item_ids.append(cursor.lastrowid)
    return item_ids


def current_borrow_requested_items(cursor, form_id):
    cursor.execute(
        """
        SELECT id, material_id, request_quantity, purchase_project, data_json
        FROM workflow_items
        WHERE form_id = ?
        ORDER BY id
        """,
        (form_id,),
    )
    grouped = {}
    order = []
    for row in cursor.fetchall():
        item = dict(row)
        data = json.loads(item.get("data_json") or "{}")
        item_type = str(data.get("borrow_item_type") or "material")
        item_ref_id = int(
            data.get("borrow_ref_id")
            or item.get("material_id")
            or 0
        )
        group_key = str(
            data.get("allocation_group_key")
            or f"legacy-borrow-item:{item['id']}"
        )
        if group_key not in grouped:
            grouped[group_key] = {
                "item_type": item_type,
                "item_ref_id": item_ref_id,
                "request_quantity": 0.0,
                "allocation_group_key": group_key,
                "purchase_project": item.get("purchase_project") or data.get("purchase_project") or "",
            }
            order.append(group_key)
        requested_snapshot = data.get("requested_quantity_snapshot")
        if requested_snapshot is not None:
            grouped[group_key]["request_quantity"] = quantity_value(
                requested_snapshot,
                "借用数量",
                positive=True,
            )
        else:
            grouped[group_key]["request_quantity"] += float(
                item.get("request_quantity") or 0
            )
    return [grouped[key] for key in order]


def borrow_revision_requested_items(cursor, form_id, submitted_items):
    cursor.execute(
        """
        SELECT id, material_id, purchase_project, data_json
        FROM workflow_items
        WHERE form_id = ?
        ORDER BY id
        """,
        (form_id,),
    )
    current_rows = {int(row["id"]): dict(row) for row in cursor.fetchall()}
    grouped = {}
    order = []
    for submitted in submitted_items or []:
        item_id = int(submitted.get("id") or 0)
        current = current_rows.get(item_id)
        if not current:
            raise ValueError("借用明细不存在或不属于当前流程")
        data = json.loads(current.get("data_json") or "{}")
        item_type = str(data.get("borrow_item_type") or "material")
        item_ref_id = int(
            data.get("borrow_ref_id")
            or current.get("material_id")
            or 0
        )
        group_key = str(
            submitted.get("allocation_group_key")
            or data.get("allocation_group_key")
            or f"legacy-borrow-item:{item_id}"
        )
        quantity = _requested_quantity(submitted)
        if group_key not in grouped:
            grouped[group_key] = {
                "item_type": item_type,
                "item_ref_id": item_ref_id,
                "request_quantity": 0.0,
                "allocation_group_key": group_key,
                "purchase_project": (
                    submitted.get("purchase_project")
                    or current.get("purchase_project")
                    or data.get("purchase_project")
                    or ""
                ),
            }
            order.append(group_key)
        current_group = grouped[group_key]
        if (
            current_group["item_type"] != item_type
            or int(current_group["item_ref_id"]) != item_ref_id
        ):
            raise ValueError("同一借用行不能包含不同物料")
        if submitted.get("allocation_group_key"):
            if current_group["request_quantity"] > 0:
                raise ValueError("同一借用行不能重复提交")
            current_group["request_quantity"] = quantity
        else:
            current_group["request_quantity"] += quantity
    if not order:
        raise ValueError("借用申请至少需要一项物料")
    return [grouped[key] for key in order]


def borrow_remaining_quantity(record):
    """未归还 = 原数量 − 已归还 − 已转领用。"""
    return max(
        0.0,
        float(record.get("quantity") or 0)
        - float(record.get("returned_quantity") or 0)
        - float(record.get("claimed_quantity") or 0),
    )


def borrow_settled_status(record):
    if borrow_remaining_quantity(record) > 1e-9:
        return "borrowed"
    if float(record.get("claimed_quantity") or 0) > 1e-9:
        return "claimed"
    return "returned"


def is_full_borrow_transfer(record, quantity):
    """整单转借：未归还且未转领用，数量等于原单数量。"""
    remaining = borrow_remaining_quantity(record)
    return (
        abs(float(quantity) - remaining) <= 1e-9
        and abs(remaining - float(record.get("quantity") or 0)) <= 1e-9
    )


def insert_borrow_record(
    cursor,
    record,
    *,
    quantity,
    status,
    data,
    returned_quantity=0,
    claimed_quantity=0,
    borrower_id=None,
):
    stamp = now_text()
    # 拆出的转借单不占用原 workflow_item_id，避免 (borrow_form_id, workflow_item_id) 唯一约束冲突
    cursor.execute(
        """
        INSERT INTO borrow_records
            (borrow_no, item_type, item_ref_id, material_id, workflow_item_id, stock_source,
             item_code, item_name, brand_model, spec, unit, quantity, returned_quantity,
             claimed_quantity, status, borrower_id, borrow_form_id, outbound_date,
             data_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("borrow_no") or "",
            record.get("item_type") or "material",
            int(record.get("item_ref_id") or 0),
            record.get("material_id"),
            record.get("stock_source") or STOCK_SOURCE_FORMAL,
            record.get("item_code") or "",
            record.get("item_name") or "",
            record.get("brand_model") or "",
            record.get("spec") or "",
            record.get("unit") or "",
            float(quantity),
            float(returned_quantity),
            float(claimed_quantity),
            status,
            int(borrower_id if borrower_id is not None else record["borrower_id"]),
            record.get("borrow_form_id"),
            record.get("outbound_date") or "",
            json.dumps(data, ensure_ascii=False),
            stamp,
            stamp,
        ),
    )
    return cursor.lastrowid


def borrow_has_actual_outbound(cursor, form_id):
    cursor.execute(
        "SELECT 1 FROM borrow_records WHERE borrow_form_id = ? LIMIT 1",
        (int(form_id),),
    )
    return bool(cursor.fetchone())


def list_active_borrows(cursor, material_id, stock_source=None):
    """查询指定物料仍未归还的借用记录。"""
    sql = """
        SELECT
            br.*,
            COALESCE(br.quantity, 0) - COALESCE(br.returned_quantity, 0)
                - COALESCE(br.claimed_quantity, 0)
                AS remaining_quantity
        FROM borrow_records br
        WHERE br.material_id = ?
          AND br.status IN ('borrowed', 'transferring', 'partially_returned')
          AND COALESCE(br.quantity, 0) - COALESCE(br.returned_quantity, 0)
                - COALESCE(br.claimed_quantity, 0) > 0.000000001
    """
    params = [int(material_id)]
    if stock_source:
        sql += " AND br.stock_source = ?"
        params.append(stock_source)
    sql += " ORDER BY br.id"
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def list_active_temporary_borrows(cursor, material_id):
    return list_active_borrows(cursor, material_id, STOCK_SOURCE_TEMPORARY)


def has_active_temporary_borrows(cursor, material_id):
    return bool(list_active_temporary_borrows(cursor, material_id))


def execute_create_borrow(cursor, user, data):
    """Create a borrow workflow from a submit payload without committing."""
    items = data.get("items") or []
    if not items:
        raise ValueError("借用申请至少需要一项物料")
    leader_id = resolve_department_leader(
        cursor,
        user,
        "borrow",
        "leader_borrow",
        int(data.get("leader_id") or 0),
    )
    begin_inventory_transaction(cursor.connection)
    allocations = allocate_borrow_items(
        cursor,
        items,
        user,
        include_temporary=temporary_inventory_enabled(cursor),
    )
    purchase_project = require_same_purchase_project(
        allocation.get("purchase_project") or allocation["data"].get("purchase_project")
        for allocation in allocations
    )
    purpose_fields = apply_purpose_payload(cursor, data, purchase_project)
    form_no = next_form_no(cursor, "JY")
    title = workflow_generated_title(user, form_no)
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, leader_id,
             data_json, created_at, updated_at)
        VALUES (?, 'borrow', ?, 'leader_borrow', 'leader_borrow', ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            title,
            user["id"],
            leader_id,
            json.dumps(
                {
                    "department": user.get("department") or "",
                    "expected_return_date": data.get("expected_return_date") or "",
                    "purpose": purpose_fields["purpose"],
                    "rd_item_kind": purpose_fields["rd_item_kind"],
                    "project_code": purpose_fields["project_code"],
                    "purchase_project": purpose_fields["purchase_project"],
                    "project_material_kind": "",
                },
                ensure_ascii=False,
            ),
            now_text(),
            now_text(),
        ),
    )
    form_id = cursor.lastrowid
    insert_borrow_allocations(cursor, form_id, allocations)
    create_workflow_tasks(cursor, form_id, "borrow", "leader_borrow", [leader_id])
    return form_id


def _batches_from_payload(data):
    if not isinstance(data, dict):
        return []
    batches = data.get("consumed_batches")
    if isinstance(batches, list) and batches:
        return batches
    nested = data.get("borrow_outbound")
    if isinstance(nested, dict):
        batches = nested.get("consumed_batches")
        if isinstance(batches, list) and batches:
            return batches
    return []


def borrow_outbound_batches(cursor, record):
    """取该借用记录对应的出库批次；记录上没有时回原借用明细。"""
    batches = _batches_from_payload(parse_json(record.get("data_json"), {}))
    if batches:
        return batches
    item_id = borrow_origin_workflow_item_id(cursor, record)
    if not item_id:
        return []
    cursor.execute("SELECT data_json FROM workflow_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        return []
    return _batches_from_payload(parse_json(row["data_json"], {}))


def transfer_borrow_batch_slice(cursor, record, transfer_qty):
    """从原出库批次中切出本次转借数量，供接收人后续转领用进验收池。"""
    batches = borrow_outbound_batches(cursor, record)
    if not batches:
        return []
    transfer_qty = float(transfer_qty or 0)
    skip_qty = float(record.get("quantity") or 0) - transfer_qty
    if skip_qty < -1e-9:
        raise ValueError("转借数量不能超过借用数量")
    return take_batch_slice(batches, max(0.0, skip_qty), transfer_qty)


def borrow_origin_workflow_item_id(cursor, record):
    item_id = int(record.get("workflow_item_id") or 0)
    if item_id:
        return item_id
    data = parse_json(record.get("data_json"), {})
    if data.get("origin_workflow_item_id"):
        return int(data["origin_workflow_item_id"])
    parent_id = int(data.get("transfer_from_record_id") or 0)
    seen = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        cursor.execute("SELECT workflow_item_id, data_json FROM borrow_records WHERE id = ?", (parent_id,))
        parent = cursor.fetchone()
        if not parent:
            break
        parent = dict(parent)
        item_id = int(parent.get("workflow_item_id") or 0)
        if item_id:
            return item_id
        parent_data = parse_json(parent.get("data_json"), {})
        if parent_data.get("origin_workflow_item_id"):
            return int(parent_data["origin_workflow_item_id"])
        parent_id = int(parent_data.get("transfer_from_record_id") or 0)
    return 0


def mark_borrow_item_claimed(cursor, record, quantity):
    """原借用单对应物料累加已转领用数量。"""
    item_id = borrow_origin_workflow_item_id(cursor, record)
    if not item_id:
        return
    cursor.execute("SELECT data_json FROM workflow_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        return
    data = parse_json(row["data_json"], {})
    data["claimed_quantity"] = float(data.get("claimed_quantity") or 0) + float(quantity)
    cursor.execute(
        "UPDATE workflow_items SET data_json = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), item_id),
    )


def _user_name(cursor, user_id):
    if not user_id:
        return ""
    cursor.execute(
        "SELECT COALESCE(display_name, username, '') AS name FROM users WHERE id = ?",
        (int(user_id),),
    )
    row = cursor.fetchone()
    return str(row["name"] or "") if row else ""


def borrow_form_snapshot(cursor, record):
    form_id = int(record.get("borrow_form_id") or 0)
    if form_id:
        form = serialize_form(cursor, form_id)
        if form:
            items = []
            for item in form.get("items") or []:
                items.append(
                    {
                        "material_code": item.get("material_code") or "",
                        "material_name": item.get("material_name") or "",
                        "brand_model": item.get("brand_model") or "",
                        "spec": item.get("spec") or "",
                        "unit": item.get("unit") or "",
                        "request_quantity": float(item.get("request_quantity") or 0),
                        "outbound_quantity": float(item.get("outbound_quantity") or 0),
                        "claimed_quantity": float((item.get("data") or {}).get("claimed_quantity") or 0),
                        "stock_source": item.get("stock_source") or STOCK_SOURCE_FORMAL,
                    }
                )
            return {
                "form_id": form.get("id"),
                "form_no": form.get("form_no") or record.get("borrow_no") or "",
                "title": form.get("title") or "",
                "applicant_name": form.get("applicant_name") or "",
                "leader_name": form.get("leader_name") or "",
                "warehouse_user_name": form.get("warehouse_user_name") or "",
                "created_at": form.get("created_at") or "",
                "department": (form.get("data") or {}).get("department") or "",
                "expected_return_date": (form.get("data") or {}).get("expected_return_date") or "",
                "items": items,
            }
    return {
        "form_id": form_id or None,
        "form_no": record.get("borrow_no") or "",
        "title": "",
        "applicant_name": _user_name(cursor, record.get("borrower_id")),
        "leader_name": "",
        "warehouse_user_name": "",
        "created_at": record.get("created_at") or "",
        "department": "",
        "expected_return_date": "",
        "items": [
            {
                "material_code": record.get("item_code") or "",
                "material_name": record.get("item_name") or "",
                "brand_model": record.get("brand_model") or "",
                "spec": record.get("spec") or "",
                "unit": record.get("unit") or "",
                "request_quantity": float(record.get("quantity") or 0),
                "outbound_quantity": float(record.get("quantity") or 0),
                "claimed_quantity": float(record.get("claimed_quantity") or 0),
                "stock_source": record.get("stock_source") or STOCK_SOURCE_FORMAL,
            }
        ],
    }


def existing_borrow_claim_form_id(cursor, record_id):
    cursor.execute(
        """
        SELECT id FROM workflow_forms
        WHERE form_type = 'borrow_claim'
          AND json_valid(COALESCE(data_json, '{}'))
          AND CAST(json_extract(data_json, '$.borrow_record_id') AS INTEGER) = ?
        LIMIT 1
        """,
        (int(record_id),),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row else 0


def _borrow_form_purpose_fields(cursor, borrow_form_id, snapshot):
    """转领用必须抄原借用单项目，旧单缺字段则失败。"""
    form_data = {}
    if borrow_form_id:
        cursor.execute("SELECT data_json FROM workflow_forms WHERE id = ?", (int(borrow_form_id),))
        row = cursor.fetchone()
        form_data = parse_json(row["data_json"] if row else {}, {})
    if not form_data:
        form_data = dict(snapshot or {})
    purchase_project = require_stored_purchase_project(
        form_data.get("purchase_project"),
        "原借用单",
    )
    return {
        "purpose": str(form_data.get("purpose") or "").strip(),
        "rd_item_kind": str(form_data.get("rd_item_kind") or "").strip(),
        "project_code": str(form_data.get("project_code") or "").strip(),
        "purchase_project": purchase_project,
    }


def create_borrow_claim_form(cursor, user, record, quantity, claimed_at, skip_qty, require_purchase_project=True):
    """生成已办结的借用转领用单，不扣库存。"""
    batches = take_batch_slice(borrow_outbound_batches(cursor, record), skip_qty, quantity)
    mark_borrow_item_claimed(cursor, record, quantity)
    snapshot = borrow_form_snapshot(cursor, record)
    borrow_form_id = int(record.get("borrow_form_id") or 0) or None
    if require_purchase_project:
        purpose_fields = _borrow_form_purpose_fields(cursor, borrow_form_id, snapshot)
    else:
        form_data = {}
        if borrow_form_id:
            cursor.execute("SELECT data_json FROM workflow_forms WHERE id = ?", (int(borrow_form_id),))
            row = cursor.fetchone()
            form_data = parse_json(row["data_json"] if row else {}, {})
        purpose_fields = {
            "purpose": str(form_data.get("purpose") or "").strip(),
            "rd_item_kind": str(form_data.get("rd_item_kind") or "").strip(),
            "project_code": str(form_data.get("project_code") or "").strip(),
            "purchase_project": str(form_data.get("purchase_project") or "").strip(),
        }
    form_no = next_form_no(cursor, "ZL")
    title = workflow_generated_title(user, form_no)
    form_data = {
        "borrow_form_id": borrow_form_id,
        "borrow_form_no": snapshot.get("form_no") or record.get("borrow_no") or "",
        "borrow_record_id": int(record["id"]),
        "claimed_date": claimed_at[:10] if claimed_at else "",
        "claimed_quantity": float(quantity),
        "borrow_snapshot": snapshot,
        "purpose": purpose_fields["purpose"],
        "rd_item_kind": purpose_fields["rd_item_kind"],
        "project_code": purpose_fields["project_code"],
        "purchase_project": purpose_fields["purchase_project"],
        "project_material_kind": "",
    }
    stamp = claimed_at or now_text()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id,
             data_json, origin_type, origin_ref_id, created_at, updated_at)
        VALUES (?, 'borrow_claim', ?, 'completed', 'completed', ?, ?, 'borrow_claim', ?, ?, ?)
        """,
        (
            form_no,
            title,
            int(user["id"]),
            json.dumps(form_data, ensure_ascii=False),
            borrow_form_id,
            stamp,
            stamp,
        ),
    )
    form_id = int(cursor.lastrowid)
    stock_source = record.get("stock_source") or STOCK_SOURCE_FORMAL
    item_data = {
        "borrow_record_id": int(record["id"]),
        "borrow_form_id": borrow_form_id,
        "borrow_form_no": form_data["borrow_form_no"],
        "claimed_date": form_data["claimed_date"],
        "consumed_batches": batches,
        "stock_source": stock_source,
        "purpose": purpose_fields["purpose"],
        "rd_item_kind": purpose_fields["rd_item_kind"],
        "project_code": purpose_fields["project_code"],
        "purchase_project": purpose_fields["purchase_project"],
    }
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, brand_model, spec, unit,
             request_quantity, outbound_quantity, data_json, stock_source, purchase_project)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_id,
            record.get("material_id"),
            record.get("item_code") or "",
            record.get("item_name") or "",
            record.get("brand_model") or "",
            record.get("spec") or "",
            record.get("unit") or "",
            float(quantity),
            float(quantity),
            json.dumps(item_data, ensure_ascii=False),
            stock_source,
            purpose_fields["purchase_project"],
        ),
    )
    return form_id


def backfill_borrow_claim_forms(cursor):
    """为已转领用但没有单据的借用记录补单。"""
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workflow_forms'"
    )
    if not cursor.fetchone():
        return
    cursor.execute('PRAGMA table_info("workflow_forms")')
    form_columns = {row[1] for row in cursor.fetchall()}
    if "form_type" not in form_columns:
        return
    cursor.execute('PRAGMA table_info("borrow_records")')
    record_columns = {row[1] for row in cursor.fetchall()}
    if "claimed_quantity" not in record_columns:
        return
    cursor.execute(
        """
        SELECT * FROM borrow_records
        WHERE COALESCE(claimed_quantity, 0) > 0.000000001
        ORDER BY id
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    for record in rows:
        if existing_borrow_claim_form_id(cursor, record["id"]):
            continue
        borrower_id = int(record.get("borrower_id") or 0)
        if not borrower_id:
            raise ValueError("已转领用记录缺少借用人，无法补单据")
        cursor.execute("SELECT * FROM users WHERE id = ?", (borrower_id,))
        user_row = cursor.fetchone()
        if not user_row:
            raise ValueError("已转领用记录的借用人不存在，无法补单据")
        user = dict(user_row)
        quantity = float(record.get("claimed_quantity") or 0)
        claimed_at = str(parse_json(record.get("data_json"), {}).get("claimed_at") or record.get("updated_at") or now_text())
        skip_qty = float(record.get("returned_quantity") or 0)
        create_borrow_claim_form(cursor, user, record, quantity, claimed_at, skip_qty, require_purchase_project=False)
