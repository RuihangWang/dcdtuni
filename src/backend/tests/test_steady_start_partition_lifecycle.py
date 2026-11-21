"""The steady and transient stages must own one unchanged final mesh layout."""
import shutil
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from backend.aircooling.runner.bridge_bc import BridgeBCRegistry
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256


def _mesh(case, identity):
    root = case / "constant/polyMesh"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (root / name).write_text(f"{identity} {name}\n")
    (root / "owner").write_text(f'FoamFile {{ note "nCells:1"; object owner; }}\n// {identity}\n')


def _field(name, value, patch, kind="volScalarField"):
    return (f"FoamFile {{ format ascii; class {kind}; object {name}; }}\n"
            f"internalField uniform {value};\nboundaryField {{ {patch} "
            "{ type fixedValue; value uniform 1; } }\n")


def _write_fields(case, time, *, steady):
    zero = case / time
    zero.mkdir(parents=True, exist_ok=True)
    patch = "final_" + case.name
    (zero / "T").write_text(_field("T", "301" if steady else "293", patch))
    (zero / "U").write_text(_field("U", "(1 0 0)" if steady else "(0 0 0)",
                                  patch, "volVectorField"))
    if steady:
        (zero / "phi").write_text(_field("phi", ".25", patch, "surfaceScalarField"))


def _converged_log():
    lines = ["Exec : buoyantBoussinesqSimpleFoam_new", "SIMPLE: convergence criteria"]
    for name in ("p_rgh", "U", "T"):
        lines.append(f"    field {name} tolerance 0.001")
    lines.append("Time = 10")
    for name in ("Ux", "Uy", "Uz", "T", "p_rgh"):
        lines.append(f"PCG: Solving for {name}, Initial residual = .0001, Final residual = 1e-8, No Iterations 2")
    return "\n".join(lines + ["SIMPLE solution converged in 10 iterations", "End", ""])


def _runner(tmp_path, monkeypatch, source, requested, actual, *, mutate_mesh=False):
    runner = STLGenRunner.__new__(STLGenRunner)
    runner.mesh_dir = tmp_path
    registry = BridgeBCRegistry()
    runner._bridge_registry = registry
    runner._data = {"simulationConfig": {"solverType": "transient", "endTime": 10}}
    events = []
    source_cases = [tmp_path] if source == 1 else [tmp_path / f"processor{i}" for i in range(source)]
    for case in source_cases:
        _mesh(case, "old_" + case.name)
        _write_fields(case, "0", steady=False)

    def decompose(path, count):
        events.append(("decompose", count))
        assert count == requested
        for case in path.glob("processor*"):
            shutil.rmtree(case)
        _mesh(path, "final_serial")
        if actual > 1:
            for rank in range(actual):
                _mesh(path / f"processor{rank}", f"final_{rank}")
        return actual

    def setup(**kwargs):
        cases = ([tmp_path / f"processor{i}" for i in range(actual)]
                 if kwargs.get("prefer_partitions") else [tmp_path])
        events.append(("setup", "transient" if runner._bridge_registry is registry else "steady",
                       tuple(case.name for case in cases)))
        # Real setup prefers an existing serial root unless explicitly directed
        # to the final MPI layout. Regeneration then deletes those processors.
        if not kwargs.get("prefer_partitions") and (tmp_path / "constant/polyMesh/faces").exists():
            for case in tmp_path.glob("processor*"):
                shutil.rmtree(case)
        for case in cases:
            _write_fields(case, "0", steady=False)
            for name in ("phi", "phi.gz"):
                (case / "0" / name).unlink(missing_ok=True)

    def command(cmd, path):
        events.append(("solver", cmd))
        if "SimpleFoam" in cmd:
            cases = ([path / f"processor{i}" for i in range(actual)]
                     if cmd.startswith("mpirun ") else [path])
            for case in cases:
                _write_fields(case, "10", steady=True)
            (path / ("log.mpi_solver" if cmd.startswith("mpirun ") else "log.steady_start")).write_text(_converged_log())
            if mutate_mesh:
                (cases[0] / "constant/polyMesh/faces").write_text("unexpected remesh")
        elif cmd.startswith("reconstructPar "):
            _write_fields(path, "10", steady=True)

    monkeypatch.setattr(runner, "_decompose_for_solve", decompose)
    monkeypatch.setattr(runner, "_setup_complete_case_in_mesh", setup)
    monkeypatch.setattr(runner, "_generate_solver_dicts", lambda path: None)
    monkeypatch.setattr(runner, "_resolve_solver_binary", lambda value: value)
    monkeypatch.setattr(runner, "_run_cmd", command)
    return runner, events


