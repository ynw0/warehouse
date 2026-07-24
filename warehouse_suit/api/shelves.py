# -*- coding: utf-8 -*-
"""Shelf and storage-location route registration."""

from flask import jsonify, request

from warehouse_suit.db import now_text, row_to_dict
from warehouse_suit.material_repository import fetch_layers, fetch_shelf, material_query
from warehouse_suit.recycle import recycle_payload, recycle_store
from warehouse_suit.shelf_service import replace_layers, validate_zones
from warehouse_suit.workflow_service import require_permission


def register_shelf_routes(app, *, get_db):
    """Register shelf CRUD endpoints."""

    def get_shelves():
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM shelves ORDER BY warehouse_type, id")
        shelves = []
        for row in cursor.fetchall():
            shelf = dict(row)
            shelf["layers"] = fetch_layers(cursor, shelf["id"])
            shelves.append(shelf)
        conn.close()
        return jsonify(shelves)

    def create_shelf():
        data = request.get_json(force=True)
        layers = validate_zones(data.get("layers") or [])
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "edit_material")
        cursor.execute(
            """
            INSERT INTO shelves (name, warehouse_type, shape, position_x, position_y, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("name") or "未命名货架",
                data.get("warehouse_type") or data.get("type") or "office",
                data.get("shape") or "straight",
                float(data.get("position_x") or 12),
                float(data.get("position_y") or 18),
                now_text(),
            ),
        )
        shelf_id = cursor.lastrowid
        replace_layers(cursor, shelf_id, layers)
        conn.commit()
        shelf = fetch_shelf(cursor, shelf_id)
        conn.close()
        return jsonify({"success": True, "shelf": shelf})

    def update_shelf(shelf_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "edit_material")
        cursor.execute(
            """
            UPDATE shelves
            SET name = ?, warehouse_type = ?, shape = ?, position_x = ?, position_y = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                data.get("name") or "未命名货架",
                data.get("warehouse_type") or data.get("type") or "office",
                data.get("shape") or "straight",
                float(data.get("position_x") or 0),
                float(data.get("position_y") or 0),
                now_text(),
                shelf_id,
            ),
        )
        replace_layers(cursor, shelf_id, data.get("layers") or [])
        conn.commit()
        shelf = fetch_shelf(cursor, shelf_id)
        conn.close()
        if not shelf:
            return jsonify({"success": False, "error": "货架不存在"}), 404
        return jsonify({"success": True, "shelf": shelf})

    def get_shelf(shelf_id):
        conn = get_db()
        cursor = conn.cursor()
        shelf = fetch_shelf(cursor, shelf_id)
        if not shelf:
            conn.close()
            return jsonify({"error": "货架不存在"}), 404
        sql, params = material_query("mp.shelf_id = ?", (shelf_id,))
        cursor.execute(sql, params)
        shelf["materials"] = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(shelf)

    def delete_shelf(shelf_id):
        conn = get_db()
        cursor = conn.cursor()
        user = require_permission(cursor, "edit_material")
        cursor.execute("SELECT * FROM shelves WHERE id = ?", (shelf_id,))
        shelf = row_to_dict(cursor.fetchone())
        if not shelf:
            conn.close()
            return jsonify({"success": False, "error": "货架不存在"}), 404
        recycle_store(
            cursor,
            "shelf",
            shelf_id,
            shelf.get("name") or "",
            {"shelves": [shelf], "shelf_layers": recycle_payload(cursor, "shelf_layers", "shelf_id = ?", (shelf_id,))},
            user.get("id"),
        )
        cursor.execute("DELETE FROM material_positions WHERE shelf_id = ?", (shelf_id,))
        cursor.execute("DELETE FROM shelf_layers WHERE shelf_id = ?", (shelf_id,))
        cursor.execute("DELETE FROM shelves WHERE id = ?", (shelf_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    app.add_url_rule("/api/shelves", "get_shelves", get_shelves, methods=["GET"])
    app.add_url_rule("/api/shelves", "create_shelf", create_shelf, methods=["POST"])
    app.add_url_rule("/api/shelves/<int:shelf_id>", "update_shelf", update_shelf, methods=["PUT"])
    app.add_url_rule("/api/shelves/<int:shelf_id>", "get_shelf", get_shelf, methods=["GET"])
    app.add_url_rule("/api/shelves/<int:shelf_id>", "delete_shelf", delete_shelf, methods=["DELETE"])
