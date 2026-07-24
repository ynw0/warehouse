import pytest

import app as app_module
from warehouse_suit.inventory_service import parse_batch_allocations


def _login_admin(client):
    resp = client.post("/api/login", json={"username": "admin", "password": "Costar@508"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _create_material(client, code="10200100019999"):
    resp = client.post(
        "/api/material-master",
        json={"material_code": code, "name": "校验测试物料", "unit": "个"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["material"]


def _create_material_with_stock(client, code="10200100017777", quantity=10):
    resp = client.post(
        "/api/material-master",
        json={"material_code": code, "name": "库存快照物料", "unit": "个", "quantity": 0},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    material = resp.get_json()["material"]
    resp = client.post("/api/stock/in", json={"material_id": material["id"], "quantity": quantity, "unit_price": 1})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["material"]


def test_material_master_rejects_invalid_code(client):
    _login_admin(client)
    resp = client.post("/api/material-master", json={"material_code": "BAD-CODE", "name": "坏编码"})
    assert resp.status_code == 400
    assert "14 位数字" in resp.get_json()["error"]


def test_data_validation_settings_defaults(client):
    _login_admin(client)
    resp = client.get("/api/system/data-validation")
    assert resp.status_code == 200
    settings = resp.get_json()
    assert settings["enabled"] is True
    assert settings["project_code"]["max_length"] == 50
    assert settings["batch_no"]["required"] is True
    assert settings["serial_no"]["unique_in_database"] is True


def test_data_validation_can_disable_material_code_rule(client):
    _login_admin(client)
    resp = client.post(
        "/api/system/data-validation",
        json={"enabled": True, "material_code": {"enabled": False}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = client.post("/api/material-master", json={"material_code": "BAD-CODE", "name": "宽松编码", "unit": "个"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_data_validation_project_code_rule_is_configurable(client):
    _login_admin(client)
    resp = client.post(
        "/api/system/data-validation",
        json={"enabled": True, "project_code": {"enabled": True, "max_length": 3, "allow_control_chars": False}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["settings"]["project_code"]["max_length"] == 3
    with client.application.test_request_context("/"):
        with pytest.raises(ValueError, match="项目号长度不能超过 3 个字符"):
            app_module.validate_project_code("ABCD")


def test_workflow_stock_quantity_is_locked_to_form_snapshot(client):
    _login_admin(client)
    material = _create_material_with_stock(client)
    resp = client.post(
        "/api/claims",
        json={
            "purpose": "办公",
            "items": [{"material_id": material["id"], "request_quantity": 2}],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    item = form["items"][0]
    assert item["stock_quantity"] == 10
    assert item["current_stock_quantity"] == 10
    assert item["data"]["stock_quantity_snapshot"] == 10

    resp = client.post("/api/stock/out", json={"material_id": material["id"], "quantity": 4})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.get(f"/api/workflows/{form['id']}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    item = resp.get_json()["items"][0]
    assert item["stock_quantity"] == 10
    assert item["current_stock_quantity"] == 6
    assert item["stock_quantity_locked"] is True


def test_material_master_rejects_negative_initial_quantity(client):
    _login_admin(client)
    resp = client.post(
        "/api/material-master",
        json={"material_code": "10200100018888", "name": "负库存", "quantity": -1},
    )
    assert resp.status_code == 400
    assert "初始库存" in resp.get_json()["error"]


def test_stock_change_rejects_invalid_number(client):
    _login_admin(client)
    material = _create_material(client)
    resp = client.post("/api/stock/in", json={"material_id": material["id"], "quantity": "nan", "unit_price": 1})
    assert resp.status_code == 400
    assert "有效数字" in resp.get_json()["error"]


def test_stock_in_rejects_negative_price(client):
    _login_admin(client)
    material = _create_material(client)
    resp = client.post("/api/stock/in", json={"material_id": material["id"], "quantity": 1, "unit_price": -1})
    assert resp.status_code == 400
    assert "入库单价" in resp.get_json()["error"]


def test_serial_count_rejects_invalid_number(client):
    _login_admin(client)
    resp = client.get("/api/production/finished-serials?product_name=校验成品&count=abc")
    assert resp.status_code == 400
    assert "生成数量" in resp.get_json()["error"]


def test_batch_allocations_allow_unused_zero_rows():
    allocations = parse_batch_allocations([
        {"batch_id": 1, "quantity": 2},
        {"batch_id": 2, "quantity": 0},
        {"batch_id": 3, "quantity": ""},
    ])
    assert allocations == [{"batch_id": 1, "quantity": 2.0}]


def test_batch_allocations_still_reject_negative_rows():
    with pytest.raises(ValueError):
        parse_batch_allocations([{"batch_id": 1, "quantity": -1}])
