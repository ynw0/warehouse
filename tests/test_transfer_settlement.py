
import pytest

import app as app_module

from warehouse_suit.inventory_service import (
    add_inventory_batch,
    begin_inventory_transaction,
    consume_inventory_fifo,
)
from warehouse_suit.transfer_service import record_transfer_formal_inbound


_COUNTER = [0]


def login(client):
    response = client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def enable_temporary(client):
    login(client)
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": True},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def seed_material(db):
    _COUNTER[0] += 1
    code = f"9060010001{_COUNTER[0]:04d}"
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, unit, created_at, updated_at)
        VALUES (?, ?, '个', ?, ?)
        """,
        (code, f"结算测试物料{_COUNTER[0]}", app_module.now_text(), app_module.now_text()),
    )
    db.commit()
    return int(cursor.lastrowid)


def create_temporary_batch(client, material_id, quantity, key):
    response = client.post(
        "/api/temporary-inventory/batches",
        json={
            "material_id": material_id,
            "quantity": quantity,
            "unit_price": 2,
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "location": "A",
            "remark": "结算测试",
            "operation_key": key,
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return int(response.get_json()["batch_id"])


def seed_obligation(db, material_id, batch_id, pending_quantity=3, applicant_id=None):
    cursor = db.cursor()
    user_id = int(applicant_id or cursor.execute(
        "SELECT id FROM users WHERE username = 'admin'"
    ).fetchone()[0])
    cursor.execute(
        """
        INSERT INTO workflow_forms (
            form_no, form_type, status, current_step, applicant_id,
            created_at, updated_at
        ) VALUES (?, 'claim', 'completed', 'completed', ?, ?, ?)
        """,
        (
            f"SETTLE-OLD-{material_id}-{user_id}",
            user_id,
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    old_form_id = int(cursor.lastrowid)
    material = cursor.execute(
        "SELECT material_code, name, unit FROM materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    cursor.execute(
        """
        INSERT INTO workflow_items (
            form_id, material_id, material_code, material_name, unit,
            request_quantity, outbound_quantity, stock_source, data_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'temporary', '{}')
        """,
        (
            old_form_id,
            material_id,
            material["material_code"],
            material["name"],
            material["unit"],
            pending_quantity,
            pending_quantity,
        ),
    )
    item_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO stock_records (
            material_id, operation_type, quantity, balance_after,
            operation_date, remark, batch_id, form_no, stock_source,
            business_type, operation_key, operator_id, workflow_item_id
        ) VALUES (?, 'out', ?, ?, ?, ?, ?, ?, 'temporary', 'claim_outbound', ?, ?, ?)
        """,
        (
            material_id,
            pending_quantity,
            0,
            app_module.today_text(),
            "历史临时领用出库",
            batch_id,
            f"SETTLE-OLD-{material_id}-{user_id}",
            f"settlement-outbound:{material_id}:{user_id}",
            user_id,
            item_id,
        ),
    )
    stock_record_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO temporary_issue_obligations (
            applicant_id, material_id, source_batch_id, claim_form_id,
            claim_item_id, stock_record_id, issued_quantity, settled_quantity,
            status, operation_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?, ?)
        """,
        (
            user_id,
            material_id,
            batch_id,
            old_form_id,
            item_id,
            stock_record_id,
            pending_quantity,
            f"settlement-obligation:{material_id}:{user_id}",
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def create_formal_inbound_for_task(db, task_id, material_id, quantity):
    cursor = db.cursor()
    link = cursor.execute(
        """
        SELECT acceptance_form_id, acceptance_item_id
        FROM transfer_acceptance_links
        WHERE task_id = ? ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    begin_inventory_transaction(db)
    batch_id = add_inventory_batch(
        cursor,
        material_id,
        quantity,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "zone_name": "A",
            "remark": "转移正式入库",
        },
        f"SETTLE-IN-{task_id}",
        operation_key=f"settlement-inbound:{task_id}",
    )
    record_transfer_formal_inbound(
        cursor,
        link["acceptance_form_id"],
        link["acceptance_item_id"],
        batch_id,
        quantity,
    )
    db.commit()
    return batch_id
def create_transfer_with_formal_inbound(client, db, material_id, target):
    response = client.post(
        "/api/temporary-inventory/transfers",
        json={
            "material_id": material_id,
            "idempotency_key": f"settlement-transfer:{material_id}",
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    task_id = int(response.get_json()["task"]["id"])
    claim = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/claim", json={}
    )
    assert claim.status_code == 200, claim.get_data(as_text=True)
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    start = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": f"settlement-acceptance:{task_id}",
            "validator_ids": [admin_id],
            "unit_price": 2,
        },
    )
    assert start.status_code == 200, start.get_data(as_text=True)
    formal_batch_id = create_formal_inbound_for_task(
        db, task_id, material_id, target
    )
    return task_id, formal_batch_id




