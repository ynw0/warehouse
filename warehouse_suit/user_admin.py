# -*- coding: utf-8 -*-
"""账号、角色和部门维护。页面和物料小助手共用。"""

from werkzeug.security import generate_password_hash

from warehouse_suit.access_token import revoke_access_tokens
from warehouse_suit.db import now_text
from warehouse_suit.recycle import recycle_table_row
from warehouse_suit.settings import password_policy, validate_password_policy
from warehouse_suit.workflow_service import require_permission


def list_user_rows(cursor):
    """账号列表，不含密码。"""
    cursor.execute(
        """
        SELECT u.id, u.username, u.display_name, u.department, u.is_active,
               GROUP_CONCAT(r.code) AS role_codes, GROUP_CONCAT(r.name) AS role_names
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        LEFT JOIN roles r ON r.id = ur.role_id
        GROUP BY u.id
        ORDER BY u.id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def list_departments(cursor):
    cursor.execute("SELECT id, name, description FROM departments ORDER BY id")
    return [dict(row) for row in cursor.fetchall()]


def write_department(cursor, data):
    """新建或修改部门。权限由调用方检查。"""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("部门名称不能为空")
    department_id = int(data.get("id") or 0)
    if department_id:
        cursor.execute(
            "UPDATE departments SET name = ?, description = ?, updated_at = ? WHERE id = ?",
            (name, data.get("description") or "", now_text(), department_id),
        )
    else:
        cursor.execute(
            "INSERT INTO departments (name, description, updated_at) VALUES (?, ?, ?)",
            (name, data.get("description") or "", now_text()),
        )
        department_id = cursor.lastrowid
    return int(department_id)


def remove_department(cursor, user, department_id):
    require_permission(cursor, "edit_department")
    cursor.execute("SELECT name FROM departments WHERE id = ?", (int(department_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("部门不存在")
    recycle_table_row(cursor, "department", "departments", int(department_id), ["name"], user.get("id"))
    cursor.execute("UPDATE users SET department = ?, updated_at = ? WHERE department = ?", ("", now_text(), row["name"]))
    cursor.execute("DELETE FROM departments WHERE id = ?", (int(department_id),))
    return {"id": int(department_id), "name": row["name"]}


def save_user_account(cursor, data):
    """新建或修改账号。调用方必须已经确认是管理员。"""
    user_id = int(data.get("id") or 0)
    username = str(data.get("username") or "").strip()
    display_name = str(data.get("display_name") or username).strip()
    if not username or not display_name:
        raise ValueError("账号和姓名不能为空")
    if user_id:
        cursor.execute(
            """
            UPDATE users
            SET username = ?, display_name = ?, department = ?, is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (username, display_name, data.get("department") or "", 1 if data.get("is_active", True) else 0, now_text(), user_id),
        )
        if data.get("password"):
            policy = password_policy(cursor)
            validate_password_policy(data.get("password"), policy)
            cursor.execute(
                "UPDATE users SET password = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
                (generate_password_hash(data.get("password")), 1 if policy.get("force_change_on_first_login") else 0, now_text(), user_id),
            )
    else:
        policy = password_policy(cursor)
        supplied_password = str(data.get("password") or "")
        raw_password = supplied_password or "123456"
        if supplied_password:
            validate_password_policy(raw_password, policy)
        cursor.execute(
            """
            INSERT INTO users (username, display_name, department, password, must_change_password, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                username,
                display_name,
                data.get("department") or "",
                generate_password_hash(raw_password),
                1 if policy.get("force_change_on_first_login") else 0,
                now_text(),
            ),
        )
        user_id = cursor.lastrowid
    cursor.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    for code in data.get("roles") or ["user"]:
        cursor.execute("SELECT id FROM roles WHERE code = ?", (code,))
        role = cursor.fetchone()
        if role:
            cursor.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role[0]))
    return int(user_id)


def reset_user_password(cursor, user_id, password):
    password = str(password or "").strip()
    if not password:
        raise ValueError("请输入新密码")
    policy = password_policy(cursor)
    validate_password_policy(password, policy)
    cursor.execute("SELECT username, display_name FROM users WHERE id = ?", (int(user_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("用户不存在")
    cursor.execute(
        "UPDATE users SET password = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
        (generate_password_hash(password), 1 if policy.get("force_change_on_first_login") else 0, now_text(), int(user_id)),
    )
    revoke_access_tokens(cursor, int(user_id))
    return {"id": int(user_id), "username": row["username"], "display_name": row["display_name"]}


def delete_user_account(cursor, actor, user_id):
    user_id = int(user_id)
    if user_id == int(actor["id"]):
        raise ValueError("不能删除当前登录账号")
    cursor.execute("SELECT username, display_name FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("用户不存在")
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"id": user_id, "username": row["username"], "display_name": row["display_name"]}


def set_user_active(cursor, actor, user_id, is_active):
    user_id = int(user_id)
    active = 1 if is_active else 0
    if user_id == int(actor["id"]) and not active:
        raise ValueError("不能停用当前登录账号")
    cursor.execute("SELECT username, display_name FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("用户不存在")
    cursor.execute(
        "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
        (active, now_text(), user_id),
    )
    return {"id": user_id, "username": row["username"], "display_name": row["display_name"], "is_active": bool(active)}
