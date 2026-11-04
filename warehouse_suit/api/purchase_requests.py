"""采购申请与汇总接口。"""

from flask import jsonify, request

from warehouse_suit.purchase_request_service import (
    create_purchase_request,
    create_purchase_summary,
    list_purchase_requests,
    list_purchase_summaries,
    match_existing_materials,
    remove_summary_line,
    return_purchase_request,
    save_summary_to_warehouse,
    suggest_material_code,
)
from warehouse_suit.workflow_service import require_any_permission, require_permission, serialize_form


def register_purchase_request_routes(app, *, get_db, current_user_provider):
    @app.get("/api/purchase-requests")
    def purchase_request_list():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_any_permission(cursor, "start_purchase_request", "manage_purchase_summary")
            rows = list_purchase_requests(cursor, user, request.args.get("scope") or "mine")
            return jsonify({"items": rows})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/purchase-requests")
    def purchase_request_create():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_purchase_request")
            form_id = create_purchase_request(cursor, user, request.get_json(force=True) or {})
            conn.commit()
            return jsonify({"success": True, "form": serialize_form(cursor, form_id)})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/purchase-requests/<int:form_id>/return")
    def purchase_request_return(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "manage_purchase_summary")
            data = request.get_json(force=True) or {}
            return_purchase_request(cursor, user, form_id, data.get("reason"))
            conn.commit()
            return jsonify({"success": True})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/purchase-summaries")
    def purchase_summary_list():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            rows = list_purchase_summaries(cursor, request.args.get("scope") or "history")
            return jsonify({"items": rows})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/purchase-summaries")
    def purchase_summary_create():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "manage_purchase_summary")
            data = request.get_json(force=True) or {}
            form_id = create_purchase_summary(cursor, user, data.get("request_ids") or [])
            conn.commit()
            return jsonify({"success": True, "form": serialize_form(cursor, form_id)})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/purchase-summaries/match")
    def purchase_summary_match():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            rows = match_existing_materials(
                cursor,
                request.args.get("name"),
                request.args.get("brand_model"),
                request.args.get("spec"),
            )
            return jsonify({"items": rows})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/purchase-summaries/next-code")
    def purchase_summary_next_code():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            code = suggest_material_code(
                cursor,
                request.args.get("warehouse_code"),
                request.args.get("major_code"),
                request.args.get("middle_code"),
                request.args.get("small_code"),
                request.args.get("name") or "",
                request.args.get("brand_model") or "",
                request.args.get("spec") or "",
            )
            return jsonify({"material_code": code})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/purchase-summaries/<int:form_id>/remove-line")
    def purchase_summary_remove_line(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            data = request.get_json(force=True) or {}
            remove_summary_line(cursor, form_id, data.get("item_id"))
            conn.commit()
            return jsonify({"success": True})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/purchase-summaries/<int:form_id>/save")
    def purchase_summary_save(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            data = request.get_json(force=True) or {}
            save_summary_to_warehouse(cursor, form_id, data.get("items") or [])
            conn.commit()
            return jsonify({"success": True, "form": serialize_form(cursor, form_id)})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()
