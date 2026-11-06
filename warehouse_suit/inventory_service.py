# -*- coding: utf-8 -*-
"""Inventory, borrowing, scrapping, and item-history domain services."""

import json

from warehouse_suit.db import now_text, row_to_dict, today_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_BORROW_OUTBOUND,
    BUSINESS_TYPE_BORROW_RETURN_INBOUND,
    BUSINESS_TYPE_MANUAL,
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
    validate_stock_source,
)
from warehouse_suit.material_repository import (
    material_batch_rows,
    material_query,
    material_snapshot,
    material_stock_total,
)
from warehouse_suit.stock_allocation_service import held_source_quantities, pending_outbound_hold_index
from warehouse_suit.material_utils import clean_material_name
from warehouse_suit.purchase_project import purchase_project_from_acceptance_code
from warehouse_suit.settings import parse_json
from warehouse_suit.validation import positive_int_value, price_value, quantity_value, validated_number


_material_upsert_provider = None


def configure_material_upsert_provider(provider):
    global _material_upsert_provider
    _material_upsert_provider = provider


def _upsert_material_master(cursor, payload):
    if _material_upsert_provider is None:
        raise RuntimeError("material upsert provider is not configured")
    return _material_upsert_provider(cursor, payload)


def parse_batch_allocations(value):
    allocations = []
    for item in value or []:
        raw_batch_id = item.get("batch_id") or item.get("id")
        raw_quantity = item.get("quantity") if item.get("quantity") is not None else item.get("outbound_quantity")
        if raw_batch_id in (None, "") and raw_quantity in (None, ""):
            continue
        batch_id = positive_int_value(raw_batch_id, "批次")
        quantity = quantity_value(raw_quantity, "批次出库数量")
        if batch_id and quantity > 0:
            allocations.append({"batch_id": batch_id, "quantity": quantity})
    return allocations


def cleanup_code_prefixed_material_names(cursor):
    cursor.execute("SELECT id, material_code, name FROM materials WHERE COALESCE(material_code, '') <> ''")
    for row in cursor.fetchall():
        cleaned = clean_material_name(row["material_code"], row["name"])
        if cleaned and cleaned != row["name"]:
            cursor.execute("UPDATE materials SET name = ?, updated_at = ? WHERE id = ?", (cleaned, now_text(), row["id"]))
    cursor.execute("SELECT id, material_code, material_name FROM workflow_items WHERE COALESCE(material_code, '') <> ''")
    for row in cursor.fetchall():
        cleaned = clean_material_name(row["material_code"], row["material_name"])
        if cleaned and cleaned != row["material_name"]:
            cursor.execute("UPDATE workflow_items SET material_name = ? WHERE id = ?", (cleaned, row["id"]))


def material_id_from_payload(cursor, item, require_existing=False):
    material_id = int(item.get("material_id") or 0)
    if material_id:
        cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
        if cursor.fetchone():
            return material_id
    material_code = str(item.get("material_code") or "").strip()
    if material_code:
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (material_code,))
        row = cursor.fetchone()
        if row:
            return row["id"]
    if require_existing:
        name = clean_material_name(material_code, item.get("material_name") or item.get("name") or "")
        brand_model = str(item.get("brand_model") or "").strip()
        spec = str(item.get("spec") or "").strip()
        if name:
            params = [name]
            where = ["name = ?"]
            if brand_model:
                where.append("COALESCE(brand_model, '') = ?")
                params.append(brand_model)
            if spec:
                where.append("COALESCE(spec, '') = ?")
                params.append(spec)
            cursor.execute(f"SELECT id FROM materials WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 1", params)
            row = cursor.fetchone()
            if row:
                return row["id"]
    return 0


def ensure_material_from_payload(cursor, item):
    material_id = material_id_from_payload(cursor, item)
    if material_id:
        return material_id
    material_code = str(item.get("material_code") or "").strip()
    name = clean_material_name(material_code, item.get("material_name") or item.get("name") or "")
    if not material_code or not name:
        raise ValueError("每行物料必须选择已有物料，或填写物料编号和物料名称")
    material = _upsert_material_master(
        cursor,
        {
            "material_code": material_code,
            "name": name,
            "brand_model": item.get("brand_model") or "",
            "spec": item.get("spec") or "",
            "unit": item.get("unit") or "个",
            "warehouse_code": item.get("warehouse_code") or "",
            "major_code": item.get("major_code") or "",
            "middle_code": item.get("middle_code") or "",
            "small_code": item.get("small_code") or "",
            "detail_code": item.get("detail_code") or "",
            "category_name": item.get("category_name") or "",
            "material_type": item.get("material_type") or "",
            "purchase_applicant": item.get("purchase_applicant") or "",
        },
    )
    return material["id"]


