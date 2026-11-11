# -*- coding: utf-8 -*-


def _login(client, username, password):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["user"]


def _create_material_with_stock(client):
    resp = client.post(
        "/api/material-master",
        json={"material_code": "10200100018888", "name": "申领退回测试物料", "unit": "个"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    material = resp.get_json()["material"]
    resp = client.post("/api/stock/in", json={"material_id": material["id"], "quantity": 10, "total_amount": 10, "purchase_project": "办公"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return material


def _pending_steps(db, form_id):
    cursor = db.cursor()
    cursor.execute(
        "SELECT step_code, assignee_id FROM workflow_tasks WHERE form_id = ? AND status = 'pending' ORDER BY id",
        (form_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def test_claim_rejection_uses_applicant_revision_and_resubmits_to_leader(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material_with_stock(client)

    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 3, "purchase_project": "办公"}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    item_id = form["items"][0]["id"]

    _login(client, "admin", "admin")
    resp = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "不同意", "remark": "数量请修改"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rejected = resp.get_json()["form"]
    assert rejected["status"] == "applicant_revision"
    assert rejected["current_step"] == "applicant_revision"

    pending = _pending_steps(db, form["id"])
    assert pending == [{"step_code": "applicant_revision", "assignee_id": warehouse["id"]}]

    _login(client, "warehouse", "test")
    resp = client.put(
        f"/api/workflows/{form['id']}",
        json={"title": rejected["title"], "items": [{"id": item_id, "request_quantity": 2}]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.post(f"/api/workflows/{form['id']}/resubmit-returned", json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resubmitted = resp.get_json()["form"]
    assert resubmitted["status"] == "leader_claim"
    assert resubmitted["current_step"] == "leader_claim"

    pending = _pending_steps(db, form["id"])
    assert pending == [{"step_code": "leader_claim", "assignee_id": admin_id}]

    cursor = db.cursor()
    cursor.execute(
        "SELECT decision FROM workflow_tasks WHERE form_id = ? AND step_code = 'applicant_revision' AND status = 'completed'",
        (form["id"],),
    )
    assert cursor.fetchone()["decision"] == "已重新提交"


def test_finished_claim_issues_two_serials_in_one_workflow(client, db):
    warehouse = _login(client, "warehouse", "test")
    leader = _login(client, "admin", "admin")
    db.execute("""
        INSERT INTO finished_acceptances (acceptance_no, product_name, acceptance_date, project_code)
        VALUES ('CP-TEST', '测试成品', '2026-11-11', '办公')
    """)
    acceptance_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.executemany("""
        INSERT INTO finished_good_inventory (acceptance_id, product_name, quantity, serial_no, acceptance_date)
        VALUES (?, '测试成品', 1, ?, '2026-11-11')
    """, [(acceptance_id, "CP-TEST-1"), (acceptance_id, "CP-TEST-2")])
    db.commit()
    _login(client, "warehouse", "test")
    items = client.get("/api/finished-claims/items?keyword=CP-TEST").get_json()["items"]
    assert len(items) == 2
    payload = {"project_code": "办公", "leader_id": leader["id"], "items": items}
    response = client.post("/api/finished-claims", json=payload)
    assert response.status_code == 200, response.get_json()
    form = response.get_json()["form"]
    assert form["form_type"] == "finished_claim"
    assert len(form["items"]) == 2
    assert not client.get("/api/finished-claims/items?keyword=CP-TEST").get_json()["items"]

    _login(client, "admin", "admin")
    endpoint = f"/api/finished-claims/{form['id']}"
    approval = client.post(endpoint + "/leader", json={"decision": "同意", "warehouse_user_id": warehouse["id"]})
    assert approval.status_code == 200, approval.get_json()
    _login(client, "warehouse", "test")
    outbound = client.post(endpoint + "/outbound", json={})
    assert outbound.status_code == 200, outbound.get_json()
    assert outbound.get_json()["form"]["status"] == "completed"
    assert [row[0] for row in db.execute("SELECT claimed_quantity FROM finished_good_inventory")] == [1, 1]
