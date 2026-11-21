"""A missing solver build must be reported, not discovered as exit 127.

The steady solve defaults to buoyantBoussinesqSimpleFoam_new, which only exists
once third_parties/foam/deploy.sh has built it into platforms/<WM_OPTIONS>/bin/.
On a fresh clone platforms/ does not exist at all, and _resolve_solver_binary
used to return the bare name WITHOUT a warning in that case; the parallel solve then
aborted because mpirun could not find the binary. The "is not built"
warning only fired when platforms/ existed but held no binary.
"""

from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from backend.aircooling.runner.stlgen_runner import STLGenRunner

SOLVER = "buoyantBoussinesqSimpleFoam_new"


@pytest.fixture
def warnings_log():
    """loguru does not feed pytest's caplog; collect WARNING+ messages directly."""
    messages: list = []
    sink_id = loguru_logger.add(lambda msg: messages.append(str(msg)), level="WARNING")
    try:
        yield messages
    finally:
        loguru_logger.remove(sink_id)


def _not_built_warnings(messages: list, name: str) -> list:
    return [
        m for m in messages
        if f"{name} is not built" in m and "third_parties/foam/deploy.sh" in m
    ]


def test_missing_platforms_dir_warns(tmp_path, monkeypatch, warnings_log):
    """Fresh clone: platforms/ was never created."""
    platforms = tmp_path / "platforms"
    assert not platforms.exists()
    monkeypatch.setattr(STLGenRunner, "_CUSTOM_SOLVER_DIR", platforms)

    assert STLGenRunner._resolve_solver_binary(SOLVER) == SOLVER
    assert _not_built_warnings(warnings_log, SOLVER), warnings_log


def test_empty_platforms_dir_warns(tmp_path, monkeypatch, warnings_log):
    """platforms/ exists (e.g. a half-finished build) but holds no solver."""
    platforms = tmp_path / "platforms"
    (platforms / "linux64GccDPInt32Opt" / "bin").mkdir(parents=True)
    monkeypatch.setattr(STLGenRunner, "_CUSTOM_SOLVER_DIR", platforms)

    assert STLGenRunner._resolve_solver_binary(SOLVER) == SOLVER
    assert _not_built_warnings(warnings_log, SOLVER), warnings_log


def test_built_solver_is_used_without_warning(tmp_path, monkeypatch, warnings_log):
    bin_dir = tmp_path / "platforms" / "linux64GccDPInt32Opt" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / SOLVER
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(STLGenRunner, "_CUSTOM_SOLVER_DIR", tmp_path / "platforms")

    assert STLGenRunner._resolve_solver_binary(SOLVER) == str(exe)
    assert not [m for m in warnings_log if "is not built" in m], warnings_log


@pytest.mark.parametrize("platforms_exists", [False, True])
@pytest.mark.parametrize("name", ["buoyantBoussinesqPimpleFoam", "writeExplicitBC"])
def test_non_new_names_resolve_quietly(tmp_path, monkeypatch, warnings_log, platforms_exists, name):
    """Only *_new solvers are ours to demand. Stock solvers come from PATH, and
    writeExplicitBC's caller emits its own, more specific warning."""
    platforms = tmp_path / "platforms"
    if platforms_exists:
        platforms.mkdir()
    monkeypatch.setattr(STLGenRunner, "_CUSTOM_SOLVER_DIR", platforms)

    assert STLGenRunner._resolve_solver_binary(name) == name
    assert not [m for m in warnings_log if "is not built" in m], warnings_log
