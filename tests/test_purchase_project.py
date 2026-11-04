# -*- coding: utf-8 -*-
"""批次采购申请项目：跨项目扣库、混单、旧批次、转领用继承。"""

import json

from warehouse_suit.settings import set_setting, workflow_settings


_COUNTER = [0]


def _login(client, username="warehouse", password="test"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["user"]


def _next_code():
    _COUNTER[0] += 1
    return f"1020092102{_COUNTER[0]:04d}"


def _set_project_codes(db, codes):
    cursor = db.cursor()
    settings = workflow_settings(cursor)
    settings["project_codes"] = list(codes)
    set_setting(cursor, "workflow_settings", json.dumps(settings, ensure_ascii=False))
    db.commit()


def _create_material(client, name="项目物料"):
    _login(client)
    resp = client.post(
        "/api/material-master",
        json={"material_code": _next_code(), "name": name, "unit": "个"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["material"]


def _inspect_payload(item, quantity):
    return {
        "items": [
            {
                "id": item["id"],
                "qualified_quantity": quantity,
                "unqualified_quantity": 0,
                "package_ok_quantity": quantity,
                "appearance_ok_quantity": quantity,
                "name_spec_ok_quantity": quantity,
                "usage_ok_quantity": quantity,
            }
        ],
        "decision": "同意",
    }


def _accept_inbound(client, db, material, quantity, purchase_project, unit_price=1):
    warehouse = _login(client, "warehouse", "test")
    resp = client.post(
        "/api/acceptance",
        json={
            "items": [
                {
                    "material_id": material["id"],
                    "material_code": material["material_code"],
                    "material_name": material["name"],
                    "purchase_quantity": quantity,
                    "arrival_quantity": quantity,
                    "total_amount": quantity * unit_price,
                    "purchase_project": purchase_project,
                }
            ],
            "validator_ids": [warehouse["id"]],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    item = form["items"][0]
    resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item, quantity))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    admin = _login(client, "admin", "admin")
    resp = client.post(
        f"/api/acceptance/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse["id"]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _login(client, "warehouse", "test")
    resp = client.post(f"/api/acceptance/{form['id']}/inbound", json={"items": []})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return form, admin


def _approve_and_outbound_claim(client, db, form):
    warehouse_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    _login(client, "admin", "admin")
    resp = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    approved = resp.get_json()["form"]
    _login(client, "warehouse", "test")
    items = [
        {
            "id": item["id"],
            "outbound_quantity": item.get("remaining_outbound_quantity") or item["request_quantity"],
        }
        for item in approved["items"]
    ]
    resp = client.post(
        f"/api/claims/{approved['id']}/outbound",
        json={"items": items, "operation_key": f"pp-out-{approved['id']}"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["form"]


def test_claim_only_consumes_matching_project_batches(client, db):
    _set_project_codes(db, ["PROJ-A", "PROJ-B"])
    material = _create_material(client, "跨项目物料")
    _accept_inbound(client, db, material, 10, "PROJ-A")
    _accept_inbound(client, db, material, 5, "PROJ-B")

    _login(client, "warehouse", "test")
    rows = client.get(f"/api/claims/materials?keyword={material['material_code']}").get_json()
    by_project = {row["purchase_project"]: row for row in rows}
    assert by_project["PROJ-A"]["total_available_quantity"] == 10
    assert by_project["PROJ-B"]["total_available_quantity"] == 5

    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "研发",
            "rd_item_kind": "项目物料",
            "project_code": "PROJ-A",
            "items": [{"material_id": material["id"], "request_quantity": 10, "purchase_project": "PROJ-A"}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _approve_and_outbound_claim(client, db, resp.get_json()["form"])

    remaining = {
        row["purchase_project"]: float(row["quantity"])
        for row in db.execute(
            "SELECT purchase_project, quantity FROM material_batches WHERE material_id = ?",
            (material["id"],),
        )
    }
    assert remaining["PROJ-A"] == 0
    assert remaining["PROJ-B"] == 5


def test_office_and_project_cannot_mix_or_cross_consume(client, db):
    _set_project_codes(db, ["PROJ-A"])
    material = _create_material(client, "混单物料")
    _accept_inbound(client, db, material, 8, "办公")
    _accept_inbound(client, db, material, 6, "PROJ-A")
    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "办公",
            "items": [
                {"material_id": material["id"], "request_quantity": 1, "purchase_project": "办公"},
                {"material_id": material["id"], "request_quantity": 1, "purchase_project": "PROJ-A"},
            ],
        },
    )
    assert resp.status_code == 400
    assert "一个采购申请项目" in resp.get_json()["error"]

    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 8, "purchase_project": "办公"}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _approve_and_outbound_claim(client, db, resp.get_json()["form"])
    leftover = {
        row["purchase_project"]: float(row["quantity"])
        for row in db.execute(
            "SELECT purchase_project, quantity FROM material_batches WHERE material_id = ?",
            (material["id"],),
        )
    }
    assert leftover["办公"] == 0
    assert leftover["PROJ-A"] == 6


def test_untagged_batches_are_hidden_from_claim_pool(client, db):
    material = _create_material(client, "旧批次物料")
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/stock/in",
        json={"material_id": material["id"], "quantity": 4, "total_amount": 4},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rows = client.get(f"/api/claims/materials?keyword={material['material_code']}").get_json()
    assert rows == []
    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 1, "purchase_project": "办公"}],
        },
    )
    assert resp.status_code == 400
    assert "可用库存" in resp.get_json()["error"]


