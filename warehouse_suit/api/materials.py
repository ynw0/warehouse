# -*- coding: utf-8 -*-
"""Material master-data and lookup route registration."""

from flask import jsonify, request

from warehouse_suit.attachments import attach_batch_attachments

from warehouse_suit.db import now_text, row_to_dict, today_text
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.material_repository import fetch_material, material_query
from warehouse_suit.material_service import (
    next_material_code,
    save_material_batches_from_payload,
    update_material_master_by_id,
    upsert_material_master,
    write_material_position,
)
from warehouse_suit.material_utils import infer_code_parts
from warehouse_suit.recycle import recycle_material
from warehouse_suit.validation import (
    nonnegative_int_value,
    positive_int_value,
    quantity_value,
    validate_material_code_value,
    validate_plain_text,
)
from warehouse_suit.workflow_service import require_permission


def register_material_routes(app, *, get_db):
    """Register material lookup and master-data endpoints."""

    def get_materials():
        conn = get_db()
        cursor = conn.cursor()
        sql, params = material_query()
        cursor.execute(sql, params)
        materials = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(materials)

    def get_next_material_code():
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor()
            code, step = next_material_code(
                cursor,
                request.args.get("warehouse_code") or request.args.get("warehouse") or "20",
                request.args.get("major_code") or request.args.get("category") or "",
                request.args.get("middle_code") or "",
                request.args.get("small_code") or "",
                request.args.get("name") or "",
                request.args.get("brand_model") or request.args.get("brand") or "",
                request.args.get("spec") or "",
            )
            conn.close()
            return jsonify({"success": True, "material_code": code, "detail_code": code[-4:], "step": step})
        except Exception as exc:
            if conn is not None:
                conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400

    def create_or_update_material_master():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            material_code = str(data.get("material_code") or "").strip()
            existing = False
            if material_code:
                cursor.execute("SELECT id FROM materials WHERE material_code = ?", (material_code,))
                existing = bool(cursor.fetchone())
            require_permission(cursor, "edit_material" if existing else "add_material")
            material = upsert_material_master(cursor, data)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "material": material})

    def update_material_master(material_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "edit_material")
            material = update_material_master_by_id(cursor, material_id, data)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "material": material})

    def search_materials():
        keyword = request.args.get("keyword", "").strip()
        conn = get_db()
        cursor = conn.cursor()
        if keyword:
            like = f"%{keyword}%"
            sql, params = material_query(
                """
                m.material_code LIKE ?
                OR m.brand_model LIKE ?
                OR m.spec LIKE ?
                OR m.name LIKE ?
                OR m.purchase_applicant LIKE ?
                """,
                (like, like, like, like, like),
            )
        else:
            sql, params = material_query()
        cursor.execute(sql, params)
        materials = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(materials)

    @app.post("/api/materials")
    def create_material():
        data = request.get_json(force=True)
        try:
            required = ["material_code", "name", "shelf_id", "layer_number", "zone_name"]
            missing = [field for field in required if not data.get(field)]
            if missing:
                raise ValueError("缺少字段: " + ", ".join(missing))
            material_code = validate_material_code_value(data.get("material_code"))
            name = validate_plain_text(data.get("name"), "物料名称", max_length=120, required=True)
            initial_quantity = quantity_value(data.get("initial_quantity"), "初始库存", 0)
            shelf_id = positive_int_value(data.get("shelf_id"), "货架")
            layer_number = positive_int_value(data.get("layer_number"), "层号")
            slot_index = nonnegative_int_value(data.get("slot_index"), "货位序号", 0)
            zone_name = validate_plain_text(data.get("zone_name"), "分区", max_length=20, required=True).upper()
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "add_material")
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (material_code,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "物料编号已存在"}), 400

        cursor.execute(
            """
            INSERT INTO materials
                (material_code, brand_model, spec, name, category, sub_category, unit, icon, purchase_applicant, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                material_code,
                data.get("brand_model") or "",
                data.get("spec") or "",
                name,
                data.get("category") or "",
                data.get("sub_category") or "",
                data.get("unit") or "个",
                data.get("icon") or "📦",
                data.get("purchase_applicant") or "",
                now_text(),
            ),
        )
        material_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO inventory (material_id, quantity, updated_at) VALUES (?, ?, ?)",
            (material_id, initial_quantity, now_text()),
        )
        cursor.execute(
            """
            INSERT INTO material_positions (material_id, shelf_id, layer_number, zone_name, slot_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                material_id,
                shelf_id,
                layer_number,
                zone_name,
                slot_index,
            ),
        )
        if initial_quantity:
            cursor.execute(
                """
                INSERT INTO stock_records
                    (material_id, operation_type, quantity, balance_after, operation_date, remark, created_at)
                VALUES (?, 'in', ?, ?, ?, ?, ?)
                """,
                (
                    material_id,
                    initial_quantity,
                    initial_quantity,
                    data.get("operation_date") or today_text(),
                    "初始入库",
                    now_text(),
                ),
            )
        conn.commit()
        material = fetch_material(cursor, material_id)
        conn.close()
        return jsonify({"success": True, "material": material})


    @app.get("/api/materials/<int:material_id>")
    def get_material(material_id):
        conn = get_db()
        cursor = conn.cursor()
        material = fetch_material(cursor, material_id)
        if material:
            attach_batch_attachments(cursor, material)
        conn.close()
        if not material:
            return jsonify({"error": "物料不存在"}), 404
        return jsonify(material)


    @app.put("/api/materials/<int:material_id>")
    def update_material(material_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "edit_material")
            begin_inventory_transaction(conn)
            cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
            current = row_to_dict(cursor.fetchone())
            if not current:
                raise ValueError("material not found")

            old_material_code = current.get("material_code") or ""
            material_code = validate_material_code_value(data.get("material_code") if data.get("material_code") is not None else old_material_code)
            name = str(data.get("name") if data.get("name") is not None else current.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            cursor.execute("SELECT id FROM materials WHERE material_code = ? AND id <> ?", (material_code, material_id))
            if cursor.fetchone():
                raise ValueError("material_code already exists")

            parts = infer_code_parts(material_code)
            warehouse_code = str(data.get("warehouse_code") if data.get("warehouse_code") is not None else current.get("warehouse_code") or parts.get("warehouse_code", "")).strip()
            major_code = str(data.get("major_code") if data.get("major_code") is not None else data.get("category") if data.get("category") is not None else current.get("major_code") or parts.get("major_code", "")).strip()
            middle_code = str(data.get("middle_code") if data.get("middle_code") is not None else current.get("middle_code") or parts.get("middle_code", "")).strip()
            small_code = str(data.get("small_code") if data.get("small_code") is not None else current.get("small_code") or parts.get("small_code", "")).strip()
            detail_code = str(data.get("detail_code") if data.get("detail_code") is not None else current.get("detail_code") or parts.get("detail_code", "")).strip()
            sub_category = str(data.get("sub_category") if data.get("sub_category") is not None else current.get("sub_category") or f"{middle_code}{small_code}".strip()).strip()

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
                    data.get("brand_model") if data.get("brand_model") is not None else current.get("brand_model") or "",
                    data.get("spec") if data.get("spec") is not None else current.get("spec") or "",
                    name,
                    major_code,
                    sub_category,
                    data.get("unit") if data.get("unit") is not None else current.get("unit") or "",
                    data.get("icon") if data.get("icon") is not None else current.get("icon") or "",
                    warehouse_code,
                    major_code,
                    middle_code,
                    small_code,
                    detail_code,
                    data.get("category_name") if data.get("category_name") is not None else current.get("category_name") or "",
                    data.get("material_type") if data.get("material_type") is not None else current.get("material_type") or "",
                    data.get("purchase_applicant") if data.get("purchase_applicant") is not None else current.get("purchase_applicant") or "",
                    now_text(),
                    material_id,
                ),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO inventory (material_id, quantity, amount, updated_at) VALUES (?, 0, 0, ?)",
                (material_id, now_text()),
            )
            write_material_position(cursor, material_id, data)
            save_material_batches_from_payload(cursor, material_id, material_code, old_material_code, data)
            conn.commit()
            material = fetch_material(cursor, material_id)
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "material": material})


    @app.put("/api/materials/<int:material_id>/position")
    def update_material_position(material_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "edit_material")
            cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({"success": False, "error": "物料不存在"}), 404
            shelf_id = positive_int_value(data.get("shelf_id"), "货架")
            layer_number = positive_int_value(data.get("layer_number") or 1, "层号")
            slot_index = nonnegative_int_value(data.get("slot_index"), "货位序号", 0)
            zone_name = validate_plain_text(data.get("zone_name") or "A", "分区", max_length=20, required=True).upper()
            cursor.execute("SELECT id FROM shelves WHERE id = ?", (shelf_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({"success": False, "error": "货架不存在"}), 404
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
                (material_id, shelf_id, layer_number, zone_name, slot_index),
            )
            conn.commit()
            material = fetch_material(cursor, material_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "material": material})


    @app.delete("/api/materials/<int:material_id>")
    def delete_material(material_id):
        conn = get_db()
        cursor = conn.cursor()
        user = require_permission(cursor, "edit_material")
        cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "物料不存在"}), 404
        cursor.execute("SELECT COUNT(*) FROM material_attachments WHERE material_id = ? AND material_batch_id IS NOT NULL", (material_id,))
        if int(cursor.fetchone()[0] or 0) > 0:
            conn.close()
            return jsonify({"success": False, "error": "该物料已有需永久留存的批次附件，不能删除"}), 400
        recycle_material(cursor, material_id, user.get("id"))
        cursor.execute("DELETE FROM material_batches WHERE material_id = ?", (material_id,))
        cursor.execute("DELETE FROM stock_records WHERE material_id = ?", (material_id,))
        cursor.execute("DELETE FROM material_positions WHERE material_id = ?", (material_id,))
        cursor.execute("DELETE FROM inventory WHERE material_id = ?", (material_id,))
        cursor.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    app.add_url_rule("/api/materials", "get_materials", get_materials, methods=["GET"])
    app.add_url_rule("/api/material-code/next", "get_next_material_code", get_next_material_code, methods=["GET"])
    app.add_url_rule("/api/material-master", "create_or_update_material_master", create_or_update_material_master, methods=["POST"])
    app.add_url_rule("/api/material-master/<int:material_id>", "update_material_master", update_material_master, methods=["PUT"])
    app.add_url_rule("/api/materials/search", "search_materials", search_materials, methods=["GET"])
