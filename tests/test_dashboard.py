from pathlib import Path
import json

from warehouse_suit.db import today_text
from warehouse_suit.settings import set_setting, workflow_settings


def _login_as(client, db, username="testuser"):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    user_id = int(cursor.fetchone()[0])
    with client.session_transaction() as session:
        session["user_id"] = user_id


def _set_temporary_inventory(db, enabled):
    cursor = db.cursor()
    settings = workflow_settings(cursor)
    settings["temporary_inventory_enabled"] = enabled
    set_setting(cursor, "workflow_settings", json.dumps(settings, ensure_ascii=False))
    db.commit()


def test_dashboard_route_requires_login_and_renders_live_screen(client, db):
    assert client.get("/").status_code == 302

    _login_as(client, db)
    response = client.get("/")

    assert response.status_code == 200
    workspace = response.get_data(as_text=True)
    assert "static/system.js" in workspace
    assert "css/temporary-inventory.css" in workspace
    system_js = client.get("/static/system.js").get_data(as_text=True)
    assert "/api/dashboard/view" in system_js
    assert "system-dashboard-frame" not in system_js
    assert "/?dashboard=1" not in system_js

    dashboard = client.get("/api/dashboard/view")
    assert dashboard.status_code == 200
    page = dashboard.get_data(as_text=True)
    assert "dashboard-embedded" in page
    assert "物料管理系统数据大屏" in page
    assert "/api/dashboard/overview" not in page
    assert "mockData" not in page
    assert "进入业务工作台" not in page
    assert "dashboardStatus" not in page
    assert "href=\"/?view=todo\" target=\"_top\"" in page
    assert "workspace=1&amp;view=todo" not in page


def test_dashboard_realtime_refresh_keeps_compact_todo_rows():
    system_js = Path("static/system.js").read_text()
    assert "The dashboard owns #todoList" in system_js
    assert 'if (system.view !== "dashboard")' in system_js


def test_dashboard_overview_empty_database_is_safe(client, db):
    _login_as(client, db)

    response = client.get("/api/dashboard/overview")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["total_materials"] == 0
    assert payload["summary"]["total_amount"] == 0.0
    assert payload["inventory"]["total_stock"] == 0
    assert payload["todos"] == []
    assert payload["inventory_check"]["status"] == "unset"
    assert len(payload["trend"]) == 12
    assert [item["month"] for item in payload["trend"]] == sorted(item["month"] for item in payload["trend"])


def test_dashboard_overview_uses_batches_and_hides_temporary_when_disabled(client, db):
    _login_as(client, db)
    _set_temporary_inventory(db, True)
    cursor = db.cursor()
    today = today_text()
    cursor.execute(
        """
        INSERT INTO materials (material_code, name, category_name, unit, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("10000000000001", "仪表板测试物料", "测试分类", "件", today, today),
    )
    material_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, stock_source, inventory_status, received_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (material_id, "DASH-FORMAL", 10, 3, "rd", "formal", "available", today),
    )
    cursor.execute(
        """
        INSERT INTO material_batches
            (material_id, batch_no, quantity, unit_price, warehouse_type, stock_source, inventory_status, received_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (material_id, "DASH-TEMP", 4, 9, "office", "temporary", "available", today),
    )
    cursor.execute(
        """
        INSERT INTO stock_records
            (material_id, operation_type, quantity, balance_after, operation_date, stock_source, business_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (material_id, "in", 10, 10, today, "formal", "manual"),
    )
    cursor.execute(
        """
        INSERT INTO stock_records
            (material_id, operation_type, quantity, balance_after, operation_date, stock_source, business_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (material_id, "out", 2, 8, today, "formal", "manual"),
    )
    db.commit()

    payload = client.get("/api/dashboard/overview").get_json()
    assert payload["settings"]["temporary_warehouse_enabled"] is True
    assert payload["inventory"]["formal_stock"] == 10
    assert payload["inventory"]["temporary_stock"] == 4
    assert payload["inventory"]["total_stock"] == 14
    assert payload["summary"]["total_amount"] == 30.0
    assert payload["summary"]["today_inbound"] == 10
    assert payload["summary"]["today_outbound"] == 2
    assert payload["categories"] == [{"name": "测试分类", "stock_quantity": 14}]

    _set_temporary_inventory(db, False)
    hidden_payload = client.get("/api/dashboard/overview").get_json()
    assert hidden_payload["settings"]["temporary_warehouse_enabled"] is False
    assert hidden_payload["inventory"]["temporary_stock"] == 0
    assert hidden_payload["inventory"]["total_stock"] == 10
    assert hidden_payload["workflow"]["pending_transfer"] == 0
    assert hidden_payload["categories"] == [{"name": "测试分类", "stock_quantity": 10}]


def test_workspace_shell_keeps_local_icons_and_plain_dashboard_counts():
    index = Path("templates/index.html").read_text()
    system_js = Path("static/system.js").read_text()
    dashboard_js = Path("static/js/dashboard.js").read_text()

    assert 'id="icon-clipboard-list"' in index
    assert 'id="icon-settings"' in index
    assert 'class="app-icon-sprite"' in index
    assert 'class="system-shell app-shell"' in system_js
    assert 'class="app-topbar" id="appTopbar"' in system_js
    assert 'class="app-content" id="systemMain"' in system_js
    assert 'class="todo-table flow-table"' in system_js
    assert 'class="handle-button"' in system_js
    assert 'positionMaterialResults(row, list, filtered.length)' in system_js
    assert 'id="aiFloatBtn"' in system_js
    assert 'topbarAiBtn' not in system_js
    assert 'if (hasRole("admin")) return true;' in system_js
    assert 'group.addEventListener("toggle"' in system_js
    for label in (
        "我的借用",
        "半成品验收",
        "成品验收",
        "半成品库",
        "成品库",
        "仓库料卡系统",
        "盘点",
    ):
        assert label in system_js
    assert 'fill="#35d8ff"' in index
    system_css = Path("static/css/system.css").read_text()
    dashboard_css = Path("static/css/dashboard.css").read_text()
    assert '.work-panel {\n  backdrop-filter: none;' in system_css
    assert 'z-index: 1200;' in system_css
    assert '-webkit-mask-image: radial-gradient(circle, transparent 61%, #000 62%);' in dashboard_css
    assert '@supports (-webkit-mask-image: radial-gradient(circle, transparent 61%, #000 62%))' in dashboard_css
    assert 'display: none;\n  animation: dashboard-rotate 24s linear infinite;' in dashboard_css
    assert 'claimOutboundTotalHtml(items)' in system_js
    assert 'item?.data?.consumed_batches' in system_js
    assert 'Number(batch.quantity || 0) * Number(batch.unit_price || 0)' in system_js
    assert 'Intl.NumberFormat' not in dashboard_js
    assert 'String(Math.round(number(value)))' in dashboard_js
    assert 'number(value).toFixed(2)' in dashboard_js
    assert '实时数据 · 每 30 秒刷新' not in dashboard_js
    assert 'dashboardStatus' not in dashboard_js
