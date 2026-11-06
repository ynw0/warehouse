"""个人事项和物料到货提醒。"""


def upgrade(conn):
    conn.execute("""
        CREATE TABLE assistant_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('note', 'material_wait')),
            status TEXT NOT NULL CHECK (status IN ('recorded', 'incomplete', 'watching', 'notified', 'done')),
            material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
            acceptance_item_id INTEGER REFERENCES workflow_items(id) ON DELETE SET NULL,
            quantity REAL,
            recipient_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            source_message_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notified_at TEXT,
            UNIQUE(user_id, source_message_id, content)
        )
    """)
    # 验收明细或物料被删除时，提醒回到待补充，避免阻挡原有删除流程。
    conn.execute("""
        CREATE TRIGGER assistant_note_detached
        AFTER UPDATE OF material_id, acceptance_item_id ON assistant_notes
        WHEN NEW.status = 'watching' AND (NEW.material_id IS NULL OR NEW.acceptance_item_id IS NULL)
        BEGIN
            UPDATE assistant_notes SET status = 'incomplete', updated_at = datetime('now', 'localtime')
            WHERE id = NEW.id;
        END
    """)
    # 只更新系统旧默认开场白，保留管理员自行设置的内容。
    conn.execute(
        """
        UPDATE app_settings SET value = json_set(value, '$.ai_welcome_message', ?)
        WHERE key = 'workflow_settings' AND json_valid(value)
          AND json_extract(value, '$.ai_welcome_message') = ?
        """,
        (
            "你好，我是物料小助手。我可以帮你查询库存和待办、填写表单、处理流程、辅助物料编码，也可以帮你记下事项并在物料可申领时提醒。",
            "您好我是仓库管理小助手，我可以帮你查询物料，辅助编写新编码。",
        ),
    )
    conn.execute("CREATE INDEX idx_assistant_notes_owner ON assistant_notes(user_id, status, id)")
    conn.execute("CREATE INDEX idx_assistant_notes_material ON assistant_notes(material_id, status)")
    conn.execute("CREATE INDEX idx_assistant_notes_waiting ON assistant_notes(status, material_id)")


MIGRATION = {"version": "2026110501", "name": "assistant notes and reminders", "upgrade": upgrade}
