"""Every final mesh uses full geometry; a rejected verdict is reported, an incomplete one still fails.

Owner rule (2026-10-22): mesh quality is reported, never required.
"""
import pytest
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.aircooling.runner import polymesh_io


def _runner(monkeypatch, text, engine):
    r = STLGenRunner.__new__(STLGenRunner)
    r._data = {"meshConfig": {"meshEngine": engine}}
    r.commands = []
    def capture(command, workdir):
        r.commands.append(command)
        return text
    r._run_cmd_capture = capture
    r._fix_old_internal_faces_patch_type = lambda _: None
    from backend.aircooling.runner import openfoam_patch_area
    # the empty-mesh guard reads a real polyMesh; these tests drive the checker text only
    monkeypatch.setattr(openfoam_patch_area, "require_nonempty_mesh", lambda *a, **k: None)
    monkeypatch.setattr(polymesh_io, "mesh_topology_summary", lambda _: {
        "nCells": 1, "nRegions": 1, "regionSizes": [1], "nIsolatedCells": 0})
    return r


@pytest.mark.parametrize("engine", ["dcCart", "snappy"])
@pytest.mark.parametrize("snap_off", [None, True, False])
def test_strict_geometry_is_independent_of_engine_and_legacy_snap_override(monkeypatch, tmp_path, engine, snap_off):
    r = _runner(monkeypatch, "Mesh stats\n    cells: 1\nMesh OK.\n", engine)
    assert r._remove_disconnected_cells(tmp_path, snap_off=snap_off) == 0
    assert r.commands == ["checkMesh -allTopology -allGeometry"]


def test_a_rejected_final_verdict_is_a_warning_not_a_failed_mesh(monkeypatch, tmp_path):
    r = _runner(monkeypatch, "Mesh stats\n    cells: 1\n***Error in face tets: 2 faces.\nFailed 5 mesh checks.\n",
                "dcCart")
    assert r._remove_disconnected_cells(tmp_path) == 0


def test_an_incomplete_final_check_still_fails(monkeypatch, tmp_path):
    r = _runner(monkeypatch, "Mesh stats\n    cells: 1\nEnd\n", "dcCart")
    with pytest.raises(RuntimeError, match="Final checkMesh did not produce"):
        r._remove_disconnected_cells(tmp_path)


_CONCAVE_ONLY = ("Mesh stats\n    cells: 1\n"
                 " ***Concave cells (using face planes) found, number of cells: 5\n"
                 "Failed 1 mesh checks.\n")


def test_planar_only_concave_cells_do_not_fail_the_strict_gate(monkeypatch, tmp_path):
    from backend.aircooling.runner import mesh_concavity
    monkeypatch.setattr(mesh_concavity, "concave_cell_counts_in", lambda case, n: (5, 0))
    r = _runner(monkeypatch, _CONCAVE_ONLY, "dcCart")
    assert r._remove_disconnected_cells(tmp_path) == 0


def test_real_concave_cells_are_reported_not_refused(monkeypatch, tmp_path):
    from backend.aircooling.runner import mesh_concavity
    monkeypatch.setattr(mesh_concavity, "concave_cell_counts_in", lambda case, n: (5, 2))
    r = _runner(monkeypatch, _CONCAVE_ONLY, "snappy")
    assert r._remove_disconnected_cells(tmp_path) == 0


def test_other_failures_are_counted_past_concavity_and_reported(monkeypatch, tmp_path):
    from backend.aircooling.runner import mesh_concavity
    from loguru import logger
    monkeypatch.setattr(mesh_concavity, "concave_cell_counts_in", lambda case, n: (5, 0))
    text = _CONCAVE_ONLY.replace("Failed 1", " ***Error in face tets: 2 faces.\nFailed 2")
    r = _runner(monkeypatch, text, "dcCart")
    seen = []
    handler = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        assert r._remove_disconnected_cells(tmp_path) == 0
    finally:
        logger.remove(handler)
    assert any("Final checkMesh failed 1 mesh checks (warning, not refused)" in line for line in seen)
