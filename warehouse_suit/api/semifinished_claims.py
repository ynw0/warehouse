# -*- coding: utf-8 -*-
"""半成品领用流程。"""

from flask import jsonify, request

from warehouse_suit.semifinished_claim_service import (
    create_semifinished_claim,
    decide_semifinished_claim,
    list_claimable_semifinished,
    outbound_semifinished_claim,
)
from warehouse_suit.workflow_service import require_permission, serialize_form


def register_semifinished_claim_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider

    @app.get("/api/semifinished-claims/items")
    def semifinished_claim_items():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "start_claim")
            rows = list_claimable_semifinished(
                cursor,
                keyword=request.args.get("keyword", ""),
                purchase_project=request.args.get("purchase_project") or "",
            )
        except Exception as exc:
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"items": rows})

    @app.post("/api/semifinished-claims")
    def create_semifinished_claim_form():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_claim")
            form_id = create_semifinished_claim(cursor, user, data)
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})

    @app.post("/api/semifinished-claims/<int:form_id>/leader")
    def leader_semifinished_claim(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            decide_semifinished_claim(cursor, user, form_id, data)
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})

    @app.post("/api/semifinished-claims/<int:form_id>/outbound")
    def outbound_semifinished_claim_form(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            outbound_semifinished_claim(cursor, user, form_id, data or {})
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})
