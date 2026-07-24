# -*- coding: utf-8 -*-
import json
import os
import secrets
import sqlite3
import threading
from html import escape

from flask import Flask, g, has_request_context, jsonify, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from warehouse_suit import __version__
from warehouse_suit.api import (
    register_acceptance_routes,
    register_ai_routes,
    register_auth_routes,
    register_attachment_routes,
    register_backup_routes,
    register_borrow_routes,
    register_claim_routes,
    register_history_routes,
    register_extended_routes,
    register_material_routes,
    register_notification_routes,
    register_production_routes,
    register_recycle_routes,
    register_shelf_routes,
    register_stock_routes,
    register_system_bootstrap_routes,
    register_system_log_routes,
    register_system_settings_routes,
    register_system_user_routes,
    register_temporary_inventory_routes,
    register_temporary_transfer_routes,
    register_workflow_routes,
)
from warehouse_suit.auth_service import configure_auth_service
from warehouse_suit.backup_service import configure_backup_service, start_background_services
from warehouse_suit.backup_utils import configure_backup_paths
from warehouse_suit.content import markdownish_to_html, read_text_prefix
from warehouse_suit.database_init import configure_database_provider, init_db
from warehouse_suit.db import connect_db, now_text, row_to_dict, today_text
from warehouse_suit.inventory_service import (
    configure_material_upsert_provider,
    get_item_change_history,
    save_borrow_change,
)
from warehouse_suit.material_service import upsert_material_master
from warehouse_suit.maintenance import maintenance_enabled
from warehouse_suit.permissions import PERMISSION_KEYS
from warehouse_suit.runtime import (
    BASE_DIR as PROJECT_BASE_DIR,
    DEFAULT_BACKUP_DIR as RUNTIME_BACKUP_DIR,
    DEFAULT_LOG_PATH,
    SECRET_KEY_PATH,
    ensure_runtime_dirs,
    find_database_path,
)
from warehouse_suit.settings import data_validation_settings, default_data_validation_settings, get_setting, parse_json
from warehouse_suit.todo_service import configure_todo_service, notify_todos_changed
from warehouse_suit.validation import (
    configure_validation_settings_provider,
    positive_int_value,
    validate_project_code,
    validation_rule_enabled,
)
from warehouse_suit.workflow_service import configure_workflow_service


app = Flask(__name__)
BASE_DIR = str(PROJECT_BASE_DIR)
ensure_runtime_dirs()
DB_PATH = find_database_path()
AI_ENABLED = os.environ.get("ENABLE_AI", "1") != "0"
MATERIAL_SYSTEM = os.environ.get("MATERIAL_SYSTEM", "1") != "0"
DEFAULT_SKILL_PATH = os.path.join(BASE_DIR, "wuliao_skill", "SKILL.md") if os.path.exists(os.path.join(BASE_DIR, "wuliao_skill", "SKILL.md")) else os.path.join(os.path.dirname(BASE_DIR), "wuliao skill240424", "SKILL.md")
DEFAULT_AI_BASE_URL = os.environ.get("AI_BASE_URL", "http://192.168.0.5:1234/v1")
DEFAULT_AI_MODEL = os.environ.get("AI_MODEL", "")
DEFAULT_AI_API_KEY = os.environ.get("AI_API_KEY", "")
DEFAULT_BACKUP_DIR = os.environ.get("WAREHOUSE_BACKUP_DIR", str(RUNTIME_BACKUP_DIR))
configure_backup_paths(BASE_DIR, DEFAULT_BACKUP_DIR)
SSE_CLIENTS = set()
SSE_LOCK = threading.Lock()
app.config["VERSION"] = __version__


def load_secret_key():
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    secret_path = os.environ.get("WAREHOUSE_SECRET_KEY_FILE", str(SECRET_KEY_PATH))
    legacy_secret_path = os.path.join(BASE_DIR, ".secret_key")
    try:
        if not os.path.exists(secret_path) and os.path.exists(legacy_secret_path):
            secret_path = legacy_secret_path
        if os.path.exists(secret_path):
            with open(secret_path, "r", encoding="utf-8") as file:
                value = file.read().strip()
                if value:
                    return value
        value = secrets.token_urlsafe(48)
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        with open(secret_path, "w", encoding="utf-8") as file:
            file.write(value)
        try:
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
        return value
    except OSError:
        return "warehouse-material-system-local-secret"


app.secret_key = load_secret_key()


def resolve_skill_path(path):
    local_path = os.path.join(BASE_DIR, "wuliao_skill", "SKILL.md")
    if os.path.exists(local_path):
        return local_path
    if path and os.path.exists(path):
        return path
    return path or DEFAULT_SKILL_PATH