def prepare_transfer(client, db, temporary_quantity=5, obligation_quantity=3):
    material_id = seed_material(db)
    enable_temporary(client)
    temp_batch_id = create_temporary_batch(
        client, material_id, temporary_quantity, f"settlement-temp:{material_id}"
    )
    obligation_id = 0
    if obligation_quantity:
        obligation_id = seed_obligation(
            db, material_id, temp_batch_id, obligation_quantity
        )
    response = client.post(
        "/api/temporary-inventory/transfers",
        json={
            "material_id": material_id,
            "idempotency_key": f"settlement-transfer:{material_id}",
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    task_id = int(response.get_json()["task"]["id"])
    assert client.post(
        f"/api/temporary-inventory/transfers/{task_id}/claim", json={}
    ).status_code == 200
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    start = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": f"settlement-acceptance:{task_id}",
            "validator_ids": [admin_id],
            "unit_price": 2,
        },
    )
    assert start.status_code == 200, start.get_data(as_text=True)
    target = temporary_quantity + obligation_quantity
    formal_batch_id = create_formal_inbound_for_task(
        db, task_id, material_id, target
    )
    return material_id, temp_batch_id, obligation_id, task_id, formal_batch_id


def test_auto_claim_reserves_outbounds_settles_and_completes(client, db):
    material_id, temp_batch_id, obligation_id, task_id, formal_batch_id = (
        prepare_transfer(client, db)
    )
    processed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert processed.status_code == 200, processed.get_data(as_text=True)
    assert processed.get_json()["task"]["status"] == "auto_claim_pending"

    reservation = db.execute(
        """
        SELECT id, reserved_quantity, consumed_quantity, status
        FROM inventory_reservations WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    assert tuple(reservation)[1:] == (3, 0, "active")
    auto_claim = db.execute(
        """
        SELECT id, current_claim_form_id, quantity, status
        FROM transfer_auto_claims WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    assert tuple(auto_claim)[2:] == (3, "approval_pending")
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (formal_batch_id,)
    ).fetchone()[0] == 8
    code = db.execute(
        "SELECT material_code FROM materials WHERE id = ?", (material_id,)
    ).fetchone()[0]
    options = client.get(f"/api/claims/materials?keyword={code}").get_json()
    assert len(options) == 1
    assert options[0]["formal_available_quantity"] == 5
    borrow_options = client.get(
        f"/api/borrow/items?keyword={code}"
    ).get_json()["items"]
    borrow_row = next(
        row for row in borrow_options if int(row["material_id"]) == material_id
    )
    assert borrow_row["formal_available_quantity"] == 5
    assert db.execute(
        "SELECT quantity FROM inventory WHERE material_id = ?", (material_id,)
    ).fetchone()[0] == 8

    begin_inventory_transaction(db)
    with pytest.raises(ValueError):
        consume_inventory_fifo(
            db.cursor(),
            material_id,
            6,
            "RESERVATION-PROTECT",
            "2026-07-15",
            "普通业务不能消耗预留",
            stock_source="formal",
        )
    db.rollback()
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (formal_batch_id,)
    ).fetchone()[0] == 8

    auto_form_id = int(auto_claim["current_claim_form_id"])
    assert client.put(
        f"/api/workflows/{auto_form_id}", json={"title": "伪造修改"}
    ).status_code == 400
    assert client.delete(f"/api/workflows/{auto_form_id}").status_code == 400


    leader = client.post(
        f"/api/claims/{auto_claim['current_claim_form_id']}/leader",
        json={"decision": "同意"},
    )
    assert leader.status_code == 200, leader.get_data(as_text=True)
    outbound = client.post(
        f"/api/claims/{auto_claim['current_claim_form_id']}/outbound",
        json={"items": [{"outbound_quantity": 999}]},
    )
    assert outbound.status_code == 200, outbound.get_data(as_text=True)

    assert tuple(
        db.execute(
            "SELECT quantity, inventory_status FROM material_batches WHERE id = ?",
            (temp_batch_id,),
        ).fetchone()
    ) == (0, "transferred")
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (formal_batch_id,)
    ).fetchone()[0] == 5
    assert db.execute(
        "SELECT quantity FROM inventory WHERE material_id = ?", (material_id,)
    ).fetchone()[0] == 5
    assert tuple(
        db.execute(
            "SELECT settled_quantity, status FROM temporary_issue_obligations WHERE id = ?",
            (obligation_id,),
        ).fetchone()
    ) == (3, "settled")
    outbound_record = db.execute(
        """
        SELECT transfer_auto_claim_id, inventory_reservation_id
        FROM stock_records
        WHERE transfer_task_id = ? AND business_type = 'claim_outbound'
        """,
        (task_id,),
    ).fetchone()
    assert tuple(outbound_record) == (
        int(auto_claim["id"]), int(reservation["id"])
    )
    assert tuple(
        db.execute(
            "SELECT consumed_quantity, status FROM inventory_reservations WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    ) == (3, "consumed")
    assert tuple(
        db.execute(
            "SELECT status, active_key FROM inventory_transfer_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    ) == ("completed", None)
    assert db.execute(
        """
        SELECT COUNT(*) FROM stock_records
        WHERE transfer_task_id = ? AND business_type = 'temporary_transfer_close'
        """,
        (task_id,),
    ).fetchone()[0] == 1
    notification = db.execute(
        "SELECT data_json FROM notifications WHERE title = ? ORDER BY id DESC LIMIT 1",
        ("临时物料转正式库已全部完成",),
    ).fetchone()
    notification_data = app_module.parse_json(notification["data_json"], {})
    assert notification_data["business_type"] == "temporary_transfer_completed"
    assert int(notification_data["transfer_task_id"]) == task_id

    repeated = client.post(
        f"/api/claims/{auto_claim['current_claim_form_id']}/outbound",
        json={},
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["idempotent"] is True
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0] == 1


def test_transfer_without_obligation_fast_completes(client, db):
    _, temp_batch_id, _, task_id, formal_batch_id = prepare_transfer(
        client, db, temporary_quantity=4, obligation_quantity=0
    )
    response = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["task"]["status"] == "completed"
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (formal_batch_id,)
    ).fetchone()[0] == 4
    assert tuple(
        db.execute(
            "SELECT quantity, inventory_status FROM material_batches WHERE id = ?",
            (temp_batch_id,),
        ).fetchone()
    ) == (0, "transferred")

def test_auto_claim_rejection_keeps_reservation_and_retry_reuses_logic(client, db):
    _, temp_batch_id, obligation_id, task_id, _ = prepare_transfer(client, db)
    processed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},

    )
    assert processed.status_code == 200, processed.get_data(as_text=True)
    original = db.execute(
        """
        SELECT id, current_claim_form_id, attempt_no
        FROM transfer_auto_claims WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    rejected = client.post(
        f"/api/claims/{original['current_claim_form_id']}/leader",
        json={"decision": "不同意", "remark": "审批配置待核对"},
    )
    assert rejected.status_code == 200, rejected.get_data(as_text=True)
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "auto_claim_exception"
    assert db.execute(
        "SELECT status FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == "active"
    assert tuple(
        db.execute(
            "SELECT settled_quantity, status FROM temporary_issue_obligations WHERE id = ?",
            (obligation_id,),
        ).fetchone()
    ) == (0, "pending")
    assert db.execute(
        "SELECT inventory_status FROM material_batches WHERE id = ?",
        (temp_batch_id,),
    ).fetchone()[0] == "transfer_locked"

    retried = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/retry-auto-claims",
        json={},
    )
    assert retried.status_code == 200, retried.get_data(as_text=True)
    current = db.execute(
        """
        SELECT id, current_claim_form_id, attempt_no, status
        FROM transfer_auto_claims WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    assert int(current["id"]) == int(original["id"])
    assert int(current["current_claim_form_id"]) != int(
        original["current_claim_form_id"]
    )
    assert tuple(current[2:]) == (2, "approval_pending")
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 1
    assert db.execute(
        """
        SELECT COUNT(*) FROM workflow_forms
        WHERE origin_type = 'temporary_transfer_auto_claim' AND origin_ref_id = ?
        """,
        (task_id,),
    ).fetchone()[0] == 2


def test_feature_toggle_restores_auto_claim_pending_without_duplicates(client, db):
    _, _, _, task_id, _ = prepare_transfer(client, db)
    processed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert processed.status_code == 200, processed.get_data(as_text=True)
    auto_form_id = int(
        db.execute(
            "SELECT current_claim_form_id FROM transfer_auto_claims WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]
    )
    counts_before = tuple(
        db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?),
                (SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?),
                (SELECT COUNT(*) FROM workflow_forms
                 WHERE origin_type = 'temporary_transfer_auto_claim'
                   AND origin_ref_id = ?)
            """,
            (task_id, task_id, task_id),
        ).fetchone()
    )
    disabled = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": False},
    )
    assert disabled.status_code == 200, disabled.get_data(as_text=True)
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "paused"
    blocked = client.post(
        f"/api/claims/{auto_form_id}/leader", json={"decision": "同意"}
    )
    assert blocked.status_code == 400, blocked.get_data(as_text=True)
    assert db.execute(
        "SELECT status FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == "active"
    enabled = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": True},
    )
    assert enabled.status_code == 200, enabled.get_data(as_text=True)
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "auto_claim_pending"
    counts_after = tuple(
        db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?),
                (SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?),
                (SELECT COUNT(*) FROM workflow_forms
                 WHERE origin_type = 'temporary_transfer_auto_claim'
                   AND origin_ref_id = ?)
            """,
            (task_id, task_id, task_id),
        ).fetchone()
    )
    assert counts_after == counts_before


def test_multi_user_obligations_create_one_atomic_claim_per_applicant(client, db):
    material_id = seed_material(db)
    enable_temporary(client)
    batch_id = create_temporary_batch(
        client, material_id, 5, f"settlement-temp:{material_id}"
    )
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    test_user_id = int(
        db.execute("SELECT id FROM users WHERE username = 'testuser'").fetchone()[0]
    )
    seed_obligation(db, material_id, batch_id, 3, admin_id)
    seed_obligation(db, material_id, batch_id, 2, test_user_id)
    task_id, _ = create_transfer_with_formal_inbound(client, db, material_id, 10)
    response = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["task"]["status"] == "auto_claim_pending"
    claims = db.execute(
        """
        SELECT applicant_id, quantity, current_claim_form_id
        FROM transfer_auto_claims WHERE task_id = ? ORDER BY applicant_id
        """,
        (task_id,),
    ).fetchall()
    assert len(claims) == 2
    assert {int(row["applicant_id"]): float(row["quantity"]) for row in claims} == {
        admin_id: 3,
        test_user_id: 2,
    }
    assert all(row["current_claim_form_id"] for row in claims)
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claim_obligations WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT SUM(reserved_quantity) FROM inventory_reservations WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0] == 5
    for claim in claims:
        assert client.post(
            f"/api/claims/{claim['current_claim_form_id']}/leader",
            json={"decision": "同意"},
        ).status_code == 200
        assert client.post(
            f"/api/claims/{claim['current_claim_form_id']}/outbound", json={}
        ).status_code == 200
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "completed"
    assert db.execute(
        "SELECT COUNT(*) FROM temporary_issue_obligations WHERE status = 'settled' AND material_id = ?",
        (material_id,),
    ).fetchone()[0] == 2


