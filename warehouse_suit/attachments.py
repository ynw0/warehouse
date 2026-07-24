# -*- coding: utf-8 -*-
"""Material attachment storage helpers."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import INVENTORY_STATUS_AVAILABLE, STOCK_SOURCE_FORMAL
from warehouse_suit.runtime import DATA_DIR, ensure_runtime_dirs


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".bmp",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
}
IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp"}


def upload_root() -> Path:
    ensure_runtime_dirs()
    root = DATA_DIR / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalize_attachment_type(value: str | None) -> str:
    text = str(value or "").strip().lower()
    legacy_map = {
        "photo": "material_photo",
        "certificate": "document",
        "invoice": "document",
        "other": "document",
    }
    text = legacy_map.get(text, text)
    return text if text in {"material_photo", "document"} else "document"


def new_upload_token() -> str:
    return uuid.uuid4().hex


def _safe_extension(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的附件类型")
    return suffix


def attachment_to_dict(row):
    if not row:
        return None
    data = dict(row)
    suffix = Path(data.get("original_name") or data.get("stored_name") or "").suffix.lower()
    content_type = data.get("content_type") or ""
    data["is_image"] = content_type.startswith("image/") or suffix in IMAGE_EXTENSIONS
    data["is_material_photo"] = normalize_attachment_type(data.get("attachment_type")) == "material_photo"
    data["download_url"] = f"/api/material-attachments/{data['id']}/download"
    return data


def save_uploaded_attachment(
    cursor,
    file_storage,
    *,
    upload_token: str,
    user_id: int | None = None,
    material_id: int | None = None,
    material_batch_id: int | None = None,
    workflow_form_id: int | None = None,
    workflow_item_id: int | None = None,
    attachment_type: str = "other",
    remark: str = "",
):
    if file_storage is None or not file_storage.filename:
        raise ValueError("请选择要上传的附件")
    original_name = str(file_storage.filename)
    suffix = _safe_extension(original_name)
    safe_base = secure_filename(Path(original_name).stem) or "attachment"
    stored_name = f"{uuid.uuid4().hex}_{safe_base}{suffix}"
    now = now_text()
    month_dir = now[:7].replace("-", "/")
    relative_dir = Path("material_attachments") / month_dir
    absolute_dir = upload_root() / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)
    relative_path = str(relative_dir / stored_name).replace(os.sep, "/")
    absolute_path = upload_root() / relative_path
    file_storage.save(absolute_path)
    file_size = absolute_path.stat().st_size
    if file_size > MAX_ATTACHMENT_BYTES:
        absolute_path.unlink(missing_ok=True)
        raise ValueError("附件不能超过 25MB")
    cursor.execute(
        """
        INSERT INTO material_attachments
            (material_id, material_batch_id, workflow_form_id, workflow_item_id, upload_token, attachment_type,
             original_name, stored_name, relative_path, content_type, file_size, remark,
             uploaded_by, created_at, linked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            material_batch_id,
            workflow_form_id,
            workflow_item_id,
            upload_token,
            normalize_attachment_type(attachment_type),
            original_name,
            stored_name,
            relative_path,
            file_storage.mimetype or "",
            file_size,
            remark or "",
            user_id,
            now,
            now if material_id else "",
        ),
    )
    cursor.execute("SELECT * FROM material_attachments WHERE id = ?", (cursor.lastrowid,))
    return attachment_to_dict(cursor.fetchone())


def list_material_attachments(cursor, material_id: int):
    cursor.execute(
        """
        SELECT a.*, u.display_name AS uploaded_by_name, f.form_no, b.batch_no
        FROM material_attachments a
        LEFT JOIN users u ON u.id = a.uploaded_by
        LEFT JOIN workflow_forms f ON f.id = a.workflow_form_id
        LEFT JOIN material_batches b ON b.id = a.material_batch_id
        WHERE a.material_id = ?
        ORDER BY CASE WHEN a.attachment_type IN ('material_photo', 'photo') THEN 0 ELSE 1 END, a.created_at DESC, a.id DESC
        """,
        (material_id,),
    )
    return [attachment_to_dict(row) for row in cursor.fetchall()]


