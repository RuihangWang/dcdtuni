"""Transient solver selection must require the repo-owned pressure-gauge build."""
from pathlib import Path
import pytest
from backend.aircooling.runner.stlgen_runner import STLGenRunner

PIMPLE="buoyantBoussinesqPimpleFoam_new"


def test_transient_default_selects_repo_owned_component_solver(monkeypatch):
    monkeypatch.delenv("DCDTUNI_SOLVER_OVERRIDE",raising=False)
    monkeypatch.setattr(STLGenRunner,"_resolve_solver_binary",lambda name:name)
    assert STLGenRunner._solver_for_simulation_config({"solverType":"transient"}) == PIMPLE


@pytest.mark.parametrize("config,environment,expected", [
    ({"solverType":"steady"},None,"buoyantBoussinesqSimpleFoam_new"),
    ({"solverType":"transient","solverName":"explicitSolver"},None,"explicitSolver"),
    ({"solverType":"transient","solverName":"explicitSolver"},"envSolver","envSolver"),
])
def test_user_override_precedence_stays_explicit(monkeypatch,config,environment,expected):
    monkeypatch.delenv("DCDTUNI_SOLVER_OVERRIDE",raising=False)
    if environment:
        monkeypatch.setenv("DCDTUNI_SOLVER_OVERRIDE",environment)
    monkeypatch.setattr(STLGenRunner,"_resolve_solver_binary",lambda name:name)
    assert STLGenRunner._solver_for_simulation_config(config) == expected


def test_missing_pimple_new_build_fails_before_mpi_launch(tmp_path,monkeypatch):
    monkeypatch.setattr(STLGenRunner,"_CUSTOM_SOLVER_DIR",tmp_path/"missing")
    with pytest.raises(FileNotFoundError,match="buoyantBoussinesqPimpleFoam_new.*not built"):
        STLGenRunner._resolve_solver_binary(PIMPLE)


def test_pimple_new_build_resolves_to_exact_repo_executable(tmp_path,monkeypatch):
    executable=tmp_path/"platforms/linux64GccDPInt32Opt/bin"/PIMPLE
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setattr(STLGenRunner,"_CUSTOM_SOLVER_DIR",tmp_path/"platforms")
    assert STLGenRunner._resolve_solver_binary(PIMPLE) == str(executable)
