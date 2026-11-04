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
from warehouse_suit.purchase_project import (
    require_same_purchase_project,
    validate_purchase_project,
)
from warehouse_suit.stock_allocation_service import (
    allocate_stock_sources,
    held_source_quantities,
    occupancy_text,
    pending_outbound_hold_index,
    stock_source_quantities as claim_stock_quantities,
)
from warehouse_suit.usage_kind import USAGE_RD, validate_usage_kind
from warehouse_suit.validation import quantity_value


def claim_material_rows(cursor, keyword="", include_temporary=False, limit=100, usage_kind=USAGE_RD, purchase_project=None, formal_only=False):
    usage_kind = validate_usage_kind(usage_kind)
    keyword = str(keyword or "").strip()
    project = "" if purchase_project is None else str(purchase_project or "").strip()
    like = f"%{keyword}%"
    limit = max(1, min(int(limit or 100), 200))
    cursor.execute(
        """
        WITH source_stock AS (
            SELECT
                b.material_id,
                TRIM(COALESCE(b.purchase_project, '')) AS purchase_project,
                SUM(
                    CASE WHEN b.stock_source = ? AND b.inventory_status = ?
                         THEN b.quantity
                         ELSE 0 END
                ) AS formal_quantity,
                SUM(
                    CASE WHEN b.stock_source = ? AND b.inventory_status = ?
                         THEN b.quantity ELSE 0 END
                ) AS temporary_quantity,
                SUM(CASE WHEN b.stock_source = ? THEN 1 ELSE 0 END)
                    AS formal_batch_count
            FROM material_batches b
            WHERE TRIM(COALESCE(b.purchase_project, '')) <> ''
              AND TRIM(COALESCE(b.usage_kind, '')) = ?
            GROUP BY b.material_id, TRIM(COALESCE(b.purchase_project, ''))
        )
        SELECT
            m.id,
            m.material_code,
            m.name,
            m.brand_model,
            m.spec,
            m.unit,
            m.purchase_applicant,
            ss.purchase_project,
            CASE
                WHEN COALESCE(ss.formal_batch_count, 0) > 0
                THEN COALESCE(ss.formal_quantity, 0)
                ELSE 0
            END AS formal_available_quantity,
            CASE WHEN ? THEN COALESCE(ss.temporary_quantity, 0) ELSE 0 END
                AS temporary_available_quantity,
            (
                CASE
                    WHEN COALESCE(ss.formal_batch_count, 0) > 0
                    THEN COALESCE(ss.formal_quantity, 0)
                    ELSE 0
                END
                + CASE WHEN ? THEN COALESCE(ss.temporary_quantity, 0) ELSE 0 END
            ) AS total_available_quantity
        FROM materials m
        JOIN source_stock ss ON ss.material_id = m.id
        WHERE (
            ? = ''
            OR m.material_code LIKE ?
            OR m.name LIKE ?
            OR COALESCE(m.brand_model, '') LIKE ?
            OR COALESCE(m.spec, '') LIKE ?
            OR COALESCE(m.purchase_applicant, '') LIKE ?
            OR COALESCE(ss.purchase_project, '') LIKE ?
        )
          AND (? = '' OR ss.purchase_project = ?)
          AND (? = 0 OR (COALESCE(ss.formal_batch_count, 0) > 0 AND COALESCE(ss.formal_quantity, 0) > 0))
        ORDER BY
            CASE
                WHEN (
                    CASE
                        WHEN COALESCE(ss.formal_batch_count, 0) > 0
                        THEN COALESCE(ss.formal_quantity, 0)
                        ELSE 0
                    END
                    + CASE WHEN ? THEN COALESCE(ss.temporary_quantity, 0) ELSE 0 END
                ) > 0 THEN 0
                ELSE 1
            END,
            m.material_code,
            ss.purchase_project,
            m.id
        LIMIT ?
        """,
        (
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_TEMPORARY,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_FORMAL,
            usage_kind,
            int(bool(include_temporary)),
            int(bool(include_temporary)),
            keyword,
            like,
            like,
            like,
            like,
            like,
            like,
            project,
            project,
            int(bool(formal_only)),
            int(bool(include_temporary)),
            limit,
        ),
    )
    fetched = [dict(row) for row in cursor.fetchall()]
    holds = pending_outbound_hold_index(cursor)
    rows = []
    for item in fetched:
        formal = float(item.get("formal_available_quantity") or 0)
        temporary = float(item.get("temporary_available_quantity") or 0)
        held_formal, held_temporary = held_source_quantities(
            holds, item["id"], item.get("purchase_project"), usage_kind
        )
        item["formal_available_quantity"] = max(0.0, formal - held_formal)
        item["temporary_available_quantity"] = max(0.0, temporary - held_temporary)
        item["total_available_quantity"] = (
            item["formal_available_quantity"] + item["temporary_available_quantity"]
        )
        item["quantity"] = item["total_available_quantity"]
        rows.append(item)
    return rows


