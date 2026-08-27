# -*- coding: utf-8 -*-

import app as app_module
import pytest

from tests.test_claim_workflow import _create_material_with_stock, _login, _pending_steps
from warehouse_suit.todo_service import pending_tasks_for_user
from warehouse_suit.workflow_service import create_workflow_tasks


def _create_role_user(db, username, display_name, role_code):
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, display_name, department, password, updated_at)
        VALUES (?, ?, '', ?, ?)
        """,
        (
            username,
            display_name,
            app_module.generate_password_hash("test"),
            app_module.now_text(),
        ),
    )
    user_id = cursor.lastrowid
    cursor.execute("SELECT id FROM roles WHERE code = ?", (role_code,))
    role_id = cursor.fetchone()["id"]
    cursor.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (user_id, role_id),
    )
    db.commit()
    return user_id


def test_workflow_transition_closes_stale_tasks_and_rejects_duplicates(db):
    cursor = db.cursor()
    warehouse_id = cursor.execute(
        "SELECT id FROM users WHERE username = 'warehouse'"
    ).fetchone()["id"]
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, created_at, updated_at)
        VALUES ('TASK-INTEGRITY-1', 'claim', '任务一致性', 'leader_claim', 'leader_claim', ?, ?, ?)
        """,
        (warehouse_id, app_module.now_text(), app_module.now_text()),
    )
    form_id = cursor.lastrowid
    create_workflow_tasks(cursor, form_id, "claim", "leader_claim", [warehouse_id])
    db.commit()

    with pytest.raises(ValueError, match="已存在相同办理人的待办"):
        create_workflow_tasks(cursor, form_id, "claim", "leader_claim", [warehouse_id])

    cursor.execute(
        "UPDATE workflow_forms SET status = 'completed', current_step = 'completed' WHERE id = ?",
        (form_id,),
    )
    db.commit()

    task = cursor.execute(
        "SELECT status, decision FROM workflow_tasks WHERE form_id = ?",
        (form_id,),
    ).fetchone()
    assert tuple(task) == ("completed", "流程已流转自动关闭")
    todos = pending_tasks_for_user(
        cursor,
        {"id": warehouse_id, "role_codes": ["warehouse", "user"]},
    )
    assert form_id not in {item["form_id"] for item in todos}


def test_multi_leader_claim_waits_for_all_and_closes_tasks_on_rejection(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material_with_stock(client)
    admin_id = _login(client, "admin", "admin")["id"]
    second_leader_id = _create_role_user(db, "second_leader", "第二审批领导", "leader")

    _login(client, "warehouse", "test")
    response = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "leader_ids": [admin_id, second_leader_id],
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 2}],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    form = response.get_json()["form"]
    assert _pending_steps(db, form["id"]) == [
        {"step_code": "leader_claim", "assignee_id": admin_id},
        {"step_code": "leader_claim", "assignee_id": second_leader_id},
    ]

    _login(client, "admin", "admin")
    response = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse["id"]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["form"]["status"] == "leader_claim"
    assert _pending_steps(db, form["id"]) == [
        {"step_code": "leader_claim", "assignee_id": second_leader_id}
    ]

    _login(client, "second_leader", "test")
    response = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse["id"]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["form"]["status"] == "outbound"
    assert _pending_steps(db, form["id"]) == [
        {"step_code": "outbound", "assignee_id": warehouse["id"]}
    ]

    _login(client, "warehouse", "test")
    response = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "leader_ids": [admin_id, second_leader_id],
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 1}],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    rejected_form = response.get_json()["form"]

    _login(client, "admin", "admin")
    response = client.post(
        f"/api/claims/{rejected_form['id']}/leader",
        json={"decision": "不同意", "remark": "退回修改"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["form"]["status"] == "applicant_revision"
    assert _pending_steps(db, rejected_form["id"]) == [
        {"step_code": "applicant_revision", "assignee_id": warehouse["id"]}
    ]
