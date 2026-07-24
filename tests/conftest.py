import os
import tempfile

import pytest

import app as app_module


def _create_test_users(cursor):
    """Insert warehouse and testuser test accounts."""
    users = [
        ("warehouse", "仓库管理员", "", "test", ["warehouse", "user"]),
        ("testuser", "测试用户", "", "test", ["user"]),
    ]
    for username, display_name, department, password, role_codes in users:
        cursor.execute(
            "INSERT OR IGNORE INTO users (username, display_name, department, password, updated_at) VALUES (?, ?, ?, ?, ?)",
            (username, display_name, department, app_module.generate_password_hash(password), app_module.now_text()),
        )
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_id = cursor.fetchone()[0]
        for role_code in role_codes:
            cursor.execute("SELECT id FROM roles WHERE code = ?", (role_code,))
            role_row = cursor.fetchone()
            if role_row:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    (user_id, role_row[0]),
                )


@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    original_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret-key"

    app_module.init_db()

    conn = app_module.get_db()
    cursor = conn.cursor()
    _create_test_users(cursor)
    conn.commit()
    conn.close()

    yield app_module.app

    app_module.DB_PATH = original_db_path
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()


@pytest.fixture
def db(app):
    """A fresh database connection for the app."""
    conn = app_module.get_db()
    yield conn
    conn.close()
