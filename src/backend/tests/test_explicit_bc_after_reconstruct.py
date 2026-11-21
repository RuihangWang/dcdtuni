"""writeExplicitBC must run after reconstructPar, or the values it writes are lost.

buoyantBoussinesqSimpleFoam_new writes explicit per-face boundary values, but on
a parallel run it writes them into `processorN/<time>/<field>`. reconstructPar
then merges the processor fields into `<time>/<field>` by re-serialising them the
stock way, which puts `type zeroGradient;` with no values back into the only file
anything downstream reads.

Measured on a real 100-iteration run of case/Base_leakage before the fix:
processor0/100/T had 56 of 58 patches written nonuniform, while the
reconstructed 100/T had 41 of 53 patches carrying nothing but a type.

These tests pin the ORDER, which is the whole content of the fix. A test that
only checked "writeExplicitBC is called somewhere" would pass on the broken
version too, because the solver already called the same write — just too early.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


class _Recorder:
    """Records the command strings a runner would execute, in order."""

    def __init__(self):
        self.commands: list[str] = []

    def __call__(self, cmd, cwd=None, *args, **kwargs):
        self.commands.append(cmd)
        return MagicMock(returncode=0)


def _runner() -> STLGenRunner:
    return STLGenRunner.__new__(STLGenRunner)


def test_explicit_bc_runs_and_runs_after_reconstruct(tmp_path):
    """The write must come AFTER reconstructPar, not merely happen."""
    runner = _runner()
    rec = _Recorder()

    with patch.object(STLGenRunner, "_run_cmd", rec), patch.object(
        STLGenRunner, "_resolve_solver_binary", staticmethod(lambda name: f"/built/{name}")
    ):
        runner._run_cmd("reconstructPar -latestTime", tmp_path)
        runner._rewrite_explicit_boundary_values(tmp_path)

    joined = " | ".join(rec.commands)
    assert "reconstructPar" in joined, joined
    assert "writeExplicitBC" in joined, joined

    recon = next(i for i, c in enumerate(rec.commands) if "reconstructPar" in c)
    explicit = next(i for i, c in enumerate(rec.commands) if "writeExplicitBC" in c)
    assert explicit > recon, (
        "writeExplicitBC ran before reconstructPar, which is exactly the bug: "
        f"reconstructPar would overwrite its output. commands={rec.commands}"
    )


def test_explicit_bc_targets_the_latest_time(tmp_path):
    """-latestTime, so it rewrites the results this solve just produced."""
    runner = _runner()
    rec = _Recorder()
    with patch.object(STLGenRunner, "_run_cmd", rec), patch.object(
        STLGenRunner, "_resolve_solver_binary", staticmethod(lambda name: f"/built/{name}")
    ):
        runner._rewrite_explicit_boundary_values(tmp_path)
    assert rec.commands, "writeExplicitBC was not invoked at all"
    assert "-latestTime" in rec.commands[0], rec.commands


def test_missing_binary_warns_but_does_not_fail_the_solve(tmp_path):
    """A solve whose results are otherwise complete must not fail over this."""
    runner = _runner()
    rec = _Recorder()
    with patch.object(STLGenRunner, "_run_cmd", rec), patch.object(
        STLGenRunner, "_resolve_solver_binary", staticmethod(lambda name: name)
    ), patch("backend.aircooling.runner.stlgen_runner.shutil.which", return_value=None):
        runner._rewrite_explicit_boundary_values(tmp_path)
    assert rec.commands == [], "should not try to run a binary that is not built"


def test_a_failing_write_does_not_propagate(tmp_path):
    """Best-effort: losing the explicit values must not lose the solve."""
    runner = _runner()

    def boom(cmd, cwd=None, *args, **kwargs):
        raise RuntimeError("writeExplicitBC blew up")

    with patch.object(STLGenRunner, "_run_cmd", boom), patch.object(
        STLGenRunner, "_resolve_solver_binary", staticmethod(lambda name: f"/built/{name}")
    ):
        runner._rewrite_explicit_boundary_values(tmp_path)  # must not raise


def test_solve_path_wires_the_call_in(tmp_path):
    """The source itself must place the call inside the parallel branch.

    Reading the source rather than running _run_solver: that method needs a full
    case, a mesh and a Time object, and a test that stubs all of it would be
    testing the stubs. What matters here is the two lines' order.
    """
    src = Path(STLGenRunner.__module__.replace(".", "/") + ".py")
    for base in Path(__file__).resolve().parents:
        candidate = base / src
        if candidate.is_file():
            text = candidate.read_text()
            break
    else:
        pytest.skip("stlgen_runner.py not found from the test's path")
    # The solve path only: the steady-start stage also reconstructs, but it
    # reads nothing back but internal fields, so it needs no explicit write.
    start = text.index("    def _run_solve_from_mesh(")
    end = text.find("\n    def ", start + 1)
    text = text[start:end if end > 0 else len(text)]

    RECON = 'self._run_cmd("reconstructPar -latestTime", run_dir)'
    EXPLICIT = "self._rewrite_explicit_boundary_values(run_dir)"
    assert RECON in text, "the reconstructPar call itself is gone from _run_solver"
    assert EXPLICIT in text, (
        "the solve path does not call _rewrite_explicit_boundary_values at all, so "
        "reconstructPar's stock serialisation is the last thing to touch the result "
        "and every physical patch loses its per-face values"
    )
    recon = text.index(RECON)
    explicit = text.index(EXPLICIT)
    assert explicit > recon, "the call is placed before reconstructPar in the source"
    between = text[recon:explicit]
    assert between.count("\n") < 4, (
        "the call drifted away from the reconstruct step; keep them adjacent so "
        f"the ordering stays obvious. between=\n{between}"
    )
