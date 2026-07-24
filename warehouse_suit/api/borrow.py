# -*- coding: utf-8 -*-
"""Borrow, return, and transfer workflow route registration."""

import json

from flask import jsonify, request

from warehouse_suit.borrow_service import allocate_borrow_items, insert_borrow_allocations
from warehouse_suit.extended_service import create_defective_inventory
from warehouse_suit.db import now_text, row_to_dict, today_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, validate_stock_source
from warehouse_suit.inventory_service import (
    begin_inventory_transaction,
    borrow_out_item,
    borrowable_item_snapshot,
    borrowable_items,
    return_borrow_item,
    save_borrow_change,
    update_borrow_return_balance,
)
from warehouse_suit.notifications import create_notification
from warehouse_suit.numbering import next_form_no
from warehouse_suit.settings import parse_json, temporary_inventory_enabled
from warehouse_suit.validation import quantity_value, validation_rule_enabled
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    require_form_status,
    require_permission,
    require_task_assignee,
    resolve_department_leader,
    serialize_form,
    workflow_assignees,
    workflow_generated_title,
)


def register_borrow_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider
    @app.get("/api/borrow/items")
    def list_borrow_items():
        keyword = request.args.get("keyword", "").strip()
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "start_borrow")
        rows = borrowable_items(
            cursor,
            keyword,
            include_temporary=temporary_inventory_enabled(cursor),
        )
        conn.close()
        return jsonify({"items": rows})


    @app.post("/api/borrows")
    def create_borrow():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_borrow")
            items = data.get("items") or []
            if not items:
                raise ValueError("借用申请至少需要一项物料")
            leader_id = resolve_department_leader(
                cursor,
                user,
                "borrow",
                "leader_borrow",
                int(data.get("leader_id") or 0),
            )
            begin_inventory_transaction(conn)
            allocations = allocate_borrow_items(
                cursor,
                items,
                user,
                include_temporary=temporary_inventory_enabled(cursor),
            )
            form_no = next_form_no(cursor, "JY")
            title = workflow_generated_title(user, form_no)
            cursor.execute(
                """
                INSERT INTO workflow_forms
                    (form_no, form_type, title, status, current_step, applicant_id, leader_id,
                     data_json, created_at, updated_at)
                VALUES (?, 'borrow', ?, 'leader_borrow', 'leader_borrow', ?, ?, ?, ?, ?)
                """,
                (
                    form_no,
                    title,
                    user["id"],
                    leader_id,
                    json.dumps(
                        {
                            "department": user.get("department") or "",
                            "expected_return_date": data.get("expected_return_date") or "",
                        },
                        ensure_ascii=False,
                    ),
                    now_text(),
                    now_text(),
                ),
            )
            form_id = cursor.lastrowid
            insert_borrow_allocations(cursor, form_id, allocations)
            create_workflow_tasks(cursor, form_id, "borrow", "leader_borrow", [leader_id])
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/borrows/<int:form_id>/leader")
    def leader_borrow(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            require_form_status(cursor, form_id, "borrow", "leader_borrow")
            task = require_task_assignee(cursor, user, form_id, "leader_borrow")
            decision = data.get("decision") or "同意"
            if decision != "同意" and not str(data.get("remark") or "").strip():
                raise ValueError("不同意时必须填写审批意见")
            cursor.execute(
                "UPDATE workflow_tasks SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ? WHERE id = ?",
                (
                    decision,
                    data.get("signature") or user["display_name"],
                    data.get("signed_at") or today_text(),
                    json.dumps({"remark": data.get("remark") or ""}, ensure_ascii=False),
                    now_text(),
                    task["id"],
                ),
            )
            status = "borrow_outbound" if decision == "同意" else "applicant_revision"
            cursor.execute(
                "UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ?",
                (status, status, now_text(), form_id),
            )
            if decision == "同意":
                outbound_user_id = workflow_assignees(
                    cursor,
                    "borrow",
                    "borrow_outbound",
                    [int(data.get("warehouse_user_id") or user["id"])],
                )[0]
                create_workflow_tasks(
                    cursor,
                    form_id,
                    "borrow",
                    "borrow_outbound",
                    [outbound_user_id],
                )
            else:
                cursor.execute(
                    "SELECT applicant_id FROM workflow_forms WHERE id = ?",
                    (form_id,),
                )
                applicant_id = cursor.fetchone()["applicant_id"]
                remark = str(data.get("remark") or "").strip()
                cursor.execute(
                    """
                    INSERT INTO workflow_tasks
                        (form_id, step_code, assignee_id, data_json, created_at, updated_at)
                    VALUES (?, 'applicant_revision', ?, ?, ?, ?)
                    """,
                    (
                        form_id,
                        applicant_id,
                        json.dumps(
                            {"remark": remark, "return_reason": remark},
                            ensure_ascii=False,
                        ),
                        now_text(),
                        now_text(),
                    ),
                )
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/borrows/<int:form_id>/outbound")
    def outbound_borrow(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            begin_inventory_transaction(conn)
            form = dict(
                require_form_status(
                    cursor,
                    form_id,
                    "borrow",
                    "borrow_outbound",
                    "completed",
                )
            )
            if form["status"] == "completed":
                form_data = serialize_form(cursor, form_id)
                conn.commit()
                conn.close()
                return jsonify(
                    {"success": True, "form": form_data, "idempotent": True}
                )

            task = require_task_assignee(cursor, user, form_id, "borrow_outbound")
            items_by_id = {
                int(item.get("id") or 0): item for item in data.get("items") or []
            }
            cursor.execute(
                "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id",
                (form_id,),
            )
            for row in cursor.fetchall():
                wf_item = dict(row)
                patch = items_by_id.get(wf_item["id"], {})
                qty = quantity_value(
                    patch.get("outbound_quantity")
                    if patch.get("outbound_quantity") is not None
                    else wf_item.get("request_quantity"),
                    "借用出库数量",
                    positive=True,
                )
                if (
                    validation_rule_enabled("workflow_bounds")
                    and qty > float(wf_item.get("request_quantity") or 0) + 1e-9
                ):
                    raise ValueError(
                        f"{wf_item['material_name']} 借用出库数量不能大于申请数量"
                    )
                item_data = parse_json(wf_item.get("data_json"), {})
                item_type = item_data.get("borrow_item_type") or (
                    "material" if wf_item.get("material_id") else ""
                )
                item_ref_id = int(
                    item_data.get("borrow_ref_id")
                    or wf_item.get("material_id")
                    or 0
                )
                stock_source = validate_stock_source(
                    wf_item.get("stock_source") or STOCK_SOURCE_FORMAL
                )
                snap = borrowable_item_snapshot(
                    cursor,
                    item_type,
                    item_ref_id,
                    stock_source=stock_source,
                )
                result = borrow_out_item(
                    cursor,
                    snap,
                    qty,
                    form["form_no"],
                    data.get("outbound_date") or today_text(),
                    patch.get("batches") or patch.get("batch_allocations"),
                    stock_source=stock_source,
                    operation_key=f"borrow:{form_id}:{wf_item['id']}:outbound",
                    operator_id=user["id"],
                    workflow_item_id=wf_item["id"],
                )
                item_data.update(
                    {
                        "borrow_outbound": result,
                        "outbound_stock_source": stock_source,
                    }
                )
                cursor.execute(
                    "UPDATE workflow_items SET outbound_quantity = ?, data_json = ? WHERE id = ?",
                    (qty, json.dumps(item_data, ensure_ascii=False), wf_item["id"]),
                )
                cursor.execute(
                    """
                    INSERT INTO borrow_records
                        (borrow_no, item_type, item_ref_id, material_id, workflow_item_id, stock_source,
                         item_code, item_name, brand_model, spec, unit, quantity, returned_quantity, status,
                         borrower_id, borrow_form_id, outbound_date, data_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'borrowed', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        form["form_no"],
                        item_type,
                        item_ref_id,
                        snap.get("material_id"),
                        wf_item["id"],
                        stock_source,
                        snap.get("item_code") or "",
                        snap.get("item_name") or "",
                        snap.get("brand_model") or "",
                        snap.get("spec") or "",
                        snap.get("unit") or "",
                        qty,
                        form["applicant_id"],
                        form_id,
                        data.get("outbound_date") or today_text(),
                        json.dumps(result, ensure_ascii=False),
                        now_text(),
                        now_text(),
                    ),
                )
            cursor.execute(
                "UPDATE workflow_tasks SET status = 'completed', decision = '已出库', signature = ?, signed_at = ?, updated_at = ? WHERE id = ?",
                (
                    data.get("signature") or user["display_name"],
                    data.get("outbound_date") or today_text(),
                    now_text(),
                    task["id"],
                ),
            )
            cursor.execute(
                "UPDATE workflow_forms SET status = 'completed', current_step = 'completed', warehouse_user_id = ?, updated_at = ? WHERE id = ?",
                (user["id"], now_text(), form_id),
            )
            conn.commit()
            form_data = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form_data, "idempotent": False})


    @app.get("/api/borrows/mine")
    def my_borrows():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        if not user:
            conn.close()
            raise PermissionError("请先登录")
        # Items where user is the borrower (excludes returned)
        cursor.execute(
            """
            SELECT br.*, f.data_json AS borrow_form_data_json
            FROM borrow_records br
            LEFT JOIN workflow_forms f ON f.id = br.borrow_form_id
            WHERE br.borrower_id = ? AND br.status != 'returned'
            ORDER BY id DESC
            """,
            (user["id"],),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        seen_ids = set(int(r["id"]) for r in rows)

        # Also include items where user is the transfer receiver (status='transferring')
        cursor.execute(
            """
            SELECT *
            FROM borrow_records
            WHERE status = 'transferring' AND borrower_id != ?
            ORDER BY id DESC
            """,
            (user["id"],),
        )
        for row in cursor.fetchall():
            record = dict(row)
            data = parse_json(record.get("data_json", "{}"), {})
            if int(data.get("transfer_receiver_id") or 0) == int(user["id"]):
                if int(record["id"]) not in seen_ids:
                    rows.append(record)
                    seen_ids.add(int(record["id"]))

        for row in rows:
            row["data"] = parse_json(row.pop("data_json", "{}"), {})
            borrow_form_data = parse_json(row.pop("borrow_form_data_json", "{}"), {})
            row["expected_return_date"] = str(borrow_form_data.get("expected_return_date") or "")
            row["remaining_quantity"] = max(
                0,
                float(row.get("quantity") or 0)
                - float(row.get("returned_quantity") or 0),
            )
            row["is_overdue"] = bool(
                row["expected_return_date"]
                and row["expected_return_date"] < today_text()
                and row["remaining_quantity"] > 0
                and row.get("status") != "returned"
            )
            row["stock_source"] = validate_stock_source(
                row.get("stock_source") or STOCK_SOURCE_FORMAL
            )
            row["stock_source_label"] = (
                "临时库" if row["stock_source"] == "temporary" else "正式库"
            )
        conn.close()
        return jsonify({"items": rows})


    @app.post("/api/borrow-returns")
    def create_borrow_return():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            borrow_record_id = int(data.get("borrow_record_id") or 0)
            cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (borrow_record_id,))
            record = row_to_dict(cursor.fetchone())
            if not record:
                raise ValueError("借用记录不存在")
            if "admin" not in user.get("role_codes", []) and int(record.get("borrower_id") or 0) != int(user["id"]):
                raise PermissionError("只能归还自己的借用物料")
            remaining = float(record["quantity"] or 0) - float(record["returned_quantity"] or 0)
            qty = quantity_value(data.get("return_quantity") if data.get("return_quantity") is not None else remaining, "归还数量", positive=True)
            if qty <= 0 or (validation_rule_enabled("workflow_bounds") and qty > remaining + 1e-9):
                raise ValueError("归还数量不正确")
            status = str(data.get("status") or "完好").strip()
            if status not in ("完好", "报废", "异常"):
                raise ValueError("归还状态值不正确，请选择 完好/报废/异常")
            remarks = str(data.get("remarks") or "").strip()
            if status in ("报废", "异常") and not remarks:
                raise ValueError("报废/异常需填写备注")
            item_type = str(record.get("item_type") or "").strip()
            has_changes = str(data.get("has_changes") or "否").strip()
            change_type = str(data.get("change_type") or "").strip()
            change_detail = str(data.get("change_detail") or "").strip()
            version_after = str(data.get("version_after") or "").strip()
            normal_use = str(data.get("normal_use") or "").strip()
            if item_type in ("semifinished", "finished") and has_changes == "是":
                if change_type not in ("软件", "硬件"):
                    raise ValueError("变更类型需选择 软件/硬件")
                if normal_use not in ("是", "否"):
                    raise ValueError("能否正常使用需选择 是/否")
                if change_type == "软件" and not version_after:
                    raise ValueError("软件变更需填写版本号")
            form_no = next_form_no(cursor, "GH")
            warehouse_user_id = workflow_assignees(cursor, "borrow_return", "return_inbound", [int(data.get("warehouse_user_id") or user["id"])])[0]
            title = workflow_generated_title(user, form_no)
            stock_source = validate_stock_source(
                record.get("stock_source") or STOCK_SOURCE_FORMAL
            )
            form_data = {
                "borrow_record_id": borrow_record_id,
                "stock_source": stock_source,
                "status": status,
                "remarks": remarks,
                "has_changes": has_changes,
                "change_type": change_type,
                "change_detail": change_detail,
                "version_after": version_after,
                "normal_use": normal_use,
            }
            cursor.execute(
                """
                INSERT INTO workflow_forms
                    (form_no, form_type, title, status, current_step, applicant_id, warehouse_user_id,
                     data_json, created_at, updated_at)
                VALUES (?, 'borrow_return', ?, 'return_inbound', 'return_inbound', ?, ?, ?, ?, ?)
                """,
                (
                    form_no,
                    title,
                    user["id"],
                    warehouse_user_id,
                    json.dumps(form_data, ensure_ascii=False),
                    now_text(),
                    now_text(),
                ),
            )
            form_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO workflow_items
                    (form_id, material_id, material_code, material_name, brand_model, spec, unit,
                     request_quantity, data_json, stock_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    form_id,
                    record.get("material_id"),
                    record.get("item_code") or "",
                    record.get("item_name") or "",
                    record.get("brand_model") or "",
                    record.get("spec") or "",
                    record.get("unit") or "",
                    qty,
                    json.dumps(
                        {
                            "borrow_record_id": borrow_record_id,
                            "borrow_item_type": record.get("item_type"),
                            "borrow_ref_id": record.get("item_ref_id"),
                            "stock_source": stock_source,
                        },
                        ensure_ascii=False,
                    ),
                    stock_source,
                ),
            )
            create_workflow_tasks(cursor, form_id, "borrow_return", "return_inbound", [warehouse_user_id])
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/borrow-returns/<int:form_id>/inbound")
    def inbound_borrow_return(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            begin_inventory_transaction(conn)
            form = require_form_status(
                cursor,
                form_id,
                "borrow_return",
                "return_inbound",
                "completed",
            )
            if form["status"] == "completed":
                form_result = serialize_form(cursor, form_id)
                conn.commit()
                conn.close()
                return jsonify({"success": True, "form": form_result, "idempotent": True})
            task = require_task_assignee(cursor, user, form_id, "return_inbound")
            form_data = parse_json(form["data_json"], {})

            cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id LIMIT 1", (form_id,))
            item = row_to_dict(cursor.fetchone())
            if not item:
                raise ValueError("归还明细不存在")
            item_data = parse_json(item.get("data_json"), {})
            borrow_record_id = int(item_data.get("borrow_record_id") or form_data.get("borrow_record_id") or 0)
            cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (borrow_record_id,))
            record = row_to_dict(cursor.fetchone())
            if not record:
                raise ValueError("借用记录不存在")
            stock_source = validate_stock_source(
                record.get("stock_source") or STOCK_SOURCE_FORMAL
            )

            remaining = float(record["quantity"] or 0) - float(record["returned_quantity"] or 0)
            qty = quantity_value(data.get("return_quantity") if data.get("return_quantity") is not None else item.get("request_quantity"), "归还入库数量", positive=True)
            if validation_rule_enabled("workflow_bounds") and qty > remaining + 1e-9:
                raise ValueError("归还入库数量不能大于未归还数量")
            decision = str(data.get("decision") or "同意").strip()
            return_status = str(form_data.get("status") or "完好").strip()
            has_changes = str(form_data.get("has_changes") or "否").strip()
            change_type = str(form_data.get("change_type") or "").strip()
            change_detail = str(form_data.get("change_detail") or "").strip()
            version_after = str(form_data.get("version_after") or "").strip()
            normal_use = str(form_data.get("normal_use") or "").strip()
            record_item_type = str(record.get("item_type") or "").strip()

            if decision != "同意" and not str(data.get("remark") or "").strip():
                raise ValueError("拒绝时必须填写审批意见")

            if decision in ("不同意", "拒绝"):
                cursor.execute(
                    "UPDATE workflow_tasks SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ? WHERE id = ?",
                    (decision,
                     data.get("signature") or user["display_name"],
                     data.get("inbound_date") or today_text(),
                     json.dumps({"remark": data.get("remark") or ""}, ensure_ascii=False),
                     now_text(),
                     task["id"]),
                )
                cursor.execute(
                    "UPDATE workflow_forms SET status = 'rejected', current_step = 'completed', warehouse_user_id = ?, updated_at = ? WHERE id = ?",
                    (user["id"], now_text(), form_id),
                )
                conn.commit()
                form_result = serialize_form(cursor, form_id)
                conn.close()
                return jsonify({"success": True, "form": form_result})

            if has_changes == "是" and change_type:
                save_borrow_change(cursor, borrow_record_id, change_type, change_detail, version_after, normal_use)

            item_serial = str(record.get("item_code") or "").strip()

            if return_status == "完好" and item_serial:
                scrapped_row = None
                defective_row = None
                if record_item_type in ("semifinished", "finished"):
                    cursor.execute(
                        "SELECT id FROM scrapped_finished_goods WHERE serial_no = ? UNION ALL SELECT id FROM scrapped_semifinished_goods WHERE serial_no = ? LIMIT 1",
                        (item_serial, item_serial),
                    )
                    scrapped_row = cursor.fetchone()
                    if not scrapped_row:
                        cursor.execute(
                            "SELECT id FROM defective_finished_goods WHERE serial_no = ? UNION ALL SELECT id FROM defective_semifinished_goods WHERE serial_no = ? LIMIT 1",
                            (item_serial, item_serial),
                        )
                        defective_row = cursor.fetchone()
                if scrapped_row or defective_row:
                    if record_item_type == "semifinished":
                        cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (int(record.get("item_ref_id") or 0),))
                    else:
                        cursor.execute("SELECT * FROM finished_good_inventory WHERE id = ?", (int(record.get("item_ref_id") or 0),))
                    inv_row = row_to_dict(cursor.fetchone())
                    if inv_row:
                        if scrapped_row:
                            if record_item_type == "semifinished":
                                cursor.execute("DELETE FROM scrapped_semifinished_goods WHERE id = ?", (scrapped_row[0],))
                            else:
                                cursor.execute("DELETE FROM scrapped_finished_goods WHERE id = ?", (scrapped_row[0],))
                        if defective_row:
                            if record_item_type == "semifinished":
                                cursor.execute("DELETE FROM defective_semifinished_goods WHERE id = ?", (defective_row[0],))
                            else:
                                cursor.execute("DELETE FROM defective_finished_goods WHERE id = ?", (defective_row[0],))
                        if record_item_type == "semifinished":
                            cursor.execute(
                                "UPDATE semifinished_inventory SET quantity = COALESCE(quantity, 0) + ?, borrowed_quantity = MAX(0, COALESCE(borrowed_quantity, 0) - ?), updated_at = ? WHERE id = ?",
                                (qty, qty, now_text(), record["item_ref_id"]),
                            )
                        else:
                            cursor.execute(
                                "UPDATE finished_good_inventory SET quantity = COALESCE(quantity, 0) + ?, borrowed_quantity = MAX(0, COALESCE(borrowed_quantity, 0) - ?), updated_at = ? WHERE id = ?",
                                (qty, qty, now_text(), record["item_ref_id"]),
                            )
                        update_borrow_return_balance(
                            cursor,
                            record["id"],
                            qty,
                            return_date=data.get("inbound_date") or today_text(),
                            return_form_id=form_id,
                        )
                    else:
                        location = {
                            "warehouse_type": data.get("warehouse_type") or "office",
                            "shelf_id": data.get("shelf_id"),
                            "layer_number": data.get("layer_number") or 1,
                            "zone_name": data.get("zone_name") or "A",
                            "received_date": data.get("inbound_date") or today_text(),
                            "remark": f"借用单 {record.get('borrow_no') or ''} 归还入库",
                            "return_form_id": form_id,
                        }
                        return_borrow_item(
                    cursor,
                    record,
                    qty,
                    location,
                    form["form_no"],
                    operation_key=f"borrow_return:{form_id}:{record['id']}:{item['id']}",
                    operator_id=user["id"],
                )
                else:
                    location = {
                        "warehouse_type": data.get("warehouse_type") or "office",
                        "shelf_id": data.get("shelf_id"),
                        "layer_number": data.get("layer_number") or 1,
                        "zone_name": data.get("zone_name") or "A",
                        "received_date": data.get("inbound_date") or today_text(),
                        "remark": f"借用单 {record.get('borrow_no') or ''} 归还入库",
                        "return_form_id": form_id,
                    }
                    return_borrow_item(
                    cursor,
                    record,
                    qty,
                    location,
                    form["form_no"],
                    operation_key=f"borrow_return:{form_id}:{record['id']}:{item['id']}",
                    operator_id=user["id"],
                )
            elif return_status != "\u5b8c\u597d":
                if record_item_type in ("semifinished", "finished"):
                    table = "semifinished_inventory" if record_item_type == "semifinished" else "finished_good_inventory"
                    cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (int(record.get("item_ref_id") or 0),))
                    inv_row = row_to_dict(cursor.fetchone())
                    if not inv_row:
                        raise ValueError("return inventory record not found")
                    cursor.execute(
                        f"UPDATE {table} SET quantity = MAX(0, COALESCE(quantity, 0) - ?), borrowed_quantity = MAX(0, COALESCE(borrowed_quantity, 0) - ?), updated_at = ? WHERE id = ?",
                        (qty, qty, now_text(), record["item_ref_id"]),
                    )
                    create_defective_inventory(cursor, user, {
                        "item_type": record_item_type,
                        "original_inventory_id": record.get("item_ref_id"),
                        "source_type": "borrow_return",
                        "source_ref_id": borrow_record_id,
                        "item_code": record.get("item_code") or "",
                        "item_name": record.get("item_name") or "",
                        "brand_model": record.get("brand_model") or "",
                        "spec": record.get("spec") or "",
                        "unit": record.get("unit") or "",
                        "quantity": qty,
                        "unit_price": inv_row.get("cost_price") or 0,
                        "reason": form_data.get("remarks") or data.get("remark") or return_status,
                    })
                else:
                    record_data = parse_json(record.get("data_json"), {}) or {}
                    create_defective_inventory(cursor, user, {
                        "item_type": "material",
                        "material_id": record.get("material_id") or record.get("item_ref_id"),
                        "source_type": "borrow_return",
                        "source_ref_id": borrow_record_id,
                        "item_code": record.get("item_code") or "",
                        "item_name": record.get("item_name") or "",
                        "brand_model": record.get("brand_model") or "",
                        "spec": record.get("spec") or "",
                        "unit": record.get("unit") or "",
                        "quantity": qty,
                        "unit_price": record_data.get("unit_price") or 0,
                        "reason": form_data.get("remarks") or data.get("remark") or return_status,
                    })
                update_borrow_return_balance(
                    cursor, record["id"], qty,
                    return_date=data.get("inbound_date") or today_text(),
                    return_form_id=form_id,
                )
            else:
                location = {
                    "warehouse_type": data.get("warehouse_type") or "office",
                    "shelf_id": data.get("shelf_id"),
                    "layer_number": data.get("layer_number") or 1,
                    "zone_name": data.get("zone_name") or "A",
                    "received_date": data.get("inbound_date") or today_text(),
                    "remark": f"borrow return {record.get('borrow_no') or ''}",
                    "return_form_id": form_id,
                }
                return_borrow_item(
                    cursor, record, qty, location, form["form_no"],
                    operation_key=f"borrow_return:{form_id}:{record['id']}:{item['id']}",
                    operator_id=user["id"],
                )

            cursor.execute(
                "UPDATE workflow_items SET approved_quantity = ?, data_json = ? WHERE id = ?",
                (qty, json.dumps({**item_data, "returned": True, "decision": decision, "status": return_status, "stock_source": stock_source}, ensure_ascii=False), item["id"]),
            )
            task_decision = "已入库" if decision == "同意" else decision
            cursor.execute(
                "UPDATE workflow_tasks SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ? WHERE id = ?",
                (task_decision,
                 data.get("signature") or user["display_name"],
                 data.get("inbound_date") or today_text(),
                 json.dumps({"remark": data.get("remark") or "", "status": return_status}, ensure_ascii=False),
                 now_text(),
                 task["id"]),
            )
            form_status = "completed" if decision == "同意" else "rejected"
            cursor.execute(
                "UPDATE workflow_forms SET status = ?, current_step = 'completed', warehouse_user_id = ?, updated_at = ? WHERE id = ?",
                (form_status, user["id"], now_text(), form_id),
            )
            conn.commit()
            form_result = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form_result, "idempotent": False})


    @app.post("/api/borrows/<int:record_id>/transfer")
    def transfer_borrow(record_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM borrow_records WHERE id = ? AND borrower_id = ?", (record_id, user["id"]))
            record = row_to_dict(cursor.fetchone())
            if not record:
                raise ValueError("借用记录不存在")
            if record.get("status") != "borrowed":
                raise ValueError("只能转借状态为借用的记录")
            receiver_id = int(data.get("receiver_id") or 0)
            if receiver_id == int(user["id"]):
                raise ValueError("不能转借给自己")
            cursor.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (receiver_id,))
            receiver = row_to_dict(cursor.fetchone())
            if not receiver:
                raise ValueError("接收用户不存在")
            existing_data = parse_json(record.get("data_json"), {})
            existing_data["transfer_receiver_id"] = receiver_id
            existing_data["transfer_initiated_at"] = now_text()
            cursor.execute(
                "UPDATE borrow_records SET status = ?, data_json = ?, updated_at = ? WHERE id = ?",
                ("transferring", json.dumps(existing_data, ensure_ascii=False), now_text(), record_id),
            )
            create_notification(
                cursor,
                receiver_id,
                "转借请求",
                f'{user.get("display_name") or user.get("username") or ""} 想将 {record.get("item_name") or ""} 转借给您',
                {"borrow_record_id": record_id},
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "message": "转借请求已发送"})


    @app.post("/api/transfers/<int:record_id>/accept")
    def accept_transfer(record_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
            record = row_to_dict(cursor.fetchone())
            if not record:
                raise ValueError("借用记录不存在")
            if record.get("status") != "transferring":
                raise ValueError("该记录不在转借中")
            record_data = parse_json(record.get("data_json"), {})
            if int(record_data.get("transfer_receiver_id") or 0) != int(user["id"]):
                raise PermissionError("您不是该转借请求的接收人")
            new_data = {k: v for k, v in record_data.items() if k not in ("transfer_receiver_id", "transfer_initiated_at")}
            cursor.execute(
                "UPDATE borrow_records SET borrower_id = ?, status = ?, data_json = ?, updated_at = ? WHERE id = ?",
                (user["id"], "borrowed", json.dumps(new_data, ensure_ascii=False), now_text(), record_id),
            )
            original_borrower_id = int(record.get("borrower_id") or 0)
            if original_borrower_id:
                create_notification(
                    cursor,
                    original_borrower_id,
                    "转借已接受",
                    f'{user.get("display_name") or user.get("username") or ""} 已接受 {record.get("item_name") or ""} 的转借',
                    {"borrow_record_id": record_id},
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.post("/api/transfers/<int:record_id>/reject")
    def reject_transfer(record_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
            record = row_to_dict(cursor.fetchone())
            if not record:
                raise ValueError("借用记录不存在")
            if record.get("status") != "transferring":
                raise ValueError("该记录不在转借中")
            record_data = parse_json(record.get("data_json"), {})
            if int(record_data.get("transfer_receiver_id") or 0) != int(user["id"]):
                raise PermissionError("您不是该转借请求的接收人")
            new_data = {k: v for k, v in record_data.items() if k not in ("transfer_receiver_id", "transfer_initiated_at")}
            cursor.execute(
                "UPDATE borrow_records SET status = ?, data_json = ?, updated_at = ? WHERE id = ?",
                ("borrowed", json.dumps(new_data, ensure_ascii=False), now_text(), record_id),
            )
            original_borrower_id = int(record.get("borrower_id") or 0)
            if original_borrower_id:
                create_notification(
                    cursor,
                    original_borrower_id,
                    "转借已拒绝",
                    f'{user.get("display_name") or user.get("username") or ""} 拒绝了 {record.get("item_name") or ""} 的转借',
                    {"borrow_record_id": record_id},
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})
