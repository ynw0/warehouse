import json
import threading

import pytest

import warehouse_suit.transfer_settlement_service as settlement_service

import app as app_module

from warehouse_suit.inventory_service import (
    add_inventory_batch,
    begin_inventory_transaction,
    consume_inventory_fifo,
)
from warehouse_suit.transfer_service import record_transfer_formal_inbound


_COUNTER = [0]


def login(client, username="admin", password="admin"):
    response = client.post(
        "/api/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def enable_temporary(client, enabled=True):
    login(client)
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": enabled},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def seed_material(db):
    _COUNTER[0] += 1
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, unit, created_at, updated_at)
        VALUES (?, ?, '个', ?, ?)
        """,
        (
            f"9190010001{_COUNTER[0]:04d}",
            f"最终验收物料{_COUNTER[0]}",
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def add_formal_batch(db, material_id, quantity, operation_key):
    begin_inventory_transaction(db)
    batch_id = add_inventory_batch(
        db.cursor(),
        material_id,
        quantity,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "zone_name": "A",
            "remark": "最终验收正式库存",
        },
        f"FINAL-{material_id}",
        operation_key=operation_key,
    )
    db.commit()
    return batch_id


def add_temporary_batch(client, material_id, quantity, operation_key):
    response = client.post(
        "/api/temporary-inventory/batches",
        json={
            "material_id": material_id,
            "quantity": quantity,
            "unit_price": 2,
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "location": "A",
            "remark": "最终验收临时库存",
            "operation_key": operation_key,
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return int(response.get_json()["batch_id"])


def approve_claim(client, db, form_id):
    warehouse_id = int(
        db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    )
    login(client)
    response = client.post(
        f"/api/claims/{form_id}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def complete_transfer_acceptance(client, db, task_id):
    link = db.execute(
        """
        SELECT acceptance_form_id, acceptance_item_id
        FROM transfer_acceptance_links
        WHERE task_id = ? AND status = 'in_progress'
        ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    form_id = int(link["acceptance_form_id"])
    item_id = int(link["acceptance_item_id"])
    quantity = float(
        db.execute(
            "SELECT request_quantity FROM workflow_items WHERE id = ?", (item_id,)
        ).fetchone()[0]
    )
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    warehouse_id = int(
        db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    )
    task_row = db.execute(
        """
        SELECT id FROM workflow_tasks
        WHERE form_id = ? AND step_code = 'acceptance' AND status = 'pending'
        """,
        (form_id,),
    ).fetchone()
    login(client)
    inspected = client.post(
        f"/api/acceptance/{form_id}/inspect",
        json={
            "task_id": int(task_row["id"]),
            "decision": "同意",
            "leader_id": admin_id,
            "warehouse_user_id": warehouse_id,
            "items": [
                {
                    "id": item_id,
                    "qualified_quantity": quantity,
                    "unqualified_quantity": 0,
                    "package_ok_quantity": quantity,
                    "appearance_ok_quantity": quantity,
                    "name_spec_ok_quantity": quantity,
                    "usage_ok_quantity": quantity,
                }
            ],
        },
    )
    assert inspected.status_code == 200, inspected.get_data(as_text=True)
    approved = client.post(
        f"/api/acceptance/{form_id}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert approved.status_code == 200, approved.get_data(as_text=True)
    login(client, "warehouse", "test")
    inbound = client.post(
        f"/api/acceptance/{form_id}/inbound",
        json={
            "items": [
                {
                    "id": item_id,
                    "approved_quantity": quantity,
                    "warehouse_type": "office",
                    "zone_name": "A",
                }
            ]
        },
    )
    assert inbound.status_code == 200, inbound.get_data(as_text=True)
    return form_id, quantity


def create_second_buyer(db):
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, display_name, password, is_active, updated_at)
        VALUES ('final_buyer2', '最终验收采购员二', ?, 1, ?)
        """,
        (app_module.generate_password_hash("test"), app_module.now_text()),
    )
    user_id = int(cursor.lastrowid)
    role_id = int(
        cursor.execute("SELECT id FROM roles WHERE code = 'buyer'").fetchone()[0]
    )
    cursor.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (user_id, role_id),
    )
    db.commit()
    return user_id


def test_complete_temporary_transfer_business_loop_is_atomic_and_idempotent(
    app, client, db
):
    enable_temporary(client)
    material_id = seed_material(db)
    add_formal_batch(db, material_id, 2, f"final-formal:{material_id}")
    temp_batch_id = add_temporary_batch(
        client, material_id, 10, f"final-temporary:{material_id}"
    )
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )

    login(client)
    created = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "items": [{"material_id": material_id, "request_quantity": 5}],
        },
    )
    assert created.status_code == 200, created.get_data(as_text=True)
    claim = created.get_json()["form"]
    assert [
        (item["stock_source"], float(item["request_quantity"]))
        for item in claim["items"]
    ] == [("formal", 2), ("temporary", 3)]
    approve_claim(client, db, claim["id"])
    login(client, "warehouse", "test")
    outbound = client.post(f"/api/claims/{claim['id']}/outbound", json={})
    assert outbound.status_code == 200, outbound.get_data(as_text=True)
    obligation = db.execute(
        "SELECT * FROM temporary_issue_obligations WHERE claim_form_id = ?",
        (claim["id"],),
    ).fetchone()
    assert float(obligation["issued_quantity"]) == 3

    login(client)
    created_transfer = client.post(
        "/api/temporary-inventory/transfers",
        json={
            "material_id": material_id,
            "idempotency_key": f"final-transfer:{material_id}",
            "target_acceptance_quantity": 999,
        },
    )
    assert created_transfer.status_code == 200
    task = created_transfer.get_json()["task"]
    task_id = int(task["id"])
    assert (
        task["target_acceptance_quantity"]
        == task["temporary_quantity_snapshot"] + task["obligation_quantity_snapshot"]
        == 10
    )
    assert db.execute(
        "SELECT inventory_status FROM material_batches WHERE id = ?",
        (temp_batch_id,),
    ).fetchone()[0] == "transfer_locked"
    payloads = [
        json.loads(row[0] or "{}")
        for row in db.execute(
            "SELECT data_json FROM notifications WHERE data_json LIKE '%temporary_transfer%'"
        )
    ]
    assert any(
        data.get("business_type") == "temporary_transfer"
        and data.get("transfer_task_id") == task_id
        for data in payloads
    )

    buyer2_id = create_second_buyer(db)
    barrier = threading.Barrier(2)
    outcomes = []

    def claim_task(username, password):
        worker = app.test_client()
        login(worker, username, password)
        barrier.wait()
        response = worker.post(
            f"/api/temporary-inventory/transfers/{task_id}/claim", json={}
        )
        outcomes.append((username, response.status_code))

    workers = [
        threading.Thread(target=claim_task, args=("admin", "admin")),
        threading.Thread(target=claim_task, args=("final_buyer2", "test")),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(status for _, status in outcomes) == [200, 409]
    assigned = int(
        db.execute(
            "SELECT assigned_buyer_id FROM inventory_transfer_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()[0]
    )
    assert assigned in {admin_id, buyer2_id}
    login(
        client,
        "admin" if assigned == admin_id else "final_buyer2",
        "admin" if assigned == admin_id else "test",
    )
    started = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": f"final-acceptance:{task_id}",
            "validator_ids": [admin_id],
            "unit_price": 2,
            "target_acceptance_quantity": 1,
        },
    )
    assert started.status_code == 200, started.get_data(as_text=True)
    _, accepted = complete_transfer_acceptance(client, db, task_id)
    assert accepted == 10

    auto_claim = db.execute(
        "SELECT * FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert auto_claim, dict(
        db.execute(
            "SELECT status, error_code, error_message FROM inventory_transfer_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    )
    assert float(auto_claim["quantity"]) == 3
    auto_form_id = int(auto_claim["current_claim_form_id"])
    approve_claim(client, db, auto_form_id)
    login(client, "warehouse", "test")
    completed = client.post(f"/api/claims/{auto_form_id}/outbound", json={})
    assert completed.status_code == 200, completed.get_data(as_text=True)

    task = db.execute(
        "SELECT * FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    reservation = db.execute(
        "SELECT * FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()
    obligation = db.execute(
        "SELECT * FROM temporary_issue_obligations WHERE id = ?",
        (obligation["id"],),
    ).fetchone()
    temp = db.execute(
        "SELECT quantity, inventory_status FROM material_batches WHERE id = ?",
        (temp_batch_id,),
    ).fetchone()
    assert task["status"] == "completed" and task["active_key"] is None
    assert float(task["accepted_quantity"]) == 10
    assert float(reservation["reserved_quantity"]) == 3
    assert float(reservation["consumed_quantity"]) == 3
    assert float(obligation["settled_quantity"]) == 3
    assert obligation["status"] == "settled"
    assert tuple(temp) == (0, "transferred")
    assert db.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) FROM stock_records
        WHERE transfer_task_id = ? AND business_type = 'temporary_transfer_close'
        """,
        (task_id,),
    ).fetchone()[0] == 7
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE target_id = ?", (str(task_id),)
    ).fetchone()[0] > 0

    counts_before = tuple(
        db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?),
              (SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?),
              (SELECT COUNT(*) FROM stock_records WHERE transfer_task_id = ?),
              (SELECT COUNT(*) FROM notifications WHERE data_json LIKE ?)
            """,
            (task_id, task_id, task_id, f'%"transfer_task_id": {task_id}%'),
        ).fetchone()
    )
    login(client)
    assert client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    ).status_code == 200
    login(client, "warehouse", "test")
    repeated = client.post(f"/api/claims/{auto_form_id}/outbound", json={})
    assert repeated.status_code == 200 and repeated.get_json()["idempotent"] is True
    counts_after = tuple(
        db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?),
              (SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?),
              (SELECT COUNT(*) FROM stock_records WHERE transfer_task_id = ?),
              (SELECT COUNT(*) FROM notifications WHERE data_json LIKE ?)
            """,
            (task_id, task_id, task_id, f'%"transfer_task_id": {task_id}%'),
        ).fetchone()
    )
    assert counts_after == counts_before


