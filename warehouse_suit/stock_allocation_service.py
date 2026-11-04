"""Shared formal-first stock source allocation helpers."""

from __future__ import annotations

from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.material_repository import material_stock_total

# 已办结、驳回、作废和草稿都不占用库存。退回修改仍算未出库，继续占用。
_CLOSED_FORM_STATUSES = ("completed", "rejected", "cancelled", "draft")


_HOLD_VERBS = {
    "claim": "领用",
    "borrow": "借用",
    "special_claim": "领用",
}


def _quantity_text(value):
    number = float(value or 0)
    if abs(number - round(number)) <= 1e-9:
        return str(int(round(number)))
    return f"{number:g}"


def occupancy_text(cursor, material_id, purchase_project=None, usage_kind=None, exclude_form_id=None):
    """提交被拒绝时点出是谁的未出库申请占着数量。"""
    sql = """
        SELECT
            f.form_type,
            CASE
                WHEN COALESCE(wi.request_quantity, 0) > COALESCE(wi.outbound_quantity, 0)
                THEN COALESCE(wi.request_quantity, 0) - COALESCE(wi.outbound_quantity, 0)
                ELSE 0
            END AS held,
            TRIM(COALESCE(wi.unit, '')) AS unit,
            TRIM(COALESCE(NULLIF(u.display_name, ''), u.username, '')) AS applicant_name
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE f.form_type IN ('claim', 'borrow', 'special_claim')
          AND f.status NOT IN (?, ?, ?, ?)
          AND wi.material_id = ?
          AND (
                f.form_type <> 'borrow'
                OR COALESCE(json_extract(wi.data_json, '$.borrow_item_type'), 'material') = 'material'
          )
    """
    params = [*_CLOSED_FORM_STATUSES, int(material_id)]
    if purchase_project is not None:
        sql += " AND TRIM(COALESCE(wi.purchase_project, '')) = ?"
        params.append(str(purchase_project or "").strip())
    if usage_kind is not None:
        sql += " AND TRIM(COALESCE(json_extract(wi.data_json, '$.usage_kind'), '')) = ?"
        params.append(str(usage_kind or "").strip())
    if exclude_form_id:
        sql += " AND f.id <> ?"
        params.append(int(exclude_form_id))
    sql += " ORDER BY f.id, wi.id"
    cursor.execute(sql, params)
    grouped = {}
    order = []
    for row in cursor.fetchall():
        held = float(row["held"] or 0)
        if held <= 1e-9:
            continue
        name = str(row["applicant_name"] or "").strip() or "申请人"
        verb = _HOLD_VERBS.get(str(row["form_type"] or ""), "领用")
        unit = str(row["unit"] or "").strip() or "个"
        key = (name, verb, unit)
        if key not in grouped:
            order.append(key)
            grouped[key] = 0.0
        grouped[key] += held
    if not order:
        return ""
    phrases = [
        f"{name}已申请{verb}{_quantity_text(grouped[key])}{unit}"
        for key in order
        for name, verb, unit in [key]
    ]
    return "，" + "，".join(phrases)


def pending_outbound_hold_index(cursor, material_id=None, exclude_form_id=None):
    """未出库的领用、借用、特殊用料占用。

    键是 (物料, 采购项目, 用料形式, 库别)。已出库部分不再占用，只留申请数减出库数。
    exclude_form_id 用于本单退回后重提，避免把自己算进去。
    """
    sql = """
        SELECT
            wi.material_id,
            TRIM(COALESCE(wi.purchase_project, '')) AS purchase_project,
            TRIM(COALESCE(json_extract(wi.data_json, '$.usage_kind'), '')) AS usage_kind,
            CASE
                WHEN f.form_type = 'special_claim' THEN ?
                WHEN TRIM(COALESCE(wi.stock_source, '')) = '' THEN ?
                ELSE TRIM(wi.stock_source)
            END AS stock_source,
            CASE
                WHEN COALESCE(wi.request_quantity, 0) > COALESCE(wi.outbound_quantity, 0)
                THEN COALESCE(wi.request_quantity, 0) - COALESCE(wi.outbound_quantity, 0)
                ELSE 0
            END AS held
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type IN ('claim', 'borrow', 'special_claim')
          AND f.status NOT IN (?, ?, ?, ?)
          AND wi.material_id IS NOT NULL
          AND (
                f.form_type <> 'borrow'
                OR COALESCE(json_extract(wi.data_json, '$.borrow_item_type'), 'material') = 'material'
          )
    """
    params = [
        STOCK_SOURCE_FORMAL,
        STOCK_SOURCE_FORMAL,
        *_CLOSED_FORM_STATUSES,
    ]
    if material_id is not None:
        sql += " AND wi.material_id = ?"
        params.append(int(material_id))
    if exclude_form_id:
        sql += " AND f.id <> ?"
        params.append(int(exclude_form_id))
    cursor.execute(sql, params)
    index = {}
    for row in cursor.fetchall():
        held = float(row["held"] or 0)
        if held <= 1e-9:
            continue
        key = (
            int(row["material_id"]),
            str(row["purchase_project"] or ""),
            str(row["usage_kind"] or ""),
            str(row["stock_source"] or STOCK_SOURCE_FORMAL),
        )
        index[key] = index.get(key, 0.0) + held
    return index


