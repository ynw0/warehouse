# -*- coding: utf-8 -*-

import io
import json

from werkzeug.security import check_password_hash

from warehouse_suit.settings import set_setting


def _login_admin(client):
    response = client.post(
        "/api/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def _enable_strong_password_policy(db):
    set_setting(
        db.cursor(),
        "password_policy",
        json.dumps(
            {
                "min_length": 10,
                "require_digit": True,
                "require_lower": True,
                "require_upper": True,
                "require_symbol": True,
                "force_change_on_first_login": True,
            }
        ),
    )
    db.commit()


def test_new_user_blank_password_uses_default_under_strong_policy(client, db):
    _enable_strong_password_policy(db)
    _login_admin(client)

    response = client.post(
        "/api/system/users",
        json={
            "username": "new_blank_password",
            "display_name": "新建用户",
            "password": "",
            "roles": ["user"],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    row = db.execute(
        "SELECT password, must_change_password FROM users WHERE username = ?",
        ("new_blank_password",),
    ).fetchone()
    assert check_password_hash(row["password"], "123456")
    assert row["must_change_password"] == 1

    rejected = client.post(
        "/api/system/users",
        json={
            "username": "new_explicit_weak_password",
            "display_name": "弱密码用户",
            "password": "123456",
        },
    )
    assert rejected.status_code == 400
    assert "密码长度至少 10 位" in rejected.get_json()["error"]


def test_imported_new_user_blank_password_uses_default_under_strong_policy(client, db):
    _enable_strong_password_policy(db)
    _login_admin(client)
    content = "username,display_name,password,department,roles,is_active\nimport_blank,导入用户,,研发部,user,1\n"
    response = client.post(
        "/api/system/users/import",
        data={"file": (io.BytesIO(content.encode("utf-8")), "users.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["imported"] == 1
    row = db.execute(
        "SELECT password, must_change_password FROM users WHERE username = ?",
        ("import_blank",),
    ).fetchone()
    assert check_password_hash(row["password"], "123456")
    assert row["must_change_password"] == 1
