# -*- coding: utf-8 -*-
"""Frontend entry and authentication route registration."""

import os

from flask import jsonify, redirect, render_template, render_template_string, request, session

from warehouse_suit import __version__
from warehouse_suit.auth_service import authenticate_user, public_user, update_own_password
from warehouse_suit.auth_templates import CHANGE_PASSWORD_HTML, LOGIN_HTML
from warehouse_suit.attachments import available_batch_material_photo_map
from warehouse_suit.material_repository import fetch_layers, material_query
from warehouse_suit.settings import password_policy, password_policy_text


def register_auth_routes(app, *, material_system, base_dir, db_path, get_db, current_user_provider):
    """Register entry, auth, session, and basic bootstrap endpoints."""

    def index():
        static_paths = [
            os.path.join(base_dir, "static", "system.js"),
            os.path.join(base_dir, "static", "js", "system-core.js"),
            os.path.join(base_dir, "static", "js", "system-state.js"),
            os.path.join(base_dir, "static", "js", "card-system.js"),
            os.path.join(base_dir, "static", "js", "camera", "attachment-camera.js"),
            os.path.join(base_dir, "static", "js", "attachment-upload.js"),
            os.path.join(base_dir, "static", "js", "inventory-list-controls.js"),
            os.path.join(base_dir, "static", "css", "system.css"),
            os.path.join(base_dir, "static", "css", "card-system.css"),
            os.path.join(base_dir, "static", "css", "dashboard.css"),
            os.path.join(base_dir, "static", "js", "dashboard.js"),
        ]
        mtimes = []
        for static_path in static_paths:
            try:
                mtimes.append(int(os.path.getmtime(static_path)))
            except OSError:
                pass
        static_version = max(mtimes) if mtimes else 1
        return render_template("index.html", material_system=material_system, static_version=static_version)

    def dashboard_fragment():
        return render_template("dashboard_fragment.html")

    def login_page():
        if not material_system:
            return redirect("/")
        error = ""
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            ok, user_or_error = authenticate_user(username, password)
            if ok:
                session["user_id"] = user_or_error["id"]
                return redirect("/")
            error = user_or_error
        return render_template_string(LOGIN_HTML, error=error)

    def change_password_page():
        if not session.get("user_id"):
            return redirect("/login")
        conn = get_db()
        cursor = conn.cursor()
        policy = password_policy(cursor)
        error = ""
        success = ""
        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            try:
                update_own_password(cursor, int(session["user_id"]), current_password, new_password, confirm_password)
                conn.commit()
                success = "密码已修改，请继续使用系统。"
                conn.close()
                return redirect("/")
            except Exception as exc:
                conn.rollback()
                error = str(exc)
        conn.close()
        return render_template_string(
            CHANGE_PASSWORD_HTML,
            error=error,
            success=success,
            policy_text=password_policy_text(policy),
        )

    def api_login():
        data = request.get_json(force=True)
        ok, user_or_error = authenticate_user(data.get("username", ""), data.get("password", ""))
        if not ok:
            return jsonify({"success": False, "error": user_or_error}), 401
        session["user_id"] = user_or_error["id"]
        return jsonify({"success": True, "user": public_user(user_or_error)})

    def api_change_password():
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "请先登录"}), 401
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            update_own_password(
                cursor,
                int(session["user_id"]),
                data.get("current_password") or "",
                data.get("new_password") or "",
                data.get("confirm_password") or "",
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    def api_logout():
        session.clear()
        return jsonify({"success": True})

    def api_session():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor) if session.get("user_id") else None
        conn.close()
        return jsonify({"authenticated": bool(user), "user": public_user(user) if user else None})

    def bootstrap():
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM shelves ORDER BY warehouse_type, id")
        shelves = []
        for row in cursor.fetchall():
            shelf = dict(row)
            shelf["layers"] = fetch_layers(cursor, shelf["id"])
            shelves.append(shelf)

        sql, params = material_query()
        cursor.execute(sql, params)
        materials = [dict(row) for row in cursor.fetchall()]
        photo_map = available_batch_material_photo_map(cursor)
        for material in materials:
            material.update(photo_map.get(int(material["id"] or 0), {}))
        conn.close()
        return jsonify({"shelves": shelves, "materials": materials, "db_file": os.path.basename(db_path), "version": __version__})

    app.add_url_rule("/", "index", index)
    app.add_url_rule("/api/dashboard/view", "dashboard_fragment", dashboard_fragment, methods=["GET"])
    app.add_url_rule("/login", "login_page", login_page, methods=["GET", "POST"])
    app.add_url_rule("/change-password", "change_password_page", change_password_page, methods=["GET", "POST"])
    app.add_url_rule("/api/login", "api_login", api_login, methods=["POST"])
    app.add_url_rule("/api/change-password", "api_change_password", api_change_password, methods=["POST"])
    app.add_url_rule("/api/logout", "api_logout", api_logout, methods=["POST"])
    app.add_url_rule("/api/session", "api_session", api_session, methods=["GET"])
    app.add_url_rule("/api/bootstrap", "bootstrap", bootstrap, methods=["GET"])
