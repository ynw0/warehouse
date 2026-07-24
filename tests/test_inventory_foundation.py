import json
import threading

import app as app_module
import pytest

from warehouse_suit.db import connect_db
from warehouse_suit.inventory_constants import (
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import (
    add_inventory_batch,
    consume_inventory_fifo,
    update_inventory_total,
)
from warehouse_suit.material_repository import material_query, material_stock_total


def _seed_material(cursor, code, name="Sort material"):
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES (?, ?, 'pc', ?, ?)",
        (code, name, app_module.now_text(), app_module.now_text()),
    )
    return cursor.lastrowid


def _seed_batch(
    cursor,
    material_id,
    batch_no,
    quantity,
    received_date,
    stock_source=STOCK_SOURCE_FORMAL,
    unit_price=1,
):
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, received_date, stock_source,
             inventory_status, version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            material_id,
            batch_no,
            quantity,
            unit_price,
            received_date,
            stock_source,
            INVENTORY_STATUS_AVAILABLE,
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    return cursor.lastrowid


def _login(client):
    response = client.post("/api/login", json={"username": "warehouse", "password": "test"})
    assert response.status_code == 200


def test_material_endpoints_sort_formal_stock_first_and_keep_prior_order(client, db):
    cursor = db.cursor()
    zero_a = _seed_material(cursor, "10200100010001")
    stocked_a = _seed_material(cursor, "10200100010002")
    zero_b = _seed_material(cursor, "10200100010003")
    stocked_b = _seed_material(cursor, "10200100010004")
    _seed_batch(cursor, stocked_a, "S-A", 3, "2025-01-01")
    _seed_batch(cursor, stocked_b, "S-B", 2, "2025-01-02")
    _seed_batch(cursor, zero_a, "TEMP-ONLY", 100, "2025-01-01", STOCK_SOURCE_TEMPORARY)
    for material_id in (zero_a, stocked_a, zero_b, stocked_b):
        update_inventory_total(cursor, material_id)
    db.commit()

    _login(client)
    listed = client.get("/api/materials").get_json()
    searched = client.get("/api/materials/search?keyword=Sort").get_json()
    empty_search = client.get("/api/materials/search?keyword=").get_json()

    expected = [stocked_a, stocked_b, zero_a, zero_b]
    assert [row["id"] for row in listed if row["id"] in expected] == expected
    assert [row["id"] for row in searched] == expected
    assert [row["id"] for row in empty_search if row["id"] in expected] == expected


def test_material_query_paging_is_stable(db):
    cursor = db.cursor()
    material_ids = []
    for index in range(1, 7):
        material_id = _seed_material(cursor, f"1020010002000{index}")
        material_ids.append(material_id)
        if index % 2 == 0:
            _seed_batch(cursor, material_id, f"B-{index}", index, f"2025-01-{index:02d}")
        update_inventory_total(cursor, material_id)
    db.commit()

    sql, params = material_query("m.id IN (?, ?, ?, ?, ?, ?)", tuple(material_ids))
    full = [row["id"] for row in cursor.execute(sql, params)]
    paged = []
    for offset in range(0, 6, 2):
        paged.extend(
            row["id"]
            for row in cursor.execute(sql + " LIMIT ? OFFSET ?", params + (2, offset))
        )
    repeated = [row["id"] for row in cursor.execute(sql, params)]

    assert paged == full
    assert repeated == full
    assert full == [material_ids[1], material_ids[3], material_ids[5], material_ids[0], material_ids[2], material_ids[4]]


