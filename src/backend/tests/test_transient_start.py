"""Steady-converged start (transient design §4, plan T4d): the steady result's
internal fields seed the transient case's 0/, whose boundary conditions (tables,
time() expressions) stay as generated."""
from __future__ import annotations

import pytest

from backend.aircooling.runner.transient_start import seed_internal_fields

HEADER = """FoamFile
{{
    version     2.0;
    format      {fmt};
    class       {cls};
    object      {name};
}}
"""


def field(name, internal, boundary, cls="volScalarField", fmt="ascii"):
    return (HEADER.format(fmt=fmt, cls=cls, name=name) + "\ndimensions      [0 0 0 1 0 0 0];\n\n"
            + f"internalField   {internal};\n\nboundaryField\n{{\n{boundary}\n}}\n")


STEADY_T = field("T", "nonuniform List<scalar> \n3\n(\n301.5\n302.25\n(1)\n)\n",
                 "    inlet { type fixedValue; value uniform 288.15; }")
STEADY_U = field("U", "nonuniform List<vector> 3((0.1 0 0) (0.2 0 0) (0.3 0 0))",
                 "    inlet { type fixedValue; value uniform (1 0 0); }", cls="volVectorField")
TRANSIENT_T = field("T", "uniform 295.15",
                    "    inlet\n    {\n        type uniformFixedValue;\n        uniformValue table ((0 288.15) (0.999999 288.15) (1 285.15));\n        value uniform 288.15;\n    }")
TRANSIENT_U = field("U", "uniform (0 0 0)", "    inlet { type flowRateInletVelocity; volumetricFlowRate 4; value uniform (0 0 0); }",
                    cls="volVectorField")


def write(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text)


def test_internal_fields_come_from_the_steady_result_and_boundaries_stay(tmp_path):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    write(steady, {"T": STEADY_T, "U": STEADY_U, "phi": STEADY_PHI})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U, "k": field("k", "uniform 0.01", "")})
    seeded = seed_internal_fields(steady, zero)
    assert seeded == ["T", "U", "phi"]
    t = (zero / "T").read_text()
    assert "301.5" in t and "302.25" in t and "uniform 295.15" not in t
    assert "uniformValue table ((0 288.15) (0.999999 288.15) (1 285.15));" in t
    assert "internalField   nonuniform List<scalar>" in t
    u = (zero / "U").read_text()
    assert "(0.3 0 0)" in u and "flowRateInletVelocity" in u
    assert "uniform 0.01" in (zero / "k").read_text()      # no steady k: left as generated


def test_a_binary_steady_result_is_refused(tmp_path):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    write(steady, {"T": field("T", "uniform 300", "", fmt="binary")})
    write(zero, {"T": TRANSIENT_T})
    with pytest.raises(ValueError, match="ASCII"):
        seed_internal_fields(steady, zero)


def test_nothing_to_seed_is_an_error(tmp_path):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    write(steady, {"p": field("p", "uniform 0", "")})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U})
    with pytest.raises(ValueError, match="T and U"):
        seed_internal_fields(steady, zero)

