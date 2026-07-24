import io
import json

import warehouse_suit.attachments as attachment_service
from warehouse_suit.settings import set_setting, workflow_settings


def _login(client, username="admin", password="Costar@508"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200


def _user_id(db, username):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    return cursor.fetchone()["id"]


def _set_acceptance_attachment_required(db, *, photo=True, document=True):
    cursor = db.cursor()
    settings = workflow_settings(cursor)
    settings["acceptance_material_photo_required"] = photo
    settings["acceptance_document_required"] = document
    set_setting(cursor, "workflow_settings", json.dumps(settings, ensure_ascii=False))
    db.commit()


def _inspect_payload(item, quantity=2):
    return {
        "items": [
            {
                "id": item["id"],
                "qualified_quantity": quantity,
                "unqualified_quantity": 0,
                "package_ok_quantity": quantity,
                "appearance_ok_quantity": quantity,
                "name_spec_ok_quantity": quantity,
                "usage_ok_quantity": quantity,
            }
        ],
        "decision": "\u540c\u610f",
    }


def _create_acceptance(client, material_code, material_name, validator_id):
    response = client.post(
        "/api/acceptance",
        json={
            "items": [
                {
                    "material_code": material_code,
                    "material_name": material_name,
                    "brand_model": "M1",
                    "spec": "S1",
                    "purchase_quantity": 2,
                    "arrival_quantity": 2,
                    "unit_price": 3,
                }
            ],
            "validator_ids": [validator_id],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"]


def _upload_attachment(client, form, item, material_id, attachment_type, filename, content):
    response = client.post(
        "/api/material-attachments",
        data={
            "material_id": str(material_id),
            "workflow_form_id": str(form["id"]),
            "workflow_item_id": str(item["id"]),
            "attachment_type": attachment_type,
            "files": (io.BytesIO(content), filename),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["attachments"][0]


def _upload_batch_attachment(client, material_id, batch_id, attachment_type, filename, content):
    response = client.post(
        "/api/material-attachments",
        data={
            "material_id": str(material_id),
            "material_batch_id": str(batch_id),
            "attachment_type": attachment_type,
            "files": (io.BytesIO(content), filename),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["attachments"][0]


def _leader_approve_acceptance(client, form_id, warehouse_id):
    _login(client, "admin", "Costar@508")
    response = client.post(
        f"/api/acceptance/{form_id}/leader",
        json={"decision": "同意", "warehouse_user_id": warehouse_id},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"]


def _inbound_acceptance(client, form_id):
    _login(client, "warehouse", "test")
    response = client.post(f"/api/acceptance/{form_id}/inbound", json={"items": []})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["form"]


def _create_material_master(client, material_code, material_name):
    response = client.post(
        "/api/material-master",
        json={"material_code": material_code, "name": material_name, "unit": "个"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["material"]


def test_acceptance_inspection_attachments_required_for_warehouse_and_can_be_deleted(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_service, "DATA_DIR", tmp_path)
    _set_acceptance_attachment_required(db)
    _login(client, "warehouse", "test")

    warehouse_id = _user_id(db, "warehouse")
    form = _create_acceptance(client, "10200101010010", "\u6d4b\u8bd5\u9a8c\u6536\u9644\u4ef6\u7269\u6599", warehouse_id)
    item = form["items"][0]
    material_id = item["material_id"]

    missing_resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item))
    assert missing_resp.status_code == 400
    assert "\u8bf7\u4e0a\u4f20\u7269\u6599\u7167\u7247" in missing_resp.get_json()["error"]

    photo = _upload_attachment(client, form, item, material_id, "material_photo", "photo.jpg", b"photo-bytes")
    assert photo["attachment_type"] == "material_photo"

    delete_photo_resp = client.delete(f"/api/material-attachments/{photo['id']}")
    assert delete_photo_resp.status_code == 200, delete_photo_resp.get_data(as_text=True)

    missing_after_delete_resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item))
    assert missing_after_delete_resp.status_code == 400
    assert "\u8bf7\u4e0a\u4f20\u7269\u6599\u7167\u7247" in missing_after_delete_resp.get_json()["error"]

    photo = _upload_attachment(client, form, item, material_id, "material_photo", "photo.jpg", b"photo-bytes")

    missing_doc_resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item))
    assert missing_doc_resp.status_code == 400
    assert "\u8bf7\u4e0a\u4f20\u8d44\u6599" in missing_doc_resp.get_json()["error"]

    document = _upload_attachment(client, form, item, material_id, "document", "invoice.pdf", b"invoice-bytes")
    assert document["attachment_type"] == "document"

    inspect_resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item))
    assert inspect_resp.status_code == 200, inspect_resp.get_data(as_text=True)

    detail_resp = client.get(f"/api/materials/{material_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.get_json()
    attachments = detail["attachments"]
    assert [row["attachment_type"] for row in attachments] == ["material_photo", "document"]
    assert attachments[0]["is_material_photo"] is True
    assert attachments[0]["original_name"] == "photo.jpg"
    assert attachments[1]["original_name"] == "invoice.pdf"

    download_resp = client.get(attachments[1]["download_url"])
    assert download_resp.status_code == 200
    assert download_resp.data == b"invoice-bytes"

    delete_document_resp = client.delete(f"/api/material-attachments/{document['id']}")
    assert delete_document_resp.status_code == 200, delete_document_resp.get_data(as_text=True)
    detail_after_delete = client.get(f"/api/materials/{material_id}").get_json()
    assert [row["attachment_type"] for row in detail_after_delete["attachments"]] == ["material_photo"]
    deleted_download_resp = client.get(document["download_url"])
    assert deleted_download_resp.status_code == 404


def test_required_acceptance_attachments_do_not_block_non_warehouse_inspector(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_service, "DATA_DIR", tmp_path)
    _set_acceptance_attachment_required(db)
    _login(client, "warehouse", "test")

    test_user_id = _user_id(db, "testuser")
    form = _create_acceptance(client, "10200101010011", "\u666e\u901a\u9a8c\u6536\u4eba\u9644\u4ef6\u975e\u5fc5\u586b\u7269\u6599", test_user_id)
    item = form["items"][0]

    _login(client, "testuser", "test")
    inspect_resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item))
    assert inspect_resp.status_code == 200, inspect_resp.get_data(as_text=True)


def test_acceptance_attachments_bind_to_inbound_batch_and_are_permanent(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_service, "DATA_DIR", tmp_path)
    _set_acceptance_attachment_required(db)
    _login(client, "warehouse", "test")

    warehouse_id = _user_id(db, "warehouse")
    form = _create_acceptance(client, "10200101010012", "批次附件永久留存物料", warehouse_id)
    item = form["items"][0]
    material_id = item["material_id"]
    photo = _upload_attachment(client, form, item, material_id, "material_photo", "batch-photo.jpg", b"photo-bytes")
    document = _upload_attachment(client, form, item, material_id, "document", "batch-doc.pdf", b"doc-bytes")

    inspect_resp = client.post(f"/api/acceptance/{form['id']}/inspect", json=_inspect_payload(item))
    assert inspect_resp.status_code == 200, inspect_resp.get_data(as_text=True)

    detail_before = client.get(f"/api/materials/{material_id}").get_json()
    assert {row["id"] for row in detail_before["unbound_attachments"]} == {photo["id"], document["id"]}

    _leader_approve_acceptance(client, form["id"], warehouse_id)
    _inbound_acceptance(client, form["id"])

    detail_after = client.get(f"/api/materials/{material_id}").get_json()
    batches = [batch for batch in detail_after["batches"] if float(batch["quantity"] or 0) > 0]
    assert len(batches) == 1
    batch = batches[0]
    batch_attachment_ids = {row["id"] for row in batch["attachments"]}
    assert batch_attachment_ids == {photo["id"], document["id"]}
    assert {row["material_batch_id"] for row in batch["attachments"]} == {batch["id"]}
    assert detail_after["material_photo_url"].endswith(f"/{photo['id']}/download")

    _login(client, "testuser", "test")
    denied_resp = client.delete(f"/api/material-attachments/{photo['id']}")
    assert denied_resp.status_code == 403

    _login(client, "warehouse", "test")
    download_resp = client.get(photo["download_url"])
    assert download_resp.status_code == 200
    assert download_resp.data == b"photo-bytes"

    delete_resp = client.delete(f"/api/material-attachments/{photo['id']}")
    assert delete_resp.status_code == 200, delete_resp.get_data(as_text=True)
    deleted_download_resp = client.get(photo["download_url"])
    assert deleted_download_resp.status_code == 404


def test_direct_batch_attachment_upload_is_listed_and_used_by_bootstrap(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_service, "DATA_DIR", tmp_path)
    _login(client, "warehouse", "test")

    material = _create_material_master(client, "10200101010013", "直接批次附件物料")
    stock_resp = client.post("/api/stock/in", json={"material_id": material["id"], "quantity": 5, "unit_price": 2})
    assert stock_resp.status_code == 200, stock_resp.get_data(as_text=True)
    stock_material = stock_resp.get_json()["material"]
    batch = stock_material["batches"][0]
    photo = _upload_batch_attachment(client, material["id"], batch["id"], "material_photo", "direct-photo.jpg", b"direct-photo")

    detail = client.get(f"/api/materials/{material['id']}").get_json()
    assert detail["batches"][0]["attachments"][0]["id"] == photo["id"]
    assert detail["material_photo_url"].endswith(f"/{photo['id']}/download")

    bootstrap = client.get("/api/bootstrap").get_json()
    boot_material = next(row for row in bootstrap["materials"] if row["id"] == material["id"])
    assert boot_material["material_photo_url"].endswith(f"/{photo['id']}/download")

    delete_resp = client.delete(f"/api/material-attachments/{photo['id']}")
    assert delete_resp.status_code == 200, delete_resp.get_data(as_text=True)
