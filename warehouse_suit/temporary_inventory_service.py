"""Temporary inventory query and manual-management services."""

from __future__ import annotations

import json
from datetime import datetime
from warehouse_suit.attachments import list_material_attachments

from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_BORROW_OUTBOUND,
    BUSINESS_TYPE_BORROW_RETURN_INBOUND,
    BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_IN,
    BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_OUT,
    BUSINESS_TYPE_TEMPORARY_MANUAL_INBOUND,
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
    validate_inventory_status,
)
from warehouse_suit.inventory_service import (
    add_inventory_batch,
    adjust_inventory_batch,
    begin_inventory_transaction,
    update_inventory_total,
    validate_inventory_location,
)
from warehouse_suit.material_service import update_material_master_by_id, upsert_material_master
from warehouse_suit.recycle import recycle_material
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.validation import positive_int_value, quantity_value, unit_price_from_total_amount, validated_number


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
    material_warehouse_code="",
    inventory_status=INVENTORY_STATUS_AVAILABLE,
    include_zero=False,
):
    inventory_status = validate_inventory_status(inventory_status)
    page, page_size = _page_values(page, page_size)
    batch_where = ["b.stock_source = ?", "b.inventory_status = ?"]
    batch_params = [STOCK_SOURCE_TEMPORARY, inventory_status]
    outer_where = []
    outer_params = []
    material_warehouse_code = str(material_warehouse_code or "").strip()
    if material_warehouse_code:
        if material_warehouse_code not in {"10", "20"}:
            raise ValueError("物料库房编码必须为 10 或 20")
        outer_where.append("substr(m.material_code, 3, 2) = ?")
        outer_params.append(material_warehouse_code)
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
                COALESCE(SUM(CASE WHEN b.inventory_status = ? THEN b.quantity * b.unit_price ELSE 0 END), 0) AS amount,
                COUNT(*) AS batch_count,
                GROUP_CONCAT(DISTINCT COALESCE(s.warehouse_type, b.warehouse_type)) AS warehouse_types,
                GROUP_CONCAT(DISTINCT COALESCE(s.name, '')) AS shelf_names,
                GROUP_CONCAT(DISTINCT COALESCE(b.zone_name, '')) AS zone_names,
                GROUP_CONCAT(DISTINCT TRIM(COALESCE(b.usage_kind, ''))) AS usage_kinds,
                GROUP_CONCAT(DISTINCT TRIM(COALESCE(b.purchase_project, ''))) AS purchase_projects,
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
            ts.amount,
            ts.batch_count,
            ts.warehouse_types AS warehouse_type,
            ts.usage_kinds,
            ts.purchase_projects,
            ? AS inventory_status,
            TRIM(COALESCE(ts.shelf_names, '') || ' ' || COALESCE(ts.zone_names, '')) AS location,
            ts.updated_at,
            ? AS stock_source
        FROM temporary_stock ts
        JOIN materials m ON m.id = ts.material_id
    """
    all_params = [inventory_status, inventory_status, *batch_params, inventory_status, STOCK_SOURCE_TEMPORARY, *outer_params]
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
        SELECT b.*, s.name AS shelf_name, s.warehouse_type AS shelf_warehouse_type
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
        batch["warehouse_type"] = batch.get("shelf_warehouse_type") or batch.get("warehouse_type")
        batch.pop("shelf_warehouse_type", None)
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



def create_temporary_batch(cursor, data, user, ip_address=""):
    operation_key = str(data.get("operation_key") or "").strip()
    if not operation_key:
        raise ValueError("operation_key 不能为空")
    material_id = positive_int_value(data.get("material_id"), "物料")
    quantity = quantity_value(data.get("quantity"), "入库数量", positive=True)
    unit_price = unit_price_from_total_amount(
        data.get("total_amount"), quantity, "临时库入库总金额", "入库数量"
    )
    warehouse_type, shelf_id = validate_inventory_location(
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
    batch_data = dict(data)
    material_data = dict(data)
    material_data.pop("quantity", None)
    material = upsert_material_master(cursor, material_data)
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


# 库存转换阶段仍先跳过借用流水日期；完整改号/改日在 promote_renumber 中统一处理。
_BORROW_BUSINESS_TYPES = (BUSINESS_TYPE_BORROW_OUTBOUND, BUSINESS_TYPE_BORROW_RETURN_INBOUND)


def _follow_unissued_promoted_lines(cursor, batch_ids):
    """这批临时库转空后，还没出库的借用和领用改从正式库出。

    出库前流程行只记下库别，没有批次号。该项目、该用料形式的临时库已经没有剩余时，
    再按临时库出库一定会失败，所以把来源改成正式库。临时库还有剩余时不动。
    """
    ids = [int(item) for item in batch_ids]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    cursor.execute(
        f"""
        SELECT DISTINCT
            material_id,
            TRIM(COALESCE(purchase_project, '')) AS purchase_project,
            TRIM(COALESCE(usage_kind, '')) AS usage_kind
        FROM material_batches
        WHERE id IN ({placeholders})
        """,
        ids,
    )
    pools = [dict(row) for row in cursor.fetchall()]
    for pool in pools:
        cursor.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN inventory_status = ? THEN quantity ELSE 0 END
            ), 0)
            FROM material_batches
            WHERE material_id = ? AND stock_source = ?
              AND TRIM(COALESCE(purchase_project, '')) = ?
              AND TRIM(COALESCE(usage_kind, '')) = ?
            """,
            (
                INVENTORY_STATUS_AVAILABLE,
                pool["material_id"],
                STOCK_SOURCE_TEMPORARY,
                pool["purchase_project"],
                pool["usage_kind"],
            ),
        )
        if float(cursor.fetchone()[0] or 0) > 1e-9:
            continue
        cursor.execute(
            """
            UPDATE workflow_items
            SET stock_source = ?
            WHERE id IN (
                SELECT wi.id
                FROM workflow_items wi
                JOIN workflow_forms f ON f.id = wi.form_id
                WHERE f.form_type IN ('borrow', 'claim')
                  AND f.status NOT IN ('completed', 'rejected', 'cancelled')
                  AND wi.material_id = ?
                  AND TRIM(COALESCE(wi.stock_source, '')) = ?
                  AND TRIM(COALESCE(wi.purchase_project, '')) = ?
                  AND TRIM(COALESCE(json_extract(wi.data_json, '$.usage_kind'), '')) = ?
                  AND (
                        f.form_type <> 'borrow'
                        OR COALESCE(json_extract(wi.data_json, '$.borrow_item_type'), 'material') = 'material'
                  )
            )
            """,
            (
                STOCK_SOURCE_FORMAL,
                pool["material_id"],
                STOCK_SOURCE_TEMPORARY,
                pool["purchase_project"],
                pool["usage_kind"],
            ),
        )


