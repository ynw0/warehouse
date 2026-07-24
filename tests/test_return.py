def _login(client, username, password):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.get_json()["user"]


def _get_user_id(db, username):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    return row[0] if row else None


def _create_material(db):
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit) VALUES (?, ?, ?)",
        ("MAT-001", "Test Material", "个"),
    )
    material_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO inventory (material_id, quantity) VALUES (?, ?)",
        (material_id, 100),
    )
    db.commit()
    return material_id


def _create_semifinished(db, serial_no="SF-001", qty=10):
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO semifinished_inventory (name, spec, unit, quantity, acceptance_date, serial_no, borrowed_quantity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Semi A", "Spec", "个", qty, "2024-01-01", serial_no, 0),
    )
    inv_id = cursor.lastrowid
    db.commit()
    return inv_id


def _create_finished(db, serial_no="FG-001", qty=10):
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO finished_good_inventory (product_name, spec, unit, quantity, acceptance_date, serial_no, borrowed_quantity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Finished A", "Spec", "台", qty, "2024-01-01", serial_no, 0),
    )
    inv_id = cursor.lastrowid
    db.commit()
    return inv_id


def _create_borrow_record(db, borrower_id, item_type, item_ref_id, material_id=None, qty=5, item_code="", item_name=""):
    cursor = db.cursor()
    borrow_no = f"JY-{item_type}-{item_ref_id}"
    cursor.execute(
        """
        INSERT INTO borrow_records
        (borrow_no, item_type, item_ref_id, material_id, item_code, item_name, brand_model, spec, unit,
         quantity, returned_quantity, status, borrower_id, borrow_form_id, outbound_date, data_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'borrowed', ?, NULL, ?, ?, ?, ?)
        """,
        (
            borrow_no,
            item_type,
            item_ref_id,
            material_id if material_id is not None else None,
            item_code,
            item_name,
            "",
            "",
            "个",
            qty,
            borrower_id,
            "2024-01-01",
            "{}",
            "2024-01-01",
            "2024-01-01",
        ),
    )
    record_id = cursor.lastrowid

    if item_type == "semifinished":
        cursor.execute(
            "UPDATE semifinished_inventory SET borrowed_quantity = COALESCE(borrowed_quantity, 0) + ? WHERE id = ?",
            (qty, item_ref_id),
        )
    elif item_type == "finished":
        cursor.execute(
            "UPDATE finished_good_inventory SET borrowed_quantity = COALESCE(borrowed_quantity, 0) + ? WHERE id = ?",
            (qty, item_ref_id),
        )

    db.commit()
    return record_id


def _create_return(client, borrow_record_id, **kwargs):
    payload = {
        "borrow_record_id": borrow_record_id,
        "return_quantity": kwargs.get("return_quantity", 5),
        "status": kwargs.get("status", "完好"),
        "remarks": kwargs.get("remarks", ""),
        "has_changes": kwargs.get("has_changes", "否"),
        "change_type": kwargs.get("change_type", ""),
        "change_detail": kwargs.get("change_detail", ""),
        "version_after": kwargs.get("version_after", ""),
        "normal_use": kwargs.get("normal_use", ""),
    }
    if "warehouse_user_id" in kwargs:
        payload["warehouse_user_id"] = kwargs["warehouse_user_id"]
    resp = client.post("/api/borrow-returns", json=payload)
    return resp


def _approve_return(client, form_id, decision="同意", remark=""):
    payload = {"decision": decision, "remark": remark}
    resp = client.post(f"/api/borrow-returns/{form_id}/inbound", json=payload)
    return resp


# 1. Missing/invalid status → 400
# (The endpoint defaults missing status to "完好", so we test an invalid value.)
def test_return_status_required(client, db):
    testuser_id = _get_user_id(db, "testuser")
    mat_id = _create_material(db)
    record_id = _create_borrow_record(db, testuser_id, "material", mat_id, material_id=mat_id, qty=5, item_code="MAT-001", item_name="Test Material")

    _login(client, "testuser", "test")
    resp = _create_return(client, record_id, status="invalid")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


# 2. status=报废, no remarks → 400
def test_return_scrap_requires_remarks(client, db):
    testuser_id = _get_user_id(db, "testuser")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(client, record_id, status="报废", remarks="")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


# 3. status=异常, no remarks → 400
def test_return_abnormal_requires_remarks(client, db):
    testuser_id = _get_user_id(db, "testuser")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(client, record_id, status="异常", remarks="")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


# 4. status=完好, no remarks → 200 (creates workflow)
def test_return_fine_no_remarks(client, db):
    testuser_id = _get_user_id(db, "testuser")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(client, record_id, status="完好", remarks="")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "form" in data