def coding_rules_html(cursor):
    skill_path = resolve_skill_path(get_setting(cursor, "ai_skill_path", DEFAULT_SKILL_PATH))
    skill_dir = os.path.dirname(skill_path) if skill_path else os.path.join(BASE_DIR, "wuliao_skill")
    sections = [
        ("codeingrules / coding-rules", os.path.join(skill_dir, "references", "coding-rules.md")),
        ("后学习到的编码规则", os.path.join(skill_dir, "references", "learned-category-rules.md")),
    ]
    html_parts = []
    for title, path in sections:
        text = read_text_prefix(path, 80000) or "暂无内容"
        html_parts.append(f"<section><h2>{escape(title)}</h2>{markdownish_to_html(text)}</section>")
    return "\n".join(html_parts)



def wants_json_response():
    return request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or "")


def auth_required_path():
    if not MATERIAL_SYSTEM:
        return False
    allowed = {
        "/login",
        "/change-password",
        "/api/login",
        "/api/change-password",
        "/api/session",
        "/api/logout",
        "/favicon.ico",
    }
    if request.path in allowed or request.path.startswith("/static/"):
        return False
    return True


def password_change_allowed_path():
    allowed = {
        "/change-password",
        "/api/change-password",
        "/api/logout",
        "/api/session",
        "/favicon.ico",
    }
    return request.path in allowed or request.path.startswith("/static/")


@app.before_request
def reject_writes_during_maintenance():
    """Keep reads available while an offline upgrade holds the write window."""

    if request.method in {"GET", "HEAD", "OPTIONS"} or not maintenance_enabled():
        return None
    if wants_json_response():
        return jsonify({"success": False, "error": "系统维护中，请稍后再试"}), 503
    return "系统维护中，请稍后再试", 503


@app.before_request
def require_login_for_material_system():
    if auth_required_path() and not session.get("user_id"):
        if wants_json_response():
            return jsonify({"success": False, "error": "请先登录"}), 401
        return redirect("/login")
    if MATERIAL_SYSTEM and session.get("user_id") and not password_change_allowed_path():
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT must_change_password FROM users WHERE id = ? AND is_active = 1", (int(session["user_id"]),))
            row = cursor.fetchone()
            conn.close()
            if row and int(row["must_change_password"] or 0):
                if wants_json_response():
                    return jsonify({"success": False, "error": "首次登录或密码重置后必须先修改密码", "must_change_password": True}), 403
                return redirect("/change-password")
        except sqlite3.Error:
            pass


@app.errorhandler(PermissionError)
def handle_permission_error(exc):
    if wants_json_response():
        return jsonify({"success": False, "error": str(exc)}), 403
    return str(exc), 403


SENSITIVE_AUDIT_KEYS = {"password", "api_key", "key", "secret", "token"}


