import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import pytest

@pytest.mark.parametrize("operation, explicit, expected", [
    ("load_project", None, 120.0), ("get_state_summary", None, 30.0),
    ("exec_js", None, 30.0), ("load_project", 7.0, 7.0),
])
def test_scene_load_budget_does_not_delay_other_browser_operations(monkeypatch, operation, explicit, expected):
    waited = []
    class Completed:
        def wait(self, *, timeout):
            waited.append(timeout); return True
    class Queue:
        def put(self, command):
            command[3]["result"] = {"success": True}
    monkeypatch.setattr(server.threading, "Event", Completed)
    monkeypatch.setattr(server, "_browser_cmd_q", Queue())
    kwargs = {} if explicit is None else {"timeout": explicit}
    assert server._browser_call(operation, **kwargs) == {"success": True}
    assert waited == [expected]
