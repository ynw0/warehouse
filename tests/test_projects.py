# -*- coding: utf-8 -*-
"""项目代号修改、大小写去重，以及停用后删除。"""

import json

import app as app_module
from warehouse_suit.settings import set_setting, workflow_settings


def _login(client):
    resp = client.post("/api/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _codes(client):
    resp = client.get("/api/projects?scope=all")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return {row["code"]: row for row in resp.get_json()["items"]}


def test_renaming_project_code_updates_the_same_project(client, db):
    _login(client)
    created = client.post("/api/projects", json={"code": "zg2516", "active": True})
    assert created.status_code == 200, created.get_data(as_text=True)
    project_id = created.get_json()["id"]
    cursor = db.cursor()
    settings = workflow_settings(cursor)
    settings["project_codes"] = ["zg2516"]
    set_setting(cursor, "workflow_settings", json.dumps(settings, ensure_ascii=False))
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, unit, purchase_project, created_at, updated_at)
        VALUES ('10200921990001', '代号物料', '个', 'zg2516', ?, ?)
        """,
        (app_module.now_text(), app_module.now_text()),
    )
    material_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, purchase_project, created_at, updated_at)
        VALUES (?, 'B1', 1, 1, 'office', '2026-10-12', 'formal', 'available', 0, 'zg2516', ?, ?)
        """,
        (material_id, app_module.now_text(), app_module.now_text()),
    )
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('CS1', 'claim', '代号', 'completed', 'completed', 1, ?, ?, ?)
        """,
        (json.dumps({"purchase_project": "zg2516", "project_code": "zg2516"}), app_module.now_text(), app_module.now_text()),
    )
    db.commit()

    duplicate = client.post("/api/projects", json={"code": "ZG2516", "active": True})
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.get_json()["error"]

    renamed = client.post(
        f"/api/projects/{project_id}",
        json={"code": "ZG2516", "active": True, "member_ids": []},
    )
    assert renamed.status_code == 200, renamed.get_data(as_text=True)
    codes = _codes(client)
    assert "ZG2516" in codes
    assert "zg2516" not in codes
    assert db.execute(
        "SELECT purchase_project FROM material_batches WHERE material_id = ?",
        (material_id,),
    ).fetchone()[0] == "ZG2516"
    form_data = json.loads(db.execute("SELECT data_json FROM workflow_forms WHERE form_no = 'CS1'").fetchone()[0])
    assert form_data["purchase_project"] == "ZG2516"
    assert form_data["project_code"] == "ZG2516"
    assert workflow_settings(db.cursor()).get("project_codes") == ["ZG2516"]


def test_only_inactive_project_can_be_deleted(client, db):
    _login(client)
    created = client.post("/api/projects", json={"code": "ZG2516", "active": True})
    assert created.status_code == 200, created.get_data(as_text=True)
    project_id = created.get_json()["id"]
    cursor = db.cursor()
    settings = workflow_settings(cursor)
    settings["project_codes"] = ["ZG2516"]
    set_setting(cursor, "workflow_settings", json.dumps(settings, ensure_ascii=False))
    db.commit()

    blocked = client.delete(f"/api/projects/{project_id}")
    assert blocked.status_code == 400
    assert "只有停用的项目可以删除" in blocked.get_json()["error"]

    disabled = client.post(f"/api/projects/{project_id}", json={"code": "ZG2516", "active": False})
    assert disabled.status_code == 200, disabled.get_data(as_text=True)
    removed = client.delete(f"/api/projects/{project_id}")
    assert removed.status_code == 200, removed.get_data(as_text=True)
    assert "ZG2516" not in _codes(client)
    assert "ZG2516" not in (workflow_settings(db.cursor()).get("project_codes") or [])
