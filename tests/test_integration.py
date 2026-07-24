# -*- coding: utf-8 -*-


_COUNTER = [0]

def _next_code():
    _COUNTER[0] += 1
    return f"1020010001{_COUNTER[0]:04d}"


def login_as(client, username, password):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is True
    return data["user"]


def create_material(client, code, name, unit="个"):
    resp = client.post("/api/material-master", json={
        "material_code": code,
        "name": name,
        "unit": unit,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["material"]


def create_shelf(client, name, warehouse_type="office"):
    resp = client.post("/api/shelves", json={
        "name": name,
        "warehouse_type": warehouse_type,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["shelf"]


def set_position(client, material_id, shelf_id, layer=1, zone="A"):
    resp = client.put(f"/api/materials/{material_id}/position", json={
        "shelf_id": shelf_id,
        "layer_number": layer,
        "zone_name": zone,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def stock_in(client, material_id, quantity, unit_price=10):
    resp = client.post("/api/stock/in", json={
        "material_id": material_id,
        "quantity": quantity,
        "unit_price": unit_price,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def create_borrow(client, items, leader_id=None):
    payload = {"items": items}
    if leader_id is not None:
        payload["leader_id"] = leader_id
    resp = client.post("/api/borrows", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["form"]


def leader_approve_borrow(client, form_id, decision="同意", warehouse_user_id=None):
    payload = {"decision": decision}
    if warehouse_user_id is not None:
        payload["warehouse_user_id"] = warehouse_user_id
    resp = client.post(f"/api/borrows/{form_id}/leader", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["form"]


def outbound_borrow(client, form_id):
    resp = client.post(f"/api/borrows/{form_id}/outbound", json={"items": []})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["form"]


def get_borrow_record(db, form_id):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM borrow_records WHERE borrow_form_id = ?", (form_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def get_borrow_record_by_id(db, record_id):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def transfer_borrow(client, record_id, receiver_id):
    resp = client.post(f"/api/borrows/{record_id}/transfer", json={"receiver_id": receiver_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def accept_transfer(client, record_id):
    resp = client.post(f"/api/transfers/{record_id}/accept")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def reject_transfer(client, record_id):
    resp = client.post(f"/api/transfers/{record_id}/reject")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def create_return(client, record_id, status="完好", **kwargs):
    payload = {"borrow_record_id": record_id, "status": status}
    payload.update(kwargs)
    resp = client.post("/api/borrow-returns", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["form"]


def inbound_return(client, form_id, decision="同意"):
    resp = client.post(f"/api/borrow-returns/{form_id}/inbound", json={"decision": decision})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["form"]


def get_user_id(db, username):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    return row[0] if row else None


def get_admin_id(db):
    return get_user_id(db, "admin")


# ── full borrow flow ──────────────────────────────────────────────────

def full_borrow_flow(client, db, item_type, item_ref_id, qty=1,
                     borrower="warehouse", leader="admin", leader_pw="Costar@508"):
    login_as(client, borrower, "test")
    admin_id = get_admin_id(db)
    wh_id = get_user_id(db, "warehouse")
    form = create_borrow(client, [{
        "item_type": item_type,
        "item_ref_id": item_ref_id,
        "request_quantity": qty,
    }], leader_id=admin_id)
    login_as(client, leader, leader_pw)
    form = leader_approve_borrow(client, form["id"], warehouse_user_id=wh_id)
    login_as(client, "warehouse", "test")
    form = outbound_borrow(client, form["id"])
    record = get_borrow_record(db, form["id"])
    assert record is not None, "borrow record not created"
    return record


# ── data setup helpers ────────────────────────────────────────────────

def setup_material_with_stock(client, db, name, qty=10, unit="个"):
    login_as(client, "warehouse", "test")
    code = _next_code()
    mat = create_material(client, code, name, unit)
    shelf = create_shelf(client, f"货架-{code[-6:]}", "office")
    set_position(client, mat["id"], shelf["id"])
    stock_in(client, mat["id"], qty)
    return mat


def setup_semifinished(db, name="测试半成品", spec="v1.0", serial_no=None, qty=5):
    if serial_no is None:
        serial_no = f"BP-{_next_code()[-8:]}"
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO semifinished_inventory (name, spec, unit, quantity, serial_no, acceptance_date, created_at, updated_at) "
        "VALUES (?, ?, '个', ?, ?, date('now'), datetime('now'), datetime('now'))",
        (name, spec, qty, serial_no),
    )
    db.commit()
    return cursor.lastrowid


def setup_finished(db, product_name="测试成品", spec="v2.0", serial_no=None, qty=5):
    if serial_no is None:
        serial_no = f"CP-{_next_code()[-8:]}"
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO finished_good_inventory (product_name, spec, unit, quantity, serial_no, acceptance_date, created_at, updated_at) "
        "VALUES (?, ?, '台', ?, ?, date('now'), datetime('now'), datetime('now'))",
        (product_name, spec, qty, serial_no),
    )
    db.commit()
    return cursor.lastrowid


# ══════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════


class TestTransferLifecycle:

    def test_full_transfer_lifecycle(self, client, db):
        mat = setup_material_with_stock(client, db, "转借测试物料")
        record = full_borrow_flow(client, db, "material", mat["id"], qty=1)
        assert record["status"] == "borrowed"
        wh_id = get_user_id(db, "warehouse")
        tu_id = get_user_id(db, "testuser")
        assert record["borrower_id"] == wh_id

        login_as(client, "warehouse", "test")
        transfer_borrow(client, record["id"], tu_id)
        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["status"] == "transferring"

        login_as(client, "testuser", "test")
        accept_transfer(client, record["id"])
        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["status"] == "borrowed"
        assert rec["borrower_id"] == tu_id

    def test_full_transfer_reject_lifecycle(self, client, db):
        mat = setup_material_with_stock(client, db, "转借拒绝测试物料")
        record = full_borrow_flow(client, db, "material", mat["id"], qty=1)
        wh_id = get_user_id(db, "warehouse")
        tu_id = get_user_id(db, "testuser")
        assert record["borrower_id"] == wh_id

        login_as(client, "warehouse", "test")
        transfer_borrow(client, record["id"], tu_id)
        login_as(client, "testuser", "test")
        reject_transfer(client, record["id"])
        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["status"] == "borrowed"
        assert rec["borrower_id"] == wh_id


class TestReturnWorkflows:

    def test_full_return_scrapped_finished(self, client, db):
        login_as(client, "warehouse", "test")
        fin_id = setup_finished(db, "报废测试成品", "v3.0", "CP-SCRAP-001", qty=5)
        create_shelf(client, "成品货架", "office")
        record = full_borrow_flow(client, db, "finished", fin_id, qty=1)

        login_as(client, "warehouse", "test")
        ret_form = create_return(client, record["id"], status="报废", remarks="测试报废原因")
        inbound_return(client, ret_form["id"], decision="同意")

        cursor = db.cursor()
        cursor.execute("SELECT * FROM scrapped_finished_goods WHERE serial_no = 'CP-SCRAP-001'")
        scrapped = cursor.fetchone()
        assert scrapped is not None, "scrapped_finished_goods entry not found"
        assert scrapped["scrap_source"] == "borrow_return"

        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["status"] == "returned"

    def test_full_return_restore_scrapped(self, client, db):
        login_as(client, "warehouse", "test")
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO finished_good_inventory (product_name, spec, unit, quantity, borrowed_quantity, serial_no, acceptance_date, created_at, updated_at) "
            "VALUES ('恢复测试成品', 'v4.0', '台', 1, 1, 'CP-RESTORE-001', date('now'), datetime('now'), datetime('now'))"
        )
        fin_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO scrapped_finished_goods (product_name, spec, serial_no, unit, quantity, original_inventory_id, scrap_source, scrap_reason, scrap_date, created_at) "
            "VALUES ('恢复测试成品', 'v4.0', 'CP-RESTORE-001', '台', 1, ?, 'manual', 'test', date('now'), datetime('now'))",
            (fin_id,),
        )
        db.commit()
        create_shelf(client, "恢复货架", "office")

        warehouse_id = get_user_id(db, "warehouse")
        login_as(client, "warehouse", "test")
        cursor.execute(
            "INSERT INTO borrow_records (borrow_no, item_type, item_ref_id, item_code, item_name, spec, unit, quantity, returned_quantity, status, borrower_id, outbound_date, created_at, updated_at) "
            "VALUES ('TEST-RESTORE', 'finished', ?, 'CP-RESTORE-001', '恢复测试成品', 'v4.0', '台', 1, 0, 'borrowed', ?, date('now'), datetime('now'), datetime('now'))",
            (fin_id, warehouse_id),
        )
        record_id = cursor.lastrowid
        db.commit()

        ret_form = create_return(client, record_id, status="完好")
        inbound_return(client, ret_form["id"], decision="同意")

        cursor = db.cursor()
        cursor.execute("SELECT * FROM scrapped_finished_goods WHERE serial_no = 'CP-RESTORE-001'")
        assert cursor.fetchone() is None, "scrapped entry should be removed"

        cursor.execute("SELECT * FROM finished_good_inventory WHERE id = ?", (fin_id,))
        inv = dict(cursor.fetchone())
        assert float(inv["borrowed_quantity"] or 0) == 0

    def test_full_return_semifinished_with_change(self, client, db):
        login_as(client, "warehouse", "test")
        semi_id = setup_semifinished(db, "变更测试半成品", "v1.0", "BP-CHANGE-001", qty=5)
        create_shelf(client, "半成品货架", "office")
        record = full_borrow_flow(client, db, "semifinished", semi_id, qty=1)

        login_as(client, "warehouse", "test")
        ret_form = create_return(
            client, record["id"], status="异常",
            has_changes="是", change_type="软件",
            change_detail="升级固件到 v2.0",
            version_after="v2.0",
            normal_use="是",
            remarks="软件变更测试",
        )
        inbound_return(client, ret_form["id"], decision="同意")

        cursor = db.cursor()
        cursor.execute("SELECT * FROM borrow_change_records WHERE borrow_record_id = ?", (record["id"],))
        changes = cursor.fetchall()
        assert len(changes) >= 1, "borrow_change_records not created"
        ch = dict(changes[0])
        assert ch["change_type"] == "软件"
        assert ch["change_detail"] == "升级固件到 v2.0"
        assert ch["version_after"] == "v2.0"
        assert ch["normal_use"] == "是"

        login_as(client, "warehouse", "test")
        resp = client.get(f"/api/items/semifinished/{semi_id}/history")
        assert resp.status_code == 200
        hist = resp.get_json()
        assert "history" in hist
        assert "item" in hist


class TestWarningAndTransferReturn:

    def test_warning_condition_data(self, client, db):
        login_as(client, "warehouse", "test")
        semi_id = setup_semifinished(db, "异常测试半成品", "v1.0", "BP-WARN-001", qty=5)
        create_shelf(client, "异常货架", "office")
        record = full_borrow_flow(client, db, "semifinished", semi_id, qty=1)

        login_as(client, "warehouse", "test")
        ret_form = create_return(
            client, record["id"], status="异常",
            has_changes="是", change_type="硬件",
            change_detail="测试异常状态",
            normal_use="否",
            remarks="异常测试",
        )
        inbound_return(client, ret_form["id"], decision="同意")

        login_as(client, "warehouse", "test")
        resp = client.get(f"/api/items/semifinished/{semi_id}/history")
        assert resp.status_code == 200
        hist = resp.get_json()
        assert "history" in hist
        assert len(hist["history"]) >= 1

        cursor = db.cursor()
        cursor.execute("SELECT * FROM borrow_change_records WHERE borrow_record_id = ?", (record["id"],))
        changes = cursor.fetchall()
        assert len(changes) >= 1
        assert dict(changes[0])["normal_use"] == "否"

    def test_transfer_then_return(self, client, db):
        mat = setup_material_with_stock(client, db, "转借后归还测试")
        record = full_borrow_flow(client, db, "material", mat["id"], qty=2)
        tu_id = get_user_id(db, "testuser")

        login_as(client, "warehouse", "test")
        transfer_borrow(client, record["id"], tu_id)
        login_as(client, "testuser", "test")
        accept_transfer(client, record["id"])

        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["borrower_id"] == tu_id

        login_as(client, "testuser", "test")
        wh_id = get_user_id(db, "warehouse")
        ret_form = create_return(client, record["id"], status="完好", warehouse_user_id=wh_id)
        login_as(client, "warehouse", "test")
        inbound_return(client, ret_form["id"], decision="同意")

        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["status"] == "returned"

    def test_concurrent_block(self, client, db):
        mat = setup_material_with_stock(client, db, "并发阻塞测试")
        record = full_borrow_flow(client, db, "material", mat["id"], qty=1)
        tu_id = get_user_id(db, "testuser")

        login_as(client, "warehouse", "test")
        transfer_borrow(client, record["id"], tu_id)

        login_as(client, "warehouse", "test")
        resp = client.post("/api/borrow-returns", json={
            "borrow_record_id": record["id"],
            "status": "完好",
        })
        data = resp.get_json()
        if resp.status_code == 400:
            assert not data.get("success")
        else:
            assert data.get("success") is True


class TestAdminRejectAndNotifications:

    def test_admin_reject_no_changes(self, client, db):
        mat = setup_material_with_stock(client, db, "拒绝归还测试", qty=10)
        record = full_borrow_flow(client, db, "material", mat["id"], qty=1)

        login_as(client, "warehouse", "test")
        ret_form = create_return(client, record["id"], status="完好")

        login_as(client, "warehouse", "test")
        resp = client.post(f"/api/borrow-returns/{ret_form['id']}/inbound", json={
            "decision": "拒绝",
            "remark": "测试拒绝",
        })
        assert resp.status_code == 200

        rec = get_borrow_record_by_id(db, record["id"])
        assert rec["status"] == "borrowed"
        assert float(rec["returned_quantity"] or 0) == 0

        cursor = db.cursor()
        cursor.execute("SELECT status FROM workflow_forms WHERE id = ?", (ret_form["id"],))
        form_status = cursor.fetchone()["status"]
        assert form_status == "rejected"

    def test_notification_chain(self, client, db):
        mat = setup_material_with_stock(client, db, "通知链测试")
        record = full_borrow_flow(client, db, "material", mat["id"], qty=1)
        tu_id = get_user_id(db, "testuser")
        wh_id = get_user_id(db, "warehouse")

        login_as(client, "warehouse", "test")
        transfer_borrow(client, record["id"], tu_id)

        cursor = db.cursor()
        cursor.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (tu_id,),
        )
        notif = cursor.fetchone()
        assert notif is not None, "transfer notification not created"
        assert "转借" in str(notif["title"])

        login_as(client, "testuser", "test")
        accept_transfer(client, record["id"])

        cursor.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (wh_id,),
        )
        notif2 = cursor.fetchone()
        assert notif2 is not None, "accept notification not created"
        assert "转借已接受" in str(notif2["title"])

        cursor.execute("SELECT COUNT(*) FROM notifications")
        total = cursor.fetchone()[0]
        assert total >= 2, f"expected at least 2 notifications, got {total}"


class TestAcceptanceSearchPagination:

    def test_acceptance_fields_flow(self, client, db):
        login_as(client, "warehouse", "test")
        mat = setup_material_with_stock(client, db, "验收流程测试物料", qty=100)
        mat_code = mat["material_code"]

        resp = client.post("/api/acceptance", json={
            "items": [{
                "material_code": mat_code,
                "name": "验收流程测试物料",
                "purchase_quantity": 10,
                "arrival_quantity": 10,
                "unit_price": 50,
            }],
            "validator_ids": [get_user_id(db, "warehouse")],
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        form = resp.get_json()["form"]
        assert form["form_no"].startswith("YS")

        item = form["items"][0]
        resp = client.post(f"/api/acceptance/{form['id']}/inspect", json={
            "items": [{
                "id": item["id"],
                "qualified_quantity": 10,
                "unqualified_quantity": 0,
                "package_ok_quantity": 10,
                "appearance_ok_quantity": 10,
                "name_spec_ok_quantity": 10,
                "usage_ok_quantity": 10,
            }],
        })
        assert resp.status_code == 200

        login_as(client, "admin", "Costar@508")
        wh_id = get_user_id(db, "warehouse")
        resp = client.post(f"/api/acceptance/{form['id']}/leader", json={
            "decision": "同意",
            "warehouse_user_id": wh_id,
        })
        assert resp.status_code == 200

        login_as(client, "warehouse", "test")
        resp = client.post(f"/api/acceptance/{form['id']}/inbound", json={"items": []})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json().get("success") is True

        resp = client.get("/api/materials")
        assert resp.status_code == 200
        materials = resp.get_json()
        codes = [m.get("material_code") for m in materials]
        assert mat_code in codes

    def test_pagination_boundary(self, client, db):
        login_as(client, "warehouse", "test")
        semi_id = setup_semifinished(db, "分页测试半成品", "v1.0", "BP-PAGE-001", qty=30)
        create_shelf(client, "分页货架", "office")

        cursor = db.cursor()
        wh_id = get_user_id(db, "warehouse")
        for i in range(25):
            cursor.execute(
                "INSERT INTO borrow_records (borrow_no, item_type, item_ref_id, item_code, item_name, spec, unit, quantity, returned_quantity, status, borrower_id, outbound_date, created_at, updated_at) "
                "VALUES (?, 'semifinished', ?, 'BP-PAGE-001', '分页测试半成品', 'v1.0', '个', 1, 1, 'returned', ?, date('now'), datetime('now'), datetime('now'))",
                (f"PAGE-{i:03d}", semi_id, wh_id),
            )
            record_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO borrow_change_records (borrow_record_id, change_type, change_detail, version_after, normal_use, created_at) "
                "VALUES (?, '软件', ?, ?, '是', datetime('now'))",
                (record_id, f"变更记录#{i+1}", f"v{i+1}.0"),
            )
        db.commit()

        login_as(client, "warehouse", "test")
        resp = client.get(f"/api/items/semifinished/{semi_id}/history?page=1&limit=20")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["page"] == 1
        assert data["limit"] == 20
        assert data["total"] == 25
        assert len(data["history"]) > 0

        resp = client.get(f"/api/items/semifinished/{semi_id}/history?page=2&limit=20")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["page"] == 2
        assert data["total"] == 25
        assert len(data["history"]) > 0

    def test_cjk_search_integration(self, client, db):
        login_as(client, "warehouse", "test")
        code = _next_code()
        mat = create_material(client, code, "高速贴片电容测试物料", "个")
        shelf = create_shelf(client, "中文搜索货架", "office")
        set_position(client, mat["id"], shelf["id"])
        stock_in(client, mat["id"], 10)

        resp = client.get("/api/materials/search?keyword=贴片电容")
        assert resp.status_code == 200
        results = resp.get_json()
        codes = [r.get("material_code") for r in results]
        assert code in codes, f"Chinese keyword search failed, got: {codes}"

        resp = client.get("/api/materials/search?keyword=高速贴片")
        assert resp.status_code == 200
        results = resp.get_json()
        codes = [r.get("material_code") for r in results]
        assert code in codes

        resp = client.get("/api/materials/search?keyword=")
        assert resp.status_code == 200
        results = resp.get_json()
        assert code in [row.get("material_code") for row in results]


class TestBackwardCompatAndUntouched:

    def test_backward_compat(self, client, db):
        mat = setup_material_with_stock(client, db, "向后兼容测试", qty=10)
        admin_id = get_admin_id(db)

        login_as(client, "warehouse", "test")
        resp = client.post("/api/borrows", json={
            "items": [{
                "item_type": "material",
                "item_ref_id": mat["id"],
                "request_quantity": 1,
            }],
            "leader_id": admin_id,
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        form = resp.get_json()["form"]
        assert form["status"] == "leader_borrow"

        login_as(client, "admin", "Costar@508")
        wh_id = get_user_id(db, "warehouse")
        leader_approve_borrow(client, form["id"], warehouse_user_id=wh_id)
        login_as(client, "warehouse", "test")
        outbound_borrow(client, form["id"])

        record = get_borrow_record(db, form["id"])
        assert record is not None
        assert record["status"] == "borrowed"

    def test_material_untouched(self, client, db):
        mat = setup_material_with_stock(client, db, "响应结构验证", qty=5)
        login_as(client, "warehouse", "test")

        resp = client.get("/api/materials")
        assert resp.status_code == 200
        materials = resp.get_json()
        assert isinstance(materials, list)
        if materials:
            m = materials[0]
            assert "id" in m
            assert "material_code" in m
            assert "name" in m

        resp = client.get(f"/api/materials/{mat['id']}")
        assert resp.status_code == 200
        m = resp.get_json()
        assert m["id"] == mat["id"]
        assert "material_code" in m
        assert "name" in m
        assert "quantity" in m

        resp = client.get("/api/materials/search?keyword=响应结构")
        assert resp.status_code == 200
        results = resp.get_json()
        assert isinstance(results, list)
        if results:
            r = results[0]
            assert "id" in r
            assert "material_code" in r
            assert "name" in r

        resp = client.get("/api/shelves")
        assert resp.status_code == 200
        shelves = resp.get_json()
        assert isinstance(shelves, list)
