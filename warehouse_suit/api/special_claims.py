"""特殊用料接口。"""

from flask import jsonify, request

from warehouse_suit.special_claim_service import (
    create_special_claim,
    decide_special_claim,
    formal_special_materials,
    outbound_special_claim,
)
from warehouse_suit.workflow_service import require_permission, serialize_form


def register_special_claim_routes(app, *, get_db, current_user_provider):
    @app.get("/api/special-claims/materials")
    def special_claim_materials():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "start_special_claim")
            rows = formal_special_materials(
                cursor,
                keyword=request.args.get("keyword", ""),
                usage_kind=request.args.get("usage_kind") or "研发用料",
                purchase_project=request.args.get("purchase_project", ""),
            )
        except Exception as exc:
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify(rows)

    @app.post("/api/special-claims")
    def special_claim_create():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_special_claim")
            form_id = create_special_claim(cursor, user, request.get_json(force=True) or {})
            conn.commit()
            return jsonify({"success": True, "form": serialize_form(cursor, form_id)})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/special-claims/<int:form_id>/decide")
    def special_claim_decide(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user_provider(cursor)
            if not user:
                raise PermissionError("请先登录")
            data = request.get_json(force=True) or {}
            decide_special_claim(cursor, user, form_id, bool(data.get("approved")), data.get("remark") or "")
            conn.commit()
            return jsonify({"success": True, "form": serialize_form(cursor, form_id)})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/special-claims/<int:form_id>/outbound")
    def special_claim_outbound(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "edit_outbound")
            outbound_special_claim(cursor, user, form_id, request.get_json(force=True) or {})
            conn.commit()
            return jsonify({"success": True, "form": serialize_form(cursor, form_id)})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()
