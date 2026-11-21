"""Explicit pressure settings reach the actual solver dictionaries, preserving preset defaults."""
import copy
import re
import pytest
from backend.aircooling.runner.cfdrun_schema import CfdrunV2, CFDExportSimulationConfig, PressureLinearSolverConfig
from backend.aircooling.runner.derived_config import execution_blob
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.aircooling.runner.openfoam_case import generate_fv_solution
from backend.tests.test_cfdrun_reader import _minimal_manifest


def pressure_block(text):
    start = re.search(r"\bp_rgh\s*\{", text).end()
    level = 1
    for at in range(start, len(text)):
        level += (text[at] == "{") - (text[at] == "}")
        if level == 0:
            return text[start:at]
    raise AssertionError("unterminated pressure dictionary")


@pytest.mark.parametrize("mode,preconditioner,tolerance", [("steady","GAMG",1e-6),("transient","DIC",1e-8)])
def test_no_block_preserves_each_mode_preset(tmp_path, mode, preconditioner, tolerance):
    generate_fv_solution(tmp_path, solver_type=mode)
    p = pressure_block((tmp_path/"fvSolution").read_text())
    assert re.search(r"preconditioner\s+"+preconditioner+r";",p)
    # Use the last tolerance/relTol: the GAMG preconditioner has its own fixed settings.
    assert float(re.findall(r"(?<!\w)tolerance\s+(\S+);",p)[-1]) == tolerance
    assert float(re.findall(r"(?<!\w)relTol\s+(\S+);",p)[-1]) == .01
    assert ("maxIter" in p) == (mode == "steady")


@pytest.mark.parametrize("mode,choice", [("steady","DIC"),("transient","GAMG")])
def test_v4_loader_derived_config_and_runner_reach_native_pressure_controls(tmp_path, mode, choice):
    cfg = {"preconditioner":choice, "tolerance":2e-9,"relTol":0.001,"maxIter":401}
    raw = _minimal_manifest(schemaVersion=4, simulationConfig={"roomTemperatureC":22,"solverType":mode,"pressureLinearSolver":cfg})
    before = copy.deepcopy(raw)
    model = CfdrunV2.model_validate(raw)
    assert model.simulationConfig.pressureLinearSolver.model_dump(exclude_none=True) == cfg
    sc = CFDExportSimulationConfig.model_validate(execution_blob(raw)["simulationConfig"]).model_dump()
    assert sc["pressureLinearSolver"] == cfg
    assert raw == before
    runner = object.__new__(STLGenRunner)
    runner._data = {"simulationConfig":sc}
    runner._bridge_registry = {}
    runner._extract_sensor_points = lambda: []
    runner._generate_solver_dicts(tmp_path)
    text = (tmp_path/"system"/"fvSolution").read_text()
    p = pressure_block(text)
    assert re.search(r"preconditioner\s+"+choice+r";",p)
    assert float(re.findall(r"(?<!\w)tolerance\s+(\S+);",p)[-1]) == 2e-9
    assert float(re.findall(r"(?<!\w)relTol\s+(\S+);",p)[-1]) == .001
    assert re.search(r"maxIter\s+401;",p)
    assert "p_rghFinal" in text and "relTol          0;" in text


@pytest.mark.parametrize("cfg", [{"preconditioner":"unknown"},{"tolerance":0},{"tolerance":float("inf")},{"tolerance":True},{"tolerance":"1e-8"},{"relTol":-1},{"relTol":1},{"relTol":float("nan")},{"maxIter":0},{"maxIter":1.5},{"maxIter":True},{"extra":4}])
def test_invalid_pressure_controls_are_rejected_before_emitting_a_dictionary(tmp_path,cfg):
    with pytest.raises(ValueError):
        PressureLinearSolverConfig.model_validate(cfg)
    with pytest.raises(ValueError):
        generate_fv_solution(tmp_path, solver_type="transient", pressure_linear_solver=cfg)
    assert not (tmp_path/"fvSolution").exists()


def test_optional_clears_use_defaults_and_zero_relative_tolerance_is_not_lost(tmp_path):
    cfg = {"preconditioner":None,"tolerance":None,"relTol":0,"maxIter":None}
    generate_fv_solution(tmp_path, solver_type="transient",pressure_linear_solver=cfg)
    p = pressure_block((tmp_path/"fvSolution").read_text())
    assert "preconditioner  DIC;" in p
    assert float(re.findall(r"(?<!\w)relTol\s+(\S+);",p)[-1]) == 0


def test_steady_simple_is_written_as_the_solver_runs_it(tmp_path, monkeypatch):
    # The template wrote "consistent yes" while the solver read no such key:
    # every steady run was SIMPLE, so that is the default it now states.
    monkeypatch.delenv("DCDTUNI_SIMPLEC", raising=False)
    generate_fv_solution(tmp_path)
    assert "consistent  no;" in (tmp_path / "fvSolution").read_text()
    generate_fv_solution(tmp_path, simplec=True)
    assert "consistent  yes;" in (tmp_path / "fvSolution").read_text()
    monkeypatch.setenv("DCDTUNI_SIMPLEC", "1")
    generate_fv_solution(tmp_path)
    assert "consistent  yes;" in (tmp_path / "fvSolution").read_text()
