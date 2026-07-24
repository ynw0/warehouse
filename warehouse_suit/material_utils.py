"""Small material, stock, and code utility helpers."""

from __future__ import annotations

from .db import now_text


def stock_record_display_type(record):
    operation_type = record.get("operation_type") or ""
    remark = str(record.get("remark") or "").lower()
    if "归还" in remark or "return" in remark:
        return "return"
    if "借用" in remark or "borrow" in remark:
        return "borrow"
    return operation_type


def numeric_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stock_snapshot_payload(quantity, snapshot_at=None, source="form_create"):
    quantity = float(quantity or 0)
    return {
        "stock_quantity_snapshot": quantity,
        "available_quantity_snapshot": quantity,
        "stock_snapshot_at": snapshot_at or now_text(),
        "stock_snapshot_source": source,
    }


def locked_stock_quantity(item_data, current_quantity):
    snapshot = numeric_or_none((item_data or {}).get("stock_quantity_snapshot"))
    if snapshot is None:
        snapshot = numeric_or_none((item_data or {}).get("available_quantity_snapshot"))
    return float(current_quantity or 0) if snapshot is None else snapshot


def clean_material_name(material_code, name):
    material_code = str(material_code or "").strip()
    value = str(name or "").strip()
    if not material_code or not value:
        return value
    while value == material_code or value.startswith(material_code + " "):
        value = value[len(material_code):].strip()
    return value or str(name or "").strip()


def normalize_code_part(value, width):
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValueError(f"code part must be numeric: {value}")
    return text.zfill(width)


def infer_code_parts(material_code):
    code = str(material_code or "").strip()
    if len(code) != 14 or not code.isdigit():
        return {}
    return {
        "warehouse_code": code[2:4],
        "major_code": code[4:6],
        "middle_code": code[6:8],
        "small_code": code[8:10],
        "detail_code": code[10:14],
    }


def default_edit_batch_no(received_date, material_code):
    date_part = "".join(ch for ch in str(received_date or "") if ch.isdigit())[:8] or "00000000"
    return f"{date_part}{material_code or ''}"

