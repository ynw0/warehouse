# -*- coding: utf-8 -*-
"""Start-page draft box route registration."""

from flask import jsonify, request

from warehouse_suit.draft_service import (
    delete_draft,
    get_draft,
    list_drafts,
    require_draft_access,
    save_draft,
    submit_draft,
)
from warehouse_suit.workflow_service import serialize_form


def _error_response(exc):
    if isinstance(exc, PermissionError):
        return jsonify({"success": False, "error": str(exc)}), 403
    if isinstance(exc, LookupError):
        return jsonify({"success": False, "error": str(exc)}), 404
    return jsonify({"success": False, "error": str(exc)}), 400


def register_draft_routes(app, *, get_db, current_user_provider, optional_active_user_id_provider):
    optional_active_user_id = optional_active_user_id_provider

    @app.get("/api/drafts")
    def list_workflow_drafts():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_draft_access(cursor)
            drafts = list_drafts(cursor, user)
        except Exception as exc:
            conn.close()
            return _error_response(exc)
        conn.close()
        return jsonify({"success": True, "drafts": drafts})

    @app.post("/api/drafts")
    def create_workflow_draft():
        data = request.get_json(force=True) or {}
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_draft_access(cursor)
            draft = save_draft(cursor, user, data.get("form_type"), data.get("payload"))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return _error_response(exc)
        conn.close()
        return jsonify({"success": True, "draft": draft})

    @app.get("/api/drafts/<int:draft_id>")
    def get_workflow_draft(draft_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_draft_access(cursor)
            draft = get_draft(cursor, user, draft_id)
        except Exception as exc:
            conn.close()
            return _error_response(exc)
        conn.close()
        return jsonify({"success": True, "draft": draft})

    @app.put("/api/drafts/<int:draft_id>")
    def update_workflow_draft(draft_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_draft_access(cursor)
            form_type = data.get("form_type")
            if not form_type:
                existing = get_draft(cursor, user, draft_id)
                form_type = existing["form_type"]
            draft = save_draft(cursor, user, form_type, data.get("payload"), draft_id=draft_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return _error_response(exc)
        conn.close()
        return jsonify({"success": True, "draft": draft})

    @app.delete("/api/drafts/<int:draft_id>")
    def delete_workflow_draft(draft_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_draft_access(cursor)
            delete_draft(cursor, user, draft_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return _error_response(exc)
        conn.close()
        return jsonify({"success": True})

    @app.post("/api/drafts/<int:draft_id>/submit")
    def submit_workflow_draft(draft_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_draft_access(cursor)
            form_id = submit_draft(cursor, user, draft_id, optional_active_user_id)
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return _error_response(exc)
        conn.close()
        return jsonify({"success": True, "form": form})
