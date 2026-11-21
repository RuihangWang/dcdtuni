"""dcCart's measured peak kept per case and used for the next mesh of the same settings: a room with many surfaces
peaks far above the fitted law (Alibaba-hongchou: 86 GB on 8 ranks, law 57 GB), and 24 ranks ran the host out of
memory before dcCartMesh's own guard."""
from __future__ import annotations

import json

import pytest

from backend.aircooling.runner import stlgen_runner as sr
from backend.aircooling.runner.stlgen_runner import STLGenRunner

LINE = ("Memory: peak RSS sum 85.978 GB (largest rank 10.7703 GB) for 15973830 octree leaves on 8 rank(s); "
        "guard estimate 57.4186 GB\n")


def _runner(tmp_path, **mc):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model/p.cfdrun")
    r._data = {"meshConfig": {"meshEngine": "dcCart", "meshBaseSize": 0.25, "meshProcess": 8, **mc}}
    r.mesh_dir.mkdir(parents=True, exist_ok=True)
    return r


def test_the_peak_is_kept_and_budgets_the_next_mesh(tmp_path, monkeypatch):
    r = _runner(tmp_path)
    (r.mesh_dir / "log.mpi_solver").write_text("...\n" + LINE)
    r._record_dcCart_memory(r.mesh_dir, 8)
    rec = json.loads((tmp_path / sr.DCCART_MEMORY_RECORD).read_text())
    assert (rec["ranks"], rec["leaves"], rec["peakSumBytes"]) == (8, 15973830, 85978000000)
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(232e9))
    # 10.77 GB x 1.15 per rank: 18 ranks fit 232 GB less the headroom, 24 do not
    assert r._dcCart_ranks_that_fit(24) == 18
    assert r._dcCart_ranks_that_fit(8) == 8
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(5e9))
    with pytest.raises(sr.MeshMemoryError):
        r._dcCart_ranks_that_fit(8)


def test_other_settings_or_no_record_leave_the_rank_count(tmp_path, monkeypatch):
    monkeypatch.setenv(sr.DCCART_MEM_AVAILABLE_ENV, str(20e9))
    r = _runner(tmp_path)
    assert r._dcCart_ranks_that_fit(24) == 24                     # nothing measured yet: the law's guards decide
    (r.mesh_dir / "log.mpi_solver").write_text(LINE)
    r._record_dcCart_memory(r.mesh_dir, 8)
    assert r._dcCart_ranks_that_fit(24) == 1
    assert _runner(tmp_path, meshProcess=24, parallelMesh=True)._dcCart_ranks_that_fit(24) == 1   # how it ran: same
    assert _runner(tmp_path, meshBaseSize=0.5)._dcCart_ranks_that_fit(24) == 24                   # another mesh
    r._record_dcCart_memory(r.mesh_dir, 4)                        # a log of another rank count is not recorded again
    assert json.loads((tmp_path / sr.DCCART_MEMORY_RECORD).read_text())["ranks"] == 8
