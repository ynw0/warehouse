from warehouse_suit.notifications import create_notification


def _login(client, username="admin", password="admin"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.get_json()["user"]


def test_todos_payload_includes_unread_system_notifications(client, db):
    user = _login(client)
    cursor = db.cursor()
    create_notification(
        cursor,
        user["id"],
        "物料入库通知",
        "有新的物料入库了，请按需领取。",
        {"material_id": 123},
        category="inbound",
    )
    db.commit()

    response = client.get("/api/todos")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["notifications"]
    assert payload["notifications"][0]["title"] == "物料入库通知"
    assert payload["notifications"][0]["body"] == "有新的物料入库了，请按需领取。"
    assert payload["notifications"][0]["category"] == "inbound"


def test_notifications_filter_by_category(client, db):
    user = _login(client)
    cursor = db.cursor()
    create_notification(cursor, user["id"], "物料入库通知", "入库", {"material_id": 1}, category="inbound")
    create_notification(cursor, user["id"], "转借请求", "转借", {"borrow_record_id": 1}, category="transfer")
    db.commit()
    all_rows = client.get("/api/notifications?read=0").get_json()["items"]
    assert len(all_rows) == 2
    inbound = client.get("/api/notifications?read=0&category=inbound").get_json()["items"]
    assert len(inbound) == 1
    assert inbound[0]["category"] == "inbound"
    bad = client.get("/api/notifications?read=0&category=nope")
    assert bad.status_code == 400
