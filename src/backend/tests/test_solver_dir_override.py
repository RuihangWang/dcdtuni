"""DCDTUNI_SOLVER_DIR (opt-in) picks the solver from another build; off by default."""
import os

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def test_override_directory_wins(tmp_path, monkeypatch):
    exe = tmp_path / "buoyantBoussinesqSimpleFoam_new"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("DCDTUNI_SOLVER_DIR", str(tmp_path))
    assert STLGenRunner._resolve_solver_binary("buoyantBoussinesqSimpleFoam_new") == str(exe)


def test_override_without_the_solver_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DCDTUNI_SOLVER_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="DCDTUNI_SOLVER_DIR"):
        STLGenRunner._resolve_solver_binary("buoyantBoussinesqSimpleFoam_new")


def test_default_is_unchanged(monkeypatch):
    monkeypatch.delenv("DCDTUNI_SOLVER_DIR", raising=False)
    path = STLGenRunner._resolve_solver_binary("buoyantBoussinesqSimpleFoam_new")
    assert "DPInt32" in path or os.path.basename(path) == "buoyantBoussinesqSimpleFoam_new"
