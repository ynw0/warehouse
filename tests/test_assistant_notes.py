"""个人事项和到货提醒的关键业务链路。"""

import json

import pytest


def _login(client, username, password):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["user"]


def _account(db, username):
    from warehouse_suit.workflow_service import _user_by_id
    row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return _user_by_id(db.cursor(), row["id"])


def _acceptance(db, user):
    material_id = db.execute(
        "INSERT INTO materials (material_code, name, unit) VALUES ('10103901010060', '电池', '粒')"
    ).lastrowid
    form_id = db.execute(
        "INSERT INTO workflow_forms (form_no, form_type, status, current_step, applicant_id) VALUES ('YS2026110601', 'acceptance', 'inbound', 'inbound', ?)",
        (user["id"],),
    ).lastrowid
    item_id = db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, purchase_project, data_json)
        VALUES (?, ?, '10103901010060', '电池', '办公', ?)
        """,
        (form_id, material_id, json.dumps({"inbound_done": False, "usage_kind": "研发用料"}, ensure_ascii=False)),
    ).lastrowid
    db.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date, stock_source, inventory_status, purchase_project, usage_kind)
        VALUES (?, 'TEST', 3, 1, 'office', '2026-11-06', 'formal', 'available', '办公', '研发用料')
        """,
        (material_id,),
    )
    db.commit()
    return material_id, form_id, item_id


def test_personal_note_completion_and_recipient_notification(client, db):
    from warehouse_suit.assistant.notes import save_note, list_notes, evaluate_material_notes, complete_note

    _login(client, "admin", "admin")
    owner = _account(db, "admin")
    recipient = _account(db, "warehouse")
    material_id, form_id, item_id = _acceptance(db, owner)
    note = save_note(db, owner, {
        "kind": "material_wait", "content": "仓管提前取走两粒电池", "quantity": 2,
        "recipient_user_id": recipient["id"],
    })
    assert note["status"] == "incomplete"
    note = save_note(db, owner, {"note_id": note["id"], "acceptance_item_id": item_id})
    assert note["status"] == "watching"
    assert list_notes(db.cursor(), recipient) == []
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE category = 'assistant'").fetchone()[0] == 0

    db.execute("UPDATE workflow_items SET data_json = ? WHERE id = ?", (
        json.dumps({"inbound_done": True, "usage_kind": "研发用料"}, ensure_ascii=False), item_id,
    ))
    evaluate_material_notes(db.cursor(), material_id)
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE category = 'assistant'").fetchone()[0] == 2
    db.rollback()
    assert db.execute("SELECT status FROM assistant_notes WHERE id = ?", (note["id"],)).fetchone()[0] == "watching"
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE category = 'assistant'").fetchone()[0] == 0

    db.execute("UPDATE workflow_items SET data_json = ? WHERE id = ?", (
        json.dumps({"inbound_done": True, "usage_kind": "研发用料"}, ensure_ascii=False), item_id,
    ))
    evaluate_material_notes(db.cursor(), material_id)
    evaluate_material_notes(db.cursor(), material_id)
    db.commit()
    rows = db.execute("SELECT user_id FROM notifications WHERE category = 'assistant' ORDER BY user_id").fetchall()
    assert {row["user_id"] for row in rows} == {owner["id"], recipient["id"]}
    with pytest.raises(ValueError):
        complete_note(db, recipient, note["id"])
    complete_note(db, owner, note["id"])
    assert list_notes(db.cursor(), owner) == []


def test_note_with_missing_details_can_be_updated_only_by_owner(client, db):
    from warehouse_suit.assistant.notes import save_note

    _login(client, "admin", "admin")
    owner = _account(db, "admin")
    other = _account(db, "warehouse")
    _, _, item_id = _acceptance(db, owner)
    note = save_note(db, owner, {"kind": "material_wait", "content": "提前取了两粒电池"})
    with pytest.raises(ValueError):
        save_note(db, other, {"note_id": note["id"], "acceptance_item_id": item_id, "quantity": 2})
    updated = save_note(db, owner, {"note_id": note["id"], "acceptance_item_id": item_id, "quantity": 2})
    assert updated["status"] == "watching"
    assert updated["content"] == note["content"]


def test_desktop_login_does_not_revoke_existing_api_tokens(client, db):
    user = _login(client, "warehouse", "test")
    before = db.execute(
        "SELECT COUNT(*) FROM api_access_tokens WHERE user_id = ? AND revoked = 0", (user["id"],),
    ).fetchone()[0]
    response = client.post("/api/assistant/desktop-login", json={"username": "warehouse", "password": "test"})
    assert response.status_code == 200
    after = db.execute(
        "SELECT COUNT(*) FROM api_access_tokens WHERE user_id = ? AND revoked = 0", (user["id"],),
    ).fetchone()[0]
    assert before == after
    assert client.post("/api/assistant/desktop-logout").status_code == 200
    assert db.execute(
        "SELECT COUNT(*) FROM api_access_tokens WHERE user_id = ? AND revoked = 0", (user["id"],),
    ).fetchone()[0] == before