def test_reservation_never_falls_back_to_unrelated_formal_batch(client, db):
    material_id, _, _, task_id, formal_batch_id = prepare_transfer(client, db)
    begin_inventory_transaction(db)
    unrelated_batch_id = add_inventory_batch(
        db.cursor(),
        material_id,
        100,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-14",
            "zone_name": "B",
            "remark": "非本次转移正式批次",
        },
        "UNRELATED-FORMAL",
        operation_key=f"unrelated-formal:{material_id}",
    )
    db.commit()
    db.execute(
        "UPDATE material_batches SET quantity = 2 WHERE id = ?", (formal_batch_id,)
    )
    db.commit()
    response = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert response.status_code == 409, response.get_data(as_text=True)
    assert tuple(
        db.execute(
            "SELECT status, error_code FROM inventory_transfer_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    ) == ("auto_claim_exception", "auto_claim_process_failed")
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (unrelated_batch_id,)
    ).fetchone()[0] == 100


def test_invalid_obligation_user_rolls_back_and_can_retry_after_repair(client, db):
    material_id = seed_material(db)
    enable_temporary(client)
    batch_id = create_temporary_batch(
        client, material_id, 4, f"settlement-temp:{material_id}"
    )
    test_user_id = int(
        db.execute("SELECT id FROM users WHERE username = 'testuser'").fetchone()[0]
    )
    seed_obligation(db, material_id, batch_id, 3, test_user_id)
    task_id, _ = create_transfer_with_formal_inbound(client, db, material_id, 7)
    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (test_user_id,))
    db.commit()
    failed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert failed.status_code == 409, failed.get_data(as_text=True)
