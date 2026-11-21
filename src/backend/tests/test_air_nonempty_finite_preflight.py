"""Native empty-mesh evidence must not authorize a steady or transient solve."""
from pathlib import Path
from types import SimpleNamespace
import struct

import pytest

from backend.aircooling.runner.transient_start import require_steady_convergence
from backend.tests.test_transient_start import _simple_log


def _mesh(case, cells, *, binary=False, note=True):
    root = case / "constant/polyMesh"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("points", "faces", "boundary"):
        (root / name).write_text("topology")
    for name, values in (("owner", list(range(cells))), ("neighbour", [])):
        note_text = f' note "nCells:{cells}";' if note and name == "owner" else ""
        header = (f'FoamFile {{ format {"binary" if binary else "ascii"}; '
                  f'arch "LSB;label=32;scalar=64"; class labelList; object {name};{note_text} }}\n'
                  "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n")
        body = (b"".join(struct.pack("<i", n) for n in values) if binary
                else ("\n".join(str(n) for n in values) + "\n").encode())
        (root / name).write_bytes(header.encode() + f"{len(values)}\n(".encode() + body + b"\n)\n")


@pytest.mark.parametrize("diagnostic", [
    "time step continuity errors : sum local = -nan, global = -nan, cumulative = -nan",
    "time step continuity errors : sum local = 0, global = inf, cumulative = 0",
    "Courant Number mean: 0 max: -inf",
    "deltaT = nan",
    "Time = 1e999",
    "PCG: Solving for T, Initial residual = .0001, Final residual = nan, No Iterations 1",
])
def test_numeric_nonfinite_diagnostics_refuse_an_otherwise_converged_simple(tmp_path, diagnostic):
    log = tmp_path / "log.steady_start"
    log.write_text(_simple_log().replace("SIMPLE solution", diagnostic + "\nSIMPLE solution"))
    with pytest.raises(RuntimeError, match="finite"):
        require_steady_convergence(log)


def test_finite_diagnostics_and_non_numeric_nan_words_do_not_refuse_simple(tmp_path):
    log = tmp_path / "log.steady_start"
    diagnostics = ("nan handling is enabled\n"
                   "time step continuity errors : sum local = 0, global = -1e-16, cumulative = 0\n"
                   "Courant Number mean: 0 max: 0.1\ndeltaT = .01\n")
    log.write_text(_simple_log().replace("SIMPLE solution", diagnostics + "SIMPLE solution"))
    assert require_steady_convergence(log)["verdict"] == "STEADY_CONVERGED"


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("note", [False, True])
def test_global_mesh_cells_allow_empty_rank_but_require_global_nonempty(tmp_path, binary, note):
    from backend.aircooling.runner.openfoam_patch_area import require_nonempty_mesh
    _mesh(tmp_path / "processor0", 0, binary=binary, note=note)
    _mesh(tmp_path / "processor1", 2, binary=binary, note=note)
    report = require_nonempty_mesh(tmp_path)
    assert report["global_cells"] == 2
    assert sorted(report["cells_by_mesh"].values()) == [0, 2]
    _mesh(tmp_path / "processor1", 0, binary=binary, note=note)
    with pytest.raises(ValueError, match="globally empty"):
        require_nonempty_mesh(tmp_path)


def test_final_partition_cell_guard_ignores_unused_reconstructed_root(tmp_path):
    from backend.aircooling.runner.openfoam_patch_area import require_nonempty_mesh
    _mesh(tmp_path, 9)
    _mesh(tmp_path / "processor0", 0)
    _mesh(tmp_path / "processor1", 0)
    assert require_nonempty_mesh(tmp_path)["global_cells"] == 9
    with pytest.raises(ValueError, match="globally empty"):
        require_nonempty_mesh(tmp_path, prefer_partitions=True)


def test_empty_mesh_cannot_receive_new_or_reuse_old_accepted_quality_receipt(tmp_path, monkeypatch):
    from backend.aircooling.runner.air_mesh_quality_receipt import record_air_mesh_quality
    mesh = tmp_path / "Mesh"
    _mesh(mesh / "processor0", 0)
    _mesh(mesh / "processor1", 0)
    source = tmp_path / "input.cfdrun"
    source.write_text("{}")
    receipt = tmp_path / "mesh-quality-receipt.json"
    receipt.write_text('{"status":"accepted"}')
    calls = []
    monkeypatch.setattr("backend.aircooling.runner.air_mesh_quality_receipt.subprocess.run",
                        lambda *a, **kw: calls.append(a) or SimpleNamespace(returncode=0, stdout="Mesh OK.\n", stderr=""))
    with pytest.raises(ValueError, match="globally empty"):
        record_air_mesh_quality(mesh, source)
    assert calls == []
    assert not receipt.exists()