def begin_inventory_transaction(conn):
    """Acquire the SQLite write lock without taking ownership of commit/rollback."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def update_inventory_total(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL):
    stock_source = validate_stock_source(stock_source)
    cursor.execute(
        """
        SELECT COALESCE(SUM(quantity), 0), COALESCE(SUM(quantity * unit_price), 0)
        FROM material_batches
        WHERE material_id = ?
          AND stock_source = ?
          AND inventory_status = ?
        """,
        (material_id, stock_source, INVENTORY_STATUS_AVAILABLE),
    )
    quantity, amount = cursor.fetchone()
    quantity = float(quantity or 0)
    amount = float(amount or 0)
    if stock_source == STOCK_SOURCE_FORMAL:
        cursor.execute(
            """
            INSERT INTO inventory (material_id, quantity, amount, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(material_id) DO UPDATE SET
                quantity = excluded.quantity,
                amount = excluded.amount,
                updated_at = excluded.updated_at
            """,
            (material_id, quantity, amount, now_text()),
        )
    return quantity, amount


def _existing_operation_rows(cursor, operation_key):
    operation_key = str(operation_key or "").strip()
    if not operation_key:
        return []
    prefix = operation_key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ":%"
    cursor.execute(
        """
        SELECT *
        FROM stock_records
        WHERE operation_key = ? OR operation_key LIKE ? ESCAPE '\\'
        ORDER BY id
        """,
        (operation_key, prefix),
    )
    return [dict(row) for row in cursor.fetchall()]


def _validate_existing_operation(rows, material_id, stock_source, operation_type, quantity):
    if not rows:
        return
    if any(
        int(row["material_id"]) != int(material_id)
        or row["stock_source"] != stock_source
        or row["operation_type"] != operation_type
        for row in rows
    ):
        raise ValueError("库存操作幂等键已被其他业务使用")
    recorded = sum(float(row.get("quantity") or 0) for row in rows)
    if abs(recorded - float(quantity)) > 1e-6:
        raise ValueError("库存操作幂等键对应的数量不一致")


def validate_inventory_location(cursor, warehouse_type="", shelf_id=0):
    warehouse_type = str(warehouse_type or "").strip()
    if warehouse_type and warehouse_type not in {"office", "rd"}:
        raise ValueError("仓库类型必须为办公库或研发库")
    shelf_id = int(shelf_id or 0)
    if not shelf_id:
        return warehouse_type or "office", None
    cursor.execute("SELECT warehouse_type FROM shelves WHERE id = ?", (shelf_id,))
    shelf = cursor.fetchone()
    if not shelf:
        raise ValueError("选择的货架不存在")
    shelf_warehouse_type = str(shelf["warehouse_type"] or "").strip()
    if warehouse_type and warehouse_type != shelf_warehouse_type:
        raise ValueError("货架与仓库类型不一致")
    return shelf_warehouse_type, shelf_id


def add_inventory_batch(
    cursor,
    material_id,
    quantity,
    unit_price,
    location,
    form_no,
    stock_source=STOCK_SOURCE_FORMAL,
    business_type=BUSINESS_TYPE_MANUAL,
    operation_key=None,
    operator_id=None,
):
    stock_source = validate_stock_source(stock_source)
    quantity = quantity_value(quantity, "入库数量", positive=True)
    unit_price = price_value(unit_price, "入库单价")
    business_type = str(business_type or BUSINESS_TYPE_MANUAL).strip()
    operation_key = str(operation_key or "").strip() or None
    location = dict(location or {})
    begin_inventory_transaction(cursor.connection)
    warehouse_type, shelf_id = validate_inventory_location(
        cursor, location.get("warehouse_type"), location.get("shelf_id")
    )

    existing = _existing_operation_rows(cursor, operation_key)
    if existing:
        _validate_existing_operation(existing, material_id, stock_source, "in", quantity)
        return int(existing[0].get("batch_id") or 0)

    batch_no = f"{today_text().replace('-', '')}{material_snapshot(cursor, material_id, stock_source=stock_source).get('material_code', '')}"
    purchase_project = str(location.get("purchase_project") or "").strip()
    from warehouse_suit.usage_kind import USAGE_RD, pack_quantity_value, validate_usage_kind

    pack_quantity = pack_quantity_value(location.get("pack_quantity") or 1)
    usage_kind = validate_usage_kind(location.get("usage_kind") or USAGE_RD)
    arrival_unit = str(location.get("arrival_unit") or "").strip()
    purchase_applicant = str(location.get("purchase_applicant") or "").strip()
    raw_book = location.get("book_quantity")
    book_quantity = quantity_value(
        raw_book if raw_book not in (None, "") else quantity / pack_quantity,
        "账面入库数量",
        positive=True,
    )
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, shelf_id, layer_number,
             zone_name, source_form_no, received_date, stock_source, inventory_status, version,
             purchase_project, pack_quantity, arrival_unit, usage_kind, book_quantity, pack_progress,
             purchase_applicant, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            material_id,
            batch_no,
            quantity,
            unit_price,
            warehouse_type,
            shelf_id,
            int(location.get("layer_number") or 1),
            str(location.get("zone_name") or "A").upper(),
            form_no,
            location.get("received_date") or today_text(),
            stock_source,
            INVENTORY_STATUS_AVAILABLE,
            purchase_project,
            pack_quantity,
            arrival_unit,
            usage_kind,
            book_quantity,
            purchase_applicant,
            now_text(),
            now_text(),
        ),
    )
    batch_id = cursor.lastrowid
    if stock_source == STOCK_SOURCE_FORMAL and location.get("shelf_id"):
        cursor.execute(
            """
            INSERT INTO material_positions (material_id, shelf_id, layer_number, zone_name, slot_index)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(material_id) DO UPDATE SET
                shelf_id = excluded.shelf_id,
                layer_number = excluded.layer_number,
                zone_name = excluded.zone_name
            """,
            (
                material_id,
                int(location.get("shelf_id")),
                int(location.get("layer_number") or 1),
                str(location.get("zone_name") or "A").upper(),
                int(location.get("slot_index") or 0),
            ),
        )
    balance, _ = update_inventory_total(cursor, material_id, stock_source=stock_source)
    cursor.execute(
        """
        INSERT INTO stock_records
            (material_id, operation_type, quantity, balance_after, operation_date, remark,
             batch_id, form_no, unit_price, amount, stock_source, business_type, operation_key,
             operator_id, created_at)
        VALUES (?, 'in', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            quantity,
            balance,
            location.get("received_date") or today_text(),
            location.get("remark") or "流程入库",
            batch_id,
            form_no,
            unit_price,
            quantity * unit_price,
            stock_source,
            business_type,
            operation_key,
            operator_id,
            now_text(),
        ),
    )
    from warehouse_suit.assistant.notes import evaluate_material_notes
    evaluate_material_notes(cursor, material_id)
    return batch_id


