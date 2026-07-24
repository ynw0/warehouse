"""Recycle-bin persistence and restore helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from .db import now_text
from .settings import parse_json, recycle_retention_days


def cleanup_recycle_bin(cursor):
    try:
        cursor.execute("DELETE FROM recycle_bin WHERE purge_after <= ?", (now_text(),))
    except sqlite3.Error:
        pass




def recycle_payload(cursor, table, where="", params=()):
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def recycle_store(cursor, target_type, target_id, title, payload, user_id=None):
    cleanup_recycle_bin(cursor)
    days = recycle_retention_days(cursor)
    purge_after = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO recycle_bin
            (target_type, target_id, title, data_json, deleted_by, deleted_at, purge_after)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_type,
            str(target_id or ""),
            str(title or ""),
            json.dumps(payload or {}, ensure_ascii=False),
            user_id,
            now_text(),
            purge_after,
        ),
    )


def recycle_workflow(cursor, form_id, user_id=None):
    forms = recycle_payload(cursor, "workflow_forms", "id = ?", (form_id,))
    if not forms:
        return
    form = forms[0]
    payload = {
        "workflow_forms": forms,
        "workflow_items": recycle_payload(cursor, "workflow_items", "form_id = ?", (form_id,)),
        "workflow_tasks": recycle_payload(cursor, "workflow_tasks", "form_id = ?", (form_id,)),
    }
    recycle_store(cursor, "workflow", form_id, f"{form.get('form_no') or ''} {form.get('title') or ''}".strip(), payload, user_id)


def recycle_material(cursor, material_id, user_id=None):
    materials = recycle_payload(cursor, "materials", "id = ?", (material_id,))
    if not materials:
        return
    material = materials[0]
    payload = {
        "materials": materials,
        "inventory": recycle_payload(cursor, "inventory", "material_id = ?", (material_id,)),
        "material_positions": recycle_payload(cursor, "material_positions", "material_id = ?", (material_id,)),
        "material_batches": recycle_payload(cursor, "material_batches", "material_id = ?", (material_id,)),
        "stock_records": recycle_payload(cursor, "stock_records", "material_id = ?", (material_id,)),
    }
    recycle_store(cursor, "material", material_id, f"{material.get('material_code') or ''} {material.get('name') or ''}".strip(), payload, user_id)


def recycle_stocktake(cursor, stocktake_id, user_id=None):
    forms = recycle_payload(cursor, "stocktake_forms", "id = ?", (stocktake_id,))
    if not forms:
        return
    payload = {
        "stocktake_forms": forms,
        "stocktake_items": recycle_payload(cursor, "stocktake_items", "stocktake_id = ?", (stocktake_id,)),
    }
    recycle_store(cursor, "stocktake", stocktake_id, forms[0].get("form_no") or "", payload, user_id)


def recycle_table_row(cursor, target_type, table, row_id, title_fields=None, user_id=None):
    rows = recycle_payload(cursor, table, "id = ?", (row_id,))
    if not rows:
        return
    row = rows[0]
    title = " ".join(str(row.get(field) or "") for field in (title_fields or ["id"])).strip()
    recycle_store(cursor, target_type, row_id, title, {table: rows}, user_id)


def restore_recycle_table(cursor, table, rows):
    for row in rows or []:
        if not row:
            continue
        columns = list(row.keys())
        placeholders = ",".join("?" for _ in columns)
        cursor.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )


def restore_recycle_payload(cursor, entry):
    payload = parse_json(entry.get("data_json"), {})
    target_type = entry.get("target_type") or ""
    order_map = {
        "workflow": ["workflow_forms", "workflow_items", "workflow_tasks"],
        "material": ["materials", "inventory", "material_positions", "material_batches", "stock_records"],
        "stocktake": ["stocktake_forms", "stocktake_items"],
        "shelf": ["shelves", "shelf_layers"],
        "department": ["departments"],
        "semifinished_inventory": ["semifinished_inventory"],
        "defective_semifinished": ["defective_semifinished_goods"],
        "finished_inventory": ["finished_good_inventory"],
        "defective_finished": ["defective_finished_goods"],
    }
    tables = order_map.get(target_type)
    if not tables:
        raise ValueError("unsupported recycle restore target")
    for table in tables:
        restore_recycle_table(cursor, table, payload.get(table) or [])
