from flask import render_template

def test_app_starts(client):
    """Smoke test: the app responds on the root route."""
    response = client.get("/")
    # Root may redirect to login (302) or render index (200)
    assert response.status_code in (200, 302)


def test_login_page(client):
    """Smoke test: the login page renders."""
    response = client.get("/login")
    assert response.status_code == 200


def test_login_api(client):
    """Smoke test: the API login endpoint accepts valid credentials."""
    response = client.post(
        "/api/login",
        json={"username": "admin", "password": "Costar@508"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["user"]["username"] == "admin"

def _render_index(app, path, material_system):
    with app.test_request_context(path):
        return render_template("index.html", material_system=material_system, static_version=1)


def test_card_mode_restores_card_topbar(app):
    system_home = _render_index(app, "/", True)
    assert '<header class="topbar">' not in system_home
    assert "仓库料卡系统" not in system_home
    assert "static/system.js" in system_home

    card_page = _render_index(app, "/?card=1", True)
    assert '<header class="topbar">' in card_page
    assert "仓库料卡系统" in card_page
    assert "查找物料" in card_page
    assert "static/system.js" not in card_page

