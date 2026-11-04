# -*- coding: utf-8 -*-
"""部分转领用、转借拆单。"""

import json

import app as app_module
from warehouse_suit.inventory_service import update_borrow_return_balance


def _create_user(cursor, username, password="test"):
    cursor.execute(
        "INSERT INTO users (username, display_name, department, password, updated_at) VALUES (?, ?, ?, ?, ?)",
        (username, username, "", app_module.generate_password_hash(password), app_module.now_text()),
    )
    return cursor.lastrowid


def _create_borrow_record(cursor, borrower_id, quantity=10, claimed_quantity=0, returned_quantity=0, status="borrowed"):
    stamp = app_module.now_text()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'borrow', '借用', 'completed', 'completed', ?, ?, ?, ?)
        """,
        (
            f"JY-PARTIAL-{borrower_id}",
            borrower_id,
            json.dumps({"purchase_project": "办公", "purpose": "办公"}, ensure_ascii=False),
            stamp,
            stamp,
        ),
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO borrow_records
            (borrow_no, item_type, item_name, quantity, returned_quantity, claimed_quantity,
             status, borrower_id, borrow_form_id, data_json, created_at, updated_at)
        VALUES (?, 'material', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "BR-PARTIAL",
            "测试物料",
            quantity,
            returned_quantity,
            claimed_quantity,
            status,
            borrower_id,
            form_id,
            "{}",
            stamp,
            stamp,
        ),
    )
    return cursor.lastrowid


def _login(client, username, password="test"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    return data["user"]


def test_partial_claim_keeps_remaining_borrowed(client, db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "claim_user")
    record_id = _create_borrow_record(cursor, user_id, quantity=10)
    db.commit()
    _login(client, "claim_user")
    resp = client.post(f"/api/borrows/{record_id}/claim", json={"quantity": 3})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "borrowed"
    assert body["claimed_quantity"] == 3

    cursor.execute(
        "SELECT quantity, claimed_quantity, status FROM borrow_records WHERE id = ?",
        (record_id,),
    )
    row = dict(cursor.fetchone())
    assert row["claimed_quantity"] == 3
    assert row["status"] == "borrowed"

    cursor.execute("SELECT id, form_type, status FROM workflow_forms WHERE form_type = 'borrow_claim'")
    form = dict(cursor.fetchone())
    assert form["status"] == "completed"
    mine = client.get("/api/my-claims").get_json()
    assert mine["total"] == 1
    assert mine["items"][0]["source_kind"] == "borrow_claim"
    assert mine["items"][0]["issued_quantity"] == 3
    assert mine["items"][0]["can_return"] is True
    assert mine["items"][0]["returnable_quantity"] == 3

    mine = client.get("/api/borrows/mine").get_json()["items"]
    match = next(item for item in mine if int(item["id"]) == record_id)
    assert match["remaining_quantity"] == 7


def test_full_remaining_claim_marks_claimed(client, db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "claim_full")
    record_id = _create_borrow_record(cursor, user_id, quantity=10, claimed_quantity=3)
    db.commit()
    _login(client, "claim_full")
    resp = client.post(f"/api/borrows/{record_id}/claim", json={"quantity": 7})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "claimed"
    cursor.execute("SELECT claimed_quantity, status FROM borrow_records WHERE id = ?", (record_id,))
    row = dict(cursor.fetchone())
    assert row["claimed_quantity"] == 10
    assert row["status"] == "claimed"


def test_claim_rejects_over_remaining(client, db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "claim_over")
    record_id = _create_borrow_record(cursor, user_id, quantity=5)
    db.commit()
    _login(client, "claim_over")
    resp = client.post(f"/api/borrows/{record_id}/claim", json={"quantity": 6})
    assert resp.status_code == 400


def test_return_after_partial_claim_does_not_mark_claimed_as_returned(db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "return_claim")
    record_id = _create_borrow_record(cursor, user_id, quantity=10, claimed_quantity=3)
    db.commit()
    update_borrow_return_balance(cursor, record_id, 7)
    cursor.execute(
        "SELECT returned_quantity, claimed_quantity, status FROM borrow_records WHERE id = ?",
        (record_id,),
    )
    row = dict(cursor.fetchone())
    assert row["returned_quantity"] == 7
    assert row["claimed_quantity"] == 3
    assert row["status"] == "claimed"


def test_partial_transfer_split_accept(client, db):
    cursor = db.cursor()
    user_a = _create_user(cursor, "split_a")
    user_b = _create_user(cursor, "split_b")
    record_id = _create_borrow_record(cursor, user_a, quantity=10)
    db.commit()
    _login(client, "split_a")
    resp = client.post(
        f"/api/borrows/{record_id}/transfer",
        json={"receiver_id": user_b, "quantity": 3},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    child_id = resp.get_json()["borrow_record_id"]
    assert child_id != record_id

    cursor.execute("SELECT quantity, status FROM borrow_records WHERE id = ?", (record_id,))
    parent = dict(cursor.fetchone())
    assert parent["quantity"] == 7
    assert parent["status"] == "borrowed"

    cursor.execute("SELECT quantity, status, borrower_id, data_json FROM borrow_records WHERE id = ?", (child_id,))
    child = dict(cursor.fetchone())
    assert child["quantity"] == 3
    assert child["status"] == "transferring"
    assert int(child["borrower_id"]) == user_a
    assert int(json.loads(child["data_json"])["transfer_from_record_id"]) == record_id

    _login(client, "split_b")
    accept = client.post(f"/api/transfers/{child_id}/accept")
    assert accept.status_code == 200, accept.get_data(as_text=True)
    cursor.execute("SELECT borrower_id, status FROM borrow_records WHERE id = ?", (child_id,))
    accepted = dict(cursor.fetchone())
    assert accepted["borrower_id"] == user_b
    assert accepted["status"] == "borrowed"


def test_partial_transfer_reject_merges_back(client, db):
    cursor = db.cursor()
    user_a = _create_user(cursor, "merge_a")
    user_b = _create_user(cursor, "merge_b")
    record_id = _create_borrow_record(cursor, user_a, quantity=10)
    db.commit()
    _login(client, "merge_a")
    resp = client.post(
        f"/api/borrows/{record_id}/transfer",
        json={"receiver_id": user_b, "quantity": 3},
    )
    child_id = resp.get_json()["borrow_record_id"]
    _login(client, "merge_b")
    reject = client.post(f"/api/transfers/{child_id}/reject")
    assert reject.status_code == 200, reject.get_data(as_text=True)

    cursor.execute("SELECT quantity, status FROM borrow_records WHERE id = ?", (record_id,))
    parent = dict(cursor.fetchone())
    assert parent["quantity"] == 10
    assert parent["status"] == "borrowed"
    cursor.execute("SELECT id FROM borrow_records WHERE id = ?", (child_id,))
    assert cursor.fetchone() is None


def test_partial_transfer_reject_rebuilds_when_parent_settled(client, db):
    cursor = db.cursor()
    user_a = _create_user(cursor, "rebuild_a")
    user_b = _create_user(cursor, "rebuild_b")
    record_id = _create_borrow_record(cursor, user_a, quantity=10, claimed_quantity=2)
    db.commit()
    _login(client, "rebuild_a")
    resp = client.post(
        f"/api/borrows/{record_id}/transfer",
        json={"receiver_id": user_b, "quantity": 8},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    child_id = resp.get_json()["borrow_record_id"]
    cursor.execute("SELECT quantity, status, claimed_quantity FROM borrow_records WHERE id = ?", (record_id,))
    parent = dict(cursor.fetchone())
    assert parent["quantity"] == 2
    assert parent["claimed_quantity"] == 2
    assert parent["status"] == "claimed"

    _login(client, "rebuild_b")
    reject = client.post(f"/api/transfers/{child_id}/reject")
    assert reject.status_code == 200, reject.get_data(as_text=True)
    cursor.execute("SELECT id FROM borrow_records WHERE id = ?", (child_id,))
    assert cursor.fetchone() is None
    cursor.execute(
        """
        SELECT quantity, status, borrower_id
        FROM borrow_records
        WHERE borrower_id = ? AND status = 'borrowed'
        ORDER BY id DESC LIMIT 1
        """,
        (user_a,),
    )
    restored = dict(cursor.fetchone())
    assert restored["quantity"] == 8
    assert restored["borrower_id"] == user_a


def test_borrow_claim_enters_production_material_pool(client, db):
    cursor = db.cursor()
    now = app_module.now_text()
    user_id = _create_user(cursor, "pool_claim")
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES (?, ?, '个', ?, ?)",
        ("BPCLAIM001", "转领用进池", now, now),
    )
    material_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, received_date, stock_source, created_at, updated_at)
        VALUES (?, 'B001', 0, 1, '2026-09-01', 'formal', ?, ?)
        """,
        (material_id, now, now),
    )
    batch_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'borrow', '借用', 'completed', 'completed', ?, ?, ?, ?)
        """,
        (
            f"JY-POOL-{user_id}",
            user_id,
            json.dumps({"purchase_project": "办公", "purpose": "办公"}, ensure_ascii=False),
            now,
            now,
        ),
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO borrow_records
            (borrow_no, item_type, item_ref_id, material_id, item_code, item_name, unit, quantity,
             returned_quantity, claimed_quantity, status, borrower_id, borrow_form_id, data_json, created_at, updated_at)
        VALUES ('JYTEST', 'material', ?, ?, 'BPCLAIM001', '转领用进池', '个', 8, 0, 0, 'borrowed', ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            material_id,
            user_id,
            form_id,
            json.dumps(
                {"consumed_batches": [{"batch_id": batch_id, "batch_no": "B001", "quantity": 8, "unit_price": 1}]},
                ensure_ascii=False,
            ),
            now,
            now,
        ),
    )
    record_id = cursor.lastrowid
    db.commit()
    _login(client, "pool_claim")
    resp = client.post(f"/api/borrows/{record_id}/claim", json={"quantity": 8})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _login(client, "warehouse")
    pool = client.get("/api/production/material-pool").get_json()["items"]
    match = next(item for item in pool if int(item["material_id"]) == int(material_id))
    assert abs(float(match["available_quantity"]) - 8) < 1e-9
