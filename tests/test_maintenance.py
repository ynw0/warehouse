from pathlib import Path
import subprocess


def test_maintenance_mode_blocks_writes_but_keeps_reads(monkeypatch, tmp_path, client):
    flag = tmp_path / "runtime" / "warehouse-maintenance.flag"
    monkeypatch.setenv("WAREHOUSE_MAINTENANCE_FLAG", str(flag))

    assert client.get("/api/session").status_code == 200
    assert client.post("/api/login", json={}).status_code != 503

    result = subprocess.run(
        ["sh", "offline_update/warehouse-maintenance", "enable", "--flag", str(flag)],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "enabled:" in result.stdout
    assert flag.is_file()
    assert client.get("/api/session").status_code == 200
    blocked = client.post("/api/login", json={})
    assert blocked.status_code == 503
    assert blocked.get_json()["error"] == "系统维护中，请稍后再试"

    subprocess.run(
        ["sh", "offline_update/warehouse-maintenance", "disable", "--flag", str(flag)],
        check=True,
        text=True,
        capture_output=True,
    )
    assert not flag.exists()
    assert client.post("/api/login", json={}).status_code != 503


def test_maintenance_script_requires_absolute_flag_path():
    script = Path("offline_update/warehouse-maintenance")
    result = subprocess.run(
        ["sh", str(script), "enable", "--flag", "relative.flag"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "绝对路径" in result.stderr


def test_maintenance_script_defaults_to_parent_data_directory(tmp_path):
    package = tmp_path / "warehouse-update"
    script_dir = package / "scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "warehouse-maintenance"
    script.write_text(Path("offline_update/warehouse-maintenance").read_text(), encoding="utf-8")
    flag = tmp_path / "data" / "warehouse-maintenance.flag"

    enabled = subprocess.run(["sh", str(script), "enable"], text=True, capture_output=True)
    assert enabled.returncode == 0
    assert flag.is_file()
    disabled = subprocess.run(["sh", str(script), "disable"], text=True, capture_output=True)
    assert disabled.returncode == 0
    assert not flag.exists()
