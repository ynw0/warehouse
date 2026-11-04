# -*- coding: utf-8 -*-
"""半成品/成品验收按勾选批次合计消耗。"""

import json

import app as app_module
from warehouse_suit.production_service import (
    claimed_material_batch_pool,
    insert_material_consumptions,
    prepare_material_component_consumptions,
    production_item_payload,
)


def _login(client, username="warehouse", password="test"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.get_json()["user"]


def _seed_claimed_batches(db, quantities, received_dates=None):
    cursor = db.cursor()
    now = app_module.now_text()
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES (?, ?, '个', ?, ?)",
        ("TEST001", "测试物料", now, now),
    )
    material_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_forms
        (form_no, form_type, title, status, current_step, applicant_id, created_at, updated_at)
        VALUES (?, 'claim', ?, 'completed', 'completed', ?, ?, ?)
        """,
        ("CK2026010101", "测试领用", 1, now, now),
    )
    form_id = cursor.lastrowid
    batch_ids = []
    consumed = []
    for index, quantity in enumerate(quantities):
        received = (received_dates or [])[index] if received_dates else f"2026-01-{index + 1:02d}"
        cursor.execute(
            """
            INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, received_date, stock_source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'formal', ?, ?)
            """,
            (material_id, f"B{index + 1:03d}", quantity, index + 1, received, now, now),
        )
        batch_id = cursor.lastrowid
        batch_ids.append(batch_id)
        consumed.append({"batch_id": batch_id, "batch_no": f"B{index + 1:03d}", "quantity": quantity})
    cursor.execute(
        """
        INSERT INTO workflow_items
        (form_id, material_id, material_code, material_name, unit, outbound_quantity, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_id,
            material_id,
            "TEST001",
            "测试物料",
            "个",
            sum(quantities),
            json.dumps({"consumed_batches": consumed}, ensure_ascii=False),
        ),
    )
    db.commit()
    return material_id, batch_ids


