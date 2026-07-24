"""Borrow allocation and active temporary-borrow helpers."""

from __future__ import annotations

import json

from warehouse_suit.inventory_constants import (
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import borrowable_item_snapshot
from warehouse_suit.material_utils import stock_snapshot_payload
from warehouse_suit.stock_allocation_service import (
    allocate_stock_sources,
    stock_source_quantities,
)
from warehouse_suit.validation import quantity_value, validation_rule_enabled


def _requested_quantity(raw):
    value = raw.get("request_quantity")
    if value is None:
        value = raw.get("requested_quantity")
    if value is None:
        value = raw.get("quantity")
    return quantity_value(value, "借用数量", positive=True)


def allocate_borrow_items(cursor, requested_items, applicant, include_temporary):
    """Build server-owned, formal-first workflow-item allocations."""
    if not requested_items:
        raise ValueError("借用申请至少需要一项物料")

    allocations = []
    available_by_item = {}
    applicant_name = applicant.get("display_name") or applicant.get("username") or ""

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
        group_key = str(requested.get("allocation_group_key") or "").strip()
        if not group_key:
            group_key = f"borrow-row:{index + 1}:{item_type}:{item_ref_id}"

        if item_type == "material":
            cache_key = (item_type, item_ref_id)
            if cache_key not in available_by_item:
                available_by_item[cache_key] = stock_source_quantities(
                    cursor,
                    item_ref_id,
                    temporary_enabled=include_temporary,
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
                )
                raise ValueError(
                    f"{snapshot.get('item_name') or '物料'} 借用数量不能大于可用库存"
                    f"（正式库 {available['formal']:g}，临时库 {available['temporary']:g}）"
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
                )
                item_data = {
                    "borrow_item_type": item_type,
                    "borrow_ref_id": item_ref_id,
                    "borrow_applicant_id": applicant["id"],
                    "borrow_applicant_name": applicant_name,
                    "allocation_group_key": group_key,
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
                        "data": item_data,
                    }
                )
            available["formal"] -= source_allocation["formal"]
            available["temporary"] -= source_allocation["temporary"]
            available["total"] = available["formal"] + available["temporary"]
            continue

        cache_key = (item_type, item_ref_id)
        if cache_key not in available_by_item:
            snapshot = borrowable_item_snapshot(cursor, item_type, item_ref_id)
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
                "data": item_data,
            }
        )
        available["formal"] -= requested_quantity

    return allocations


def insert_borrow_allocations(cursor, form_id, allocations):
    item_ids = []
    for allocation in allocations:
        snapshot = allocation["snapshot"]
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, brand_model,
                 spec, unit, request_quantity, data_json, stock_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        item_ids.append(cursor.lastrowid)
    return item_ids


def current_borrow_requested_items(cursor, form_id):
    cursor.execute(
        """
        SELECT id, material_id, request_quantity, data_json
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
        SELECT id, material_id, data_json
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


def borrow_has_actual_outbound(cursor, form_id):
    cursor.execute(
        "SELECT 1 FROM borrow_records WHERE borrow_form_id = ? LIMIT 1",
        (int(form_id),),
    )
    return bool(cursor.fetchone())


def list_active_temporary_borrows(cursor, material_id):
    cursor.execute(
        """
        SELECT
            br.*,
            COALESCE(br.quantity, 0) - COALESCE(br.returned_quantity, 0)
                AS remaining_quantity
        FROM borrow_records br
        WHERE br.material_id = ?
          AND br.stock_source = ?
          AND br.status IN ('borrowed', 'transferring', 'partially_returned')
          AND COALESCE(br.quantity, 0) - COALESCE(br.returned_quantity, 0) > 0.000000001
        ORDER BY br.id
        """,
        (int(material_id), STOCK_SOURCE_TEMPORARY),
    )
    return [dict(row) for row in cursor.fetchall()]


def has_active_temporary_borrows(cursor, material_id):
    return bool(list_active_temporary_borrows(cursor, material_id))
