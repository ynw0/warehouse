# -*- coding: utf-8 -*-
"""Temporary inventory HTTP API routes."""

from flask import jsonify, request

from warehouse_suit.temporary_inventory_service import (
    TemporaryInventoryDisabled,
    adjust_temporary_batch,
    create_temporary_batch,
    create_temporary_material,
    delete_temporary_material,
    require_temporary_inventory_enabled,
    temporary_batch_rows,
    temporary_material_choices,
    temporary_inventory_rows,
    temporary_record_rows,
    update_temporary_material,
)
from warehouse_suit.workflow_service import require_permission


def _error_response(exc):
    if isinstance(exc, TemporaryInventoryDisabled):
        return jsonify({"success": False, "error": str(exc)}), 409
    if isinstance(exc, PermissionError):
        return jsonify({"success": False, "error": str(exc)}), 403
    return jsonify({"success": False, "error": str(exc)}), 400


def register_temporary_inventory_routes(app, *, get_db, current_user_provider):
    """Register source-fixed temporary inventory management endpoints."""

    def require_access(cursor, permission):
        require_temporary_inventory_enabled(cursor)
        return require_permission(cursor, permission)

    @app.get("/api/temporary-inventory")
    def list_temporary_inventory():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_access(cursor, "view_temporary_inventory")
            result = temporary_inventory_rows(
                cursor,
                q=request.args.get("q", ""),
                page=request.args.get("page", 1),
                page_size=request.args.get("page_size", 20),
                category=request.args.get("category", ""),
                warehouse_type=request.args.get("warehouse_type", ""),
                inventory_status=request.args.get("inventory_status", "available"),
                include_zero=str(request.args.get("include_zero", "")).lower() in {"1", "true", "yes"},
            )
            return jsonify({"success": True, **result})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()


    @app.get("/api/temporary-inventory/material-choices")
    def list_temporary_material_choices():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_access(cursor, "manage_temporary_inventory")
            return jsonify({"success": True, "items": temporary_material_choices(
                cursor, request.args.get("q", ""), request.args.get("limit", 200)
            )})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()

    @app.get("/api/temporary-inventory/materials/<int:material_id>/batches")
    def list_temporary_batches(material_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_access(cursor, "view_temporary_inventory")
            cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
            if not cursor.fetchone():
                return jsonify({"success": False, "error": "物料不存在"}), 404
            return jsonify({"success": True, "items": temporary_batch_rows(cursor, material_id)})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()

    @app.get("/api/temporary-inventory/records")
    def list_temporary_records():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_access(cursor, "view_temporary_inventory")
            result = temporary_record_rows(
                cursor,
                q=request.args.get("q", ""),
                page=request.args.get("page", 1),
                page_size=request.args.get("page_size", 30),
                material_id=request.args.get("material_id", 0),
            )
            return jsonify({"success": True, **result})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/batches")
    def add_temporary_batch():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "manage_temporary_inventory")
            result = create_temporary_batch(
                cursor,
                request.get_json(force=True) or {},
                user,
                request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            )
            conn.commit()
            return jsonify({"success": True, **result})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()


    @app.post("/api/temporary-inventory/materials")
    def add_temporary_material():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "manage_temporary_inventory")
            result = create_temporary_material(cursor, request.get_json(force=True) or {}, user,
                                               request.headers.get("X-Forwarded-For", request.remote_addr or ""))
            conn.commit()
            return jsonify({"success": True, **result})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.put("/api/temporary-inventory/materials/<int:material_id>")
    def edit_temporary_material(material_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "manage_temporary_inventory")
            material = update_temporary_material(cursor, material_id, request.get_json(force=True) or {}, user,
                                                 request.headers.get("X-Forwarded-For", request.remote_addr or ""))
            conn.commit()
            return jsonify({"success": True, "material": material})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.delete("/api/temporary-inventory/materials/<int:material_id>")
    def remove_temporary_material(material_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "manage_temporary_inventory")
            delete_temporary_material(cursor, material_id, user,
                                      request.headers.get("X-Forwarded-For", request.remote_addr or ""))
            conn.commit()
            return jsonify({"success": True})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/batches/<int:batch_id>/adjust")
    def adjust_temporary_inventory(batch_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "manage_temporary_inventory")
            result = adjust_temporary_batch(
                cursor,
                batch_id,
                request.get_json(force=True) or {},
                user,
                request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            )
            conn.commit()
            return jsonify({"success": True, **result})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()
