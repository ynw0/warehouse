"""Personal issued-material balances and atomic returns to temporary inventory."""

import json

from warehouse_suit.db import now_text
from warehouse_suit.issued_material_service import (
    SOURCE_BORROW_CLAIM,
    SOURCE_CLAIM_OUTBOUND,
    list_issued_lots,
)
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import (
    add_inventory_batch,
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
           COALESCE(b.purchase_project, '') AS purchase_project,
           COALESCE(b.usage_kind, '') AS usage_kind,
           COALESCE(r.returned_quantity, 0) AS returned_quantity,
           sr.quantity - COALESCE(r.returned_quantity, 0) AS returnable_quantity
    FROM stock_records sr
    JOIN workflow_forms f ON f.form_no = sr.form_no AND f.form_type IN ('claim', 'special_claim')
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
    keyword = str(keyword or "").strip().lower()
    lots = [
        lot
        for lot in list_issued_lots(cursor, user_id)
        if lot["source_kind"] in {SOURCE_CLAIM_OUTBOUND, SOURCE_BORROW_CLAIM}
    ]
    if keyword:
        # 编号、名称、品牌、规格、采购申请人和领用单号都可查。
        lots = [lot for lot in lots if keyword in _claim_search_text(lot)]
    lots.sort(
        key=lambda lot: (str(lot.get("operation_date") or ""), int(lot.get("source_id") or 0)),
        reverse=True,
    )
    total = len(lots)
    pages = max(1, (total + 19) // 20)
    page = min(page, pages)
    sliced = lots[(page - 1) * 20 : page * 20]
    items = []
    for lot in sliced:
        items.append(
            {
                "source_kind": lot["source_kind"],
                "source_id": lot["source_id"],
                "outbound_record_id": lot.get("outbound_record_id"),
                "form_id": lot.get("form_id"),
                "form_no": lot.get("form_no") or "",
                "material_id": lot.get("material_id"),
                "material_code": lot.get("material_code") or "",
                "material_name": lot.get("material_name") or "",
                "brand_model": lot.get("brand_model") or "",
                "spec": lot.get("spec") or "",
                "unit": lot.get("unit") or "",
                "batch_id": lot.get("batch_id"),
                "batch_no": lot.get("batch_no") or "",
                "stock_source": lot.get("stock_source"),
                "inventory_status": lot.get("inventory_status"),
                "operation_date": lot.get("operation_date") or "",
                "issued_quantity": lot.get("issued_quantity") or 0,
                "returned_quantity": lot.get("returned_quantity") or 0,
                "consumed_quantity": lot.get("consumed_quantity") or 0,
                "consume_status": lot.get("consume_status") or "",
                "returnable_quantity": lot.get("returnable_quantity") or 0,
                "can_return": lot["source_kind"] in {SOURCE_CLAIM_OUTBOUND, SOURCE_BORROW_CLAIM}
                and float(lot.get("returnable_quantity") or 0) > 1e-9
                and (
                    lot["source_kind"] == SOURCE_BORROW_CLAIM
                    or lot.get("inventory_status") == "available"
                ),
            }
        )
    return {"items": items, "total": total, "page": page, "pages": pages}


def _claim_search_text(lot):
    return " ".join(
        str(lot.get(key) or "")
        for key in (
            "material_code",
            "material_name",
            "brand_model",
            "spec",
            "purchase_applicant",
            "form_no",
        )
    ).lower()


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
    cursor.execute(
        """
        SELECT COALESCE(SUM(quantity), 0)
        FROM claim_consumptions
        WHERE source_kind = ? AND source_id = ?
        """,
        (SOURCE_CLAIM_OUTBOUND, outbound_record_id),
    )
    consumed = float(cursor.fetchone()[0] or 0)
    row["returnable_quantity"] = max(
        0.0,
        float(row["issued_quantity"] or 0) - float(row["returned_quantity"] or 0) - consumed,
    )
    if row["returnable_quantity"] <= 0 or quantity > row["returnable_quantity"] + 1e-9:
        raise ValueError("退库数量不能超过尚未退回的领用数量")
    quantity = min(quantity, row["returnable_quantity"])
    if row["inventory_status"] is None:
        raise ValueError("原出库批次缺失，请联系库管核对原始出库记录")
    if row["inventory_status"] != "available":
        raise ValueError("原批次正在处理，暂不能退库")
    cursor.execute("SELECT * FROM temporary_issue_obligations WHERE stock_record_id = ?", (outbound_record_id,))
    obligation = cursor.fetchone()
    if obligation and obligation["status"] not in {"pending", "settled"}:
        raise ValueError("该临时领用正在结算，请完成结算后退库")
    if obligation and obligation["status"] == "pending":
        if obligation["settled_quantity"] > 0:
            raise ValueError("该临时领用正在结算，请完成结算后退库")

    record_key = "claim-return:" + operation_key
    # 退库独立成批，避免与可转正式库的普通临时库存混合。
    return_batch_id = add_inventory_batch(
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
            "purchase_project": str(row.get("purchase_project") or "").strip(),
            "pack_quantity": 1,
            "usage_kind": str(row.get("usage_kind") or "").strip() or "研发用料",
        },
        row["form_no"],
        stock_source=STOCK_SOURCE_TEMPORARY,
        business_type=BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
        operation_key=record_key,
        operator_id=user_id,
    )
    cursor.execute(
        """
        UPDATE material_batches
        SET transfer_blocked = 1, transfer_block_reason = 'claim_return',
            version = version + 1, updated_at = ?
        WHERE id = ?
        """,
        (now_text(), return_batch_id),
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
    if obligation and obligation["status"] == "pending":
        cursor.execute("""
            UPDATE temporary_issue_obligations
            SET returned_quantity = returned_quantity + ?,
                status = CASE WHEN returned_quantity + ? >= issued_quantity THEN 'cancelled' ELSE status END,
                updated_at = ?
            WHERE id = ?
        """, (quantity, quantity, now_text(), obligation["id"]))
    return {"return_id": return_id, "quantity": quantity, "idempotent": False}


RETURN_NORMAL = "正常"
RETURN_DAMAGED = "损坏"


def _warehouse_user_id(cursor, requested):
    from warehouse_suit.workflow_service import workflow_assignees

    requested = int(requested or 0)
    if requested:
        return workflow_assignees(cursor, "claim_return", "return_inbound", [requested])[0]
    cursor.execute(
        """
        SELECT u.id
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.is_active = 1 AND r.code = 'warehouse'
        ORDER BY u.id
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError("没有可办理的库房人员")
    return int(row["id"])


def _find_returnable_lot(cursor, user_id, source_kind, source_id, batch_id):
    lots = [
        lot
        for lot in list_issued_lots(cursor, user_id)
        if lot["source_kind"] == source_kind and int(lot["source_id"] or 0) == int(source_id)
    ]
    if batch_id:
        lots = [lot for lot in lots if int(lot.get("batch_id") or 0) == int(batch_id)]
    if not lots:
        raise PermissionError("没有该领用记录的退库权限，或库存来源当前不可用")
    return lots[0]


def request_claim_return(cursor, user, payload):
    """用户提交退库。库存不动，等库房入库。"""
    from warehouse_suit.numbering import next_form_no
    from warehouse_suit.workflow_service import create_workflow_tasks, workflow_generated_title

    source_kind = str(payload.get("source_kind") or SOURCE_CLAIM_OUTBOUND).strip()
    if source_kind not in {SOURCE_CLAIM_OUTBOUND, SOURCE_BORROW_CLAIM}:
        raise ValueError("退库来源不正确")
    source_id = int(payload.get("source_id") or payload.get("outbound_record_id") or 0)
    batch_id = int(payload.get("batch_id") or 0) or None
    quantity = quantity_value(payload.get("quantity"), "退库数量", positive=True)
    condition = str(payload.get("condition_status") or RETURN_NORMAL).strip()
    if condition not in {RETURN_NORMAL, RETURN_DAMAGED}:
        raise ValueError("退库状态只能是正常或损坏")
    remark = str(payload.get("remark") or "").strip()
    if condition == RETURN_DAMAGED and not remark:
        raise ValueError("损坏必须填写备注")
    operation_key = str(payload.get("operation_key") or "").strip()
    if not operation_key or len(operation_key) > 128:
        raise ValueError("退库请求编号不能为空且不能超过128字符")
    begin_inventory_transaction(cursor.connection)
    cursor.execute("SELECT * FROM issued_return_requests WHERE operation_key = ?", (operation_key,))
    existing = cursor.fetchone()
    if existing:
        if int(existing["user_id"]) != int(user["id"]) or abs(float(existing["quantity"]) - quantity) > 1e-9:
            raise ValueError("退库请求编号已用于其他退库操作")
        return {"form_id": existing["form_id"], "quantity": existing["quantity"], "idempotent": True}
    lot = _find_returnable_lot(cursor, user["id"], source_kind, source_id, batch_id or 0)
    if source_kind == SOURCE_CLAIM_OUTBOUND and lot.get("inventory_status") is None:
        raise ValueError("原出库批次缺失，请联系库管核对原始出库记录")
    if source_kind == SOURCE_CLAIM_OUTBOUND and lot.get("inventory_status") != "available":
        raise ValueError("原批次正在处理，暂不能退库")
    if quantity > float(lot.get("returnable_quantity") or 0) + 1e-9:
        raise ValueError("退库数量不能超过尚未退回的领用数量")
    warehouse_user_id = _warehouse_user_id(cursor, payload.get("warehouse_user_id"))
    form_no = next_form_no(cursor, "TK")
    form_data = {
        "source_kind": source_kind,
        "source_id": int(lot["source_id"]),
        "batch_id": lot.get("batch_id"),
        "condition_status": condition,
        "remark": remark,
        "quantity": quantity,
    }
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, warehouse_user_id,
             data_json, created_at, updated_at)
        VALUES (?, 'claim_return', ?, 'return_inbound', 'return_inbound', ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            workflow_generated_title(user, form_no),
            int(user["id"]),
            warehouse_user_id,
            json.dumps(form_data, ensure_ascii=False),
            now_text(),
            now_text(),
        ),
    )
    form_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, spec, unit, request_quantity, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_id,
            lot.get("material_id"),
            lot.get("material_code") or "",
            lot.get("material_name") or "",
            lot.get("spec") or "",
            lot.get("unit") or "",
            quantity,
            json.dumps(form_data, ensure_ascii=False),
        ),
    )
    cursor.execute(
        """
        INSERT INTO issued_return_requests
            (source_kind, source_id, batch_id, user_id, quantity, condition_status, remark,
             status, form_id, operation_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            source_kind,
            int(lot["source_id"]),
            lot.get("batch_id"),
            int(user["id"]),
            quantity,
            condition,
            remark,
            form_id,
            operation_key,
            now_text(),
            now_text(),
        ),
    )
    create_workflow_tasks(cursor, form_id, "claim_return", "return_inbound", [warehouse_user_id])
    return {"form_id": form_id, "quantity": quantity, "idempotent": False}


def _mark_obligation_returned(cursor, outbound_record_id, quantity):
    cursor.execute("SELECT * FROM temporary_issue_obligations WHERE stock_record_id = ?", (outbound_record_id,))
    obligation = cursor.fetchone()
    if obligation and obligation["status"] == "pending":
        cursor.execute(
            """
            UPDATE temporary_issue_obligations
            SET returned_quantity = returned_quantity + ?,
                status = CASE WHEN returned_quantity + ? >= issued_quantity THEN 'cancelled' ELSE status END,
                updated_at = ?
            WHERE id = ?
            """,
            (quantity, quantity, now_text(), obligation["id"]),
        )


def _inbound_borrow_claim_return(cursor, user_id, lot, quantity, condition, remark, form_no, operation_key):
    from warehouse_suit.extended_service import create_defective_inventory

    if condition == RETURN_DAMAGED:
        create_defective_inventory(
            cursor,
            {"id": user_id},
            {
                "item_type": "material",
                "material_id": lot.get("material_id"),
                "source_batch_id": lot.get("batch_id"),
                "source_type": "claim_return",
                "source_ref_id": lot.get("source_id"),
                "item_code": lot.get("material_code") or "",
                "item_name": lot.get("material_name") or "",
                "spec": lot.get("spec") or "",
                "unit": lot.get("unit") or "",
                "quantity": quantity,
                "unit_price": lot.get("unit_price") or 0,
                "reason": remark or "领用退库损坏",
                "stock_source": lot.get("stock_source") or STOCK_SOURCE_TEMPORARY,
            },
        )
        return
    return_batch_id = add_inventory_batch(
        cursor,
        int(lot["material_id"]),
        quantity,
        float(lot.get("unit_price") or 0),
        {
            "warehouse_type": "office",
            "layer_number": 1,
            "zone_name": "A",
            "received_date": now_text()[:10],
            "remark": "领用退库 " + (lot.get("form_no") or form_no),
            "purchase_project": str(lot.get("purchase_project") or "").strip(),
            "pack_quantity": 1,
            "usage_kind": "研发用料",
        },
        lot.get("form_no") or form_no,
        stock_source=STOCK_SOURCE_TEMPORARY,
        business_type=BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
        operation_key="claim-return:" + operation_key,
        operator_id=user_id,
    )
    cursor.execute(
        """
        UPDATE material_batches
        SET transfer_blocked = 1, transfer_block_reason = 'claim_return',
            version = version + 1, updated_at = ?
        WHERE id = ?
        """,
        (now_text(), return_batch_id),
    )


def complete_claim_return(cursor, user, form_id, decision, remark=""):
    """库房入库。不能改用户选择的正常或损坏。"""
    from warehouse_suit.extended_service import create_defective_inventory
    from warehouse_suit.settings import parse_json
    from warehouse_suit.workflow_service import require_form_status, require_task_assignee

    begin_inventory_transaction(cursor.connection)
    form = require_form_status(cursor, form_id, "claim_return", "return_inbound", "completed", "rejected")
    if form["status"] in {"completed", "rejected"}:
        return {"form_id": form_id, "idempotent": True}
    task = require_task_assignee(cursor, user, form_id, "return_inbound")
    cursor.execute("SELECT * FROM issued_return_requests WHERE form_id = ?", (form_id,))
    request_row = cursor.fetchone()
    if not request_row:
        raise ValueError("退库申请不存在")
    request_row = dict(request_row)
    decision = str(decision or "同意").strip()
    remark = str(remark or "").strip()
    if decision in {"不同意", "拒绝"}:
        if not remark:
            raise ValueError("拒绝时必须填写审批意见")
        cursor.execute(
            """
            UPDATE issued_return_requests SET status = 'rejected', updated_at = ? WHERE id = ?
            """,
            (now_text(), request_row["id"]),
        )
        cursor.execute(
            """
            UPDATE workflow_tasks
            SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                decision,
                user.get("display_name") or "",
                now_text()[:10],
                json.dumps({"remark": remark}, ensure_ascii=False),
                now_text(),
                task["id"],
            ),
        )
        cursor.execute(
            """
            UPDATE workflow_forms
            SET status = 'rejected', current_step = 'completed', warehouse_user_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (user["id"], now_text(), form_id),
        )
        return {"form_id": form_id, "idempotent": False}
    if decision != "同意":
        raise ValueError("入库结论不正确")
    condition = request_row["condition_status"]
    quantity = float(request_row["quantity"])
    source_kind = request_row["source_kind"]
    if source_kind == SOURCE_CLAIM_OUTBOUND and condition == RETURN_NORMAL:
        return_claim(
            cursor,
            int(request_row["user_id"]),
            int(request_row["source_id"]),
            quantity,
            request_row["operation_key"],
        )
    elif source_kind == SOURCE_CLAIM_OUTBOUND and condition == RETURN_DAMAGED:
        lot = _find_returnable_lot(
            cursor,
            int(request_row["user_id"]),
            source_kind,
            int(request_row["source_id"]),
            int(request_row["batch_id"] or 0),
        )
        create_defective_inventory(
            cursor,
            user,
            {
                "item_type": "material",
                "material_id": lot.get("material_id"),
                "source_batch_id": lot.get("batch_id"),
                "source_type": "claim_return",
                "source_ref_id": request_row["source_id"],
                "item_code": lot.get("material_code") or "",
                "item_name": lot.get("material_name") or "",
                "spec": lot.get("spec") or "",
                "unit": lot.get("unit") or "",
                "quantity": quantity,
                "unit_price": lot.get("unit_price") or 0,
                "reason": request_row["remark"] or "领用退库损坏",
                "stock_source": lot.get("stock_source") or STOCK_SOURCE_TEMPORARY,
            },
        )
        _mark_obligation_returned(cursor, int(request_row["source_id"]), quantity)
    else:
        form_data = parse_json(form["data_json"], {})
        lot = _find_returnable_lot(
            cursor,
            int(request_row["user_id"]),
            source_kind,
            int(request_row["source_id"]),
            int(request_row["batch_id"] or 0),
        )
        _inbound_borrow_claim_return(
            cursor,
            int(request_row["user_id"]),
            lot,
            quantity,
            condition,
            request_row["remark"] or form_data.get("remark") or "",
            form["form_no"],
            request_row["operation_key"],
        )
    cursor.execute(
        "UPDATE issued_return_requests SET status = 'completed', updated_at = ? WHERE id = ?",
        (now_text(), request_row["id"]),
    )
    cursor.execute(
        """
        UPDATE workflow_tasks
        SET status = 'completed', decision = '已入库', signature = ?, signed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (user.get("display_name") or "", now_text()[:10], now_text(), task["id"]),
    )
    cursor.execute(
        """
        UPDATE workflow_forms
        SET status = 'completed', current_step = 'completed', warehouse_user_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (user["id"], now_text(), form_id),
    )
    return {"form_id": form_id, "idempotent": False}

