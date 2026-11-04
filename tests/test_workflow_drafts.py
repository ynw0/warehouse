# -*- coding: utf-8 -*-


def _login(client, username, password):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["user"]


def _create_material_with_stock(client, quantity=10):
    resp = client.post(
        "/api/material-master",
        json={"material_code": "10200100019991", "name": "草稿提交测试物料", "unit": "个"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    material = resp.get_json()["material"]
    resp = client.post(
        "/api/stock/in",
        json={"material_id": material["id"], "quantity": quantity, "total_amount": quantity, "purchase_project": "办公"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return material


def _draft_count(db, draft_id):
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM workflow_drafts WHERE id = ?", (draft_id,))
    return int(cursor.fetchone()["n"])


def test_save_incomplete_claim_does_not_create_workflow(client, db):
    warehouse = _login(client, "warehouse", "test")
    resp = client.post(
        "/api/drafts",
        json={
            "form_type": "claim",
            "payload": {"title": "未完成申领", "items": [{"material_name": "螺丝"}]},
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft = resp.get_json()["draft"]
    assert draft["form_type"] == "claim"
    assert draft["title"] == "未完成申领"
    assert draft["applicant_id"] == warehouse["id"]
    assert draft["payload"]["items"][0]["material_name"] == "螺丝"

    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM workflow_forms")
    assert int(cursor.fetchone()["n"]) == 0
    cursor.execute("SELECT COUNT(*) AS n FROM workflow_tasks")
    assert int(cursor.fetchone()["n"]) == 0
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(workflow_drafts)")}
    assert "form_no" not in columns


def test_draft_list_is_isolated_by_user(client):
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/drafts",
        json={"form_type": "claim", "payload": {"title": "仓库草稿"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    warehouse_draft_id = resp.get_json()["draft"]["id"]

    _login(client, "testuser", "test")
    resp = client.post(
        "/api/drafts",
        json={"form_type": "claim", "payload": {"title": "用户草稿"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    user_draft_id = resp.get_json()["draft"]["id"]

    resp = client.get("/api/drafts")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    ids = [row["id"] for row in resp.get_json()["drafts"]]
    assert user_draft_id in ids
    assert warehouse_draft_id not in ids

    _login(client, "warehouse", "test")
    resp = client.get("/api/drafts")
    ids = [row["id"] for row in resp.get_json()["drafts"]]
    assert warehouse_draft_id in ids
    assert user_draft_id not in ids


def test_submit_draft_creates_tasks_and_deletes_draft(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material_with_stock(client)
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")

    resp = client.post(
        "/api/drafts",
        json={
            "form_type": "claim",
            "payload": {
                "title": "完整申领草稿",
                "leader_id": admin_id,
                "purpose": "办公",
                "items": [{"material_id": material["id"], "request_quantity": 2, "purchase_project": "办公"}],
            },
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft_id = resp.get_json()["draft"]["id"]

    resp = client.post(f"/api/drafts/{draft_id}/submit")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    assert form["form_type"] == "claim"
    assert form["applicant_id"] == warehouse["id"]

    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS n FROM workflow_tasks WHERE form_id = ?",
        (form["id"],),
    )
    assert int(cursor.fetchone()["n"]) > 0
    assert _draft_count(db, draft_id) == 0


def test_submit_insufficient_stock_keeps_draft(client, db):
    _login(client, "warehouse", "test")
    material = _create_material_with_stock(client, quantity=1)
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")

    resp = client.post(
        "/api/drafts",
        json={
            "form_type": "claim",
            "payload": {
                "title": "超量申领草稿",
                "leader_id": admin_id,
                "purpose": "办公",
                "items": [{"material_id": material["id"], "request_quantity": 99, "purchase_project": "办公"}],
            },
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft_id = resp.get_json()["draft"]["id"]

    resp = client.post(f"/api/drafts/{draft_id}/submit")
    assert resp.status_code == 400
    assert _draft_count(db, draft_id) == 1


def test_other_user_cannot_access_draft(client):
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/drafts",
        json={"form_type": "claim", "payload": {"title": "他人不可见"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft_id = resp.get_json()["draft"]["id"]

    _login(client, "testuser", "test")
    resp = client.get(f"/api/drafts/{draft_id}")
    assert resp.status_code == 403
    resp = client.put(
        f"/api/drafts/{draft_id}",
        json={"form_type": "claim", "payload": {"title": "篡改"}},
    )
    assert resp.status_code == 403
    resp = client.post(f"/api/drafts/{draft_id}/submit")
    assert resp.status_code == 403
    resp = client.delete(f"/api/drafts/{draft_id}")
    assert resp.status_code == 403


def test_save_incomplete_supply_does_not_create_workflow(client, db):
    warehouse = _login(client, "warehouse", "test")
    resp = client.post(
        "/api/drafts",
        json={
            "form_type": "supply",
            "payload": {
                "recipient": {"company": "未完成供货公司"},
                "items": [{"item_name": "样机"}],
            },
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft = resp.get_json()["draft"]
    assert draft["form_type"] == "supply"
    assert draft["title"] == "未完成供货公司"
    assert draft["applicant_id"] == warehouse["id"]
    assert draft["payload"]["items"][0]["item_name"] == "样机"

    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM workflow_forms")
    assert int(cursor.fetchone()["n"]) == 0
    cursor.execute("SELECT COUNT(*) AS n FROM workflow_tasks")
    assert int(cursor.fetchone()["n"]) == 0


def test_submit_supply_draft_creates_tasks_and_deletes_draft(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material_with_stock(client)
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")

    resp = client.post(
        "/api/drafts",
        json={
            "form_type": "supply",
            "payload": {
                "recipient": {
                    "company": "完整供货公司",
                    "name": "收货人",
                    "phone": "13800000000",
                    "address": "测试地址",
                },
                "expected_close_date": "2026-12-31",
                "reason": "测试供货",
                "contact_phone": "13700000000",
                "leader_id": admin_id,
                "items": [
                    {
                        "item_type": "material",
                        "item_ref_id": material["id"],
                        "quantity": 1,
                        "goods_status": "产品",
                        "item_name": material.get("name") or "草稿提交测试物料",
                    }
                ],
            },
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft_id = resp.get_json()["draft"]["id"]

    resp = client.post(f"/api/drafts/{draft_id}/submit")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    assert form["form_type"] == "supply"
    assert form["applicant_id"] == warehouse["id"]

    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS n FROM workflow_tasks WHERE form_id = ?",
        (form["id"],),
    )
    assert int(cursor.fetchone()["n"]) > 0
    assert _draft_count(db, draft_id) == 0


def _claim_available(client, material_id):
    resp = client.get("/api/claims/materials?keyword=10200100019991")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rows = [row for row in resp.get_json() if int(row["id"]) == int(material_id)]
    assert rows
    return sum(float(row["total_available_quantity"]) for row in rows)


def test_saved_claim_draft_does_not_reduce_available_stock(client):
    _login(client, "warehouse", "test")
    material = _create_material_with_stock(client, quantity=10)
    before = _claim_available(client, material["id"])
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")
    resp = client.post(
        "/api/drafts",
        json={
            "form_type": "claim",
            "payload": {
                "title": "不占库存的草稿",
                "leader_id": admin_id,
                "purpose": "办公",
                "items": [{"material_id": material["id"], "request_quantity": 4, "purchase_project": "办公"}],
            },
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    draft_id = resp.get_json()["draft"]["id"]
    assert _claim_available(client, material["id"]) == before

    resp = client.post(f"/api/drafts/{draft_id}/submit")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _claim_available(client, material["id"]) == before - 4


def test_draft_status_form_does_not_hold_stock(client, db):
    _login(client, "warehouse", "test")
    material = _create_material_with_stock(client, quantity=10)
    before = _claim_available(client, material["id"])
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('CL-DRAFT', 'claim', '旧草稿', 'draft', 'draft', 1, '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
        """
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, request_quantity, purchase_project, data_json)
        VALUES (?, ?, ?, '草稿提交测试物料', 4, '办公', '{"usage_kind":"研发用料"}')
        """,
        (form_id, material["id"], material["material_code"]),
    )
    db.commit()
    assert _claim_available(client, material["id"]) == before
    cursor.execute(
        "UPDATE workflow_forms SET status = 'leader_claim', current_step = 'leader_claim' WHERE id = ?",
        (form_id,),
    )
    db.commit()
    assert _claim_available(client, material["id"]) == before - 4


def test_draft_status_production_does_not_hold_issued_material(db):
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('BY-DRAFT', 'semifinished', '草稿验收', 'draft', 'draft', 1, '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
        """
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_code, material_name, request_quantity, arrival_quantity, data_json)
        VALUES (?, 'BY-DRAFT', '半成品', 1, 1, '{"material_components":[{"material_id":1,"per_unit_quantity":1}]}')
        """,
        (form_id,),
    )
    db.commit()
    from warehouse_suit.production_service import bind_production_material_hold

    bind_production_material_hold(cursor, form_id)
    if cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'claim_consumptions'").fetchone():
        cursor.execute("SELECT COUNT(*) AS n FROM claim_consumptions")
        assert int(cursor.fetchone()["n"]) == 0
