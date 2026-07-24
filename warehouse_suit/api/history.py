# -*- coding: utf-8 -*-
"""Inventory item history route registration."""

from flask import jsonify, request

from warehouse_suit.inventory_service import get_item_change_history


VALID_ITEM_HISTORY_TYPES = {
    "semifinished",
    "finished",
    "defective_finished",
    "defective_semifinished",
    "scrapped_finished",
    "scrapped_semifinished",
}


def register_history_routes(app, *, get_db, current_user_provider):
    """Register item change-history endpoints."""

    def item_history(item_type, item_ref_id):
        if item_type not in VALID_ITEM_HISTORY_TYPES:
            return jsonify({"error": "无效的物品类型"}), 400

        page = max(1, int(request.args.get("page", 1)))
        limit = max(1, min(100, int(request.args.get("limit", 20))))

        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        if not user:
            conn.close()
            return jsonify({"error": "请先登录"}), 401

        try:
            result = get_item_change_history(cursor, item_type, item_ref_id, page, limit)
        except ValueError:
            conn.close()
            return jsonify({"error": "物品不存在"}), 404

        conn.close()
        return jsonify(result)

    app.add_url_rule("/api/items/<item_type>/<int:item_ref_id>/history", "item_history", item_history, methods=["GET"])
