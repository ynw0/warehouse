"""物料小助手的会话、附件、用户记忆和思考偏好。"""


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            thinking_level TEXT NOT NULL DEFAULT 'off',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_sessions_user ON assistant_sessions(user_id, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            reasoning TEXT NOT NULL DEFAULT '',
            tool_json TEXT NOT NULL DEFAULT '',
            usage_json TEXT NOT NULL DEFAULT '',
            attachments_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_messages_session ON assistant_messages(session_id, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            mime TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            extracted_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_files_user ON assistant_files(user_id, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_memories (
            user_id INTEGER PRIMARY KEY,
            body TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_preferences (
            user_id INTEGER PRIMARY KEY,
            thinking_level TEXT NOT NULL DEFAULT 'off',
            updated_at TEXT NOT NULL
        )
        """
    )


MIGRATION = {
    "version": "2026101502",
    "name": "assistant runtime",
    "upgrade": upgrade,
}
