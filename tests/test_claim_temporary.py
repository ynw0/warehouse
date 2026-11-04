import json
import threading

import app as app_module

from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.inventory_service import update_inventory_total
from warehouse_suit.material_repository import material_stock_total


_COUNTER = [0]


def login(client, username="warehouse", password="test"):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["user"]


def enable_temporary(client, enabled=True):
    login(client, "admin", "admin")
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": enabled},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def seed_material(db, formal=0, temporary_batches=()):
    _COUNTER[0] += 1
    code = f"9120010001{_COUNTER[0]:04d}"
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, unit, created_at, updated_at)
        VALUES (?, ?, '个', ?, ?)
        """,
        (code, f"第三阶段物料{_COUNTER[0]}", app_module.now_text(), app_module.now_text()),
    )
    material_id = cursor.lastrowid
    if formal:
        cursor.execute(
            """
                INSERT INTO material_batches
                    (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
                     stock_source, inventory_status, version, purchase_project, created_at, updated_at)
                VALUES (?, ?, ?, 1, 'office', '2026-01-01', ?, 'available', 0, '办公', ?, ?)
            """,
            (
                material_id,
                f"FORMAL-{material_id}",
                formal,
                STOCK_SOURCE_FORMAL,
                app_module.now_text(),
                app_module.now_text(),
            ),
        )
    batch_ids = []
    for index, quantity in enumerate(temporary_batches):
        cursor.execute(
            """
                INSERT INTO material_batches
                    (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
                     stock_source, inventory_status, version, purchase_project, created_at, updated_at)
                VALUES (?, ?, ?, 2, 'office', ?, ?, 'available', 0, '办公', ?, ?)
            """,
            (
                material_id,
                f"TEMP-{material_id}-{index + 1}",
                quantity,
                f"2026-01-{index + 2:02d}",
                STOCK_SOURCE_TEMPORARY,
                app_module.now_text(),
                app_module.now_text(),
            ),
        )
        batch_ids.append(cursor.lastrowid)
    update_inventory_total(cursor, material_id)
    db.commit()
    return material_id, code, batch_ids


def create_claim(client, db, items):
    warehouse = login(client)
    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    payload_items = [
        {**item, "purchase_project": item.get("purchase_project") or "办公"}
        for item in items
    ]
    response = client.post(
        "/api/claims",
        json={"leader_id": admin_id, "purpose": "办公", "items": payload_items},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"], warehouse


def approve_claim(client, db, form_id):
    warehouse_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    login(client, "admin", "admin")
    response = client.post(
        f"/api/claims/{form_id}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"]


def outbound_claim(client, form, items=None, operation_key=None):
    login(client)
    if items is None:
        items = [
            {
                "id": item["id"],
                "outbound_quantity": item.get("remaining_outbound_quantity")
                or item["request_quantity"],
                "batches": item.get("suggested_batches") or [],
            }
            for item in form["items"]
        ]
    response = client.post(
        f"/api/claims/{form['id']}/outbound",
        json={
            "items": items,
            "signature": "仓库管理员",
            "operation_key": operation_key or f"test-claim-outbound-{form['id']}",
        },
    )
    return response


def source_quantities(form):
    return [(item["stock_source"], float(item["request_quantity"])) for item in form["items"]]


def test_server_allocates_formal_first_and_keeps_temporary_claim_to_one_material(client, db):
    enable_temporary(client)
    formal_id, _, _ = seed_material(db, formal=10, temporary_batches=(8,))
    temporary_id, _, _ = seed_material(db, temporary_batches=(9,))
    mixed_id, _, _ = seed_material(db, formal=3, temporary_batches=(10,))

    formal_form, _ = create_claim(client, db, [{"material_id": formal_id, "request_quantity": 4}])
    assert source_quantities(formal_form) == [(STOCK_SOURCE_FORMAL, 4)]
    temporary_form, _ = create_claim(client, db, [{"material_id": temporary_id, "request_quantity": 5}])
    assert source_quantities(temporary_form) == [(STOCK_SOURCE_TEMPORARY, 5)]
    # 正式库不够时整笔改从临时库扣，不用正式库补差。
    temporary_only, _ = create_claim(client, db, [{"material_id": mixed_id, "request_quantity": 7}])
    assert source_quantities(temporary_only) == [(STOCK_SOURCE_TEMPORARY, 7)]
    assert material_stock_total(db.cursor(), mixed_id) == 3
    assert material_stock_total(db.cursor(), mixed_id, STOCK_SOURCE_TEMPORARY) == 10

    warehouse = login(client)
    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    response = client.post("/api/claims", json={
        "leader_id": admin_id,
        "purpose": "办公",
        "items": [
            {"material_id": formal_id, "request_quantity": 1, "purchase_project": "办公"},
            {"material_id": temporary_id, "request_quantity": 1, "purchase_project": "办公"},
        ],
    })
    assert response.status_code == 400
    assert "单次只能领用一种物料" in response.get_json()["error"]


def test_closed_feature_hides_temporary_quantity_and_rejects_shortfall(client, db):
    material_id, _, _ = seed_material(db, formal=2, temporary_batches=(10,))
    login(client)
    rows = client.get("/api/claims/materials").get_json()
    row = next(item for item in rows if item["id"] == material_id)
    assert row["formal_available_quantity"] == 2
    assert row["temporary_available_quantity"] == 0
    assert row["total_available_quantity"] == 2

    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    response = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "items": [
                {
                    "material_id": material_id,
                    "request_quantity": 3,
                    "purchase_project": "办公",
                    "stock_source": "temporary",
                    "temporary_quantity": 3,
                }
            ],
        },
    )
    assert response.status_code == 400
    response = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "items": [{"material_id": material_id, "request_quantity": 2, "purchase_project": "办公", "stock_source": "temporary"}],
        },
    )
    assert response.status_code == 200
    assert source_quantities(response.get_json()["form"]) == [(STOCK_SOURCE_FORMAL, 2)]


def test_claim_material_query_exposes_source_totals_only_when_enabled(client, db):
    material_id, code, _ = seed_material(db, formal=2, temporary_batches=(5,))
    enable_temporary(client)
    login(client)
    response = client.get(f"/api/claims/materials?keyword={code}")
    assert response.status_code == 200
    row = response.get_json()[0]
    assert row["id"] == material_id
    assert row["formal_available_quantity"] == 2
    assert row["temporary_available_quantity"] == 5
    assert row["total_available_quantity"] == 7
    assert row["quantity"] == 7


def test_temporary_fifo_outbound_creates_one_obligation_per_batch_and_is_idempotent(client, db):
    enable_temporary(client)
    material_id, _, batch_ids = seed_material(db, temporary_batches=(3, 5))
    form, warehouse = create_claim(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 8}],
    )
    assert source_quantities(form) == [(STOCK_SOURCE_TEMPORARY, 8)]
    approved = approve_claim(client, db, form["id"])

    response = outbound_claim(client, approved)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 0
    records = db.execute(
        """
        SELECT id, batch_id, quantity, stock_source, business_type, workflow_item_id, operation_key
        FROM stock_records
        WHERE form_no = ? ORDER BY id
        """,
        (form["form_no"],),
    ).fetchall()
    assert [(row["batch_id"], row["quantity"]) for row in records] == [
        (batch_ids[0], 3),
        (batch_ids[1], 5),
    ]
    outbound_item = response.get_json()["form"]["items"][0]
    assert [
        (batch["batch_id"], batch["quantity"], batch["unit_price"])
        for batch in outbound_item["data"]["consumed_batches"]
    ] == [
        (batch_ids[0], 3, 2.0),
        (batch_ids[1], 5, 2.0),
    ]
    assert sum(
        batch["quantity"] * batch["unit_price"]
        for batch in outbound_item["data"]["consumed_batches"]
    ) == 16.0
    assert {row["stock_source"] for row in records} == {STOCK_SOURCE_TEMPORARY}
    assert {row["business_type"] for row in records} == {"claim_outbound"}
    item_id = form["items"][0]["id"]
    assert [row["operation_key"] for row in records] == [
        f"claim:{form['id']}:{item_id}:outbound:test-claim-outbound-{form['id']}:{batch_ids[0]}",
        f"claim:{form['id']}:{item_id}:outbound:test-claim-outbound-{form['id']}:{batch_ids[1]}",
    ]
    assert len({row["operation_key"] for row in records}) == 2
    operation_key_index = next(
        row
        for row in db.execute("PRAGMA index_list('stock_records')")
        if row["name"] == "uq_stock_records_operation_key"
    )
    assert operation_key_index["unique"] == 1

    obligations = db.execute(
        """
        SELECT applicant_id, material_id, source_batch_id, claim_form_id,
               claim_item_id, stock_record_id, issued_quantity, status, operation_key
        FROM temporary_issue_obligations ORDER BY id
        """
    ).fetchall()
    assert [row["source_batch_id"] for row in obligations] == batch_ids
    assert [row["issued_quantity"] for row in obligations] == [3, 5]
    assert sum(row["issued_quantity"] for row in obligations) == 8
    assert {row["applicant_id"] for row in obligations} == {warehouse["id"]}
    assert {row["claim_form_id"] for row in obligations} == {form["id"]}
    assert {row["status"] for row in obligations} == {"pending"}
    record_by_batch = {row["batch_id"]: row["id"] for row in records}
    assert {
        row["source_batch_id"]: row["stock_record_id"] for row in obligations
    } == record_by_batch
    assert [row["operation_key"] for row in obligations] == [
        f"claim_out:{form['id']}:{item_id}:{batch_ids[0]}:{record_by_batch[batch_ids[0]]}",
        f"claim_out:{form['id']}:{item_id}:{batch_ids[1]}:{record_by_batch[batch_ids[1]]}",
    ]

    retry = outbound_claim(client, response.get_json()["form"])
    assert retry.status_code == 200
    assert retry.get_json()["idempotent"] is True
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 0
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 2
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?", (form["form_no"],)
    ).fetchone()[0] == 2


def test_second_fifo_batch_ledger_failure_rolls_back_first_batch(client, db):
    enable_temporary(client)
    material_id, _, batch_ids = seed_material(db, temporary_batches=(3, 5))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 8}])
    approved = approve_claim(client, db, form["id"])
    db.execute(
        f"""
        CREATE TRIGGER fail_second_claim_batch_ledger
        BEFORE INSERT ON stock_records
        WHEN NEW.business_type = 'claim_outbound' AND NEW.batch_id = {int(batch_ids[1])}
        BEGIN
            SELECT RAISE(FAIL, 'forced second batch ledger failure');
        END
        """
    )
    db.commit()

    response = outbound_claim(client, approved)
    assert response.status_code == 400
    quantities = db.execute(
        "SELECT id, quantity FROM material_batches WHERE id IN (?, ?) ORDER BY id",
        batch_ids,
    ).fetchall()
    assert [(row["id"], row["quantity"]) for row in quantities] == [
        (batch_ids[0], 3),
        (batch_ids[1], 5),
    ]
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?", (form["form_no"],)
    ).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 0
    current = db.execute(
        "SELECT status, current_step FROM workflow_forms WHERE id = ?", (form["id"],)
    ).fetchone()
    assert tuple(current) == ("outbound", "outbound")

def test_formal_outbound_never_creates_obligation_or_consumes_temporary(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, formal=6, temporary_batches=(20,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 4}])
    approved = approve_claim(client, db, form["id"])
    response = outbound_claim(client, approved)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert material_stock_total(db.cursor(), material_id) == 2
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 20
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 0
    record = db.execute(
        "SELECT stock_source, workflow_item_id FROM stock_records WHERE form_no = ?",
        (form["form_no"],),
    ).fetchone()
    assert record["stock_source"] == STOCK_SOURCE_FORMAL
    assert record["workflow_item_id"] == form["items"][0]["id"]


def test_mixed_outbound_is_one_transaction_when_obligation_insert_fails(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, temporary_batches=(2,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 2}])
    approved = approve_claim(client, db, form["id"])
    db.execute(
        """
        CREATE TRIGGER fail_claim_obligation
        BEFORE INSERT ON temporary_issue_obligations
        BEGIN
            SELECT RAISE(FAIL, 'forced obligation failure');
        END
        """
    )
    db.commit()

    response = outbound_claim(client, approved)
    assert response.status_code == 400
    assert material_stock_total(db.cursor(), material_id) == 0
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 2
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?", (form["form_no"],)
    ).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 0
    current = db.execute(
        "SELECT status, current_step FROM workflow_forms WHERE id = ?", (form["id"],)
    ).fetchone()
    assert tuple(current) == ("outbound", "outbound")


def test_mixed_outbound_rolls_back_when_temporary_stock_changes(client, db):
    enable_temporary(client)
    material_id, _, batch_ids = seed_material(db, temporary_batches=(2,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 2}])
    approved = approve_claim(client, db, form["id"])
    db.execute("UPDATE material_batches SET quantity = 1 WHERE id = ?", (batch_ids[0],))
    db.commit()

    response = outbound_claim(client, approved)
    assert response.status_code == 400
    assert material_stock_total(db.cursor(), material_id) == 0
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 1
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?", (form["form_no"],)
    ).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 0


def test_rejected_claim_reallocates_sources_on_resubmit(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, formal=2, temporary_batches=(5,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 4}])
    assert source_quantities(form) == [(STOCK_SOURCE_TEMPORARY, 4)]

    login(client, "admin", "admin")
    response = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "不同意", "remark": "请重新确认数量"},
    )
    assert response.status_code == 200
    rejected = response.get_json()["form"]
    group_key = rejected["items"][0]["allocation_group_key"]

    formal_batch_id = db.execute(
        "SELECT id FROM material_batches WHERE material_id = ? AND stock_source = ?",
        (material_id, STOCK_SOURCE_FORMAL),
    ).fetchone()[0]
    db.execute("UPDATE material_batches SET quantity = 4 WHERE id = ?", (formal_batch_id,))
    update_inventory_total(db.cursor(), material_id)
    db.commit()

    login(client)
    response = client.put(
        f"/api/workflows/{form['id']}",
        json={
            "title": rejected["title"],
            "items": [
                {
                    "id": rejected["items"][0]["id"],
                    "request_quantity": 4,
                    "allocation_group_key": group_key,
                }
            ],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert source_quantities(response.get_json()["form"]) == [(STOCK_SOURCE_FORMAL, 4)]
    response = client.post(f"/api/workflows/{form['id']}/resubmit-returned", json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert source_quantities(response.get_json()["form"]) == [(STOCK_SOURCE_FORMAL, 4)]
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 0


def test_mixed_claim_is_hidden_as_a_whole_when_feature_is_closed(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, formal=2, temporary_batches=(3,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 3}])
    assert len(form["items"]) == 1

    enable_temporary(client, False)
    login(client)
    rows = client.get("/api/workflows?type=claim").get_json()
    assert form["id"] not in {row["id"] for row in rows}
    assert db.execute(
        "SELECT COUNT(*) FROM workflow_items WHERE form_id = ?", (form["id"],)
    ).fetchone()[0] == 1

    enable_temporary(client, True)
    login(client)
    rows = client.get("/api/workflows?type=claim").get_json()
    assert form["id"] in {row["id"] for row in rows}


def test_obligation_and_ledger_failure_leave_no_partial_outbound(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, temporary_batches=(4,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 4}])
    approved = approve_claim(client, db, form["id"])
    db.execute(
        """
        CREATE TRIGGER fail_temporary_claim_ledger
        BEFORE INSERT ON stock_records
        WHEN NEW.stock_source = 'temporary' AND NEW.business_type = 'claim_outbound'
        BEGIN
            SELECT RAISE(FAIL, 'forced ledger failure');
        END
        """
    )
    db.commit()

    response = outbound_claim(client, approved)
    assert response.status_code == 400
    assert material_stock_total(db.cursor(), material_id) == 0
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 4
    assert db.execute("SELECT COUNT(*) FROM stock_records WHERE form_no = ?", (form["form_no"],)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 0


def test_claim_partial_outbound_stays_pending_and_accumulates_idempotently(client, db):
    enable_temporary(client)
    material_id, _, batch_ids = seed_material(db, temporary_batches=(5,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 4}])
    approved = approve_claim(client, db, form["id"])
    item = approved["items"][0]

    first_items = [{
        "id": item["id"],
        "outbound_quantity": 1,
        "batches": [{"batch_id": batch_ids[0], "quantity": 1}],
    }]
    first = outbound_claim(client, approved, first_items, "partial-first")
    assert first.status_code == 200, first.get_data(as_text=True)
    first_form = first.get_json()["form"]
    assert first.get_json()["partial_outbound"] is True
    assert first_form["status"] == "outbound"
    assert first_form["items"][0]["outbound_quantity"] == 1
    assert first_form["items"][0]["remaining_outbound_quantity"] == 3
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 4
    expected_assignee_id = next(
        task["assignee_id"]
        for task in approved["tasks"]
        if task["step_code"] == "outbound" and task["status"] == "pending"
    )
    pending = db.execute(
        "SELECT step_code, assignee_id FROM workflow_tasks WHERE form_id = ? AND status = 'pending'",
        (form["id"],),
    ).fetchall()
    assert [tuple(row) for row in pending] == [("outbound", expected_assignee_id)]

    retry = outbound_claim(client, first_form, first_items, "partial-first")
    assert retry.status_code == 200
    assert retry.get_json()["idempotent"] is True
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 4

    second_items = [{
        "id": item["id"],
        "outbound_quantity": 3,
        "batches": [{"batch_id": batch_ids[0], "quantity": 3}],
    }]
    second = outbound_claim(client, first_form, second_items, "partial-second")
    assert second.status_code == 200, second.get_data(as_text=True)
    completed = second.get_json()["form"]
    assert second.get_json()["partial_outbound"] is False
    assert completed["status"] == "completed"
    assert completed["items"][0]["outbound_quantity"] == 4
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 1
    assert db.execute(
        "SELECT COUNT(*) FROM claim_outbound_operations WHERE form_id = ?", (form["id"],)
    ).fetchone()[0] == 2
    obligations = db.execute(
        "SELECT issued_quantity FROM temporary_issue_obligations ORDER BY id"
    ).fetchall()
    assert [row[0] for row in obligations] == [1, 3]


def test_concurrent_duplicate_outbound_is_idempotent(app, client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, temporary_batches=(5,))
    form, _ = create_claim(client, db, [{"material_id": material_id, "request_quantity": 4}])
    approved = approve_claim(client, db, form["id"])

    first = app.test_client()
    second = app.test_client()
    login(first)
    login(second)
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def run(test_client):
        try:
            barrier.wait()
            response = test_client.post(
                f"/api/claims/{approved['id']}/outbound",
                json={
                    "items": [
                        {
                            "id": approved["items"][0]["id"],
                            "outbound_quantity": approved["items"][0]["request_quantity"],
                        }
                    ],
                    "signature": "并发测试",
                    "operation_key": f"concurrent-{approved['id']}",
                },
            )
            results.append((response.status_code, response.get_json()))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(first,)), threading.Thread(target=run, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert sorted(status for status, _ in results) == [200, 200]
    assert sorted(bool(body.get("idempotent")) for _, body in results) == [False, True]
    assert material_stock_total(db.cursor(), material_id, STOCK_SOURCE_TEMPORARY) == 1
    assert db.execute("SELECT COUNT(*) FROM temporary_issue_obligations").fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?", (form["form_no"],)
    ).fetchone()[0] == 1
