import threading

import app as app_module

from warehouse_suit.inventory_service import add_inventory_batch, begin_inventory_transaction
from warehouse_suit.stock_allocation_service import stock_source_quantities
from warehouse_suit.transfer_service import (
    record_transfer_formal_inbound,
)


_COUNTER = [0]


def login(client, username="admin", password="Costar@508"):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)


def set_temporary_enabled(client, enabled):
    login(client)
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": enabled},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def seed_material(db):
    _COUNTER[0] += 1
    code = f"9050010001{_COUNTER[0]:04d}"
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, unit, created_at, updated_at)
        VALUES (?, ?, '个', ?, ?)
        """,
        (code, f"转移测试物料{_COUNTER[0]}", app_module.now_text(), app_module.now_text()),
    )
    db.commit()
    return int(cursor.lastrowid)


def create_temporary_batch(client, material_id, quantity=5, key="transfer-temp"):
    response = client.post(
        "/api/temporary-inventory/batches",
        json={
            "material_id": material_id,
            "quantity": quantity,
            "unit_price": 2,
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "location": "A",
            "remark": "转移测试",
            "operation_key": key,
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return int(response.get_json()["batch_id"])


def create_transfer(client, material_id, key, expected_status=200):
    response = client.post(
        "/api/temporary-inventory/transfers",
        json={
            "material_id": material_id,
            "idempotency_key": key,
            "target_acceptance_quantity": 9999,
        },
    )
    assert response.status_code == expected_status, response.get_data(as_text=True)
    return response


def seed_pending_obligation(db, material_id, batch_id, operation_key):
    cursor = db.cursor()
    user_id = int(
        cursor.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    cursor.execute(
        """
        INSERT INTO workflow_forms (
            form_no, form_type, status, current_step, applicant_id, created_at, updated_at
        ) VALUES (?, 'claim', 'completed', 'completed', ?, ?, ?)
        """,
        (
            f"OB-{operation_key}",
            user_id,
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    form_id = int(cursor.lastrowid)
    material = cursor.execute(
        "SELECT material_code, name, unit FROM materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    cursor.execute(
        """
        INSERT INTO workflow_items (
            form_id, material_id, material_code, material_name, unit,
            request_quantity, stock_source, data_json
        ) VALUES (?, ?, ?, ?, ?, 4, 'temporary', '{}')
        """,
        (form_id, material_id, material["material_code"], material["name"], material["unit"]),
    )
    item_id = int(cursor.lastrowid)
    stock_record_id = int(
        cursor.execute(
            "SELECT id FROM stock_records WHERE batch_id = ? ORDER BY id LIMIT 1",
            (batch_id,),
        ).fetchone()[0]
    )
    cursor.execute(
        """
        INSERT INTO temporary_issue_obligations (
            applicant_id, material_id, source_batch_id, claim_form_id, claim_item_id,
            stock_record_id, issued_quantity, settled_quantity, status, operation_key,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 4, 1, 'pending', ?, ?, ?)
        """,
        (
            user_id,
            material_id,
            batch_id,
            form_id,
            item_id,
            stock_record_id,
            operation_key,
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def test_transfer_creation_locks_batches_snapshots_obligations_and_is_idempotent(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    batch_id = create_temporary_batch(client, material_id, 5, "transfer-create-base")
    obligation_id = seed_pending_obligation(
        db, material_id, batch_id, "transfer-create-obligation"
    )

    first = create_transfer(client, material_id, "transfer-create-request")
    second = create_transfer(client, material_id, "transfer-create-request")
    duplicate_material = create_transfer(
        client, material_id, "transfer-create-new-key", expected_status=409
    )

    assert first.status_code == second.status_code == 200
    assert second.get_json()["idempotent"] is True
    assert duplicate_material.status_code == 409
    task = first.get_json()["task"]
    assert task["temporary_quantity_snapshot"] == 5
    assert task["obligation_quantity_snapshot"] == 3
    assert task["target_acceptance_quantity"] == 8
    assert task["status"] == "awaiting_purchase"
    assert len(task["items"]) == 1
    assert len(task["obligations"]) == 1
    assert task["obligations"][0]["obligation_id"] == obligation_id

    batch = db.execute(
        "SELECT inventory_status, version, quantity FROM material_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    assert tuple(batch) == ("transfer_locked", 1, 5)
    assert db.execute("SELECT COUNT(*) FROM inventory_transfer_tasks").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM inventory_transfer_items").fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_transfer_obligations"
    ).fetchone()[0] == 1
    obligation = db.execute(
        "SELECT issued_quantity, settled_quantity, status FROM temporary_issue_obligations WHERE id = ?",
        (obligation_id,),
    ).fetchone()
    assert tuple(obligation) == (4, 1, "pending")


def test_active_temporary_borrow_blocks_transfer(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    create_temporary_batch(client, material_id, 5, "transfer-borrow-base")
    user_id = int(db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0])
    db.execute(
        """
        INSERT INTO borrow_records (
            borrow_no, material_id, item_code, item_name, quantity, returned_quantity,
            status, borrower_id, stock_source, created_at, updated_at
        ) VALUES ('BR-BLOCK', ?, 'CODE', '临时借用', 2, 0, 'borrowed', ?,
                  'temporary', ?, ?)
        """,
        (material_id, user_id, app_module.now_text(), app_module.now_text()),
    )
    db.commit()

    response = create_transfer(
        client, material_id, "transfer-borrow-block", expected_status=409
    )
    assert response.status_code == 409
    assert "未归还的临时借用" in response.get_json()["error"]
    assert db.execute("SELECT COUNT(*) FROM inventory_transfer_tasks").fetchone()[0] == 0


def test_transfer_claim_and_acceptance_creation_are_idempotent(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    create_temporary_batch(client, material_id, 6, "transfer-acceptance-base")
    created = create_transfer(client, material_id, "transfer-acceptance-create").get_json()["task"]
    task_id = int(created["id"])

    claimed = client.post(f"/api/temporary-inventory/transfers/{task_id}/claim", json={})
    claimed_again = client.post(f"/api/temporary-inventory/transfers/{task_id}/claim", json={})
    admin_id = int(db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0])
    payload = {
        "idempotency_key": "transfer-acceptance-start",
        "validator_ids": [admin_id],
        "unit_price": 3,
        "target_acceptance_quantity": 1,
    }
    started = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json=payload,
    )
    started_again = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json=payload,
    )

    assert claimed.status_code == claimed_again.status_code == 200
    assert claimed_again.get_json()["idempotent"] is True
    assert started.status_code == started_again.status_code == 200
    assert started_again.get_json()["idempotent"] is True
    form_id = int(started.get_json()["task"]["acceptance_form_id"])
    form = db.execute(
        "SELECT origin_type, origin_ref_id FROM workflow_forms WHERE id = ?",
        (form_id,),
    ).fetchone()
    item = db.execute(
        "SELECT request_quantity, arrival_quantity, stock_source FROM workflow_items WHERE form_id = ?",
        (form_id,),
    ).fetchone()
    assert tuple(form) == ("temporary_transfer", task_id)
    assert tuple(item) == (6, 6, "formal")
    assert db.execute(
        "SELECT COUNT(*) FROM transfer_acceptance_links WHERE task_id = ?",
        (task_id,),
    ).fetchone()[0] == 1


def test_formal_inbound_links_are_recomputed_and_stop_before_stage_six(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    temp_batch_id = create_temporary_batch(client, material_id, 5, "transfer-inbound-base")
    obligation_id = seed_pending_obligation(
        db, material_id, temp_batch_id, "transfer-inbound-obligation"
    )
    task_id = int(
        create_transfer(client, material_id, "transfer-inbound-create").get_json()["task"]["id"]
    )
    client.post(f"/api/temporary-inventory/transfers/{task_id}/claim", json={})
    admin_id = int(db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0])
    first_start = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": "transfer-inbound-first",
            "validator_ids": [admin_id],
            "unit_price": 2,
        },
    ).get_json()["task"]
    first_link = db.execute(
        "SELECT acceptance_form_id, acceptance_item_id FROM transfer_acceptance_links WHERE task_id = ?",
        (task_id,),
    ).fetchone()

    begin_inventory_transaction(db)
    first_batch = add_inventory_batch(
        db.cursor(),
        material_id,
        3,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "zone_name": "A",
            "remark": "转移部分正式入库",
        },
        "TRANSFER-PARTIAL",
        operation_key="transfer-inbound-formal-first",
    )
    record_transfer_formal_inbound(
        db.cursor(),
        first_link["acceptance_form_id"],
        first_link["acceptance_item_id"],
        first_batch,
        3,
    )
    db.commit()
    assert tuple(db.execute(
        "SELECT status, accepted_quantity FROM inventory_transfer_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()) == ("formal_inbound_partial", 3)

    second_start = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": "transfer-inbound-second",
            "validator_ids": [admin_id],
            "unit_price": 2,
        },
    )
    assert second_start.status_code == 200, second_start.get_data(as_text=True)
    second_link = db.execute(
        """
        SELECT acceptance_form_id, acceptance_item_id
        FROM transfer_acceptance_links
        WHERE task_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    begin_inventory_transaction(db)
    second_batch = add_inventory_batch(
        db.cursor(),
        material_id,
        5,
        2,
        {
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "zone_name": "A",
            "remark": "转移补充正式入库",
        },
        "TRANSFER-COMPLETE",
        operation_key="transfer-inbound-formal-second",
    )
    record_transfer_formal_inbound(
        db.cursor(),
        second_link["acceptance_form_id"],
        second_link["acceptance_item_id"],
        second_batch,
        5,
    )
    db.commit()

    task = db.execute(
        "SELECT status, accepted_quantity, active_key FROM inventory_transfer_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert tuple(task) == ("formal_inbound_complete", 8, f"material:{material_id}")
    assert db.execute(
        "SELECT inventory_status FROM material_batches WHERE id = ?",
        (temp_batch_id,),
    ).fetchone()[0] == "transfer_locked"
    assert tuple(db.execute(
        "SELECT issued_quantity, settled_quantity, status FROM temporary_issue_obligations WHERE id = ?",
        (obligation_id,),
    ).fetchone()) == (4, 1, "pending")
    assert db.execute("SELECT COUNT(*) FROM workflow_forms WHERE form_type = 'claim'").fetchone()[0] == 1


def test_cancel_unlocks_and_feature_toggle_pauses_without_deleting(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    batch_id = create_temporary_batch(client, material_id, 4, "transfer-cancel-base")
    task_id = int(
        create_transfer(client, material_id, "transfer-cancel-create").get_json()["task"]["id"]
    )

    set_temporary_enabled(client, False)
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "paused"
    assert db.execute(
        "SELECT inventory_status FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()[0] == "transfer_locked"

    set_temporary_enabled(client, True)
    assert db.execute(
        "SELECT status FROM inventory_transfer_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0] == "awaiting_purchase"
    cancelled = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/cancel",
        json={"reason": "测试取消"},
    )
    cancelled_again = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/cancel",
        json={"reason": "测试取消"},
    )
    assert cancelled.status_code == cancelled_again.status_code == 200
    assert cancelled_again.get_json()["idempotent"] is True
    assert tuple(db.execute(
        "SELECT status, active_key FROM inventory_transfer_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()) == ("cancelled", None)
    assert tuple(db.execute(
        "SELECT inventory_status, quantity FROM material_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()) == ("available", 4)


def test_transfer_lock_blocks_temporary_changes_and_source_allocation(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    batch_id = create_temporary_batch(client, material_id, 5, "transfer-lock-base")
    task = create_transfer(client, material_id, "transfer-lock-create").get_json()["task"]

    available = stock_source_quantities(db.cursor(), material_id, temporary_enabled=True)
    assert available["temporary"] == 0

    preview = client.get(
        f"/api/temporary-inventory/transfers/preview?material_id={material_id}"
    )
    assert preview.status_code == 200
    preview_data = preview.get_json()["preview"]
    assert preview_data["active_transfer_task_id"] == task["id"]
    assert preview_data["can_transfer"] is False

    add_response = client.post(
        "/api/temporary-inventory/batches",
        json={
            "material_id": material_id,
            "quantity": 1,
            "unit_price": 2,
            "warehouse_type": "office",
            "received_date": "2026-07-15",
            "location": "A",
            "operation_key": "transfer-lock-add",
        },
    )
    adjust_response = client.post(
        f"/api/temporary-inventory/batches/{batch_id}/adjust",
        json={
            "adjustment_quantity": -1,
            "reason": "锁定校验",
            "operation_key": "transfer-lock-adjust",
        },
    )
    assert add_response.status_code == 400
    assert adjust_response.status_code == 400
    assert "正在转移" in add_response.get_json()["error"]
    assert "转移锁定" in adjust_response.get_json()["error"]
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()[0] == 5


def test_disabled_transfer_tasks_are_hidden_but_admin_can_audit(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    create_temporary_batch(client, material_id, 2, "transfer-disabled-base")
    task_id = int(
        create_transfer(client, material_id, "transfer-disabled-create").get_json()["task"]["id"]
    )
    set_temporary_enabled(client, False)

    login(client, "warehouse", "test")
    list_response = client.get("/api/temporary-inventory/transfers")
    detail_response = client.get(f"/api/temporary-inventory/transfers/{task_id}")
    assert list_response.status_code == 409
    assert detail_response.status_code == 409

    login(client)
    admin_list = client.get("/api/temporary-inventory/transfers")
    admin_detail = client.get(f"/api/temporary-inventory/transfers/{task_id}")
    assert admin_list.status_code == 200
    assert admin_detail.status_code == 200
    assert admin_detail.get_json()["task"]["status"] == "paused"


def test_withdrawing_transfer_acceptance_preserves_form_and_marks_failure(client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    create_temporary_batch(client, material_id, 3, "transfer-failure-base")
    task_id = int(
        create_transfer(client, material_id, "transfer-failure-create").get_json()["task"]["id"]
    )
    client.post(f"/api/temporary-inventory/transfers/{task_id}/claim", json={})
    admin_id = int(db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0])
    started = client.post(
        f"/api/temporary-inventory/transfers/{task_id}/start-acceptance",
        json={
            "idempotency_key": "transfer-failure-start",
            "validator_ids": [admin_id],
            "unit_price": 2,
        },
    )
    form_id = int(started.get_json()["task"]["acceptance_form_id"])

    withdrawn = client.delete(f"/api/workflows/{form_id}")
    assert withdrawn.status_code == 200
    assert withdrawn.get_json()["preserved"] is True
    assert db.execute(
        "SELECT status FROM workflow_forms WHERE id = ?", (form_id,)
    ).fetchone()[0] == "cancelled"
    assert db.execute(
        "SELECT status FROM transfer_acceptance_links WHERE acceptance_form_id = ?",
        (form_id,),
    ).fetchone()[0] == "failed"
    assert tuple(db.execute(
        "SELECT status, accepted_quantity FROM inventory_transfer_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()) == ("acceptance_failed", 0)


def test_concurrent_transfer_creation_allows_only_one_active_task(app, client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    create_temporary_batch(client, material_id, 5, "transfer-concurrent-create-base")
    barrier = threading.Barrier(2)
    results = []

    def worker(index):
        worker_client = app.test_client()
        login(worker_client)
        barrier.wait()
        response = worker_client.post(
            "/api/temporary-inventory/transfers",
            json={
                "material_id": material_id,
                "idempotency_key": f"transfer-concurrent-create-{index}",
            },
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 409]
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_transfer_tasks WHERE material_id = ?",
        (material_id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_transfer_items"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT inventory_status FROM material_batches WHERE material_id = ?",
        (material_id,),
    ).fetchone()[0] == "transfer_locked"


def test_concurrent_transfer_claim_allows_only_one_buyer(app, client, db):
    material_id = seed_material(db)
    set_temporary_enabled(client, True)
    create_temporary_batch(client, material_id, 5, "transfer-concurrent-claim-base")
    task_id = int(
        create_transfer(client, material_id, "transfer-concurrent-claim-create")
        .get_json()["task"]["id"]
    )
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, display_name, password, is_active, updated_at)
        VALUES ('buyer2', '采购员二', ?, 1, ?)
        """,
        (
            app_module.generate_password_hash("test"),
            app_module.now_text(),
        ),
    )
    buyer_id = int(cursor.lastrowid)
    buyer_role_id = int(
        cursor.execute("SELECT id FROM roles WHERE code = 'buyer'").fetchone()[0]
    )
    cursor.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (buyer_id, buyer_role_id),
    )
    db.commit()

    barrier = threading.Barrier(2)
    results = []

    def worker(username, password):
        worker_client = app.test_client()
        login(worker_client, username, password)
        barrier.wait()
        response = worker_client.post(
            f"/api/temporary-inventory/transfers/{task_id}/claim",
            json={},
        )
        results.append((username, response.status_code))

    threads = [
        threading.Thread(target=worker, args=("admin", "Costar@508")),
        threading.Thread(target=worker, args=("buyer2", "test")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(status for _, status in results) == [200, 409]
    assigned = int(
        db.execute(
            "SELECT assigned_buyer_id FROM inventory_transfer_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()[0]
    )
    admin_id = int(
        db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    )
    assert assigned in {admin_id, buyer_id}
