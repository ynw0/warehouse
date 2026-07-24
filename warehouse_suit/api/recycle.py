# -*- coding: utf-8 -*-
"""Recycle-bin route registration."""

from flask import jsonify

from warehouse_suit.db import row_to_dict
from warehouse_suit.recycle import cleanup_recycle_bin, restore_recycle_payload
from warehouse_suit.settings import workflow_settings
from warehouse_suit.workflow_service import require_permission


def register_recycle_routes(app, *, get_db):
    """Register recycle-bin list, restore, and purge endpoints."""

    def list_recycle_bin():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_recycle")
        cleanup_recycle_bin(cursor)
        cursor.execute(
            """
            SELECT rb.id, rb.target_type, rb.target_id, rb.title, rb.deleted_at, rb.purge_after,
                   u.display_name AS deleted_by_name
            FROM recycle_bin rb
            LEFT JOIN users u ON u.id = rb.deleted_by
            ORDER BY rb.deleted_at DESC, rb.id DESC
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        retention_days = workflow_settings(cursor).get("recycle_retention_days", 30)
        conn.commit()
        conn.close()
        return jsonify({"items": rows, "retention_days": retention_days})

    def restore_recycle_entry(entry_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "view_recycle")
            cursor.execute("SELECT * FROM recycle_bin WHERE id = ?", (entry_id,))
            entry = row_to_dict(cursor.fetchone())
            if not entry:
                raise ValueError("recycle entry not found")
            restore_recycle_payload(cursor, entry)
            cursor.execute("DELETE FROM recycle_bin WHERE id = ?", (entry_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    def purge_recycle_entry(entry_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "view_recycle")
            cursor.execute("DELETE FROM recycle_bin WHERE id = ?", (entry_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    app.add_url_rule("/api/system/recycle-bin", "list_recycle_bin", list_recycle_bin, methods=["GET"])
    app.add_url_rule("/api/system/recycle-bin/<int:entry_id>/restore", "restore_recycle_entry", restore_recycle_entry, methods=["POST"])
    app.add_url_rule("/api/system/recycle-bin/<int:entry_id>", "purge_recycle_entry", purge_recycle_entry, methods=["DELETE"])
