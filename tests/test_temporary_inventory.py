import json
import io
import threading

import app as app_module

from warehouse_suit.db import connect_db
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.temporary_inventory_service import adjust_temporary_batch


_COUNTER = [0]


def login(client, username="warehouse", password="test"):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)


def enable_temporary(client, enabled=True):
    login(client, "admin", "Costar@508")
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": enabled},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["settings"]


def seed_material(db, name="临时库测试物料", category="测试分类"):
    _COUNTER[0] += 1
    code = f"9020010001{_COUNTER[0]:04d}"
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO materials
            (material_code, name, category, unit, created_at, updated_at)
        VALUES (?, ?, ?, '个', ?, ?)
        """,
        (code, name, category, app_module.now_text(), app_module.now_text()),
    )
    db.commit()
    return cursor.lastrowid, code


def create_batch(client, material_id, quantity=5, key="temporary-create-1", **overrides):
    payload = {
        "material_id": material_id,
        "quantity": quantity,
        "unit_price": 2,
        "warehouse_type": "office",
        "received_date": "2026-07-14",
        "location": "A",
        "remark": "测试临时入库",
        "operation_key": key,
        "stock_source": "formal",
    }
    payload.update(overrides)
    return client.post("/api/temporary-inventory/batches", json=payload)


def adjust_batch(client, batch_id, quantity, key, reason="测试调整"):
    return client.post(
        f"/api/temporary-inventory/batches/{batch_id}/adjust",
        json={
            "adjustment_quantity": quantity,
            "reason": reason,
            "operation_key": key,
        },
    )


def test_feature_defaults_off_and_bootstrap_exposes_permissions(client):
    login(client)
    response = client.get("/api/temporary-inventory")
    assert response.status_code == 409
    assert response.get_json()["error"] == "临时库功能已关闭"

    bootstrap = client.get("/api/system/bootstrap").get_json()
    assert bootstrap["workflow_settings"]["temporary_inventory_enabled"] is False
    assert bootstrap["user_permissions"]["view_temporary_inventory"] is True
    assert bootstrap["user_permissions"]["manage_temporary_inventory"] is True


def test_only_admin_can_change_feature_toggle(client):
    login(client, "testuser", "test")
    response = client.post(
        "/api/system/workflow-settings",
        json={"temporary_inventory_enabled": True},
    )
    assert response.status_code == 403

    settings = enable_temporary(client, True)
    assert settings["temporary_inventory_enabled"] is True


def test_permission_enforcement_for_read_and_manage(client, db):
    enable_temporary(client)
    login(client, "testuser", "test")
    assert client.get("/api/temporary-inventory").status_code == 403
    assert create_batch(client, 999999, key="forbidden-create").status_code == 403

    material_id, _ = seed_material(db)
    login(client, "warehouse", "test")
    assert client.get("/api/temporary-inventory").status_code == 200
    assert create_batch(client, material_id, key="allowed-create").status_code == 200


def test_create_batch_forces_temporary_and_keeps_formal_inventory_unchanged(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    login(client)
    response = create_batch(client, material_id, quantity=7, key="forced-temporary")
    assert response.status_code == 200, response.get_data(as_text=True)
    batch_id = response.get_json()["batch_id"]

    cursor = db.cursor()
    batch = cursor.execute(
        "SELECT stock_source, quantity, inventory_status, version FROM material_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    record = cursor.execute(
        """
        SELECT stock_source, business_type, quantity, operator_id
        FROM stock_records WHERE operation_key = ?
        """,
        ("forced-temporary",),
    ).fetchone()
    formal = cursor.execute(
        "SELECT COALESCE(quantity, 0) FROM inventory WHERE material_id = ?",
        (material_id,),
    ).fetchone()
    assert tuple(batch) == (STOCK_SOURCE_TEMPORARY, 7, "available", 0)
    assert record["stock_source"] == STOCK_SOURCE_TEMPORARY
    assert record["business_type"] == "temporary_manual_inbound"
    assert record["quantity"] == 7
    assert record["operator_id"] is not None
    assert formal is None or float(formal[0] or 0) == 0
    assert cursor.execute("SELECT COUNT(*) FROM workflow_forms").fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'temporary_inventory.manual_inbound'"
    ).fetchone()[0] == 1


def test_create_validation_and_idempotency(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    login(client)

    assert create_batch(client, material_id, quantity=0, key="zero").status_code == 400
    assert create_batch(client, 999999, key="missing-material").status_code == 400
    assert create_batch(
        client, material_id, key="bad-warehouse", warehouse_type="temporary"
    ).status_code == 400

    first = create_batch(client, material_id, quantity=3, key="same-create")
    second = create_batch(client, material_id, quantity=3, key="same-create")
    assert first.status_code == second.status_code == 200
    assert second.get_json()["idempotent"] is True
    cursor = db.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM stock_records WHERE operation_key = 'same-create'"
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM material_batches WHERE material_id = ? AND stock_source = ?",
        (material_id, STOCK_SOURCE_TEMPORARY),
    ).fetchone()[0] == 1


def test_adjustment_is_atomic_versioned_and_idempotent(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    login(client)
    batch_id = create_batch(client, material_id, quantity=5, key="adjust-base").get_json()["batch_id"]

    plus = adjust_batch(client, batch_id, 2, "adjust-plus")
    minus = adjust_batch(client, batch_id, -4, "adjust-minus")
    duplicate = adjust_batch(client, batch_id, -4, "adjust-minus")
    insufficient = adjust_batch(client, batch_id, -10, "adjust-too-much")

    assert plus.status_code == minus.status_code == duplicate.status_code == 200
    assert duplicate.get_json()["idempotent"] is True
    assert insufficient.status_code == 400
    row = db.execute(
        "SELECT quantity, version FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    assert tuple(row) == (3, 2)
    records = db.execute(
        """
        SELECT business_type, operation_type, quantity
        FROM stock_records
        WHERE operation_key IN ('adjust-plus', 'adjust-minus')
        ORDER BY id
        """
    ).fetchall()
    assert [tuple(row) for row in records] == [
        ("temporary_manual_adjust_in", "in", 2),
        ("temporary_manual_adjust_out", "out", 4),
    ]


def test_adjust_endpoint_rejects_formal_batch(client, db):
    material_id, _ = seed_material(db)
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, created_at, updated_at)
        VALUES (?, 'FORMAL', 5, 1, 'office', '2026-07-14', ?, 'available', 0, ?, ?)
        """,
        (material_id, STOCK_SOURCE_FORMAL, app_module.now_text(), app_module.now_text()),
    )
    formal_batch_id = cursor.lastrowid
    db.commit()
    enable_temporary(client)
    login(client)

    response = adjust_batch(client, formal_batch_id, -1, "reject-formal")
    assert response.status_code == 400
    assert db.execute(
        "SELECT quantity FROM material_batches WHERE id = ?", (formal_batch_id,)
    ).fetchone()[0] == 5


