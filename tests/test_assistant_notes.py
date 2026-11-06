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


def test_immediate_note_sends_to_owner_and_recipient_once(client, db):
    from warehouse_suit.assistant.notes import save_note
    _login(client, "admin", "admin")
    owner = _account(db, "admin")
    recipient = _account(db, "warehouse")
    note = save_note(db, owner, {
        "content": "你好", "reminder_mode": "immediate", "recipient_user_id": recipient["id"],
    }, source_message_id=10001)
    assert note["status"] == "notified"
    assert note["notified_at"] is not None
    save_note(db, owner, {
        "content": "你好", "reminder_mode": "immediate", "recipient_user_id": recipient["id"],
    }, source_message_id=10001)
    rows = db.execute("SELECT user_id FROM notifications WHERE category='assistant'").fetchall()
    assert len(rows) == 2
    assert {row["user_id"] for row in rows} == {owner["id"], recipient["id"]}


def test_scheduled_note_keeps_due_time_and_sends_only_when_due(client, db):
    from warehouse_suit.assistant.notes import save_note, deliver_due_notes
    _login(client, "admin", "admin")
    owner = _account(db, "admin")
    note = save_note(db, owner, {
        "content": "阿松大数的胡", "reminder_mode": "at_time", "delay_seconds": 10,
    })
    assert note["status"] == "watching"
    assert note["remind_at"] is not None
    assert note["notified_at"] is None
    deliver_due_notes(db.cursor())
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE category='assistant'").fetchone()[0] == 0
    db.execute("UPDATE assistant_notes SET remind_at='2000-01-01 00:00:00' WHERE id=?", (note["id"],))
    deliver_due_notes(db.cursor())
    deliver_due_notes(db.cursor())
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE category='assistant'").fetchone()[0] == 1
    stored = db.execute("SELECT notified_at,status FROM assistant_notes WHERE id=?", (note["id"],)).fetchone()
    assert stored["notified_at"] is not None
    assert stored["status"] == "notified"


def test_recorded_note_can_be_sent_immediately_without_material(client, db):
    from warehouse_suit.assistant.notes import save_note
    _login(client, "admin", "admin")
    owner = _account(db, "admin")
    recorded = save_note(db, owner, {"content": "你好"})
    sent = save_note(db, owner, {"note_id": recorded["id"], "reminder_mode": "immediate"})
    assert sent["status"] == "notified"
    assert sent["notified_at"] is not None
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE category='assistant'").fetchone()[0] == 1


def test_scheduler_delivers_ten_second_reminder(client, db):
    import threading
    import time
    import app as app_module
    from warehouse_suit.db import connect_db
    from warehouse_suit.assistant.notes import save_note
    from warehouse_suit.assistant.reminder_scheduler import _run

    _login(client, "admin", "admin")
    owner = _account(db, "admin")
    note = save_note(db, owner, {"content": "十秒提醒", "reminder_mode": "at_time", "delay_seconds": 10})
    path = app_module.DB_PATH
    stop = threading.Event()
    worker = threading.Thread(target=_run, args=(lambda: connect_db(path), lambda: True, stop), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = db.execute("SELECT status,notified_at FROM assistant_notes WHERE id=?", (note["id"],)).fetchone()
            if row["status"] == "notified":
                assert row["notified_at"] is not None
                assert db.execute("SELECT COUNT(*) FROM notifications WHERE category='assistant'").fetchone()[0] == 1
                return
            time.sleep(0.2)
        pytest.fail("十秒定时提醒未在等待窗口内发送")
    finally:
        stop.set()
        worker.join(timeout=5)
