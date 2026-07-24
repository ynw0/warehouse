# -*- coding: utf-8 -*-
"""Database schema creation, migrations, and seed data."""

import json
import sqlite3

from werkzeug.security import generate_password_hash

from warehouse_suit.db import decode_db_label, ensure_column, now_text, quote_identifier, today_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL
from warehouse_suit.inventory_service import cleanup_code_prefixed_material_names, update_inventory_total
from warehouse_suit.material_repository import material_stock_total
from warehouse_suit.material_utils import numeric_or_none, stock_snapshot_payload
from warehouse_suit.migrations import run_migrations
from warehouse_suit.recycle import cleanup_recycle_bin
from warehouse_suit.settings import parse_json


_db_provider = None


def configure_database_provider(provider):
    global _db_provider
    _db_provider = provider


def _get_db():
    if _db_provider is None:
        raise RuntimeError("database provider is not configured")
    return _db_provider()


def find_legacy_material_table(cursor):
    app_tables = {
        "shelves",
        "shelf_layers",
        "materials",
        "material_positions",
        "inventory",
        "stock_records",
        "sqlite_sequence",
    }
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    for row in cursor.fetchall():
        table_name = row[0]
        if table_name in app_tables:
            continue
        cursor.execute(f"PRAGMA table_info({quote_identifier(table_name)})")
        columns = [column[1] for column in cursor.fetchall()]
        decoded = {decode_db_label(column): column for column in columns}
        if {"物料编码", "物料名称"}.issubset(decoded):
            return table_name, decoded
    return None, {}


def sync_legacy_materials(cursor):
    table_name, columns = find_legacy_material_table(cursor)
    if not table_name:
        return

    def col(label):
        return columns.get(label)

    required_code = col("物料编码")
    required_name = col("物料名称")
    if not required_code or not required_name:
        return

    select_columns = {
        "material_code": required_code,
        "name": required_name,
        "brand_model": col("品牌型号"),
        "spec": col("技术规格"),
        "unit": col("单位"),
        "category": col("大类码") or col("物料类别"),
        "sub_category": col("中类码"),
        "quantity": col("当前库存"),
    }
    expressions = []
    keys = []
    for key, column in select_columns.items():
        keys.append(key)
        expressions.append(f"{quote_identifier(column)} AS {key}" if column else f"NULL AS {key}")

    cursor.execute(f"SELECT {', '.join(expressions)} FROM {quote_identifier(table_name)}")

    def text_value(value):
        return decode_db_label(value).strip() if value is not None else ""

    for row in cursor.fetchall():
        item = dict(zip(keys, row))
        material_code = text_value(item.get("material_code"))
        name = text_value(item.get("name"))
        if not material_code or not name:
            continue
        cursor.execute(
            """
            INSERT INTO materials
                (material_code, brand_model, spec, name, category, sub_category, unit, icon, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(material_code) DO UPDATE SET
                brand_model = COALESCE(NULLIF(excluded.brand_model, ''), materials.brand_model),
                spec = COALESCE(NULLIF(excluded.spec, ''), materials.spec),
                name = COALESCE(NULLIF(excluded.name, ''), materials.name),
                category = COALESCE(NULLIF(excluded.category, ''), materials.category),
                sub_category = COALESCE(NULLIF(excluded.sub_category, ''), materials.sub_category),
                unit = COALESCE(NULLIF(excluded.unit, ''), materials.unit),
                updated_at = excluded.updated_at
            """,
            (
                material_code,
                text_value(item.get("brand_model")),
                text_value(item.get("spec")),
                name,
                text_value(item.get("category")),
                text_value(item.get("sub_category")),
                text_value(item.get("unit")) or "个",
                "□",
                now_text(),
            ),
        )
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (material_code,))
        material_id = cursor.fetchone()[0]
        quantity = float(item.get("quantity") or 0)
        cursor.execute(
            """
            INSERT INTO inventory (material_id, quantity, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(material_id) DO NOTHING
            """,
            (material_id, quantity, now_text()),
        )


