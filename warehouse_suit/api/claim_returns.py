"""Personal claim history and return endpoints."""

from flask import jsonify, request

from warehouse_suit.claim_return_service import complete_claim_return, my_claims, request_claim_return


def register_claim_return_routes(app, *, get_db, current_user_provider):
    def require_user(cursor):
        user = current_user_provider(cursor)
        if not user:
            raise PermissionError("请先登录")
        return user

    @app.get("/api/my-claims")
    def list_my_claims():
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_user(cursor)
            return jsonify(my_claims(cursor, user["id"], request.args.get("page", 1),
                                     request.args.get("q", "")))
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/my-claims/<int:outbound_record_id>/return")
    def submit_claim_return(outbound_record_id):
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_user(cursor)
            data = request.get_json() or {}
            payload = dict(data)
            payload.setdefault("source_kind", "claim_outbound")
            payload.setdefault("source_id", outbound_record_id)
            result = request_claim_return(cursor, user, payload)
            conn.commit()
            return jsonify({"success": True, **result})
        except PermissionError as exc:
            conn.rollback()
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            conn.rollback()
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/claim-returns/<int:form_id>/inbound")
    def inbound_claim_return(form_id):
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_user(cursor)
            data = request.get_json() or {}
            result = complete_claim_return(
                cursor,
                user,
                form_id,
                data.get("decision") or "同意",
                data.get("remark") or "",
            )
            conn.commit()
            return jsonify({"success": True, **result})
        except PermissionError as exc:
            conn.rollback()
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            conn.rollback()
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()
