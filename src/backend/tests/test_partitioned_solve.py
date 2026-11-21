"""Retained dcCart partitions are solver inputs and readable result outputs."""

import json
from pathlib import Path
import shutil

import pytest

from backend.aircooling.runner.fields import FieldGenerator, STANDARD_FIELD_NAMES
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _mesh(case: Path, *, inlet_faces=1, outlet_faces=1, processor=False):
    mesh = case / "constant" / "polyMesh"
    mesh.mkdir(parents=True, exist_ok=True)
    for name in ("points", "faces", "owner", "neighbour"):
        (mesh / name).write_text("mesh data\n")
    (mesh / "owner").write_text('FoamFile { note "nCells:1"; object owner; }\n')
    patches = [("server_inlet_patch", "patch", inlet_faces),
               ("server_outlet_patch", "patch", outlet_faces)]
    if processor:
        patches.append(("procBoundary", "processorCyclic", 1))
    (mesh / "boundary").write_text("\n".join(
        f"    {name}\n    {{\n        type {kind};\n        nFaces {count};\n        startFace 0;\n    }}"
        for name, kind, count in patches
    ))
    return mesh


def _runner(tmp_path, monkeypatch):
    runner = STLGenRunner.__new__(STLGenRunner)
    runner.case_dir = tmp_path
    runner.mesh_dir = tmp_path / "Mesh"
    runner.mesh_dir.mkdir()
    model = tmp_path / "model"
    model.mkdir()
    runner.cfdrun_path = model / "input.cfdrun"
    runner.cfdrun_path.write_text(json.dumps({
        "runId": "partition-solve-fixture",
        "sourceProject": {"contentHash": "a" * 64},
    }))
    runner._data = {"meshConfig": {"meshEngine": "dcCart"},
                    "simulationConfig": {"parallelSolve": True, "solveProcess": 2}}
    gen = FieldGenerator(case_dir=tmp_path, simulation_config={}, room_config={})
    gen._data_collected = True
    gen._imported_bc_data = {
        "server_inlet_patch": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "server_outlet_patch": {"bc_type": "fan_outlet", "flow_rate": 0.1, "power": 300},
    }
    monkeypatch.setattr(runner, "_build_field_generator", lambda: gen)
    monkeypatch.setattr(runner, "_ensure_wall_baffles_are_wall_type", lambda path: None)
    monkeypatch.setattr(runner, "_build_partition_hole_fvoptions_config", lambda: {})
    monkeypatch.setattr(runner, "_clear_mesh_budget", lambda: None)
    monkeypatch.setattr(runner, "_generate_solver_dicts", lambda path: None)
    monkeypatch.setattr(runner, "_auto_harvest_sensors", lambda: None)
    monkeypatch.setattr(runner, "_resolve_solver_binary", lambda name: "solver_new")
    monkeypatch.setattr(runner, "_solver_for_simulation_config", lambda cfg: "solver_new")
    from backend.tests.test_air_mesh_quality_reuse import mock_completed_strict_quality
    mock_completed_strict_quality(runner, monkeypatch)
    return runner


def _write_result_fields(root: Path):
    time_dir = root / "100"
    time_dir.mkdir(parents=True)
    for name, klass, dimensions, value in (
        ("T", "volScalarField", "[0 0 0 1 0 0 0]", "300"),
        ("U", "volVectorField", "[0 1 -1 0 0 0 0]", "(0.1 0 0)"),
    ):
        (time_dir / name).write_text(
            f"FoamFile {{ class {klass}; object {name}; }}\n"
            f"dimensions {dimensions};\ninternalField uniform {value};\n"
        )


