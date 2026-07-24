"""Server-side formal-first allocation for claim requests."""

from __future__ import annotations

import json

from warehouse_suit.inventory_constants import (
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import material_id_from_payload
from warehouse_suit.material_repository import material_snapshot
from warehouse_suit.material_utils import stock_snapshot_payload
from warehouse_suit.stock_allocation_service import (
    allocate_stock_sources,
    stock_source_quantities as claim_stock_quantities,
)
from warehouse_suit.validation import quantity_value


def claim_material_rows(cursor, keyword="", include_temporary=False, limit=100):
    keyword = str(keyword or "").strip()
    like = f"%{keyword}%"
    limit = max(1, min(int(limit or 100), 200))
    cursor.execute(
        """
        WITH active_reservations AS (
            SELECT formal_batch_id,
                   SUM(reserved_quantity - consumed_quantity - released_quantity)
                       AS reserved_quantity
            FROM inventory_reservations
            WHERE status = 'active'
              AND reserved_quantity - consumed_quantity - released_quantity > 0
            GROUP BY formal_batch_id
        ),
        source_stock AS (
            SELECT
                b.material_id,
                SUM(
                    CASE WHEN b.stock_source = ? AND b.inventory_status = ?
                         THEN MAX(b.quantity - COALESCE(r.reserved_quantity, 0), 0)
                         ELSE 0 END
                ) AS formal_quantity,
                SUM(
                    CASE WHEN b.stock_source = ? AND b.inventory_status = ?
                         THEN b.quantity ELSE 0 END
                ) AS temporary_quantity,
                SUM(CASE WHEN b.stock_source = ? THEN 1 ELSE 0 END)
                    AS formal_batch_count
            FROM material_batches b
            LEFT JOIN active_reservations r ON r.formal_batch_id = b.id
            GROUP BY b.material_id
        )
        SELECT
            m.*,
            CASE
                WHEN COALESCE(ss.formal_batch_count, 0) > 0
                THEN COALESCE(ss.formal_quantity, 0)
                ELSE COALESCE(i.quantity, 0)
            END AS formal_available_quantity,
            CASE WHEN ? THEN COALESCE(ss.temporary_quantity, 0) ELSE 0 END
                AS temporary_available_quantity,
            (
                CASE
                    WHEN COALESCE(ss.formal_batch_count, 0) > 0
                    THEN COALESCE(ss.formal_quantity, 0)
                    ELSE COALESCE(i.quantity, 0)
                END
                + CASE WHEN ? THEN COALESCE(ss.temporary_quantity, 0) ELSE 0 END
            ) AS total_available_quantity
        FROM materials m
        LEFT JOIN inventory i ON i.material_id = m.id
        LEFT JOIN source_stock ss ON ss.material_id = m.id
        WHERE (
            ? = ''
            OR m.material_code LIKE ?
            OR m.name LIKE ?
            OR COALESCE(m.brand_model, '') LIKE ?
            OR COALESCE(m.spec, '') LIKE ?
            OR COALESCE(m.purchase_applicant, '') LIKE ?
        )
        ORDER BY
            CASE
                WHEN (
                    CASE
                        WHEN COALESCE(ss.formal_batch_count, 0) > 0
                        THEN COALESCE(ss.formal_quantity, 0)
                        ELSE COALESCE(i.quantity, 0)
                    END
                    + CASE WHEN ? THEN COALESCE(ss.temporary_quantity, 0) ELSE 0 END
                ) > 0 THEN 0
                ELSE 1
            END,
            m.material_code,
            m.id
        LIMIT ?
        """,
        (
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_TEMPORARY,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_FORMAL,
            int(bool(include_temporary)),
            int(bool(include_temporary)),
            keyword,
            like,
            like,
            like,
            like,
            like,
            int(bool(include_temporary)),
            limit,
        ),
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(row)
        item["formal_available_quantity"] = float(item.get("formal_available_quantity") or 0)
        item["temporary_available_quantity"] = float(item.get("temporary_available_quantity") or 0)
        item["total_available_quantity"] = float(item.get("total_available_quantity") or 0)
        item["quantity"] = item["total_available_quantity"]
        rows.append(item)
    return rows


def allocate_claim_items(cursor, requested_items, applicant, include_temporary):
    if not requested_items:
        raise ValueError("申领单至少需要一行物料")

    available_by_material = {}
    allocations = []
    applicant_name = applicant.get("display_name") or applicant.get("username") or ""

    for index, requested_item in enumerate(requested_items):
        material_id = material_id_from_payload(cursor, requested_item, require_existing=True)
        snapshot = material_snapshot(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL)
        if not snapshot:
            label = (
                requested_item.get("material_code")
                or requested_item.get("material_name")
                or requested_item.get("name")
                or ""
            )
            raise ValueError(f"申领物料不存在：{label}")
        requested_quantity = quantity_value(
            requested_item.get("request_quantity")
            if requested_item.get("request_quantity") is not None
            else requested_item.get("requested_quantity"),
            "申领数量",
            positive=True,
        )
        if material_id not in available_by_material:
            available_by_material[material_id] = claim_stock_quantities(
                cursor,
                material_id,
                temporary_enabled=include_temporary,
            )
        available = available_by_material[material_id]
        source_allocation = allocate_stock_sources(
            cursor,
            material_id,
            requested_quantity,
            include_temporary,
            available_quantities=available,
        )
        total_available = source_allocation["available"]["total"]
        if source_allocation["shortfall"] > 1e-9:
            raise ValueError(
                f"{snapshot['name']} 申领数量不能大于可用库存"
                f"（正式库 {available['formal']:g}，临时库 {available['temporary']:g}）"
            )

        group_key = str(requested_item.get("allocation_group_key") or "").strip()
        if not group_key:
            group_key = f"claim-row:{index + 1}:{material_id}"
        formal_quantity = source_allocation["formal"]
        temporary_quantity = source_allocation["temporary"]

        for stock_source, allocated_quantity in (
            (STOCK_SOURCE_FORMAL, formal_quantity),
            (STOCK_SOURCE_TEMPORARY, temporary_quantity),
        ):
            if allocated_quantity <= 1e-9:
                continue
            source_available = available[stock_source]
            item_data = {
                "claim_applicant_id": applicant["id"],
                "claim_applicant_name": applicant_name,
                "allocation_group_key": group_key,
                "requested_quantity_snapshot": requested_quantity,
                "formal_available_quantity_snapshot": available["formal"],
                "temporary_available_quantity_snapshot": available["temporary"],
                "total_available_quantity_snapshot": total_available,
                **stock_snapshot_payload(source_available),
            }
            allocations.append(
                {
                    "material_id": material_id,
                    "material": snapshot,
                    "request_quantity": allocated_quantity,
                    "stock_source": stock_source,
                    "data": item_data,
                }
            )
        available["formal"] -= formal_quantity
        available["temporary"] -= temporary_quantity
    return allocations


def insert_claim_allocations(cursor, form_id, allocations):
    item_ids = []
    for allocation in allocations:
        snapshot = allocation["material"]
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, brand_model, spec,
                 unit, request_quantity, data_json, stock_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_id,
                allocation["material_id"],
                snapshot["material_code"],
                snapshot["name"],
                snapshot["brand_model"],
                snapshot["spec"],
                snapshot["unit"],
                allocation["request_quantity"],
                json.dumps(allocation["data"], ensure_ascii=False),
                allocation["stock_source"],
            ),
        )
        item_ids.append(cursor.lastrowid)
    return item_ids


