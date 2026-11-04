"""每个用户一份长期记忆。"""

from warehouse_suit.db import now_text


MAX_MEMORY_CHARS = 4000


def get_memory(cursor, user_id):
    cursor.execute("SELECT body FROM assistant_memories WHERE user_id = ?", (int(user_id),))
    row = cursor.fetchone()
    return str(row[0] or "") if row else ""


def save_memory(conn, user_id, body):
    """整份覆盖该用户的记忆。"""
    text = str(body or "").strip()
    if len(text) > MAX_MEMORY_CHARS:
        raise ValueError(f"记忆最多 {MAX_MEMORY_CHARS} 字")
    conn.execute(
        """
        INSERT INTO assistant_memories (user_id, body, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
        """,
        (int(user_id), text, now_text()),
    )
    conn.commit()
    return text
