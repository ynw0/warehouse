import json
import io
import threading

import app as app_module

from warehouse_suit.db import connect_db
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.inventory_service import add_inventory_batch
from warehouse_suit.issued_material_service import list_issued_lots
from warehouse_suit.migrations.v2026083101_batch_warehouse_alignment import upgrade as align_batch_warehouses
from warehouse_suit.temporary_inventory_service import adjust_temporary_batch


_COUNTER = [0]


def login(client, username="warehouse", password="test"):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)


def enable_temporary(client, enabled=True):
    login(client, "admin", "admin")
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
        "total_amount": quantity * 2,
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


def test_promote_only_selected_transferable_batches(client, db):
    material_id, _ = seed_material(db)
    enable_temporary(client)
    cursor = db.cursor()
    first_id = add_inventory_batch(
        cursor, material_id, 3, 2, {}, "TEMP-FIRST",
        stock_source=STOCK_SOURCE_TEMPORARY, operation_key="selective-first",
    )
    second_id = add_inventory_batch(
        cursor, material_id, 5, 2, {}, "TEMP-SECOND",
        stock_source=STOCK_SOURCE_TEMPORARY, operation_key="selective-second",
    )
    db.commit()
    login(client)

    batches = client.get(
        f"/api/temporary-inventory/materials/{material_id}/batches"
    ).get_json()["items"]
    selected = next(batch for batch in batches if batch["id"] == first_id)
    response = client.post(
        f"/api/temporary-inventory/materials/{material_id}/promote",
        json={"batches": [{"id": first_id, "version": selected["version"]}]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["converted_batches"] == 1
    rows = db.execute(
        "SELECT id, stock_source, quantity FROM material_batches WHERE id IN (?, ?) ORDER BY id",
        (first_id, second_id),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (first_id, STOCK_SOURCE_FORMAL, 3),
        (second_id, STOCK_SOURCE_TEMPORARY, 5),
    ]
    assert db.execute(
        "SELECT quantity FROM inventory WHERE material_id=?", (material_id,)
    ).fetchone()[0] == 3
    sources = db.execute(
        "SELECT operation_key, stock_source FROM stock_records WHERE material_id=? ORDER BY id",
        (material_id,),
    ).fetchall()
    assert [tuple(row) for row in sources] == [
        ("selective-first", STOCK_SOURCE_FORMAL),
        ("selective-second", STOCK_SOURCE_TEMPORARY),
    ]


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
    filtered = client.get("/api/temporary-inventory?material_warehouse_code=20&q=B").get_json()

    assert [row["material_id"] for row in default_rows["items"]] == [second_id]
    assert with_zero["total"] == 2
    assert with_zero["pages"] == 2
    assert with_zero["items"][0]["material_id"] == second_id
    assert second_page["items"][0]["material_id"] == first_id
    assert [row["material_id"] for row in filtered["items"]] == [second_id]
    assert all(row["material_id"] != formal_id for row in with_zero["items"] + second_page["items"])

    batches = client.get(
        f"/api/temporary-inventory/materials/{first_id}/batches"
    ).get_json()["items"]
    assert len(batches) == 1
    assert batches[0]["stock_source"] == STOCK_SOURCE_TEMPORARY


def test_temporary_batch_warehouse_type_follows_shelf(client, db):
    material_id, _ = seed_material(db, "研发临时物料")
    shelf = db.execute(
        "SELECT id FROM shelves WHERE warehouse_type = 'rd' ORDER BY id LIMIT 1"
    ).fetchone()
    assert shelf
    batch_id = add_inventory_batch(
        db.cursor(),
        material_id,
        3,
        2,
        {"shelf_id": shelf["id"], "layer_number": 1, "zone_name": "A"},
        "TEMP-RD-SHELF",
        stock_source=STOCK_SOURCE_TEMPORARY,
        operation_key="temporary-rd-shelf",
    )
    db.commit()
    assert db.execute(
        "SELECT warehouse_type FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()["warehouse_type"] == "rd"

    db.execute(
        "UPDATE material_batches SET warehouse_type = 'office' WHERE id = ?", (batch_id,)
    )
    db.commit()
    enable_temporary(client)
    login(client)
    rows = client.get("/api/temporary-inventory?q=研发临时物料").get_json()["items"]
    assert rows[0]["warehouse_type"] == "rd"
    batches = client.get(
        f"/api/temporary-inventory/materials/{material_id}/batches"
    ).get_json()["items"]
    assert batches[0]["warehouse_type"] == "rd"

    align_batch_warehouses(db)
    db.commit()
    assert db.execute(
        "SELECT warehouse_type FROM material_batches WHERE id = ?", (batch_id,)
    ).fetchone()["warehouse_type"] == "rd"


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
                'temporary_inventory', ?, ?)
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
    assert '"temporaryInventory", "临时库管理"' in system_js
    assert "setTemporaryInventoryEnabled" in system_js
    assert "manage_temporary_inventory" in system_js
    assert "/api/temporary-inventory" in module_js
    assert "是否拆分申领单，把临时库物料拆出来？" in module_js
    assert "async function fetchAllRows(query)" in module_js
    assert "items.push(...(data.items || []))" in module_js
    assert "pageSize: 20" in module_js
    assert "requestPageSize: 100" in module_js
    assert "temporaryNext" in module_js
    assert "runMaterialQuery();\n  }\n\n  function renderStats" in system_js


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

    login(client, "admin", "admin")
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


def test_acceptance_promote_moves_claim_dates_and_leaves_borrow(client, db):
    """转正后借用流水也随方案 A 对齐正式库与转正日。"""
    material_id, code = seed_material(db)
    enable_temporary(client)
    user_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    now = app_module.now_text()
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('YS-TEMP-1', 'acceptance', '无订单验收', 'completed', 'completed', ?, ?, '2026-01-01 08:00:00', '2026-01-01 08:00:00')
        """,
        (user_id, json.dumps({"has_purchase_order": False}, ensure_ascii=False)),
    )
    form_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, created_at, updated_at)
        VALUES (?, 'TEMP-PROMOTE', 2, 1, 'office', '2026-01-02', 'temporary', 'available', 0, ?, ?)
        """,
        (material_id, now, now),
    )
    batch_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, qualified_quantity, stock_source, data_json)
        VALUES (?, ?, ?, '临时库测试物料', '个', 2, 'temporary', ?)
        """,
        (form_id, material_id, code, json.dumps({
            "inbound_done": True,
            "inbound_batch_id": batch_id,
            "inbound_batches": [{"batch_id": batch_id, "quantity": 2}],
        })),
    )
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, operation_type, quantity, balance_after, operation_date,
             stock_source, business_type, operation_key, created_at)
        VALUES (?, ?, 'in', 2, 2, '2026-01-02', 'temporary', 'temporary_acceptance_inbound', ?, '2026-01-02 09:00:00')
        """,
        (material_id, batch_id, f"acceptance:{form_id}:1:inbound"),
    )
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, operation_type, quantity, balance_after, operation_date,
             stock_source, business_type, operation_key, created_at)
        VALUES (?, ?, 'out', 1, 1, '2026-01-03', 'temporary', 'borrow_outbound', 'borrow:1:out', '2026-01-03 09:00:00')
        """,
        (material_id, batch_id),
    )
    db.commit()
    login(client)
    response = client.post(f"/api/temporary-inventory/acceptance-forms/{form_id}/promote", json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    batch = db.execute(
        "SELECT stock_source, received_date, batch_no FROM material_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    assert batch["stock_source"] == STOCK_SOURCE_FORMAL
    assert batch["received_date"] == app_module.today_text()
    assert batch["batch_no"].startswith(app_module.today_text().replace("-", ""))
    borrow = db.execute(
        "SELECT stock_source, operation_date FROM stock_records WHERE business_type = 'borrow_outbound'"
    ).fetchone()
    assert borrow["stock_source"] == STOCK_SOURCE_FORMAL
    assert borrow["operation_date"] == app_module.today_text()
    inbound = db.execute(
        "SELECT stock_source, operation_date, business_type FROM stock_records WHERE operation_type = 'in'"
    ).fetchone()
    assert inbound["stock_source"] == STOCK_SOURCE_FORMAL
    assert inbound["operation_date"] == app_module.today_text()
    assert inbound["business_type"] == "manual"
    stored = db.execute("SELECT created_at, data_json FROM workflow_forms WHERE id = ?", (form_id,)).fetchone()
    assert stored["created_at"] != "2026-01-01 08:00:00"
    assert json.loads(stored["data_json"])["promoted_to_formal"] is True
    again = client.post(f"/api/temporary-inventory/acceptance-forms/{form_id}/promote", json={})
    assert again.status_code == 400
    assert "已转移" in again.get_json()["error"]


def test_acceptance_promote_renumbers_form_and_gap_fills_same_day(client, db):
    """转正日迁号，原日同前缀后续单号向前补位。"""
    material_id, code = seed_material(db)
    enable_temporary(client)
    user_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    now = app_module.now_text()
    today8 = app_module.today_text().replace("-", "")
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES
            ('YS2026090909', 'acceptance', '仓管YS2026090909', 'completed', 'completed', ?, ?, '2026-09-09 08:00:00', '2026-09-09 08:00:00'),
            ('YS2026090910', 'acceptance', '仓管YS2026090910', 'completed', 'completed', ?, ?, '2026-09-09 09:00:00', '2026-09-09 09:00:00'),
            ('YS2026090911', 'acceptance', '仓管YS2026090911', 'completed', 'completed', ?, ?, '2026-09-09 10:00:00', '2026-09-09 10:00:00')
        """,
        (
            user_id, json.dumps({"has_purchase_order": False}, ensure_ascii=False),
            user_id, json.dumps({"has_purchase_order": True}, ensure_ascii=False),
            user_id, json.dumps({"has_purchase_order": True}, ensure_ascii=False),
        ),
    )
    form_id = db.execute("SELECT id FROM workflow_forms WHERE form_no = 'YS2026090909'").fetchone()[0]
    other_10 = db.execute("SELECT id FROM workflow_forms WHERE form_no = 'YS2026090910'").fetchone()[0]
    other_11 = db.execute("SELECT id FROM workflow_forms WHERE form_no = 'YS2026090911'").fetchone()[0]
    db.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, source_form_no, created_at, updated_at)
        VALUES (?, '20260909TEMP', 2, 1, 'office', '2026-09-09', 'temporary', 'available', 0, 'RK2026090901', ?, ?)
        """,
        (material_id, now, now),
    )
    batch_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, qualified_quantity, stock_source, data_json)
        VALUES (?, ?, ?, '临时库测试物料', '个', 2, 'temporary', ?)
        """,
        (form_id, material_id, code, json.dumps({
            "inbound_done": True,
            "inbound_batch_id": batch_id,
            "inbound_no": "RK2026090901",
            "inbound_batches": [{"batch_id": batch_id, "quantity": 2, "inbound_no": "RK2026090901"}],
        })),
    )
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, operation_type, quantity, balance_after, operation_date,
             stock_source, business_type, form_no, operation_key, created_at)
        VALUES (?, ?, 'in', 2, 2, '2026-09-09', 'temporary', 'temporary_acceptance_inbound', 'RK2026090901', ?, ?)
        """,
        (material_id, batch_id, f"acceptance:{form_id}:1:inbound", now),
    )
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, operation_type, quantity, balance_after, operation_date,
             stock_source, business_type, form_no, created_at)
        VALUES (?, ?, 'out', 1, 1, '2026-09-10', 'temporary', 'claim_outbound', 'CK2026091001', ?)
        """,
        (material_id, batch_id, now),
    )
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('CK2026091001', 'claim', '仓管CK2026091001', 'completed', 'completed', ?, '{}', '2026-09-10 11:00:00', '2026-09-10 11:00:00')
        """,
        (user_id,),
    )
    claim_id = db.execute("SELECT id FROM workflow_forms WHERE form_no = 'CK2026091001'").fetchone()[0]
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, request_quantity, stock_source, data_json)
        VALUES (?, ?, ?, '临时库测试物料', '个', 1, 'temporary', '{}')
        """,
        (claim_id, material_id, code),
    )
    db.execute(
        "UPDATE stock_records SET workflow_item_id = (SELECT id FROM workflow_items WHERE form_id = ?) "
        "WHERE form_no = 'CK2026091001'",
        (claim_id,),
    )
    db.commit()
    login(client)
    response = client.post(f"/api/temporary-inventory/acceptance-forms/{form_id}/promote", json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    promoted = db.execute("SELECT form_no, title FROM workflow_forms WHERE id = ?", (form_id,)).fetchone()
    assert promoted["form_no"].startswith(f"YS{today8}")
    assert promoted["form_no"] in promoted["title"]
    assert db.execute("SELECT form_no FROM workflow_forms WHERE id = ?", (other_10,)).fetchone()["form_no"] == "YS2026090909"
    assert db.execute("SELECT form_no FROM workflow_forms WHERE id = ?", (other_11,)).fetchone()["form_no"] == "YS2026090910"
    claim = db.execute("SELECT form_no FROM workflow_forms WHERE id = ?", (claim_id,)).fetchone()
    assert claim["form_no"].startswith(f"CK{today8}")
    assert db.execute(
        "SELECT form_no FROM stock_records WHERE business_type = 'claim_outbound'"
    ).fetchone()["form_no"] == claim["form_no"]
    batch = db.execute("SELECT source_form_no, batch_no FROM material_batches WHERE id = ?", (batch_id,)).fetchone()
    assert batch["source_form_no"].startswith(f"RK{today8}")
    assert batch["batch_no"].startswith(today8)


def test_acceptance_promote_rejects_open_temporary_claim(client, db):
    material_id, code = seed_material(db)
    enable_temporary(client)
    user_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    now = app_module.now_text()
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('YS-TEMP-2', 'acceptance', '无订单验收', 'completed', 'completed', ?, ?, '2026-01-01 08:00:00', '2026-01-01 08:00:00')
        """,
        (user_id, json.dumps({"has_purchase_order": False})),
    )
    form_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, created_at, updated_at)
        VALUES (?, 'TEMP-OPEN', 1, 1, 'office', '2026-01-02', 'temporary', 'available', 0, ?, ?)
        """,
        (material_id, now, now),
    )
    batch_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, qualified_quantity, stock_source, data_json)
        VALUES (?, ?, ?, '临时库测试物料', '个', 1, 'temporary', ?)
        """,
        (form_id, material_id, code, json.dumps({"inbound_done": True, "inbound_batch_id": batch_id})),
    )
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES ('CK-OPEN', 'claim', '未完成申领', 'leader_claim', 'leader_claim', ?, '{}', ?, ?)
        """,
        (user_id, now, now),
    )
    claim_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, request_quantity, stock_source, data_json)
        VALUES (?, ?, ?, '临时库测试物料', '个', 1, 'temporary', '{}')
        """,
        (claim_id, material_id, code),
    )
    db.commit()
    login(client)
    response = client.post(f"/api/temporary-inventory/acceptance-forms/{form_id}/promote", json={})
    assert response.status_code == 400
    assert "尚未完成" in response.get_json()["error"]
    assert response.get_json().get("splittable") is not True
    assert db.execute("SELECT stock_source FROM material_batches WHERE id = ?", (batch_id,)).fetchone()[0] == "temporary"


def _insert_batch(db, material_id, batch_no, quantity, received_date):
    now = app_module.now_text()
    db.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, received_date,
             stock_source, inventory_status, version, created_at, updated_at)
        VALUES (?, ?, ?, 1, 'office', ?, 'temporary', 'available', 0, ?, ?)
        """,
        (material_id, batch_no, quantity, received_date, now, now),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_acceptance(db, user_id, form_no, batch_id, material_id, code, name):
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'acceptance', '无订单验收', 'completed', 'completed', ?, ?, '2026-02-01 08:00:00', '2026-02-01 08:00:00')
        """,
        (form_no, user_id, json.dumps({"has_purchase_order": False})),
    )
    form_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, qualified_quantity, stock_source, data_json)
        VALUES (?, ?, ?, ?, '个', 1, 'temporary', ?)
        """,
        (form_id, material_id, code, name, json.dumps({"inbound_done": True, "inbound_batch_id": batch_id})),
    )
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, operation_type, quantity, balance_after, operation_date,
             stock_source, business_type, operation_key, created_at)
        VALUES (?, ?, 'in', 1, 1, '2026-02-01', 'temporary', 'temporary_acceptance_inbound', ?, '2026-02-01 09:00:00')
        """,
        (material_id, batch_id, f"acceptance:{form_id}:1:inbound"),
    )
    return form_id


def _insert_claim_form(db, user_id, form_no):
    db.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'claim', '历史申领', 'completed', 'completed', ?, '{}', '2026-01-01 08:00:00', '2026-01-01 08:00:00')
        """,
        (form_no, user_id),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_claim_item(db, form_id, material_id, code, name, quantity, stock_source, data=None):
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, request_quantity,
             outbound_quantity, approved_quantity, stock_source, data_json)
        VALUES (?, ?, ?, ?, '个', ?, ?, ?, ?, ?)
        """,
        (
            form_id,
            material_id,
            code,
            name,
            quantity,
            quantity if stock_source == "temporary" else 0,
            quantity,
            stock_source,
            json.dumps(data or {}),
        ),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_outbound(db, material_id, batch_id, item_id, form_no, quantity):
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, workflow_item_id, operation_type, quantity, balance_after,
             operation_date, form_no, stock_source, business_type, operation_key, created_at)
        VALUES (?, ?, ?, 'out', ?, ?, '2026-03-01', ?, 'temporary', 'claim_outbound', ?, '2026-03-01 10:00:00')
        """,
        (material_id, batch_id, item_id, quantity, quantity, form_no, f"claim-out-{form_no}-{batch_id}"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_split_cross_acceptance_line_keeps_quantities_and_form_no(client, db):
    material_id, code = seed_material(db, name="跨单物料")
    enable_temporary(client)
    user_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    batch_a = _insert_batch(db, material_id, "BATCH-A", 4, "2026-02-01")
    batch_b = _insert_batch(db, material_id, "BATCH-B", 6, "2026-02-02")
    acceptance_a = _insert_acceptance(db, user_id, "YS-SPLIT-A", batch_a, material_id, code, "跨单物料")
    _insert_acceptance(db, user_id, "YS-SPLIT-B", batch_b, material_id, code, "跨单物料")
    claim_id = _insert_claim_form(db, user_id, "CK-SPLIT-50")
    item_id = _insert_claim_item(db, claim_id, material_id, code, "跨单物料", 50, "temporary")
    out_a = _insert_outbound(db, material_id, batch_a, item_id, "CK-SPLIT-50", 20)
    out_b = _insert_outbound(db, material_id, batch_b, item_id, "CK-SPLIT-50", 30)
    consumed = [
        {"stock_record_id": out_a, "batch_id": batch_a, "quantity": 20},
        {"stock_record_id": out_b, "batch_id": batch_b, "quantity": 30},
    ]
    db.execute(
        "UPDATE workflow_items SET data_json = ? WHERE id = ?",
        (json.dumps({"consumed_batches": consumed}), item_id),
    )
    operation = {
        "operation_key": "op-50",
        "items": [{"item_id": item_id, "quantity": 50, "cumulative_quantity": 50}],
    }
    db.execute(
        """
        INSERT INTO workflow_tasks
            (form_id, step_code, assignee_id, status, decision, signature, signed_at, data_json, created_at, updated_at)
        VALUES (?, 'outbound', ?, 'completed', '已出库', '库管', '2026-03-02', ?, '2026-03-02 09:00:00', '2026-03-02 09:00:00')
        """,
        (claim_id, user_id, json.dumps({"outbound_operations": [operation]})),
    )
    task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO claim_outbound_operations
            (form_id, task_id, operator_id, operation_key, payload_json, created_at)
        VALUES (?, ?, ?, 'op-50', ?, '2026-03-02 09:00:00')
        """,
        (claim_id, task_id, user_id, json.dumps(operation)),
    )
    db.commit()
    login(client)
    blocked = client.post(f"/api/temporary-inventory/acceptance-forms/{acceptance_a}/promote", json={})
    assert blocked.status_code == 400
    body = blocked.get_json()
    assert body["splittable"] is True
    assert "两张验收单" in body["error"]
    split = client.post(f"/api/temporary-inventory/acceptance-forms/{acceptance_a}/split-claims", json={})
    assert split.status_code == 200, split.get_data(as_text=True)
    created = split.get_json()["created"]
    assert len(created) == 1
    new_form_no = created[0]["form_no"]
    assert new_form_no != "CK-SPLIT-50"
    assert new_form_no.startswith("CK")
    original = db.execute(
        "SELECT request_quantity, outbound_quantity, form_id FROM workflow_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    assert original["form_id"] == claim_id
    assert original["request_quantity"] == 20
    assert original["outbound_quantity"] == 20
    moved = db.execute(
        """
        SELECT wi.request_quantity, wi.outbound_quantity, wi.form_id, wi.data_json
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = 'claim' AND wi.form_id != ? AND wi.material_id = ?
        """,
        (claim_id, material_id),
    ).fetchone()
    assert moved["request_quantity"] == 30
    assert moved["outbound_quantity"] == 30
    assert json.loads(moved["data_json"])["consumed_batches"][0]["quantity"] == 30
    record_a = db.execute(
        "SELECT quantity, form_no, operation_date, workflow_item_id FROM stock_records WHERE id = ?",
        (out_a,),
    ).fetchone()
    record_b = db.execute(
        "SELECT quantity, form_no, operation_date, workflow_item_id FROM stock_records WHERE id = ?",
        (out_b,),
    ).fetchone()
    assert record_a["quantity"] == 20 and record_a["form_no"] == "CK-SPLIT-50"
    assert record_a["operation_date"] == "2026-03-01"
    assert record_a["workflow_item_id"] == item_id
    assert record_b["quantity"] == 30 and record_b["form_no"] == new_form_no
    assert record_b["operation_date"] == "2026-03-01"
    assert db.execute("SELECT quantity FROM material_batches WHERE id = ?", (batch_a,)).fetchone()[0] == 4
    assert db.execute("SELECT quantity FROM material_batches WHERE id = ?", (batch_b,)).fetchone()[0] == 6
    lots = {int(lot["outbound_record_id"]): lot for lot in list_issued_lots(db.cursor(), user_id)}
    assert lots[out_a]["issued_quantity"] == 20
    assert lots[out_a]["form_no"] == "CK-SPLIT-50"
    assert lots[out_a]["form_id"] == claim_id
    assert lots[out_b]["issued_quantity"] == 30
    assert lots[out_b]["form_no"] == new_form_no
    assert lots[out_b]["form_id"] == moved["form_id"]
    original_payload = json.loads(db.execute(
        "SELECT payload_json FROM claim_outbound_operations WHERE form_id = ?",
        (claim_id,),
    ).fetchone()[0])
    assert original_payload["items"][0]["item_id"] == item_id
    assert original_payload["items"][0]["quantity"] == 20
    new_payload = json.loads(db.execute(
        "SELECT payload_json FROM claim_outbound_operations WHERE form_id = ?",
        (moved["form_id"],),
    ).fetchone()[0])
    assert new_payload["items"][0]["item_id"] == moved_item_id(db, moved["form_id"])
    assert new_payload["items"][0]["quantity"] == 30
    signatures = db.execute(
        "SELECT form_id, signature, signed_at FROM workflow_tasks WHERE step_code = 'outbound' ORDER BY form_id"
    ).fetchall()
    assert {row["signature"] for row in signatures} == {"库管"}
    assert {row["signed_at"] for row in signatures} == {"2026-03-02"}
    assert db.execute("SELECT created_at FROM workflow_forms WHERE id = ?", (claim_id,)).fetchone()[0] == "2026-01-01 08:00:00"
    promoted = client.post(f"/api/temporary-inventory/acceptance-forms/{acceptance_a}/promote", json={})
    assert promoted.status_code == 200, promoted.get_data(as_text=True)
    assert db.execute("SELECT stock_source FROM material_batches WHERE id = ?", (batch_a,)).fetchone()[0] == "formal"
    assert db.execute("SELECT stock_source FROM material_batches WHERE id = ?", (batch_b,)).fetchone()[0] == "temporary"
    assert db.execute("SELECT operation_date FROM stock_records WHERE id = ?", (out_b,)).fetchone()[0] == "2026-03-01"
    assert db.execute("SELECT created_at FROM workflow_forms WHERE form_no = ?", (new_form_no,)).fetchone()[0] == "2026-01-01 08:00:00"


def moved_item_id(db, form_id):
    return db.execute("SELECT id FROM workflow_items WHERE form_id = ?", (form_id,)).fetchone()[0]


def test_split_mixed_and_two_temporary_materials(client, db):
    formal_material, formal_code = seed_material(db, name="正式物料")
    first_material, first_code = seed_material(db, name="临时物料甲")
    second_material, second_code = seed_material(db, name="临时物料乙")
    third_material, third_code = seed_material(db, name="临时物料丙")
    enable_temporary(client)
    user_id = db.execute("SELECT id FROM users WHERE username = 'warehouse'").fetchone()[0]
    mixed_batch = _insert_batch(db, first_material, "BATCH-MIX", 3, "2026-02-01")
    two_batch_a = _insert_batch(db, third_material, "BATCH-TWO-A", 2, "2026-02-01")
    two_batch_b = _insert_batch(db, second_material, "BATCH-TWO-B", 2, "2026-02-01")
    mixed_acceptance = _insert_acceptance(db, user_id, "YS-MIX", mixed_batch, first_material, first_code, "临时物料甲")
    two_acceptance = _insert_acceptance(db, user_id, "YS-TWO", two_batch_a, third_material, third_code, "临时物料丙")
    db.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, unit, qualified_quantity, stock_source, data_json)
        VALUES (?, ?, ?, '临时物料乙', '个', 1, 'temporary', ?)
        """,
        (
            two_acceptance,
            second_material,
            second_code,
            json.dumps({"inbound_done": True, "inbound_batch_id": two_batch_b}),
        ),
    )
    db.execute(
        """
        INSERT INTO stock_records
            (material_id, batch_id, operation_type, quantity, balance_after, operation_date,
             stock_source, business_type, operation_key, created_at)
        VALUES (?, ?, 'in', 1, 1, '2026-02-01', 'temporary', 'temporary_acceptance_inbound', ?, '2026-02-01 09:00:00')
        """,
        (second_material, two_batch_b, f"acceptance:{two_acceptance}:2:inbound"),
    )
    mixed_claim = _insert_claim_form(db, user_id, "CK-MIX")
    _insert_claim_item(db, mixed_claim, formal_material, formal_code, "正式物料", 1, "formal")
    mixed_item = _insert_claim_item(db, mixed_claim, first_material, first_code, "临时物料甲", 3, "temporary")
    mixed_out = _insert_outbound(db, first_material, mixed_batch, mixed_item, "CK-MIX", 3)
    two_claim = _insert_claim_form(db, user_id, "CK-TWO")
    first_item = _insert_claim_item(db, two_claim, third_material, third_code, "临时物料丙", 2, "temporary")
    second_item = _insert_claim_item(db, two_claim, second_material, second_code, "临时物料乙", 2, "temporary")
    _insert_outbound(db, third_material, two_batch_a, first_item, "CK-TWO", 2)
    _insert_outbound(db, second_material, two_batch_b, second_item, "CK-TWO", 2)
    db.commit()
    login(client)
    mixed_blocked = client.post(f"/api/temporary-inventory/acceptance-forms/{mixed_acceptance}/promote", json={})
    assert mixed_blocked.status_code == 400
    assert mixed_blocked.get_json()["splittable"] is True
    assert "同时领用了临时库和正式库" in mixed_blocked.get_json()["error"]
    mixed_split = client.post(f"/api/temporary-inventory/acceptance-forms/{mixed_acceptance}/split-claims", json={})
    assert mixed_split.status_code == 200, mixed_split.get_data(as_text=True)
    mixed_new = mixed_split.get_json()["created"][0]["form_no"]
    assert db.execute(
        "SELECT stock_source FROM workflow_items WHERE form_id = ?",
        (mixed_claim,),
    ).fetchone()[0] == "formal"
    assert db.execute("SELECT form_no FROM stock_records WHERE id = ?", (mixed_out,)).fetchone()[0] == mixed_new
    assert db.execute("SELECT quantity FROM material_batches WHERE id = ?", (mixed_batch,)).fetchone()[0] == 3
    two_blocked = client.post(f"/api/temporary-inventory/acceptance-forms/{two_acceptance}/promote", json={})
    assert two_blocked.status_code == 400
    assert "两种临时库物料" in two_blocked.get_json()["error"]
    two_split = client.post(f"/api/temporary-inventory/acceptance-forms/{two_acceptance}/split-claims", json={})
    assert two_split.status_code == 200, two_split.get_data(as_text=True)
    promoted = client.post(f"/api/temporary-inventory/acceptance-forms/{two_acceptance}/promote", json={})
    assert promoted.status_code == 200, promoted.get_data(as_text=True)
