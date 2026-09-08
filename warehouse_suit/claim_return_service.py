"""Personal issued-material balances and atomic returns to temporary inventory."""

from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import (
    add_inventory_batch,
    adjust_inventory_batch,
    begin_inventory_transaction,
)
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.temporary_inventory_visibility import append_workflow_visibility
from warehouse_suit.validation import quantity_value


ISSUED_SQL = """
    SELECT sr.id AS outbound_record_id, sr.workflow_item_id, sr.material_id,
           sr.quantity AS issued_quantity, sr.operation_date, sr.form_no,
           sr.unit_price, sr.batch_id, f.applicant_id,
           m.material_code, m.name AS material_name, m.brand_model, m.spec, m.unit,
           b.batch_no, COALESCE(b.stock_source, sr.stock_source) AS stock_source, b.inventory_status,
           b.warehouse_type, b.shelf_id, b.layer_number, b.zone_name,
           COALESCE(r.returned_quantity, 0) AS returned_quantity,
           sr.quantity - COALESCE(r.returned_quantity, 0) AS returnable_quantity
    FROM stock_records sr
    JOIN workflow_forms f ON f.form_no = sr.form_no AND f.form_type = 'claim'
    JOIN materials m ON m.id = sr.material_id
    LEFT JOIN material_batches b ON b.id = sr.batch_id AND b.material_id = sr.material_id
    LEFT JOIN (
        SELECT outbound_record_id, SUM(quantity) AS returned_quantity
        FROM claim_returns GROUP BY outbound_record_id
    ) r ON r.outbound_record_id = sr.id
"""


def issued_scope(cursor, user_id):
    where = ["sr.operation_type = 'out'", "sr.quantity > 0", "f.applicant_id = ?"]
    params = [user_id]
    append_workflow_visibility(cursor, where, params)
    if not temporary_inventory_enabled(cursor):
        where.append("COALESCE(b.stock_source, sr.stock_source) = 'formal'")
    return where, params


def my_claims(cursor, user_id, page=1, keyword=""):
    page = max(1, int(page))
    where, params = issued_scope(cursor, user_id)
    if keyword.strip():
        where.append("(m.material_code LIKE ? OR m.name LIKE ? OR sr.form_no LIKE ?)")
        params.extend(["%" + keyword.strip() + "%"] * 3)
    sql = ISSUED_SQL + " WHERE " + " AND ".join(where)
    cursor.execute("SELECT COUNT(*) FROM (" + sql + ")", params)
    total = cursor.fetchone()[0]
    pages = max(1, (total + 19) // 20)
    page = min(page, pages)
    cursor.execute(sql + " ORDER BY sr.id DESC LIMIT 20 OFFSET ?", [*params, (page - 1) * 20])
    return {"items": [dict(row) for row in cursor.fetchall()],
            "total": total, "page": page, "pages": pages}


def return_claim(cursor, user_id, outbound_record_id, quantity, operation_key):
    quantity = quantity_value(quantity, "退库数量", positive=True)
    if quantity <= 0:
        raise ValueError("退库数量必须大于 0")
    operation_key = str(operation_key or "").strip()
    if not operation_key or len(operation_key) > 128:
        raise ValueError("退库请求编号不能为空且不能超过128字符")
    begin_inventory_transaction(cursor.connection)
    cursor.execute("SELECT * FROM claim_returns WHERE operation_key = ?", (operation_key,))
    existing = cursor.fetchone()
    if existing:
        if (existing["user_id"] != user_id
                or existing["outbound_record_id"] != outbound_record_id
                or abs(existing["quantity"] - quantity) > 1e-9):
            raise ValueError("退库请求编号已用于其他退库操作")
        return {"return_id": existing["id"], "quantity": existing["quantity"], "idempotent": True}
    where, params = issued_scope(cursor, user_id)
    where.append("sr.id = ?")
    cursor.execute(ISSUED_SQL + " WHERE " + " AND ".join(where), [*params, outbound_record_id])
    row = cursor.fetchone()
    if not row:
        raise PermissionError("没有该领用记录的退库权限，或库存来源当前不可用")
    row = dict(row)
    if row["returnable_quantity"] <= 0 or quantity > row["returnable_quantity"] + 1e-9:
        raise ValueError("退库数量不能超过尚未退回的领用数量")
    quantity = min(quantity, row["returnable_quantity"])
    if row["inventory_status"] is None:
        raise ValueError("原出库批次缺失，请联系库管核对原始出库记录")
    if row["inventory_status"] != "available":
        raise ValueError("原批次正在处理，暂不能退库")
    cursor.execute("SELECT * FROM temporary_issue_obligations WHERE stock_record_id = ?", (outbound_record_id,))
    obligation = cursor.fetchone()
    if obligation:
        if obligation["status"] != "pending" or obligation["settled_quantity"] > 0:
            raise ValueError("该临时领用正在结算，请完成结算后退库")

    record_key = "claim-return:" + operation_key
    if row["stock_source"] == STOCK_SOURCE_TEMPORARY:
        adjust_inventory_batch(
            cursor, row["batch_id"], quantity, "领用退库 " + row["form_no"],
            stock_source=STOCK_SOURCE_TEMPORARY,
            business_type=BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
            operation_key=record_key,
            operator_id=user_id,
        )
    else:
        # 正式库领用退回后先进入临时库，后续由库管按转正式库流程处理。
        add_inventory_batch(
            cursor,
            row["material_id"],
            quantity,
            row["unit_price"],
            {
                "warehouse_type": row["warehouse_type"] or "office",
                "shelf_id": row["shelf_id"],
                "layer_number": row["layer_number"] or 1,
                "zone_name": row["zone_name"] or "A",
                "received_date": now_text()[:10],
                "remark": "领用退库 " + row["form_no"],
            },
            row["form_no"],
            stock_source=STOCK_SOURCE_TEMPORARY,
            business_type=BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
            operation_key=record_key,
            operator_id=user_id,
        )
    cursor.execute("""
        UPDATE stock_records SET form_no = ?, workflow_item_id = ?, unit_price = ?, amount = ?
        WHERE operation_key = ?
    """, (row["form_no"], row["workflow_item_id"], row["unit_price"],
          quantity * row["unit_price"], record_key))
    cursor.execute("SELECT id FROM stock_records WHERE operation_key = ?", (record_key,))
    inbound_id = cursor.fetchone()[0]
    cursor.execute("""
        INSERT INTO claim_returns
            (outbound_record_id, inbound_record_id, user_id, quantity, operation_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (outbound_record_id, inbound_id, user_id, quantity, operation_key, now_text()))
    return_id = cursor.lastrowid
    if obligation:
        cursor.execute("""
            UPDATE temporary_issue_obligations
            SET returned_quantity = returned_quantity + ?,
                status = CASE WHEN returned_quantity + ? >= issued_quantity THEN 'cancelled' ELSE status END,
                updated_at = ?
            WHERE id = ?
        """, (quantity, quantity, now_text(), obligation["id"]))
    return {"return_id": return_id, "quantity": quantity, "idempotent": False}