# A positive time folder proves a write, not SIMPLE residualControl convergence.
def _steady_stage_runner(tmp_path, monkeypatch, log, *, parallel=False, fail=False):
    from backend.aircooling.runner.bridge_bc import BridgeBCRegistry
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    from types import MethodType
    runner = STLGenRunner.__new__(STLGenRunner)
    runner.mesh_dir = tmp_path
    runner._bridge_registry = BridgeBCRegistry()
    runner._data = {"simulationConfig": {"solverType": "transient", "endTime": 10}}
    write(tmp_path / "0", {"T": TRANSIENT_T, "U": TRANSIENT_U})
    mesh = {name: f"dummy {name}" for name in ("points", "faces", "owner", "neighbour", "boundary")}
    mesh["owner"] = 'FoamFile { note "nCells:1"; object owner; }\n'
    write(tmp_path / "constant" / "polyMesh", mesh)
    if parallel:
        for rank in (0, 1):
            write(tmp_path / f"processor{rank}" / "constant" / "polyMesh", mesh)
            write(tmp_path / f"processor{rank}" / "0", {"T": TRANSIENT_T, "U": TRANSIENT_U})
    calls = []
    monkeypatch.setattr(runner, "_setup_complete_case_in_mesh", lambda **kw: None)
    monkeypatch.setattr(runner, "_generate_solver_dicts", lambda path: None)
    monkeypatch.setattr(runner, "_resolve_solver_binary", lambda name: name)
    monkeypatch.setattr(runner, "_decompose_for_solve", lambda path, count: count)
    def run(_self, command, path):
        calls.append(command)
        if "SimpleFoam" in command:
            for case in (list(path.glob("processor*")) if parallel else [path]):
                write(case / "10", {"T": STEADY_T, "U": STEADY_U, "phi": STEADY_PHI})
                write(case / "0", {"T": STEADY_T, "U": STEADY_U})
            # MPI's _run_cmd output is dedicated; serial uses a tee log.
            (path / ("log.mpi_solver" if parallel else "log.steady_start")).write_text(log)
            if fail:
                raise RuntimeError("solver exited with code 1")
        elif "reconstructPar -latestTime" in command:
            write(path / "10", {"T": STEADY_T, "U": STEADY_U, "phi": STEADY_PHI})
    monkeypatch.setattr(runner, "_run_cmd", MethodType(run, runner))
    return runner, calls


def _simple_log(*, pressure=0.0002, second=0.00001, marker=True, ended=True):
    lines = [
        "Exec : buoyantBoussinesqSimpleFoam_new",
        "SIMPLE: convergence criteria",
        "    field p_rgh tolerance 0.001",
        "    field U tolerance 0.001",
        "    field T tolerance 0.001",
        '    field "(k|epsilon|omega)" tolerance 0.001',
        "Time = 10",
    ]
    for name, value in [
        ("Ux", 0.0002), ("Uy", 0.0003), ("Uz", 0.0001), ("T", 0.0002),
        ("p_rgh", pressure), ("p_rgh", second), ("epsilon", 0.0001), ("k", 0.0002),
    ]:
        lines.append(f"PCG: Solving for {name}, Initial residual = {value}, Final residual = 1e-7, No Iterations 2")
    if marker:
        lines.append("SIMPLE solution converged in 10 iterations")
    if ended:
        lines.append("End")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("parallel", [False, True])
def test_endtime_result_cannot_seed_a_steady_converged_start(tmp_path, monkeypatch, parallel):
    runner, calls = _steady_stage_runner(
        tmp_path, monkeypatch, _simple_log(pressure=0.00204921, second=0.000156804, marker=False),
        parallel=parallel)
    with pytest.raises(RuntimeError, match="steady.*converg"):
        runner._run_steady_start(tmp_path, parallel, 2 if parallel else 1, False)
    assert not any("reconstructPar" in call for call in calls)
    assert (tmp_path / "0" / "T").read_text() == TRANSIENT_T
    assert not (tmp_path / "0.transient").exists()
    assert not (tmp_path / "steadyStart").exists()
    result_case = tmp_path / "processor0" if parallel else tmp_path
    assert (result_case / "10" / "T").is_file()


def test_steady_solver_exception_restores_transient_zero(tmp_path, monkeypatch):
    runner, _ = _steady_stage_runner(tmp_path, monkeypatch, _simple_log(), fail=True)
    with pytest.raises(RuntimeError, match="exited"):
        runner._run_steady_start(tmp_path, False, 1, False)
    assert (tmp_path / "0" / "T").read_text() == TRANSIENT_T
    assert not (tmp_path / "0.transient").exists()


