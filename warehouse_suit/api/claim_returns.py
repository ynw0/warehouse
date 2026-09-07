"""Personal claim history and return endpoints."""

from flask import jsonify, request

from warehouse_suit.claim_return_service import my_claims, return_claim


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
            result = return_claim(cursor, user["id"], outbound_record_id,
                                  data.get("quantity"), data.get("operation_key"))
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
