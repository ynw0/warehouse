import json

import app as app_module


def _login(client, username="warehouse", password="test"):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    return data["user"]


def _seed_claimed_material(db):
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES (?, ?, '个', ?, ?)",
        ("TEST001", "测试物料", app_module.now_text(), app_module.now_text()),
    )
    material_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO material_batches
        (material_id, batch_no, quantity, unit_price, received_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (material_id, "B001", 100, 1.0, app_module.today_text(), app_module.now_text(), app_module.now_text()),
    )
    batch_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO workflow_forms
        (form_no, form_type, title, status, current_step, applicant_id, created_at, updated_at)
        VALUES (?, 'claim', ?, 'completed', 'completed', ?, ?, ?)
        """,
        ("YS2026010101", "测试领用", 1, app_module.now_text(), app_module.now_text()),
    )
    form_id = cursor.lastrowid

    data_json = json.dumps(
        {
            "consumed_batches": [{"batch_id": batch_id, "batch_no": "B001", "quantity": 10}],
            "claim_applicant_name": "测试用户",
            "claim_applicant_id": 1,
        },
        ensure_ascii=False,
    )
    cursor.execute(
        """
        INSERT INTO workflow_items
        (form_id, material_id, material_code, material_name, unit, outbound_quantity, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (form_id, material_id, "TEST001", "测试物料", "个", 10, data_json),
    )
    db.commit()
    return material_id, batch_id


def _seed_maker(db, display_name):
    cursor = db.cursor()
    username = f"maker_{display_name.lower().replace(' ', '_')}"
    cursor.execute(
        """
        INSERT INTO users
        (username, display_name, password, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (username, display_name, app_module.generate_password_hash("test"), app_module.now_text(), app_module.now_text()),
    )
    user_id = cursor.lastrowid
    cursor.execute("SELECT id FROM roles WHERE code = 'user'")
    role_id = cursor.fetchone()[0]
    cursor.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
    db.commit()
    return user_id


def _next_acceptance_no(cursor, prefix):
    today = app_module.today_text().replace("-", "")
    like = f"{prefix}{today}%"
    cursor.execute(
        "SELECT acceptance_no FROM semifinished_acceptances WHERE acceptance_no LIKE ? ORDER BY acceptance_no DESC LIMIT 1",
        (like,),
    )
    row = cursor.fetchone()
    if row:
        serial = int(str(row[0])[-2:]) + 1
    else:
        serial = 1
    return f"{prefix}{today}{serial:02d}"


def _seed_semifinished_acceptance(db, name, project_code="", maker_id=None):
    cursor = db.cursor()
    today = app_module.today_text()
    now = app_module.now_text()
    acceptance_no = _next_acceptance_no(cursor, "BY")
    cursor.execute(
        """
        INSERT INTO semifinished_acceptances
        (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date,
         qualified_quantity, unqualified_quantity, appearance_ok_quantity,
         function_ok_quantity, performance_ok_quantity, cost_price, components_json,
         serials_json, applicant_id, project_code, maker_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            acceptance_no,
            name,
            "",
            1,
            "个",
            today,
            1,
            0,
            1,
            1,
            1,
            0,
            "[]",
            json.dumps([{"serial_no": acceptance_no, "qualified": True}], ensure_ascii=False),
            1,
            project_code,
            maker_id,
            now,
            now,
        ),
    )
    acceptance_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO semifinished_inventory
        (acceptance_id, name, spec, unit, quantity, used_quantity, cost_price,
         components_json, serial_no, acceptance_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            acceptance_id,
            name,
            "",
            "个",
            1,
            0,
            "[]",
            f"BP{acceptance_id}",
            today,
            now,
            now,
        ),
    )
    db.commit()
    return acceptance_id


def _next_finished_acceptance_no(cursor):
    today = app_module.today_text().replace("-", "")
    like = f"CY{today}%"
    cursor.execute(
        "SELECT acceptance_no FROM finished_acceptances WHERE acceptance_no LIKE ? ORDER BY acceptance_no DESC LIMIT 1",
        (like,),
    )
    row = cursor.fetchone()
    if row:
        serial = int(str(row[0])[-2:]) + 1
    else:
        serial = 1
    return f"CY{today}{serial:02d}"