@pytest.mark.parametrize("parallel,retain", [(False, False), (True, False), (True, True)])
def test_verified_steady_start_seeds_and_records_strict_evidence(tmp_path, monkeypatch, parallel, retain):
    runner, calls = _steady_stage_runner(tmp_path, monkeypatch, _simple_log(), parallel=parallel)
    report = runner._run_steady_start(tmp_path, parallel, 2 if parallel else 1, retain)
    assert report["convergence"]["verdict"] == "STEADY_CONVERGED"
    assert report["convergence"]["converged_iteration"] == 10
    assert report["convergence"]["first_initial_residuals"]["p_rgh"] == 0.0002
    assert report["convergence"]["first_initial_residuals"]["U"] == 0.0003
    case = tmp_path / "processor0" if parallel else tmp_path
    assert "301.5" in (case / "0" / "T").read_text()
    assert "uniformValue table" in (case / "0" / "T").read_text()
    assert not (tmp_path / "0.transient").exists()
    archive = tmp_path / "steadyStart" / "processor0" if parallel else tmp_path / "steadyStart"
    assert (archive / "10" / "T").is_file()
    assert report["actual_n_procs"] == (2 if parallel else 1)
    assert any("reconstructPar" in call for call in calls) == (parallel and not retain)
    assert runner._data["simulationConfig"] == {"solverType": "transient", "endTime": 10}


def test_seed_failure_rolls_back_already_modified_fields(tmp_path, monkeypatch):
    runner, _ = _steady_stage_runner(tmp_path, monkeypatch, _simple_log())
    import backend.aircooling.runner.transient_start as module
    def fail(source, zero):
        (zero / "T").write_text("partly seeded")
        raise ValueError("invalid steady field")
    monkeypatch.setattr(module, "seed_internal_fields", fail)
    with pytest.raises(ValueError, match="invalid steady field"):
        runner._run_steady_start(tmp_path, False, 1, False)
    assert (tmp_path / "0" / "T").read_text() == TRANSIENT_T
    assert not (tmp_path / "0.transient").exists()


@pytest.mark.parametrize("log,reason", [
    (_simple_log(pressure=0.00204921, second=0.000156804), "p_rgh first"),
    (_simple_log(pressure=0.001), "not below"),
    (_simple_log(marker=False), "residualControl was not reached"),
    (_simple_log(ended=False), "did not end cleanly"),
    (_simple_log().replace("converged in 10", "converged in 11"), "does not match completed"),
    (_simple_log().replace("for Uy, Initial residual = 0.0003", "for Uy, Initial residual = 0.002"), "U first"),
    (_simple_log().replace("for epsilon, Initial residual = 0.0001", "for epsilon, Initial residual = 0.002"), "epsilon first"),
    (_simple_log().replace("Final residual = 1e-7", "Final residual = nan", 1), "finite residuals"),
    (_simple_log().replace("field T tolerance 0.001", "field T tolerance nan"), "invalid residualControl"),
    (_simple_log().replace("field T tolerance 0.001", "field T tolerance 0"), "invalid residualControl"),
    (_simple_log().replace("field T tolerance 0.001", ""), "missing the required"),
    (_simple_log().replace("End", "FOAM FATAL ERROR"), "did not end cleanly"),
    (_simple_log().replace("buoyantBoussinesqSimpleFoam_new", "buoyantBoussinesqPimpleFoam"), "unexpected solver"),
    (_simple_log() + "Time = 11\nEnd\n", "does not match completed"),
])
def test_strict_start_guard_rejects_unproved_or_inconsistent_evidence(tmp_path, log, reason):
    from backend.aircooling.runner.transient_start import require_steady_convergence
    evidence = tmp_path / "log.steady_start"
    evidence.write_text(log)
    with pytest.raises(RuntimeError, match=reason):
        require_steady_convergence(evidence)


def test_start_uses_tolerance_loaded_by_the_solver(tmp_path):
    from backend.aircooling.runner.transient_start import require_steady_convergence
    evidence = tmp_path / "log.steady_start"
    evidence.write_text(_simple_log(pressure=0.0002).replace("field p_rgh tolerance 0.001", "field p_rgh tolerance 0.0001"))
    with pytest.raises(RuntimeError, match="residualControl 0.0001"):
        require_steady_convergence(evidence)