def test_formal_inventory_total_excludes_temporary_batches(db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100030001")
    _seed_batch(cursor, material_id, "FORMAL", 5, "2025-01-01")
    _seed_batch(cursor, material_id, "TEMP", 9, "2025-01-01", STOCK_SOURCE_TEMPORARY)

    quantity, amount = update_inventory_total(cursor, material_id)
    db.commit()

    assert quantity == 5
    assert amount == 5
    assert material_stock_total(cursor, material_id) == 5
    assert material_stock_total(cursor, material_id, STOCK_SOURCE_TEMPORARY) == 9
    assert tuple(
        cursor.execute(
            "SELECT quantity, amount FROM inventory WHERE material_id = ?", (material_id,)
        ).fetchone()
    ) == (5, 5)


def test_default_inbound_and_fifo_are_formal_and_keep_order(db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100040001")
    first_id = _seed_batch(cursor, material_id, "FIRST", 4, "2025-01-01", unit_price=2)
    second_id = _seed_batch(cursor, material_id, "SECOND", 5, "2025-02-01", unit_price=3)
    update_inventory_total(cursor, material_id)
    db.commit()

    consumed = consume_inventory_fifo(
        cursor,
        material_id,
        6,
        "OUT-1",
        operation_key="fifo-out-1",
    )
    db.commit()

    assert [row["batch_id"] for row in consumed] == [first_id, second_id]
    rows = cursor.execute(
        "SELECT id, quantity, version, stock_source FROM material_batches WHERE id IN (?, ?) ORDER BY id",
        (first_id, second_id),
    ).fetchall()
    assert [(row["quantity"], row["version"], row["stock_source"]) for row in rows] == [
        (0, 1, STOCK_SOURCE_FORMAL),
        (3, 1, STOCK_SOURCE_FORMAL),
    ]
    assert cursor.execute(
        "SELECT COUNT(*) FROM stock_records WHERE operation_key LIKE 'fifo-out-1%' AND stock_source = ?",
        (STOCK_SOURCE_FORMAL,),
    ).fetchone()[0] == 2
    assert cursor.execute(
        "SELECT quantity FROM inventory WHERE material_id = ?", (material_id,)
    ).fetchone()[0] == 3


def test_old_callers_default_to_formal_for_inbound_and_ledger(db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100050001")
    batch_id = add_inventory_batch(
        cursor,
        material_id,
        4,
        2,
        {"received_date": "2025-03-01"},
        "IN-1",
    )
    db.commit()

    batch = cursor.execute(
        "SELECT stock_source, inventory_status, version FROM material_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    record = cursor.execute(
        "SELECT stock_source, business_type FROM stock_records WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    assert tuple(batch) == (STOCK_SOURCE_FORMAL, INVENTORY_STATUS_AVAILABLE, 0)
    assert tuple(record) == (STOCK_SOURCE_FORMAL, "manual")


def test_insufficient_stock_rolls_back_without_partial_deduction(db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100060001")
    first_id = _seed_batch(cursor, material_id, "A", 2, "2025-01-01")
    second_id = _seed_batch(cursor, material_id, "B", 2, "2025-01-02")
    update_inventory_total(cursor, material_id)
    db.commit()

    with pytest.raises(ValueError, match="库存数量不足"):
        consume_inventory_fifo(cursor, material_id, 5, "OUT-FAIL")
    db.rollback()

    assert [
        row[0]
        for row in cursor.execute(
            "SELECT quantity FROM material_batches WHERE id IN (?, ?) ORDER BY id",
            (first_id, second_id),
        )
    ] == [2, 2]
    assert cursor.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = 'OUT-FAIL'"
    ).fetchone()[0] == 0


def test_ledger_failure_rolls_back_batch_and_inventory(db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100070001")
    batch_id = _seed_batch(cursor, material_id, "ROLLBACK", 5, "2025-01-01")
    update_inventory_total(cursor, material_id)
    db.commit()
    cursor.execute(
        """
        CREATE TRIGGER fail_stock_record
        BEFORE INSERT ON stock_records
        BEGIN
            SELECT RAISE(FAIL, 'forced ledger failure');
        END
        """
    )
    db.commit()

    with pytest.raises(Exception, match="forced ledger failure"):
        consume_inventory_fifo(cursor, material_id, 2, "OUT-ROLLBACK")
    db.rollback()

    assert tuple(
        cursor.execute(
            "SELECT quantity, version FROM material_batches WHERE id = ?", (batch_id,)
        ).fetchone()
    ) == (5, 0)
    assert cursor.execute(
        "SELECT quantity FROM inventory WHERE material_id = ?", (material_id,)
    ).fetchone()[0] == 5


def test_duplicate_operation_key_is_idempotent(db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100080001")
    _seed_batch(cursor, material_id, "IDEMPOTENT", 10, "2025-01-01")
    update_inventory_total(cursor, material_id)
    db.commit()

    first = consume_inventory_fifo(
        cursor, material_id, 3, "OUT-IDEM", operation_key="same-operation"
    )
    db.commit()
    second = consume_inventory_fifo(
        cursor, material_id, 3, "OUT-IDEM", operation_key="same-operation"
    )
    db.commit()

    assert sum(row["quantity"] for row in first) == 3
    assert sum(row["quantity"] for row in second) == 3
    assert material_stock_total(cursor, material_id) == 7
    assert cursor.execute(
        "SELECT COUNT(*) FROM stock_records WHERE operation_key LIKE 'same-operation:%'"
    ).fetchone()[0] == 1


def test_concurrent_fifo_requests_cannot_make_inventory_negative(app, db):
    cursor = db.cursor()
    material_id = _seed_material(cursor, "10200100090001")
    _seed_batch(cursor, material_id, "CONCURRENT", 10, "2025-01-01")
    update_inventory_total(cursor, material_id)
    db.commit()

    barrier = threading.Barrier(2)
    results = []
    result_lock = threading.Lock()

    def consume(operation_key):
        conn = connect_db(app_module.DB_PATH)
        try:
            barrier.wait()
            consume_inventory_fifo(
                conn.cursor(),
                material_id,
                7,
                "OUT-CONCURRENT",
                operation_key=operation_key,
            )
            conn.commit()
            outcome = "success"
        except Exception:
            conn.rollback()
            outcome = "failed"
        finally:
            conn.close()
        with result_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=consume, args=("concurrent-1",)),
        threading.Thread(target=consume, args=("concurrent-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == ["failed", "success"]
    assert material_stock_total(cursor, material_id) == 3
    assert cursor.execute(
        "SELECT MIN(quantity) FROM material_batches WHERE material_id = ?", (material_id,)
    ).fetchone()[0] >= 0


def _warehouse_user_id(cursor):
    return cursor.execute(
        "SELECT id FROM users WHERE username = 'warehouse'"
    ).fetchone()[0]


def _seed_workflow(cursor, form_no, form_type, status, user_id, material_id, code, quantity, item_data=None):
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, warehouse_user_id,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            form_type,
            "Inventory source regression",
            status,
            status,
            user_id,
            user_id,
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, request_quantity,
             qualified_quantity, unit_price, data_json)
        VALUES (?, ?, ?, 'Workflow material', 'pc', ?, ?, 2, ?)
        """,
        (
            form_id,
            material_id,
            code,
            quantity,
            quantity,
            json.dumps(item_data or {}, ensure_ascii=False),
        ),
    )
    item_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_tasks
            (form_id, step_code, assignee_id, status, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?)
        """,
        (form_id, status, user_id, app_module.now_text(), app_module.now_text()),
    )
    return form_id, item_id


def test_acceptance_inbound_api_creates_formal_batch_and_ledger(client, db):
    cursor = db.cursor()
    user_id = _warehouse_user_id(cursor)
    material_id = _seed_material(cursor, "10200100100001")
    form_id, item_id = _seed_workflow(
        cursor,
        "YS-STAGE1-1",
        "acceptance",
        "inbound",
        user_id,
        material_id,
        "10200100100001",
        5,
    )
    db.commit()
    _login(client)

    response = client.post(f"/api/acceptance/{form_id}/inbound", json={"items": []})
    assert response.status_code == 200, response.get_data(as_text=True)

    batch = cursor.execute(
        "SELECT stock_source, quantity FROM material_batches WHERE source_form_no LIKE 'RK%'"
    ).fetchone()
    ledger = cursor.execute(
        "SELECT stock_source FROM stock_records WHERE operation_key = ?",
        (f"acceptance:{form_id}:{item_id}:inbound",),
    ).fetchone()
    assert tuple(batch) == (STOCK_SOURCE_FORMAL, 5)
    assert ledger[0] == STOCK_SOURCE_FORMAL


def test_claim_outbound_api_only_consumes_formal_stock(client, db):
    cursor = db.cursor()
    user_id = _warehouse_user_id(cursor)
    material_id = _seed_material(cursor, "10200100110001")
    _seed_batch(cursor, material_id, "FORMAL-CLAIM", 10, "2025-01-01")
    _seed_batch(
        cursor,
        material_id,
        "TEMP-CLAIM",
        50,
        "2025-01-01",
        STOCK_SOURCE_TEMPORARY,
    )
    update_inventory_total(cursor, material_id)
    form_id, item_id = _seed_workflow(
        cursor,
        "CK-STAGE1-1",
        "claim",
        "outbound",
        user_id,
        material_id,
        "10200100110001",
        4,
    )
    db.commit()
    _login(client)

    response = client.post(f"/api/claims/{form_id}/outbound", json={"items": []})
    assert response.status_code == 200, response.get_data(as_text=True)

    assert material_stock_total(cursor, material_id) == 6
    assert material_stock_total(cursor, material_id, STOCK_SOURCE_TEMPORARY) == 50
    assert cursor.execute(
        "SELECT stock_source FROM workflow_items WHERE id = ?", (item_id,)
    ).fetchone()[0] == STOCK_SOURCE_FORMAL
    records = cursor.execute(
        """
        SELECT stock_source, operation_key
        FROM stock_records
        WHERE operation_key LIKE ?
        ORDER BY id
        """,
        (f"claim:{form_id}:{item_id}:outbound:%",),
    ).fetchall()
    assert len(records) == 1
    assert records[0][0] == STOCK_SOURCE_FORMAL
    assert records[0][1].startswith(f"claim:{form_id}:{item_id}:outbound:")


def test_borrow_and_return_apis_stay_in_formal_stock(client, db):
    cursor = db.cursor()
    user_id = _warehouse_user_id(cursor)
    material_id = _seed_material(cursor, "10200100120001")
    _seed_batch(cursor, material_id, "FORMAL-BORROW", 10, "2025-01-01")
    _seed_batch(
        cursor,
        material_id,
        "TEMP-BORROW",
        40,
        "2025-01-01",
        STOCK_SOURCE_TEMPORARY,
    )
    update_inventory_total(cursor, material_id)
    form_id, item_id = _seed_workflow(
        cursor,
        "JY-STAGE1-1",
        "borrow",
        "borrow_outbound",
        user_id,
        material_id,
        "10200100120001",
        3,
        {"borrow_item_type": "material", "borrow_ref_id": material_id},
    )
    db.commit()
    _login(client)

    response = client.post(f"/api/borrows/{form_id}/outbound", json={"items": []})
    assert response.status_code == 200, response.get_data(as_text=True)
    record = cursor.execute(
        "SELECT * FROM borrow_records WHERE borrow_form_id = ?", (form_id,)
    ).fetchone()
    assert record["stock_source"] == STOCK_SOURCE_FORMAL
    assert record["workflow_item_id"] == item_id
    assert material_stock_total(cursor, material_id) == 7
    assert material_stock_total(cursor, material_id, STOCK_SOURCE_TEMPORARY) == 40

    response = client.post(
        "/api/borrow-returns",
        json={"borrow_record_id": record["id"], "return_quantity": 3, "status": "完好"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return_form_id = response.get_json()["form"]["id"]
    response = client.post(
        f"/api/borrow-returns/{return_form_id}/inbound",
        json={"decision": "同意"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    assert material_stock_total(cursor, material_id) == 10
    assert material_stock_total(cursor, material_id, STOCK_SOURCE_TEMPORARY) == 40
    assert cursor.execute(
        "SELECT COUNT(*) FROM stock_records WHERE stock_source = ? AND form_no = ?",
        (STOCK_SOURCE_FORMAL, response.get_json()["form"]["form_no"]),
    ).fetchone()[0] == 1