def test_partition_setup_keeps_mesh_and_resolves_remote_inlet(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    first = _mesh(runner.mesh_dir / "processor0", inlet_faces=0, processor=True)
    second = _mesh(runner.mesh_dir / "processor1", outlet_faces=0, processor=True)
    runner._setup_complete_case_in_mesh()

    for mesh in (first, second):
        assert (mesh / "faces").read_text() == "mesh data\n"
        fields = mesh.parents[1] / "0"
        for field in STANDARD_FIELD_NAMES:
            assert (fields / field).is_file()
            assert "type            processorCyclic;" in (fields / field).read_text()
        assert "weightAverage(T)" in (fields / "T").read_text()
        assert "flowRateOutletVelocity" in (fields / "U").read_text()
        assert "noSlip" not in (fields / "U").read_text()
    assert not (runner.mesh_dir / "constant" / "polyMesh").exists()
    assert (runner.mesh_dir / "constant" / "g").is_file()
    assert (runner.mesh_dir / "system" / "fvSchemes").is_file()


def test_partition_setup_rejects_globally_empty_paired_inlet(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    for rank in range(2):
        _mesh(runner.mesh_dir / f"processor{rank}", inlet_faces=0)
    with pytest.raises(RuntimeError, match="paired inlet patch .* missing"):
        runner._setup_complete_case_in_mesh()
    assert (runner.mesh_dir / "processor0" / "constant" / "polyMesh" / "faces").is_file()


def test_matching_partition_solve_skips_copy_artifacts_and_reconstruction(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    for rank in range(2):
        _mesh(runner.mesh_dir / f"processor{rank}")
        for relative in ("0/T", "200/T", "VTK/old.vtm", "log.checkMesh"):
            artifact = runner.mesh_dir / f"processor{rank}" / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("old artifact\n")
    for relative in ("constant/triSurface/room.stl", "VTK/large.vtm", "50/U", "log.dcCartMesh"):
        artifact = runner.mesh_dir / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("old artifact\n")
    for relative in ("solverIncludes/flow.table", "customLibrary.so", "processor0/userInput"):
        custom_input = runner.mesh_dir / relative
        custom_input.parent.mkdir(parents=True, exist_ok=True)
        custom_input.write_text("custom solver input\n")
    commands = []
    def command(cmd, path):
        commands.append(cmd)
        if cmd.startswith("mpirun"):
            for rank in (0, 1):
                _write_result_fields(path / f"processor{rank}")

    monkeypatch.setattr(runner, "_run_cmd", command)
    monkeypatch.setattr(runner, "_rewrite_explicit_boundary_values",
                        lambda path: pytest.fail("direct results must preserve solver-written BCs"))

    result = runner._run_solve_from_mesh()

    assert commands == ["mpirun -np 2 solver_new -parallel"]
    assert (result / "processor0" / "0" / "T").is_file()
    assert (result / "processor1" / "constant" / "polyMesh" / "faces").is_file()
    for relative in ("solverIncludes/flow.table", "customLibrary.so", "processor0/userInput"):
        assert (result / relative).read_text() == "custom solver input\n"
    for relative in ("constant/triSurface", "VTK", "50", "log.dcCartMesh",
                     "processor0/200", "processor1/VTK", "processor1/log.checkMesh"):
        assert not (result / relative).exists()
        assert (runner.mesh_dir / relative).exists()


@pytest.mark.parametrize("n_procs", [1, 3])
def test_changed_solver_count_reconstructs_only_run_copy(tmp_path, monkeypatch, n_procs):
    runner = _runner(tmp_path, monkeypatch)
    for rank in range(2):
        _mesh(runner.mesh_dir / f"processor{rank}")
    runner._data["simulationConfig"]["solveProcess"] = n_procs
    commands = []

    def command(cmd, case):
        commands.append(cmd)
        if cmd.startswith("reconstructParMesh"):
            _mesh(case)
        elif cmd == "decomposePar":
            for rank in range(n_procs):
                _mesh(case / f"processor{rank}")
                shutil.copytree(case / "0", case / f"processor{rank}" / "0")
        elif cmd.startswith("mpirun"):
            for rank in range(n_procs):
                _write_result_fields(case / f"processor{rank}")
        elif cmd == "solver_new":
            _write_result_fields(case)

    monkeypatch.setattr(runner, "_run_cmd", command)
    result = runner._run_solve_from_mesh()
    assert commands[0] == "reconstructParMesh -constant"
    if n_procs == 1:
        assert commands == ["reconstructParMesh -constant", "solver_new"]
        assert (result / "constant" / "polyMesh" / "faces").is_file()
        assert not (result / "processor0").exists()
    else:
        assert commands == ["reconstructParMesh -constant", "decomposePar",
                            "mpirun -np 3 solver_new -parallel"]
        assert not (result / "constant" / "polyMesh").exists()
        assert (result / "processor2" / "0" / "T").is_file()
    assert not (runner.mesh_dir / "constant" / "polyMesh").exists()
    assert (runner.mesh_dir / "processor1" / "constant" / "polyMesh" / "faces").is_file()


def test_serial_snappy_mesh_preserves_result_reconstruction(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    runner._data["meshConfig"]["meshEngine"] = "snappy"
    _mesh(runner.mesh_dir)
    commands = []
    def command(cmd, case):
        commands.append(cmd)
        if cmd == "decomposePar":
            for rank in range(2):
                _mesh(case / f"processor{rank}")
        elif cmd.startswith("mpirun"):
            for rank in range(2):
                _write_result_fields(case / f"processor{rank}")
        elif cmd == "reconstructPar -latestTime":
            _write_result_fields(case)

    monkeypatch.setattr(runner, "_run_cmd", command)
    monkeypatch.setattr(runner, "_rewrite_explicit_boundary_values",
                        lambda path: commands.append("writeExplicitBC"))
    runner._run_solve_from_mesh()
    assert commands == ["decomposePar", "mpirun -np 2 solver_new -parallel",
                        "reconstructPar -latestTime", "writeExplicitBC"]


def test_wall_type_reconciliation_preserves_flow_and_coupled_patches(tmp_path):
    fields = tmp_path / "0"
    fields.mkdir()
    boundary = tmp_path / "boundary"
    patches = [("room", "patch"), ("floor", "patch"), ("inlet", "patch"),
               ("procBoundary", "processorCyclic"), ("baffle", "cyclic"),
               ("existing_wall", "wall")]
    boundary.write_text("\n".join(
        f"    {name}\n\n    {{\n\n        type {kind}; // keep comment\n        nFaces 3;\n    }}"
        for name, kind in patches
    ) + "\n")
    (fields / "nut").write_text("\n".join(
        f"    {name}\n    {{\n        type {kind};\n        value uniform 0;\n    }}"
        for name, kind in [("room", "nutkWallFunction"), ("inlet", "fixedValue"),
                           ("procBoundary", "processorCyclic"), ("baffle", "cyclic")]
    ))
    (fields / "alphat").write_text(
        "    floor\n\n    {\n\n        type compressible::alphatWallFunction;\n    }\n"
    )
    before = boundary.read_text()
    STLGenRunner._reconcile_boundary_wall_types(boundary, tmp_path)
    expected = before
    for name in ("room", "floor"):
        expected = expected.replace(
            f"    {name}\n\n    {{\n\n        type patch;",
            f"    {name}\n\n    {{\n\n        type wall;",
        )
    assert boundary.read_text() == expected
    STLGenRunner._reconcile_boundary_wall_types(boundary, tmp_path)
    assert boundary.read_text() == expected
