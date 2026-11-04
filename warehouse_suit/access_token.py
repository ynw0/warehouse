"""登录后按用户签发的接口令牌。数据库只存摘要，明文按用户分开放。"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from warehouse_suit.db import now_text
from warehouse_suit.runtime import DATA_DIR

TOKEN_HOURS = 12


def token_path(user_id):
    """该用户自己的令牌文件。"""
    directory = DATA_DIR / "assistant_tokens"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory / str(int(user_id))


def issue_access_token(conn, user_id):
    """作废该用户旧令牌，写入新令牌。调用方负责提交事务。"""
    user_id = int(user_id)
    raw = secrets.token_urlsafe(32)
    created = datetime.now()
    expires = created + timedelta(hours=TOKEN_HOURS)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE api_access_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
        (user_id,),
    )
    cursor.execute(
        """
        INSERT INTO api_access_tokens (user_id, token_hash, created_at, expires_at, revoked)
        VALUES (?, ?, ?, ?, 0)
        """,
        (user_id, _digest(raw), created.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    path = token_path(user_id)
    path.write_text(raw, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return raw


def revoke_access_tokens(cursor, user_id):
    """作废该用户全部令牌，并删除明文文件。"""
    user_id = int(user_id)
    cursor.execute(
        "UPDATE api_access_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
        (user_id,),
    )
    path = token_path(user_id)
    if path.exists():
        path.unlink()


def user_id_for_token(cursor, raw):
    """核对未作废、未过期的令牌，返回对应用户。"""
    token = str(raw or "").strip()
    if not token:
        return None
    cursor.execute(
        """
        SELECT t.user_id
        FROM api_access_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = ? AND t.revoked = 0 AND t.expires_at > ? AND u.is_active = 1
        """,
        (_digest(token), now_text()),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return int(row["user_id"])


def _digest(raw):
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()