def available_batch_material_photo_map(cursor):
    cursor.execute(
        """
        SELECT
            b.material_id,
            b.id AS batch_id,
            b.batch_no,
            a.id AS attachment_id
        FROM material_batches b
        JOIN material_attachments a ON a.material_batch_id = b.id
        WHERE b.quantity > 0
          AND b.stock_source = ?
          AND b.inventory_status = ?
          AND a.attachment_type IN ('material_photo', 'photo')
        ORDER BY b.received_date DESC, b.id DESC, a.created_at DESC, a.id DESC
        """,
        (STOCK_SOURCE_FORMAL, INVENTORY_STATUS_AVAILABLE),
    )
    photos = {}
    for row in cursor.fetchall():
        material_id = int(row["material_id"] or 0)
        if not material_id or material_id in photos:
            continue
        attachment_id = int(row["attachment_id"])
        photos[material_id] = {
            "material_photo_attachment_id": attachment_id,
            "material_photo_batch_id": int(row["batch_id"] or 0),
            "material_photo_batch_no": row["batch_no"] or "",
            "material_photo_url": f"/api/material-attachments/{attachment_id}/download",
        }
    return photos


def bind_material_attachments(cursor, tokens, *, material_id: int, workflow_form_id: int | None = None, workflow_item_id: int | None = None):
    cleaned = [str(token or "").strip() for token in (tokens or []) if str(token or "").strip()]
    if not cleaned:
        return 0
    placeholders = ",".join("?" for _ in cleaned)
    params = [material_id, workflow_form_id, workflow_item_id, now_text(), *cleaned]
    cursor.execute(
        f"""
        UPDATE material_attachments
        SET material_id = ?,
            workflow_form_id = COALESCE(?, workflow_form_id),
            workflow_item_id = COALESCE(?, workflow_item_id),
            linked_at = ?
        WHERE upload_token IN ({placeholders})
          AND (material_id IS NULL OR material_id = ?)
        """,
        (*params, material_id),
    )
    return cursor.rowcount


def bind_workflow_item_attachments_to_batch(cursor, *, workflow_form_id: int, workflow_item_id: int, material_id: int, material_batch_id: int):
    now = now_text()
    cursor.execute(
        """
        UPDATE material_attachments
        SET material_id = ?,
            material_batch_id = ?,
            linked_at = COALESCE(NULLIF(linked_at, ''), ?)
        WHERE workflow_form_id = ?
          AND workflow_item_id = ?
          AND material_id = ?
          AND (material_batch_id IS NULL OR material_batch_id = ?)
        """,
        (material_id, material_batch_id, now, workflow_form_id, workflow_item_id, material_id, material_batch_id),
    )
    return cursor.rowcount


def attach_batch_attachments(cursor, material):
    if not material:
        return material
    batches = material.get("batches") or []
    attachments = list_material_attachments(cursor, material["id"])
    by_batch = {}
    unbound = []
    quantity_by_batch = {}
    for batch in batches:
        batch_id = int(batch.get("id") or 0)
        quantity_by_batch[batch_id] = float(batch.get("quantity") or 0)
    for row in attachments:
        batch_id = row.get("material_batch_id")
        if batch_id:
            by_batch.setdefault(int(batch_id), []).append(row)
        else:
            unbound.append(row)
    for batch in batches:
        batch["attachments"] = by_batch.get(int(batch.get("id") or 0), [])
    for row in attachments:
        batch_id = int(row.get("material_batch_id") or 0)
        if (
            batch_id
            and quantity_by_batch.get(batch_id, 0) > 0
            and normalize_attachment_type(row.get("attachment_type")) == "material_photo"
        ):
            material["material_photo_url"] = row.get("download_url")
            material["material_photo_attachment_id"] = row.get("id")
            material["material_photo_batch_id"] = batch_id
            material["material_photo_batch_no"] = row.get("batch_no") or ""
            break
    material["attachments"] = attachments
    material["unbound_attachments"] = unbound
    return material


def attachment_absolute_path(row) -> Path:
    relative_path = str(row["relative_path"] or "").replace("\\", "/").lstrip("/")
    root = upload_root().resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents and path != root:
        raise ValueError("附件路径无效")
    return path