def test_missing_steady_start_log_is_refused(tmp_path):
    from backend.aircooling.runner.transient_start import require_steady_convergence
    with pytest.raises(RuntimeError, match="missing solver evidence"):
        require_steady_convergence(tmp_path / "absent.log")


def test_failed_layout_preparation_cannot_resurrect_a_stale_partition(tmp_path, monkeypatch):
    runner, calls = _steady_stage_runner(tmp_path, monkeypatch, _simple_log(), parallel=True)
    import shutil
    def fail(path, count):
        shutil.rmtree(path / "processor0")
        raise RuntimeError("decomposition failed")
    monkeypatch.setattr(runner, "_decompose_for_solve", fail)
    with pytest.raises(RuntimeError, match="decomposition failed"):
        runner._run_steady_start(tmp_path, True, 2, True)
    assert (tmp_path / "0" / "T").read_text() == TRANSIENT_T
    # Layout preparation precedes the final-layout backup. Restoring this old
    # rank after failed repartitioning would create fields without its mesh.
    assert not (tmp_path / "processor0").exists()
    assert not any("SimpleFoam" in call for call in calls)
    assert not list(tmp_path.rglob("0.transient"))
    assert not (tmp_path / ".steady-start-backup").exists()


STEADY_PHI = field("phi", "nonuniform List<scalar> 2(0.5 -0.5)",
    "    inlet { type calculated; value uniform -0.5; }\n"
    "    outlet { type calculated; value uniform 0.5; }\n"
    "    paired { type cyclic; }", cls="surfaceScalarField")


def test_steady_seed_copies_pressure_corrected_flux_with_its_boundary_values(tmp_path):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    write(steady, {"T": STEADY_T, "U": STEADY_U, "phi": STEADY_PHI})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U})
    seeded = seed_internal_fields(steady, zero)
    assert "phi" in seeded
    assert (zero / "phi").read_text() == STEADY_PHI
    assert "uniformValue table" in (zero / "T").read_text()


def test_steady_seed_requires_pressure_corrected_flux(tmp_path):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    write(steady, {"T": STEADY_T, "U": STEADY_U})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U})
    with pytest.raises(ValueError, match="phi"):
        seed_internal_fields(steady, zero)


@pytest.mark.parametrize("cls,fmt", [("volScalarField", "ascii"), ("surfaceScalarField", "binary")])
def test_steady_seed_rejects_invalid_flux_identity(tmp_path, cls, fmt):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    write(steady, {"T": STEADY_T, "U": STEADY_U,
                  "phi": field("phi", "uniform 0", "", cls=cls, fmt=fmt)})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U})
    with pytest.raises(ValueError, match="phi"):
        seed_internal_fields(steady, zero)


def test_steady_flux_cannot_be_copied_between_mesh_partitions(tmp_path):
    steady, zero = tmp_path / "processor0" / "1000", tmp_path / "processor1" / "0"
    write(steady, {"T": STEADY_T, "U": STEADY_U, "phi": STEADY_PHI})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U})
    with pytest.raises(ValueError, match="same mesh partition"):
        seed_internal_fields(steady, zero)
    assert (zero / "U").read_text() == TRANSIENT_U


def test_steady_flux_header_location_is_zero_without_changing_flux(tmp_path):
    steady, zero = tmp_path / "1000", tmp_path / "0"
    phi = STEADY_PHI.replace("    object", '    location "1000";\n    object')
    write(steady, {"T": STEADY_T, "U": STEADY_U, "phi": phi})
    write(zero, {"T": TRANSIENT_T, "U": TRANSIENT_U})
    seed_internal_fields(steady, zero)
    assert (zero / "phi").read_text() == phi.replace('location "1000";', 'location    "0";')
