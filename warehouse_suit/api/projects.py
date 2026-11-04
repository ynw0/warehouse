"""项目设置接口。"""

from flask import jsonify, request

from warehouse_suit.project_service import delete_project, list_projects, save_project, user_project_codes
from warehouse_suit.workflow_service import require_permission


def register_project_routes(app, *, get_db, current_user_provider):
    @app.get("/api/projects")
    def get_projects():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "view_projects")
            scope = request.args.get("scope") or "active"
            rows = list_projects(cursor, active_only=scope != "all")
            if request.args.get("mine") == "1":
                allowed = set(user_project_codes(cursor, user["id"]))
                rows = [row for row in rows if row["code"] in allowed]
            conn.commit()
            return jsonify({"items": rows})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/projects")
    def post_project():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_projects")
            project_id = save_project(cursor, request.get_json(force=True) or {})
            conn.commit()
            return jsonify({"success": True, "id": project_id})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/projects/<int:project_id>")
    def update_project(project_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_projects")
            save_project(cursor, request.get_json(force=True) or {}, project_id)
            conn.commit()
            return jsonify({"success": True, "id": project_id})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.delete("/api/projects/<int:project_id>")
    def remove_project(project_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_projects")
            delete_project(cursor, project_id)
            conn.commit()
            return jsonify({"success": True})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()
