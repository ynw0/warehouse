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


def test_viewing_selected_notifications_persists_read_state_after_login(client, db):
    user = _login(client)
    other = db.execute("SELECT id FROM users WHERE username='warehouse'").fetchone()["id"]
    cursor = db.cursor()
    for user_id, content in [(user["id"], "已查看"), (user["id"], "未查看"), (other, "其他账号")]:
        create_notification(cursor, user_id, "事项提醒", content, category="assistant")
    # 按通知正文读取编号，避免使用事件表的 lastrowid。
    ids = [db.execute("SELECT id FROM notifications WHERE body=?", (body,)).fetchone()["id"]
           for body in ["已查看", "未查看", "其他账号"]]
    db.commit()

    response = client.post("/api/notifications/read", json={"ids": [ids[0], ids[2], ids[0]]})
    assert response.status_code == 200
    assert response.get_json()["updated"] == 1
    assert response.get_json()["unread_count"] == 1
    assert db.execute("SELECT is_read FROM notifications WHERE id=?", (ids[2],)).fetchone()[0] == 0
    first_read_at = db.execute("SELECT read_at FROM notifications WHERE id=?", (ids[0],)).fetchone()[0]
    assert first_read_at
    assert client.post("/api/notifications/read", json={"ids": [ids[0]]}).get_json()["updated"] == 0
    assert db.execute("SELECT read_at FROM notifications WHERE id=?", (ids[0],)).fetchone()[0] == first_read_at

    client.post("/api/logout")
    _login(client)
    assert [row["id"] for row in client.get("/api/todos").get_json()["notifications"]] == [ids[1]]
    history = client.get("/api/notifications?read=1&category=assistant").get_json()["items"]
    assert [row["id"] for row in history] == [ids[0]]


def test_selected_notification_read_requires_login_and_valid_ids(client):
    assert client.post("/api/notifications/read", json={"ids": [1]}).status_code == 401
    _login(client)
    for ids in [[], [True], [-1], ["1"], "1"]:
        assert client.post("/api/notifications/read", json={"ids": ids}).status_code == 400