def held_source_quantities(index, material_id, purchase_project=None, usage_kind=None):
    """从占用索引里取出正式库、临时库数量。项目或用料形式为 None 时不限该项。"""
    formal = 0.0
    temporary = 0.0
    project = None if purchase_project is None else str(purchase_project or "").strip()
    usage = None if usage_kind is None else str(usage_kind or "").strip()
    for (row_material, row_project, row_usage, source), quantity in index.items():
        if int(row_material) != int(material_id):
            continue
        if project is not None and row_project != project:
            continue
        if usage is not None and row_usage != usage:
            continue
        if source == STOCK_SOURCE_TEMPORARY:
            temporary += quantity
        else:
            formal += quantity
    return formal, temporary


def stock_source_quantities(
    cursor,
    material_id,
    temporary_enabled,
    purchase_project=None,
    usage_kind=None,
    exclude_form_id=None,
):
    formal = material_stock_total(
        cursor,
        material_id,
        stock_source=STOCK_SOURCE_FORMAL,
        purchase_project=purchase_project,
        usage_kind=usage_kind,
    )
    temporary = (
        material_stock_total(
            cursor,
            material_id,
            stock_source=STOCK_SOURCE_TEMPORARY,
            purchase_project=purchase_project,
            usage_kind=usage_kind,
        )
        if temporary_enabled
        else 0.0
    )
    holds = pending_outbound_hold_index(
        cursor, material_id=material_id, exclude_form_id=exclude_form_id
    )
    held_formal, held_temporary = held_source_quantities(
        holds, material_id, purchase_project, usage_kind
    )
    formal_left = max(0.0, float(formal or 0) - held_formal)
    temporary_left = max(0.0, float(temporary or 0) - held_temporary) if temporary_enabled else 0.0
    return {
        "formal": formal_left,
        "temporary": temporary_left,
        "total": formal_left + temporary_left,
        "held_formal": held_formal,
        "held_temporary": held_temporary,
    }


def allocate_stock_sources(
    cursor,
    material_id,
    requested_quantity,
    temporary_enabled,
    available_quantities=None,
    exclude_form_id=None,
):
    """Return a formal-first allocation without writing workflow-specific data."""
    requested = float(requested_quantity or 0)
    if requested <= 0:
        raise ValueError("申请数量必须大于 0")
    available = dict(
        available_quantities
        or stock_source_quantities(
            cursor,
            material_id,
            temporary_enabled,
            exclude_form_id=exclude_form_id,
        )
    )
    formal_available = max(float(available.get("formal") or 0), 0.0)
    temporary_available = (
        max(float(available.get("temporary") or 0), 0.0) if temporary_enabled else 0.0
    )
    formal = min(requested, formal_available)
    temporary = min(max(requested - formal, 0.0), temporary_available)
    allocated = formal + temporary
    return {
        "formal": formal,
        "temporary": temporary,
        "allocated": allocated,
        "shortfall": max(requested - allocated, 0.0),
        "available": {
            "formal": formal_available,
            "temporary": temporary_available,
            "total": formal_available + temporary_available,
            "held_formal": float(available.get("held_formal") or 0),
            "held_temporary": float(available.get("held_temporary") or 0),
        },
    }
