import importlib.util
import sys
from pathlib import Path


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