def test_empty_mesh_is_refused_before_solve_copy_or_solver(tmp_path):
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    _mesh(tmp_path / "processor0", 0)
    _mesh(tmp_path / "processor1", 0)
    runner = STLGenRunner.__new__(STLGenRunner)
    runner.mesh_dir = tmp_path
    runner._clear_mesh_budget = lambda: None
    runner._read_cfdrun_manifest = lambda: {}
    # The strict quality gate runs first and refuses an empty mesh itself
    # (air_mesh_quality_receipt); here the solve's own preflight is checked.
    runner._require_air_mesh_quality = lambda: None
    runner._check_mesh_budget = lambda *_args: None
    with pytest.raises(ValueError, match="globally empty"):
        runner._run_solve_from_mesh()
    assert not (tmp_path / "log").exists()


def test_empty_final_layout_is_refused_before_steady_setup_and_command(tmp_path, monkeypatch):
    from backend.tests.test_steady_start_partition_lifecycle import _runner
    runner, events = _runner(tmp_path, monkeypatch, 1, 2, 2)
    finalize = runner._decompose_for_solve
    def empty_layout(path, count):
        actual = finalize(path, count)
        for rank in range(actual):
            _mesh(path / f"processor{rank}", 0)
        return actual
    monkeypatch.setattr(runner, "_decompose_for_solve", empty_layout)
    with pytest.raises(ValueError, match="globally empty"):
        runner._run_steady_start(tmp_path, True, 2, True)
    assert events == [("decompose", 2)]
    assert not (tmp_path / ".steady-start-backup").exists()


def test_empty_mesh_is_refused_by_quality_cleanup_before_command(tmp_path):
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    _mesh(tmp_path / "processor0", 0)
    _mesh(tmp_path / "processor1", 0)
    runner = STLGenRunner.__new__(STLGenRunner)
    with pytest.raises(ValueError, match="globally empty"):
        runner._remove_disconnected_cells(tmp_path, n_procs=2)


def test_saved_empty_mesh_accepted_receipt_cannot_be_used(tmp_path):
    import hashlib
    import json
    from backend.aircooling.runner.air_mesh_quality_receipt import verify_air_mesh_quality_receipt
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
    mesh = tmp_path / "Mesh"
    _mesh(mesh / "processor0", 0)
    _mesh(mesh / "processor1", 0)
    source = tmp_path / "input.cfdrun"
    source.write_text("{}")
    receipt = {
        "format": "air-mesh-quality/v1", "status": "accepted", "failedChecks": 0,
        "meshSha256": mesh_identity_sha256(mesh),
        "cfdrunSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "mode": "partitioned", "ranks": 2,
        "checkmeshStdoutSha256": "a" * 64, "checkmeshStderrSha256": "b" * 64,
    }
    receipt_path = tmp_path / "mesh-quality-receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="globally empty"):
        verify_air_mesh_quality_receipt(receipt_path, mesh, source, require_accepted=True)


def test_partial_empty_rank_can_receive_and_verify_accepted_quality(tmp_path, monkeypatch):
    from backend.aircooling.runner.air_mesh_quality_receipt import record_air_mesh_quality, verify_air_mesh_quality_receipt
    mesh = tmp_path / "Mesh"
    _mesh(mesh / "processor0", 0)
    _mesh(mesh / "processor1", 2)
    source = tmp_path / "input.cfdrun"
    source.write_text("{}")
    monkeypatch.setattr("backend.aircooling.runner.air_mesh_quality_receipt.subprocess.run",
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="cells: 2\nMesh OK.\n", stderr=""))
    receipt = record_air_mesh_quality(mesh, source)
    assert receipt["status"] == "accepted"
    assert verify_air_mesh_quality_receipt(tmp_path / "mesh-quality-receipt.json", mesh, source, require_accepted=True) == receipt