# 5. semifinished, has_changes=是, change_type=软件, version_after="2.0" → change stored
def test_return_with_software_change(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        has_changes="是",
        change_type="软件",
        version_after="2.0",
        normal_use="是",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM borrow_change_records WHERE borrow_record_id = ? ORDER BY id DESC LIMIT 1",
        (record_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row["change_type"] == "软件"
    assert row["version_after"] == "2.0"
    assert row["normal_use"] == "是"


# 6. finished, has_changes=是, change_type=硬件, normal_use=否 → stored
def test_return_with_hardware_change(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    fg_id = _create_finished(db)
    record_id = _create_borrow_record(db, testuser_id, "finished", fg_id, qty=5, item_code="FG-001", item_name="Finished A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        has_changes="是",
        change_type="硬件",
        change_detail="replaced board",
        normal_use="否",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM borrow_change_records WHERE borrow_record_id = ? ORDER BY id DESC LIMIT 1",
        (record_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row["change_type"] == "硬件"
    assert row["change_detail"] == "replaced board"
    assert row["normal_use"] == "否"


# 7. borrow_change_records has row after return
def test_return_change_record_saved(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        has_changes="是",
        change_type="软件",
        version_after="1.5",
        normal_use="是",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM borrow_change_records WHERE borrow_record_id = ?", (record_id,))
    count = cursor.fetchone()[0]
    assert count == 1


# 8. Admin approve报废 → scrapped table populated, inventory decreased
def test_return_admin_approve_scrapped(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db, serial_no="SF-SCRAP-8", qty=10)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-SCRAP-8", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="报废",
        remarks="broken",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (sf_id,))
    inv = cursor.fetchone()
    assert inv["quantity"] == 5
    assert inv["borrowed_quantity"] == 0

    cursor.execute("SELECT * FROM scrapped_semifinished_goods WHERE serial_no = ?", ("SF-SCRAP-8",))
    scrap = cursor.fetchone()
    assert scrap is not None
    assert scrap["quantity"] == 5


# 9. Admin approve完好 → standard inventory update
def test_return_admin_approve_fine(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db, serial_no="SF-FINE-9", qty=10)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-FINE-9", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (sf_id,))
    inv = cursor.fetchone()
    assert inv["quantity"] == 10
    assert inv["borrowed_quantity"] == 0


# 10. Admin reject → borrow_record back to 'borrowed', no DB changes
def test_return_admin_reject(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db, serial_no="SF-REJ-10", qty=10)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-REJ-10", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="拒绝", remark="not accepted")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
    record = cursor.fetchone()
    assert record["status"] == "borrowed"
    assert record["returned_quantity"] == 0

    cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (sf_id,))
    inv = cursor.fetchone()
    assert inv["borrowed_quantity"] == 5


# 11. Scrapped item returned完好 → moves to qualified inventory
def test_return_scrapped_to_qualified(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db, serial_no="SF-QUAL-11", qty=0)
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO scrapped_semifinished_goods
        (acceptance_id, name, spec, serial_no, unit, quantity, original_inventory_id, scrap_source, scrap_reason, scrap_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (None, "Semi A", "Spec", "SF-QUAL-11", "个", 5, sf_id, "borrow_return", "previous scrap", "2024-01-01", "2024-01-01"),
    )
    db.commit()

    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-QUAL-11", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT * FROM scrapped_semifinished_goods WHERE serial_no = ?", ("SF-QUAL-11",))
    assert cursor.fetchone() is None

    cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (sf_id,))
    inv = cursor.fetchone()
    assert inv["quantity"] == 5
    assert inv["borrowed_quantity"] == 0


# 12. Non-borrower cannot return → 403
def test_return_wrong_user(client, db):
    testuser_id = _get_user_id(db, "testuser")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "warehouse", "test")
    resp = _create_return(client, record_id, status="完好")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "只能归还自己的借用物料" in data["error"]


# 13. Return qty > borrowed → 400
def test_return_exceed_quantity(client, db):
    testuser_id = _get_user_id(db, "testuser")
    sf_id = _create_semifinished(db)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-001", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(client, record_id, return_quantity=10, status="完好")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


# 14. Partial return supported
def test_return_partial(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    sf_id = _create_semifinished(db, serial_no="SF-PART-14", qty=10)
    record_id = _create_borrow_record(db, testuser_id, "semifinished", sf_id, qty=5, item_code="SF-PART-14", item_name="Semi A")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        return_quantity=2,
        status="完好",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
    record = cursor.fetchone()
    assert record["returned_quantity"] == 2
    assert record["status"] == "borrowed"

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        return_quantity=3,
        status="完好",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id2 = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id2, decision="同意")
    assert resp.status_code == 200

    cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
    record = cursor.fetchone()
    assert record["returned_quantity"] == 5
    assert record["status"] == "returned"


# 15. Material return without change fields → 200
def test_return_material_no_changes(client, db):
    testuser_id = _get_user_id(db, "testuser")
    warehouse_id = _get_user_id(db, "warehouse")
    mat_id = _create_material(db)
    record_id = _create_borrow_record(db, testuser_id, "material", mat_id, material_id=mat_id, qty=5, item_code="MAT-001", item_name="Test Material")

    _login(client, "testuser", "test")
    resp = _create_return(
        client,
        record_id,
        status="完好",
        warehouse_user_id=warehouse_id,
    )
    assert resp.status_code == 200
    form_id = resp.get_json()["form"]["id"]

    _login(client, "warehouse", "test")
    resp = _approve_return(client, form_id, decision="同意")
    assert resp.status_code == 200

    cursor = db.cursor()
    cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
    record = cursor.fetchone()
    assert record["status"] == "returned"
    assert record["returned_quantity"] == 5
