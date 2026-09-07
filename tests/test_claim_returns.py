"""Return balances, ownership and ledger integrity using isolated fixtures."""

import json

import pytest

from warehouse_suit.claim_service import create_temporary_issue_obligations
from warehouse_suit.inventory_service import add_inventory_batch, consume_inventory_fifo
from warehouse_suit.settings import set_setting
from tests.test_claim_workflow import _login


def seed_issue(db, source="formal"):
    cursor = db.cursor()
    user_id = cursor.execute("SELECT id FROM users WHERE username='testuser'").fetchone()[0]
    set_setting(cursor, "workflow_settings", json.dumps({"temporary_inventory_enabled": True}))
    cursor.execute("INSERT INTO materials (material_code, name, unit) VALUES ('10200101010001', '退库物料', '个')")
    material_id = cursor.lastrowid
    batch_id = add_inventory_batch(cursor, material_id, 10, 2, {}, "IN-RETURN-TEST", stock_source=source)
    cursor.execute("""
        INSERT INTO workflow_forms (form_no, form_type, title, status, current_step, applicant_id)
        VALUES ('CK-RETURN-TEST', 'claim', '领用', 'completed', 'completed', ?)
    """, (user_id,))
    form_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO workflow_items (form_id, material_id, material_code, material_name,
                                    request_quantity, outbound_quantity, stock_source)
        VALUES (?, ?, '10200101010001', '退库物料', 6, 6, ?)
    """, (form_id, material_id, source))
    item_id = cursor.lastrowid
    consumed = consume_inventory_fifo(cursor, material_id, 6, "CK-RETURN-TEST",
                                     stock_source=source, business_type="claim_outbound",
                                     workflow_item_id=item_id, operation_key="test-issue")
    if source == "temporary":
        create_temporary_issue_obligations(
            cursor, {"id": form_id, "applicant_id": user_id},
            {"id": item_id, "material_id": material_id, "stock_source": source}, consumed,
        )
    db.commit()
    return consumed[0]["stock_record_id"], batch_id, material_id


@pytest.mark.parametrize("source", ["formal", "temporary"])
def test_partial_return_duplicate_and_full_return(client, db, source):
    outbound_id, batch_id, material_id = seed_issue(db, source)
    _login(client, "testuser", "test")
    listing = client.get("/api/my-claims").get_json()
    assert listing["total"] == 1
    assert listing["items"][0]["returnable_quantity"] == 6
    url = f"/api/my-claims/{outbound_id}/return"
    payload = {"quantity": 2, "operation_key": "return-one"}
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.get_json()
    assert client.post(url, json=payload).get_json()["idempotent"] is True
    assert db.execute("SELECT quantity FROM material_batches WHERE id=?", (batch_id,)).fetchone()[0] == 6
    assert db.execute("SELECT COUNT(*) FROM claim_returns").fetchone()[0] == 1
    response = client.post(url, json={"quantity": 5, "operation_key": "too-many"})
    assert response.status_code == 400
    response = client.post(url, json={"quantity": 4, "operation_key": "return-two"})
    assert response.status_code == 200, response.get_json()
    row = client.get("/api/my-claims").get_json()["items"][0]
    assert row["issued_quantity"] == row["returned_quantity"] == 6
    assert row["returnable_quantity"] == 0
    assert db.execute("SELECT quantity FROM material_batches WHERE id=?", (batch_id,)).fetchone()[0] == 10
    ledger = db.execute("""
        SELECT SUM(quantity), SUM(amount) FROM stock_records
        WHERE material_id=? AND business_type='claim_return_inbound'
    """, (material_id,)).fetchone()
    assert tuple(ledger) == (6, 12)
    if source == "temporary":
        assert tuple(db.execute("SELECT issued_quantity, returned_quantity, status FROM temporary_issue_obligations").fetchone()) == (6, 6, "cancelled")
    else:
        assert db.execute("SELECT quantity FROM inventory WHERE material_id=?", (material_id,)).fetchone()[0] == 10


def test_other_user_cannot_view_or_return_and_invalid_quantity_is_rejected(client, db):
    outbound_id, batch_id, _ = seed_issue(db)
    _login(client, "warehouse", "test")
    assert client.get("/api/my-claims").get_json()["total"] == 0
    url = f"/api/my-claims/{outbound_id}/return"
    assert client.post(url, json={"quantity": 1, "operation_key": "wrong-user"}).status_code == 403
    _login(client, "testuser", "test")
    for value in (0, -1, "NaN", "Infinity", None):
        assert client.post(url, json={"quantity": value, "operation_key": "bad-quantity"}).status_code == 400
    assert db.execute("SELECT quantity FROM material_batches WHERE id=?", (batch_id,)).fetchone()[0] == 4
    assert db.execute("SELECT COUNT(*) FROM claim_returns").fetchone()[0] == 0


def test_ledger_failure_rolls_back_batch_and_return(client, db):
    outbound_id, batch_id, _ = seed_issue(db)
    _login(client, "testuser", "test")
    db.execute("""
        CREATE TRIGGER fail_return BEFORE INSERT ON claim_returns
        BEGIN SELECT RAISE(ABORT, 'forced return failure'); END
    """)
    db.commit()
    with pytest.raises(Exception, match="forced return failure"):
        client.post(f"/api/my-claims/{outbound_id}/return",
                    json={"quantity": 1, "operation_key": "rollback"})
    assert db.execute("SELECT quantity FROM material_batches WHERE id=?", (batch_id,)).fetchone()[0] == 4
    assert db.execute("SELECT COUNT(*) FROM stock_records WHERE business_type='claim_return_inbound'").fetchone()[0] == 0


def test_return_after_temporary_promotion_uses_current_batch_source(client, db):
    outbound_id, batch_id, material_id = seed_issue(db, "temporary")
    from warehouse_suit.temporary_inventory_service import promote_temporary_material
    user = dict(db.execute("SELECT * FROM users WHERE username='warehouse'").fetchone())
    promote_temporary_material(db.cursor(), material_id, user)
    db.commit()
    _login(client, "testuser", "test")
    response = client.post(f"/api/my-claims/{outbound_id}/return",
                           json={"quantity": 2, "operation_key": "promoted-return"})
    assert response.status_code == 200, response.get_json()
    assert client.get("/api/my-claims").get_json()["items"][0]["stock_source"] == "formal"
    assert db.execute("SELECT quantity FROM inventory WHERE material_id=?", (material_id,)).fetchone()[0] == 6
