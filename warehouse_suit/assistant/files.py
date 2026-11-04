"""小助手附件。只落在该用户自己的目录里。"""

import base64
import uuid
from pathlib import Path

from warehouse_suit.db import now_text
from warehouse_suit.runtime import DATA_DIR


MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 20000
MAX_FILES = 5
ALLOWED = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def upload_root():
    root = DATA_DIR / "assistant_files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_upload(conn, user_id, filename, raw):
    """保存一个附件并抽出文本。"""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("附件内容无效")
    if len(raw) > MAX_BYTES:
        raise ValueError("单个附件不能超过 8MB")
    extension = Path(str(filename or "")).suffix.lower()
    if extension not in ALLOWED:
        raise ValueError("只支持 png、jpeg、webp、txt、csv、json")
    original = Path(str(filename or "")).name
    if not original or original in {".", ".."}:
        original = "附件" + extension
    original = original[:120]
    relative = f"{int(user_id)}/{uuid.uuid4().hex}{extension}"
    path = _resolve_relative(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(raw))
    extracted = ""
    if extension not in IMAGE_EXTENSIONS:
        extracted = bytes(raw).decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO assistant_files
            (user_id, original_name, mime, stored_path, extracted_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(user_id), original, ALLOWED[extension], relative, extracted, now_text()),
    )
    conn.commit()
    return file_view(get_file(cursor, user_id, cursor.lastrowid))


def load_files(cursor, user_id, file_ids):
    """读取本条消息引用的附件。不属于该用户的编号会报错。"""
    ids = []
    for item in file_ids or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            raise ValueError("附件编号不正确")
        if number not in ids:
            ids.append(number)
    if len(ids) > MAX_FILES:
        raise ValueError(f"每条消息最多 {MAX_FILES} 个附件")
    rows = []
    for file_id in ids:
        row = get_file(cursor, user_id, file_id)
        if not row:
            raise ValueError("附件不存在")
        rows.append(row)
    return rows


def get_file(cursor, user_id, file_id):
    cursor.execute(
        "SELECT * FROM assistant_files WHERE id = ? AND user_id = ?",
        (int(file_id), int(user_id)),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def file_view(row):
    return {
        "id": row["id"],
        "name": row["original_name"],
        "mime": row["mime"],
        "is_image": _is_image(row),
    }


def read_attachment_text(cursor, user_id, file_id, vision):
    """给工具看的附件内容。不返回磁盘路径。"""
    row = get_file(cursor, user_id, file_id)
    if not row:
        raise ValueError("附件不存在")
    name = row["original_name"]
    if _is_image(row):
        if vision:
            return f"图片 {name} 已在对话附件里。模型开启视觉时，图片会随用户消息发送。"
        return f"附件 {name} 是图片。当前模型未开启视觉，不能查看。"
    return row.get("extracted_text") or "附件没有可读文本"


def compose_user_content(cursor, user_id, question, attachments, vision, include_images):
    """把问题和附件收成上游消息内容。图片只在开启视觉时附上。"""
    texts = [str(question or "").strip()]
    images = []
    for item in attachments or []:
        row = get_file(cursor, user_id, item.get("id"))
        name = (row or {}).get("original_name") or item.get("name") or "附件"
        if not row:
            texts.append(f"附件 {name} 已无法读取。")
            continue
        if _is_image(row):
            if vision and include_images:
                images.append(row)
            elif vision:
                texts.append(f"较早的图片未重复附上：{name}")
            else:
                texts.append(f"附件 {name} 是图片。当前模型未开启视觉，不能查看。")
            continue
        body = row.get("extracted_text") or ""
        texts.append(f"附件 {name}：\n{body}" if body else f"附件 {name} 没有可读文本。")
    text = "\n\n".join(part for part in texts if part)
    if not images:
        return text
    content = [{"type": "text", "text": text or "请看附件。"}]
    for row in images:
        content.append({"type": "image_url", "image_url": {"url": _data_url(row)}})
    return content


def _is_image(row):
    mime = str(row.get("mime") or "")
    suffix = Path(str(row.get("stored_path") or "")).suffix.lower()
    return mime.startswith("image/") or suffix in IMAGE_EXTENSIONS


def _data_url(row):
    path = _resolve_relative(row["stored_path"])
    if not path.is_file():
        raise ValueError("附件文件不存在")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{row['mime']};base64,{encoded}"


def _resolve_relative(relative):
    """只允许读该数据目录内的相对路径。"""
    text = str(relative or "").replace("\\", "/").strip("/")
    if not text or text.startswith("/") or ".." in Path(text).parts:
        raise ValueError("附件路径无效")
    root = upload_root().resolve()
    path = (root / text).resolve()
    if path != root and root not in path.parents:
        raise ValueError("附件路径无效")
    return path
