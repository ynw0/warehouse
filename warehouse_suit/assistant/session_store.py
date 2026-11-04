"""按用户保存小助手会话和消息。"""

import json

from warehouse_suit.db import now_text


def create_session(conn, user_id, thinking_level):
    """新建一轮对话。"""
    current = now_text()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO assistant_sessions (user_id, title, summary, thinking_level, created_at, updated_at)
        VALUES (?, '', '', ?, ?, ?)
        """,
        (int(user_id), thinking_level or "off", current, current),
    )
    conn.commit()
    return get_session(cursor, cursor.lastrowid, user_id)


def get_session(cursor, session_id, user_id):
    cursor.execute(
        "SELECT * FROM assistant_sessions WHERE id = ? AND user_id = ?",
        (int(session_id), int(user_id)),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def latest_session(cursor, user_id):
    cursor.execute(
        "SELECT * FROM assistant_sessions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def set_thinking(conn, session_id, user_id, thinking_level):
    """改这一轮的思考等级。会话不属于该用户时返回 False。"""
    cursor = conn.execute(
        """
        UPDATE assistant_sessions
        SET thinking_level = ?, updated_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (thinking_level, now_text(), int(session_id), int(user_id)),
    )
    changed = cursor.rowcount == 1
    conn.commit()
    return changed


def set_summary(conn, session_id, summary, keep_ids):
    """用摘要替换更早的消息，只保留指定的近期消息。"""
    if keep_ids:
        marks = ",".join("?" for _ in keep_ids)
        conn.execute(
            f"DELETE FROM assistant_messages WHERE session_id = ? AND id NOT IN ({marks})",
            (int(session_id), *[int(item) for item in keep_ids]),
        )
    else:
        conn.execute("DELETE FROM assistant_messages WHERE session_id = ?", (int(session_id),))
    conn.execute(
        "UPDATE assistant_sessions SET summary = ?, updated_at = ? WHERE id = ?",
        (summary or "", now_text(), int(session_id)),
    )
    conn.commit()


def touch_title(conn, session_id, title):
    text = " ".join(str(title or "").split())[:24]
    if not text:
        return
    conn.execute(
        """
        UPDATE assistant_sessions
        SET title = ?, updated_at = ?
        WHERE id = ? AND title = ''
        """,
        (text, now_text(), int(session_id)),
    )
    conn.commit()


def add_message(conn, session_id, role, content="", reasoning="", tool_calls=None, usage=None, attachments=None):
    """写入一条会话消息。"""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO assistant_messages
            (session_id, role, content, reasoning, tool_json, usage_json, attachments_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(session_id),
            role,
            content or "",
            reasoning or "",
            json.dumps(tool_calls or [], ensure_ascii=False) if role == "assistant" else json.dumps(tool_calls or {}, ensure_ascii=False),
            json.dumps(usage or {}, ensure_ascii=False),
            json.dumps(attachments or [], ensure_ascii=False),
            now_text(),
        ),
    )
    message_id = int(cursor.lastrowid)
    conn.execute(
        "UPDATE assistant_sessions SET updated_at = ? WHERE id = ?",
        (now_text(), int(session_id)),
    )
    conn.commit()
    return message_id


def update_message_content(conn, message_id, content):
    conn.execute(
        "UPDATE assistant_messages SET content = ? WHERE id = ?",
        (content or "", int(message_id)),
    )
    conn.commit()


def list_messages(cursor, session_id):
    cursor.execute(
        "SELECT * FROM assistant_messages WHERE session_id = ? ORDER BY id",
        (int(session_id),),
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(row)
        item["tool_calls"] = _loads(item.pop("tool_json"), [] if item["role"] == "assistant" else {})
        item["usage"] = _loads(item.pop("usage_json"), {})
        item["attachments"] = _loads(item.pop("attachments_json"), [])
        rows.append(item)
    return rows


def present_messages(rows):
    """给浮窗看的消息。工具结果并进助手消息的工具名。"""
    view = []
    for row in rows:
        if row["role"] == "tool":
            continue
        if row["role"] not in ("user", "assistant"):
            continue
        tools = []
        if row["role"] == "assistant":
            calls = row.get("tool_calls") or []
            if isinstance(calls, list):
                tools = [str(item.get("name") or "") for item in calls if item.get("name")]
        view.append(
            {
                "id": row["id"],
                "role": "user" if row["role"] == "user" else "ai",
                "content": row.get("content") or "",
                "reasoning": row.get("reasoning") or "",
                "tools": tools,
                "files": row.get("attachments") or [],
            }
        )
    return view


def get_preference(cursor, user_id):
    cursor.execute(
        "SELECT thinking_level FROM assistant_preferences WHERE user_id = ?",
        (int(user_id),),
    )
    row = cursor.fetchone()
    return str(row[0] or "") if row else ""


def set_preference(conn, user_id, thinking_level):
    conn.execute(
        """
        INSERT INTO assistant_preferences (user_id, thinking_level, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            thinking_level = excluded.thinking_level,
            updated_at = excluded.updated_at
        """,
        (int(user_id), thinking_level, now_text()),
    )
    conn.commit()


def _loads(value, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default
