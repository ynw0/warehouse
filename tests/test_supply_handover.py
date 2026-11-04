# -*- coding: utf-8 -*-
"""供货交接：打印回执、上传凭证、领导查阅后进入外部待结清。"""

import io
import json


def _login(client, username, password):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["user"]


def _create_material(client, code, quantity=5):
    resp = client.post(
        "/api/material-master",
        json={"material_code": code, "name": "交接测试物料", "unit": "个", "brand_model": "A1", "spec": "10mm"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    material = resp.get_json()["material"]
    resp = client.post(
        "/api/stock/in",
        json={"material_id": material["id"], "quantity": quantity, "total_amount": quantity, "purchase_project": "办公"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return material


def _supply_payload(material, phone="13900000000", goods_status="样机", quantity=1):
    return {
        "recipient": {"company": "接收公司", "name": "收货人", "phone": "13800000000", "address": "测试路 1 号"},
        "supply_method": "试用",
        "contact_phone": phone,
        "expected_close_date": "2026-12-31",
        "reason": "交接测试",
        "items": [{
            "item_type": "material",
            "item_ref_id": material["id"],
            "quantity": quantity,
            "goods_status": goods_status,
        }],
    }


def _batch_and_item(db, form_id):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM supply_orders WHERE form_id = ?", (form_id,))
    order_id = int(cursor.fetchone()["id"])
    cursor.execute("SELECT id, item_ref_id FROM supply_items WHERE order_id = ?", (order_id,))
    item = cursor.fetchone()
    cursor.execute(
        "SELECT id, quantity FROM material_batches WHERE material_id = ? ORDER BY id DESC LIMIT 1",
        (item["item_ref_id"],),
    )
    batch = cursor.fetchone()
    return int(item["id"]), int(batch["id"]), float(batch["quantity"] or 0)


def _ship(client, form_id, supply_item_id, batch_id, quantity=1):
    return client.post(
        f"/api/supplies/{form_id}/shipments",
        json={
            "items": [{
                "supply_item_id": supply_item_id,
                "quantity": quantity,
                "allocations": [{"batch_id": batch_id, "quantity": quantity}],
            }],
        },
    )


def test_supply_requires_phone_and_goods_status(client):
    warehouse = _login(client, "warehouse", "test")
    assert warehouse["display_name"] == "仓库管理员"
    material = _create_material(client, "10200100018881")
    payload = _supply_payload(material)
    payload.pop("contact_phone")
    resp = client.post("/api/supplies", json=payload)
    assert resp.status_code == 400
    assert "申请人电话" in resp.get_json()["error"]

    payload = _supply_payload(material, goods_status="试用")
    resp = client.post("/api/supplies", json=payload)
    assert resp.status_code == 400
    assert "货物状态" in resp.get_json()["error"]


def test_handover_review_enters_external_open(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material(client, "10200100018882")
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")
    payload = _supply_payload(material)
    payload["leader_id"] = admin_id
    resp = client.post("/api/supplies", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form = resp.get_json()["form"]
    form_id = form["id"]
    assert form["data"]["handover_required"] == 1
    assert form["data"]["contact_phone"] == "13900000000"
    assert form["items"][0]["data"]["goods_status"] == "样机"

    _login(client, "admin", "admin")
    resp = client.post(f"/api/supplies/{form_id}/leader", json={"decision": "同意", "warehouse_user_id": warehouse["id"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "supply_handover_print"

    _login(client, "warehouse", "test")
    supply_item_id, batch_id, _quantity = _batch_and_item(db, form_id)
    resp = _ship(client, form_id, supply_item_id, batch_id)
    assert resp.status_code == 400

    resp = client.post(f"/api/supplies/{form_id}/handover-print", json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "supply_outbound"

    cursor = db.cursor()
    cursor.execute("UPDATE supply_orders SET handover_printed = 0 WHERE form_id = ?", (form_id,))
    db.commit()
    resp = _ship(client, form_id, supply_item_id, batch_id)
    assert resp.status_code == 400
    assert "打印" in resp.get_json()["error"]
    cursor.execute("UPDATE supply_orders SET handover_printed = 1 WHERE form_id = ?", (form_id,))
    db.commit()

    resp = client.get(f"/api/supplies/{form_id}/handover-receipt")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    receipt = resp.get_json()
    assert receipt["provider_company"] == "中光学(杭州)智能光电科技有限公司"
    assert receipt["provider_contact"] == "仓库管理员"
    assert receipt["provider_phone"] == "13900000000"
    assert receipt["recipient_company"] == "接收公司"
    assert receipt["items"][0]["name"] == "交接测试物料、A1、10mm"
    assert receipt["items"][0]["goods_status"] == "样机"
    assert receipt["items"][0]["quantity"] == "1"
    assert receipt["items"][0]["remark"] == ""

    resp = _ship(client, form_id, supply_item_id, batch_id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "supply_voucher_upload"

    resp = client.post(
        f"/api/supplies/{form_id}/handover-vouchers",
        data={"files": (io.BytesIO(b"not-a-voucher"), "note.txt")},
    )
    assert resp.status_code == 400
    assert "PDF" in resp.get_json()["error"]

    resp = client.post(
        f"/api/supplies/{form_id}/handover-vouchers",
        data={"files": (io.BytesIO(b"%PDF-1.4\n%handover\n"), "receipt.pdf")},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "supply_voucher_review"
    cursor.execute("SELECT assignee_id FROM workflow_tasks WHERE form_id = ? AND step_code = 'supply_voucher_review' AND status = 'pending'", (form_id,))
    assert int(cursor.fetchone()["assignee_id"]) == admin_id

    _login(client, "admin", "admin")
    resp = client.post(f"/api/supplies/{form_id}/voucher-review", json={"decision": "同意"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "external_open"
    cursor.execute("SELECT status FROM supply_orders WHERE form_id = ?", (form_id,))
    assert cursor.fetchone()["status"] == "external_open"


def test_voucher_reject_does_not_restore_stock(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material(client, "10200100018883", quantity=5)
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")
    payload = _supply_payload(material, goods_status="备件")
    payload["leader_id"] = admin_id
    resp = client.post("/api/supplies", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form_id = resp.get_json()["form"]["id"]

    _login(client, "admin", "admin")
    resp = client.post(f"/api/supplies/{form_id}/leader", json={"decision": "同意", "warehouse_user_id": warehouse["id"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _login(client, "warehouse", "test")
    resp = client.post(f"/api/supplies/{form_id}/handover-print", json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    supply_item_id, batch_id, _quantity = _batch_and_item(db, form_id)
    resp = _ship(client, form_id, supply_item_id, batch_id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    cursor = db.cursor()
    cursor.execute("SELECT quantity FROM material_batches WHERE id = ?", (batch_id,))
    shipped_quantity = float(cursor.fetchone()["quantity"] or 0)
    assert shipped_quantity == 4

    resp = client.post(
        f"/api/supplies/{form_id}/handover-vouchers",
        data={"files": (io.BytesIO(b"%PDF-1.4\n%handover\n"), "receipt.pdf")},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _login(client, "admin", "admin")
    resp = client.post(f"/api/supplies/{form_id}/voucher-review", json={"decision": "退回"})
    assert resp.status_code == 400
    resp = client.post(f"/api/supplies/{form_id}/voucher-review", json={"decision": "退回", "remark": "签字不清"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "supply_voucher_upload"
    cursor.execute("SELECT quantity FROM material_batches WHERE id = ?", (batch_id,))
    assert float(cursor.fetchone()["quantity"] or 0) == shipped_quantity
    cursor.execute("SELECT status FROM supply_orders WHERE form_id = ?", (form_id,))
    assert cursor.fetchone()["status"] == "voucher"


def test_old_supply_skips_handover_steps(client, db):
    warehouse = _login(client, "warehouse", "test")
    material = _create_material(client, "10200100018884")
    admin_id = _login(client, "admin", "admin")["id"]
    _login(client, "warehouse", "test")
    payload = _supply_payload(material, goods_status="产品")
    payload["leader_id"] = admin_id
    resp = client.post("/api/supplies", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    form_id = resp.get_json()["form"]["id"]
    cursor = db.cursor()
    cursor.execute("SELECT data_json FROM workflow_forms WHERE id = ?", (form_id,))
    data = json.loads(cursor.fetchone()["data_json"])
    data["handover_required"] = 0
    cursor.execute(
        "UPDATE workflow_forms SET data_json = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), form_id),
    )
    cursor.execute("UPDATE supply_orders SET handover_required = 0 WHERE form_id = ?", (form_id,))
    db.commit()

    _login(client, "admin", "admin")
    resp = client.post(f"/api/supplies/{form_id}/leader", json={"decision": "同意", "warehouse_user_id": warehouse["id"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "supply_outbound"

    _login(client, "warehouse", "test")
    supply_item_id, batch_id, _quantity = _batch_and_item(db, form_id)
    resp = _ship(client, form_id, supply_item_id, batch_id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["form"]["status"] == "external_open"