def test_prepare_consumes_sum_of_selected_batches(db):
    material_id, batch_ids = _seed_claimed_batches(db, [5, 3], ["2026-01-01", "2026-01-02"])
    cursor = db.cursor()
    prepared = prepare_material_component_consumptions(
        cursor,
        [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
        8,
    )
    assert [(item["batch_id"], item["total_quantity"]) for item in prepared] == [
        (batch_ids[0], 5),
        (batch_ids[1], 3),
    ]


def test_prepare_rejects_when_selected_batches_insufficient(db):
    material_id, batch_ids = _seed_claimed_batches(db, [5, 3])
    cursor = db.cursor()
    try:
        prepare_material_component_consumptions(
            cursor,
            [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": [batch_ids[0]]}],
            8,
        )
    except ValueError as exc:
        assert "已领用未消耗数量不足" in str(exc)
    else:
        raise AssertionError("expected insufficient claimed quantity")


def test_semifinished_acceptance_uses_multiple_claim_batches(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [5, 3], ["2026-01-01", "2026-01-02"])
    resp = client.post(
        "/api/production/semifinished-acceptance",
        json={
            "name": "半品多批",
            "project_code": "PJ-1",
            "acceptance_quantity": 8,
            "qualified_quantity": 8,
            "unqualified_quantity": 0,
            "appearance_ok_quantity": 8,
            "function_ok_quantity": 8,
            "performance_ok_quantity": 8,
            "maker_id": user["id"],
            "components": [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    acceptance_id = resp.get_json()["acceptance"]["id"]
    cursor = db.cursor()
    cursor.execute(
        "SELECT batch_id, quantity FROM production_material_consumptions WHERE source_id = ? ORDER BY batch_id",
        (acceptance_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    assert [(row["batch_id"], row["quantity"]) for row in rows] == [
        (batch_ids[0], 5),
        (batch_ids[1], 3),
    ]


def test_production_item_payload_keeps_project_code():
    payload = production_item_payload(
        "semifinished",
        {"name": "半品代号", "acceptance_quantity": 1, "project_code": "PJ-88"},
    )
    assert payload["project_code"] == "PJ-88"


def test_semifinished_workflow_persists_project_code(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [1])
    resp = client.post(
        "/api/production/semifinished/workflows",
        json={
            "name": "半品流程代号",
            "acceptance_quantity": 1,
            "project_code": "PJ-88",
            "maker_id": user["id"],
            "components": [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    assert (form.get("data") or {}).get("project_code") == "PJ-88"
    assert (form["items"][0].get("data") or {}).get("project_code") == "PJ-88"


def test_semifinished_reinspect_uses_next_serial_after_other_inbound(client, db):
    warehouse = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [1])
    resp = client.post(
        "/api/production/semifinished/workflows",
        json={
            "name": "半品占用",
            "project_code": "PJ-1",
            "acceptance_quantity": 1,
            "maker_id": warehouse["id"],
            "components": [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    form_id = form["id"]
    item_id = form["items"][0]["id"]
    occupied_serial = "BP半品占用001"

    cursor = db.cursor()
    now = app_module.now_text()
    cursor.execute(
        """
        INSERT INTO semifinished_inventory
            (name, spec, unit, quantity, serial_no, acceptance_date, created_at, updated_at)
        VALUES (?, '', '个', 1, ?, ?, ?, ?)
        """,
        ("半品占用", occupied_serial, "2026-09-17", now, now),
    )
    cursor.execute("SELECT data_json FROM workflow_items WHERE id = ?", (item_id,))
    item_data = json.loads(cursor.fetchone()["data_json"] or "{}")
    item_data["serial_items"] = [
        {"serial_no": occupied_serial, "qualified": True, "abnormal_conditions": [], "remark": "旧号"}
    ]
    cursor.execute(
        "UPDATE workflow_items SET data_json = ? WHERE id = ?",
        (json.dumps(item_data, ensure_ascii=False), item_id),
    )
    db.commit()

    serials = client.get(
        f"/api/production/semifinished/serials?name=半品占用&count=1&form_id={form_id}"
    ).get_json()["serials"]
    assert serials == ["BP半品占用002"]

    admin_id = _login(client, "admin", "admin")["id"]
    _login(client)
    inspect = client.post(
        f"/api/production/semifinished/{form_id}/inspect",
        json={
            "items": [
                {
                    "id": item_id,
                    "serial_items": [
                        {"serial_no": serials[0], "qualified": True, "abnormal_conditions": [], "remark": "旧号"}
                    ],
                }
            ],
            "leader_id": admin_id,
            "warehouse_user_id": warehouse["id"],
            "decision": "同意",
            "signature": "仓库管理员",
        },
    )
    assert inspect.status_code == 200, inspect.get_data(as_text=True)
    saved = ((inspect.get_json()["form"]["items"][0].get("data") or {}).get("serial_items") or [{}])[0]
    assert saved.get("serial_no") == "BP半品占用002"


def test_insert_consumption_reduces_claimed_pool(db):
    material_id, batch_ids = _seed_claimed_batches(db, [5])
    cursor = db.cursor()
    prepared = prepare_material_component_consumptions(
        cursor,
        [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
        5,
    )
    insert_material_consumptions(cursor, "semifinished", 1, prepared)
    db.commit()
    assert claimed_material_batch_pool(cursor) == []


def test_finished_consumption_marks_my_claims(client, db):
    from tests.test_claim_returns import seed_issue

    outbound_id, batch_id, material_id = seed_issue(db, "formal")
    cursor = db.cursor()
    prepared = prepare_material_component_consumptions(
        cursor,
        [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": [batch_id]}],
        2,
    )
    insert_material_consumptions(cursor, "finished", 99, prepared)
    db.commit()
    _login(client, "testuser", "test")
    row = client.get("/api/my-claims").get_json()["items"][0]
    assert int(row["outbound_record_id"]) == int(outbound_id)
    assert row["consumed_quantity"] == 2
    assert row["consume_status"] == "已消耗-成品制作"
    assert row["returnable_quantity"] == 4


def test_semifinished_history_includes_unit_components(client, db):
    material_id, batch_ids = _seed_claimed_batches(db, [4])
    cursor = db.cursor()
    now = app_module.now_text()
    prepared = prepare_material_component_consumptions(
        cursor,
        [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
        2,
    )
    cursor.execute(
        """
        INSERT INTO semifinished_acceptances
            (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date, created_at, updated_at)
        VALUES (?, ?, '', 2, '台', ?, ?, ?)
        """,
        ("SA-COMP", "组成半品", now[:10], now, now),
    )
    acceptance_id = cursor.lastrowid
    insert_material_consumptions(cursor, "semifinished", acceptance_id, prepared)
    cursor.execute(
        """
        INSERT INTO semifinished_inventory
            (acceptance_id, name, spec, unit, quantity, components_json, serial_no,
             acceptance_date, created_at, updated_at)
        VALUES (?, '组成半品', '', '台', 1, ?, 'BP组成半品001', ?, ?, ?)
        """,
        (acceptance_id, json.dumps(prepared, ensure_ascii=False), now[:10], now, now),
    )
    inventory_id = cursor.lastrowid
    db.commit()
    _login(client)
    resp = client.get(f"/api/items/semifinished/{inventory_id}/history")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    components = resp.get_json()["item"]["components"]
    assert len(components) == 1
    assert int(components[0]["batch_id"]) == int(batch_ids[0])
    assert abs(float(components[0]["per_unit_quantity"]) - 1) < 1e-9
    assert components[0]["claim_applicant"]


def test_semifinished_history_rejects_unmatched_components(client, db):
    cursor = db.cursor()
    now = app_module.now_text()
    cursor.execute(
        """
        INSERT INTO semifinished_acceptances
            (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date, created_at, updated_at)
        VALUES (?, ?, '', 2, '台', ?, ?, ?)
        """,
        ("SA-BAD", "对不上半品", now[:10], now, now),
    )
    acceptance_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO semifinished_inventory
            (acceptance_id, name, spec, unit, quantity, components_json, serial_no,
             acceptance_date, created_at, updated_at)
        VALUES (?, '对不上半品', '', '台', 1, ?, 'BP对不上001', ?, ?, ?)
        """,
        (
            acceptance_id,
            json.dumps(
                [{"material_id": 1, "batch_id": 1, "total_quantity": 2, "per_unit_quantity": 1}],
                ensure_ascii=False,
            ),
            now[:10],
            now,
            now,
        ),
    )
    inventory_id = cursor.lastrowid
    db.commit()
    _login(client)
    resp = client.get(f"/api/items/semifinished/{inventory_id}/history")
    assert resp.status_code == 400
    assert "对不上" in resp.get_json()["error"]


def _create_semifinished_units(client, user_id, material_id, batch_ids, quantity, name, qualified=None, serial_items=None):
    qualified = quantity if qualified is None else qualified
    payload = {
        "name": name,
        "project_code": "PJ-1",
        "acceptance_quantity": quantity,
        "qualified_quantity": qualified,
        "unqualified_quantity": quantity - qualified,
        "appearance_ok_quantity": qualified,
        "function_ok_quantity": qualified,
        "performance_ok_quantity": qualified,
        "maker_id": user_id,
        "components": [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": batch_ids}],
    }
    if serial_items is not None:
        payload["serial_items"] = serial_items
    resp = client.post("/api/production/semifinished-acceptance", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["acceptance"]


def _inventory_ids(db, name):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM semifinished_inventory WHERE name = ? ORDER BY id", (name,))
    return [int(row["id"]) for row in cursor.fetchall()]


def _occupancy_qty(db, acceptance_id):
    cursor = db.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM production_material_consumptions WHERE source_type = 'semifinished' AND source_id = ?",
        (acceptance_id,),
    )
    pmc = float(cursor.fetchone()[0] or 0)
    cursor.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM claim_consumptions WHERE production_source_type = 'semifinished' AND production_source_id = ?",
        (acceptance_id,),
    )
    cc = float(cursor.fetchone()[0] or 0)
    return pmc, cc


def test_disassemble_restores_claimed_pool(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [2])
    acceptance = _create_semifinished_units(client, user["id"], material_id, batch_ids, 2, "拆解半品")
    inventory_ids = _inventory_ids(db, "拆解半品")
    assert len(inventory_ids) == 2
    cursor = db.cursor()
    assert claimed_material_batch_pool(cursor) == []
    resp = client.post(f"/api/production/semifinished-inventory/{inventory_ids[0]}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    pool = claimed_material_batch_pool(cursor)
    assert len(pool) == 1
    assert abs(float(pool[0]["available_quantity"]) - 1) < 1e-9
    pmc, cc = _occupancy_qty(db, acceptance["id"])
    assert abs(pmc - 1) < 1e-9
    assert abs(cc - 1) < 1e-9
    assert _inventory_ids(db, "拆解半品") == [inventory_ids[1]]
    resp = client.post(f"/api/production/semifinished-inventory/{inventory_ids[1]}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    pool = claimed_material_batch_pool(cursor)
    assert abs(float(pool[0]["available_quantity"]) - 2) < 1e-9
    pmc, cc = _occupancy_qty(db, acceptance["id"])
    assert pmc == 0
    assert cc == 0
    assert _inventory_ids(db, "拆解半品") == []


def test_delete_forbidden_when_occupancy_exists(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [1])
    _create_semifinished_units(client, user["id"], material_id, batch_ids, 1, "占用删除半品")
    inventory_id = _inventory_ids(db, "占用删除半品")[0]
    resp = client.delete(f"/api/production/semifinished-inventory/{inventory_id}")
    assert resp.status_code == 400
    assert "拆解" in resp.get_json()["error"]
    assert _inventory_ids(db, "占用删除半品") == [inventory_id]


def test_disassemble_last_clears_remainder_after_plain_delete(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [2])
    acceptance = _create_semifinished_units(client, user["id"], material_id, batch_ids, 2, "尾差拆解半品")
    inventory_ids = _inventory_ids(db, "尾差拆解半品")
    cursor = db.cursor()
    cursor.execute("DELETE FROM semifinished_inventory WHERE id = ?", (inventory_ids[0],))
    db.commit()
    pmc, cc = _occupancy_qty(db, acceptance["id"])
    assert abs(pmc - 2) < 1e-9
    assert abs(cc - 2) < 1e-9
    resp = client.post(f"/api/production/semifinished-inventory/{inventory_ids[1]}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    pmc, cc = _occupancy_qty(db, acceptance["id"])
    assert pmc == 0
    assert cc == 0
    pool = claimed_material_batch_pool(cursor)
    assert abs(float(pool[0]["available_quantity"]) - 2) < 1e-9


def test_disassemble_with_defectives_keeps_defective_share(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [3])
    acceptance = _create_semifinished_units(
        client,
        user["id"],
        material_id,
        batch_ids,
        3,
        "含不合格拆解半品",
        qualified=2,
    )
    inventory_ids = _inventory_ids(db, "含不合格拆解半品")
    assert len(inventory_ids) == 2
    client.post(f"/api/production/semifinished-inventory/{inventory_ids[0]}/disassemble")
    resp = client.post(f"/api/production/semifinished-inventory/{inventory_ids[1]}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    pmc, cc = _occupancy_qty(db, acceptance["id"])
    assert abs(pmc - 1) < 1e-9
    assert abs(cc - 1) < 1e-9


def test_disassemble_old_data_without_claim_consumptions(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [1])
    acceptance = _create_semifinished_units(client, user["id"], material_id, batch_ids, 1, "旧账拆解半品")
    inventory_id = _inventory_ids(db, "旧账拆解半品")[0]
    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM claim_consumptions WHERE production_source_type = 'semifinished' AND production_source_id = ?",
        (acceptance["id"],),
    )
    db.commit()
    assert claimed_material_batch_pool(cursor)
    resp = client.post(f"/api/production/semifinished-inventory/{inventory_id}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    pmc, cc = _occupancy_qty(db, acceptance["id"])
    assert pmc == 0
    assert cc == 0
    assert _inventory_ids(db, "旧账拆解半品") == []
    pool = claimed_material_batch_pool(cursor)
    assert abs(float(pool[0]["available_quantity"]) - 1) < 1e-9


def test_disassemble_without_occupancy_deletes_inventory(client, db):
    _login(client)
    cursor = db.cursor()
    now = app_module.now_text()
    cursor.execute(
        """
        INSERT INTO semifinished_inventory
            (name, spec, unit, quantity, serial_no, acceptance_date, created_at, updated_at)
        VALUES ('无占用半品', '', '台', 1, 'BP无占用001', ?, ?, ?)
        """,
        (now[:10], now, now),
    )
    inventory_id = cursor.lastrowid
    db.commit()
    resp = client.post(f"/api/production/semifinished-inventory/{inventory_id}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    cursor.execute("SELECT id FROM semifinished_inventory WHERE id = ?", (inventory_id,))
    assert cursor.fetchone() is None


def test_disassemble_rejects_used_or_borrowed(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [2])
    _create_semifinished_units(client, user["id"], material_id, batch_ids, 2, "占用中拆解半品")
    inventory_ids = _inventory_ids(db, "占用中拆解半品")
    cursor = db.cursor()
    cursor.execute("UPDATE semifinished_inventory SET used_quantity = 1 WHERE id = ?", (inventory_ids[0],))
    cursor.execute("UPDATE semifinished_inventory SET borrowed_quantity = 1 WHERE id = ?", (inventory_ids[1],))
    db.commit()
    used_resp = client.post(f"/api/production/semifinished-inventory/{inventory_ids[0]}/disassemble")
    assert used_resp.status_code == 400
    assert "成品" in used_resp.get_json()["error"]
    borrowed_resp = client.post(f"/api/production/semifinished-inventory/{inventory_ids[1]}/disassemble")
    assert borrowed_resp.status_code == 400
    assert "借用" in borrowed_resp.get_json()["error"]


def test_disassemble_restores_my_claims(client, db):
    from tests.test_claim_returns import seed_issue

    outbound_id, batch_id, material_id = seed_issue(db, "formal")
    cursor = db.cursor()
    prepared = prepare_material_component_consumptions(
        cursor,
        [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": [batch_id]}],
        2,
    )
    now = app_module.now_text()
    cursor.execute(
        """
        INSERT INTO semifinished_acceptances
            (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date, created_at, updated_at)
        VALUES (?, ?, '', 2, '台', ?, ?, ?)
        """,
        ("SA-DIS", "领用回冲半品", now[:10], now, now),
    )
    acceptance_id = cursor.lastrowid
    insert_material_consumptions(cursor, "semifinished", acceptance_id, prepared)
    ids = []
    for index in range(2):
        cursor.execute(
            """
            INSERT INTO semifinished_inventory
                (acceptance_id, name, spec, unit, quantity, components_json, serial_no,
                 acceptance_date, created_at, updated_at)
            VALUES (?, '领用回冲半品', '', '台', 1, ?, ?, ?, ?, ?)
            """,
            (
                acceptance_id,
                json.dumps(prepared, ensure_ascii=False),
                f"BP领用回冲半品{index + 1:03d}",
                now[:10],
                now,
                now,
            ),
        )
        ids.append(cursor.lastrowid)
    db.commit()
    _login(client)
    resp = client.post(f"/api/production/semifinished-inventory/{ids[0]}/disassemble")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _login(client, "testuser", "test")
    row = client.get("/api/my-claims").get_json()["items"][0]
    assert int(row["outbound_record_id"]) == int(outbound_id)
    assert abs(float(row["consumed_quantity"]) - 1) < 1e-9
    assert row["consume_status"] == "已消耗-半成品制作"
    assert abs(float(row["returnable_quantity"]) - 5) < 1e-9


def test_acceptance_pool_allows_office_and_auxiliary_not_other_project(db):
    cursor = db.cursor()
    now = app_module.now_text()
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES ('POOL001', '池物料', '个', ?, ?)",
        (now, now),
    )
    material_id = cursor.lastrowid

    def add_claimed(batch_no, project, purpose, kind):
        cursor.execute(
            """
            INSERT INTO material_batches
                (material_id, batch_no, quantity, unit_price, received_date, stock_source,
                 purchase_project, created_at, updated_at)
            VALUES (?, ?, 2, 1, '2026-01-01', 'formal', ?, ?, ?)
            """,
            (material_id, batch_no, project, now, now),
        )
        batch_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO workflow_forms
                (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
            VALUES (?, 'claim', ?, 'completed', 'completed', 1, ?, ?, ?)
            """,
            (
                batch_no,
                batch_no,
                json.dumps({"purpose": purpose, "rd_item_kind": kind}, ensure_ascii=False),
                now,
                now,
            ),
        )
        form_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, unit, outbound_quantity, data_json)
            VALUES (?, ?, 'POOL001', '池物料', '个', 2, ?)
            """,
            (
                form_id,
                material_id,
                json.dumps({"consumed_batches": [{"batch_id": batch_id, "quantity": 2}]}, ensure_ascii=False),
            ),
        )
        return batch_id

    office_id = add_claimed("POOL-OFF", "办公", "办公", "")
    auxiliary_id = add_claimed("POOL-AUX", "OTHER", "研发", "辅料")
    other_id = add_claimed("POOL-OTH", "OTHER", "研发", "项目物料")
    db.commit()
    prepared = prepare_material_component_consumptions(
        cursor,
        [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": [office_id, auxiliary_id]}],
        4,
        purchase_project="PJ-1",
    )
    assert {item["batch_id"] for item in prepared} == {office_id, auxiliary_id}
    try:
        prepare_material_component_consumptions(
            cursor,
            [{"material_id": material_id, "per_unit_quantity": 1, "batch_ids": [other_id]}],
            1,
            purchase_project="PJ-1",
        )
    except ValueError as exc:
        assert "只能选择本项目、办公或辅料的已领用物料" in str(exc)
    else:
        raise AssertionError("other project material should be rejected")


def _semifinished_workflow(client, user_id, material_id, batch_ids, name, acceptance_quantity, per_unit_quantity):
    return client.post(
        "/api/production/semifinished/workflows",
        json={
            "name": name,
            "acceptance_quantity": acceptance_quantity,
            "project_code": "PJ-1",
            "maker_id": user_id,
            "components": [
                {
                    "material_id": material_id,
                    "per_unit_quantity": per_unit_quantity,
                    "batch_ids": batch_ids,
                }
            ],
        },
    )


def test_semifinished_submit_holds_unconsumed_quantity(client, db):
    user = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [16])
    first = _semifinished_workflow(client, user["id"], material_id, batch_ids, "先提十六", 16, 1)
    assert first.status_code == 200, first.get_data(as_text=True)
    first_id = first.get_json()["form"]["id"]
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT production_source_type, production_source_id, quantity
        FROM claim_consumptions
        WHERE production_source_id = ?
        """,
        (first_id,),
    )
    holds = [dict(row) for row in cursor.fetchall()]
    assert holds == [
        {"production_source_type": "semifinished_hold", "production_source_id": first_id, "quantity": 16}
    ]
    second = _semifinished_workflow(client, user["id"], material_id, batch_ids, "再提四个", 4, 1)
    assert second.status_code == 400
    assert "已领用未消耗" in second.get_json()["error"]
    deleted = client.delete(f"/api/workflows/{first_id}")
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    cursor.execute("SELECT COUNT(*) FROM claim_consumptions WHERE production_source_id = ?", (first_id,))
    assert cursor.fetchone()[0] == 0
    second = _semifinished_workflow(client, user["id"], material_id, batch_ids, "删除后可提", 4, 1)
    assert second.status_code == 200, second.get_data(as_text=True)


def test_semifinished_inbound_keeps_its_own_hold(client, db):
    warehouse = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [16])
    created = _semifinished_workflow(client, warehouse["id"], material_id, batch_ids, "占用入库", 1, 16)
    assert created.status_code == 200, created.get_data(as_text=True)
    form = created.get_json()["form"]
    form_id = form["id"]
    item_id = form["items"][0]["id"]
    blocked = _semifinished_workflow(client, warehouse["id"], material_id, batch_ids, "不应再提", 1, 4)
    assert blocked.status_code == 400
    serials = client.get(
        f"/api/production/semifinished/serials?name=占用入库&count=1&form_id={form_id}"
    ).get_json()["serials"]
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client)
    inspect = client.post(
        f"/api/production/semifinished/{form_id}/inspect",
        json={
            "items": [{"id": item_id, "serial_items": [{"serial_no": serials[0], "qualified": True, "abnormal_conditions": [], "remark": ""}]}],
            "leader_id": admin_id,
            "warehouse_user_id": warehouse["id"],
            "decision": "同意",
            "signature": "仓库管理员",
        },
    )
    assert inspect.status_code == 200, inspect.get_data(as_text=True)
    _login(client, "admin", "admin")
    leader = client.post(
        f"/api/production/semifinished/{form_id}/leader",
        json={"decision": "同意", "signature": "管理员"},
    )
    assert leader.status_code == 200, leader.get_data(as_text=True)
    _login(client)
    inbound = client.post(
        f"/api/production/semifinished/{form_id}/inbound",
        json={"approved_quantity": 1, "signature": "仓库管理员"},
    )
    assert inbound.status_code == 200, inbound.get_data(as_text=True)
    cursor = db.cursor()
    cursor.execute(
        "SELECT production_source_type, quantity FROM claim_consumptions ORDER BY id"
    )
    rows = [dict(row) for row in cursor.fetchall()]
    assert rows == [{"production_source_type": "semifinished", "quantity": 16}]
    still_blocked = _semifinished_workflow(client, warehouse["id"], material_id, batch_ids, "入库后仍不可提", 1, 4)
    assert still_blocked.status_code == 400


def test_revoke_completed_semifinished_restores_hold(client, db):
    warehouse = _login(client)
    material_id, batch_ids = _seed_claimed_batches(db, [4])
    created = _semifinished_workflow(client, warehouse["id"], material_id, batch_ids, "撤销半品", 1, 2)
    assert created.status_code == 200, created.get_data(as_text=True)
    form = created.get_json()["form"]
    form_id = form["id"]
    item_id = form["items"][0]["id"]
    serials = client.get(
        f"/api/production/semifinished/serials?name=撤销半品&count=1&form_id={form_id}"
    ).get_json()["serials"]
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client)
    inspect = client.post(
        f"/api/production/semifinished/{form_id}/inspect",
        json={
            "items": [{"id": item_id, "serial_items": [{"serial_no": serials[0], "qualified": True, "abnormal_conditions": [], "remark": ""}]}],
            "leader_id": admin_id,
            "warehouse_user_id": warehouse["id"],
            "decision": "同意",
            "signature": "仓库管理员",
        },
    )
    assert inspect.status_code == 200, inspect.get_data(as_text=True)
    _login(client, "admin", "admin")
    assert client.post(
        f"/api/production/semifinished/{form_id}/leader",
        json={"decision": "同意", "signature": "管理员"},
    ).status_code == 200
    _login(client)
    assert client.post(
        f"/api/production/semifinished/{form_id}/inbound",
        json={"approved_quantity": 1, "signature": "仓库管理员"},
    ).status_code == 200
    cursor = db.cursor()
    cursor.execute("UPDATE semifinished_inventory SET used_quantity = 1")
    db.commit()
    _login(client, "admin", "admin")
    blocked = client.post(f"/api/production/semifinished/{form_id}/revoke", json={})
    assert blocked.status_code == 400
    assert "成品" in blocked.get_json()["error"]
    cursor.execute("UPDATE semifinished_inventory SET used_quantity = 0")
    db.commit()
    revoked = client.post(f"/api/production/semifinished/{form_id}/revoke", json={})
    assert revoked.status_code == 200, revoked.get_data(as_text=True)
    assert revoked.get_json()["form"]["status"] == "inbound"
    assert db.execute("SELECT COUNT(*) FROM semifinished_inventory").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM semifinished_acceptances").fetchone()[0] == 0
    holds = db.execute(
        "SELECT production_source_type, production_source_id, quantity FROM claim_consumptions"
    ).fetchall()
    assert [(row["production_source_type"], row["production_source_id"], row["quantity"]) for row in holds] == [
        ("semifinished_hold", form_id, 2)
    ]
    _login(client)
    again = _semifinished_workflow(client, warehouse["id"], material_id, batch_ids, "撤销后不可再占", 1, 3)
    assert again.status_code == 400

