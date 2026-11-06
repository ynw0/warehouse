"""事项的即时通知和定时提醒。"""


def upgrade(conn):
    conn.execute("ALTER TABLE assistant_notes ADD COLUMN reminder_mode TEXT NOT NULL DEFAULT 'none' CHECK(reminder_mode IN ('none','immediate','at_time','material'))")
    conn.execute("ALTER TABLE assistant_notes ADD COLUMN remind_at TEXT")
    conn.execute("UPDATE assistant_notes SET reminder_mode = 'material' WHERE kind = 'material_wait'")
    conn.execute("CREATE INDEX idx_assistant_notes_due ON assistant_notes(status, reminder_mode, remind_at)")
    # 替换触发器时不依赖旧定义存在，迁移最终始终创建下面的新定义。
    conn.execute("DROP TRIGGER IF EXISTS assistant_note_detached")
    conn.execute("""
        CREATE TRIGGER assistant_note_detached
        AFTER UPDATE OF material_id, acceptance_item_id ON assistant_notes
        WHEN NEW.status = 'watching' AND NEW.reminder_mode = 'material'
          AND (NEW.material_id IS NULL OR NEW.acceptance_item_id IS NULL)
        BEGIN
            UPDATE assistant_notes SET status = 'incomplete', updated_at = datetime('now', 'localtime')
            WHERE id = NEW.id;
        END
    """)


MIGRATION = {"version": "2026110601", "name": "timed assistant reminders", "upgrade": upgrade}