def seed_obligation(db, material_id, batch_id, applicant_id, quantity, key):
    cursor = db.cursor()
    material = cursor.execute(
        "SELECT material_code, name, unit FROM materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    cursor.execute(
        """
        INSERT INTO workflow_forms (
            form_no, form_type, status, current_step, applicant_id,
            created_at, updated_at
        ) VALUES (?, 'claim', 'completed', 'completed', ?, ?, ?)
        """,
        (f"FINAL-OLD-{key}", applicant_id, app_module.now_text(), app_module.now_text()),
    )
    form_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO workflow_items (
            form_id, material_id, material_code, material_name, unit,
            request_quantity, outbound_quantity, stock_source, data_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'temporary', '{}')
        """,
        (
            form_id,
            material_id,
            material["material_code"],
            material["name"],
            material["unit"],
            quantity,
            quantity,
        ),
    )
    item_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO stock_records (
            material_id, operation_type, quantity, balance_after, operation_date,
            remark, batch_id, form_no, stock_source, business_type,
            operation_key, operator_id, workflow_item_id
        ) VALUES (?, 'out', ?, 0, ?, '最终验收历史临时领用', ?, ?,
                  'temporary', 'claim_outbound', ?, ?, ?)
        """,
        (
            material_id,
            quantity,
            app_module.today_text(),
            batch_id,
            f"FINAL-OLD-{key}",
            f"final-old-out:{key}",
            applicant_id,
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
            applicant_id,
            material_id,
            batch_id,
            form_id,
            item_id,
            stock_record_id,
            quantity,
            f"final-obligation:{key}",
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def prepare_transfer(
    client,
    db,
    temporary_quantities=(5,),
    obligations=(),
):
    enable_temporary(client)
    material_id = seed_material(db)
    batch_ids = [
        add_temporary_batch(
            client,
            material_id,
            quantity,
            f"final-prepare-temp:{material_id}:{index}",
        )
        for index, quantity in enumerate(temporary_quantities, 1)
    ]
    for index, (applicant_id, quantity) in enumerate(obligations, 1):
        seed_obligation(
            db,
            material_id,
            batch_ids[0],
            applicant_id,
            quantity,
            f"{material_id}:{index}",
        )
    login(client)
    response = client.post(
        "/api/temporary-inventory/transfers",
        json={
            "material_id": material_id,
            "idempotency_key": f"final-prepare-transfer:{material_id}",
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
    started = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": f"final-prepare-acceptance:{task_id}",
            "validator_ids": [admin_id],
            "unit_price": 2,
        },
    )
    assert started.status_code == 200, started.get_data(as_text=True)
    link = db.execute(
        """
        SELECT acceptance_form_id, acceptance_item_id
        FROM transfer_acceptance_links
        WHERE task_id = ? AND status = 'in_progress'
        """,
        (task_id,),
    ).fetchone()
    target = float(
        db.execute(
            "SELECT target_acceptance_quantity FROM inventory_transfer_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()[0]
    )
    begin_inventory_transaction(db)
    formal_batch_id = add_inventory_batch(
        db.cursor(),
        material_id,
        target,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "zone_name": "A",
            "remark": "最终验收转移入库",
        },
        f"FINAL-IN-{task_id}",
        operation_key=f"final-transfer-inbound:{task_id}",
    )
    record_transfer_formal_inbound(
        db.cursor(),
        int(link["acceptance_form_id"]),
        int(link["acceptance_item_id"]),
        formal_batch_id,
        target,
    )
    db.commit()
    return {
        "material_id": material_id,
        "batch_ids": batch_ids,
        "task_id": task_id,
        "formal_batch_id": formal_batch_id,
        "target": target,
    }


def auto_claim_row(db, task_id):
    return db.execute(
        "SELECT * FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()

def test_auto_claim_creation_failure_rolls_back_every_user_and_retries(
    client, db, monkeypatch
):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    test_user_id = int(
        db.execute("SELECT id FROM users WHERE username = 'testuser'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client,
        db,
        temporary_quantities=(5,),
        obligations=((admin_id, 3), (test_user_id, 2)),
    )
    task_id = prepared["task_id"]
    original = settlement_service.create_claim_workflow
    call_count = [0]

    def fail_second(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("injected second workflow failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(settlement_service, "create_claim_workflow", fail_second)
    failed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert failed.status_code == 400, failed.get_data(as_text=True)
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "auto_claim_exception"
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claim_obligations WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        """
        SELECT COUNT(*) FROM workflow_forms
        WHERE origin_type = 'temporary_transfer_auto_claim' AND origin_ref_id = ?
        """,
        (task_id,),
    ).fetchone()[0] == 0
    monkeypatch.setattr(settlement_service, "create_claim_workflow", original)
    repaired = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert repaired.status_code == 200, repaired.get_data(as_text=True)
    assert repaired.get_json()["task"]["status"] == "auto_claim_pending"
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 2


def test_temporary_close_second_batch_failure_rolls_back_all_batches(client, db):
    prepared = prepare_transfer(
        client, db, temporary_quantities=(3, 4), obligations=()
    )
    task_id = prepared["task_id"]
    first_batch, second_batch = prepared["batch_ids"]
    db.execute(
        f"""
        CREATE TRIGGER final_fail_second_temporary_close
        BEFORE UPDATE ON material_batches
        WHEN OLD.id = {int(second_batch)}
         AND NEW.inventory_status = 'transferred'
        BEGIN
            SELECT RAISE(FAIL, 'injected temporary close failure');
        END
        """
    )
    db.commit()
    failed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert failed.status_code == 400, failed.get_data(as_text=True)
    batches = db.execute(
        """
        SELECT id, quantity, inventory_status FROM material_batches
        WHERE id IN (?, ?) ORDER BY id
        """,
        (first_batch, second_batch),
    ).fetchall()
    assert [(row["quantity"], row["inventory_status"]) for row in batches] == [
        (3, "transfer_locked"),
        (4, "transfer_locked"),
    ]
    task = db.execute(
        "SELECT status, active_key FROM inventory_transfer_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert task["status"] == "auto_claim_exception"
    assert task["active_key"]
    assert db.execute(
        """
        SELECT COUNT(*) FROM stock_records
        WHERE transfer_task_id = ? AND business_type = 'temporary_transfer_close'
        """,
        (task_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM notifications WHERE data_json LIKE ? AND data_json LIKE '%temporary_transfer_completed%'",
        (f'%"transfer_task_id": {task_id}%',),
    ).fetchone()[0] == 0
    db.execute("DROP TRIGGER final_fail_second_temporary_close")
    db.commit()
    repaired = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert repaired.status_code == 200, repaired.get_data(as_text=True)
    assert repaired.get_json()["task"]["status"] == "completed"


def split_linked_formal_inventory(db, prepared, first_quantity):
    task_id = prepared["task_id"]
    material_id = prepared["material_id"]
    first_batch_id = prepared["formal_batch_id"]
    total = prepared["target"]
    second_quantity = total - first_quantity
    link = db.execute(
        """
        SELECT * FROM transfer_acceptance_links
        WHERE task_id = ? ORDER BY id LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    db.execute(
        "UPDATE material_batches SET quantity = ? WHERE id = ?",
        (first_quantity, first_batch_id),
    )
    db.execute(
        "UPDATE transfer_acceptance_links SET linked_quantity = ? WHERE id = ?",
        (first_quantity, int(link["id"])),
    )
    material = db.execute(
        "SELECT material_code, name, unit FROM materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    db.execute(
        """
        INSERT INTO workflow_items (
            form_id, material_id, material_code, material_name, unit,
            request_quantity, qualified_quantity, approved_quantity,
            stock_source, data_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'formal', '{}')
        """,
        (
            int(link["acceptance_form_id"]),
            material_id,
            material["material_code"],
            material["name"],
            material["unit"],
            second_quantity,
            second_quantity,
            second_quantity,
        ),
    )
    second_item_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    begin_inventory_transaction(db)
    second_batch_id = add_inventory_batch(
        db.cursor(),
        material_id,
        second_quantity,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-16",
            "zone_name": "B",
            "remark": "最终验收第二正式批次",
        },
        f"FINAL-IN-{task_id}-2",
        operation_key=f"final-transfer-inbound:{task_id}:2",
    )
    db.execute(
        """
        INSERT INTO transfer_acceptance_links (
            task_id, acceptance_form_id, acceptance_item_id, formal_batch_id,
            linked_quantity, status, operation_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'inbound', ?, ?, ?)
        """,
        (
            task_id,
            int(link["acceptance_form_id"]),
            second_item_id,
            second_batch_id,
            second_quantity,
            f"final-transfer-link:{task_id}:2",
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    db.commit()
    return first_batch_id, second_batch_id


def test_second_reserved_batch_ledger_failure_rolls_back_settlement(client, db):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client,
        db,
        temporary_quantities=(5,),
        obligations=((admin_id, 8),),
    )
    task_id = prepared["task_id"]
    first_batch, second_batch = split_linked_formal_inventory(db, prepared, 3)
    processed = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    )
    assert processed.status_code == 200, processed.get_data(as_text=True)
    claim = auto_claim_row(db, task_id)
    reservations = db.execute(
        """
        SELECT * FROM inventory_reservations
        WHERE task_id = ? ORDER BY formal_batch_id
        """,
        (task_id,),
    ).fetchall()
    assert [float(row["reserved_quantity"]) for row in reservations] == [3, 5]
    second_reservation_id = int(reservations[1]["id"])
    approve_claim(client, db, int(claim["current_claim_form_id"]))
    db.execute(
        f"""
        CREATE TRIGGER final_fail_second_reservation_ledger
        BEFORE INSERT ON stock_records
        WHEN NEW.inventory_reservation_id = {second_reservation_id}
        BEGIN
            SELECT RAISE(FAIL, 'injected reservation ledger failure');
        END
        """
    )
    db.commit()
    login(client, "warehouse", "test")
    failed = client.post(
        f"/api/claims/{int(claim['current_claim_form_id'])}/outbound", json={}
    )
    assert failed.status_code == 400, failed.get_data(as_text=True)
    assert [
        float(row[0])
        for row in db.execute(
            "SELECT quantity FROM material_batches WHERE id IN (?, ?) ORDER BY id",
            (first_batch, second_batch),
        )
    ] == [3, 10]
    assert [
        (float(row["consumed_quantity"]), row["status"])
        for row in db.execute(
            "SELECT consumed_quantity, status FROM inventory_reservations WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
    ] == [(0, "active"), (0, "active")]
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE transfer_auto_claim_id = ?",
        (int(claim["id"]),),
    ).fetchone()[0] == 0
    assert tuple(
        db.execute(
            """
            SELECT settled_quantity, status FROM temporary_issue_obligations
            WHERE transfer_task_id IS NULL OR transfer_task_id = ?
            ORDER BY id LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    ) == (0, "pending")
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "auto_claim_exception"
    assert db.execute(
        "SELECT COUNT(*) FROM material_batches WHERE material_id = ? AND inventory_status = 'transferred'",
        (prepared["material_id"],),
    ).fetchone()[0] == 0



def run_concurrently(app, calls):
    barrier = threading.Barrier(len(calls))
    results = []

    def worker(call):
        client = app.test_client()
        barrier.wait()
        response = call(client)
        results.append((response.status_code, response.get_json()))

    threads = [threading.Thread(target=worker, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_concurrent_process_auto_claims_creates_one_fact_set(app, client, db):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client,
        db,
        temporary_quantities=(5,),
        obligations=((admin_id, 3),),
    )
    task_id = prepared["task_id"]

    def process(worker):
        login(worker)
        return worker.post(
            f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
            json={},
        )

    results = run_concurrently(app, [process, process])
    assert all(status == 200 for status, _ in results), results
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 1
    assert db.execute(
        """
        SELECT COUNT(*) FROM workflow_forms
        WHERE origin_type = 'temporary_transfer_auto_claim' AND origin_ref_id = ?
        """,
        (task_id,),
    ).fetchone()[0] == 1
    assert sorted(bool(body["idempotent"]) for _, body in results) == [False, True]


def test_two_warehouse_outbounds_settle_and_finalize_only_once(app, client, db):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client,
        db,
        temporary_quantities=(5,),
        obligations=((admin_id, 3),),
    )
    task_id = prepared["task_id"]
    assert client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    ).status_code == 200
    claim = auto_claim_row(db, task_id)
    form_id = int(claim["current_claim_form_id"])
    approve_claim(client, db, form_id)

    def outbound(worker):
        login(worker, "warehouse", "test")
        return worker.post(f"/api/claims/{form_id}/outbound", json={})

    results = run_concurrently(app, [outbound, outbound])
    assert all(status == 200 for status, _ in results), results
    assert sorted(bool(body["idempotent"]) for _, body in results) == [False, True]
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE transfer_auto_claim_id = ?",
        (int(claim["id"]),),
    ).fetchone()[0] == 1
    obligation = db.execute(
        "SELECT settled_quantity, status FROM temporary_issue_obligations WHERE transfer_task_id = ?",
        (task_id,),
    ).fetchone()
    assert tuple(obligation) == (3, "settled")
    assert db.execute(
        """
        SELECT COUNT(*) FROM stock_records
        WHERE transfer_task_id = ? AND business_type = 'temporary_transfer_close'
        """,
        (task_id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "completed"


def test_feature_disable_racing_final_outbound_never_partially_commits(
    app, client, db
):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client,
        db,
        temporary_quantities=(5,),
        obligations=((admin_id, 3),),
    )
    task_id = prepared["task_id"]
    assert client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    ).status_code == 200
    claim = auto_claim_row(db, task_id)
    form_id = int(claim["current_claim_form_id"])
    approve_claim(client, db, form_id)

    def disable(worker):
        login(worker)
        return worker.post(
            "/api/system/workflow-settings",
            json={"temporary_inventory_enabled": False},
        )

    def outbound(worker):
        login(worker, "warehouse", "test")
        return worker.post(f"/api/claims/{form_id}/outbound", json={})

    results = run_concurrently(app, [disable, outbound])
    assert any(status == 200 for status, _ in results)
    task = db.execute(
        "SELECT status, active_key FROM inventory_transfer_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    reservation = db.execute(
        "SELECT reserved_quantity, consumed_quantity, status FROM inventory_reservations WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    obligation = db.execute(
        "SELECT settled_quantity, status FROM temporary_issue_obligations WHERE transfer_task_id IS NULL OR transfer_task_id = ? ORDER BY id LIMIT 1",
        (task_id,),
    ).fetchone()
    temporary = db.execute(
        "SELECT quantity, inventory_status FROM material_batches WHERE id = ?",
        (prepared["batch_ids"][0],),
    ).fetchone()
    close_count = db.execute(
        """
        SELECT COUNT(*) FROM stock_records
        WHERE transfer_task_id = ? AND business_type = 'temporary_transfer_close'
        """,
        (task_id,),
    ).fetchone()[0]
    if task["status"] == "completed":
        assert tuple(reservation) == (3, 3, "consumed")
        assert tuple(obligation) == (3, "settled")
        assert tuple(temporary) == (0, "transferred")
        assert close_count == 1
        assert task["active_key"] is None
    else:
        assert task["status"] == "paused"
        assert tuple(reservation) == (3, 0, "active")
        assert tuple(obligation) == (0, "pending")
        assert tuple(temporary) == (5, "transfer_locked")
        assert close_count == 0
        assert task["active_key"]
        enable_temporary(client, True)
        login(client, "warehouse", "test")
        resumed = client.post(f"/api/claims/{form_id}/outbound", json={})
        assert resumed.status_code == 200, resumed.get_data(as_text=True)
        assert db.execute(
            "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
        ).fetchone()[0] == "completed"



def test_material_batch_edit_racing_reservation_never_invades_reserved_stock(
    app, client, db
):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client,
        db,
        temporary_quantities=(5,),
        obligations=((admin_id, 3),),
    )
    task_id = prepared["task_id"]
    formal_batch_id = prepared["formal_batch_id"]
    material = db.execute(
        "SELECT material_code, name FROM materials WHERE id = ?",
        (prepared["material_id"],),
    ).fetchone()
    batch = db.execute(
        """
        SELECT batch_no, unit_price, warehouse_type, received_date
        FROM material_batches WHERE id = ?
        """,
        (formal_batch_id,),
    ).fetchone()

    def reserve(worker):
        login(worker)
        return worker.post(
            f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
            json={},
        )

    def edit(worker):
        login(worker)
        return worker.put(
            f"/api/materials/{prepared['material_id']}",
            json={
                "material_code": material["material_code"],
                "name": material["name"],
                "batches": [
                    {
                        "id": formal_batch_id,
                        "batch_no": batch["batch_no"],
                        "quantity": 2,
                        "unit_price": batch["unit_price"],
                        "warehouse_type": batch["warehouse_type"],
                        "received_date": batch["received_date"],
                    }
                ],
            },
        )

    results = run_concurrently(app, [reserve, edit])
    statuses = [status for status, _ in results]
    assert statuses.count(200) == 1, results
    physical = float(
        db.execute(
            "SELECT quantity FROM material_batches WHERE id = ?", (formal_batch_id,)
        ).fetchone()[0]
    )
    reserved = float(
        db.execute(
            """
            SELECT COALESCE(SUM(reserved_quantity - consumed_quantity - released_quantity), 0)
            FROM inventory_reservations
            WHERE formal_batch_id = ? AND status = 'active'
            """,
            (formal_batch_id,),
        ).fetchone()[0]
    )
    assert physical + 1e-9 >= reserved
    if reserved:
        assert physical == prepared["target"]
        assert reserved == 3
    else:
        assert physical == 2
        assert db.execute(
            "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
        ).fetchone()[0] == "auto_claim_exception"

@pytest.mark.parametrize("business_type", ["claim_outbound", "borrow_outbound"])
def test_regular_fifo_racing_reservation_creation_never_invades_reserved_stock(
    app, client, db, business_type
):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client, db, temporary_quantities=(5,), obligations=((admin_id, 3),)
    )
    task_id = prepared["task_id"]
    barrier = threading.Barrier(2)
    results = []

    def reserve():
        worker = app.test_client()
        login(worker)
        barrier.wait()
        response = worker.post(
            f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
            json={},
        )
        results.append(("reserve", response.status_code))

    def consume_regular_fifo():
        conn = app_module.get_db()
        try:
            barrier.wait()
            consume_inventory_fifo(
                conn.cursor(),
                prepared["material_id"],
                6,
                f"FINAL-RACE-{task_id}",
                stock_source="formal",
                business_type=business_type,
                operation_key=f"final-race:{business_type}:{task_id}",
            )
            conn.commit()
            results.append(("fifo", 200))
        except Exception:
            conn.rollback()
            results.append(("fifo", 409))
        finally:
            conn.close()

    threads = [
        threading.Thread(target=reserve),
        threading.Thread(target=consume_regular_fifo),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(status == 200 for _, status in results) == 1, results
    physical = float(
        db.execute(
            "SELECT quantity FROM material_batches WHERE id = ?",
            (prepared["formal_batch_id"],),
        ).fetchone()[0]
    )
    reserved = float(
        db.execute(
            """
            SELECT COALESCE(
                SUM(reserved_quantity - consumed_quantity - released_quantity), 0
            )
            FROM inventory_reservations
            WHERE formal_batch_id = ? AND status = 'active'
            """,
            (prepared["formal_batch_id"],),
        ).fetchone()[0]
    )
    assert physical >= 0
    assert physical + 1e-9 >= reserved
    assert (physical, reserved) in {(prepared["target"], 3.0), (2.0, 0.0)}


def test_regular_fifo_and_reserved_outbound_share_batch_without_overconsumption(
    app, client, db
):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client, db, temporary_quantities=(5,), obligations=((admin_id, 3),)
    )
    task_id = prepared["task_id"]
    assert client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    ).status_code == 200
    claim = auto_claim_row(db, task_id)
    form_id = int(claim["current_claim_form_id"])
    approve_claim(client, db, form_id)
    barrier = threading.Barrier(2)
    results = []

    def outbound_reserved():
        worker = app.test_client()
        login(worker, "warehouse", "test")
        barrier.wait()
        response = worker.post(f"/api/claims/{form_id}/outbound", json={})
        results.append(("reserved", response.status_code))

    def consume_regular_fifo():
        conn = app_module.get_db()
        try:
            barrier.wait()
            consume_inventory_fifo(
                conn.cursor(),
                prepared["material_id"],
                5,
                f"FINAL-ORDINARY-{task_id}",
                stock_source="formal",
                business_type="claim_outbound",
                operation_key=f"final-ordinary-race:{task_id}",
            )
            conn.commit()
            results.append(("ordinary", 200))
        except Exception:
            conn.rollback()
            results.append(("ordinary", 409))
        finally:
            conn.close()

    threads = [
        threading.Thread(target=outbound_reserved),
        threading.Thread(target=consume_regular_fifo),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [("ordinary", 200), ("reserved", 200)]
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?",
        (prepared["formal_batch_id"],),
    ).fetchone()[0] == 0
    assert tuple(
        db.execute(
            "SELECT consumed_quantity, status FROM inventory_reservations WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    ) == (3, "consumed")
    assert tuple(
        db.execute(
            "SELECT settled_quantity, status FROM temporary_issue_obligations WHERE transfer_task_id = ?",
            (task_id,),
        ).fetchone()
    ) == (3, "settled")
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "completed"


def test_concurrent_retry_auto_claims_creates_only_one_new_attempt(app, client, db):
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    prepared = prepare_transfer(
        client, db, temporary_quantities=(5,), obligations=((admin_id, 3),)
    )
    task_id = prepared["task_id"]
    assert client.post(
        f"/api/temporary-inventory/transfers/{task_id}/process-auto-claims",
        json={},
    ).status_code == 200
    original = auto_claim_row(db, task_id)
    rejected = client.post(
        f"/api/claims/{original['current_claim_form_id']}/leader",
        json={"decision": "\u4e0d\u540c\u610f", "remark": "concurrent retry audit"},
    )
    assert rejected.status_code == 200, rejected.get_data(as_text=True)

    def retry(worker):
        login(worker)
        return worker.post(
            f"/api/temporary-inventory/transfers/{task_id}/retry-auto-claims",
            json={},
        )

    results = run_concurrently(app, [retry, retry])
    assert sorted(status for status, _ in results) == [200, 409], results
    current = auto_claim_row(db, task_id)
    assert int(current["id"]) == int(original["id"])
    assert int(current["attempt_no"]) == 2
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

