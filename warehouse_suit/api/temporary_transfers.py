# -*- coding: utf-8 -*-
"""Temporary-to-formal inventory transfer HTTP routes."""

from flask import jsonify, request

from warehouse_suit.temporary_inventory_service import (
    TemporaryInventoryDisabled,
    require_temporary_inventory_enabled,
)
from warehouse_suit.transfer_settlement_service import (
    mark_auto_claim_exception,
    process_auto_claims,
    retry_auto_claims,
    settlement_summary,
)
from warehouse_suit.transfer_service import (
    TransferConflict,
    TransferNotFound,
    cancel_transfer_task,
    claim_transfer_task,
    create_transfer_task,
    list_transfer_tasks,
    serialize_transfer_task,
    start_transfer_acceptance,
    sync_transfer_task,
    transfer_preview,
)
from warehouse_suit.workflow_service import require_permission


def _error_response(exc):
    if isinstance(exc, TemporaryInventoryDisabled):
        return jsonify({"success": False, "error": str(exc)}), 409
    if isinstance(exc, TransferConflict):
        return jsonify({"success": False, "error": str(exc)}), 409
    if isinstance(exc, TransferNotFound):
        return jsonify({"success": False, "error": str(exc)}), 404
    if isinstance(exc, PermissionError):
        return jsonify({"success": False, "error": str(exc)}), 403
    return jsonify({"success": False, "error": str(exc)}), 400


def register_temporary_transfer_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider

    def actor(cursor):
        user = current_user(cursor)
        if not user:
            raise PermissionError("请先登录")
        return user

    def require_task_visibility(cursor, user):
        if "admin" not in (user.get("role_codes") or []):
            require_temporary_inventory_enabled(cursor)
        return user

    def request_ip():
        return request.headers.get("X-Forwarded-For", request.remote_addr or "")

    @app.get("/api/temporary-inventory/transfers/preview")
    def get_temporary_transfer_preview():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            require_permission(cursor, "transfer_temporary_inventory")
            preview = transfer_preview(cursor, request.args.get("material_id", 0))
            return jsonify({"success": True, "preview": preview})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()


    @app.post("/api/temporary-inventory/transfers")
    def create_temporary_transfer():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "transfer_temporary_inventory")
            data = request.get_json(force=True) or {}
            task = create_transfer_task(
                cursor,
                data.get("material_id"),
                data.get("idempotency_key"),
                user,
                request_ip(),
            )
            conn.commit()
            return jsonify({"success": True, "task": task, "idempotent": task["idempotent"]})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.get("/api/temporary-inventory/transfers")
    def get_temporary_transfers():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_task_visibility(cursor, actor(cursor))
            result = list_transfer_tasks(
                cursor,
                user,
                page=request.args.get("page", 1),
                page_size=request.args.get("page_size", 20),
                status=request.args.get("status", ""),
                material_id=request.args.get("material_id", 0),
                assigned_to_me=str(request.args.get("assigned_to_me", "")).lower()
                in {"1", "true", "yes"},
                q=request.args.get("q", ""),
            )
            return jsonify({"success": True, **result})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()

    @app.get("/api/temporary-inventory/transfers/<int:task_id>")
    def get_temporary_transfer(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_task_visibility(cursor, actor(cursor))
            task = serialize_transfer_task(cursor, task_id, user)
            return jsonify({"success": True, "task": task})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/transfers/<int:task_id>/claim")
    def claim_temporary_transfer(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            task = claim_transfer_task(cursor, task_id, actor(cursor), request_ip())
            conn.commit()
            return jsonify({"success": True, "task": task, "idempotent": task["idempotent"]})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/transfers/<int:task_id>/cancel")
    def cancel_temporary_transfer(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            data = request.get_json(force=True) or {}
            task = cancel_transfer_task(
                cursor,
                task_id,
                actor(cursor),
                data.get("reason") or "",
                request_ip(),
            )
            conn.commit()
            return jsonify({"success": True, "task": task, "idempotent": task["idempotent"]})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/transfers/<int:task_id>/start-acceptance")
    def start_temporary_transfer_acceptance(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            data = request.get_json(force=True) or {}
            task = start_transfer_acceptance(
                cursor,
                task_id,
                actor(cursor),
                data,
                data.get("idempotency_key"),
                request_ip(),
            )
            conn.commit()
            return jsonify(
                {"success": True, "task": task, "idempotent": task["idempotent"]}
            )
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/transfers/<int:task_id>/retry")
    def retry_temporary_transfer(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "process_temporary_transfer")
            task = sync_transfer_task(cursor, task_id, user, request_ip())
            conn.commit()
            return jsonify({"success": True, "task": task})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()
    @app.post("/api/temporary-inventory/transfers/<int:task_id>/process-auto-claims")
    def process_temporary_transfer_auto_claims(task_id):
        conn = get_db()
        cursor = conn.cursor()
        user = None
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "process_temporary_transfer")
            task = process_auto_claims(cursor, task_id, user, request_ip())
            conn.commit()
            return jsonify({"success": True, "task": task, "idempotent": task["idempotent"]})
        except Exception as exc:
            conn.rollback()
            if user is not None:
                try:
                    row = cursor.execute(
                        "SELECT status FROM inventory_transfer_tasks WHERE id = ?",
                        (int(task_id),),
                    ).fetchone()
                    if row and row["status"] in {
                        "formal_inbound_complete",
                        "reserving",
                        "auto_claim_creating",
                    }:
                        mark_auto_claim_exception(
                            cursor, task_id, "auto_claim_process_failed", str(exc), user, request_ip()
                        )
                        conn.commit()
                except Exception:
                    conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.post("/api/temporary-inventory/transfers/<int:task_id>/retry-auto-claims")
    def retry_temporary_transfer_auto_claims(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_temporary_inventory_enabled(cursor)
            user = require_permission(cursor, "process_temporary_transfer")
            task = retry_auto_claims(cursor, task_id, user, request_ip())
            conn.commit()
            return jsonify({"success": True, "task": task, "idempotent": task["idempotent"]})
        except Exception as exc:
            conn.rollback()
            return _error_response(exc)
        finally:
            conn.close()

    @app.get("/api/temporary-inventory/transfers/<int:task_id>/settlement")
    def get_temporary_transfer_settlement(task_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_task_visibility(cursor, actor(cursor))
            summary = settlement_summary(cursor, task_id, user)
            return jsonify({"success": True, "settlement": summary})
        except Exception as exc:
            return _error_response(exc)
        finally:
            conn.close()

