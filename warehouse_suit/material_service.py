# -*- coding: utf-8 -*-
"""Material master, numbering, position, and batch services."""

from warehouse_suit.db import now_text, row_to_dict, today_text
from warehouse_suit.inventory_constants import INVENTORY_STATUS_AVAILABLE, STOCK_SOURCE_FORMAL
from warehouse_suit.inventory_service import update_inventory_total
from warehouse_suit.material_repository import fetch_material
from warehouse_suit.reservation_service import batch_reserved_quantity
from warehouse_suit.material_utils import (
    clean_material_name,
    default_edit_batch_no,
    infer_code_parts,
    normalize_code_part,
)
from warehouse_suit.validation import (
    payload_int_or_none,
    price_value,
    quantity_value,
    validate_batch_no,
    validate_material_code_value,
)


def next_material_code(cursor, warehouse_code, major_code, middle_code, small_code, name="", brand_model="", spec=""):
    warehouse_code = normalize_code_part(warehouse_code, 2)
    major_code = normalize_code_part(major_code, 2)
    middle_code = normalize_code_part(middle_code, 2)
    small_code = normalize_code_part(small_code, 2)
    prefix = f"10{warehouse_code}{major_code}{middle_code}{small_code}"

    cursor.execute(
        "SELECT material_code, name, brand_model, spec FROM materials WHERE material_code LIKE ?",
        (f"{prefix}____",),
    )
    rows = cursor.fetchall()
    details = []
    same_name_details = []
    tight_details = []
    for row in rows:
        code = row["material_code"]
        if not code or len(code) != 14 or not code[-4:].isdigit():
            continue
        detail = int(code[-4:])
        details.append(detail)
        if name and (row["name"] or "").strip() == name.strip():
            same_name_details.append(detail)
            row_brand = (row["brand_model"] or "").strip()
            row_spec = (row["spec"] or "").strip()
            if (brand_model and row_brand == str(brand_model).strip()) or (spec and row_spec == str(spec).strip()):
                tight_details.append(detail)

    name_text = str(name or "").lower()
    chip_like = (
        "芯片" in name_text
        or "ic" in name_text
        or (major_code == "01" and middle_code in {"06", "09", "14", "17", "19", "20", "21", "40"})
    )
    if chip_like:
        detail = (max(details) + 1) if details else 1
        step = 1
    elif tight_details:
        detail = max(tight_details) + 1
        step = 1
    elif details:
        detail = (max(details) // 10 + 1) * 10
        step = 10
    else:
        detail = 10
        step = 10

    for _ in range(10000):
        candidate = f"{prefix}{detail:04d}"
        cursor.execute("SELECT 1 FROM materials WHERE material_code = ?", (candidate,))
        if not cursor.fetchone():
            return candidate, step
        detail += 1 if step == 1 else 10
    raise ValueError("no available material code")


def upsert_material_master(cursor, data):
    material_code = validate_material_code_value(data.get("material_code"))
    name = clean_material_name(material_code, data.get("name") or "")
    if not name:
        raise ValueError("name is required")
    parts = infer_code_parts(material_code)
    warehouse_code = data.get("warehouse_code") or parts.get("warehouse_code", "")
    major_code = data.get("major_code") or data.get("category") or parts.get("major_code", "")
    middle_code = data.get("middle_code") or parts.get("middle_code", "")
    small_code = data.get("small_code") or parts.get("small_code", "")
    detail_code = data.get("detail_code") or parts.get("detail_code", "")
    sub_category = data.get("sub_category") or f"{middle_code}{small_code}".strip()

    cursor.execute(
        """
        INSERT INTO materials
            (material_code, brand_model, spec, name, category, sub_category, unit, icon,
             warehouse_code, major_code, middle_code, small_code, detail_code,
             category_name, material_type, purchase_applicant, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(material_code) DO UPDATE SET
            brand_model = excluded.brand_model,
            spec = excluded.spec,
            name = excluded.name,
            category = excluded.category,
            sub_category = excluded.sub_category,
            unit = excluded.unit,
            icon = excluded.icon,
            warehouse_code = excluded.warehouse_code,
            major_code = excluded.major_code,
            middle_code = excluded.middle_code,
            small_code = excluded.small_code,
            detail_code = excluded.detail_code,
            category_name = excluded.category_name,
            material_type = excluded.material_type,
            purchase_applicant = excluded.purchase_applicant,
            updated_at = excluded.updated_at
        """,
        (
            material_code,
            data.get("brand_model") or "",
            data.get("spec") or "",
            name,
            major_code,
            sub_category,
            data.get("unit") or "个",
            data.get("icon") or "□",
            warehouse_code,
            major_code,
            middle_code,
            small_code,
            detail_code,
            data.get("category_name") or "",
            data.get("material_type") or data.get("type") or "",
            data.get("purchase_applicant") or "",
            now_text(),
        ),
    )
    cursor.execute("SELECT id FROM materials WHERE material_code = ?", (material_code,))
    material_id = cursor.fetchone()["id"]
    cursor.execute(
        "INSERT OR IGNORE INTO inventory (material_id, quantity, updated_at) VALUES (?, ?, ?)",
        (material_id, quantity_value(data.get("quantity"), "初始库存", 0), now_text()),
    )
    return fetch_material(cursor, material_id)


def update_material_master_by_id(cursor, material_id, data):
    cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
    current = cursor.fetchone()
    if not current:
        raise ValueError("material not found")

    material_code = validate_material_code_value(data.get("material_code") or current["material_code"])
    name = str(data.get("name") or current["name"]).strip()
    if not name:
        raise ValueError("name is required")

    cursor.execute("SELECT id FROM materials WHERE material_code = ? AND id <> ?", (material_code, material_id))
    if cursor.fetchone():
        raise ValueError("material_code already exists")

    parts = infer_code_parts(material_code)
    warehouse_code = data.get("warehouse_code") or current["warehouse_code"] or parts.get("warehouse_code", "")
    major_code = data.get("major_code") or data.get("category") or current["major_code"] or parts.get("major_code", "")
    middle_code = data.get("middle_code") or current["middle_code"] or parts.get("middle_code", "")
    small_code = data.get("small_code") or current["small_code"] or parts.get("small_code", "")
    detail_code = data.get("detail_code") or current["detail_code"] or parts.get("detail_code", "")
    sub_category = data.get("sub_category") or current["sub_category"] or f"{middle_code}{small_code}".strip()

    cursor.execute(
        """
        UPDATE materials
        SET material_code = ?, brand_model = ?, spec = ?, name = ?, category = ?,
            sub_category = ?, unit = ?, icon = ?, warehouse_code = ?, major_code = ?,
            middle_code = ?, small_code = ?, detail_code = ?, category_name = ?,
            material_type = ?, purchase_applicant = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            material_code,
            data.get("brand_model") if data.get("brand_model") is not None else current["brand_model"],
            data.get("spec") if data.get("spec") is not None else current["spec"],
            name,
            major_code,
            sub_category,
            data.get("unit") if data.get("unit") is not None else current["unit"],
            data.get("icon") if data.get("icon") is not None else current["icon"],
            warehouse_code,
            major_code,
            middle_code,
            small_code,
            detail_code,
            data.get("category_name") if data.get("category_name") is not None else current["category_name"],
            data.get("material_type") if data.get("material_type") is not None else current["material_type"],
            data.get("purchase_applicant") if data.get("purchase_applicant") is not None else current["purchase_applicant"],
            now_text(),
            material_id,
        ),
    )
    cursor.execute(
        "INSERT OR IGNORE INTO inventory (material_id, quantity, updated_at) VALUES (?, 0, ?)",
        (material_id, now_text()),
    )
    return fetch_material(cursor, material_id)

def material_warehouse_type(cursor, shelf_id=None, warehouse_code="", fallback="office"):
    if shelf_id:
        cursor.execute("SELECT warehouse_type FROM shelves WHERE id = ?", (shelf_id,))
        shelf = cursor.fetchone()
        if shelf and shelf["warehouse_type"]:
            return shelf["warehouse_type"]
    if str(warehouse_code or "").startswith("20"):
        return "rd"
    return fallback or "office"


def write_material_position(cursor, material_id, data):
    if not any(key in data for key in ("shelf_id", "layer_number", "zone_name", "slot_index")):
        return
    shelf_id = payload_int_or_none(data.get("shelf_id"))
    if not shelf_id:
        cursor.execute("DELETE FROM material_positions WHERE material_id = ?", (material_id,))
        return
    cursor.execute("SELECT id FROM shelves WHERE id = ?", (shelf_id,))
    if not cursor.fetchone():
        raise ValueError("shelf not found")
    cursor.execute(
        """
        INSERT INTO material_positions (material_id, shelf_id, layer_number, zone_name, slot_index)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(material_id) DO UPDATE SET
            shelf_id = excluded.shelf_id,
            layer_number = excluded.layer_number,
            zone_name = excluded.zone_name,
            slot_index = excluded.slot_index
        """,
        (
            material_id,
            shelf_id,
            payload_int_or_none(data.get("layer_number")) or 1,
            str(data.get("zone_name") or "").strip().upper(),
            payload_int_or_none(data.get("slot_index")) or 0,
        ),
    )


def save_material_batches_from_payload(cursor, material_id, material_code, old_material_code, data):
    if "batches" not in data:
        return
    batches = data.get("batches") or []
    if not isinstance(batches, list):
        raise ValueError("batches must be a list")

    cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
    material = row_to_dict(cursor.fetchone()) or {}
    cursor.execute(
        "SELECT * FROM material_batches WHERE material_id = ? AND stock_source = ?",
        (material_id, STOCK_SOURCE_FORMAL),
    )
    existing = {row["id"]: dict(row) for row in cursor.fetchall()}
    touched_ids = set()

    for item in batches:
        if not isinstance(item, dict):
            raise ValueError("batch item must be an object")
        batch_id = payload_int_or_none(item.get("id"))
        current = existing.get(batch_id) if batch_id else None
        has_payload = any(str(item.get(key) or "").strip() for key in ("batch_no", "quantity", "unit_price", "received_date", "shelf_id", "layer_number", "zone_name"))
        if not current and not has_payload:
            continue
        quantity = quantity_value(item.get("quantity"), "批次数量", current.get("quantity") if current else 0)
        unit_price = price_value(item.get("unit_price"), "批次单价", current.get("unit_price") if current else 0)
        received_date = str(item.get("received_date") or (current or {}).get("received_date") or today_text()).strip()
        batch_no = str(item.get("batch_no") or (current or {}).get("batch_no") or "").strip()
        if old_material_code and material_code != old_material_code and batch_no.endswith(old_material_code):
            batch_no = f"{batch_no[:-len(old_material_code)]}{material_code}"
        if not batch_no:
            batch_no = default_edit_batch_no(received_date, material_code)
        batch_no = validate_batch_no(batch_no, required=True)
        shelf_id = payload_int_or_none(item.get("shelf_id")) if "shelf_id" in item else (current or {}).get("shelf_id")
        if shelf_id:
            cursor.execute("SELECT id FROM shelves WHERE id = ?", (shelf_id,))
            if not cursor.fetchone():
                raise ValueError("batch shelf not found")
        layer_number = payload_int_or_none(item.get("layer_number")) if "layer_number" in item else (current or {}).get("layer_number")
        zone_name = str(item.get("zone_name") if item.get("zone_name") is not None else (current or {}).get("zone_name") or "").strip().upper()
        warehouse_type = str(item.get("warehouse_type") or (current or {}).get("warehouse_type") or "").strip()
        warehouse_type = material_warehouse_type(cursor, shelf_id, material.get("warehouse_code"), warehouse_type or "office")
        source_form_no = str(item.get("source_form_no") if item.get("source_form_no") is not None else (current or {}).get("source_form_no") or "MANUAL").strip()

        if current:
            reserved = batch_reserved_quantity(cursor, batch_id)
            if quantity + 1e-9 < reserved:
                raise ValueError("批次数量不能低于已预留数量")
            cursor.execute(
                """
                UPDATE material_batches
                SET batch_no = ?, quantity = ?, unit_price = ?, warehouse_type = ?,
                    shelf_id = ?, layer_number = ?, zone_name = ?, source_form_no = ?,
                    received_date = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND material_id = ? AND stock_source = ? AND inventory_status = ?
                  AND ? >= COALESCE(
                      (
                          SELECT SUM(
                              reserved_quantity - consumed_quantity - released_quantity
                          )
                          FROM inventory_reservations
                          WHERE formal_batch_id = material_batches.id
                            AND status = 'active'
                            AND reserved_quantity - consumed_quantity - released_quantity > 0
                      ),
                      0
                  )
                """,
                (
                    batch_no,
                    quantity,
                    unit_price,
                    warehouse_type,
                    shelf_id,
                    layer_number,
                    zone_name,
                    source_form_no,
                    received_date,
                    now_text(),
                    batch_id,
                    material_id,
                    STOCK_SOURCE_FORMAL,
                    INVENTORY_STATUS_AVAILABLE,
                    quantity,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("批次数量或预留状态已变化，请刷新后重试")
            touched_ids.add(batch_id)
        elif quantity > 0 or batch_no:
            cursor.execute(
                """
                INSERT INTO material_batches
                    (material_id, batch_no, quantity, unit_price, warehouse_type,
                     shelf_id, layer_number, zone_name, source_form_no, received_date,
                     stock_source, inventory_status, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    material_id,
                    batch_no,
                    quantity,
                    unit_price,
                    warehouse_type,
                    shelf_id,
                    layer_number,
                    zone_name,
                    source_form_no,
                    received_date,
                    STOCK_SOURCE_FORMAL,
                    INVENTORY_STATUS_AVAILABLE,
                    now_text(),
                    now_text(),
                ),
            )
            touched_ids.add(cursor.lastrowid)

    for batch_id in set(existing) - touched_ids:
        if batch_reserved_quantity(cursor, batch_id) > 1e-9:
            raise ValueError("已有有效预留的批次不能删除")
        cursor.execute("SELECT COUNT(*) FROM material_attachments WHERE material_batch_id = ?", (batch_id,))
        if int(cursor.fetchone()[0] or 0) > 0:
            raise ValueError("已有附件的批次需要永久留存，不能删除")
        cursor.execute("UPDATE stock_records SET batch_id = NULL WHERE batch_id = ?", (batch_id,))
        cursor.execute("UPDATE production_material_consumptions SET batch_id = NULL WHERE batch_id = ?", (batch_id,))
        cursor.execute(
            """
            DELETE FROM material_batches
            WHERE id = ? AND material_id = ? AND stock_source = ?
              AND NOT EXISTS (
                  SELECT 1 FROM inventory_reservations
                  WHERE formal_batch_id = material_batches.id
                    AND status = 'active'
                    AND reserved_quantity - consumed_quantity - released_quantity > 0
              )
            """,
            (batch_id, material_id, STOCK_SOURCE_FORMAL),
        )
        if cursor.rowcount != 1:
            raise ValueError("批次预留状态已变化，不能删除")
    update_inventory_total(cursor, material_id)
