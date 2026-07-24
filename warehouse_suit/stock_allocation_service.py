"""Shared formal-first stock source allocation helpers."""

from __future__ import annotations

from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.material_repository import material_stock_total


def stock_source_quantities(cursor, material_id, temporary_enabled):
    formal = material_stock_total(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL)
    temporary = (
        material_stock_total(cursor, material_id, stock_source=STOCK_SOURCE_TEMPORARY)
        if temporary_enabled
        else 0.0
    )
    return {
        "formal": float(formal or 0),
        "temporary": float(temporary or 0),
        "total": float(formal or 0) + float(temporary or 0),
    }


def allocate_stock_sources(
    cursor,
    material_id,
    requested_quantity,
    temporary_enabled,
    available_quantities=None,
):
    """Return a formal-first allocation without writing workflow-specific data."""
    requested = float(requested_quantity or 0)
    if requested <= 0:
        raise ValueError("申请数量必须大于 0")
    available = dict(
        available_quantities
        or stock_source_quantities(cursor, material_id, temporary_enabled)
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
        },
    }
