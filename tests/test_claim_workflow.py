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
    resp = client.post("/api/stock/in", json={"material_id": material["id"], "quantity": 10, "unit_price": 1})
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

    admin_id = _login(client, "admin", "Costar@508")["id"]
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "items": [{"material_id": material["id"], "request_quantity": 3}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    item_id = form["items"][0]["id"]

    _login(client, "admin", "Costar@508")
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
