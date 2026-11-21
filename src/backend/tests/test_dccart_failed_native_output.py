"""Filesystem lifecycle only; native calls mocked, not a mesh acceptance."""
from unittest.mock import Mock
import pytest
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.aircooling.runner.mesh_budget import MeshTimeoutError

@pytest.mark.parametrize("error", [RuntimeError("exit1"), MeshTimeoutError("deadline"), KeyboardInterrupt()])
def test_failed_native_preserves_checkpoint(tmp_path, monkeypatch, error):
    runner=STLGenRunner.__new__(STLGenRunner);case=tmp_path/"Mesh";case.mkdir()
    def fail(cmd,cwd):
        assert cmd=="mpirun -np 2 dcCartMesh -overwrite -timeLimit 300 -parallel"
        (cwd/"processor1/preoperator.bin").write_bytes(b"original\x00checkpoint")
        raise error
    monkeypatch.setattr(runner,"_run_cmd",fail)
    if isinstance(error,RuntimeError):
        assert runner._run_dcCart_parallel("dcCartMesh"," -timeLimit 300",2,case)is False
    else:
        with pytest.raises(KeyboardInterrupt):runner._run_dcCart_parallel("dcCartMesh"," -timeLimit 300",2,case)
    assert (case/"processor1/preoperator.bin").read_bytes()==b"original\x00checkpoint"


def test_missing_rank_preserves_partial_output(tmp_path,monkeypatch):
    runner=STLGenRunner.__new__(STLGenRunner);case=tmp_path/"Mesh";case.mkdir()
    def partial(_,cwd):
        poly=cwd/"processor0/constant/polyMesh";poly.mkdir(parents=True);(poly/"faces").write_bytes(b"rank0")
        (cwd/"processor1/preoperator.bin").write_bytes(b"rank1 failure")
    monkeypatch.setattr(runner,"_run_cmd",partial)
    assert runner._run_dcCart_parallel("dcCartMesh","",2,case)is False
    assert (case/"processor0/constant/polyMesh/faces").read_bytes()==b"rank0"
    assert (case/"processor1/preoperator.bin").read_bytes()==b"rank1 failure"


def test_new_run_replaces_the_previous_output(tmp_path,monkeypatch):
    runner=STLGenRunner.__new__(STLGenRunner);case=tmp_path/"Mesh";case.mkdir()
    old=case/"processor0";old.mkdir();(old/"preoperator.bin").write_bytes(b"old\x00")
    (case/"conforming-source-rank0-points.f64").write_bytes(b"raw bits")
    (case/"log.mpi_solver").write_bytes(b"old fatal")
    (case/"generated-ap-joint-rank0.json").write_bytes(b"old source witness")
    (case/"constant").mkdir();(case/"constant/input.keep").write_bytes(b"input")
    def success(_,cwd):
        assert not (cwd/"processor0/preoperator.bin").exists()
        for rank in range(2):
            poly=cwd/f"processor{rank}/constant/polyMesh";poly.mkdir(parents=True);(poly/"faces").write_bytes(b"fresh")
    monkeypatch.setattr(runner,"_run_cmd",success)
    assert runner._run_dcCart_parallel("dcCartMesh","",2,case)is True
    # no history is kept: the new mesh replaces the old one
    assert not (tmp_path/"_dcCart_mesh_history").exists()
    assert not (case/"conforming-source-rank0-points.f64").exists()
    assert not (case/"log.mpi_solver").exists() and not (case/"generated-ap-joint-rank0.json").exists()
    assert (case/"processor0/constant/polyMesh/faces").read_bytes()==b"fresh"
    assert (case/"constant/input.keep").read_bytes()==b"input"


def test_unsafe_archive_refuses_before_native_or_delete(tmp_path,monkeypatch):
    runner=STLGenRunner.__new__(STLGenRunner);case=tmp_path/"Mesh";case.mkdir()
    held=tmp_path/"held";held.mkdir();(held/"evidence").write_bytes(b"held")
    (case/"processor0").symlink_to(held,target_is_directory=True)
    native=Mock();monkeypatch.setattr(runner,"_run_cmd",native)
    with pytest.raises(RuntimeError,match="unsafe dcCart output removal"):
        runner._run_dcCart_parallel("dcCartMesh","",2,case)
    native.assert_not_called();assert (held/"evidence").read_bytes()==b"held"