def _sync_promoted_borrow_sources(cursor, batch_ids):
    """临时批次转入正式库后，未归还借用改记这个正式批次，未出库的单改从来源出库。"""
    ids = {int(item) for item in batch_ids}
    if not ids:
        return
    cursor.execute(
        """
        SELECT id, data_json, stock_source, workflow_item_id
        FROM borrow_records
        WHERE item_type = 'material' AND status = 'borrowed'
        """
    )
    for record in cursor.fetchall():
        record = dict(record)
        try:
            data = json.loads(record.get("data_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        batches = [batch for batch in (data.get("consumed_batches") or []) if isinstance(batch, dict)]
        touched = False
        all_formal = bool(batches)
        for batch in batches:
            batch_id = int(batch.get("batch_id") or batch.get("id") or 0)
            if batch_id in ids:
                batch["stock_source"] = STOCK_SOURCE_FORMAL
                touched = True
            if not batch_id:
                all_formal = False
                continue
            cursor.execute("SELECT stock_source FROM material_batches WHERE id = ?", (batch_id,))
            row = cursor.fetchone()
            if not row or row["stock_source"] != STOCK_SOURCE_FORMAL:
                all_formal = False
        if not touched:
            continue
        data["consumed_batches"] = batches
        stock_source = STOCK_SOURCE_FORMAL if all_formal else (record.get("stock_source") or STOCK_SOURCE_TEMPORARY)
        cursor.execute(
            "UPDATE borrow_records SET stock_source = ?, data_json = ?, updated_at = ? WHERE id = ?",
            (stock_source, json.dumps(data, ensure_ascii=False), now_text(), record["id"]),
        )
        item_id = int(record.get("workflow_item_id") or 0)
        if item_id and all_formal:
            cursor.execute("SELECT data_json FROM workflow_items WHERE id = ?", (item_id,))
            item = cursor.fetchone()
            item_data = {}
            if item and item["data_json"]:
                try:
                    item_data = json.loads(item["data_json"] or "{}")
                except (TypeError, ValueError):
                    item_data = {}
            if not isinstance(item_data, dict):
                item_data = {}
            item_data["outbound_stock_source"] = STOCK_SOURCE_FORMAL
            cursor.execute(
                "UPDATE workflow_items SET stock_source = ?, data_json = ? WHERE id = ?",
                (STOCK_SOURCE_FORMAL, json.dumps(item_data, ensure_ascii=False), item_id),
            )
    placeholders = ",".join("?" for _ in ids)
    cursor.execute(
        f"""
        UPDATE stock_records
        SET stock_source = ?
        WHERE stock_source = ? AND batch_id IN ({placeholders})
          AND business_type IN (?, ?)
        """,
        [
            STOCK_SOURCE_FORMAL,
            STOCK_SOURCE_TEMPORARY,
            *ids,
            BUSINESS_TYPE_BORROW_OUTBOUND,
            BUSINESS_TYPE_BORROW_RETURN_INBOUND,
        ],
    )
    _follow_unissued_promoted_lines(cursor, ids)


def promote_temporary_material(
    cursor, material_id, selected_batches, user, ip_address="", allow_zero=False, skip_renumber=False,
    promotion_time=None,
):
    """Convert selected transferable temporary batches to formal inventory."""
    material_id = int(material_id or 0)
    selections = selected_batches if isinstance(selected_batches, list) else []
    normalized = []
    selected_ids = set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise ValueError("转移批次参数无效")
        batch_id = positive_int_value(selection.get("id") or selection.get("batch_id"), "批次")
        try:
            version = int(selection.get("version"))
        except (TypeError, ValueError) as exc:
            raise ValueError("批次版本不能为空") from exc
        if batch_id in selected_ids:
            raise ValueError("转移批次不能重复")
        selected_ids.add(batch_id)
        normalized.append({"id": batch_id, "version": version})
    if not material_id:
        raise ValueError("物料不存在")
    if not normalized:
        raise ValueError("请至少选择一个可转移批次")

    require_temporary_inventory_enabled(cursor)
    begin_inventory_transaction(cursor.connection)
    cursor.execute("SELECT id, material_code, name FROM materials WHERE id = ?", (material_id,))
    material = cursor.fetchone()
    if not material:
        raise ValueError("物料不存在")
    material = dict(material)
    batch_ids = [item["id"] for item in normalized]
    placeholders = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"""
        SELECT id, version, quantity, inventory_status, transfer_blocked,
               transfer_block_reason
        FROM material_batches
        WHERE material_id = ? AND stock_source = ? AND id IN ({placeholders})
        ORDER BY id
        """,
        [material_id, STOCK_SOURCE_TEMPORARY, *batch_ids],
    )
    batch_rows = {int(row["id"]): dict(row) for row in cursor.fetchall()}
    if len(batch_rows) != len(batch_ids):
        raise ValueError("所选批次不存在或已转入正式库，请刷新后重试")
    for selection in normalized:
        batch = batch_rows[selection["id"]]
        if int(batch.get("version") or 0) != selection["version"]:
            raise ValueError("批次库存已变化，请刷新批次详情后重试")
        if batch.get("transfer_blocked"):
            reason = "领用退库批次" if batch.get("transfer_block_reason") == "claim_return" else "受限批次"
            raise ValueError(f"{reason}不可转入正式库")
        if batch.get("inventory_status") != INVENTORY_STATUS_AVAILABLE:
            raise ValueError("所选批次当前不可转移")
        if not allow_zero and float(batch.get("quantity") or 0) <= 0:
            raise ValueError("零库存批次不能转入正式库")

    timestamp = promotion_time or now_text()
    date_value = timestamp[:10]
    if not skip_renumber:
        _prepare_promotion_flows(cursor, batch_ids, date_value)
    cursor.execute(
        f"""
        UPDATE temporary_issue_obligations
        SET settled_quantity = issued_quantity - returned_quantity,
            status = 'settled', settled_at = ?, updated_at = ?, error_message = ''
        WHERE status IN ('pending', 'reserved', 'processing', 'exception')
          AND stock_record_id IN (
              SELECT id FROM stock_records WHERE batch_id IN ({placeholders})
          )
        """,
        [timestamp, timestamp, *batch_ids],
    )
    settled_obligations = int(cursor.rowcount or 0)
    cursor.execute(
        f"""
        UPDATE material_batches
        SET stock_source = ?, received_date = ?, version = version + 1, updated_at = ?
        WHERE material_id = ? AND stock_source = ? AND id IN ({placeholders})
          AND transfer_blocked = 0
        """,
        [STOCK_SOURCE_FORMAL, date_value, timestamp, material_id, STOCK_SOURCE_TEMPORARY, *batch_ids],
    )
    converted_batches = int(cursor.rowcount or 0)
    if converted_batches != len(batch_ids):
        raise ValueError("批次状态已变化，请刷新后重试")
    cursor.execute(
        f"""
        UPDATE stock_records
        SET stock_source = ?, operation_date = ?, created_at = ?,
            business_type = CASE
                WHEN business_type IN ('temporary_acceptance_inbound', 'temporary_manual_inbound',
                                       'temporary_manual_adjust_in', 'temporary_manual_adjust_out')
                THEN 'manual'
                ELSE business_type
            END
        WHERE stock_source = ? AND batch_id IN ({placeholders})
          AND business_type NOT IN (?, ?)
        """,
        [STOCK_SOURCE_FORMAL, date_value, timestamp, STOCK_SOURCE_TEMPORARY, *batch_ids, *_BORROW_BUSINESS_TYPES],
    )
    converted_records = int(cursor.rowcount or 0)
    _sync_promoted_borrow_sources(cursor, batch_ids)
    formal_quantity, _ = update_inventory_total(cursor, material_id, STOCK_SOURCE_FORMAL)
    renumber = {"rename_map": {}, "batch_no_map": {}}
    if not skip_renumber:
        from warehouse_suit.promote_renumber import apply_promote_renumber

        renumber = apply_promote_renumber(
            cursor,
            batch_ids,
            date_value=date_value,
            timestamp=timestamp,
        )
    write_audit_log(
        cursor,
        user,
        "temporary_inventory.batches_promoted",
        "material",
        material_id,
        "临时库批次转正式库",
        {
            "material_code": material["material_code"],
            "material_name": material["name"],
            "batch_ids": batch_ids,
            "converted_batches": converted_batches,
            "converted_stock_records": converted_records,
            "settled_obligations": settled_obligations,
            "converted_date": date_value,
            "rename_map": renumber.get("rename_map") or {},
            "batch_no_map": renumber.get("batch_no_map") or {},
            "rk_changes": renumber.get("rk_changes") or [],
        },
        ip_address,
    )
    return {
        "material_id": material_id,
        "batch_ids": batch_ids,
        "converted_batches": converted_batches,
        "converted_stock_records": converted_records,
        "settled_obligations": settled_obligations,
        "formal_quantity": formal_quantity,
        "converted_date": date_value,
        "rename_map": renumber.get("rename_map") or {},
        "batch_no_map": renumber.get("batch_no_map") or {},
        "rk_changes": renumber.get("rk_changes") or [],
    }


def _load_json(text):
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_temporary_acceptance_forms(cursor):
    """没有采购订单的验收单。转正式库之后仍留在这张列表里。"""
    require_temporary_inventory_enabled(cursor)
    cursor.execute(
        """
        SELECT f.id, f.form_no, f.title, f.status, f.created_at, f.data_json,
               u.display_name AS applicant_name
        FROM workflow_forms f
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE f.form_type = 'acceptance'
          AND COALESCE(json_extract(f.data_json, '$.has_purchase_order'), 1) = 0
        ORDER BY f.id DESC
        """
    )
    forms = []
    for form in cursor.fetchall():
        data = _load_json(form["data_json"])
        cursor.execute(
            "SELECT material_name, qualified_quantity, data_json FROM workflow_items WHERE form_id = ? ORDER BY id",
            (form["id"],),
        )
        items = [dict(row) for row in cursor.fetchall()]
        pending = False
        for item in items:
            item_data = _load_json(item["data_json"])
            if float(item["qualified_quantity"] or 0) > 0 and not item_data.get("inbound_done"):
                pending = True
        forms.append({
            "id": form["id"],
            "form_no": form["form_no"],
            "title": form["title"] or "",
            "status": form["status"] or "",
            "applicant_name": form["applicant_name"] or "",
            "created_at": form["created_at"] or "",
            "materials": [item["material_name"] for item in items if item["material_name"]],
            "inbound_ready": not pending and bool(items),
            "promoted": bool(data.get("promoted_to_formal")),
            "promoted_at": data.get("promoted_at") or "",
        })
    return forms


def promote_acceptance_form(cursor, form_id, user, ip_address=""):
    """把一张没有采购订单的验收单下的临时批次全部转到正式库。"""
    require_temporary_inventory_enabled(cursor)
    begin_inventory_transaction(cursor.connection)
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'acceptance'", (int(form_id),))
    form = cursor.fetchone()
    if not form:
        raise ValueError("验收单不存在")
    form = dict(form)
    data = _load_json(form["data_json"])
    if data.get("has_purchase_order") is not False:
        raise ValueError("只有没有采购订单的验收单可以转移到正式库")
    if data.get("promoted_to_formal"):
        raise ValueError("该验收单已转移到正式库")
    batch_ids, pending = _acceptance_batch_ids(cursor, form["id"])
    if pending:
        raise ValueError("还有未入库物料，不能整单转移")
    if not batch_ids:
        raise ValueError("没有可转移的临时批次")
    placeholders = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"""
        SELECT id, material_id, version, quantity, inventory_status, transfer_blocked,
               transfer_block_reason, stock_source
        FROM material_batches
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        batch_ids,
    )
    batches = [dict(row) for row in cursor.fetchall()]
    temporary_batches = [batch for batch in batches if batch["stock_source"] == STOCK_SOURCE_TEMPORARY]
    if not temporary_batches:
        raise ValueError("没有可转移的临时批次")
    for batch in temporary_batches:
        if batch.get("transfer_blocked"):
            reason = "领用退库批次" if batch.get("transfer_block_reason") == "claim_return" else "受限批次"
            raise ValueError(f"{reason}不可转入正式库")
        if batch.get("inventory_status") != INVENTORY_STATUS_AVAILABLE:
            raise ValueError("所选批次当前不可转移")
    material_ids = {int(batch["material_id"]) for batch in temporary_batches}
    selected_ids = [int(batch["id"]) for batch in temporary_batches]
    timestamp = now_text()
    date_value = timestamp[:10]
    acceptance_ids = _prepare_promotion_flows(
        cursor, selected_ids, date_value, whole_form_id=form["id"], material_ids=material_ids,
    )
    promoted_form_id = acceptance_ids.get(form["id"], form["id"])
    grouped = {}
    for batch in temporary_batches:
        grouped.setdefault(int(batch["material_id"]), []).append(
            {"id": int(batch["id"]), "version": int(batch["version"] or 0)}
        )
    converted = []
    for material_id, selections in grouped.items():
        converted.append(promote_temporary_material(
            cursor, material_id, selections, user, ip_address, allow_zero=True,
            skip_renumber=True, promotion_time=timestamp,
        ))
    from warehouse_suit.promote_renumber import apply_promote_renumber

    renumber = apply_promote_renumber(
        cursor,
        selected_ids,
        acceptance_form_id=promoted_form_id,
        date_value=date_value,
        timestamp=timestamp,
    )
    cursor.execute("SELECT form_no, data_json FROM workflow_forms WHERE id = ?", (promoted_form_id,))
    form_row = dict(cursor.fetchone())
    data = _load_json(form_row["data_json"])
    data["promoted_to_formal"] = True
    data["promoted_at"] = date_value
    data["promote_rename_map"] = renumber.get("rename_map") or {}
    cursor.execute(
        "UPDATE workflow_forms SET data_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), timestamp, promoted_form_id),
    )
    write_audit_log(
        cursor,
        user,
        "temporary_inventory.acceptance_promoted",
        "workflow_form",
        promoted_form_id,
        "验收单临时库转正式库并改号补位",
        {
            "form_no": form_row["form_no"],
            "rename_map": renumber.get("rename_map") or {},
            "batch_no_map": renumber.get("batch_no_map") or {},
            "rk_changes": renumber.get("rk_changes") or [],
            "converted_date": date_value,
        },
        ip_address,
    )
    return {
        "form_id": promoted_form_id,
        "form_no": form_row["form_no"],
        "converted_date": date_value,
        "materials": converted,
        "rename_map": renumber.get("rename_map") or {},
        "batch_no_map": renumber.get("batch_no_map") or {},
        "rk_changes": renumber.get("rk_changes") or [],
    }


def _acceptance_batch_ids(cursor, form_id):
    cursor.execute(
        "SELECT id, qualified_quantity, data_json FROM workflow_items WHERE form_id = ? ORDER BY id",
        (int(form_id),),
    )
    batch_ids = []
    pending = False
    for item in cursor.fetchall():
        item_data = _load_json(item["data_json"])
        if float(item["qualified_quantity"] or 0) > 0 and not item_data.get("inbound_done"):
            pending = True
        if item_data.get("inbound_batch_id"):
            batch_ids.append(int(item_data["inbound_batch_id"]))
        for batch in item_data.get("inbound_batches") or []:
            if isinstance(batch, dict) and batch.get("batch_id"):
                batch_ids.append(int(batch["batch_id"]))
    cursor.execute(
        """
        SELECT batch_id, operation_key FROM stock_records
        WHERE batch_id IS NOT NULL AND operation_key GLOB ?
        """,
        (f"acceptance:{int(form_id)}:*:inbound",),
    )
    for row in cursor.fetchall():
        if row["batch_id"]:
            batch_ids.append(int(row["batch_id"]))
    unique = []
    seen = set()
    for batch_id in batch_ids:
        if batch_id in seen:
            continue
        seen.add(batch_id)
        unique.append(batch_id)
    return unique, pending


def split_acceptance_form_claims(cursor, form_id, user, ip_address=""):
    """拆开挡住这张验收单的申领单。不改库存数量，也不直接转到正式库。"""
    require_temporary_inventory_enabled(cursor)
    cursor.execute(
        "SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'acceptance'",
        (int(form_id),),
    )
    form = cursor.fetchone()
    if not form:
        raise ValueError("验收单不存在")
    form = dict(form)
    data = _load_json(form["data_json"])
    if data.get("has_purchase_order") is not False:
        raise ValueError("只有没有采购订单的验收单可以拆分申领单")
    batch_ids, _pending = _acceptance_batch_ids(cursor, form["id"])
    if not batch_ids:
        raise ValueError("没有可转移的临时批次")
    placeholders = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"SELECT DISTINCT material_id FROM material_batches WHERE id IN ({placeholders})",
        batch_ids,
    )
    material_ids = {int(row["material_id"]) for row in cursor.fetchall() if row["material_id"]}
    from warehouse_suit.claim_split_service import split_blocking_claims

    return split_blocking_claims(cursor, batch_ids, material_ids, user, ip_address)


def _prepare_promotion_flows(cursor, batch_ids, date_value, *, whole_form_id=None, material_ids=()):
    from warehouse_suit.acceptance_promotion import prepare_acceptances
    from warehouse_suit.borrow_promotion import split_borrows_for_promotion
    from warehouse_suit.claim_split_service import blocking_claim_problems, split_claims_for_promotion

    if material_ids:
        for problem in blocking_claim_problems(cursor, batch_ids, material_ids):
            if not problem["splittable"]:
                raise ValueError(problem["reason"])
    acceptance_ids = prepare_acceptances(cursor, batch_ids, date_value, whole_form_id=whole_form_id)
    split_claims_for_promotion(cursor, batch_ids, date_value)
    split_borrows_for_promotion(cursor, batch_ids, date_value)
    return acceptance_ids
