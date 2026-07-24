# -*- coding: utf-8 -*-
"""Stock, stocktake, statistics, and export route registration."""

from flask import jsonify, request

from warehouse_suit.db import next_stocktake_due_date, now_text, row_to_dict, today_text
from warehouse_suit.exporting import material_cards_html
from warehouse_suit.inventory_constants import INVENTORY_STATUS_AVAILABLE, STOCK_SOURCE_FORMAL
from warehouse_suit.inventory_service import add_inventory_batch, consume_inventory_fifo
from warehouse_suit.material_repository import fetch_material, material_batch_rows, material_query
from warehouse_suit.numbering import next_stocktake_no
from warehouse_suit.recycle import recycle_stocktake
from warehouse_suit.settings import get_setting, set_setting, workflow_settings
from warehouse_suit.validation import positive_int_value, price_value, quantity_value
from warehouse_suit.workflow_service import require_any_permission, require_permission


def register_stock_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider
    @app.get("/api/materials/<int:material_id>/batches")
    def get_material_batches(material_id):
        conn = get_db()
        cursor = conn.cursor()
        rows = material_batch_rows(
            cursor,
            material_id,
            stock_source=STOCK_SOURCE_FORMAL,
        )
        conn.close()
        return jsonify(rows)


    @app.get("/api/statistics/<kind>")
    def statistics(kind):
        if kind not in {"inbound", "outbound", "borrow", "return"}:
            return jsonify({"error": "统计类型错误"}), 400
        warehouse_type = request.args.get("warehouse_type", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_stats")
        if kind == "borrow":
            date_expr = "COALESCE(NULLIF(br.outbound_date, ''), substr(br.created_at, 1, 10))"
            where = ["br.stock_source = ?"]
            params = [STOCK_SOURCE_FORMAL]
            if date_from:
                where.append(f"{date_expr} >= ?")
                params.append(date_from)
            if date_to:
                where.append(f"{date_expr} <= ?")
                params.append(date_to)
            cursor.execute(
                f"""
                SELECT {date_expr} AS operation_date, br.borrow_no AS form_no,
                       br.item_code AS material_code, br.item_name AS material_name,
                       br.brand_model, br.spec, br.unit, '' AS batch_no,
                       br.quantity AS quantity, 0 AS amount, '' AS warehouse_type
                FROM borrow_records br
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY operation_date DESC, br.id DESC
                """,
                params,
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return jsonify({"rows": rows, "total_quantity": sum(float(row.get("quantity") or 0) for row in rows), "total_amount": 0})

        if kind == "return":
            date_expr = """
                COALESCE(
                    NULLIF((SELECT MAX(t.signed_at) FROM workflow_tasks t
                            WHERE t.form_id = f.id AND t.step_code = 'return_inbound' AND t.status = 'completed'), ''),
                    substr(f.updated_at, 1, 10)
                )
            """
            where = ["f.form_type = 'borrow_return'", "f.status = 'completed'", "wi.stock_source = ?"]
            params = [STOCK_SOURCE_FORMAL]
            if date_from:
                where.append(f"{date_expr} >= ?")
                params.append(date_from)
            if date_to:
                where.append(f"{date_expr} <= ?")
                params.append(date_to)
            cursor.execute(
                f"""
                SELECT {date_expr} AS operation_date, f.form_no,
                       wi.material_code, wi.material_name, wi.brand_model, wi.spec, wi.unit,
                       '' AS batch_no,
                       COALESCE(NULLIF(wi.approved_quantity, 0), wi.request_quantity) AS quantity,
                       0 AS amount, '' AS warehouse_type
                FROM workflow_forms f
                JOIN workflow_items wi ON wi.form_id = f.id
                WHERE {' AND '.join(where)}
                ORDER BY operation_date DESC, f.id DESC, wi.id DESC
                """,
                params,
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return jsonify({"rows": rows, "total_quantity": sum(float(row.get("quantity") or 0) for row in rows), "total_amount": 0})

        operation_type = "in" if kind == "inbound" else "out"
        where = ["sr.operation_type = ?", "sr.stock_source = ?"]
        params = [operation_type, STOCK_SOURCE_FORMAL]
        if warehouse_type:
            where.append("(s.warehouse_type = ? OR b.warehouse_type = ?)")
            params.extend([warehouse_type, warehouse_type])
        if date_from:
            where.append("sr.operation_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("sr.operation_date <= ?")
            params.append(date_to)
        cursor.execute(
            f"""
            SELECT sr.*, m.material_code, m.name AS material_name, m.brand_model, m.spec, m.unit,
                   b.batch_no, s.name AS shelf_name, COALESCE(s.warehouse_type, b.warehouse_type) AS warehouse_type
            FROM stock_records sr
            JOIN materials m ON m.id = sr.material_id
            LEFT JOIN material_batches b ON b.id = sr.batch_id
            LEFT JOIN shelves s ON s.id = b.shelf_id
            WHERE {' AND '.join(where)}
            ORDER BY sr.operation_date DESC, sr.id DESC
            """,
            params,
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"rows": rows, "total_quantity": sum(float(row["quantity"]) for row in rows), "total_amount": sum(float(row["amount"] or 0) for row in rows)})


    @app.post("/api/stocktakes")
    def create_stocktake():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_stocktake")
            form_no = next_stocktake_no(cursor)
            warehouse_type = data.get("warehouse_type") or ""
            date_to = data.get("date_to") or today_text()
            date_from = data.get("date_from") or get_setting(cursor, "last_stocktake_date", today_text())
            show_zero = 1 if data.get("show_zero") else 0
            supervisor_id = int(data.get("supervisor_id") or 0)
            if not supervisor_id:
                cursor.execute(
                    """
                    SELECT u.id
                    FROM users u
                    JOIN user_roles ur ON ur.user_id = u.id
                    JOIN roles r ON r.id = ur.role_id
                    WHERE r.code = 'leader' AND u.is_active = 1 AND u.department = ?
                    ORDER BY u.id LIMIT 1
                    """,
                    (user.get("department") or "",),
                )
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        """
                        SELECT u.id
                        FROM users u
                        JOIN user_roles ur ON ur.user_id = u.id
                        JOIN roles r ON r.id = ur.role_id
                        WHERE r.code = 'leader' AND u.is_active = 1
                        ORDER BY u.id LIMIT 1
                        """
                    )
                    row = cursor.fetchone()
                supervisor_id = row[0] if row else user["id"]
            cursor.execute(
                """
                INSERT INTO stocktake_forms
                    (form_no, warehouse_type, date_from, date_to, show_zero, status, checker_id,
                     checker_signature, checker_date, supervisor_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'supervisor', ?, ?, ?, ?, ?, ?)
                """,
                (form_no, warehouse_type, date_from, date_to, show_zero, user["id"], data.get("signature") or user["display_name"], today_text(), supervisor_id, now_text(), now_text()),
            )
            stocktake_id = cursor.lastrowid
            where = []
            params = []
            if warehouse_type:
                where.append("s.warehouse_type = ?")
                params.append(warehouse_type)
            if not show_zero:
                where.append("COALESCE(i.quantity, 0) > 0")
            sql = """
                SELECT m.id, COALESCE(i.quantity, 0) AS quantity, COALESCE(i.amount, 0) AS amount,
                       s.name AS shelf_name, mp.layer_number, mp.zone_name
                FROM materials m
                LEFT JOIN inventory i ON i.material_id = m.id
                LEFT JOIN material_positions mp ON mp.material_id = m.id
                LEFT JOIN shelves s ON s.id = mp.shelf_id
            """
            if where:
                sql += " WHERE " + " AND ".join(where)
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                material_id = row["id"]
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE material_id = ? AND stock_source = ? AND operation_type = 'in' AND operation_date BETWEEN ? AND ?", (material_id, STOCK_SOURCE_FORMAL, date_from, date_to))
                period_in = cursor.fetchone()[0] or 0
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE material_id = ? AND stock_source = ? AND operation_type = 'out' AND operation_date BETWEEN ? AND ?", (material_id, STOCK_SOURCE_FORMAL, date_from, date_to))
                period_out = cursor.fetchone()[0] or 0
                location_text = f"{row['shelf_name'] or ''} {row['layer_number'] or '-'}层 {row['zone_name'] or '-'}区".strip()
                cursor.execute(
                    """
                    INSERT INTO stocktake_items
                        (stocktake_id, material_id, book_quantity, stock_amount, period_in, period_out, location_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (stocktake_id, material_id, row["quantity"], row["amount"], period_in, period_out, location_text),
                )
            set_setting(cursor, "last_stocktake_date", date_to)
            set_setting(cursor, "next_stocktake_date", next_stocktake_due_date(workflow_settings(cursor).get("default_stocktake_reminder_day", 25)))
            conn.commit()
            stocktake = serialize_stocktake(cursor, stocktake_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "stocktake": stocktake})


    def serialize_stocktake(cursor, stocktake_id):
        cursor.execute("SELECT * FROM stocktake_forms WHERE id = ?", (stocktake_id,))
        form = row_to_dict(cursor.fetchone())
        if not form:
            return None
        cursor.execute(
            """
            SELECT si.*, m.material_code, m.name AS material_name, m.brand_model, m.spec, m.unit
            FROM stocktake_items si
            JOIN materials m ON m.id = si.material_id
            WHERE si.stocktake_id = ?
            ORDER BY m.material_code
            """,
            (stocktake_id,),
        )
        form["items"] = [dict(row) for row in cursor.fetchall()]
        return form


    @app.get("/api/stocktakes")
    def list_stocktakes():
        conn = get_db()
        cursor = conn.cursor()
        require_any_permission(cursor, "view_stocktake", "start_stocktake", "edit_stocktake")
        cursor.execute("SELECT * FROM stocktake_forms ORDER BY id DESC")
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(rows)


    @app.get("/api/stocktakes/<int:stocktake_id>")
    def get_stocktake(stocktake_id):
        conn = get_db()
        cursor = conn.cursor()
        require_any_permission(cursor, "view_stocktake", "start_stocktake", "edit_stocktake")
        stocktake = serialize_stocktake(cursor, stocktake_id)
        conn.close()
        if not stocktake:
            return jsonify({"error": "盘点单不存在"}), 404
        return jsonify(stocktake)


    @app.put("/api/stocktakes/<int:stocktake_id>")
    def update_stocktake(stocktake_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "edit_stocktake")
            fields = []
            params = []
            for key in ["warehouse_type", "date_from", "date_to", "status", "checker_signature", "checker_date", "supervisor_signature", "supervisor_date"]:
                if key in data:
                    fields.append(f"{key} = ?")
                    params.append(data.get(key) or "")
            if "show_zero" in data:
                fields.append("show_zero = ?")
                params.append(1 if data.get("show_zero") else 0)
            if "supervisor_id" in data:
                fields.append("supervisor_id = ?")
                params.append(int(data.get("supervisor_id") or 0) or None)
            if not fields:
                raise ValueError("没有可修改字段")
            fields.append("updated_at = ?")
            params.append(now_text())
            params.append(stocktake_id)
            cursor.execute(f"UPDATE stocktake_forms SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
            stocktake = serialize_stocktake(cursor, stocktake_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "stocktake": stocktake})


    @app.delete("/api/stocktakes/<int:stocktake_id>")
    def delete_stocktake(stocktake_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "edit_stocktake")
            recycle_stocktake(cursor, stocktake_id, user.get("id"))
            cursor.execute("DELETE FROM stocktake_items WHERE stocktake_id = ?", (stocktake_id,))
            cursor.execute("DELETE FROM stocktake_forms WHERE id = ?", (stocktake_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.post("/api/stocktakes/<int:stocktake_id>/supervise")
    def supervise_stocktake(stocktake_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM stocktake_forms WHERE id = ? AND status = 'supervisor'", (stocktake_id,))
            stocktake = cursor.fetchone()
            if not stocktake:
                raise ValueError("没有待监盘签字的盘点单")
            if "admin" not in user.get("role_codes", []) and stocktake["supervisor_id"] != user["id"]:
                raise PermissionError("当前账号不是该盘点单的监盘人")
            cursor.execute(
                """
                UPDATE stocktake_forms
                SET status = 'completed', supervisor_id = ?, supervisor_signature = ?, supervisor_date = ?, updated_at = ?
                WHERE id = ?
                """,
                (user["id"], data.get("signature") or user["display_name"], data.get("date") or today_text(), now_text(), stocktake_id),
            )
            conn.commit()
            stocktake = serialize_stocktake(cursor, stocktake_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "stocktake": stocktake})


    @app.post("/api/stock/<operation>")
    def stock_change(operation):
        if operation not in {"in", "out"}:
            return jsonify({"success": False, "error": "操作类型错误"}), 400
        data = request.get_json(force=True)
        try:
            material_id = positive_int_value(data.get("material_id"), "物料")
            quantity = quantity_value(data.get("quantity"), "数量", positive=True)
            unit_price = price_value(data.get("unit_price"), "入库单价") if operation == "in" else 0
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "edit_material")
        cursor.execute("SELECT quantity FROM inventory WHERE material_id = ?", (material_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "物料不存在"}), 404
        current = float(row["quantity"])
        try:
            if operation == "in":
                cursor.execute("SELECT shelf_id, layer_number, zone_name FROM material_positions WHERE material_id = ?", (material_id,))
                pos = row_to_dict(cursor.fetchone()) or {}
                add_inventory_batch(
                    cursor,
                    material_id,
                    quantity,
                    unit_price,
                    {
                        "warehouse_type": data.get("warehouse_type") or "office",
                        "shelf_id": data.get("shelf_id") or pos.get("shelf_id"),
                        "layer_number": data.get("layer_number") or pos.get("layer_number") or 1,
                        "zone_name": data.get("zone_name") or pos.get("zone_name") or "A",
                        "received_date": data.get("operation_date") or today_text(),
                        "remark": data.get("remark") or "手工入库",
                    },
                    data.get("form_no") or "MANUAL",
                )
            else:
                if current - quantity < -1e-9:
                    conn.close()
                    return jsonify({"success": False, "error": "库存不足"}), 400
                consume_inventory_fifo(
                    cursor,
                    material_id,
                    quantity,
                    data.get("form_no") or "MANUAL",
                    data.get("operation_date") or today_text(),
                    data.get("remark") or "手工出库",
                )
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.commit()
        material = fetch_material(cursor, material_id)
        conn.close()
        return jsonify({"success": True, "material": material})


    @app.get("/api/export")
    def export_materials():
        material_code = request.args.get("material_code", "").strip()
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_query")
        if material_code:
            sql, params = material_query("m.material_code = ?", (material_code,))
        else:
            sql, params = material_query()
        cursor.execute(sql, params)
        materials = [dict(row) for row in cursor.fetchall()]
        for material in materials:
            cursor.execute(
                """
                SELECT * FROM stock_records
                WHERE material_id = ? AND stock_source = ?
                ORDER BY operation_date ASC, id ASC
                """,
                (material["id"], STOCK_SOURCE_FORMAL),
            )
            material["records"] = [dict(row) for row in cursor.fetchall()]
        conn.close()
        html = material_cards_html(materials, material_code)
        return app.response_class(html, content_type="text/html; charset=utf-8")