def current_claim_requested_items(cursor, form_id):
    cursor.execute(
        "SELECT id, material_id, request_quantity, data_json FROM workflow_items WHERE form_id = ? ORDER BY id",
        (form_id,),
    )
    grouped = {}
    order = []
    for row in cursor.fetchall():
        item = dict(row)
        data = json.loads(item.get("data_json") or "{}")
        group_key = str(data.get("allocation_group_key") or f"legacy-item:{item['id']}")
        if group_key not in grouped:
            grouped[group_key] = {
                "material_id": item["material_id"],
                "request_quantity": 0.0,
                "allocation_group_key": group_key,
            }
            order.append(group_key)
        requested_snapshot = data.get("requested_quantity_snapshot")
        if requested_snapshot is not None:
            grouped[group_key]["request_quantity"] = quantity_value(
                requested_snapshot,
                "申领数量",
                positive=True,
            )
        else:
            grouped[group_key]["request_quantity"] += float(item.get("request_quantity") or 0)
    return [grouped[key] for key in order]


def claim_revision_requested_items(cursor, form_id, submitted_items):
    cursor.execute(
        "SELECT id, material_id, data_json FROM workflow_items WHERE form_id = ? ORDER BY id",
        (form_id,),
    )
    current_rows = {int(row["id"]): dict(row) for row in cursor.fetchall()}
    grouped = {}
    order = []
    for submitted in submitted_items or []:
        item_id = int(submitted.get("id") or 0)
        current = current_rows.get(item_id)
        if not current:
            raise ValueError("申领明细不存在或不属于当前流程")
        data = json.loads(current.get("data_json") or "{}")
        group_key = str(
            submitted.get("allocation_group_key")
            or data.get("allocation_group_key")
            or f"legacy-item:{item_id}"
        )
        quantity = quantity_value(submitted.get("request_quantity"), "申领数量", positive=True)
        if group_key not in grouped:
            grouped[group_key] = {
                "material_id": current["material_id"],
                "request_quantity": 0.0,
                "allocation_group_key": group_key,
            }
            order.append(group_key)
        if int(grouped[group_key]["material_id"]) != int(current["material_id"]):
            raise ValueError("同一申领行不能包含不同物料")
        if submitted.get("allocation_group_key"):
            if grouped[group_key]["request_quantity"] > 0:
                raise ValueError("同一申领行不能重复提交")
            grouped[group_key]["request_quantity"] = quantity
        else:
            grouped[group_key]["request_quantity"] += quantity
    if not order:
        raise ValueError("申领单至少需要一行物料")
    return [grouped[key] for key in order]