def test_ledger_failure_rolls_back_temporary_adjustment(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    login(client)
    batch_id = create_batch(client, material_id, quantity=5, key="rollback-base").get_json()["batch_id"]
    db.execute(
        """
        CREATE TRIGGER fail_temporary_ledger
        BEFORE INSERT ON stock_records
        WHEN NEW.operation_key = 'rollback-adjust'
        BEGIN
            SELECT RAISE(FAIL, 'forced temporary ledger failure');
        END
        """
    )
    db.commit()

    response = adjust_batch(client, batch_id, -2, "rollback-adjust")
    assert response.status_code == 400
    row = db.execute(
        "SELECT quantity, version FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    assert tuple(row) == (5, 0)
    assert db.execute(
        "SELECT COUNT(*) FROM stock_records WHERE operation_key = 'rollback-adjust'"
    ).fetchone()[0] == 0


def test_query_filters_paging_and_zero_inventory(client, db):
    first_id, first_code = seed_material(db, "A临时物料", "分类A")
    second_id, second_code = seed_material(db, "B临时物料", "分类B")
    formal_id, _ = seed_material(db, "正式物料")
    enable_temporary(client)
    login(client)
    first_batch = create_batch(client, first_id, 2, "query-first").get_json()["batch_id"]
    create_batch(client, second_id, 1, "query-second", warehouse_type="rd")
    db.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, created_at, updated_at)
        VALUES (?, 'FORMAL-ONLY', 100, 1, 'office', '2026-07-14', ?, 'available', 0, ?, ?)
        """,
        (formal_id, STOCK_SOURCE_FORMAL, app_module.now_text(), app_module.now_text()),
    )
    db.commit()
    adjust_batch(client, first_batch, -2, "query-zero")

    default_rows = client.get("/api/temporary-inventory").get_json()
    with_zero = client.get("/api/temporary-inventory?include_zero=1&page_size=1").get_json()
    second_page = client.get("/api/temporary-inventory?include_zero=1&page_size=1&page=2").get_json()
    filtered = client.get("/api/temporary-inventory?warehouse_type=rd&q=B").get_json()

    assert [row["material_id"] for row in default_rows["items"]] == [second_id]
    assert with_zero["total"] == 2
    assert with_zero["items"][0]["material_id"] == second_id
    assert second_page["items"][0]["material_id"] == first_id
    assert [row["material_id"] for row in filtered["items"]] == [second_id]
    assert all(row["material_id"] != formal_id for row in with_zero["items"] + second_page["items"])

    batches = client.get(
        f"/api/temporary-inventory/materials/{first_id}/batches"
    ).get_json()["items"]
    assert len(batches) == 1
    assert batches[0]["stock_source"] == STOCK_SOURCE_TEMPORARY


def test_disable_preserves_data_and_reenable_restores_it(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    login(client)
    create_batch(client, material_id, 4, "preserve-data")
    before = db.execute(
        "SELECT COUNT(*), SUM(quantity) FROM material_batches WHERE stock_source = ?",
        (STOCK_SOURCE_TEMPORARY,),
    ).fetchone()

    enable_temporary(client, False)
    login(client)
    assert client.get("/api/temporary-inventory").status_code == 409
    assert create_batch(client, material_id, key="closed-write").status_code == 409
    after = db.execute(
        "SELECT COUNT(*), SUM(quantity) FROM material_batches WHERE stock_source = ?",
        (STOCK_SOURCE_TEMPORARY,),
    ).fetchone()
    assert tuple(after) == tuple(before)

    enable_temporary(client, True)
    login(client)
    assert client.get("/api/temporary-inventory").get_json()["total"] == 1


def seed_flow_visibility_data(db):
    cursor = db.cursor()
    warehouse_id = cursor.execute(
        "SELECT id FROM users WHERE username = 'warehouse'"
    ).fetchone()[0]
    material_id, _ = seed_material(db, "流程过滤物料")
    ids = {}
    for source, suffix in ((STOCK_SOURCE_FORMAL, "FORMAL"), (STOCK_SOURCE_TEMPORARY, "TEMP")):
        cursor.execute(
            """
            INSERT INTO workflow_forms
                (form_no, form_type, title, status, current_step, applicant_id, created_at, updated_at)
            VALUES (?, 'claim', ?, 'pending', 'leader_claim', ?, ?, ?)
            """,
            (f"FLOW-{suffix}", suffix, warehouse_id, app_module.now_text(), app_module.now_text()),
        )
        form_id = cursor.lastrowid
        ids[source] = form_id
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, unit, request_quantity, stock_source)
            VALUES (?, ?, ?, ?, '个', 1, ?)
            """,
            (form_id, material_id, f"M-{suffix}", suffix, source),
        )
        cursor.execute(
            """
            INSERT INTO workflow_tasks
                (form_id, step_code, assignee_id, status, created_at, updated_at)
            VALUES (?, 'leader_claim', ?, 'pending', ?, ?)
            """,
            (form_id, warehouse_id, app_module.now_text(), app_module.now_text()),
        )
        cursor.execute(
            """
            INSERT INTO notifications (user_id, title, body, data_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                warehouse_id,
                suffix,
                suffix,
                json.dumps({"form_id": form_id}, ensure_ascii=False),
                app_module.now_text(),
            ),
        )
    db.commit()
    return ids


def test_closed_feature_filters_workflows_todos_notifications_and_bootstrap(client, db):
    ids = seed_flow_visibility_data(db)
    login(client)

    workflows = client.get("/api/workflows").get_json()
    todos = client.get("/api/todos").get_json()
    notices = client.get("/api/notifications").get_json()
    bootstrap = client.get("/api/system/bootstrap").get_json()
    assert [row["id"] for row in workflows] == [ids[STOCK_SOURCE_FORMAL]]
    assert [row["form_id"] for row in todos["items"]] == [ids[STOCK_SOURCE_FORMAL]]
    assert [row["title"] for row in notices["items"]] == ["FORMAL"]
    assert [row["id"] for row in bootstrap["recent_forms"]] == [ids[STOCK_SOURCE_FORMAL]]
    assert bootstrap["stats"]["active_forms"] == 1
    assert bootstrap["unread_notifications"] == 1

    enable_temporary(client, True)
    login(client)
    assert {row["id"] for row in client.get("/api/workflows").get_json()} == set(ids.values())
    assert {row["form_id"] for row in client.get("/api/todos").get_json()["items"]} == set(ids.values())
    assert {row["title"] for row in client.get("/api/notifications").get_json()["items"]} == {"FORMAL", "TEMP"}


def test_concurrent_temporary_reductions_do_not_go_negative(app, db):
    material_id, _ = seed_material(db)
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, created_at, updated_at)
        VALUES (?, 'CONCURRENT', 5, 1, 'office', '2026-07-14', ?, 'available', 0, ?, ?)
        """,
        (material_id, STOCK_SOURCE_TEMPORARY, app_module.now_text(), app_module.now_text()),
    )
    batch_id = cursor.lastrowid
    warehouse = dict(
        cursor.execute("SELECT * FROM users WHERE username = 'warehouse'").fetchone()
    )
    db.commit()
    barrier = threading.Barrier(2)
    results = []

    def worker(index):
        conn = connect_db(app_module.DB_PATH)
        try:
            barrier.wait()
            result = adjust_temporary_batch(
                conn.cursor(),
                batch_id,
                {
                    "adjustment_quantity": -4,
                    "reason": "并发测试",
                    "operation_key": f"concurrent-adjust-{index}",
                },
                warehouse,
            )
            conn.commit()
            results.append(("ok", result))
        except Exception as exc:
            conn.rollback()
            results.append(("error", str(exc)))
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [kind for kind, _ in results].count("ok") == 1
    assert [kind for kind, _ in results].count("error") == 1
    row = db.execute(
        "SELECT quantity, version FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    assert tuple(row) == (1, 1)


def test_view_only_permission_cannot_manage(client, db):
    material_id, _ = seed_material(db)
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('role_permissions', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (
            json.dumps(
                {
                    "user": {
                        "view_temporary_inventory": True,
                        "manage_temporary_inventory": False,
                    }
                }
            ),
        ),
    )
    db.commit()
    enable_temporary(client)
    login(client, "testuser", "test")

    assert client.get("/api/temporary-inventory").status_code == 200
    assert create_batch(client, material_id, key="view-only-create").status_code == 403


def test_origin_type_and_structured_notification_are_filtered(client, db):
    cursor = db.cursor()
    warehouse_id = cursor.execute(
        "SELECT id FROM users WHERE username = 'warehouse'"
    ).fetchone()[0]
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id,
             origin_type, created_at, updated_at)
        VALUES ('TEMP-ORIGIN', 'acceptance', '临时来源', 'pending', 'acceptance', ?,
                'temporary_transfer', ?, ?)
        """,
        (warehouse_id, app_module.now_text(), app_module.now_text()),
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO notifications (user_id, title, body, data_json, created_at)
        VALUES (?, '临时结构通知', '临时结构通知', ?, ?)
        """,
        (
            warehouse_id,
            json.dumps({"stock_source": "temporary", "business_type": "temporary_manual_inbound"}),
            app_module.now_text(),
        ),
    )
    db.commit()
    login(client)

    assert form_id not in {row["id"] for row in client.get("/api/workflows").get_json()}
    assert "临时结构通知" not in {
        row["title"] for row in client.get("/api/notifications").get_json()["items"]
    }

    enable_temporary(client)
    login(client)
    assert form_id in {row["id"] for row in client.get("/api/workflows").get_json()}
    assert "临时结构通知" in {
        row["title"] for row in client.get("/api/notifications").get_json()["items"]
    }


