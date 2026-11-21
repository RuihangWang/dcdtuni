"""Regression coverage for preserving and checking the distributed mesh."""
from unittest.mock import Mock
import json

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def runner(tmp_path):
    instance = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model/p.cfdrun")
    instance._data = {"meshConfig": {"meshEngine": "dcCart"}}
    return instance


@pytest.mark.parametrize("interface_faces", [None, 0, 2])
def test_partition_pipeline_keeps_mesh_and_uses_parallel_baffle_constraints(tmp_path, monkeypatch, interface_faces):
    instance = runner(tmp_path)
    mesh = instance.mesh_dir
    (mesh / "system").mkdir(parents=True)
    (mesh / "system/createBafflesDict").write_text("baffles {}")
    stale = mesh / "constant/polyMesh"
    stale.mkdir(parents=True)
    (stale / "faces").write_text("old serial mesh")
    (mesh / "constant/dcCartPartitionInfo.json").write_text(
        json.dumps({"nProcs": 2, "processorZoneFaceCopies": 0})
    )  # stale metadata must not suppress redistribution for an older binary
    for name in ("_start_mesh_budget", "_ensure_mesh_system_files", "_clean_stale_polymesh_zones",
                 "_write_dcCart_mesh_dict", "_drop_empty_baffle_patches",
                 "_convert_wall_baffle_patches_to_wall", "_warn_open_room_enclosure"):
        monkeypatch.setattr(instance, name, Mock())
    monkeypatch.setattr(instance, "_mesh_time_remaining", lambda: None)
    monkeypatch.setattr(instance, "_resolve_mesher_binary", lambda name: name)
    quality = Mock()
    monkeypatch.setattr(instance, "_remove_disconnected_cells", quality)
    commands = []
    monkeypatch.setattr(instance, "_run_cmd", lambda cmd, _: commands.append(cmd))

    def build(*_):
        for rank in range(2):
            poly = mesh / f"processor{rank}/constant/polyMesh"
            poly.mkdir(parents=True)
            (poly / "faces").write_text(f"rank {rank}")
        if interface_faces is not None:
            (mesh / "constant/dcCartPartitionInfo.json").write_text(
                json.dumps({"nProcs": 2, "processorZoneFaceCopies": interface_faces})
            )
        return True

    monkeypatch.setattr(instance, "_run_dcCart_parallel", build)
    instance._run_dcCart_mesh_pipeline(2)
    assert not stale.exists()
    assert (mesh / "processor1/constant/polyMesh/faces").read_text() == "rank 1"
    expected = ["mpirun -np 2 createBaffles -overwrite -parallel"]
    if interface_faces != 0:
        expected.insert(0, "mpirun -np 2 redistributePar -parallel -overwrite")
        assert "preserveFaceZones" in (mesh / "system/decomposeParDict").read_text()
    assert commands == expected
    quality.assert_called_once_with(mesh, n_procs=2)


@pytest.mark.parametrize("info", [[], {"nProcs": 3, "processorZoneFaceCopies": 0},
                                   {"nProcs": 2, "processorZoneFaceCopies": False},
                                   {"nProcs": 2, "processorZoneFaceCopies": -1},
                                   {"nProcs": 2}, None])
def test_ambiguous_partition_metadata_keeps_redistribution(tmp_path, info):
    (tmp_path / "constant").mkdir()
    (tmp_path / "constant/dcCartPartitionInfo.json").write_text(json.dumps(info))
    assert STLGenRunner._dcCart_needs_redistribution(tmp_path, 2)


@pytest.mark.parametrize("report", ["FOAM FATAL ERROR: bad processor patch", "Checking topology...\n"])
def test_incomplete_partition_quality_report_cannot_succeed(tmp_path, monkeypatch, report):
    instance = runner(tmp_path)
    from backend.tests.test_air_nonempty_finite_preflight import _mesh
    for rank in range(2):
        _mesh(instance.mesh_dir / f"processor{rank}", 1)
    monkeypatch.setattr(instance, "_run_cmd_capture", lambda *_: report)
    with pytest.raises(RuntimeError, match="complete quality report"):
        instance._remove_disconnected_cells(instance.mesh_dir, n_procs=2)


def test_partition_orphan_cleanup_rechecks_global_connectivity(tmp_path, monkeypatch):
    instance = runner(tmp_path)
    from backend.tests.test_air_nonempty_finite_preflight import _mesh
    for rank in range(2):
        _mesh(instance.mesh_dir / f"processor{rank}", 1)
    reports = iter([
        "Writing region 0 (fully disconnected) with 10000 cells to cellSet region0\n"
        "Writing region 1 (fully disconnected) with 5 cells to cellSet region1\n"
        "Failed 1 mesh checks.\n",
        "Number of regions: 1 (OK).\nMesh OK.\n",
    ])
    commands = []

    def check(cmd, _):
        commands.append(cmd)
        return next(reports)

    monkeypatch.setattr(instance, "_run_cmd_capture", check)
    subset = Mock(return_value=5)
    monkeypatch.setattr(instance, "_do_subset_mesh", subset)
    assert instance._remove_disconnected_cells(instance.mesh_dir, n_procs=2) == 5
    assert len(commands) == 2
    assert all(cmd == "mpirun -np 2 checkMesh -allTopology -allGeometry -parallel" for cmd in commands)
    assert subset.call_args.kwargs == {"n_procs": 2}
    assert "region0" in subset.call_args.args[1][0]


def test_unpaired_processor_interfaces_are_named_before_checkmesh_runs(tmp_path, monkeypatch):
    """Base604 (2026-09-30): a rank one face short on its interfaces made
    `checkMesh -parallel` segfault; the pairing check names the ranks first
    and checkMesh is never launched on a mesh OpenFOAM cannot construct."""
    from backend.tests.test_processor_patches import _boundary

    instance = runner(tmp_path)
    mesh = instance.mesh_dir
    _boundary(mesh, 0, {"procBoundary0to1": ("processor", 20, 1)})
    _boundary(mesh, 1, {"procBoundary1to0": ("processor", 19, 0)})
    for rank in range(2):
        poly = mesh / f"processor{rank}/constant/polyMesh"
        for name in ("points", "faces", "neighbour"):
            (poly / name).write_text("topology")
        (poly / "owner").write_text('FoamFile { note "nCells:1"; object owner; }\n')
    launched = []
    monkeypatch.setattr(instance, "_run_cmd_capture", lambda cmd, _: launched.append(cmd) or "Mesh OK.\n")
    with pytest.raises(RuntimeError, match=r"inconsistent between ranks 0 and 1.*20 faces on rank 0 but 19"):
        instance._remove_disconnected_cells(mesh, n_procs=2)
    assert launched == []
