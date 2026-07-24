# -*- coding: utf-8 -*-
"""Authentication and password services."""

from werkzeug.security import check_password_hash, generate_password_hash

from warehouse_suit.db import now_text, row_to_dict
from warehouse_suit.settings import password_policy, validate_password_policy


_db_provider = None


def configure_auth_service(db_provider):
    global _db_provider
    _db_provider = db_provider


def _get_db():
    if _db_provider is None:
        raise RuntimeError("database provider is not configured")
    return _db_provider()


def authenticate_user(username, password):
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (str(username).strip(),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "账号或密码错误"
    user = dict(row)
    stored = user.get("password") or ""
    valid = False
    if stored.startswith(("scrypt:", "pbkdf2:")):
        valid = check_password_hash(stored, password)
    else:
        valid = stored == password
        if valid:
            cursor.execute("UPDATE users SET password = ?, updated_at = ? WHERE id = ?", (generate_password_hash(password), now_text(), user["id"]))
            conn.commit()
    if not valid:
        conn.close()
        return False, "账号或密码错误"
    cursor.execute(
        """
        SELECT r.code, r.name
        FROM roles r
        JOIN user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = ?
        ORDER BY r.id
        """,
        (user["id"],),
    )
    user["roles"] = [dict(item) for item in cursor.fetchall()]
    user["role_codes"] = [role["code"] for role in user["roles"]]
    conn.close()
    return True, user


def update_own_password(cursor, user_id, current_password, new_password, confirm_password):
    cursor.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (int(user_id),))
    user = row_to_dict(cursor.fetchone())
    if not user:
        raise ValueError("用户不存在或已停用")
    stored = user.get("password") or ""
    if stored.startswith(("scrypt:", "pbkdf2:")):
        valid = check_password_hash(stored, current_password)
    else:
        valid = stored == current_password
    if not valid:
        raise ValueError("当前密码不正确")
    if not new_password:
        raise ValueError("新密码不能为空")
    if new_password != confirm_password:
        raise ValueError("两次输入的新密码不一致")
    validate_password_policy(new_password, password_policy(cursor))
    cursor.execute(
        "UPDATE users SET password = ?, must_change_password = 0, password_changed_at = ?, updated_at = ? WHERE id = ?",
        (generate_password_hash(new_password), now_text(), now_text(), user_id),
    )


def public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "department": user.get("department") or "",
        "roles": user.get("roles") or [],
        "role_codes": user.get("role_codes") or [],
        "must_change_password": bool(user.get("must_change_password")),
    }
