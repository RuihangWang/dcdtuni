"""A rotated mpirun log is named after the step that WROTE it.

It used to be tagged with the step about to start, so the steady stage of a
steady-converged transient start (T4) was kept as
`log.mpi_solver.01_buoyantBoussinesqPimpleF` — the SIMPLE output under the
PIMPLE name.
"""
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _runner() -> STLGenRunner:
    return STLGenRunner.__new__(STLGenRunner)


def test_the_rotated_log_carries_the_application_it_records(tmp_path):
    log = tmp_path / "log.mpi_solver"
    log.write_text("/*---*/\nBuild  : v2412\nExec   : buoyantBoussinesqSimpleFoam_new -parallel\nDate   : x\n")
    _runner()._rotate_mpi_log(log, "mpirun -np 8 buoyantBoussinesqPimpleFoam -parallel")
    assert [p.name for p in tmp_path.glob("log.mpi_solver.*")] == ["log.mpi_solver.01_buoyantBoussinesqSimpleF"]


def test_without_an_exec_line_the_previous_command_names_it(tmp_path):
    runner = _runner()
    log = tmp_path / "log.mpi_solver"
    runner._rotate_mpi_log(log, "mpirun -np 4 redistributePar -parallel")   # no log yet: nothing to keep
    log.write_text("redistributePar output without a banner\n")
    runner._rotate_mpi_log(log, "mpirun -np 4 snappyHexMesh -parallel")
    assert [p.name for p in tmp_path.glob("log.mpi_solver.*")] == ["log.mpi_solver.01_redistributePar"]