def _seed_finished_acceptance(db, product_name, project_code="", maker_id=None):
    cursor = db.cursor()
    today = app_module.today_text()
    now = app_module.now_text()
    acceptance_no = _next_finished_acceptance_no(cursor)
    cursor.execute(
        """
        INSERT INTO finished_acceptances
        (acceptance_no, product_name, spec, acceptance_quantity, unit, acceptance_date,
         qualified_quantity, unqualified_quantity, appearance_ok_quantity,
         function_ok_quantity, performance_ok_quantity, cost_price,
         material_components_json, semifinished_components_json, serials_json,
         applicant_id, project_code, maker_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            acceptance_no,
            product_name,
            "",
            1,
            "个",
            today,
            1,
            0,
            1,
            1,
            1,
            0,
            "[]",
            "[]",
            json.dumps([{"serial_no": acceptance_no, "qualified": True}], ensure_ascii=False),
            1,
            project_code,
            maker_id,
            now,
            now,
        ),
    )
    acceptance_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO finished_good_inventory
        (acceptance_id, product_name, spec, unit, quantity, cost_price,
         serial_no, acceptance_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            acceptance_id,
            product_name,
            "",
            "个",
            1,
            0,
            f"CP{acceptance_id}",
            today,
            now,
            now,
        ),
    )
    db.commit()
    return acceptance_id


def _next_borrow_form_no(cursor):
    today = app_module.today_text().replace("-", "")
    like = f"JY{today}%"
    cursor.execute(
        "SELECT form_no FROM workflow_forms WHERE form_no LIKE ? ORDER BY form_no DESC LIMIT 1",
        (like,),
    )
    row = cursor.fetchone()
    if row:
        serial = int(str(row[0])[-2:]) + 1
    else:
        serial = 1
    return f"JY{today}{serial:02d}"


def _seed_borrow_workflow(db, material_name, spec=""):
    cursor = db.cursor()
    now = app_module.now_text()
    form_no = _next_borrow_form_no(cursor)
    cursor.execute(
        """
        INSERT INTO workflow_forms
        (form_no, form_type, title, status, current_step, applicant_id, created_at, updated_at)
        VALUES (?, 'borrow', ?, 'completed', 'completed', ?, ?, ?)
        """,
        (form_no, "测试借用", 1, now, now),
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_items
        (form_id, material_id, material_code, material_name, spec, unit, request_quantity, outbound_quantity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (form_id, None, "", material_name, spec, "个", 1, 1),
    )
    db.commit()
    return form_id


def test_acceptance_with_project_code(client, db):
    _login(client)
    _seed_claimed_material(db)
    resp = client.post(
        "/api/production/semifinished-acceptance",
        json={
            "name": "半品A",
            "acceptance_quantity": 1,
            "qualified_quantity": 1,
            "unqualified_quantity": 0,
            "appearance_ok_quantity": 1,
            "function_ok_quantity": 1,
            "performance_ok_quantity": 1,
            "project_code": "TEST-001",
            "components": [{"material_id": 1, "batch_id": 1, "per_unit_quantity": 1}],
            "serials": [{"serial_no": "BY2026010101", "qualified": True}],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    cursor = db.cursor()
    cursor.execute("SELECT project_code FROM semifinished_acceptances WHERE id = ?", (data["acceptance"]["id"],))
    row = cursor.fetchone()
    assert row[0] == "TEST-001"


def test_acceptance_without_project_code(client, db):
    _login(client)
    _seed_claimed_material(db)
    resp = client.post(
        "/api/production/semifinished-acceptance",
        json={
            "name": "半品B",
            "acceptance_quantity": 1,
            "qualified_quantity": 1,
            "unqualified_quantity": 0,
            "appearance_ok_quantity": 1,
            "function_ok_quantity": 1,
            "performance_ok_quantity": 1,
            "components": [{"material_id": 1, "batch_id": 1, "per_unit_quantity": 1}],
            "serials": [{"serial_no": "BY2026010102", "qualified": True}],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    cursor = db.cursor()
    cursor.execute("SELECT project_code FROM semifinished_acceptances WHERE id = ?", (data["acceptance"]["id"],))
    row = cursor.fetchone()
    assert row[0] == ""


def test_acceptance_with_maker_id(client, db):
    _login(client)
    _seed_claimed_material(db)
    maker_id = _seed_maker(db, "制造者A")
    resp = client.post(
        "/api/production/finished-acceptance",
        json={
            "product_name": "成品A",
            "acceptance_quantity": 1,
            "qualified_quantity": 1,
            "unqualified_quantity": 0,
            "appearance_ok_quantity": 1,
            "function_ok_quantity": 1,
            "performance_ok_quantity": 1,
            "maker_id": maker_id,
            "material_components": [{"material_id": 1, "batch_id": 1, "per_unit_quantity": 1}],
            "serials": [{"serial_no": "CY2026010101", "qualified": True}],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    cursor = db.cursor()
    cursor.execute("SELECT maker_id FROM finished_acceptances WHERE id = ?", (data["acceptance"]["id"],))
    row = cursor.fetchone()
    assert row[0] == maker_id


def test_borrow_filter_by_maker(client, db):
    maker_id = _seed_maker(db, "制造者B")
    _seed_semifinished_acceptance(db, "半品C", project_code="", maker_id=maker_id)
    _seed_borrow_workflow(db, "半品C")
    _login(client)

    resp = client.get("/api/workflows?type=borrow&maker=制造者B")
    assert resp.status_code == 200
    forms = resp.get_json()
    assert len(forms) == 1


def test_borrow_filter_by_maker_no_match(client, db):
    maker_id = _seed_maker(db, "制造者C")
    _seed_semifinished_acceptance(db, "半品D", project_code="", maker_id=maker_id)
    _seed_borrow_workflow(db, "半品D")
    _login(client)

    resp = client.get("/api/workflows?type=borrow&maker=不存在")
    assert resp.status_code == 200
    forms = resp.get_json()
    assert len(forms) == 0


def test_borrow_filter_by_project(client, db):
    _seed_semifinished_acceptance(db, "半品E", project_code="PROJ-E")
    _seed_borrow_workflow(db, "半品E")
    _login(client)

    resp = client.get("/api/workflows?type=borrow&project=PROJ-E")
    assert resp.status_code == 200
    forms = resp.get_json()
    assert len(forms) == 1


def test_borrow_filter_combined(client, db):
    maker_id = _seed_maker(db, "制造者D")
    _seed_semifinished_acceptance(db, "半品F", project_code="PROJ-F", maker_id=maker_id)
    _seed_borrow_workflow(db, "半品F")
    _login(client)

    resp = client.get("/api/workflows?type=borrow&maker=制造者D&project=PROJ-F")
    assert resp.status_code == 200
    forms = resp.get_json()
    assert len(forms) == 1

    resp = client.get("/api/workflows?type=borrow&maker=制造者E&project=PROJ-F")
    assert resp.status_code == 200
    forms = resp.get_json()
    assert len(forms) == 0


def test_inventory_search_by_project_code(client, db):
    _seed_semifinished_acceptance(db, "半品G", project_code="ABC")
    _seed_semifinished_acceptance(db, "半品H", project_code="DEF")
    _login(client)

    resp = client.get("/api/production/semifinished?project_code=ABC")
    assert resp.status_code == 200
    data = resp.get_json()
    acceptances = data["acceptances"]
    assert len(acceptances) == 1
    assert acceptances[0]["project_code"] == "ABC"


def test_inventory_search_no_filter(client, db):
    _seed_semifinished_acceptance(db, "半品I", project_code="GHI")
    _seed_semifinished_acceptance(db, "半品J", project_code="JKL")
    _login(client)

    resp = client.get("/api/production/semifinished")
    assert resp.status_code == 200
    data = resp.get_json()
    acceptances = data["acceptances"]
    assert len(acceptances) == 2


def test_inventory_search_no_results(client, db):
    _seed_semifinished_acceptance(db, "半品K", project_code="MNO")
    _login(client)

    resp = client.get("/api/production/semifinished?project_code=NONEXISTENT")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["acceptances"] == []


def test_inventory_search_finished(client, db):
    _seed_finished_acceptance(db, "成品B", project_code="XYZ")
    _seed_finished_acceptance(db, "成品C", project_code="ZZZ")
    _login(client)

    resp = client.get("/api/production/finished?project_code=XYZ")
    assert resp.status_code == 200
    data = resp.get_json()
    acceptances = data["acceptances"]
    assert len(acceptances) == 1
    assert acceptances[0]["project_code"] == "XYZ"


def test_cjk_search(client, db):
    _seed_semifinished_acceptance(db, "半品L", project_code="测试项目")
    _login(client)

    resp = client.get("/api/production/semifinished?project_code=测试")
    assert resp.status_code == 200
    data = resp.get_json()
    acceptances = data["acceptances"]
    assert len(acceptances) == 1
    assert acceptances[0]["project_code"] == "测试项目"