def consume_inventory_fifo(
    cursor,
    material_id,
    quantity,
    form_no,
    operation_date=None,
    remark="流程出库",
    allocations=None,
    stock_source=STOCK_SOURCE_FORMAL,
    business_type=BUSINESS_TYPE_MANUAL,
    operation_key=None,
    operator_id=None,
    workflow_item_id=None,
    purchase_project=None,
    usage_kind=None,
):
    stock_source = validate_stock_source(stock_source)
    requested = quantity_value(quantity, "出库数量", positive=True)
    business_type = str(business_type or BUSINESS_TYPE_MANUAL).strip()
    operation_key = str(operation_key or "").strip() or None
    begin_inventory_transaction(cursor.connection)

    existing = _existing_operation_rows(cursor, operation_key)
    if existing:
        _validate_existing_operation(existing, material_id, stock_source, "out", requested)
        return [
            {
                "batch_id": row.get("batch_id"),
                "batch_no": "",
                "quantity": float(row.get("quantity") or 0),
                "unit_price": float(row.get("unit_price") or 0),
                "stock_record_id": row.get("id"),
            }
            for row in existing
        ]

    all_batches = material_batch_rows(
        cursor, material_id, stock_source=stock_source, usage_kind=usage_kind
    )
    if purchase_project is not None:
        project_text = str(purchase_project or "").strip()
        batches = material_batch_rows(
            cursor,
            material_id,
            stock_source=stock_source,
            purchase_project=project_text,
            usage_kind=usage_kind,
        )
    else:
        batches = all_batches
    by_id = {int(batch["id"]): batch for batch in batches}
    by_id_all = {int(batch["id"]): batch for batch in all_batches}
    if sum(float(batch["quantity"]) for batch in batches) + 1e-9 < requested:
        raise ValueError("库存数量不足，无法出库")

    consume_plan = []
    if allocations:
        total_allocated = 0
        for allocation in parse_batch_allocations(allocations):
            raw_batch = by_id_all.get(allocation["batch_id"])
            if (
                purchase_project is not None
                and raw_batch
                and str(raw_batch.get("purchase_project") or "").strip() != str(purchase_project or "").strip()
            ):
                raise ValueError("出库批次采购申请项目与单据不一致")
            if (
                usage_kind is not None
                and raw_batch
                and str(raw_batch.get("usage_kind") or "").strip() != str(usage_kind or "").strip()
            ):
                raise ValueError("出库批次用料形式与单据不一致")
            batch = by_id.get(allocation["batch_id"])
            if not batch:
                raise ValueError("选择的出库批次不存在、不可用或库存为 0")
            take = float(allocation["quantity"])
            if take > float(batch["quantity"]) + 1e-9:
                raise ValueError(f"批次 {batch['batch_no']} 库存不足")
            total_allocated += take
            consume_plan.append((batch, take))
        if abs(total_allocated - requested) > 1e-6:
            raise ValueError("出库批次数量合计必须等于出库数量")
    else:
        remain = requested
        for batch in batches:
            if remain <= 1e-9:
                break
            take = min(remain, float(batch["quantity"]))
            consume_plan.append((batch, take))
            remain -= take

    consumed = []
    for batch, take in consume_plan:
        pack = float(batch.get("pack_quantity") or 1)
        if pack < 1:
            pack = 1
        progress = float(batch.get("pack_progress") or 0) + float(take)
        whole_units = int(progress // pack)
        progress -= whole_units * pack
        cursor.execute(
            """
            UPDATE material_batches
            SET quantity = quantity - ?,
                book_quantity = MAX(0, COALESCE(book_quantity, 0) - ?),
                pack_progress = ?,
                version = version + 1,
                updated_at = ?
            WHERE id = ?
              AND stock_source = ?
              AND inventory_status = ?
              AND quantity >= ?
            """,
            (
                take,
                whole_units,
                progress,
                now_text(),
                batch["id"],
                stock_source,
                INVENTORY_STATUS_AVAILABLE,
                take,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"批次 {batch['batch_no']} 库存已变化，请刷新后重试")

        balance, _ = update_inventory_total(cursor, material_id, stock_source=stock_source)
        record_key = f"{operation_key}:{batch['id']}" if operation_key else None
        cursor.execute(
            """
            INSERT INTO stock_records
                (material_id, operation_type, quantity, balance_after, operation_date, remark,
                 batch_id, form_no, unit_price, amount, stock_source, business_type, operation_key,
                 operator_id, workflow_item_id, created_at)
            VALUES (?, 'out', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                material_id,
                take,
                balance,
                operation_date or today_text(),
                remark,
                batch["id"],
                form_no,
                float(batch.get("unit_price") or 0),
                take * float(batch.get("unit_price") or 0),
                stock_source,
                business_type,
                record_key,
                operator_id,
                workflow_item_id,
                now_text(),
            ),
        )
        stock_record_id = cursor.lastrowid
        consumed.append(
            {
                "batch_id": batch["id"],
                "batch_no": batch["batch_no"],
                "quantity": take,
                "unit_price": float(batch.get("unit_price") or 0),
                "stock_record_id": stock_record_id,
                "purchase_project": str(batch.get("purchase_project") or "").strip(),
                "usage_kind": str(batch.get("usage_kind") or "").strip(),
            }
        )
    return consumed


def adjust_inventory_batch(
    cursor,
    batch_id,
    adjustment_quantity,
    reason,
    stock_source=STOCK_SOURCE_FORMAL,
    business_type=BUSINESS_TYPE_MANUAL,
    operation_key=None,
    operator_id=None,
):
    """Adjust one batch atomically; the caller owns commit and rollback."""
    stock_source = validate_stock_source(stock_source)
    adjustment = validated_number(adjustment_quantity, "调整数量", required=True)
    if abs(adjustment) <= 1e-12:
        raise ValueError("调整数量不能为 0")
    operation_key = str(operation_key or "").strip() or None
    business_type = str(business_type or BUSINESS_TYPE_MANUAL).strip()
    begin_inventory_transaction(cursor.connection)

    existing = _existing_operation_rows(cursor, operation_key)
    operation_type = "in" if adjustment > 0 else "out"
    if existing:
        _validate_existing_operation(existing, existing[0]["material_id"], stock_source, operation_type, abs(adjustment))
        if int(existing[0].get("batch_id") or 0) != int(batch_id):
            raise ValueError("库存操作幂等键已被其他批次使用")
        cursor.execute("SELECT quantity, version FROM material_batches WHERE id = ?", (int(batch_id),))
        row = cursor.fetchone()
        return {
            "batch_id": int(batch_id),
            "quantity": float(row["quantity"] if row else existing[0].get("balance_after") or 0),
            "version": int(row["version"] if row else 0),
        }

    cursor.execute(
        """
        SELECT *
        FROM material_batches
        WHERE id = ? AND stock_source = ? AND inventory_status = ?
        """,
        (int(batch_id), stock_source, INVENTORY_STATUS_AVAILABLE),
    )
    batch = row_to_dict(cursor.fetchone())
    if not batch:
        raise ValueError("批次不存在或库存来源不匹配")

    cursor.execute(
        """
        UPDATE material_batches
        SET quantity = quantity + ?,
            version = version + 1,
            updated_at = ?
        WHERE id = ?
          AND stock_source = ?
          AND inventory_status = ?
          AND quantity + ? >= 0
        """,
        (
            adjustment,
            now_text(),
            int(batch_id),
            stock_source,
            INVENTORY_STATUS_AVAILABLE,
            adjustment,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("库存不足或批次数量已变化，请刷新后重试")

    cursor.execute("SELECT quantity, version FROM material_batches WHERE id = ?", (int(batch_id),))
    updated = cursor.fetchone()
    balance, _ = update_inventory_total(cursor, int(batch["material_id"]), stock_source=stock_source)
    unit_price = float(batch.get("unit_price") or 0)
    cursor.execute(
        """
        INSERT INTO stock_records
            (material_id, operation_type, quantity, balance_after, operation_date, remark,
             batch_id, form_no, unit_price, amount, stock_source, business_type, operation_key,
             operator_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(batch["material_id"]),
            operation_type,
            abs(adjustment),
            balance,
            today_text(),
            str(reason or ""),
            int(batch_id),
            unit_price,
            abs(adjustment) * unit_price,
            stock_source,
            business_type,
            operation_key,
            operator_id,
            now_text(),
        ),
    )
    if adjustment > 0:
        from warehouse_suit.assistant.notes import evaluate_material_notes
        evaluate_material_notes(cursor, int(batch["material_id"]))
    return {
        "batch_id": int(batch_id),
        "quantity": float(updated["quantity"]),
        "version": int(updated["version"]),
    }


def production_available_quantity(item, kind):
    """可借用数量。半成品还要扣掉已领用未消耗。"""
    quantity = float(item.get("quantity") or 0)
    borrowed = float(item.get("borrowed_quantity") or 0)
    if kind == "semifinished":
        used = float(item.get("used_quantity") or 0)
        claimed = float(item.get("claimed_quantity") or 0)
        quantity -= max(used, claimed)
    return max(0, quantity - borrowed)


def borrowable_items(cursor, keyword="", include_temporary=False):
    keyword = str(keyword or "").strip()
    like = f"%{keyword}%"
    rows = []
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
            GROUP BY b.material_id, TRIM(COALESCE(b.purchase_project, ''))
        ),
        borrow_materials AS (
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
                    AS temporary_available_quantity
            FROM materials m
            JOIN source_stock ss ON ss.material_id = m.id
        )
        SELECT
            bm.*,
            bm.formal_available_quantity + bm.temporary_available_quantity
                AS total_available_quantity
        FROM borrow_materials bm
        WHERE bm.formal_available_quantity + bm.temporary_available_quantity > 0
          AND (
              ? = ''
              OR bm.material_code LIKE ?
              OR bm.name LIKE ?
              OR COALESCE(bm.brand_model, '') LIKE ?
              OR COALESCE(bm.spec, '') LIKE ?
              OR COALESCE(bm.purchase_applicant, '') LIKE ?
              OR COALESCE(bm.purchase_project, '') LIKE ?
          )
        ORDER BY
            CASE
                WHEN bm.formal_available_quantity + bm.temporary_available_quantity > 0
                THEN 0 ELSE 1
            END,
            bm.id DESC
        """,
        (
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_TEMPORARY,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_FORMAL,
            int(bool(include_temporary)),
            keyword,
            like,
            like,
            like,
            like,
            like,
            like,
        ),
    )
    fetched = [dict(row) for row in cursor.fetchall()]
    holds = pending_outbound_hold_index(cursor)
    for item in fetched:
        formal_quantity = float(item.get("formal_available_quantity") or 0)
        temporary_quantity = float(item.get("temporary_available_quantity") or 0)
        held_formal, held_temporary = held_source_quantities(
            holds, item["id"], item.get("purchase_project")
        )
        formal_quantity = max(0.0, formal_quantity - held_formal)
        temporary_quantity = max(0.0, temporary_quantity - held_temporary)
        total_quantity = formal_quantity + temporary_quantity
        if total_quantity <= 1e-9:
            continue
        rows.append(
            {
                "item_type": "material",
                "item_ref_id": item["id"],
                "material_id": item["id"],
                "item_code": item.get("material_code") or "",
                "item_name": item.get("name") or "",
                "brand_model": item.get("brand_model") or "",
                "spec": item.get("spec") or "",
                "unit": item.get("unit") or "",
                "purchase_project": item.get("purchase_project") or "",
                "available_quantity": total_quantity,
                "formal_available_quantity": formal_quantity,
                "temporary_available_quantity": temporary_quantity,
                "total_available_quantity": total_quantity,
                "location_text": "",
                "batch_summary": "",
            }
        )
    semi_where = ""
    semi_params = []
    if keyword:
        semi_where = "WHERE si.name LIKE ? OR si.spec LIKE ? OR si.serial_no LIKE ?"
        semi_params = [like, like, like]
    cursor.execute(
        f"""
        SELECT si.*, s.name AS shelf_name, TRIM(COALESCE(sa.project_code, '')) AS acceptance_project_code
        FROM semifinished_inventory si
        LEFT JOIN shelves s ON s.id = si.shelf_id
        LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
        {semi_where}
        ORDER BY si.id DESC
        """,
        semi_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = production_available_quantity(item, "semifinished")
        if available <= 0:
            continue
        rows.append(
            {
                "item_type": "semifinished",
                "item_ref_id": item["id"],
                "material_id": None,
                "item_code": item.get("serial_no") or f"BP{item.get('id')}",
                "item_name": item.get("name") or "",
                "brand_model": "",
                "spec": item.get("spec") or "",
                "unit": item.get("unit") or "",
                "purchase_project": purchase_project_from_acceptance_code(item.get("acceptance_project_code")),
                "available_quantity": available,
                "location_text": " ".join(str(value or "") for value in [item.get("shelf_name"), item.get("layer_number"), item.get("zone_name")]).strip(),
                "batch_summary": "",
            }
        )
    fin_where = ""
    fin_params = []
    if keyword:
        fin_where = "WHERE fgi.product_name LIKE ? OR fgi.spec LIKE ? OR fgi.serial_no LIKE ?"
        fin_params = [like, like, like]
    cursor.execute(
        f"""
        SELECT fgi.*, s.name AS shelf_name, TRIM(COALESCE(fa.project_code, '')) AS acceptance_project_code
        FROM finished_good_inventory fgi
        LEFT JOIN shelves s ON s.id = fgi.shelf_id
        LEFT JOIN finished_acceptances fa ON fa.id = fgi.acceptance_id
        {fin_where}
        ORDER BY fgi.id DESC
        """,
        fin_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = production_available_quantity(item, "finished")
        if available <= 0:
            continue
        rows.append(
            {
                "item_type": "finished",
                "item_ref_id": item["id"],
                "material_id": None,
                "item_code": item.get("serial_no") or f"CP{item.get('id')}",
                "item_name": item.get("product_name") or "",
                "brand_model": "",
                "spec": item.get("spec") or "",
                "unit": item.get("unit") or "",
                "purchase_project": purchase_project_from_acceptance_code(item.get("acceptance_project_code")),
                "available_quantity": available,
                "location_text": " ".join(str(value or "") for value in [item.get("shelf_name"), item.get("layer_number"), item.get("zone_name")]).strip(),
                "batch_summary": "",
            }
        )
    # defective semifinished
    def_semi_where = ""
    def_semi_params = []
    if keyword:
        def_semi_where = "WHERE d.name LIKE ? OR d.spec LIKE ? OR d.serial_no LIKE ?"
        def_semi_params = [like, like, like]
    cursor.execute(
        f"""
        SELECT d.*, 1 AS available_quantity, TRIM(COALESCE(sa.project_code, '')) AS acceptance_project_code
        FROM defective_semifinished_goods d
        LEFT JOIN semifinished_acceptances sa ON sa.id = d.semifinished_acceptance_id
        {def_semi_where}
        ORDER BY d.id DESC
        """,
        def_semi_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = max(0, 1 - float(item.get("borrowed_quantity") or 0))
        if available <= 0:
            continue
        rows.append(
            {
                "item_type": "defective_semifinished",
                "item_ref_id": item["id"],
                "material_id": None,
                "item_code": item.get("serial_no") or f"BP{item.get('id')}",
                "item_name": item.get("name") or "",
                "brand_model": "",
                "spec": item.get("spec") or "",
                "unit": "\u4e2a",
                "purchase_project": purchase_project_from_acceptance_code(item.get("acceptance_project_code")),
                "available_quantity": available,
                "location_text": "",
                "batch_summary": "",
            }
        )
    # defective finished
    def_fin_where = ""
    def_fin_params = []
    if keyword:
        def_fin_where = "WHERE d.product_name LIKE ? OR d.spec LIKE ? OR d.serial_no LIKE ?"
        def_fin_params = [like, like, like]
    cursor.execute(
        f"""
        SELECT d.*, 1 AS available_quantity, TRIM(COALESCE(fa.project_code, '')) AS acceptance_project_code
        FROM defective_finished_goods d
        LEFT JOIN finished_acceptances fa ON fa.id = d.finished_acceptance_id
        {def_fin_where}
        ORDER BY d.id DESC
        """,
        def_fin_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = max(0, 1 - float(item.get("borrowed_quantity") or 0))
        if available <= 0:
            continue
        rows.append(
            {
                "item_type": "defective_finished",
                "item_ref_id": item["id"],
                "material_id": None,
                "item_code": item.get("serial_no") or f"CP{item.get('id')}",
                "item_name": item.get("product_name") or "",
                "brand_model": "",
                "spec": item.get("spec") or "",
                "unit": "\u53f0",
                "purchase_project": purchase_project_from_acceptance_code(item.get("acceptance_project_code")),
                "available_quantity": available,
                "location_text": "",
                "batch_summary": "",
            }
        )
    # scrapped semifinished
    scr_semi_where = ""
    scr_semi_params = []
    if keyword:
        scr_semi_where = "WHERE s.name LIKE ? OR s.spec LIKE ? OR s.serial_no LIKE ?"
        scr_semi_params = [like, like, like]
    cursor.execute(
        f"""
        SELECT s.*, COALESCE(s.quantity, 0) AS available_quantity
        FROM scrapped_semifinished_goods s
        {scr_semi_where}
        ORDER BY s.id DESC
        """,
        scr_semi_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = float(item.get("quantity") or 0)
        if available <= 0:
            continue
        rows.append(
            {
                "item_type": "scrapped_semifinished",
                "item_ref_id": item["id"],
                "material_id": None,
                "item_code": item.get("serial_no") or f"BS{item.get('id')}",
                "item_name": item.get("name") or "",
                "brand_model": "",
                "spec": item.get("spec") or "",
                "unit": item.get("unit") or "\u4e2a",
                "available_quantity": available,
                "location_text": "",
                "batch_summary": "",
            }
        )
    # scrapped finished
    scr_fin_where = ""
    scr_fin_params = []
    if keyword:
        scr_fin_where = "WHERE s.product_name LIKE ? OR s.spec LIKE ? OR s.serial_no LIKE ?"
        scr_fin_params = [like, like, like]
    cursor.execute(
        f"""
        SELECT s.*, COALESCE(s.quantity, 0) AS available_quantity
        FROM scrapped_finished_goods s
        {scr_fin_where}
        ORDER BY s.id DESC
        """,
        scr_fin_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = float(item.get("quantity") or 0)
        if available <= 0:
            continue
        rows.append(
            {
                "item_type": "scrapped_finished",
                "item_ref_id": item["id"],
                "material_id": None,
                "item_code": item.get("serial_no") or f"BF{item.get('id')}",
                "item_name": item.get("product_name") or "",
                "brand_model": "",
                "spec": item.get("spec") or "",
                "unit": item.get("unit") or "\u53f0",
                "available_quantity": available,
                "location_text": "",
                "batch_summary": "",
            }
        )
    for item in rows:
        available = float(item.get("available_quantity") or 0)
        item.setdefault("formal_available_quantity", available)
        item.setdefault("temporary_available_quantity", 0.0)
        item.setdefault("total_available_quantity", available)
        item.setdefault("stock_source", STOCK_SOURCE_FORMAL)
        item.setdefault("purchase_project", purchase_project_from_acceptance_code(""))
    return rows


def borrowable_item_snapshot(cursor, item_type, item_ref_id, stock_source=STOCK_SOURCE_FORMAL, purchase_project=None, usage_kind=None):
    stock_source = validate_stock_source(stock_source)
    item_type = str(item_type or "material").strip()
    item_ref_id = int(item_ref_id or 0)
    if item_type == "material":
        snap = material_snapshot(cursor, item_ref_id, stock_source=stock_source)
        if not snap:
            raise ValueError("borrow material not found")
        project = None if purchase_project is None else str(purchase_project or "").strip()
        snap.update(
            {
                "item_type": "material",
                "item_ref_id": item_ref_id,
                "item_code": snap.get("material_code") or "",
                "item_name": snap.get("name") or "",
                "available_quantity": material_stock_total(
                    cursor,
                    item_ref_id,
                    stock_source=stock_source,
                    purchase_project=project,
                    usage_kind=usage_kind,
                ),
                "stock_source": stock_source,
                "purchase_project": project or "",
            }
        )
        return snap
    if item_type == "semifinished":
        cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (item_ref_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("semifinished item not found")
        return {
            "item_type": "semifinished",
            "item_ref_id": item_ref_id,
            "material_id": None,
            "item_code": item.get("serial_no") or f"BP{item_ref_id}",
            "item_name": item.get("name") or "",
            "brand_model": "",
            "spec": item.get("spec") or "",
            "unit": item.get("unit") or "",
            "available_quantity": production_available_quantity(item, "semifinished"),
            "cost_price": float(item.get("cost_price") or 0),
            "purchase_project": _nonmaterial_purchase_project(cursor, item_type, item, purchase_project),
        }
    if item_type == "finished":
        cursor.execute("SELECT * FROM finished_good_inventory WHERE id = ?", (item_ref_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("finished item not found")
        return {
            "item_type": "finished",
            "item_ref_id": item_ref_id,
            "material_id": None,
            "item_code": item.get("serial_no") or f"CP{item_ref_id}",
            "item_name": item.get("product_name") or "",
            "brand_model": "",
            "spec": item.get("spec") or "",
            "unit": item.get("unit") or "",
            "available_quantity": production_available_quantity(item, "finished"),
            "cost_price": float(item.get("cost_price") or 0),
            "purchase_project": _nonmaterial_purchase_project(cursor, item_type, item, purchase_project),
        }
    if item_type == "defective_semifinished":
        cursor.execute("SELECT * FROM defective_semifinished_goods WHERE id = ?", (item_ref_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("defective semifinished item not found")
        return {
            "item_type": "defective_semifinished",
            "item_ref_id": item_ref_id,
            "material_id": None,
            "item_code": item.get("serial_no") or f"BP{item_ref_id}",
            "item_name": item.get("name") or "",
            "brand_model": "",
            "spec": item.get("spec") or "",
            "unit": "\u4e2a",
            "available_quantity": max(0, 1 - float(item.get("borrowed_quantity") or 0)),
            "cost_price": 0,
            "purchase_project": _nonmaterial_purchase_project(cursor, item_type, item, purchase_project),
        }
    if item_type == "defective_finished":
        cursor.execute("SELECT * FROM defective_finished_goods WHERE id = ?", (item_ref_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("defective finished item not found")
        return {
            "item_type": "defective_finished",
            "item_ref_id": item_ref_id,
            "material_id": None,
            "item_code": item.get("serial_no") or f"CP{item_ref_id}",
            "item_name": item.get("product_name") or "",
            "brand_model": "",
            "spec": item.get("spec") or "",
            "unit": "\u53f0",
            "available_quantity": max(0, 1 - float(item.get("borrowed_quantity") or 0)),
            "cost_price": 0,
            "purchase_project": _nonmaterial_purchase_project(cursor, item_type, item, purchase_project),
        }
    if item_type == "scrapped_semifinished":
        cursor.execute("SELECT * FROM scrapped_semifinished_goods WHERE id = ?", (item_ref_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("scrapped semifinished item not found")
        return {
            "item_type": "scrapped_semifinished",
            "item_ref_id": item_ref_id,
            "material_id": None,
            "item_code": item.get("serial_no") or f"BS{item_ref_id}",
            "item_name": item.get("name") or "",
            "brand_model": "",
            "spec": item.get("spec") or "",
            "unit": item.get("unit") or "\u4e2a",
            "available_quantity": float(item.get("quantity") or 0),
            "cost_price": 0,
            "purchase_project": _nonmaterial_purchase_project(cursor, item_type, item, purchase_project),
        }
    if item_type == "scrapped_finished":
        cursor.execute("SELECT * FROM scrapped_finished_goods WHERE id = ?", (item_ref_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("scrapped finished item not found")
        return {
            "item_type": "scrapped_finished",
            "item_ref_id": item_ref_id,
            "material_id": None,
            "item_code": item.get("serial_no") or f"BF{item_ref_id}",
            "item_name": item.get("product_name") or "",
            "brand_model": "",
            "spec": item.get("spec") or "",
            "unit": item.get("unit") or "\u53f0",
            "available_quantity": float(item.get("quantity") or 0),
            "cost_price": 0,
            "purchase_project": _nonmaterial_purchase_project(cursor, item_type, item, purchase_project),
        }
    raise ValueError("borrow item type invalid")


def _nonmaterial_purchase_project(cursor, item_type, item, purchase_project=None):
    """半成品/成品项目以验收代号为准；无代号视为办公。"""
    table_key = {
        "semifinished": ("semifinished_acceptances", item.get("acceptance_id")),
        "finished": ("finished_acceptances", item.get("acceptance_id")),
        "defective_semifinished": ("semifinished_acceptances", item.get("semifinished_acceptance_id")),
        "defective_finished": ("finished_acceptances", item.get("finished_acceptance_id")),
    }.get(item_type)
    derived = purchase_project_from_acceptance_code("")
    if table_key and table_key[1]:
        cursor.execute(f"SELECT project_code FROM {table_key[0]} WHERE id = ?", (table_key[1],))
        row = cursor.fetchone()
        derived = purchase_project_from_acceptance_code(row["project_code"] if row else "")
    requested = str(purchase_project or "").strip()
    if requested and requested != derived:
        raise ValueError("采购申请项目与该半成品/成品不一致")
    return derived


def adjust_nonmaterial_borrow(cursor, item_type, item_ref_id, quantity, outbound):
    """在对应生产库存、不良品或报废品表中维护借用数量。"""
    borrowed_tables = {
        "semifinished": (
            "semifinished_inventory",
            "quantity - borrowed_quantity - MAX(used_quantity, COALESCE(claimed_quantity, 0))",
        ),
        "finished": ("finished_good_inventory", "quantity - borrowed_quantity"),
        "defective_semifinished": ("defective_semifinished_goods", "1 - borrowed_quantity"),
        "defective_finished": ("defective_finished_goods", "1 - borrowed_quantity"),
    }
    scrapped_tables = {
        "scrapped_semifinished": "scrapped_semifinished_goods",
        "scrapped_finished": "scrapped_finished_goods",
    }
    if item_type in borrowed_tables:
        table, available = borrowed_tables[item_type]
        if outbound:
            cursor.execute(
                f"""UPDATE {table}
                    SET borrowed_quantity = borrowed_quantity + ?, updated_at = ?
                    WHERE id = ? AND {available} >= ?""",
                (quantity, now_text(), item_ref_id, quantity),
            )
        else:
            cursor.execute(
                f"""UPDATE {table}
                    SET borrowed_quantity = borrowed_quantity - ?, updated_at = ?
                    WHERE id = ? AND borrowed_quantity >= ?""",
                (quantity, now_text(), item_ref_id, quantity),
            )
    elif item_type in scrapped_tables:
        table = scrapped_tables[item_type]
        if outbound:
            claimed_guard = ""
            if item_type == "scrapped_semifinished":
                claimed_guard = " - COALESCE(claimed_quantity, 0)"
            cursor.execute(
                f"UPDATE {table} SET quantity = quantity - ? WHERE id = ? AND quantity{claimed_guard} >= ?",
                (quantity, item_ref_id, quantity),
            )
        else:
            cursor.execute(
                f"UPDATE {table} SET quantity = quantity + ? WHERE id = ?",
                (quantity, item_ref_id),
            )
    else:
        raise ValueError("borrow item type invalid")
    if cursor.rowcount != 1:
        raise ValueError("借用物品余量已变化，请刷新后重试")


def borrow_out_item(
    cursor,
    item,
    quantity,
    form_no,
    operation_date=None,
    allocations=None,
    stock_source=STOCK_SOURCE_FORMAL,
    operation_key=None,
    business_type=BUSINESS_TYPE_BORROW_OUTBOUND,
    operator_id=None,
    workflow_item_id=None,
    purchase_project=None,
    usage_kind=None,
):
    stock_source = validate_stock_source(stock_source)
    if quantity <= 0:
        raise ValueError("borrow quantity must be greater than 0")
    if quantity > float(item.get("available_quantity") or 0) + 1e-9:
        raise ValueError("borrow quantity exceeds available quantity")
    if item["item_type"] == "material":
        consumed = consume_inventory_fifo(
            cursor,
            item["item_ref_id"],
            quantity,
            form_no,
            operation_date or today_text(),
            f"借用单 {form_no} 出库",
            allocations,
            stock_source=stock_source,
            business_type=business_type,
            operation_key=operation_key,
            operator_id=operator_id,
            workflow_item_id=workflow_item_id,
            purchase_project=purchase_project,
            usage_kind=usage_kind,
        )
        amount = sum(
            float(batch.get("quantity") or 0) * float(batch.get("unit_price") or 0)
            for batch in consumed
        )
        return {
            "consumed_batches": consumed,
            "unit_price": amount / quantity if quantity > 0 else 0,
        }
    adjust_nonmaterial_borrow(
        cursor, item["item_type"], item["item_ref_id"], quantity, outbound=True
    )
    return {"consumed_batches": [], "unit_price": float(item.get("cost_price") or 0)}


def update_borrow_return_balance(
    cursor,
    borrow_record_id,
    quantity,
    return_date=None,
    return_form_id=None,
):
    quantity = quantity_value(quantity, "归还数量", positive=True)
    cursor.execute(
        """
        UPDATE borrow_records
        SET returned_quantity = COALESCE(returned_quantity, 0) + ?,
            status = CASE
                WHEN COALESCE(returned_quantity, 0) + ?
                     >= COALESCE(quantity, 0) - COALESCE(claimed_quantity, 0) - 0.000000001
                THEN CASE
                    WHEN COALESCE(claimed_quantity, 0) > 0.000000001 THEN 'claimed'
                    ELSE 'returned'
                END
                ELSE 'borrowed'
            END,
            return_date = ?,
            return_form_id = ?,
            updated_at = ?
        WHERE id = ?
          AND status IN ('borrowed', 'transferring')
          AND COALESCE(quantity, 0) - COALESCE(returned_quantity, 0)
              - COALESCE(claimed_quantity, 0) >= ? - 0.000000001
        """,
        (
            quantity,
            quantity,
            return_date or today_text(),
            return_form_id,
            now_text(),
            int(borrow_record_id),
            quantity,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("归还数量超过未归还数量，或借用记录状态已变化")
    cursor.execute(
        "SELECT returned_quantity, quantity, status FROM borrow_records WHERE id = ?",
        (int(borrow_record_id),),
    )
    return row_to_dict(cursor.fetchone())


def return_borrow_item(
    cursor,
    record,
    quantity,
    location,
    form_no,
    operation_key=None,
    business_type=BUSINESS_TYPE_BORROW_RETURN_INBOUND,
    operator_id=None,
):
    if quantity <= 0:
        raise ValueError("return quantity must be greater than 0")
    remaining = (
        float(record["quantity"] or 0)
        - float(record.get("returned_quantity") or 0)
        - float(record.get("claimed_quantity") or 0)
    )
    if quantity > remaining + 1e-9:
        raise ValueError("return quantity exceeds borrowed balance")
    if record["item_type"] == "material":
        restore_borrow_quantity_to_batches(
            cursor,
            record,
            quantity,
            form_no,
            operation_key=operation_key,
            operator_id=operator_id,
            operation_date=(location or {}).get("received_date"),
            business_type=business_type,
        )
    else:
        adjust_nonmaterial_borrow(
            cursor, record["item_type"], record["item_ref_id"], quantity, outbound=False
        )
    return update_borrow_return_balance(
        cursor,
        record["id"],
        quantity,
        return_date=(location or {}).get("received_date") or today_text(),
        return_form_id=(location or {}).get("return_form_id"),
    )


def restore_borrow_quantity_to_batches(
    cursor,
    record,
    quantity,
    form_no,
    operation_key=None,
    operator_id=None,
    operation_date=None,
    business_type=BUSINESS_TYPE_BORROW_RETURN_INBOUND,
):
    """完好归还加回出库时的原批次，不新开批次。"""
    data = parse_json(record.get("data_json"), {})
    batches = [batch for batch in (data.get("consumed_batches") or []) if isinstance(batch, dict)]
    if not batches:
        raise ValueError("借用出库批次缺失，无法归还入库")
    returned = dict(data.get("returned_batch_quantities") or {})
    left = float(quantity)
    slices = []
    for batch in batches:
        if left <= 1e-9:
            break
        batch_id = int(batch.get("batch_id") or batch.get("id") or 0)
        if not batch_id:
            raise ValueError("借用出库批次缺失，无法归还入库")
        key = str(batch_id)
        already = float(returned.get(key) or 0)
        capacity = float(batch.get("quantity") or 0) - already
        if capacity <= 1e-9:
            continue
        take = min(capacity, left)
        cursor.execute("SELECT * FROM material_batches WHERE id = ?", (batch_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("原批次不存在，无法归还入库")
        row = dict(row)
        raw_price = batch.get("unit_price")
        unit_price = float(raw_price if raw_price not in (None, "") else (row.get("unit_price") or 0))
        slices.append((batch_id, key, already, take, row, unit_price))
        left -= take
    if left > 1e-9:
        raise ValueError("原出库批次不足以归还")
    base_key = operation_key or form_no
    for batch_id, key, already, take, row, unit_price in slices:
        cursor.execute(
            """
            UPDATE material_batches
            SET quantity = quantity + ?, version = version + 1, updated_at = ?
            WHERE id = ?
            """,
            (take, now_text(), batch_id),
        )
        balance, _ = update_inventory_total(cursor, int(row["material_id"]), stock_source=row["stock_source"])
        slice_key = base_key if len(slices) == 1 else f"{base_key}:{batch_id}"
        cursor.execute(
            """
            INSERT INTO stock_records
                (material_id, operation_type, quantity, balance_after, operation_date, remark,
                 batch_id, form_no, unit_price, amount, stock_source, business_type, operation_key,
                 operator_id, created_at)
            VALUES (?, 'in', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["material_id"]),
                take,
                balance,
                operation_date or today_text(),
                f"借用单 {form_no} 归还入库",
                batch_id,
                form_no,
                unit_price,
                take * unit_price,
                row["stock_source"],
                business_type,
                slice_key,
                operator_id,
                now_text(),
            ),
        )
        returned[key] = already + take
    data["returned_batch_quantities"] = returned
    cursor.execute(
        "UPDATE borrow_records SET data_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), now_text(), int(record["id"])),
    )
    from warehouse_suit.assistant.notes import evaluate_material_notes
    evaluate_material_notes(cursor, int(record["material_id"]))