@pytest.mark.parametrize("source,requested,actual", [
    (1, 1, 1), (1, 2, 2), (2, 4, 4), (4, 2, 2), (8, 4, 4), (2, 1, 1), (2, 4, 2),
])
def test_steady_start_finalizes_layout_before_backing_up_and_seeding(tmp_path, monkeypatch, source, requested, actual):
    runner, events = _runner(tmp_path, monkeypatch, source, requested, actual)
    report = runner._run_steady_start(tmp_path, requested > 1, requested, True)

    assert events[0] == ("decompose", requested)
    assert report["actual_n_procs"] == actual
    cases = [tmp_path] if actual == 1 else [tmp_path / f"processor{i}" for i in range(actual)]
    expected = {str(case.relative_to(tmp_path)) or ".": mesh_identity_sha256(case) for case in cases}
    assert report["mesh_identities"] == expected
    assert set(report["cases"]) == set(expected)
    assert len([event for event in events if event[0] == "decompose"]) == 1
    assert len(list(tmp_path.glob("processor*"))) == (actual if actual > 1 else 0)
    for case in cases:
        assert "internalField uniform 301" in (case / "0/T").read_text()
        assert "final_" + case.name in (case / "0/U").read_text()
        archived = tmp_path / "steadyStart" / case.relative_to(tmp_path) / "10/phi"
        assert (case / "0/phi").read_text() == archived.read_text()
    assert runner.mesh_dir == tmp_path
    assert runner._data["simulationConfig"] == {"solverType": "transient", "endTime": 10}


def test_changed_mesh_after_simple_cannot_seed_pimple(tmp_path, monkeypatch):
    runner, _ = _runner(tmp_path, monkeypatch, 1, 2, 2, mutate_mesh=True)
    with pytest.raises(RuntimeError, match="mesh.*changed"):
        runner._run_steady_start(tmp_path, True, 2, True)
    for rank in range(2):
        assert "internalField uniform 293" in (tmp_path / f"processor{rank}/0/T").read_text()
        assert not (tmp_path / f"processor{rank}/0/phi").exists()


def test_solve_stage_reuses_actual_steady_partition_without_redecomposition(tmp_path, monkeypatch):
    from backend.tests.test_stlgen_air_run_receipt_integration import _runner as receipt_runner, _write_time
    runner = receipt_runner(tmp_path, monkeypatch, parallel=True)
    runner._data["simulationConfig"].update(solverType="transient", solveProcess=4)
    runner._resolved = SimpleNamespace(transient={"start": {"kind": "steadyConverged"}})
    monkeypatch.setattr(runner, "_solver_for_simulation_config", lambda cfg: "buoyantBoussinesqPimpleFoam_new")
    monkeypatch.setattr(runner, "_run_steady_start",
                        lambda *args: {"actual_n_procs": 2, "mesh_identities": {}})

    def unexpected_decompose(*args):
        pytest.fail("the seeded steady layout must not be decomposed again")
    monkeypatch.setattr(runner, "_decompose_for_solve", unexpected_decompose)
    commands = []
    def solve(command, path):
        commands.append(command)
        for rank in range(2):
            _write_time(path / f"processor{rank}", "0.1")
    monkeypatch.setattr(runner, "_run_cmd", solve)
    runner._run_solve_from_mesh()
    assert len(commands) == 1
    assert commands[0].startswith("mpirun -np 2 buoyantBoussinesqPimpleFoam")
    assert commands[0].endswith(" -parallel")


