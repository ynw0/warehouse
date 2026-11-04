# -*- coding: utf-8 -*-
"""System user and department route registration."""

import csv
import io

from flask import Response, jsonify, request
from werkzeug.security import generate_password_hash

from warehouse_suit.db import now_text
from warehouse_suit.settings import password_policy, validate_password_policy
from warehouse_suit.user_admin import remove_department, reset_user_password, save_user_account, set_user_active, write_department, delete_user_account
from warehouse_suit.workflow_service import require_permission


def register_system_user_routes(app, *, get_db, require_admin):
    """Register user and department management endpoints."""

    def system_users():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        cursor.execute(
            """
            SELECT u.id, u.username, u.display_name, u.department, u.is_active, u.must_change_password,
                   u.created_at, u.updated_at,
                   GROUP_CONCAT(r.code) AS role_codes, GROUP_CONCAT(r.name) AS role_names
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            GROUP BY u.id
            ORDER BY u.id
            """
        )
        users = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM roles ORDER BY id")
        roles = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM departments ORDER BY id")
        departments = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"users": users, "roles": roles, "departments": departments})

    def list_departments():
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM departments ORDER BY id")
        departments = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(departments)

    def save_department():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            department_id = int(data.get("id") or 0)
            if department_id:
                require_permission(cursor, "edit_department")
            else:
                require_admin(cursor, "admin")
            department_id = write_department(cursor, data)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "department_id": department_id})

    def delete_department(department_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "edit_department")
            remove_department(cursor, user, department_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    def save_system_user():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            user_id = save_user_account(cursor, data)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "user_id": user_id})

    def reset_system_user_password(user_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            reset_user_password(cursor, user_id, data.get("password"))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    def download_users_template():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["username", "display_name", "password", "department", "roles", "is_active"])
        writer.writerow(["zhangsan", "张三", "123456", "研发部", "user,warehouse", "1"])
        content = "\ufeff" + output.getvalue()
        return Response(
            content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=users_import_template.csv"},
        )

    def import_system_users():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            file = request.files.get("file")
            if not file:
                raise ValueError("请先选择用户导入模板文件")
            text = file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            imported = 0
            updated = 0
            errors = []
            for index, row in enumerate(reader, start=2):
                username = str(row.get("username") or row.get("账号") or "").strip()
                display_name = str(row.get("display_name") or row.get("姓名") or username).strip()
                if not username or not display_name:
                    errors.append("第 {} 行：账号和姓名不能为空".format(index))
                    continue
                department = str(row.get("department") or row.get("部门") or "").strip()
                policy = password_policy(cursor)
                imported_password = str(row.get("password") or row.get("密码") or "").strip()
                raw_roles = str(row.get("roles") or row.get("角色") or "user")
                role_codes = [item.strip() for item in raw_roles.replace("；", ",").replace(";", ",").split(",") if item.strip()] or ["user"]
                is_active_text = str(row.get("is_active") or row.get("启用") or "1").strip().lower()
                is_active = 0 if is_active_text in {"0", "false", "no", "否", "停用"} else 1

                cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                existing = cursor.fetchone()
                if existing:
                    user_id = existing["id"]
                    if imported_password:
                        try:
                            validate_password_policy(imported_password, policy)
                        except ValueError as exc:
                            errors.append("第 {} 行：{}".format(index, exc))
                            continue
                    cursor.execute(
                        "UPDATE users SET display_name = ?, department = ?, is_active = ?, updated_at = ? WHERE id = ?",
                        (display_name, department, is_active, now_text(), user_id),
                    )
                    if imported_password:
                        cursor.execute(
                            "UPDATE users SET password = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
                            (generate_password_hash(imported_password), 1 if policy.get("force_change_on_first_login") else 0, now_text(), user_id),
                        )
                    updated += 1
                else:
                    password = imported_password or "123456"
                    if imported_password:
                        try:
                            validate_password_policy(password, policy)
                        except ValueError as exc:
                            errors.append("第 {} 行：{}".format(index, exc))
                            continue
                    cursor.execute(
                        """
                        INSERT INTO users (username, display_name, department, password, must_change_password, is_active, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (username, display_name, department, generate_password_hash(password), 1 if policy.get("force_change_on_first_login") else 0, is_active, now_text()),
                    )
                    user_id = cursor.lastrowid
                    imported += 1

                cursor.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
                for code in role_codes:
                    cursor.execute("SELECT id FROM roles WHERE code = ? OR name = ?", (code, code))
                    role = cursor.fetchone()
                    if role:
                        cursor.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role[0]))
                    else:
                        errors.append("第 {} 行：角色不存在 {}".format(index, code))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "imported": imported, "updated": updated, "errors": errors})

    def delete_system_user(user_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_admin(cursor, "admin")
            delete_user_account(cursor, user, user_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    def set_system_user_status(user_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_admin(cursor, "admin")
            set_user_active(cursor, user, user_id, data.get("is_active", True))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})

    app.add_url_rule("/api/system/users", "system_users", system_users, methods=["GET"])
    app.add_url_rule("/api/system/departments", "list_departments", list_departments, methods=["GET"])
    app.add_url_rule("/api/system/departments", "save_department", save_department, methods=["POST"])
    app.add_url_rule("/api/system/departments/<int:department_id>", "delete_department", delete_department, methods=["DELETE"])
    app.add_url_rule("/api/system/users", "save_system_user", save_system_user, methods=["POST"])
    app.add_url_rule("/api/system/users/<int:user_id>/reset-password", "reset_system_user_password", reset_system_user_password, methods=["POST"])
    app.add_url_rule("/api/system/users/template", "download_users_template", download_users_template, methods=["GET"])
    app.add_url_rule("/api/system/users/import", "import_system_users", import_system_users, methods=["POST"])
    app.add_url_rule("/api/system/users/<int:user_id>", "delete_system_user", delete_system_user, methods=["DELETE"])
    app.add_url_rule("/api/system/users/<int:user_id>/status", "set_system_user_status", set_system_user_status, methods=["POST"])