def save_borrow_change(cursor, borrow_record_id, change_type, change_detail, version_after, normal_use):
    """Insert a borrow change record. Returns the new row id."""
    cursor.execute(
        """
        INSERT INTO borrow_change_records (borrow_record_id, change_type, change_detail, version_after, normal_use, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(borrow_record_id) if borrow_record_id else 0,
            str(change_type or ""),
            str(change_detail or ""),
            str(version_after or ""),
            str(normal_use or ""),
            now_text(),
        ),
    )
    return cursor.lastrowid


def move_borrow_return_to_unqualified(cursor, record, inventory_row, quantity, reason):
    """借用归还异常写入不合格半成品库或不合格成品库。"""
    item_type = str(record.get("item_type") or "")
    acceptance_id = inventory_row.get("acceptance_id")
    if not acceptance_id:
        raise ValueError("缺少验收单，不能写入不合格库")
    serial_no = str(record.get("item_code") or inventory_row.get("serial_no") or "").strip()
    if not serial_no:
        raise ValueError("缺少流水号，不能写入不合格库")
    table = "semifinished_inventory" if item_type == "semifinished" else "finished_good_inventory"
    if item_type not in ("semifinished", "finished"):
        raise ValueError("只有半成品和成品可以写入不合格库")
    cursor.execute(
        f"""
        UPDATE {table}
        SET quantity = quantity - ?, borrowed_quantity = borrowed_quantity - ?, updated_at = ?
        WHERE id = ? AND quantity >= ? AND borrowed_quantity >= ?
        """,
        (quantity, quantity, now_text(), int(record["item_ref_id"]), quantity, quantity),
    )
    if cursor.rowcount != 1:
        raise ValueError("归还物品余量已变化，请刷新后重试")
    abnormal = json.dumps([reason or "借用归还异常"], ensure_ascii=False)
    if item_type == "semifinished":
        cursor.execute(
            """
            INSERT INTO defective_semifinished_goods
                (semifinished_acceptance_id, name, spec, serial_no, abnormal_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                acceptance_id,
                inventory_row.get("name") or record.get("item_name") or "",
                inventory_row.get("spec") or "",
                serial_no,
                abnormal,
                now_text(),
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO defective_finished_goods
                (finished_acceptance_id, product_name, spec, serial_no, abnormal_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                acceptance_id,
                inventory_row.get("product_name") or record.get("item_name") or "",
                inventory_row.get("spec") or "",
                serial_no,
                abnormal,
                now_text(),
            ),
        )
    return cursor.lastrowid


def move_to_scrapped(cursor, item_type, item_data, scrap_reason, scrap_source='borrow_return', adjust_source_quantity=True):
    if item_type not in ("semifinished", "finished"):
        raise ValueError(f"unsupported item_type for scrapping: {item_type}")
    base_serial = item_data.get("serial_no") or ""
    serial_no = base_serial or _next_scrap_serial(cursor, item_type)
    if item_type == "finished":
        product_name = item_data.get("product_name") or item_data.get("item_name") or ""
        cursor.execute(
            """
            INSERT INTO scrapped_finished_goods
                (acceptance_id, product_name, spec, serial_no, unit, quantity,
                 original_inventory_id, scrap_source, scrap_reason, scrap_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_data.get("acceptance_id"),
                product_name,
                item_data.get("spec") or "",
                serial_no,
                item_data.get("unit") or "\u53f0",
                float(item_data.get("quantity") or 1),
                item_data.get("original_inventory_id") or item_data.get("item_ref_id"),
                scrap_source,
                scrap_reason,
                today_text(),
                now_text(),
            ),
        )
        source_id = item_data.get("original_inventory_id") or item_data.get("item_ref_id")
        if adjust_source_quantity and source_id:
            cursor.execute(
                "UPDATE finished_good_inventory SET quantity = MAX(0, COALESCE(quantity, 0) - ?), updated_at = ? WHERE id = ?",
                (float(item_data.get("quantity") or 1), now_text(), source_id),
            )
    else:
        name = item_data.get("name") or item_data.get("item_name") or ""
        cursor.execute(
            """
            INSERT INTO scrapped_semifinished_goods
                (acceptance_id, name, spec, serial_no, unit, quantity,
                 original_inventory_id, scrap_source, scrap_reason, scrap_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_data.get("acceptance_id"),
                name,
                item_data.get("spec") or "",
                serial_no,
                item_data.get("unit") or "\u4e2a",
                float(item_data.get("quantity") or 1),
                item_data.get("original_inventory_id") or item_data.get("item_ref_id"),
                scrap_source,
                scrap_reason,
                today_text(),
                now_text(),
            ),
        )
        source_id = item_data.get("original_inventory_id") or item_data.get("item_ref_id")
        if adjust_source_quantity and source_id:
            cursor.execute(
                "UPDATE semifinished_inventory SET quantity = MAX(0, COALESCE(quantity, 0) - ?), updated_at = ? WHERE id = ?",
                (float(item_data.get("quantity") or 1), now_text(), source_id),
            )
    return cursor.lastrowid


def restore_from_scrapped(cursor, item_type, scrapped_id):
    if item_type == "finished":
        cursor.execute("SELECT * FROM scrapped_finished_goods WHERE id = ?", (scrapped_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("scrapped finished item not found")
        qty = float(item.get("quantity") or 1)
        orig_id = item.get("original_inventory_id")
        if orig_id:
            cursor.execute(
                "UPDATE finished_good_inventory SET quantity = COALESCE(quantity, 0) + ?, updated_at = ? WHERE id = ?",
                (qty, now_text(), orig_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO finished_good_inventory
                    (acceptance_id, product_name, spec, unit, quantity, serial_no, acceptance_date, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("acceptance_id"),
                    item.get("product_name") or "",
                    item.get("spec") or "",
                    item.get("unit") or "\u53f0",
                    qty,
                    item.get("serial_no") or "",
                    item.get("scrap_date") or today_text(),
                    now_text(),
                    now_text(),
                ),
            )
        cursor.execute("DELETE FROM scrapped_finished_goods WHERE id = ?", (scrapped_id,))
    elif item_type == "semifinished":
        cursor.execute("SELECT * FROM scrapped_semifinished_goods WHERE id = ?", (scrapped_id,))
        item = row_to_dict(cursor.fetchone())
        if not item:
            raise ValueError("scrapped semifinished item not found")
        qty = float(item.get("quantity") or 1)
        orig_id = item.get("original_inventory_id")
        if orig_id:
            cursor.execute(
                "UPDATE semifinished_inventory SET quantity = COALESCE(quantity, 0) + ?, updated_at = ? WHERE id = ?",
                (qty, now_text(), orig_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO semifinished_inventory
                    (acceptance_id, name, spec, unit, quantity, serial_no, acceptance_date, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("acceptance_id"),
                    item.get("name") or "",
                    item.get("spec") or "",
                    item.get("unit") or "\u4e2a",
                    qty,
                    item.get("serial_no") or "",
                    item.get("scrap_date") or today_text(),
                    now_text(),
                    now_text(),
                ),
            )
        cursor.execute("DELETE FROM scrapped_semifinished_goods WHERE id = ?", (scrapped_id,))
    else:
        raise ValueError(f"unsupported item_type for restore: {item_type}")
    return scrapped_id


def _next_scrap_serial(cursor, item_type):
    prefix = "BF" if item_type == "finished" else "BS"
    date_part = today_text().replace("-", "")
    cursor.execute(
        "SELECT serial_no FROM scrapped_finished_goods WHERE serial_no LIKE ? "
        "UNION ALL SELECT serial_no FROM scrapped_semifinished_goods WHERE serial_no LIKE ? "
        "ORDER BY serial_no DESC LIMIT 1",
        (f"{prefix}{date_part}%", f"{prefix}{date_part}%"),
    )
    row = cursor.fetchone()
    serial = int(str(row["serial_no"])[-3:]) + 1 if row else 1
    return f"{prefix}{date_part}{serial:03d}"


def get_item_change_history(cursor, item_type, item_ref_id, page=1, limit=20):
    """
    Get paginated change history for a semifinished/finished item.
    Returns {item: {...}, history: [...], total: N, page: N, limit: N}.
    The first history entry (index 0) is always the initial acceptance record.
    """
    # Map item_type to inventory/defective/scrapped table and acceptance_id column
    item_type_to_table = {
        "semifinished": ("semifinished_inventory", "acceptance_id"),
        "finished": ("finished_good_inventory", "acceptance_id"),
        "defective_finished": ("defective_finished_goods", "finished_acceptance_id"),
        "defective_semifinished": ("defective_semifinished_goods", "semifinished_acceptance_id"),
        "scrapped_finished": ("scrapped_finished_goods", "acceptance_id"),
        "scrapped_semifinished": ("scrapped_semifinished_goods", "acceptance_id"),
    }
    item_type_to_acceptance = {
        "semifinished": ("semifinished_acceptances", "name", "acceptance_quantity"),
        "finished": ("finished_acceptances", "product_name", "acceptance_quantity"),
        "defective_finished": ("finished_acceptances", "product_name", "acceptance_quantity"),
        "defective_semifinished": ("semifinished_acceptances", "name", "acceptance_quantity"),
        "scrapped_finished": ("finished_acceptances", "product_name", "acceptance_quantity"),
        "scrapped_semifinished": ("semifinished_acceptances", "name", "acceptance_quantity"),
    }
    item_type_to_name_col = {
        "semifinished": "name",
        "finished": "product_name",
        "defective_finished": "product_name",
        "defective_semifinished": "name",
        "scrapped_finished": "product_name",
        "scrapped_semifinished": "name",
    }

    if item_type not in item_type_to_table:
        raise ValueError(f"Invalid item_type: {item_type}")

    inv_table, acc_id_col = item_type_to_table[item_type]
    acc_table, acc_name_col, acc_qty_col = item_type_to_acceptance[item_type]
    name_col = item_type_to_name_col[item_type]

    cursor.execute(f"SELECT * FROM {inv_table} WHERE id = ?", (item_ref_id,))
    item_row = row_to_dict(cursor.fetchone())
    if not item_row:
        raise ValueError(f"{item_type} item not found")

    item = {
        "name": item_row.get(name_col) or "",
        "spec": item_row.get("spec") or "",
        "type": item_type,
    }
    if item_type == "semifinished":
        from warehouse_suit.production_service import semifinished_unit_components

        item["components"] = semifinished_unit_components(cursor, item_row)

    history = []
    acceptance_id = item_row.get(acc_id_col)

    if acceptance_id:
        cursor.execute(
            f"""
            SELECT sa.acceptance_date, sa.{acc_name_col} AS item_name,
                   sa.{acc_qty_col} AS qty, u.display_name
            FROM {acc_table} sa
            LEFT JOIN users u ON u.id = sa.applicant_id
            WHERE sa.id = ?
            """,
            (acceptance_id,),
        )
        acc_row = row_to_dict(cursor.fetchone())
        if acc_row:
            history.append({
                "date": acc_row.get("acceptance_date") or "",
                "event_type": "入库",
                "operator": acc_row.get("display_name") or "",
                "summary": f'验收数量: {acc_row.get("qty") or 0}',
            })

    # Step 2: Get borrow/return/change records
    offset = (page - 1) * limit

    # Count total borrow records for this item
    cursor.execute(
        """
        SELECT COUNT(*) as total
        FROM borrow_records
        WHERE item_type = ? AND item_ref_id = ?
        """,
        (item_type, item_ref_id),
    )
    total = cursor.fetchone()["total"]

    # Get borrow records (without joining change records to avoid duplication)
    cursor.execute(
        """
        SELECT br.id, br.borrow_no, br.quantity, br.returned_quantity, br.status, br.data_json,
               br.outbound_date, br.return_date, br.created_at, br.updated_at,
               u.display_name AS borrower_name
        FROM borrow_records br
        LEFT JOIN users u ON u.id = br.borrower_id
        WHERE br.item_type = ? AND br.item_ref_id = ?
        ORDER BY br.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (item_type, item_ref_id, limit, offset),
    )

    borrow_records = [row_to_dict(row) for row in cursor.fetchall()]

    for br in borrow_records:
        history.append({
            "date": br.get("created_at") or "",
            "event_type": "借出",
            "operator": br.get("borrower_name") or "",
            "change_type": "",
            "change_detail": "",
            "version_after": "",
            "normal_use": "",
            "borrow_record_id": br.get("id"),
            "borrow_no": br.get("borrow_no"),
            "quantity": br.get("quantity"),
        })

        if br.get("status") == "claimed":
            claim_data = parse_json(br.get("data_json"), {})
            history.append({
                "date": claim_data.get("claimed_at") or br.get("updated_at") or "",
                "event_type": "领用",
                "operator": br.get("borrower_name") or "",
                "change_type": "",
                "change_detail": "",
                "version_after": "",
                "normal_use": "",
                "summary": f'借用转领用数量: {claim_data.get("claimed_quantity") or 0}',
            })

        cursor.execute(
            """
            SELECT change_type, change_detail, version_after, normal_use, created_at
            FROM borrow_change_records
            WHERE borrow_record_id = ?
            ORDER BY created_at ASC
            """,
            (br["id"],),
        )
        for ch_row in cursor.fetchall():
            ch = row_to_dict(ch_row)
            history.append({
                "date": ch.get("created_at") or "",
                "event_type": "变更",
                "operator": br.get("borrower_name") or "",
                "change_type": ch.get("change_type") or "",
                "change_detail": ch.get("change_detail") or "",
                "version_after": ch.get("version_after") or "",
                "normal_use": ch.get("normal_use") or "",
            })

        if br.get("return_date"):
            history.append({
                "date": br.get("return_date") or "",
                "event_type": "归还",
                "operator": br.get("borrower_name") or "",
                "change_type": "",
                "change_detail": "",
                "version_after": "",
                "normal_use": "",
            })

    return {
        "item": item,
        "history": history,
        "total": total,
        "page": page,
        "limit": limit,
    }
