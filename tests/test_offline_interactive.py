"""Updater tests use a temporary installation and never touch repository data."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "offline_update"))
spec = importlib.util.spec_from_file_location("offline_interactive", ROOT / "offline_update" / "interactive.py")
interactive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interactive)


def _installation(tmp_path):
    root = tmp_path / "warehouse-suite"
    (root / "warehouse_suit").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / "app.py").write_text("# app\n", encoding="utf-8")
    (root / "VERSION").write_text("2026.7.7\n", encoding="utf-8")
    (root / "warehouse_suit" / "__init__.py").write_text("__version__ = '2026.7.7'\n", encoding="utf-8")
    python = root / ".venv" / "bin" / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    db = root / "data" / "warehouse.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE materials (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE shelves (id INTEGER PRIMARY KEY)")
    conn.commit(); conn.close()
    for name in ("backups", "reports"):
        (root / name).mkdir()
    return root


def test_validation_and_first_release_initialisation_are_data_safe(tmp_path, monkeypatch):
    root = _installation(tmp_path)
    package = tmp_path / "package"
    package.mkdir()
    (package / "package_manifest.json").write_text(json.dumps({
        "target_application_version": "2026.7.8", "supported_from_versions": ["2026.7.7"],
        "migration_versions": ["2026071501"]}), encoding="utf-8")
    monkeypatch.setattr(interactive, "PACKAGE_DIR", package)
    monkeypatch.setattr(interactive, "_python_version", lambda _: "3.8.10")
    info = interactive.validate_install(root, allow_test=True)
    assert info["layout"] == "direct"
    interactive._copy_direct_release(info)
    assert (root / "current").is_symlink()
    assert (root / "current").resolve() == root / "releases" / "2026.7.7"
    assert (root / "data" / "warehouse.db").is_file()
    assert not (root / "releases" / "2026.7.7" / "data").exists()
    assert not (root / "releases" / "2026.7.7" / ".venv").exists()


def test_invalid_or_quit_input_makes_no_change(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    (package / "package_manifest.json").write_text(json.dumps({
        "target_application_version": "2026.7.8", "supported_from_versions": ["2026.7.7"],
        "migration_versions": ["2026071501"]}), encoding="utf-8")
    monkeypatch.setattr(interactive, "PACKAGE_DIR", package)
    output = []
    assert interactive.main([], input_fn=lambda _: "q", output=output.append) == 0
    assert any("未修改" in line for line in output)
