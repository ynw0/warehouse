import sqlite3

import pytest

from warehouse_suit.migrations import run_migrations


def _old_database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY,
            material_code TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE material_batches (
            id INTEGER PRIMARY KEY,
            material_id INTEGER NOT NULL,
            batch_no TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            received_date TEXT NOT NULL
        );
        CREATE TABLE stock_records (
            id INTEGER PRIMARY KEY,
            material_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            operation_date TEXT NOT NULL
        );
        CREATE TABLE workflow_forms (
            id INTEGER PRIMARY KEY,
            form_no TEXT NOT NULL
        );
        CREATE TABLE workflow_items (
            id INTEGER PRIMARY KEY,
            form_id INTEGER NOT NULL,
            material_id INTEGER
        );
        CREATE TABLE borrow_records (
            id INTEGER PRIMARY KEY,
            material_id INTEGER,
            borrower_id INTEGER,
            status TEXT NOT NULL
        );
        CREATE TABLE notifications (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            is_read INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO materials VALUES (1, 'OLD001', 'Old material')")
    conn.execute("INSERT INTO material_batches VALUES (1, 1, 'OLD-B1', 8, 2, '2025-01-01')")
    conn.execute("INSERT INTO stock_records VALUES (1, 1, 'in', 8, '2025-01-01')")
    conn.execute("INSERT INTO workflow_forms VALUES (1, 'OLD-F1')")
    conn.execute("INSERT INTO workflow_items VALUES (1, 1, 1)")
    conn.execute("INSERT INTO borrow_records VALUES (1, 1, 7, 'borrowed')")
    conn.execute("INSERT INTO notifications VALUES (1, 7, 0)")
    conn.commit()
    return conn


def _counts(conn):
    return {
        "materials": conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0],
        "batches": conn.execute("SELECT COUNT(*) FROM material_batches").fetchone()[0],
        "quantity": conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM material_batches").fetchone()[0],
        "records": conn.execute("SELECT COUNT(*) FROM stock_records").fetchone()[0],
    }


def _column_names(conn, table):
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def test_old_database_upgrade_is_idempotent_and_preserves_data():
    conn = _old_database()
    before = _counts(conn)

    versions = run_migrations(conn)
    first_indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    versions_again = run_migrations(conn)

    assert versions == [
        "2026071301",
        "2026071401",
        "2026071402",
        "2026071403",
        "2026071404",
        "2026071501",
    ]
    assert versions_again == versions
    assert _counts(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 6
    assert {"stock_source", "inventory_status", "version"} <= _column_names(conn, "material_batches")
    assert {
        "stock_source",
        "business_type",
        "operation_key",
        "transfer_task_id",
        "operator_id",
        "workflow_item_id",
        "transfer_auto_claim_id",
        "inventory_reservation_id",
    } <= _column_names(conn, "stock_records")
    assert {
        "applicant_id",
        "material_id",
        "source_batch_id",
        "claim_form_id",
        "claim_item_id",
        "stock_record_id",
        "issued_quantity",
        "settled_quantity",
        "status",
        "operation_key",
    } <= _column_names(conn, "temporary_issue_obligations")
    assert {"stock_source"} <= _column_names(conn, "workflow_items")
    assert {"origin_type", "origin_ref_id"} <= _column_names(conn, "workflow_forms")
    assert {"borrow_form_id", "workflow_item_id", "stock_source"} <= _column_names(conn, "borrow_records")
    assert tuple(
        conn.execute(
            "SELECT stock_source, inventory_status, version FROM material_batches"
        ).fetchone()
    ) == ("formal", "available", 0)
    assert tuple(
        conn.execute(
            "SELECT stock_source, business_type FROM stock_records"
        ).fetchone()
    ) == ("formal", "manual")
    assert conn.execute("SELECT stock_source FROM workflow_items").fetchone()[0] == "formal"
    assert conn.execute("SELECT origin_type FROM workflow_forms").fetchone()[0] == "manual"
    assert conn.execute("SELECT stock_source FROM borrow_records").fetchone()[0] == "formal"
    assert {
        "idx_material_batches_source_status_material_fifo",
        "idx_stock_records_source_material_date",
        "uq_stock_records_operation_key",
        "idx_workflow_items_form_source_material",
        "idx_borrow_records_source_material_borrower_status",
        "idx_notifications_user_read_id",
        "idx_temp_issue_obligations_status_material",
        "idx_temp_issue_obligations_applicant_material_status",
        "idx_temp_issue_obligations_batch_status",
        "idx_temp_issue_obligations_claim",
        "idx_stock_records_workflow_item",
        "idx_stock_records_transfer_auto_claim",
        "idx_stock_records_inventory_reservation",
    } <= first_indexes
    assert {
        "inventory_transfer_tasks",
        "inventory_transfer_items",
        "inventory_transfer_obligations",
        "transfer_acceptance_links",
        "inventory_reservations",
        "transfer_auto_claims",
        "transfer_auto_claim_obligations",
    } <= {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    conn.close()


def test_new_database_is_created_at_latest_schema(db):
    assert db.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = '2026071301'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = '2026071402'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = '2026071404'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = '2026071501'"
    ).fetchone()[0] == 1
    assert {
        "inventory_reservations",
        "transfer_auto_claims",
        "transfer_auto_claim_obligations",
    } <= {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"stock_source", "inventory_status", "version"} <= _column_names(db, "material_batches")
    assert {"origin_type", "origin_ref_id"} <= _column_names(db, "workflow_forms")
    assert {"workflow_item_id"} <= _column_names(db, "stock_records")
    assert {"operation_key", "issued_quantity", "status"} <= _column_names(
        db, "temporary_issue_obligations"
    )


def test_single_failed_migration_rolls_back():
    conn = sqlite3.connect(":memory:")

    def fail_upgrade(connection):
        connection.execute("CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO should_rollback VALUES (1)")
        raise RuntimeError("forced migration failure")

    failing = [{"version": "test-failure", "name": "forced failure", "upgrade": fail_upgrade}]
    with pytest.raises(RuntimeError, match="forced migration failure"):
        run_migrations(conn, migrations=failing)

    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'should_rollback'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 'test-failure'"
    ).fetchone()[0] == 0
    conn.close()


def test_operation_key_unique_index_allows_null_but_rejects_duplicates():
    conn = _old_database()
    run_migrations(conn)
    conn.execute(
        "INSERT INTO stock_records (id, material_id, operation_type, quantity, operation_date, operation_key) VALUES (2, 1, 'in', 1, '2025-01-02', NULL)"
    )
    conn.execute(
        "INSERT INTO stock_records (id, material_id, operation_type, quantity, operation_date, operation_key) VALUES (3, 1, 'in', 1, '2025-01-03', 'same-key')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stock_records (id, material_id, operation_type, quantity, operation_date, operation_key) VALUES (4, 1, 'in', 1, '2025-01-04', 'same-key')"
        )
    conn.rollback()
    conn.close()
