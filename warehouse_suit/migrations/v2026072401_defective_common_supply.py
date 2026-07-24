"""Add defective inventory, common-material alerts, and supply ledgers."""

from __future__ import annotations


VERSION = "2026072401"
NAME = "defective inventory common material and supply foundation"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(conn, table, column, definition):
    table_exists = conn.execute('SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?', ('table', table)).fetchone()
    if not table_exists:
        return
    if column not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def upgrade(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS defective_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL CHECK (item_type IN ('material', 'semifinished', 'finished')),
            material_id INTEGER,
            original_inventory_id INTEGER,
            source_batch_id INTEGER,
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_ref_id INTEGER,
            item_code TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            brand_model TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            unit TEXT DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            remaining_quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            stock_source TEXT NOT NULL DEFAULT 'formal',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS defective_inventory_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defective_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            target_ref_id INTEGER,
            operator_id INTEGER,
            operation_key TEXT,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (defective_id) REFERENCES defective_inventory(id) ON DELETE RESTRICT,
            FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS common_material_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL UNIQUE,
            warning_quantity REAL NOT NULL DEFAULT 0,
            owner_user_id INTEGER NOT NULL,
            approved_form_id INTEGER,
            alert_state TEXT NOT NULL DEFAULT 'normal',
            last_alerted_at TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (approved_form_id) REFERENCES workflow_forms(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS supply_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL UNIQUE,
            applicant_id INTEGER,
            recipient_company TEXT NOT NULL DEFAULT '',
            recipient_name TEXT NOT NULL DEFAULT '',
            recipient_phone TEXT NOT NULL DEFAULT '',
            recipient_address TEXT NOT NULL DEFAULT '',
            expected_close_date TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT DEFAULT '',
            due_alert_date TEXT DEFAULT '',
            FOREIGN KEY (form_id) REFERENCES workflow_forms(id) ON DELETE CASCADE,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS supply_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            workflow_item_id INTEGER,
            item_type TEXT NOT NULL CHECK (item_type IN ('material', 'semifinished', 'finished')),
            item_ref_id INTEGER NOT NULL,
            material_id INTEGER,
            item_code TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            brand_model TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            unit TEXT DEFAULT '',
            approved_quantity REAL NOT NULL DEFAULT 0,
            shipped_quantity REAL NOT NULL DEFAULT 0,
            good_returned_quantity REAL NOT NULL DEFAULT 0,
            defective_returned_quantity REAL NOT NULL DEFAULT 0,
            no_return_quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES supply_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (workflow_item_id) REFERENCES workflow_items(id) ON DELETE SET NULL,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS supply_shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            shipment_no TEXT NOT NULL UNIQUE,
            carrier TEXT NOT NULL DEFAULT '',
            tracking_no TEXT NOT NULL DEFAULT '',
            shipped_at TEXT NOT NULL DEFAULT '',
            operator_id INTEGER,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES supply_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS supply_shipment_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shipment_id INTEGER NOT NULL,
            supply_item_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            allocation_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shipment_id) REFERENCES supply_shipments(id) ON DELETE CASCADE,
            FOREIGN KEY (supply_item_id) REFERENCES supply_items(id) ON DELETE RESTRICT,
            UNIQUE (shipment_id, supply_item_id)
        );

        CREATE TABLE IF NOT EXISTS supply_return_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            form_id INTEGER,
            initiated_by INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            logistics_json TEXT NOT NULL DEFAULT '{}',
            received_at TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES supply_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (form_id) REFERENCES workflow_forms(id) ON DELETE SET NULL,
            FOREIGN KEY (initiated_by) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS supply_return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER NOT NULL,
            supply_item_id INTEGER NOT NULL,
            expected_quantity REAL NOT NULL DEFAULT 0,
            received_quantity REAL NOT NULL DEFAULT 0,
            good_quantity REAL NOT NULL DEFAULT 0,
            defective_quantity REAL NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (return_id) REFERENCES supply_return_records(id) ON DELETE CASCADE,
            FOREIGN KEY (supply_item_id) REFERENCES supply_items(id) ON DELETE RESTRICT,
            UNIQUE (return_id, supply_item_id)
        );

        CREATE TABLE IF NOT EXISTS supply_no_return_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            supply_item_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            operator_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES supply_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (supply_item_id) REFERENCES supply_items(id) ON DELETE RESTRICT,
            FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_defective_inventory_status ON defective_inventory(status, item_type, id);
        CREATE INDEX IF NOT EXISTS idx_defective_inventory_source ON defective_inventory(source_type, source_ref_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_defective_event_operation_key
            ON defective_inventory_events(operation_key)
            WHERE operation_key IS NOT NULL AND operation_key <> '';
        CREATE INDEX IF NOT EXISTS idx_supply_orders_status_due ON supply_orders(status, expected_close_date);
        CREATE INDEX IF NOT EXISTS idx_supply_items_order ON supply_items(order_id, id);
        CREATE INDEX IF NOT EXISTS idx_supply_shipments_order ON supply_shipments(order_id, id);
        CREATE INDEX IF NOT EXISTS idx_supply_returns_order ON supply_return_records(order_id, id);
        """
    )

    for table, column, definition in [
        ("defective_semifinished_goods", "source_type", "TEXT NOT NULL DEFAULT 'production_acceptance'"),
        ("defective_semifinished_goods", "source_ref_id", "INTEGER"),
        ("defective_semifinished_goods", "quantity", "REAL NOT NULL DEFAULT 1"),
        ("defective_semifinished_goods", "unit", "TEXT NOT NULL DEFAULT '个'"),
        ("defective_semifinished_goods", "status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("defective_finished_goods", "source_type", "TEXT NOT NULL DEFAULT 'production_acceptance'"),
        ("defective_finished_goods", "source_ref_id", "INTEGER"),
        ("defective_finished_goods", "quantity", "REAL NOT NULL DEFAULT 1"),
        ("defective_finished_goods", "unit", "TEXT NOT NULL DEFAULT '台'"),
        ("defective_finished_goods", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ]:
        _ensure_column(conn, table, column, definition)

    for table in ('defective_semifinished_goods', 'defective_finished_goods'):
        if conn.execute('SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?', ('table', table)).fetchone():
            conn.execute(f'UPDATE {table} SET quantity = 1 WHERE quantity IS NULL OR quantity <= 0')


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