def init_db():
    conn = _get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS shelves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            warehouse_type TEXT NOT NULL CHECK (warehouse_type IN ('office', 'rd')),
            shape TEXT NOT NULL DEFAULT 'straight',
            position_x REAL NOT NULL DEFAULT 0,
            position_y REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS shelf_layers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shelf_id INTEGER NOT NULL,
            layer_number INTEGER NOT NULL,
            zones TEXT NOT NULL,
            FOREIGN KEY (shelf_id) REFERENCES shelves(id) ON DELETE CASCADE,
            UNIQUE (shelf_id, layer_number)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_code TEXT UNIQUE NOT NULL,
            brand_model TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            sub_category TEXT DEFAULT '',
            unit TEXT NOT NULL DEFAULT '个',
            icon TEXT NOT NULL DEFAULT '📦',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS material_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL UNIQUE,
            shelf_id INTEGER NOT NULL,
            layer_number INTEGER NOT NULL,
            zone_name TEXT NOT NULL,
            slot_index INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
            FOREIGN KEY (shelf_id) REFERENCES shelves(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL UNIQUE,
            quantity REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL CHECK (operation_type IN ('in', 'out')),
            quantity REAL NOT NULL,
            balance_after REAL NOT NULL DEFAULT 0,
            operation_date TEXT NOT NULL,
            remark TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS material_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            batch_no TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            warehouse_type TEXT NOT NULL DEFAULT 'office',
            shelf_id INTEGER,
            layer_number INTEGER,
            zone_name TEXT DEFAULT '',
            source_form_no TEXT DEFAULT '',
            received_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
            FOREIGN KEY (shelf_id) REFERENCES shelves(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS material_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER,
            material_batch_id INTEGER,
            workflow_form_id INTEGER,
            workflow_item_id INTEGER,
            upload_token TEXT NOT NULL DEFAULT '',
            attachment_type TEXT NOT NULL DEFAULT 'other',
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            relative_path TEXT UNIQUE NOT NULL,
            content_type TEXT DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            remark TEXT DEFAULT '',
            uploaded_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            linked_at TEXT DEFAULT '',
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
            FOREIGN KEY (material_batch_id) REFERENCES material_batches(id) ON DELETE RESTRICT,
            FOREIGN KEY (workflow_form_id) REFERENCES workflow_forms(id) ON DELETE SET NULL,
            FOREIGN KEY (workflow_item_id) REFERENCES workflow_items(id) ON DELETE SET NULL,
            FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_material_attachments_material ON material_attachments(material_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_material_attachments_token ON material_attachments(upload_token)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            department TEXT DEFAULT '',
            password TEXT DEFAULT '',
            must_change_password INTEGER NOT NULL DEFAULT 0,
            password_changed_at TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_no TEXT UNIQUE NOT NULL,
            form_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            current_step TEXT NOT NULL DEFAULT 'draft',
            applicant_id INTEGER,
            leader_id INTEGER,
            warehouse_user_id INTEGER,
            total_amount REAL NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (leader_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (warehouse_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL,
            material_id INTEGER,
            material_code TEXT NOT NULL,
            material_name TEXT NOT NULL,
            brand_model TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            unit TEXT DEFAULT '',
            request_quantity REAL NOT NULL DEFAULT 0,
            arrival_quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            qualified_quantity REAL NOT NULL DEFAULT 0,
            unqualified_quantity REAL NOT NULL DEFAULT 0,
            approved_quantity REAL NOT NULL DEFAULT 0,
            outbound_quantity REAL NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (form_id) REFERENCES workflow_forms(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL,
            step_code TEXT NOT NULL,
            assignee_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            decision TEXT DEFAULT '',
            signature TEXT DEFAULT '',
            signed_at TEXT DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (form_id) REFERENCES workflow_forms(id) ON DELETE CASCADE,
            FOREIGN KEY (assignee_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stocktake_forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_no TEXT UNIQUE NOT NULL,
            warehouse_type TEXT DEFAULT '',
            date_from TEXT DEFAULT '',
            date_to TEXT DEFAULT '',
            show_zero INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            checker_id INTEGER,
            checker_signature TEXT DEFAULT '',
            checker_date TEXT DEFAULT '',
            supervisor_id INTEGER,
            supervisor_signature TEXT DEFAULT '',
            supervisor_date TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stocktake_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stocktake_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            book_quantity REAL NOT NULL DEFAULT 0,
            stock_amount REAL NOT NULL DEFAULT 0,
            period_in REAL NOT NULL DEFAULT 0,
            period_out REAL NOT NULL DEFAULT 0,
            location_text TEXT DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (stocktake_id) REFERENCES stocktake_forms(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS semifinished_acceptances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceptance_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            acceptance_quantity REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '个',
            acceptance_date TEXT NOT NULL,
            qualified_quantity REAL NOT NULL DEFAULT 0,
            unqualified_quantity REAL NOT NULL DEFAULT 0,
            appearance_ok_quantity REAL NOT NULL DEFAULT 0,
            function_ok_quantity REAL NOT NULL DEFAULT 0,
            performance_ok_quantity REAL NOT NULL DEFAULT 0,
            cost_price REAL NOT NULL DEFAULT 0,
            components_json TEXT NOT NULL DEFAULT '[]',
            applicant_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS semifinished_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceptance_id INTEGER,
            name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            unit TEXT NOT NULL DEFAULT '个',
            quantity REAL NOT NULL DEFAULT 0,
            used_quantity REAL NOT NULL DEFAULT 0,
            cost_price REAL NOT NULL DEFAULT 0,
            components_json TEXT NOT NULL DEFAULT '[]',
            acceptance_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (acceptance_id) REFERENCES semifinished_acceptances(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS production_material_consumptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            batch_id INTEGER,
            quantity REAL NOT NULL DEFAULT 0,
            unit_cost REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
            FOREIGN KEY (batch_id) REFERENCES material_batches(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS finished_acceptances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceptance_no TEXT UNIQUE NOT NULL,
            product_name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            acceptance_quantity REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '个',
            acceptance_date TEXT NOT NULL,
            qualified_quantity REAL NOT NULL DEFAULT 0,
            unqualified_quantity REAL NOT NULL DEFAULT 0,
            appearance_ok_quantity REAL NOT NULL DEFAULT 0,
            function_ok_quantity REAL NOT NULL DEFAULT 0,
            performance_ok_quantity REAL NOT NULL DEFAULT 0,
            cost_price REAL NOT NULL DEFAULT 0,
            material_components_json TEXT NOT NULL DEFAULT '[]',
            semifinished_components_json TEXT NOT NULL DEFAULT '[]',
            applicant_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS finished_good_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceptance_id INTEGER,
            product_name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            unit TEXT NOT NULL DEFAULT '个',
            quantity REAL NOT NULL DEFAULT 0,
            cost_price REAL NOT NULL DEFAULT 0,
            acceptance_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (acceptance_id) REFERENCES finished_acceptances(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS semifinished_consumptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finished_acceptance_id INTEGER NOT NULL,
            semifinished_inventory_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit_cost REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (finished_acceptance_id) REFERENCES finished_acceptances(id) ON DELETE CASCADE,
            FOREIGN KEY (semifinished_inventory_id) REFERENCES semifinished_inventory(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS defective_finished_goods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finished_acceptance_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            serial_no TEXT UNIQUE NOT NULL,
            abnormal_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (finished_acceptance_id) REFERENCES finished_acceptances(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS defective_semifinished_goods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semifinished_acceptance_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            serial_no TEXT UNIQUE NOT NULL,
            abnormal_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (semifinished_acceptance_id) REFERENCES semifinished_acceptances(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS borrow_change_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            borrow_record_id INTEGER,
            change_type TEXT DEFAULT '',
            change_detail TEXT DEFAULT '',
            version_after TEXT DEFAULT '',
            normal_use TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (borrow_record_id) REFERENCES borrow_records(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scrapped_finished_goods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceptance_id INTEGER,
            product_name TEXT NOT NULL DEFAULT '',
            spec TEXT DEFAULT '',
            serial_no TEXT UNIQUE NOT NULL,
            unit TEXT DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            original_inventory_id INTEGER,
            scrap_source TEXT DEFAULT '',
            scrap_reason TEXT DEFAULT '',
            scrap_date TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (acceptance_id) REFERENCES finished_acceptances(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scrapped_semifinished_goods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceptance_id INTEGER,
            name TEXT NOT NULL DEFAULT '',
            spec TEXT DEFAULT '',
            serial_no TEXT UNIQUE NOT NULL,
            unit TEXT DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            original_inventory_id INTEGER,
            scrap_source TEXT DEFAULT '',
            scrap_reason TEXT DEFAULT '',
            scrap_date TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (acceptance_id) REFERENCES semifinished_acceptances(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT DEFAULT '',
            action TEXT NOT NULL,
            target_type TEXT DEFAULT '',
            target_id TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            ip_address TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL DEFAULT 'todos',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            is_read INTEGER NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS recycle_bin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            deleted_by INTEGER,
            deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            purge_after TEXT NOT NULL,
            FOREIGN KEY (deleted_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS borrow_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            borrow_no TEXT NOT NULL DEFAULT '',
            item_type TEXT NOT NULL DEFAULT 'material',
            item_ref_id INTEGER NOT NULL DEFAULT 0,
            material_id INTEGER,
            item_code TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            brand_model TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            unit TEXT DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            returned_quantity REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'borrowed',
            borrower_id INTEGER,
            borrow_form_id INTEGER,
            return_form_id INTEGER,
            outbound_date TEXT DEFAULT '',
            return_date TEXT DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE SET NULL,
            FOREIGN KEY (borrower_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (borrow_form_id) REFERENCES workflow_forms(id) ON DELETE SET NULL,
            FOREIGN KEY (return_form_id) REFERENCES workflow_forms(id) ON DELETE SET NULL
        )
        """
    )

    # Tolerate older partial schemas from previous runs.
    for table, column, definition in [
        ("shelves", "warehouse_type", "TEXT NOT NULL DEFAULT 'office'"),
        ("shelves", "shape", "TEXT NOT NULL DEFAULT 'straight'"),
        ("shelves", "position_x", "REAL NOT NULL DEFAULT 0"),
        ("shelves", "position_y", "REAL NOT NULL DEFAULT 0"),
        ("shelves", "updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ("materials", "icon", "TEXT NOT NULL DEFAULT '📦'"),
        ("materials", "updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ("materials", "warehouse_code", "TEXT DEFAULT ''"),
        ("materials", "major_code", "TEXT DEFAULT ''"),
        ("materials", "middle_code", "TEXT DEFAULT ''"),
        ("materials", "small_code", "TEXT DEFAULT ''"),
        ("materials", "detail_code", "TEXT DEFAULT ''"),
        ("materials", "category_name", "TEXT DEFAULT ''"),
        ("materials", "material_type", "TEXT DEFAULT ''"),
        ("materials", "purchase_applicant", "TEXT DEFAULT ''"),
        ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "password_changed_at", "TEXT DEFAULT ''"),
        ("material_positions", "slot_index", "INTEGER NOT NULL DEFAULT 0"),
        ("stock_records", "balance_after", "REAL NOT NULL DEFAULT 0"),
        ("stock_records", "batch_id", "INTEGER"),
        ("stock_records", "form_no", "TEXT DEFAULT ''"),
        ("material_attachments", "material_batch_id", "INTEGER"),
        ("stock_records", "unit_price", "REAL NOT NULL DEFAULT 0"),
        ("stock_records", "amount", "REAL NOT NULL DEFAULT 0"),
        ("inventory", "amount", "REAL NOT NULL DEFAULT 0"),
        ("workflow_items", "purchase_applicant", "TEXT DEFAULT ''"),
        ("production_material_consumptions", "batch_id", "INTEGER"),
        ("semifinished_acceptances", "serials_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("semifinished_inventory", "shelf_id", "INTEGER"),
        ("semifinished_inventory", "layer_number", "INTEGER NOT NULL DEFAULT 1"),
        ("semifinished_inventory", "zone_name", "TEXT DEFAULT ''"),
        ("semifinished_inventory", "serial_no", "TEXT DEFAULT ''"),
        ("semifinished_inventory", "borrowed_quantity", "REAL NOT NULL DEFAULT 0"),
        ("finished_acceptances", "serials_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("finished_acceptances", "project_code", "TEXT NOT NULL DEFAULT ''"),
        ("finished_acceptances", "maker_id", "INTEGER"),
        ("finished_good_inventory", "shelf_id", "INTEGER"),
        ("finished_good_inventory", "layer_number", "INTEGER NOT NULL DEFAULT 1"),
        ("finished_good_inventory", "zone_name", "TEXT DEFAULT ''"),
        ("finished_good_inventory", "serial_no", "TEXT DEFAULT ''"),
        ("finished_good_inventory", "borrowed_quantity", "REAL NOT NULL DEFAULT 0"),
        ("semifinished_acceptances", "project_code", "TEXT NOT NULL DEFAULT ''"),
        ("semifinished_acceptances", "maker_id", "INTEGER"),
    ]:
        ensure_column(cursor, table, column, definition)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_material_attachments_batch ON material_attachments(material_batch_id)")

    conn.commit()
    run_migrations(conn)
    cursor = conn.cursor()

    seed_users_and_roles(cursor)
    seed_departments(cursor)
    cleanup_legacy_seed_people(cursor)

    cursor.execute("SELECT COUNT(*) FROM shelves")
    if cursor.fetchone()[0] == 0:
        seed_shelves(cursor)

    # Legacy Chinese material tables are retained only as historical data.
    # The current materials table is authoritative and must not be overwritten at startup.
    seed_batches_from_inventory(cursor)
    cleanup_code_prefixed_material_names(cursor)
    cleanup_recycle_bin(cursor)
    migrate_claim_revision_to_applicant_revision(cursor)
    backfill_workflow_stock_snapshots(cursor)

    conn.commit()
    conn.close()


def migrate_claim_revision_to_applicant_revision(cursor):
    """Fold the removed claim_revision step into applicant_revision."""
    cursor.execute(
        """
        UPDATE workflow_forms
        SET status = 'applicant_revision',
            current_step = CASE WHEN current_step = 'claim_revision' THEN 'applicant_revision' ELSE current_step END,
            updated_at = ?
        WHERE form_type = 'claim'
          AND (status = 'claim_revision' OR current_step = 'claim_revision')
        """,
        (now_text(),),
    )
    cursor.execute(
        """
        UPDATE workflow_tasks
        SET step_code = 'applicant_revision',
            updated_at = ?
        WHERE step_code = 'claim_revision'
        """,
        (now_text(),),
    )


def backfill_workflow_stock_snapshots(cursor):
    try:
        cursor.execute(
            """
            SELECT wi.id, wi.material_id, wi.data_json, f.created_at
            FROM workflow_items wi
            JOIN workflow_forms f ON f.id = wi.form_id
            WHERE wi.material_id IS NOT NULL
              AND f.form_type IN ('claim', 'borrow')
            """
        )
    except sqlite3.Error:
        return
    for row in cursor.fetchall():
        data = parse_json(row["data_json"], {})
        if numeric_or_none(data.get("stock_quantity_snapshot")) is not None or numeric_or_none(data.get("available_quantity_snapshot")) is not None:
            continue
        data.update(stock_snapshot_payload(material_stock_total(cursor, row["material_id"]), row["created_at"], "migration_current_quantity"))
        cursor.execute("UPDATE workflow_items SET data_json = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), row["id"]))


def seed_users_and_roles(cursor):
    roles = [
        ("user", "普通用户"),
        ("warehouse", "仓库管理员"),
        ("leader", "部门领导"),
        ("buyer", "采购员"),
        ("admin", "系统管理员"),
    ]
    for code, name in roles:
        cursor.execute("INSERT OR IGNORE INTO roles (code, name) VALUES (?, ?)", (code, name))

    users = [
        ("admin", "系统管理员", "", "admin", ["admin", "warehouse", "leader", "buyer", "user"]),
    ]
    for username, display_name, department, password, role_codes in users:
        cursor.execute(
            """
            INSERT OR IGNORE INTO users (username, display_name, department, password, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, display_name, department, generate_password_hash(password), now_text()),
        )
        inserted = cursor.rowcount > 0
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_id = cursor.fetchone()[0]
        if username == "admin":
            cursor.execute("UPDATE users SET is_active = 1, updated_at = ? WHERE id = ?", (now_text(), user_id))
        elif inserted:
            cursor.execute("UPDATE users SET must_change_password = 1, updated_at = ? WHERE id = ?", (now_text(), user_id))
        cursor.execute("SELECT password FROM users WHERE id = ?", (user_id,))
        existing_password = cursor.fetchone()[0] or ""
        if existing_password in {"", "123456"}:
            cursor.execute(
                "UPDATE users SET password = ?, updated_at = ? WHERE id = ?",
                (generate_password_hash(password), now_text(), user_id),
            )
        for role_code in role_codes:
            cursor.execute("SELECT id FROM roles WHERE code = ?", (role_code,))
            role_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, role_id),
            )


def seed_departments(cursor):
    # Departments are maintained by the customer data import / system settings.
    # Do not auto-create sample departments here.
    return


def cleanup_legacy_seed_people(cursor):
    legacy_users = [
        ("warehouse", "库房管理员", "仓储部"),
        ("leader", "部门领导", "综合部"),
        ("buyer", "采购员", "采购部"),
        ("user", "普通用户", "使用部门"),
    ]
    for username, display_name, department in legacy_users:
        cursor.execute(
            "SELECT id FROM users WHERE username = ? AND display_name = ? AND department = ?",
            (username, display_name, department),
        )
        row = cursor.fetchone()
        if not row:
            continue
        cursor.execute("DELETE FROM user_roles WHERE user_id = ?", (row["id"],))
        cursor.execute("DELETE FROM users WHERE id = ?", (row["id"],))
    cursor.execute("UPDATE users SET department = '', updated_at = ? WHERE username = 'admin' AND department = '管理部'", (now_text(),))
    for name in ["管理部", "仓储部", "综合部", "采购部", "使用部门", "研发部", "办公用品库"]:
        cursor.execute("SELECT 1 FROM users WHERE department = ? LIMIT 1", (name,))
        if cursor.fetchone():
            continue
        cursor.execute("DELETE FROM departments WHERE name = ?", (name,))


def seed_batches_from_inventory(cursor):
    cursor.execute(
        """
        SELECT m.id, m.material_code, COALESCE(i.quantity, 0) AS quantity,
               mp.shelf_id, mp.layer_number, mp.zone_name, s.warehouse_type
        FROM materials m
        JOIN inventory i ON i.material_id = m.id
        LEFT JOIN material_positions mp ON mp.material_id = m.id
        LEFT JOIN shelves s ON s.id = mp.shelf_id
        WHERE COALESCE(i.quantity, 0) > 0
          AND NOT EXISTS (
              SELECT 1 FROM material_batches b
              WHERE b.material_id = m.id AND b.stock_source = ?
          )
        """,
        (STOCK_SOURCE_FORMAL,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    for row in rows:
        cursor.execute(
            """
            INSERT INTO material_batches
                (material_id, batch_no, quantity, unit_price, warehouse_type, shelf_id,
                 layer_number, zone_name, source_form_no, received_date, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?, ?, ?, 'INIT', ?, ?, ?)
            """,
            (
                row["id"],
                f"INIT{row['material_code']}",
                row["quantity"],
                row["warehouse_type"] or "office",
                row["shelf_id"],
                row["layer_number"],
                row["zone_name"] or "",
                today_text(),
                now_text(),
                now_text(),
            ),
        )
        update_inventory_total(cursor, row["id"])


def seed_shelves(cursor):
    defaults = [
        (
            "办公 A 架",
            "office",
            "straight",
            [
                ("A", "文具/笔类"),
                ("B", "纸张/本册"),
                ("C", "桌面耗材"),
                ("A", "文件夹"),
                ("B", "标签/胶带"),
                ("C", "低值易耗"),
                ("D", "备用区"),
            ],
            [3, 4],
            12,
            18,
        ),
        (
            "办公 B 架",
            "office",
            "lshape",
            [("A", "清洁用品"), ("B", "行政备品"), ("A", "电子附件"), ("B", "会议用品")],
            [2, 2],
            60,
            18,
        ),
        (
            "研发一号架",
            "rd",
            "straight",
            [
                ("A", "R01 电阻"),
                ("B", "C01 电容"),
                ("C", "L01 电感"),
                ("A", "IC 主控"),
                ("B", "传感器"),
                ("C", "连接器"),
                ("D", "结构件"),
            ],
            [3, 4],
            12,
            18,
        ),
        (
            "研发二号架",
            "rd",
            "ushape",
            [
                ("A", "线材"),
                ("B", "电源"),
                ("C", "工具"),
                ("A", "开发板"),
                ("B", "模块"),
                ("C", "样品"),
            ],
            [3, 3],
            60,
            18,
        ),
    ]

    for name, warehouse_type, shape, zone_pairs, layer_zone_counts, x, y in defaults:
        cursor.execute(
            """
            INSERT INTO shelves (name, warehouse_type, shape, position_x, position_y)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, warehouse_type, shape, x, y),
        )
        shelf_id = cursor.lastrowid
        pair_index = 0
        for layer_number, zone_count in enumerate(layer_zone_counts, start=1):
            zones = []
            for local_index in range(zone_count):
                zone_name, note = zone_pairs[pair_index]
                zones.append(
                    {
                        "name": zone_name,
                        "note": note,
                        "capacity": 10,
                        "color": ["#69a7ff", "#7dd6a6", "#f6c85f", "#f28c8c"][local_index % 4],
                    }
                )
                pair_index += 1
            cursor.execute(
                """
                INSERT INTO shelf_layers (shelf_id, layer_number, zones)
                VALUES (?, ?, ?)
                """,
                (shelf_id, layer_number, json.dumps(zones, ensure_ascii=False)),
            )
