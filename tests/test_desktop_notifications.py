from warehouse_suit.notifications import create_notification


def _login(client, username="admin", password="Costar@508"):
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
    )
    db.commit()

    response = client.get("/api/todos")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["notifications"]
    assert payload["notifications"][0]["title"] == "物料入库通知"
    assert payload["notifications"][0]["body"] == "有新的物料入库了，请按需领取。"
