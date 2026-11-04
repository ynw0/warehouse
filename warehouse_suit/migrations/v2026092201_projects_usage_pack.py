"""项目设置，以及批次每单位数量、用料形式。"""

from warehouse_suit.db import now_text
from warehouse_suit.purchase_project import OFFICE_PURCHASE_PROJECT
from warehouse_suit.settings import parse_json
from warehouse_suit.usage_kind import USAGE_RD


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(conn, table, column, definition):
    if column in _columns(conn, table):
        return
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {definition}')


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return bool(row)


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            owner_user_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (project_id, user_id)
        )
        """
    )
    timestamp = now_text()
    conn.execute(
        """
        INSERT OR IGNORE INTO projects (code, owner_user_id, active, created_at, updated_at)
        VALUES (?, NULL, 1, ?, ?)
        """,
        (OFFICE_PURCHASE_PROJECT, timestamp, timestamp),
    )
    stored = {}
    if _table_exists(conn, "app_settings"):
        stored = parse_json(
            (conn.execute("SELECT value FROM app_settings WHERE key = 'workflow_settings'").fetchone() or [None])[0],
            {},
        )
    codes = stored.get("project_codes") if isinstance(stored, dict) else []
    if isinstance(codes, str):
        codes = [item.strip() for item in codes.replace("；", ",").replace("\n", ",").split(",") if item.strip()]
    if not isinstance(codes, list):
        codes = []
    for raw in codes:
        code = str(raw or "").strip()
        if not code or code == OFFICE_PURCHASE_PROJECT:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO projects (code, owner_user_id, active, created_at, updated_at)
            VALUES (?, NULL, 1, ?, ?)
            """,
            (code, timestamp, timestamp),
        )

    _ensure_column(conn, "material_batches", "pack_quantity", "REAL NOT NULL DEFAULT 1")
    _ensure_column(conn, "material_batches", "arrival_unit", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "material_batches", "usage_kind", f"TEXT NOT NULL DEFAULT '{USAGE_RD}'")
    conn.execute(
        """
        UPDATE material_batches
        SET pack_quantity = 1
        WHERE pack_quantity IS NULL OR pack_quantity < 1
        """
    )
    conn.execute(
        """
        UPDATE material_batches
        SET usage_kind = ?
        WHERE TRIM(COALESCE(usage_kind, '')) = ''
        """,
        (USAGE_RD,),
    )
    if _table_exists(conn, "supply_orders"):
        _ensure_column(conn, "supply_orders", "supply_method", "TEXT NOT NULL DEFAULT '试用'")
        conn.execute(
            """
            UPDATE supply_orders
            SET supply_method = '试用'
            WHERE TRIM(COALESCE(supply_method, '')) = ''
            """
        )


MIGRATION = {
    "version": "2026092201",
    "name": "projects usage kind and pack quantity",
    "upgrade": upgrade,
}
