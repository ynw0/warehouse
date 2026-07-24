"""Temporary inventory query and manual-management services."""

from __future__ import annotations

import json
from datetime import datetime
from warehouse_suit.attachments import list_material_attachments

from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_IN,
    BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_OUT,
    BUSINESS_TYPE_TEMPORARY_MANUAL_INBOUND,
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_TEMPORARY,
    validate_inventory_status,
)
from warehouse_suit.inventory_service import (
    add_inventory_batch,
    adjust_inventory_batch,
    begin_inventory_transaction,
)
from warehouse_suit.material_service import update_material_master_by_id, upsert_material_master
from warehouse_suit.recycle import recycle_material
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.validation import positive_int_value, price_value, quantity_value, validated_number


class TemporaryInventoryDisabled(RuntimeError):
    pass


def require_temporary_inventory_enabled(cursor):
    if not temporary_inventory_enabled(cursor):
        raise TemporaryInventoryDisabled("临时库功能已关闭")


def write_audit_log(cursor, user, action, target_type, target_id="", summary="", data=None, ip_address=""):
    cursor.execute(
        """
        INSERT INTO audit_logs
            (user_id, username, action, target_type, target_id, summary, data_json, ip_address, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user.get("id") if user else None,
            user.get("username") if user else "",
            str(action or ""),
            str(target_type or ""),
            str(target_id or ""),
            str(summary or ""),
            json.dumps(data or {}, ensure_ascii=False),
            str(ip_address or ""),
            now_text(),
        ),
    )


def _page_values(page, page_size):
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    return page, page_size


def _validate_warehouse_location(cursor, warehouse_type, shelf_id=0):
    warehouse_type = str(warehouse_type or "office").strip()
    if warehouse_type not in {"office", "rd"}:
        raise ValueError("仓库类型必须为办公库或研发库")
    shelf_id = int(shelf_id or 0)
    if shelf_id:
        cursor.execute("SELECT id, warehouse_type FROM shelves WHERE id = ?", (shelf_id,))
        shelf = cursor.fetchone()
        if not shelf:
            raise ValueError("选择的货架不存在")
        if shelf["warehouse_type"] != warehouse_type:
            raise ValueError("货架与仓库类型不一致")
    return warehouse_type, shelf_id or None


def _date_value(value):
    text = str(value or today_text()).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("入库日期格式必须为 YYYY-MM-DD") from exc
    return text


def temporary_inventory_rows(
    cursor,
    q="",
    page=1,
    page_size=20,
    category="",
    warehouse_type="",
    inventory_status=INVENTORY_STATUS_AVAILABLE,
    include_zero=False,
):
    inventory_status = validate_inventory_status(inventory_status)
    page, page_size = _page_values(page, page_size)
    batch_where = ["b.stock_source = ?", "b.inventory_status = ?"]
    batch_params = [STOCK_SOURCE_TEMPORARY, inventory_status]
    if warehouse_type:
        warehouse_type, _ = _validate_warehouse_location(cursor, warehouse_type)
        batch_where.append("b.warehouse_type = ?")
        batch_params.append(warehouse_type)
    outer_where = []
    outer_params = []
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        outer_where.append(
            "(m.material_code LIKE ? OR m.name LIKE ? OR m.brand_model LIKE ? OR "
            "m.spec LIKE ? OR m.category LIKE ? OR m.sub_category LIKE ?)"
        )
        outer_params.extend([like] * 6)
    category = str(category or "").strip()
    if category:
        outer_where.append("(m.category = ? OR m.sub_category = ? OR m.category_name = ?)")
        outer_params.extend([category, category, category])
    if not include_zero:
        outer_where.append("temporary_quantity > 0")

    cte = f"""
        WITH temporary_stock AS (
            SELECT
                b.material_id,
                COALESCE(SUM(b.quantity), 0) AS temporary_quantity,
                COALESCE(SUM(CASE WHEN b.inventory_status = ? THEN b.quantity ELSE 0 END), 0) AS available_quantity,
                COUNT(*) AS batch_count,
                GROUP_CONCAT(DISTINCT b.warehouse_type) AS warehouse_types,
                GROUP_CONCAT(DISTINCT COALESCE(s.name, '')) AS shelf_names,
                GROUP_CONCAT(DISTINCT COALESCE(b.zone_name, '')) AS zone_names,
                MAX(b.updated_at) AS updated_at
            FROM material_batches b
            LEFT JOIN shelves s ON s.id = b.shelf_id
            WHERE {' AND '.join(batch_where)}
            GROUP BY b.material_id
        )
    """
    select = """
        SELECT
            m.id AS material_id,
            m.material_code,
            m.name AS material_name,
            m.brand_model,
            m.spec,
            m.unit,
            m.category,
            m.sub_category,
            m.category_name,
            m.major_code,
            m.middle_code,
            m.small_code,
            ts.temporary_quantity,
            ts.available_quantity,
            ts.batch_count,
            ts.warehouse_types AS warehouse_type,
            ? AS inventory_status,
            TRIM(COALESCE(ts.shelf_names, '') || ' ' || COALESCE(ts.zone_names, '')) AS location,
            ts.updated_at,
            ? AS stock_source
        FROM temporary_stock ts
        JOIN materials m ON m.id = ts.material_id
    """
    all_params = [inventory_status, *batch_params, inventory_status, STOCK_SOURCE_TEMPORARY, *outer_params]
    where_sql = (" WHERE " + " AND ".join(outer_where)) if outer_where else ""
    count_sql = cte + "SELECT COUNT(*) FROM (" + select + where_sql + ") counted"
    cursor.execute(count_sql, all_params)
    total = int(cursor.fetchone()[0] or 0)
    data_sql = cte + select + where_sql + """
        ORDER BY
            CASE WHEN temporary_quantity > 0 THEN 0 ELSE 1 END,
            m.material_code,
            m.id
        LIMIT ? OFFSET ?
    """
    cursor.execute(data_sql, [*all_params, page_size, (page - 1) * page_size])
    return {
        "items": [dict(row) for row in cursor.fetchall()],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def temporary_batch_rows(cursor, material_id):
    cursor.execute(
        """
        SELECT b.*, s.name AS shelf_name
        FROM material_batches b
        LEFT JOIN shelves s ON s.id = b.shelf_id
        WHERE b.material_id = ? AND b.stock_source = ?
        ORDER BY b.received_date ASC, b.id ASC
        """,
        (int(material_id), STOCK_SOURCE_TEMPORARY),
    )
    batches = [dict(row) for row in cursor.fetchall()]
    attachments_by_batch = {int(batch["id"]): [] for batch in batches}
    for attachment in list_material_attachments(cursor, int(material_id)):
        batch_id = int(attachment.get("material_batch_id") or 0)
        if batch_id in attachments_by_batch:
            attachments_by_batch[batch_id].append(attachment)
    for batch in batches:
        batch["attachments"] = attachments_by_batch[int(batch["id"])]
    return batches


def temporary_record_rows(cursor, q="", page=1, page_size=30, material_id=0):
    page, page_size = _page_values(page, page_size)
    where = ["sr.stock_source = ?"]
    params = [STOCK_SOURCE_TEMPORARY]
    if material_id:
        where.append("sr.material_id = ?")
        params.append(int(material_id))
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        where.append("(m.material_code LIKE ? OR m.name LIKE ? OR sr.remark LIKE ? OR sr.business_type LIKE ?)")
        params.extend([like] * 4)
    where_sql = " AND ".join(where)
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM stock_records sr
        JOIN materials m ON m.id = sr.material_id
        WHERE {where_sql}
        """,
        params,
    )
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute(
        f"""
        SELECT sr.*, m.material_code, m.name AS material_name, m.brand_model, m.spec, m.unit,
               b.batch_no, u.display_name AS operator_name
        FROM stock_records sr
        JOIN materials m ON m.id = sr.material_id
        LEFT JOIN material_batches b ON b.id = sr.batch_id
        LEFT JOIN users u ON u.id = sr.operator_id
        WHERE {where_sql}
        ORDER BY sr.operation_date DESC, sr.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    )
    return {
        "items": [dict(row) for row in cursor.fetchall()],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def _existing_operation(cursor, operation_key):
    cursor.execute(
        "SELECT * FROM stock_records WHERE operation_key = ?",
        (str(operation_key or "").strip(),),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _ensure_material_not_transferring(cursor, material_id):
    cursor.execute(
        """
        SELECT id
        FROM inventory_transfer_tasks
        WHERE material_id = ? AND active_key IS NOT NULL
        LIMIT 1
        """,
        (int(material_id),),
    )
    if cursor.fetchone():
        raise ValueError("该物料正在转移到正式库，暂不能新增或调整临时库存")


def create_temporary_batch(cursor, data, user, ip_address=""):
    operation_key = str(data.get("operation_key") or "").strip()
    if not operation_key:
        raise ValueError("operation_key 不能为空")
    material_id = positive_int_value(data.get("material_id"), "物料")
    quantity = quantity_value(data.get("quantity"), "入库数量", positive=True)
    unit_price = price_value(data.get("unit_price") or 0, "入库单价")
    warehouse_type, shelf_id = _validate_warehouse_location(
        cursor, data.get("warehouse_type"), data.get("shelf_id")
    )
    cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
    if not cursor.fetchone():
        raise ValueError("物料不存在")
    received_date = _date_value(data.get("received_date"))
    begin_inventory_transaction(cursor.connection)
    existing = _existing_operation(cursor, operation_key)
    if existing:
        if (
            existing.get("stock_source") != STOCK_SOURCE_TEMPORARY
            or existing.get("business_type") != BUSINESS_TYPE_TEMPORARY_MANUAL_INBOUND
            or int(existing.get("material_id") or 0) != material_id
            or abs(float(existing.get("quantity") or 0) - quantity) > 1e-6
        ):
            raise ValueError("库存操作幂等键已被其他业务使用")
        return {"batch_id": int(existing.get("batch_id") or 0), "idempotent": True}
    _ensure_material_not_transferring(cursor, material_id)

    location = {
        "warehouse_type": warehouse_type,
        "shelf_id": shelf_id,
        "layer_number": positive_int_value(data.get("layer_number") or 1, "货架层"),
        "zone_name": str(data.get("zone_name") or data.get("location") or "A").strip().upper(),
        "received_date": received_date,
        "remark": str(data.get("remark") or "临时库手工入库").strip(),
    }
    batch_id = add_inventory_batch(
        cursor,
        material_id,
        quantity,
        unit_price,
        location,
        form_no="",
        stock_source=STOCK_SOURCE_TEMPORARY,
        business_type=BUSINESS_TYPE_TEMPORARY_MANUAL_INBOUND,
        operation_key=operation_key,
        operator_id=user.get("id") if user else None,
    )
    write_audit_log(
        cursor,
        user,
        "temporary_inventory.manual_inbound",
        "material_batch",
        batch_id,
        "临时库手工入库",
        {"material_id": material_id, "quantity": quantity, "operation_key": operation_key},
        ip_address,
    )
    return {"batch_id": int(batch_id), "idempotent": False}


def adjust_temporary_batch(cursor, batch_id, data, user, ip_address=""):
    adjustment = validated_number(data.get("adjustment_quantity"), "调整数量", required=True)
    if abs(adjustment) <= 1e-12:
        raise ValueError("调整数量不能为 0")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise ValueError("调整原因不能为空")
    operation_key = str(data.get("operation_key") or "").strip()
    if not operation_key:
        raise ValueError("operation_key 不能为空")
    business_type = (
        BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_IN
        if adjustment > 0
        else BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_OUT
    )
    begin_inventory_transaction(cursor.connection)
    existing = _existing_operation(cursor, operation_key)
    if existing:
        if (
            existing.get("stock_source") != STOCK_SOURCE_TEMPORARY
            or existing.get("business_type") != business_type
            or int(existing.get("batch_id") or 0) != int(batch_id)
            or abs(float(existing.get("quantity") or 0) - abs(adjustment)) > 1e-6
        ):
            raise ValueError("库存操作幂等键已被其他业务使用")
        cursor.execute("SELECT quantity, version FROM material_batches WHERE id = ?", (int(batch_id),))
        batch = cursor.fetchone()
        return {
            "batch_id": int(batch_id),
            "quantity": float(batch["quantity"] if batch else existing.get("balance_after") or 0),
            "version": int(batch["version"] if batch else 0),
            "idempotent": True,
        }
    cursor.execute(
        """
        SELECT material_id, stock_source, inventory_status
        FROM material_batches
        WHERE id = ?
        """,
        (int(batch_id),),
    )
    batch = cursor.fetchone()
    if not batch or batch["stock_source"] != STOCK_SOURCE_TEMPORARY:
        raise ValueError("临时批次不存在")
    if batch["inventory_status"] != INVENTORY_STATUS_AVAILABLE:
        raise ValueError("该临时批次已进入转移锁定状态，不能调整")
    _ensure_material_not_transferring(cursor, int(batch["material_id"]))

    result = adjust_inventory_batch(
        cursor,
        int(batch_id),
        adjustment,
        reason,
        stock_source=STOCK_SOURCE_TEMPORARY,
        business_type=business_type,
        operation_key=operation_key,
        operator_id=user.get("id") if user else None,
    )
    write_audit_log(
        cursor,
        user,
        "temporary_inventory.adjust",
        "material_batch",
        batch_id,
        "临时库库存调整",
        {
            "adjustment_quantity": adjustment,
            "reason": reason,
            "operation_key": operation_key,
        },
        ip_address,
    )
    result["idempotent"] = False
    return result


def temporary_material_choices(cursor, q="", limit=200):
    """Return all master records, including zero-stock records, for temporary inbound."""
    limit = min(500, max(1, int(limit or 200)))
    keyword = str(q or "").strip()
    where, params = "", []
    if keyword:
        like = f"%{keyword}%"
        where = (" WHERE m.material_code LIKE ? OR m.name LIKE ? OR m.brand_model LIKE ? "
                 "OR m.spec LIKE ? OR m.category_name LIKE ?")
        params = [like] * 5
    cursor.execute(f"""
        SELECT m.id, m.material_code, m.name, m.brand_model, m.spec, m.unit,
               m.category_name, m.material_type,
               COALESCE(SUM(CASE WHEN b.inventory_status = ? THEN b.quantity ELSE 0 END), 0) AS temporary_quantity,
               COUNT(b.id) AS temporary_batch_count
        FROM materials m LEFT JOIN material_batches b
          ON b.material_id = m.id AND b.stock_source = ?
        {where}
        GROUP BY m.id ORDER BY m.material_code, m.id LIMIT ?
        """, [INVENTORY_STATUS_AVAILABLE, STOCK_SOURCE_TEMPORARY, *params, limit])
    return [dict(row) for row in cursor.fetchall()]


def create_temporary_material(cursor, data, user, ip_address=""):
    code = str(data.get("material_code") or "").strip()
    if not code:
        raise ValueError("物料编号不能为空")
    cursor.execute("SELECT 1 FROM materials WHERE material_code = ?", (code,))
    if cursor.fetchone():
        raise ValueError("物料编号已存在，请使用“新增临时批次”")
    begin_inventory_transaction(cursor.connection)
    material = upsert_material_master(cursor, data)
    batch_data = dict(data)
    batch_data["material_id"] = material["id"]
    result = create_temporary_batch(cursor, batch_data, user, ip_address)
    write_audit_log(cursor, user, "temporary_inventory.material_created", "material", material["id"],
                    "新建临时库物料", {"material_code": material["material_code"], "batch_id": result["batch_id"]}, ip_address)
    return {"material": material, **result}


def update_temporary_material(cursor, material_id, data, user, ip_address=""):
    cursor.execute("SELECT 1 FROM material_batches WHERE material_id = ? AND stock_source = ? LIMIT 1",
                   (int(material_id), STOCK_SOURCE_TEMPORARY))
    if not cursor.fetchone():
        raise ValueError("该物料不属于临时库，不能从临时库维护")
    begin_inventory_transaction(cursor.connection)
    material = update_material_master_by_id(cursor, int(material_id), data)
    write_audit_log(cursor, user, "temporary_inventory.material_updated", "material", material_id,
                    "修改临时库物料", {"material_code": material["material_code"], "name": material["name"]}, ip_address)
    return material


def delete_temporary_material(cursor, material_id, user, ip_address=""):
    material_id = int(material_id)
    cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
    material = cursor.fetchone()
    if not material:
        raise ValueError("物料不存在")
    cursor.execute("SELECT 1 FROM inventory_transfer_tasks WHERE material_id = ? AND active_key IS NOT NULL LIMIT 1", (material_id,))
    if cursor.fetchone():
        raise ValueError("该物料正在转正式库，不能删除")
    cursor.execute("SELECT COUNT(*) FROM material_batches WHERE material_id = ? AND stock_source <> ?", (material_id, STOCK_SOURCE_TEMPORARY))
    if int(cursor.fetchone()[0] or 0):
        raise ValueError("该物料含正式库批次，请从物料管理中维护")
    cursor.execute("SELECT COUNT(*) FROM stock_records WHERE material_id = ? AND stock_source <> ?", (material_id, STOCK_SOURCE_TEMPORARY))
    if int(cursor.fetchone()[0] or 0):
        raise ValueError("该物料含正式库流水，请从物料管理中维护")
    cursor.execute("SELECT COUNT(*) FROM material_attachments WHERE material_id = ? AND material_batch_id IS NOT NULL", (material_id,))
    if int(cursor.fetchone()[0] or 0):
        raise ValueError("该物料已有需永久留存的批次附件，不能删除")
    begin_inventory_transaction(cursor.connection)
    recycle_material(cursor, material_id, user.get("id") if user else None)
    for table in ("material_batches", "stock_records", "material_positions", "inventory"):
        cursor.execute(f"DELETE FROM {table} WHERE material_id = ?", (material_id,))
    cursor.execute("DELETE FROM materials WHERE id = ?", (material_id,))
    write_audit_log(cursor, user, "temporary_inventory.material_deleted", "material", material_id,
                    "删除临时库物料", {"material_code": material["material_code"], "name": material["name"]}, ip_address)
