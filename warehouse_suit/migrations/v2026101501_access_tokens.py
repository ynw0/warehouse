"""按用户保存接口令牌摘要，并去掉单独的 AI 助手角色。"""


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_access_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_access_tokens_hash ON api_access_tokens(token_hash)"
    )
    conn.execute(
        """
        DELETE FROM user_roles
        WHERE role_id IN (SELECT id FROM roles WHERE code = 'ai_assistant')
        """
    )
    conn.execute("DELETE FROM roles WHERE code = 'ai_assistant'")


MIGRATION = {
    "version": "2026101501",
    "name": "access tokens",
    "upgrade": upgrade,
}
