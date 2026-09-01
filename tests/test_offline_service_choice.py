import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "offline_update"))
spec = importlib.util.spec_from_file_location("offline_service_choice", ROOT / "offline_update" / "interactive.py")
interactive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interactive)


def test_multiple_services_require_explicit_validated_selection():
    candidates = [
        {"kind": "project-scripts", "stop": "true", "start": "true", "status": "true"},
        {"kind": "systemd", "stop": "true", "start": "true", "status": "true"},
    ]
    messages = []
    selected = interactive.choose_service(candidates, input_fn=lambda _: "2", output=messages.append)
    assert selected == candidates[1]
    assert any("多个服务" in message for message in messages)


def test_multiple_services_can_be_cancelled_safely():
    candidates = [{"kind": "one", "stop": "true", "start": "true", "status": "true"}, {"kind": "two", "stop": "true", "start": "true", "status": "true"}]
    assert interactive.choose_service(candidates, input_fn=lambda _: "q") is None


def test_loaded_default_systemd_service_is_preferred(tmp_path, monkeypatch):
    for name in ("stop.sh", "start_background.sh", "status.sh"):
        (tmp_path / name).write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(
        interactive.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="loaded\n", stderr=""),
    )
    candidates = interactive.detect_service(tmp_path)
    assert candidates == [
        {
            "kind": "systemd",
            "stop": "systemctl stop warehouse-suite.service",
            "start": "systemctl start warehouse-suite.service",
            "status": "systemctl is-active warehouse-suite.service",
        }
    ]
