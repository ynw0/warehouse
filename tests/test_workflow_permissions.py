# -*- coding: utf-8 -*-

import json

import app as app_module

from tests.test_claim_workflow import _create_material_with_stock, _login, _pending_steps
from warehouse_suit.permissions import role_permissions
from warehouse_suit.settings import set_setting


def test_outbound_view_permission_allows_detail_but_not_unassigned_handling(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material_with_stock(client)
    admin_id = _login(client, "admin", "admin")["id"]

    _login(client, "warehouse", "test")
    response = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 3, "purchase_project": "办公"}],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    form = response.get_json()["form"]

    _login(client, "admin", "admin")
    response = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse["id"]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["form"]["status"] == "outbound"

    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, display_name, department, password, updated_at)
        VALUES ('permission_buyer', '权限测试采购员', '采购部', ?, ?)
        """,
        (app_module.generate_password_hash("test"), app_module.now_text()),
    )
    buyer_id = cursor.lastrowid
    cursor.execute("SELECT id FROM roles WHERE code = 'buyer'")
    buyer_role_id = cursor.fetchone()["id"]
    cursor.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (buyer_id, buyer_role_id),
    )
    db.commit()

    _login(client, "permission_buyer", "test")
    response = client.get(f"/api/workflows/{form['id']}")
    assert response.status_code == 403

    permissions = role_permissions(cursor)
    permissions["buyer"]["view_outbound"] = True
    set_setting(cursor, "role_permissions", json.dumps(permissions, ensure_ascii=False))
    db.commit()

    response = client.get("/api/workflows?type=claim&status=outbound")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert form["id"] in {row["id"] for row in response.get_json()}

    response = client.get(f"/api/workflows/{form['id']}")
    assert response.status_code == 200, response.get_data(as_text=True)

    response = client.post(
        f"/api/claims/{form['id']}/outbound",
        json={"items": [], "signature": "权限测试采购员", "operation_key": "permission-check"},
    )
    assert response.status_code == 400
    assert "没有该待办的办理权限" in response.get_json()["error"]

    cursor.execute(
        "SELECT status, current_step FROM workflow_forms WHERE id = ?",
        (form["id"],),
    )
    assert tuple(cursor.fetchone()) == ("outbound", "outbound")
    assert _pending_steps(db, form["id"]) == [
        {"step_code": "outbound", "assignee_id": warehouse["id"]}
    ]


def test_admin_can_reassign_current_pending_task_to_enabled_step_assignee(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material_with_stock(client)
    admin_id = _login(client, "admin", "admin")["id"]
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, display_name, department, password, updated_at)
        VALUES ('warehouse_reassigned', '改派库管', '仓储部', ?, ?)
        """,
        (app_module.generate_password_hash("test"), app_module.now_text()),
    )
    replacement_id = cursor.lastrowid
    warehouse_role_id = cursor.execute(
        "SELECT id FROM roles WHERE code = 'warehouse'"
    ).fetchone()["id"]
    cursor.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (replacement_id, warehouse_role_id),
    )
    db.commit()

    _login(client, "warehouse", "test")
    response = client.post(
        "/api/claims",
        json={
            "leader_id": admin_id,
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 2, "purchase_project": "办公"}],
        },
    )
    form = response.get_json()["form"]
    _login(client, "admin", "admin")
    response = client.post(
        f"/api/claims/{form['id']}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse["id"]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    task = next(
        item
        for item in response.get_json()["form"]["tasks"]
        if item["step_code"] == "outbound" and item["status"] == "pending"
    )

    _login(client, "warehouse", "test")
    denied = client.post(
        f"/api/workflows/{form['id']}/tasks/{task['id']}/reassign",
        json={"target_user_id": replacement_id},
    )
    assert denied.status_code == 403

    _login(client, "admin", "admin")
    reassigned = client.post(
        f"/api/workflows/{form['id']}/tasks/{task['id']}/reassign",
        json={"target_user_id": replacement_id},
    )
    assert reassigned.status_code == 200, reassigned.get_data(as_text=True)
    assert _pending_steps(db, form["id"]) == [
        {"step_code": "outbound", "assignee_id": replacement_id}
    ]
