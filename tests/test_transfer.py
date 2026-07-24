import json

import pytest

import app as app_module


def _create_user(cursor, username, password="test"):
    cursor.execute(
        "INSERT INTO users (username, display_name, department, password, updated_at) VALUES (?, ?, ?, ?, ?)",
        (username, username, "", app_module.generate_password_hash(password), app_module.now_text()),
    )
    return cursor.lastrowid


def _create_borrow_record(cursor, borrower_id, item_name="Test Item", status="borrowed"):
    cursor.execute(
        """
        INSERT INTO borrow_records (borrow_no, item_name, quantity, status, borrower_id, data_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("BR001", item_name, 1, status, borrower_id, "{}", app_module.now_text(), app_module.now_text()),
    )
    return cursor.lastrowid


def _login(client, username, password="test"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    return data["user"]


@pytest.fixture
def user_a(db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "user_a")
    db.commit()
    return user_id


@pytest.fixture
def user_b(db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "user_b")
    db.commit()
    return user_id


@pytest.fixture
def user_c(db):
    cursor = db.cursor()
    user_id = _create_user(cursor, "user_c")
    db.commit()
    return user_id


@pytest.fixture
def borrow_record_a(db, user_a):
    cursor = db.cursor()
    record_id = _create_borrow_record(cursor, user_a)
    db.commit()
    return record_id


def test_transfer_initiate_success(client, db, user_a, user_b, borrow_record_a):
    _login(client, "user_a")
    resp = client.post(
        f"/api/borrows/{borrow_record_a}/transfer",
        json={"receiver_id": user_b},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    cursor = db.cursor()
    cursor.execute("SELECT status, data_json FROM borrow_records WHERE id = ?", (borrow_record_a,))
    row = cursor.fetchone()
    assert row["status"] == "transferring"
    record_data = app_module.parse_json(row["data_json"], {})
    assert int(record_data.get("transfer_receiver_id")) == user_b

    cursor.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND title = ?",
        (user_b, "转借请求"),
    )
    assert cursor.fetchone()[0] == 1


def test_transfer_initiate_self(client, user_a, borrow_record_a):
    _login(client, "user_a")
    resp = client.post(
        f"/api/borrows/{borrow_record_a}/transfer",
        json={"receiver_id": user_a},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "自己" in data["error"]


def test_transfer_initiate_not_borrowed(client, db, user_a, user_b):
    cursor = db.cursor()
    record_id = _create_borrow_record(cursor, user_a, status="returned")
    db.commit()
    _login(client, "user_a")
    resp = client.post(
        f"/api/borrows/{record_id}/transfer",
        json={"receiver_id": user_b},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_transfer_initiate_wrong_user(client, user_a, user_c, borrow_record_a):
    _login(client, "user_c")
    resp = client.post(
        f"/api/borrows/{borrow_record_a}/transfer",
        json={"receiver_id": user_a},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_transfer_accept_success(client, db, user_a, user_b, borrow_record_a):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE borrow_records SET status = ?, data_json = ? WHERE id = ?",
        ("transferring", json.dumps({"transfer_receiver_id": user_b}), borrow_record_a),
    )
    db.commit()

    _login(client, "user_b")
    resp = client.post(f"/api/transfers/{borrow_record_a}/accept")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    cursor = db.cursor()
    cursor.execute("SELECT borrower_id, status FROM borrow_records WHERE id = ?", (borrow_record_a,))
    row = cursor.fetchone()
    assert row["borrower_id"] == user_b
    assert row["status"] == "borrowed"


def test_transfer_accept_wrong_user(client, db, user_a, user_b, user_c, borrow_record_a):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE borrow_records SET status = ?, data_json = ? WHERE id = ?",
        ("transferring", json.dumps({"transfer_receiver_id": user_b}), borrow_record_a),
    )
    db.commit()

    _login(client, "user_c")
    resp = client.post(f"/api/transfers/{borrow_record_a}/accept")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_transfer_reject_success(client, db, user_a, user_b, borrow_record_a):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE borrow_records SET status = ?, data_json = ? WHERE id = ?",
        ("transferring", json.dumps({"transfer_receiver_id": user_b}), borrow_record_a),
    )
    db.commit()

    _login(client, "user_b")
    resp = client.post(f"/api/transfers/{borrow_record_a}/reject")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    cursor = db.cursor()
    cursor.execute("SELECT borrower_id, status FROM borrow_records WHERE id = ?", (borrow_record_a,))
    row = cursor.fetchone()
    assert row["borrower_id"] == user_a
    assert row["status"] == "borrowed"


def test_transfer_during_transfer(client, db, user_a, user_b, borrow_record_a):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE borrow_records SET status = ?, data_json = ? WHERE id = ?",
        ("transferring", json.dumps({"transfer_receiver_id": user_b}), borrow_record_a),
    )
    db.commit()

    _login(client, "user_a")
    resp = client.post(
        f"/api/borrows/{borrow_record_a}/transfer",
        json={"receiver_id": user_b},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_transfer_notification_created(client, db, user_a, user_b, borrow_record_a):
    _login(client, "user_a")
    resp = client.post(
        f"/api/borrows/{borrow_record_a}/transfer",
        json={"receiver_id": user_b},
    )
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute(
        "SELECT id, title, body, data_json FROM notifications WHERE user_id = ?",
        (user_b,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row["title"] == "转借请求"
    notification_data = app_module.parse_json(row["data_json"], {})
    assert notification_data.get("borrow_record_id") == borrow_record_a


def test_transfer_accept_notification(client, db, user_a, user_b, borrow_record_a):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE borrow_records SET status = ?, data_json = ? WHERE id = ?",
        ("transferring", json.dumps({"transfer_receiver_id": user_b}), borrow_record_a),
    )
    db.commit()

    _login(client, "user_b")
    resp = client.post(f"/api/transfers/{borrow_record_a}/accept")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute(
        "SELECT title, body, data_json FROM notifications WHERE user_id = ?",
        (user_a,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row["title"] == "转借已接受"
    notification_data = app_module.parse_json(row["data_json"], {})
    assert notification_data.get("borrow_record_id") == borrow_record_a


def test_transfer_reject_notification(client, db, user_a, user_b, borrow_record_a):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE borrow_records SET status = ?, data_json = ? WHERE id = ?",
        ("transferring", json.dumps({"transfer_receiver_id": user_b}), borrow_record_a),
    )
    db.commit()

    _login(client, "user_b")
    resp = client.post(f"/api/transfers/{borrow_record_a}/reject")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute(
        "SELECT title, body, data_json FROM notifications WHERE user_id = ?",
        (user_a,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row["title"] == "转借已拒绝"
    notification_data = app_module.parse_json(row["data_json"], {})
    assert notification_data.get("borrow_record_id") == borrow_record_a


def test_transfer_not_found(client, user_a):
    _login(client, "user_a")
    resp = client.post("/api/transfers/99999/accept")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
