import app as app_module

from warehouse_suit.borrow_service import has_active_temporary_borrows
from warehouse_suit.inventory_constants import (
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import update_inventory_total
from warehouse_suit.material_repository import material_stock_total


_COUNTER = [0]


def login(client, username="warehouse", password="test"):
    response = client.post(
        "/api/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["user"]


def enable_temporary(client, enabled=True):
    login(client, "admin", "admin")
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": enabled},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def seed_material(db, formal_batches=(), temporary_batches=()):
    _COUNTER[0] += 1
    code = f"9130010001{_COUNTER[0]:04d}"
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, unit, created_at, updated_at)
        VALUES (?, ?, '个', ?, ?)
        """,
        (
            code,
            f"第四阶段物料{_COUNTER[0]}",
            app_module.now_text(),
            app_module.now_text(),
        ),
    )
    material_id = cursor.lastrowid
    batch_ids = {STOCK_SOURCE_FORMAL: [], STOCK_SOURCE_TEMPORARY: []}
    for stock_source, quantities in (
        (STOCK_SOURCE_FORMAL, formal_batches),
        (STOCK_SOURCE_TEMPORARY, temporary_batches),
    ):
        for index, quantity in enumerate(quantities):
            cursor.execute(
                """
                INSERT INTO material_batches
                    (material_id, batch_no, quantity, unit_price, warehouse_type,
                     received_date, stock_source, inventory_status, version,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'office', ?, ?, 'available', 0, ?, ?)
                """,
                (
                    material_id,
                    f"{stock_source.upper()}-{material_id}-{index + 1}",
                    quantity,
                    1 if stock_source == STOCK_SOURCE_FORMAL else 2,
                    f"2026-02-{index + 1:02d}",
                    stock_source,
                    app_module.now_text(),
                    app_module.now_text(),
                ),
            )
            batch_ids[stock_source].append(cursor.lastrowid)
    update_inventory_total(cursor, material_id)
    db.commit()
    return material_id, code, batch_ids


def create_borrow(client, db, items):
    borrower = login(client)
    leader_id = db.execute(
        "SELECT id FROM users WHERE username = 'admin'"
    ).fetchone()[0]
    response = client.post(
        "/api/borrows",
        json={
            "leader_id": leader_id,
            "expected_return_date": "2026-12-31",
            "items": items,
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"], borrower


def approve_borrow(client, db, form_id):
    warehouse_id = db.execute(
        "SELECT id FROM users WHERE username = 'warehouse'"
    ).fetchone()[0]
    login(client, "admin", "admin")
    response = client.post(
        f"/api/borrows/{form_id}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"]


def outbound_borrow(client, form):
    login(client)
    return client.post(
        f"/api/borrows/{form['id']}/outbound",
        json={"items": [], "signature": "仓库管理员"},
    )


def create_return(client, record_id, quantity, forged_source=None):
    login(client)
    payload = {
        "borrow_record_id": record_id,
        "return_quantity": quantity,
        "status": "完好",
        "stock_source": forged_source,
    }
    response = client.post("/api/borrow-returns", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"]


def inbound_return(client, form, quantity, forged_source=None):
    login(client)
    return client.post(
        f"/api/borrow-returns/{form['id']}/inbound",
        json={
            "decision": "同意",
            "return_quantity": quantity,
            "stock_source": forged_source,
            "warehouse_type": "office",
            "inbound_date": "2026-03-01",
        },
    )


def source_quantities(form):
    return [
        (item["stock_source"], float(item["request_quantity"]))
        for item in form["items"]
    ]


def obligation_count(db):
    return db.execute(
        "SELECT COUNT(*) FROM temporary_issue_obligations"
    ).fetchone()[0]


def test_borrow_query_and_server_allocation_are_formal_first(client, db):
    enable_temporary(client)
    material_id, code, _ = seed_material(
        db,
        formal_batches=(3,),
        temporary_batches=(5,),
    )
    login(client)
    response = client.get(f"/api/borrow/items?keyword={code}")
    assert response.status_code == 200
    row = next(
        item
        for item in response.get_json()["items"]
        if item["material_id"] == material_id
    )
    assert row["formal_available_quantity"] == 3
    assert row["temporary_available_quantity"] == 5
    assert row["total_available_quantity"] == 8

    before_obligations = obligation_count(db)
    form, _ = create_borrow(
        client,
        db,
        [
            {
                "material_id": material_id,
                "request_quantity": 6,
                "stock_source": STOCK_SOURCE_TEMPORARY,
                "formal_quantity": 0,
                "temporary_quantity": 6,
            }
        ],
    )
    assert source_quantities(form) == [
        (STOCK_SOURCE_FORMAL, 3),
        (STOCK_SOURCE_TEMPORARY, 3),
    ]
    assert db.execute(
        "SELECT COUNT(*) FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?",
        (form["form_no"],),
    ).fetchone()[0] == 0
    assert obligation_count(db) == before_obligations
    assert material_stock_total(db.cursor(), material_id) == 3
    assert material_stock_total(
        db.cursor(), material_id, STOCK_SOURCE_TEMPORARY
    ) == 5


def test_disabled_feature_excludes_temporary_from_new_borrow(client, db):
    material_id, code, _ = seed_material(
        db,
        formal_batches=(2,),
        temporary_batches=(5,),
    )
    login(client)
    row = next(
        item
        for item in client.get(
            f"/api/borrow/items?keyword={code}"
        ).get_json()["items"]
        if item["material_id"] == material_id
    )
    assert row["formal_available_quantity"] == 2
    assert row["temporary_available_quantity"] == 0
    assert row["total_available_quantity"] == 2

    leader_id = db.execute(
        "SELECT id FROM users WHERE username = 'admin'"
    ).fetchone()[0]
    response = client.post(
        "/api/borrows",
        json={
            "leader_id": leader_id,
            "items": [
                {
                    "material_id": material_id,
                    "request_quantity": 3,
                    "stock_source": STOCK_SOURCE_TEMPORARY,
                }
            ],
        },
    )
    assert response.status_code == 400

    form, _ = create_borrow(
        client,
        db,
        [
            {
                "material_id": material_id,
                "request_quantity": 2,
                "stock_source": STOCK_SOURCE_TEMPORARY,
            }
        ],
    )
    assert source_quantities(form) == [(STOCK_SOURCE_FORMAL, 2)]


def test_temporary_cross_batch_borrow_is_batch_idempotent_without_obligation(client, db):
    enable_temporary(client)
    material_id, _, batch_ids = seed_material(
        db,
        temporary_batches=(3, 5),
    )
    initial_obligations = obligation_count(db)
    form, borrower = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 8}],
    )
    assert source_quantities(form) == [(STOCK_SOURCE_TEMPORARY, 8)]
    approve_borrow(client, db, form["id"])

    response = outbound_borrow(client, form)
    assert response.status_code == 200, response.get_data(as_text=True)
    item_id = form["items"][0]["id"]
    records = db.execute(
        """
        SELECT id, batch_id, quantity, stock_source, business_type,
               workflow_item_id, operation_key
        FROM stock_records
        WHERE form_no = ?
        ORDER BY id
        """,
        (form["form_no"],),
    ).fetchall()
    assert [(row["batch_id"], row["quantity"]) for row in records] == [
        (batch_ids[STOCK_SOURCE_TEMPORARY][0], 3),
        (batch_ids[STOCK_SOURCE_TEMPORARY][1], 5),
    ]
    assert [row["operation_key"] for row in records] == [
        f"borrow:{form['id']}:{item_id}:outbound:{batch_ids[STOCK_SOURCE_TEMPORARY][0]}",
        f"borrow:{form['id']}:{item_id}:outbound:{batch_ids[STOCK_SOURCE_TEMPORARY][1]}",
    ]
    assert {row["stock_source"] for row in records} == {
        STOCK_SOURCE_TEMPORARY
    }
    assert {row["business_type"] for row in records} == {"borrow_outbound"}
    borrow_record = db.execute(
        """
        SELECT borrower_id, workflow_item_id, quantity, stock_source, status
        FROM borrow_records
        WHERE borrow_form_id = ?
        """,
        (form["id"],),
    ).fetchone()
    assert tuple(borrow_record) == (
        borrower["id"],
        item_id,
        8,
        STOCK_SOURCE_TEMPORARY,
        "borrowed",
    )
    assert material_stock_total(
        db.cursor(), material_id, STOCK_SOURCE_TEMPORARY
    ) == 0
    assert obligation_count(db) == initial_obligations

    retry = outbound_borrow(client, response.get_json()["form"])
    assert retry.status_code == 200
    assert retry.get_json()["idempotent"] is True
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?",
        (form["form_no"],),
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT COUNT(*) FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()[0] == 1
    assert obligation_count(db) == initial_obligations


def test_mixed_borrow_rolls_back_when_temporary_ledger_fails(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(
        db,
        formal_batches=(3,),
        temporary_batches=(2,),
    )
    initial_obligations = obligation_count(db)
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 5}],
    )
    approve_borrow(client, db, form["id"])
    db.execute(
        """
        CREATE TRIGGER fail_temporary_borrow_ledger
        BEFORE INSERT ON stock_records
        WHEN NEW.business_type = 'borrow_outbound'
             AND NEW.stock_source = 'temporary'
        BEGIN
            SELECT RAISE(FAIL, 'forced temporary borrow ledger failure');
        END
        """
    )
    db.commit()

    response = outbound_borrow(client, form)
    assert response.status_code == 400
    assert material_stock_total(db.cursor(), material_id) == 3
    assert material_stock_total(
        db.cursor(), material_id, STOCK_SOURCE_TEMPORARY
    ) == 2
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?",
        (form["form_no"],),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()[0] == 0
    state = db.execute(
        "SELECT status, current_step FROM workflow_forms WHERE id = ?",
        (form["id"],),
    ).fetchone()
    assert tuple(state) == ("borrow_outbound", "borrow_outbound")
    assert obligation_count(db) == initial_obligations


def test_temporary_return_inherits_source_when_feature_is_disabled(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, temporary_batches=(5,))
    initial_obligations = obligation_count(db)
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 4}],
    )
    approve_borrow(client, db, form["id"])
    outbound = outbound_borrow(client, form)
    assert outbound.status_code == 200
    record = db.execute(
        "SELECT * FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()
    assert record["stock_source"] == STOCK_SOURCE_TEMPORARY
    assert material_stock_total(
        db.cursor(), material_id, STOCK_SOURCE_TEMPORARY
    ) == 1

    enable_temporary(client, False)
    return_form = create_return(
        client,
        record["id"],
        4,
        forged_source=STOCK_SOURCE_FORMAL,
    )
    assert return_form["items"][0]["stock_source"] == STOCK_SOURCE_TEMPORARY
    response = inbound_return(
        client,
        return_form,
        4,
        forged_source=STOCK_SOURCE_FORMAL,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["idempotent"] is False
    assert material_stock_total(db.cursor(), material_id) == 0
    assert material_stock_total(
        db.cursor(), material_id, STOCK_SOURCE_TEMPORARY
    ) == 5
    updated = db.execute(
        "SELECT returned_quantity, status, stock_source FROM borrow_records WHERE id = ?",
        (record["id"],),
    ).fetchone()
    assert tuple(updated) == (4, "returned", STOCK_SOURCE_TEMPORARY)
    ledger = db.execute(
        """
        SELECT stock_source, business_type, operation_key
        FROM stock_records
        WHERE form_no = ? AND operation_type = 'in'
        """,
        (return_form["form_no"],),
    ).fetchone()
    return_item_id = return_form["items"][0]["id"]
    assert tuple(ledger) == (
        STOCK_SOURCE_TEMPORARY,
        "borrow_return_inbound",
        f"borrow_return:{return_form['id']}:{record['id']}:{return_item_id}",
    )
    assert obligation_count(db) == initial_obligations

    retry = inbound_return(client, response.get_json()["form"], 4)
    assert retry.status_code == 200
    assert retry.get_json()["idempotent"] is True
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ? AND operation_type = 'in'",
        (return_form["form_no"],),
    ).fetchone()[0] == 1
    assert obligation_count(db) == initial_obligations


def test_partial_temporary_returns_keep_block_until_fully_returned(client, db):
    enable_temporary(client)
    material_id, _, _ = seed_material(db, temporary_batches=(5,))
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 5}],
    )
    approve_borrow(client, db, form["id"])
    assert outbound_borrow(client, form).status_code == 200
    record_id = db.execute(
        "SELECT id FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()[0]
    assert has_active_temporary_borrows(db.cursor(), material_id) is True

    first = create_return(client, record_id, 2)
    assert inbound_return(client, first, 2).status_code == 200
    partial = db.execute(
        "SELECT returned_quantity, status FROM borrow_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    assert tuple(partial) == (2, "borrowed")
    assert has_active_temporary_borrows(db.cursor(), material_id) is True

    second = create_return(client, record_id, 3)
    assert inbound_return(client, second, 3).status_code == 200
    completed = db.execute(
        "SELECT returned_quantity, status FROM borrow_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    assert tuple(completed) == (5, "returned")
    assert has_active_temporary_borrows(db.cursor(), material_id) is False
    assert obligation_count(db) == 0


def test_formal_active_borrow_does_not_block_temporary_transfer_check(client, db):
    material_id, _, _ = seed_material(db, formal_batches=(4,))
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 3}],
    )
    approve_borrow(client, db, form["id"])
    assert outbound_borrow(client, form).status_code == 200
    assert has_active_temporary_borrows(db.cursor(), material_id) is False
    assert obligation_count(db) == 0

def test_formal_cross_batch_borrow_uses_batch_keys(client, db):
    material_id, _, batch_ids = seed_material(db, formal_batches=(3, 5))
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 8}],
    )
    approve_borrow(client, db, form["id"])
    response = outbound_borrow(client, form)
    assert response.status_code == 200, response.get_data(as_text=True)
    item_id = form["items"][0]["id"]
    records = db.execute(
        """
        SELECT batch_id, quantity, operation_key, stock_source
        FROM stock_records
        WHERE form_no = ?
        ORDER BY id
        """,
        (form["form_no"],),
    ).fetchall()
    assert [(row["batch_id"], row["quantity"]) for row in records] == [
        (batch_ids[STOCK_SOURCE_FORMAL][0], 3),
        (batch_ids[STOCK_SOURCE_FORMAL][1], 5),
    ]
    assert [row["operation_key"] for row in records] == [
        f"borrow:{form['id']}:{item_id}:outbound:{batch_ids[STOCK_SOURCE_FORMAL][0]}",
        f"borrow:{form['id']}:{item_id}:outbound:{batch_ids[STOCK_SOURCE_FORMAL][1]}",
    ]
    assert {row["stock_source"] for row in records} == {STOCK_SOURCE_FORMAL}
    assert obligation_count(db) == 0


def test_returned_borrow_reallocates_sources_before_resubmit(client, db):
    enable_temporary(client)
    material_id, _, batch_ids = seed_material(
        db,
        formal_batches=(2,),
        temporary_batches=(5,),
    )
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 4}],
    )
    assert source_quantities(form) == [
        (STOCK_SOURCE_FORMAL, 2),
        (STOCK_SOURCE_TEMPORARY, 2),
    ]

    login(client, "admin", "admin")
    rejected = client.post(
        f"/api/borrows/{form['id']}/leader",
        json={"decision": "不同意", "remark": "请修改后重提"},
    )
    assert rejected.status_code == 200, rejected.get_data(as_text=True)
    returned = rejected.get_json()["form"]
    assert returned["status"] == "applicant_revision"

    db.execute(
        "UPDATE material_batches SET quantity = 4 WHERE id = ?",
        (batch_ids[STOCK_SOURCE_FORMAL][0],),
    )
    update_inventory_total(db.cursor(), material_id)
    db.commit()

    first_item = returned["items"][0]
    group_key = first_item["data"]["allocation_group_key"]
    login(client)
    updated = client.put(
        f"/api/workflows/{form['id']}",
        json={
            "items": [
                {
                    "id": first_item["id"],
                    "allocation_group_key": group_key,
                    "request_quantity": 4,
                }
            ]
        },
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert source_quantities(updated.get_json()["form"]) == [
        (STOCK_SOURCE_FORMAL, 4)
    ]

    resubmitted = client.post(
        f"/api/workflows/{form['id']}/resubmit-returned",
        json={},
    )
    assert resubmitted.status_code == 200, resubmitted.get_data(as_text=True)
    result = resubmitted.get_json()["form"]
    assert result["status"] == "leader_borrow"
    assert source_quantities(result) == [(STOCK_SOURCE_FORMAL, 4)]
    assert db.execute(
        "SELECT COUNT(*) FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE form_no = ?",
        (form["form_no"],),
    ).fetchone()[0] == 0
    assert obligation_count(db) == 0


def test_completed_borrow_cannot_be_physically_deleted(client, db):
    material_id, _, _ = seed_material(db, formal_batches=(3,))
    form, _ = create_borrow(
        client,
        db,
        [{"material_id": material_id, "request_quantity": 2}],
    )
    approve_borrow(client, db, form["id"])
    assert outbound_borrow(client, form).status_code == 200

    login(client, "admin", "admin")
    response = client.delete(f"/api/workflows/{form['id']}")
    assert response.status_code == 400
    assert "已实际借出" in response.get_json()["error"]
    assert db.execute(
        "SELECT COUNT(*) FROM workflow_forms WHERE id = ?",
        (form["id"],),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM borrow_records WHERE borrow_form_id = ?",
        (form["id"],),
    ).fetchone()[0] == 1