def test_frontend_assets_include_temporary_inventory_module(client):
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "css/temporary-inventory.css" in html
    assert "js/temporary-inventory.js" in html

    system_js = client.get("/static/system.js").get_data(as_text=True)
    module_js = client.get("/static/js/temporary-inventory.js").get_data(as_text=True)
    assert '"temporaryInventory", "临时库"' in system_js
    assert "setTemporaryInventoryEnabled" in system_js
    assert "manage_temporary_inventory" in system_js
    assert "/api/temporary-inventory" in module_js


def test_temporary_batch_uploads_are_returned_with_batch_details(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    login(client)
    batch_response = create_batch(client, material_id, key="temporary-attachment-batch")
    assert batch_response.status_code == 200, batch_response.get_data(as_text=True)
    batch_id = batch_response.get_json()["batch_id"]

    upload_response = client.post(
        "/api/material-attachments",
        data={
            "material_id": str(material_id),
            "material_batch_id": str(batch_id),
            "attachment_type": "material_photo",
            "files": (io.BytesIO(b"temporary photo"), "temporary-photo.png"),
        },
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200, upload_response.get_data(as_text=True)
    attachment = upload_response.get_json()["attachments"][0]

    rows_response = client.get(f"/api/temporary-inventory/materials/{material_id}/batches")
    assert rows_response.status_code == 200
    batches = rows_response.get_json()["items"]
    assert batches[0]["id"] == batch_id
    assert batches[0]["attachments"][0]["id"] == attachment["id"]
    assert batches[0]["attachments"][0]["is_material_photo"] is True

    login(client, "admin", "Costar@508")
    settings_response = client.post(
        "/api/system/workflow-settings",
        json={
            "temporary_inventory_material_photo_required": True,
            "temporary_inventory_document_required": True,
        },
    )
    assert settings_response.status_code == 200
    settings = settings_response.get_json()["settings"]
    assert settings["temporary_inventory_material_photo_required"] is True
    assert settings["temporary_inventory_document_required"] is True