def allocate_claim_items(
    cursor, requested_items, applicant, include_temporary, usage_kind=USAGE_RD, exclude_form_id=None
):
    usage_kind = validate_usage_kind(usage_kind)
    if not requested_items:
        raise ValueError("申领单至少需要一行物料")

    available_by_key = {}
    allocations = []
    applicant_name = applicant.get("display_name") or applicant.get("username") or ""
    row_projects = []
    prepared = []

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
        purchase_project = validate_purchase_project(cursor, requested_item.get("purchase_project"))
        row_projects.append(purchase_project)
        requested_quantity = quantity_value(
            requested_item.get("request_quantity")
            if requested_item.get("request_quantity") is not None
            else requested_item.get("requested_quantity"),
            "申领数量",
            positive=True,
        )
        item_usage = validate_usage_kind(requested_item.get("usage_kind") or usage_kind)
        if item_usage != usage_kind:
            raise ValueError("一张申领单只允许一种用料形式")
        cache_key = (material_id, purchase_project, usage_kind)
        if cache_key not in available_by_key:
            available_by_key[cache_key] = claim_stock_quantities(
                cursor,
                material_id,
                temporary_enabled=include_temporary,
                purchase_project=purchase_project,
                usage_kind=usage_kind,
                exclude_form_id=exclude_form_id,
            )
        group_key = str(requested_item.get("allocation_group_key") or "").strip()
        if not group_key:
            group_key = f"claim-row:{index + 1}:{material_id}:{purchase_project}"
        prepared.append({
            "material_id": material_id,
            "snapshot": snapshot,
            "purchase_project": purchase_project,
            "requested_quantity": requested_quantity,
            "cache_key": cache_key,
            "group_key": group_key,
        })

    # 先按正式库优先看这张单会不会用到临时库。用到了就整单只留一种物料，并且只从临时库扣。
    simulation = {key: dict(value) for key, value in available_by_key.items()}
    needs_temporary = False
    for row in prepared:
        available = simulation[row["cache_key"]]
        formal_available = float(available.get("formal") or 0)
        temporary_available = float(available.get("temporary") or 0) if include_temporary else 0.0
        formal_quantity = min(row["requested_quantity"], formal_available)
        temporary_quantity = min(max(row["requested_quantity"] - formal_quantity, 0.0), temporary_available)
        if temporary_quantity > 1e-9:
            needs_temporary = True
        available["formal"] = formal_available - formal_quantity
        available["temporary"] = temporary_available - temporary_quantity

    if needs_temporary and len({row["material_id"] for row in prepared}) != 1:
        raise ValueError("领用临时库物料时，单次只能领用一种物料")

    for row in prepared:
        available = available_by_key[row["cache_key"]]
        requested_quantity = row["requested_quantity"]
        snapshot = row["snapshot"]
        if needs_temporary:
            temporary_available = float(available.get("temporary") or 0)
            if requested_quantity > temporary_available + 1e-9:
                raise ValueError(
                    f"{snapshot['name']} 临时库可用数量不足"
                    f"{occupancy_text(cursor, row['material_id'], row['purchase_project'], usage_kind, exclude_form_id)}"
                )
            formal_quantity = 0.0
            temporary_quantity = requested_quantity
            total_available = temporary_available
        else:
            source_allocation = allocate_stock_sources(
                cursor,
                row["material_id"],
                requested_quantity,
                False,
                available_quantities=available,
            )
            total_available = source_allocation["available"]["total"]
            if source_allocation["shortfall"] > 1e-9:
                raise ValueError(
                    f"{snapshot['name']} 申领数量不能大于可用库存"
                    f"（正式库 {available['formal']:g}，临时库 {available['temporary']:g}）"
                    f"{occupancy_text(cursor, row['material_id'], row['purchase_project'], usage_kind, exclude_form_id)}"
                )
            formal_quantity = source_allocation["formal"]
            temporary_quantity = 0.0

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
                "allocation_group_key": row["group_key"],
                "purchase_project": row["purchase_project"],
                "usage_kind": usage_kind,
                "requested_quantity_snapshot": requested_quantity,
                "formal_available_quantity_snapshot": available["formal"],
                "temporary_available_quantity_snapshot": available["temporary"],
                "total_available_quantity_snapshot": total_available,
                **stock_snapshot_payload(source_available),
            }
            allocations.append(
                {
                    "material_id": row["material_id"],
                    "material": snapshot,
                    "request_quantity": allocated_quantity,
                    "stock_source": stock_source,
                    "purchase_project": row["purchase_project"],
                    "data": item_data,
                }
            )
        available["formal"] -= formal_quantity
        available["temporary"] -= temporary_quantity
    require_same_purchase_project(row_projects)
    return allocations


def insert_claim_allocations(cursor, form_id, allocations):
    item_ids = []
    for allocation in allocations:
        snapshot = allocation["material"]
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, brand_model, spec,
                 unit, request_quantity, data_json, stock_source, purchase_project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                allocation.get("purchase_project") or allocation["data"].get("purchase_project") or "",
            ),
        )
        item_ids.append(cursor.lastrowid)
    return item_ids


def current_claim_requested_items(cursor, form_id):
    cursor.execute(
        """
        SELECT id, material_id, request_quantity, purchase_project, data_json
        FROM workflow_items WHERE form_id = ? ORDER BY id
        """,
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
                "purchase_project": item.get("purchase_project") or data.get("purchase_project") or "",
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
        "SELECT id, material_id, purchase_project, data_json FROM workflow_items WHERE form_id = ? ORDER BY id",
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
        purchase_project = (
            submitted.get("purchase_project")
            or current.get("purchase_project")
            or data.get("purchase_project")
            or ""
        )
        if group_key not in grouped:
            grouped[group_key] = {
                "material_id": current["material_id"],
                "request_quantity": 0.0,
                "allocation_group_key": group_key,
                "purchase_project": purchase_project,
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
