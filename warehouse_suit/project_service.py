"""项目设置：代号、一名负责人、成员。办公固定存在。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text
from warehouse_suit.purchase_project import OFFICE_PURCHASE_PROJECT
from warehouse_suit.settings import get_setting, parse_json, set_setting, workflow_settings
from warehouse_suit.validation import validate_project_code


# 单据和库存上记下的项目代号。改代号时这些字段一起改，避免旧代号再被当成另一个项目。
_PROJECT_CODE_COLUMNS = (
    ("material_batches", "purchase_project"),
    ("workflow_items", "purchase_project"),
    ("materials", "purchase_project"),
    ("finished_acceptances", "project_code"),
    ("semifinished_acceptances", "project_code"),
)
_PROJECT_JSON_KEYS = ("purchase_project", "project_code")


def _has_table(cursor, name):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
    return bool(cursor.fetchone())


def _has_column(cursor, table, column):
    cursor.execute(f'PRAGMA table_info("{table}")')
    return column in {row[1] for row in cursor.fetchall()}


def _json_doc(column):
    return f"CASE WHEN json_valid(COALESCE({column}, '')) THEN {column} ELSE '{{}}' END"


def _project_code_taken(cursor, code, except_id=None):
    """大小写不同也视为同一个代号，避免 zg2516 和 ZG2516 并存。"""
    sql = "SELECT id FROM projects WHERE code = ? COLLATE NOCASE"
    params = [code]
    if except_id:
        sql += " AND id <> ?"
        params.append(int(except_id))
    cursor.execute(sql, params)
    return bool(cursor.fetchone())


def _stored_project_codes(cursor):
    stored = parse_json(get_setting(cursor, "workflow_settings", "{}"), {})
    if not isinstance(stored, dict):
        return {}, []
    codes = stored.get("project_codes") or []
    if isinstance(codes, str):
        codes = [item.strip() for item in codes.replace("；", ",").replace("\n", ",").split(",") if item.strip()]
    if not isinstance(codes, list):
        codes = []
    return stored, [str(item or "").strip() for item in codes if str(item or "").strip()]


def _write_project_codes(cursor, stored, codes):
    stored["project_codes"] = codes
    set_setting(cursor, "workflow_settings", json.dumps(stored, ensure_ascii=False))


def _replace_setting_code(cursor, old_code, new_code=None):
    """旧流程设置里的代号清单跟着改。留着旧代号时，刷新项目列表会把它重新加回来。"""
    stored, codes = _stored_project_codes(cursor)
    if not stored:
        return
    updated = []
    for code in codes:
        text = new_code if code == old_code and new_code else "" if code == old_code else code
        if text and text not in updated:
            updated.append(text)
    _write_project_codes(cursor, stored, updated)


def _rewrite_project_code(cursor, old_code, new_code):
    """把已经记在单据和批次上的旧代号改成新代号。"""
    if old_code == new_code:
        return
    for table, column in _PROJECT_CODE_COLUMNS:
        if not _has_table(cursor, table) or not _has_column(cursor, table, column):
            continue
        cursor.execute(
            f"UPDATE {table} SET {column} = ? WHERE TRIM(COALESCE({column}, '')) = ?",
            (new_code, old_code),
        )
    for table in ("workflow_forms", "workflow_items"):
        if not _has_table(cursor, table) or not _has_column(cursor, table, "data_json"):
            continue
        doc = _json_doc("data_json")
        for key in _PROJECT_JSON_KEYS:
            cursor.execute(
                f"""
                UPDATE {table}
                SET data_json = json_set({doc}, '$.{key}', ?)
                WHERE TRIM(COALESCE(json_extract({doc}, '$.{key}'), '')) = ?
                """,
                (new_code, old_code),
            )
    _replace_setting_code(cursor, old_code, new_code)


def _project_in_use(cursor, code):
    checks = (
        ("material_batches", "purchase_project"),
        ("workflow_items", "purchase_project"),
        ("materials", "purchase_project"),
    )
    for table, column in checks:
        cursor.execute(
            f"SELECT 1 FROM {table} WHERE TRIM(COALESCE({column}, '')) = ? LIMIT 1",
            (code,),
        )
        if cursor.fetchone():
            return True
    return False


def ensure_projects_from_settings(cursor):
    """把流程设置里尚未入库的代号补进项目表，已停用的不重新启用。"""
    timestamp = now_text()
    cursor.execute(
        """
        INSERT OR IGNORE INTO projects (code, owner_user_id, active, created_at, updated_at)
        VALUES (?, NULL, 1, ?, ?)
        """,
        (OFFICE_PURCHASE_PROJECT, timestamp, timestamp),
    )
    for raw in workflow_settings(cursor).get("project_codes") or []:
        code = str(raw or "").strip()
        if not code or code == OFFICE_PURCHASE_PROJECT:
            continue
        cursor.execute(
            """
            INSERT INTO projects (code, owner_user_id, active, created_at, updated_at)
            SELECT ?, NULL, 1, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM projects WHERE code = ? COLLATE NOCASE)
            """,
            (code, timestamp, timestamp, code),
        )


def list_projects(cursor, *, active_only=False):
    ensure_projects_from_settings(cursor)
    sql = "SELECT * FROM projects"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY CASE WHEN code = ? THEN 0 ELSE 1 END, code"
    cursor.execute(sql, (OFFICE_PURCHASE_PROJECT,))
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return rows
    ids = [int(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    cursor.execute(
        f"""
        SELECT pm.project_id, pm.user_id, COALESCE(u.display_name, u.username, '') AS display_name
        FROM project_members pm
        LEFT JOIN users u ON u.id = pm.user_id
        WHERE pm.project_id IN ({placeholders})
        ORDER BY pm.user_id
        """,
        ids,
    )
    members = {}
    for row in cursor.fetchall():
        members.setdefault(int(row["project_id"]), []).append(
            {"user_id": int(row["user_id"]), "display_name": row["display_name"] or ""}
        )
    owner_ids = [int(row["owner_user_id"]) for row in rows if row.get("owner_user_id")]
    owners = {}
    if owner_ids:
        placeholders = ",".join("?" for _ in owner_ids)
        cursor.execute(
            f"SELECT id, COALESCE(display_name, username, '') AS display_name FROM users WHERE id IN ({placeholders})",
            owner_ids,
        )
        owners = {int(row["id"]): row["display_name"] or "" for row in cursor.fetchall()}
    for row in rows:
        row["members"] = [] if row["code"] == OFFICE_PURCHASE_PROJECT else members.get(int(row["id"]), [])
        row["member_ids"] = [item["user_id"] for item in row["members"]]
        row["owner_name"] = owners.get(int(row["owner_user_id"] or 0), "")
        row["active"] = bool(row.get("active"))
        row["in_use"] = _project_in_use(cursor, row["code"])
    return rows


def active_project_codes(cursor):
    ensure_projects_from_settings(cursor)
    cursor.execute(
        """
        SELECT code FROM projects
        WHERE active = 1
        ORDER BY CASE WHEN code = ? THEN 0 ELSE 1 END, code
        """,
        (OFFICE_PURCHASE_PROJECT,),
    )
    codes = [str(row["code"]) for row in cursor.fetchall()]
    if OFFICE_PURCHASE_PROJECT not in codes:
        codes.insert(0, OFFICE_PURCHASE_PROJECT)
    return codes


def user_project_codes(cursor, user_id, *, include_all_active=False):
    codes = []
    for row in list_projects(cursor, active_only=True):
        if include_all_active:
            codes.append(row["code"])
            continue
        if row["code"] == OFFICE_PURCHASE_PROJECT:
            codes.append(row["code"])
            continue
        member_ids = set(row["member_ids"])
        if int(user_id or 0) == int(row.get("owner_user_id") or 0) or int(user_id or 0) in member_ids:
            codes.append(row["code"])
    return codes


def require_project_access(cursor, user, code):
    text = str(code or "").strip()
    if text not in active_project_codes(cursor):
        raise ValueError("采购申请项目不在可选范围内")
    if text == OFFICE_PURCHASE_PROJECT:
        return text
    allowed = set(user_project_codes(cursor, user.get("id")))
    if text not in allowed and "admin" not in set(user.get("role_codes") or []):
        raise ValueError("只能选择自己负责或参与的项目")
    return text


def project_owner_id(cursor, code):
    cursor.execute("SELECT owner_user_id FROM projects WHERE code = ?", (str(code or "").strip(),))
    row = cursor.fetchone()
    if not row or not row["owner_user_id"]:
        return None
    return int(row["owner_user_id"])


def save_project(cursor, payload, project_id=None):
    code = validate_project_code(payload.get("code"))
    if code == OFFICE_PURCHASE_PROJECT and project_id is None:
        raise ValueError("办公项目已存在")
    owner_id = payload.get("owner_user_id")
    owner_id = int(owner_id) if owner_id else None
    if owner_id:
        cursor.execute("SELECT id FROM users WHERE id = ?", (owner_id,))
        if not cursor.fetchone():
            raise ValueError("项目负责人不存在")
    active = 1 if payload.get("active", True) else 0
    member_ids = []
    if code != OFFICE_PURCHASE_PROJECT:
        for raw in payload.get("member_ids") or []:
            user_id = int(raw)
            if user_id and user_id not in member_ids and user_id != owner_id:
                member_ids.append(user_id)
    timestamp = now_text()
    if project_id:
        cursor.execute("SELECT * FROM projects WHERE id = ?", (int(project_id),))
        current = cursor.fetchone()
        if not current:
            raise ValueError("项目不存在")
        if current["code"] == OFFICE_PURCHASE_PROJECT and code != OFFICE_PURCHASE_PROJECT:
            raise ValueError("办公项目代号不能修改")
        if current["code"] != code:
            if _project_code_taken(cursor, code, except_id=current["id"]):
                raise ValueError("项目代号已存在")
            _rewrite_project_code(cursor, current["code"], code)
        cursor.execute(
            """
            UPDATE projects
            SET code = ?, owner_user_id = ?, active = ?, updated_at = ?
            WHERE id = ?
            """,
            (code, owner_id, active, timestamp, int(project_id)),
        )
        saved_id = int(project_id)
    else:
        cursor.execute("SELECT id FROM projects WHERE code = ?", (code,))
        if cursor.fetchone() or _project_code_taken(cursor, code):
            raise ValueError("项目代号已存在")
        cursor.execute(
            """
            INSERT INTO projects (code, owner_user_id, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (code, owner_id, active, timestamp, timestamp),
        )
        saved_id = int(cursor.lastrowid)
    cursor.execute("DELETE FROM project_members WHERE project_id = ?", (saved_id,))
    if code != OFFICE_PURCHASE_PROJECT:
        for user_id in member_ids:
            cursor.execute(
                "INSERT INTO project_members (project_id, user_id) VALUES (?, ?)",
                (saved_id, user_id),
            )
    return saved_id


def delete_project(cursor, project_id):
    cursor.execute("SELECT * FROM projects WHERE id = ?", (int(project_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("项目不存在")
    if row["code"] == OFFICE_PURCHASE_PROJECT:
        raise ValueError("办公项目不能删除")
    if int(row["active"] or 0):
        raise ValueError("只有停用的项目可以删除")
    _replace_setting_code(cursor, row["code"])
    cursor.execute("DELETE FROM project_members WHERE project_id = ?", (int(project_id),))
    cursor.execute("DELETE FROM projects WHERE id = ?", (int(project_id),))
