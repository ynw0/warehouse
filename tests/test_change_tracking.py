# -*- coding: utf-8 -*-
"""Tests for change tracking and item history."""

import app as app_module


def _login_client(client):
    """Log in as admin and return the response data."""
    resp = client.post(
        "/api/login",
        json={"username": "admin", "password": "Costar@508"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    return data


def _create_semifinished_acceptance(cursor):
    """Insert a semifinished acceptance record and return its id."""
    cursor.execute(
        """
        INSERT INTO semifinished_acceptances
            (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date, applicant_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("SA001", "Test Semi", "SPEC-A", 10, "个", "2024-01-01", 1, app_module.now_text(), app_module.now_text()),
    )
    return cursor.lastrowid


def _create_semifinished_inventory(cursor, acceptance_id):
    """Insert a semifinished inventory item linked to an acceptance."""
    cursor.execute(
        """
        INSERT INTO semifinished_inventory
            (acceptance_id, name, spec, unit, quantity, acceptance_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (acceptance_id, "Test Semi", "SPEC-A", "个", 10, "2024-01-01", app_module.now_text(), app_module.now_text()),
    )
    return cursor.lastrowid


def _create_borrow_record(cursor, item_ref_id, borrower_id=1, borrow_no="B001", created_at=None):
    """Insert a borrow record for a semifinished item."""
    ts = created_at or app_module.now_text()
    cursor.execute(
        """
        INSERT INTO borrow_records
            (borrow_no, item_type, item_ref_id, item_code, item_name, spec, unit, quantity,
             status, borrower_id, outbound_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (borrow_no, "semifinished", item_ref_id, "CODE001", "Test Semi", "SPEC-A", "个", 1,
         "borrowed", borrower_id, "2024-01-02", ts, ts),
    )
    return cursor.lastrowid


def test_save_change_record(db):
    """Call save_borrow_change() and verify the row exists in borrow_change_records."""
    cursor = db.cursor()
    borrow_id = _create_borrow_record(cursor, item_ref_id=999, borrower_id=1)
    db.commit()

    change_id = app_module.save_borrow_change(
        cursor, borrow_id, "software", "Updated firmware", "v2.0", "是"
    )
    db.commit()

    cursor.execute("SELECT * FROM borrow_change_records WHERE id = ?", (change_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row["borrow_record_id"] == borrow_id
    assert row["change_type"] == "software"
    assert row["change_detail"] == "Updated firmware"
    assert row["version_after"] == "v2.0"
    assert row["normal_use"] == "是"


def test_get_history_with_changes(db):
    """Item with borrow + change → history returns acceptance + borrow + change records."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    borrow_id = _create_borrow_record(cursor, inv_id)
    app_module.save_borrow_change(cursor, borrow_id, "software", "FW update", "v1.1", "是")
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    assert result["item"]["name"] == "Test Semi"
    history = result["history"]
    event_types = [h["event_type"] for h in history]
    assert "入库" in event_types
    assert "借出" in event_types
    assert "变更" in event_types


def test_get_history_no_borrows(db):
    """Unborrowed item → returns acceptance record only."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    history = result["history"]
    assert len(history) == 1
    assert history[0]["event_type"] == "入库"


def test_get_history_pagination(db):
    """Page 1 vs Page 2 → disjoint results, correct total."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    for i in range(5):
        _create_borrow_record(cursor, inv_id, borrow_no=f"B{i:03d}")
    db.commit()

    page1 = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=2)
    page2 = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=2, limit=2)

    assert page1["total"] == 5
    assert page2["total"] == 5
    p1_ids = {h.get("borrow_no") for h in page1["history"]}
    p2_ids = {h.get("borrow_no") for h in page2["history"]}
    borrow_nos_p1 = {bn for bn in p1_ids if bn}
    borrow_nos_p2 = {bn for bn in p2_ids if bn}
    assert not borrow_nos_p1 & borrow_nos_p2


def test_history_ordering(db):
    """Newest borrow records first (sort DESC by created_at)."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    _create_borrow_record(cursor, inv_id, borrow_no="B_OLD", created_at="2024-01-01 08:00:00")
    _create_borrow_record(cursor, inv_id, borrow_no="B_NEW", created_at="2024-01-03 08:00:00")
    _create_borrow_record(cursor, inv_id, borrow_no="B_MID", created_at="2024-01-02 08:00:00")
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    borrow_events = [h for h in result["history"] if h["event_type"] == "借出"]
    borrow_nos = [h["borrow_no"] for h in borrow_events]
    assert borrow_nos == ["B_NEW", "B_MID", "B_OLD"]


def test_change_type_software(db):
    """change_type='software' stored and retrieved correctly."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    borrow_id = _create_borrow_record(cursor, inv_id)
    app_module.save_borrow_change(cursor, borrow_id, "software", "Patch applied", "v1.2", "是")
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    change_events = [h for h in result["history"] if h["event_type"] == "变更"]
    assert len(change_events) == 1
    assert change_events[0]["change_type"] == "software"


def test_change_type_hardware(db):
    """change_type='hardware' stored and retrieved correctly."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    borrow_id = _create_borrow_record(cursor, inv_id)
    app_module.save_borrow_change(cursor, borrow_id, "hardware", "Replaced board", "revB", "否")
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    change_events = [h for h in result["history"] if h["event_type"] == "变更"]
    assert len(change_events) == 1
    assert change_events[0]["change_type"] == "hardware"


def test_normal_use_values(db):
    """normal_use='是'/'否' stored correctly."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    borrow_id = _create_borrow_record(cursor, inv_id)
    app_module.save_borrow_change(cursor, borrow_id, "software", "A", "v1", "是")
    app_module.save_borrow_change(cursor, borrow_id, "software", "B", "v2", "否")
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    change_events = [h for h in result["history"] if h["event_type"] == "变更"]
    normal_uses = [c["normal_use"] for c in change_events]
    assert "是" in normal_uses
    assert "否" in normal_uses


def test_multiple_change_cycles(db):
    """Item borrowed/returned 3 times → 3 change records."""
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    for i in range(3):
        borrow_id = _create_borrow_record(cursor, inv_id, borrow_no=f"C{i:03d}")
        app_module.save_borrow_change(cursor, borrow_id, "software", f"Change {i}", f"v{i}", "是")
    db.commit()

    result = app_module.get_item_change_history(cursor, "semifinished", inv_id, page=1, limit=20)
    change_events = [h for h in result["history"] if h["event_type"] == "变更"]
    assert len(change_events) == 3


def test_history_item_not_found(client):
    """Non-existent item → HTTP 404."""
    _login_client(client)
    resp = client.get("/api/items/semifinished/99999/history")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "error" in data


def test_http_history_with_changes(client, db):
    """Via HTTP: item with borrow + change returns correct history."""
    _login_client(client)
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    borrow_id = _create_borrow_record(cursor, inv_id)
    app_module.save_borrow_change(cursor, borrow_id, "software", "FW update", "v1.1", "是")
    db.commit()

    resp = client.get(f"/api/items/semifinished/{inv_id}/history")
    assert resp.status_code == 200
    data = resp.get_json()
    event_types = [h["event_type"] for h in data["history"]]
    assert "入库" in event_types
    assert "借出" in event_types
    assert "变更" in event_types


def test_http_history_pagination(client, db):
    """Via HTTP: pagination returns disjoint results and correct total."""
    _login_client(client)
    cursor = db.cursor()
    acc_id = _create_semifinished_acceptance(cursor)
    inv_id = _create_semifinished_inventory(cursor, acc_id)
    for i in range(5):
        _create_borrow_record(cursor, inv_id, borrow_no=f"P{i:03d}")
    db.commit()

    r1 = client.get(f"/api/items/semifinished/{inv_id}/history?page=1&limit=2")
    r2 = client.get(f"/api/items/semifinished/{inv_id}/history?page=2&limit=2")
    d1 = r1.get_json()
    d2 = r2.get_json()

    assert d1["total"] == 5
    assert d2["total"] == 5
    n1 = {h.get("borrow_no") for h in d1["history"] if h.get("borrow_no")}
    n2 = {h.get("borrow_no") for h in d2["history"] if h.get("borrow_no")}
    assert not n1 & n2
