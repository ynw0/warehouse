"""Track claim outbound requests and reopen historical partial claims."""


VERSION = "2026090802"
NAME = "partial claim outbound"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE claim_outbound_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL REFERENCES workflow_forms(id) ON DELETE CASCADE,
            task_id INTEGER REFERENCES workflow_tasks(id) ON DELETE SET NULL,
            operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            operation_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_claim_outbound_operations_form
        ON claim_outbound_operations(form_id, created_at)
        """
    )

    required_columns = {
        "workflow_forms": {"form_type", "status", "current_step", "warehouse_user_id"},
        "workflow_items": {"form_id", "request_quantity", "outbound_quantity"},
        "workflow_tasks": {"form_id", "step_code", "assignee_id", "status", "data_json", "created_at"},
        "users": {"id", "is_active"},
    }
    if any(
        not columns.issubset(_columns(conn, table))
        for table, columns in required_columns.items()
    ):
        return

    # 旧版本会把部分出库的单据错误标记为完成；有实际出库且仍有欠量的单据应恢复待办。
    partial_condition = """
        f.form_type = 'claim'
        AND f.status = 'completed'
        AND EXISTS (
            SELECT 1
            FROM workflow_items shipped
            WHERE shipped.form_id = f.id
              AND shipped.outbound_quantity > 0.000000001
        )
        AND EXISTS (
            SELECT 1
            FROM workflow_items remaining
            WHERE remaining.form_id = f.id
              AND remaining.request_quantity - remaining.outbound_quantity > 0.000000001
        )
    """
    invalid = conn.execute(
        f"""
        SELECT f.form_no
        FROM workflow_forms f
        LEFT JOIN users u ON u.id = f.warehouse_user_id AND u.is_active = 1
        WHERE {partial_condition}
          AND u.id IS NULL
        ORDER BY f.id
        """
    ).fetchall()
    if invalid:
        numbers = ",".join(str(row[0]) for row in invalid)
        raise ValueError(f"部分出库单据缺少有效库管，无法迁移：{numbers}")

    conn.execute(
        f"""
        UPDATE workflow_forms AS f
        SET status = 'outbound',
            current_step = 'outbound',
            updated_at = datetime('now', 'localtime')
        WHERE {partial_condition}
        """
    )
    conn.execute(
        """
        INSERT INTO workflow_tasks
            (form_id, step_code, assignee_id, status, decision, data_json, created_at, updated_at)
        SELECT f.id, 'outbound', f.warehouse_user_id, 'pending', '历史部分出库待续办',
               '{}', datetime('now', 'localtime'), datetime('now', 'localtime')
        FROM workflow_forms f
        WHERE f.form_type = 'claim'
          AND f.status = 'outbound'
          AND f.current_step = 'outbound'
          AND EXISTS (
              SELECT 1 FROM workflow_items shipped
              WHERE shipped.form_id = f.id
                AND shipped.outbound_quantity > 0.000000001
          )
          AND EXISTS (
              SELECT 1 FROM workflow_items remaining
              WHERE remaining.form_id = f.id
                AND remaining.request_quantity - remaining.outbound_quantity > 0.000000001
          )
          AND NOT EXISTS (
              SELECT 1 FROM workflow_tasks task
              WHERE task.form_id = f.id AND task.step_code = 'outbound' AND task.status = 'pending'
          )
        """
    )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
