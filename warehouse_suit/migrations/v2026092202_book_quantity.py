"""批次账面入库数量，以及库房赋码待办不发给系统管理员。"""

from warehouse_suit.db import now_text


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(conn, table, column, definition):
    if column in _columns(conn, table):
        return
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {definition}')


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _warehouse_ids_without_admin(conn):
    rows = conn.execute(
        """
        SELECT DISTINCT u.id
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.is_active = 1 AND r.code = 'warehouse'
          AND NOT EXISTS (
              SELECT 1
              FROM user_roles ur2
              JOIN roles r2 ON r2.id = ur2.role_id
              WHERE ur2.user_id = u.id AND r2.code = 'admin'
          )
        ORDER BY u.id
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def upgrade(conn):
    _ensure_column(conn, "material_batches", "book_quantity", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "material_batches", "pack_progress", "REAL NOT NULL DEFAULT 0")
    conn.execute(
        """
        UPDATE material_batches
        SET book_quantity = CASE
                WHEN COALESCE(pack_quantity, 0) > 0 THEN quantity * 1.0 / pack_quantity
                ELSE quantity
            END
        WHERE COALESCE(book_quantity, 0) = 0 AND COALESCE(quantity, 0) > 0
        """
    )
    if not _table_exists(conn, "user_roles") or not _table_exists(conn, "workflow_tasks"):
        return
    timestamp = now_text()
    conn.execute(
        """
        UPDATE workflow_tasks
        SET status = 'cancelled', updated_at = ?
        WHERE step_code = 'purchase_register'
          AND status = 'pending'
          AND assignee_id IN (
              SELECT ur.user_id
              FROM user_roles ur
              JOIN roles r ON r.id = ur.role_id
              WHERE r.code = 'admin'
          )
        """,
        (timestamp,),
    )
    warehouse_ids = _warehouse_ids_without_admin(conn)
    forms = conn.execute(
        """
        SELECT f.id
        FROM workflow_forms f
        WHERE f.form_type = 'purchase_summary'
          AND f.status = 'summarized'
          AND NOT EXISTS (
              SELECT 1
              FROM workflow_tasks t
              WHERE t.form_id = f.id
                AND t.step_code = 'purchase_register'
                AND t.status = 'pending'
          )
        """
    ).fetchall()
    for form in forms:
        form_id = int(form[0])
        for user_id in warehouse_ids:
            conn.execute(
                """
                INSERT INTO workflow_tasks (form_id, step_code, assignee_id, created_at, updated_at)
                VALUES (?, 'purchase_register', ?, ?, ?)
                """,
                (form_id, user_id, timestamp, timestamp),
            )


MIGRATION = {
    "version": "2026092202",
    "name": "book quantity and coding assignees",
    "upgrade": upgrade,
}