def sanitize_for_audit(value, depth=0):
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(secret in key_text for secret in SENSITIVE_AUDIT_KEYS):
                clean[key] = "***"
            else:
                clean[key] = sanitize_for_audit(item, depth + 1)
        return clean
    if isinstance(value, list):
        return [sanitize_for_audit(item, depth + 1) for item in value[:50]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


def audit_target_from_path(path):
    parts = [part for part in str(path or "").split("/") if part]
    target_type = parts[1] if len(parts) > 1 and parts[0] == "api" else (parts[0] if parts else "")
    target_id = ""
    for part in parts[2:]:
        if str(part).isdigit():
            target_id = part
            break
    return target_type, target_id


@app.after_request
def audit_mutating_requests(response):
    try:
        if request.method not in {"POST", "PUT", "DELETE"} or not request.path.startswith("/api/"):
            return response
        if request.path in {"/api/ai/chat", "/api/ai/models"}:
            return response
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        payload = {}
        if request.is_json:
            payload = request.get_json(silent=True) or {}
        elif request.form:
            payload = dict(request.form)
        target_type, target_id = audit_target_from_path(request.path)
        cursor.execute(
            """
            INSERT INTO audit_logs
                (user_id, username, action, target_type, target_id, summary, data_json, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.get("id") if user else None,
                user.get("username") if user else str((payload or {}).get("username") or ""),
                f"{request.method} {request.path}",
                target_type,
                target_id,
                f"HTTP {response.status_code}",
                json.dumps({"status": response.status_code, "payload": sanitize_for_audit(payload)}, ensure_ascii=False),
                request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                now_text(),
            ),
        )
        conn.commit()
        conn.close()
        if response.status_code < 400:
            notify_todos_changed()
    except Exception:
        pass
    return response


def get_db():
    return connect_db(DB_PATH)


def sse_clients_snapshot():
    with SSE_LOCK:
        return list(SSE_CLIENTS)


configure_database_provider(get_db)
configure_backup_service(get_db, lambda: DB_PATH, DEFAULT_BACKUP_DIR)
configure_auth_service(get_db)


def current_user(cursor):
    user_id = session.get("user_id")
    if not user_id and not MATERIAL_SYSTEM:
        user_id = request.headers.get("X-User-Id") or request.args.get("user_id")
    if user_id:
        cursor.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (int(user_id),))
    else:
        cursor.execute("SELECT * FROM users WHERE username = 'admin' AND is_active = 1")
    user = row_to_dict(cursor.fetchone())
    if not user:
        return None
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
    user["roles"] = [dict(row) for row in cursor.fetchall()]
    user["role_codes"] = [role["code"] for role in user["roles"]]
    return user


def user_by_id(cursor, user_id):
    if not user_id:
        return None
    cursor.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (int(user_id),))
    user = row_to_dict(cursor.fetchone())
    if not user:
        return None
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
    user["roles"] = [dict(row) for row in cursor.fetchall()]
    user["role_codes"] = [role["code"] for role in user["roles"]]
    return user

configure_todo_service(get_db, user_by_id, sse_clients_snapshot)
configure_workflow_service(current_user, user_by_id)
register_history_routes(app, get_db=get_db, current_user_provider=current_user)
register_extended_routes(app, get_db=get_db, current_user_provider=current_user, notify_todos_changed=notify_todos_changed)
register_notification_routes(app, get_db=get_db, current_user_provider=current_user, sse_lock=SSE_LOCK, sse_clients=SSE_CLIENTS)


def require_any_role(cursor, *role_codes):
    user = current_user(cursor)
    if not user:
        raise PermissionError("用户不存在或已停用")
    if "admin" in user["role_codes"] or not role_codes or any(role in user["role_codes"] for role in role_codes):
        return user
    raise PermissionError("当前账号没有权限办理该操作")


configure_material_upsert_provider(upsert_material_master)

def active_data_validation_settings():
    if not has_request_context():
        return default_data_validation_settings()
    cached = getattr(g, "_data_validation_settings", None)
    if cached:
        return cached
    conn = None
    try:
        conn = get_db()
        settings = data_validation_settings(conn.cursor())
    except Exception:
        settings = default_data_validation_settings()
    finally:
        if conn:
            conn.close()
    g._data_validation_settings = settings
    return settings


configure_validation_settings_provider(active_data_validation_settings)
















def optional_active_user_id(cursor, value, label="人员"):
    if value is None or str(value).strip() == "":
        return None
    user_id = positive_int_value(value, label)
    if not validation_rule_enabled("maker_user"):
        return user_id
    cursor.execute("SELECT id FROM users WHERE id = ? AND is_active = 1", (user_id,))
    if not cursor.fetchone():
        raise ValueError(f"{label}不存在或已停用")
    return user_id








register_auth_routes(
    app,
    material_system=MATERIAL_SYSTEM,
    base_dir=BASE_DIR,
    db_path=DB_PATH,
    get_db=get_db,
    current_user_provider=current_user,
)
register_shelf_routes(app, get_db=get_db)
register_material_routes(app, get_db=get_db)
register_attachment_routes(app, get_db=get_db, current_user_provider=current_user)
register_backup_routes(app, get_db=get_db, require_admin=require_any_role, init_database=init_db, notify_changed=notify_todos_changed)
register_recycle_routes(app, get_db=get_db)
register_system_bootstrap_routes(app, get_db=get_db, current_user_provider=current_user, ai_enabled=AI_ENABLED, permission_keys=PERMISSION_KEYS)
register_system_settings_routes(app, get_db=get_db, require_admin=require_any_role, permission_keys=PERMISSION_KEYS)
register_system_log_routes(app, get_db=get_db, require_admin=require_any_role, base_dir=BASE_DIR, default_log_path=DEFAULT_LOG_PATH, coding_rules_provider=coding_rules_html)
register_system_user_routes(app, get_db=get_db, require_admin=require_any_role)
register_temporary_inventory_routes(app, get_db=get_db, current_user_provider=current_user)
register_temporary_transfer_routes(app, get_db=get_db, current_user_provider=current_user)
register_workflow_routes(app, get_db=get_db, current_user_provider=current_user, notify_todos_changed=notify_todos_changed)
register_acceptance_routes(app, get_db=get_db, current_user_provider=current_user)
register_claim_routes(app, get_db=get_db, current_user_provider=current_user)
register_borrow_routes(app, get_db=get_db, current_user_provider=current_user)
register_production_routes(app, get_db=get_db, current_user_provider=current_user, optional_active_user_id_provider=optional_active_user_id)
register_stock_routes(app, get_db=get_db, current_user_provider=current_user)
register_ai_routes(
    app,
    get_db=get_db,
    current_user_provider=current_user,
    require_role_provider=require_any_role,
    ai_enabled=AI_ENABLED,
    default_ai_base_url=DEFAULT_AI_BASE_URL,
    default_ai_model=DEFAULT_AI_MODEL,
    default_ai_api_key=DEFAULT_AI_API_KEY,
    default_skill_path=DEFAULT_SKILL_PATH,
    resolve_skill_path_provider=resolve_skill_path,
    base_dir=BASE_DIR,
)







init_db()
start_background_services()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host=host, port=port)