def test_setup_can_generate_final_partitions_without_destroying_retained_root(tmp_path, monkeypatch):
    import backend.aircooling.runner.stlgen_runner as module
    runner = STLGenRunner.__new__(STLGenRunner)
    runner.mesh_dir = tmp_path
    runner._data = {}
    for case in [tmp_path, tmp_path / "processor0", tmp_path / "processor1"]:
        _mesh(case, case.name)
        (case / "constant/polyMesh/boundary").write_text("wall { type wall; nFaces 1; }")
    generated = []
    monkeypatch.setattr(runner, "_build_field_generator", lambda: SimpleNamespace(
        generate_fields=lambda case, patches, **kwargs: generated.append(case)))
    monkeypatch.setattr(runner, "_ensure_wall_baffles_are_wall_type", lambda path: None)
    monkeypatch.setattr(runner, "_reconcile_boundary_wall_types", lambda *args: None)
    monkeypatch.setattr(runner, "_build_partition_hole_fvoptions_config", lambda: {})
    monkeypatch.setattr(module, "fix_cyclic_match_tolerance", lambda path: None)
    monkeypatch.setattr(module, "read_boundary_patches", lambda path: [("wall", "wall")])
    monkeypatch.setattr(module, "generate_fv_options", lambda *args: None)
    def constants(path, **kwargs):
        for name in ("g", "transportProperties", "turbulenceProperties", "thermophysicalProperties"):
            (path / "constant" / name).write_text("current " + name)
    monkeypatch.setattr(module, "copy_system_files_for_solver", constants)
    monkeypatch.setattr(runner, "_clean_processor_dirs",
                        lambda path: pytest.fail("final MPI partitions must survive"))
    runner._setup_complete_case_in_mesh(strict=True, prefer_partitions=True)
    assert generated == [tmp_path / "processor0", tmp_path / "processor1"]
    assert (tmp_path / "constant/polyMesh/faces").exists()
    for rank in (0, 1):
        for name in ("g", "transportProperties", "turbulenceProperties", "thermophysicalProperties"):
            assert (tmp_path / f"processor{rank}/constant" / name).read_text() == "current " + name


@pytest.mark.parametrize("missing", [True, False])
def test_every_final_rank_must_supply_the_same_steady_snapshot(tmp_path, monkeypatch, missing):
    runner, _ = _runner(tmp_path, monkeypatch, 1, 2, 2)
    original_command = runner._run_cmd
    def command(cmd, path):
        original_command(cmd, path)
        if "SimpleFoam" in cmd:
            last = path / "processor1/10"
            if missing:
                shutil.rmtree(last)
            else:
                last.rename(path / "processor1/11")
    monkeypatch.setattr(runner, "_run_cmd", command)
    with pytest.raises(RuntimeError, match="no result|one result time"):
        runner._run_steady_start(tmp_path, True, 2, True)
    for rank in (0, 1):
        assert "internalField uniform 293" in (tmp_path / f"processor{rank}/0/T").read_text()
        assert not (tmp_path / f"processor{rank}/0/phi").exists()


def test_a_late_rank_seed_error_rolls_back_all_final_rank_fields(tmp_path, monkeypatch):
    import backend.aircooling.runner.transient_start as module
    runner, _ = _runner(tmp_path, monkeypatch, 1, 2, 2)
    original_seed = module.seed_internal_fields
    def seed(source, zero):
        if zero.parent.name == "processor1":
            raise ValueError("last-rank flux is invalid")
        return original_seed(source, zero)
    monkeypatch.setattr(module, "seed_internal_fields", seed)
    with pytest.raises(ValueError, match="last-rank"):
        runner._run_steady_start(tmp_path, True, 2, True)
    for rank in (0, 1):
        assert "internalField uniform 293" in (tmp_path / f"processor{rank}/0/T").read_text()
        assert not (tmp_path / f"processor{rank}/0/phi").exists()
    assert not (tmp_path / ".steady-start-backup").exists()
