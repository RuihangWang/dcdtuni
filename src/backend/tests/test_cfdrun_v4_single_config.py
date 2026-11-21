"""cfdrun v4: ONE simulationConfig — the root (E8a, user ruling 2026-10-01).

v2/v3 carry simulationConfig twice (root + the legacy meshGeometry blob the runner
executes from). v4 drops the blob copy: the runner derives the executed config
from the root with ``derive_sim_config`` — the resolver the golden parity tests
already hold byte-exact against the blob — taking room temperature and the
monitoring config from the root too.
"""
import copy
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.derived_config import execution_blob, shadow_drift
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests.test_cfdrun_reader import _minimal_manifest
from backend.tests.test_derived_config_parity import ALL_NAMED, _is_current_format, _load

FIXTURE = Path(__file__).resolve().parent / "fixtures/cfdrun_v2_full/Base_leakage.cfdrun"


def as_v4(raw: dict) -> dict:
    """The v2 manifest the editor would now export as v4: the blob copy moves to the root."""
    out = copy.deepcopy(raw)
    blob_sc = out["meshGeometry"].pop("simulationConfig")
    out["schemaVersion"] = 4
    out["simulationConfig"]["roomTemperatureC"] = blob_sc["roomTemperature"]
    out["simulationConfig"]["monitoring"] = blob_sc.get("monitoring", {})
    return out


@pytest.mark.parametrize("case", ALL_NAMED)
def test_v4_executes_what_the_v2_blob_executed_for_every_golden(case):
    raw, mg = _load(case)
    if not _is_current_format(mg.get("meshConfig") or {}):
        pytest.skip(f"{case}: stale export (predates meshMode)")
    assert execution_blob(as_v4(raw))["simulationConfig"] == mg["simulationConfig"]


def test_the_shadow_compare_has_no_second_simulation_config_in_v4():
    raw, _ = _load("Slab")
    assert "simulationConfig" not in shadow_drift(as_v4(raw))


def test_v2_and_v3_keep_executing_the_blob():
    raw, mg = _load("Slab")
    assert execution_blob(raw)["simulationConfig"] is not None
    assert execution_blob(raw)["simulationConfig"] == mg["simulationConfig"]


def test_v4_refuses_a_second_copy_and_needs_the_room_temperature():
    raw, _ = _load("Slab")
    doubled = as_v4(raw)
    doubled["meshGeometry"]["simulationConfig"] = raw["meshGeometry"]["simulationConfig"]
    with pytest.raises(ValueError, match="one simulationConfig"):
        execution_blob(doubled)
    missing = as_v4(raw)
    missing["simulationConfig"]["roomTemperatureC"] = None
    with pytest.raises(ValueError, match="roomTemperatureC"):
        execution_blob(missing)


def make_runner(tmp_path, raw):
    model = tmp_path / "case" / "model"
    model.mkdir(parents=True)
    manifest = model / "Base_leakage.cfdrun"
    manifest.write_text(json.dumps(raw))
    return STLGenRunner(tmp_path / "case", manifest)


def test_the_runner_loads_a_v4_manifest_to_the_same_config(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    v2 = make_runner(tmp_path / "v2", raw)
    v2._load_cfd_json_only()
    v4 = make_runner(tmp_path / "v4", as_v4(raw))
    v4._load_cfd_json_only()
    assert v4._data["simulationConfig"] == v2._data["simulationConfig"]
    assert v4._data["meshConfig"] == v2._data["meshConfig"]


@pytest.mark.parametrize("loader", ("_load_cfd_json_only", "_load"))
def test_both_runner_loaders_refuse_a_doubled_v4(tmp_path, loader):
    raw = json.loads(FIXTURE.read_text())
    doubled = as_v4(raw)
    doubled["meshGeometry"]["simulationConfig"] = raw["meshGeometry"]["simulationConfig"]
    runner = make_runner(tmp_path, doubled)
    with pytest.raises(ValueError, match="one simulationConfig"):
        getattr(runner, loader)()
    assert runner._data is None


def test_the_schema_takes_v4_steady_or_transient_and_refuses_v5():
    steady = _minimal_manifest(schemaVersion=4)
    steady.setdefault("simulationConfig", {})["roomTemperatureC"] = 22
    missing = _minimal_manifest(schemaVersion=4)
    with pytest.raises(ValueError, match="roomTemperatureC"):
        CfdrunV2.model_validate(missing)
    assert CfdrunV2.model_validate(steady).schemaVersion == 4
    transient = copy.deepcopy(steady)
    transient["simulationConfig"].update(solverType="transient", endTimeSeconds=60)
    transient["transient"] = {"durationS": 60, "start": {"kind": "uniform", "uniform": {"airT_C": 22, "waterT_C": 30, "solidT_C": 40}}}
    assert CfdrunV2.model_validate(transient).transient.durationS == 60
    scheduled = copy.deepcopy(steady)
    scheduled["boundaryBindings"] = {"bb": {"patchName": "inlet", "bcType": "acu_supply",
                                            "parameters": {"temperatureC": 15, "schedules": {"temperatureC": [[0, 15], [10, 12]]}}}}
    with pytest.raises(ValueError, match="transient block"):
        CfdrunV2.model_validate(scheduled)
    with pytest.raises(ValueError, match="re-export"):
        CfdrunV2.model_validate(_minimal_manifest(schemaVersion=5))

@pytest.mark.parametrize("seconds", [None, 5, 60.0001])
def test_v4_refuses_solver_duration_that_disagrees_with_timeline(seconds):
    raw = _minimal_manifest(schemaVersion=4, simulationConfig={"roomTemperatureC":22, "solverType":"transient", "endTimeSeconds":seconds},
                            transient={"durationS":60, "start":{"kind":"uniform"}})
    with pytest.raises(ValueError, match="endTimeSeconds.*durationS"):
        CfdrunV2.model_validate(raw)
    with pytest.raises(ValueError, match="endTimeSeconds.*durationS"):
        execution_blob(raw)


def test_v4_timeline_requires_transient_solver_and_executes_exact_subsecond_duration(tmp_path):
    raw = _minimal_manifest(schemaVersion=4, simulationConfig={"roomTemperatureC":22, "solverType":"transient", "endTimeSeconds":.005, "writeIntervalSeconds":.001},
                            transient={"durationS":.005, "start":{"kind":"uniform"}})
    assert CfdrunV2.model_validate(raw).transient.durationS == .005
    sc = execution_blob(raw)["simulationConfig"]
    from backend.aircooling.runner.openfoam_case import generate_control_dict
    generate_control_dict(tmp_path, sc)
    assert "endTime         0.005;" in (tmp_path/"system"/"controlDict").read_text()
    raw["simulationConfig"]["solverType"] = "steady"
    with pytest.raises(ValueError, match="transient solver"):
        CfdrunV2.model_validate(raw)
    with pytest.raises(ValueError, match="transient solver"):
        execution_blob(raw)