def test_claim_no_longer_requires_hardware_or_structure(client, db):
    _set_project_codes(db, ["PROJ-A"])
    material = _create_material(client, "无硬件结构")
    _accept_inbound(client, db, material, 3, "PROJ-A")
    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "研发",
            "rd_item_kind": "项目物料",
            "project_code": "PROJ-A",
            "items": [{"material_id": material["id"], "request_quantity": 1, "purchase_project": "PROJ-A"}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    assert form["data"]["project_material_kind"] == ""
    assert form["data"]["purchase_project"] == "PROJ-A"


def test_borrow_claim_inherits_project_and_old_form_fails(client, db):
    _set_project_codes(db, ["PROJ-A"])
    material = _create_material(client, "转领用物料")
    _accept_inbound(client, db, material, 5, "PROJ-A")
    admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/borrows",
        json={
            "leader_id": admin_id,
            "purpose": "研发",
            "rd_item_kind": "项目物料",
            "project_code": "PROJ-A",
            "expected_return_date": "2026-12-31",
            "items": [
                {
                    "item_type": "material",
                    "item_ref_id": material["id"],
                    "request_quantity": 2,
                    "purchase_project": "PROJ-A",
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    warehouse_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    _login(client, "admin", "admin")
    resp = client.post(
        f"/api/borrows/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _login(client, "warehouse", "test")
    resp = client.post(f"/api/borrows/{form['id']}/outbound", json={"items": []})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    record_id = db.execute(
        "SELECT id FROM borrow_records WHERE borrow_form_id = ?", (form["id"],)
    ).fetchone()[0]
    resp = client.post(f"/api/borrows/{record_id}/claim", json={"quantity": 2})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    claim_form_id = resp.get_json()["form_id"]
    row = dict(
        db.execute("SELECT data_json FROM workflow_forms WHERE id = ?", (claim_form_id,)).fetchone()
    )
    data = json.loads(row["data_json"])
    assert data["purchase_project"] == "PROJ-A"
    assert data["purpose"] == "研发"
    assert data["project_code"] == "PROJ-A"

    cursor = db.cursor()
    stamp = db.execute("SELECT datetime('now')").fetchone()[0]
    user_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('JY-OLD-PP', 'borrow', '旧借用', 'completed', 'completed', ?, '{}', ?, ?)
        """,
        (user_id, stamp, stamp),
    )
    old_form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO borrow_records
            (borrow_no, item_type, item_name, quantity, returned_quantity, claimed_quantity,
             status, borrower_id, borrow_form_id, data_json, created_at, updated_at)
        VALUES ('BR-OLD-PP', 'material', '旧借用物料', 2, 0, 0, 'borrowed', ?, ?, '{}', ?, ?)
        """,
        (user_id, old_form_id, stamp, stamp),
    )
    old_record_id = cursor.lastrowid
    db.commit()
    resp = client.post(f"/api/borrows/{old_record_id}/claim", json={"quantity": 1})
    assert resp.status_code == 400
    assert "采购申请项目" in resp.get_json()["error"]


def test_duplicate_acceptance_ignores_same_row_with_different_project(client, db):
    _set_project_codes(db, ["PROJ-A", "PROJ-B"])
    material = _create_material(client, "重复验收物料")
    _accept_inbound(client, db, material, 2, "PROJ-A", unit_price=3)
    _login(client, "warehouse", "test")
    item = {
        "material_id": material["id"],
        "material_name": material["name"],
        "purchase_quantity": 2,
        "arrival_quantity": 2,
        "unit_price": 3,
        "purchase_project": "PROJ-B",
    }
    resp = client.post("/api/workflows/duplicate-check", json={"form_type": "acceptance", "items": [item]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("matches") == []
    item["purchase_project"] = "PROJ-A"
    resp = client.post("/api/workflows/duplicate-check", json={"form_type": "acceptance", "items": [item]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("matches")
