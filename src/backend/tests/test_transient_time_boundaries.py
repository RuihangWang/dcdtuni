"""Physical timeline breakpoints must reach the native timestep controller."""
import math
import re
from types import SimpleNamespace

import pytest

from backend.aircooling.runner.openfoam_case import generate_control_dict
from backend.aircooling.runner import convergence as cv
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _events(text):
    found = re.search(r"dcTransientEvents\s*\(([^)]*)\)\s*;", text)
    assert found, "transient controlDict has no native timeline event interface"
    return [float(x) for x in found.group(1).split()]


def test_original_corners_union_preserves_steps_without_table_epsilon(tmp_path):
    from backend.aircooling.runner.transient_time import time_events_from_registry
    registry = {
        "supply": SimpleNamespace(timeVarying={
            "supplyTemp": [{"t": 0, "value": 22}, {"t": .003, "value": 22},
                           {"t": .003, "value": 24}, {"t": .006, "value": 24}],
            "velocity": [{"t": 0, "value": [0, 0, 1]}, {"t": .002, "value": [0, 1, 0]}],
        }),
        "server": SimpleNamespace(timeVarying={
            "powerRows": [{"t": 0, "value": 1000}, {"t": .002, "value": 1000},
                          {"t": .004, "value": 1200}],
        }),
    }
    original = [row["t"] for row in registry["supply"].timeVarying["supplyTemp"]]
    events = time_events_from_registry(registry)
    assert events == [0, .002, .003, .004, .006]
    assert [row["t"] for row in registry["supply"].timeVarying["supplyTemp"]] == original
    generate_control_dict(tmp_path, {"solverType": "transient", "endTimeSeconds": .007},
                          transient_events=events)
    assert _events((tmp_path / "system/controlDict").read_text()) == [.002, .003, .004, .006]


def test_steady_stage_does_not_expose_timeline_events(tmp_path):
    generate_control_dict(tmp_path, {"solverType": "steady"}, transient_events=[.002])
    assert "dcTransientEvents" not in (tmp_path / "system/controlDict").read_text()


def test_legacy_transient_has_explicit_empty_event_list(tmp_path):
    generate_control_dict(tmp_path, {"solverType": "transient"})
    assert _events((tmp_path / "system/controlDict").read_text()) == []


@pytest.mark.parametrize("bad", [True, "0.001", math.nan, math.inf, -.001, .008])
def test_invalid_or_out_of_duration_events_are_refused(tmp_path, bad):
    with pytest.raises(ValueError, match="event"):
        generate_control_dict(tmp_path, {"solverType": "transient", "endTimeSeconds": .007},
                              transient_events=[bad])


def test_runner_passes_all_active_registry_corners_to_writer(tmp_path, monkeypatch):
    import backend.aircooling.runner.stlgen_runner as module
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._data = {"simulationConfig": {"solverType": "transient", "endTimeSeconds": .007}}
    runner._bridge_registry = {
        "server": SimpleNamespace(timeVarying={
            "powerRows": [{"t": 0, "value": 1000}, {"t": .003, "value": 1000},
                          {"t": .003, "value": 1200}],
        }),
    }
    runner._extract_sensor_points = lambda: []
    seen = {}
    monkeypatch.setattr(module, "generate_control_dict",
                        lambda *a, **kw: seen.update(kw))
    monkeypatch.setattr(module, "generate_fv_solution", lambda *a, **kw: None)
    monkeypatch.setattr(module, "generate_fv_schemes", lambda *a, **kw: None)
    runner._generate_solver_dicts(tmp_path)
    assert seen["transient_events"] == [0, .003]


@pytest.mark.parametrize("final,end_time", [(.028, .026), (.000002, .000001), (.02, .025)])
def test_completion_requires_matching_terminal_time_in_both_directions(tmp_path, final, end_time):
    (tmp_path / "system").mkdir()
    (tmp_path / "system/controlDict").write_text(
        f"application buoyantBoussinesqPimpleFoam_new;\nendTime {end_time};\n")
    log = cv.SolverLog()
    log.iterations = [final]
    log.ended = True
    verdict = cv.transient_verdict(log, tmp_path)
    assert not verdict["passed"]
    assert any("terminal" in reason for reason in verdict["reasons"])
