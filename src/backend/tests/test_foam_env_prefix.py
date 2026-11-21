"""The OpenFOAM shell preamble puts the environment's libraries next to the meshers' own.

A mesher binary built elsewhere (the evaluator export ships one) has an rpath that names no directory of this
environment, so OpenFOAM's libraries and the MPI Pstream must come from LD_LIBRARY_PATH.
"""
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def test_mesher_library_path_includes_environment(tmp_path: Path, monkeypatch):
    platforms = tmp_path / "platforms"
    (platforms / "linux64GccDPInt32Opt" / "lib").mkdir(parents=True)
    monkeypatch.setattr(STLGenRunner, "_CUSTOM_MESHER_DIR", platforms)
    monkeypatch.setattr(STLGenRunner, "_conda_prefix", staticmethod(lambda: "/opt/env"))
    runner = STLGenRunner.__new__(STLGenRunner)
    preamble = runner._foam_env_prefix()
    exports = [part for part in preamble.split(" && ") if part.startswith('export LD_LIBRARY_PATH="')]
    last = exports[-1]
    assert f"{platforms}/linux64GccDPInt32Opt/lib:/opt/env/lib:/opt/env/lib/sys-mpich:" in last


def test_no_environment_no_change(tmp_path: Path, monkeypatch):
    platforms = tmp_path / "platforms"
    (platforms / "linux64GccDPInt32Opt" / "lib").mkdir(parents=True)
    monkeypatch.setattr(STLGenRunner, "_CUSTOM_MESHER_DIR", platforms)
    monkeypatch.setattr(STLGenRunner, "_conda_prefix", staticmethod(lambda: None))
    runner = STLGenRunner.__new__(STLGenRunner)
    assert "/lib/sys-mpich" not in runner._foam_env_prefix()
