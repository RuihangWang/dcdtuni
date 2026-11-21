"""TDD: checkMesh must detect and remove under-determined cells.

Bug: checkMesh runs with -allTopology only, which misses the geometry
determinant check. 8 degenerate cells (det < 0.001) survive into the
solver and cause NaN on unlucky decompositions (Base604 failure).

Fix requires two changes:
1. checkMesh flag: -allTopology → -allTopology -allGeometry
2. Strategy 3: remove underdeterminedCells via topoSet + subsetMesh
"""
from backend.aircooling.runner.mesh_quality import parse_checkmesh_warnings


CHECKMESH_WITH_UNDERDETERMINED = """\
Checking geometry...
    Overall domain bounding box (0 0 0) (15.6 4.4 23.4)
    Mesh has 3 geometric (non-empty/wedge) directions (1 1 1)
    Mesh has 3 solution (non-empty) directions (1 1 1)
    Boundary openness (-1.9e-16 1.1e-14 2.6e-17) OK.
    Max cell openness = 3.47067e-16 OK.
    Max aspect ratio = 7.83 OK.
    Minimum face area = 2.49e-06. Maximum face area = 0.134.  Face area magnitudes OK.
    Min volume = 2.38e-06. Max volume = 0.0463.  Total volume = 1537.15.  Cell volumes OK.
    Mesh non-orthogonality Max: 60.05 average: 9.36
    Non-orthogonality check OK.
    Face pyramids OK.
    Cell determinant (wellposedness) : minimum: 0 average: 1.43628
 ***Cells with small determinant (< 0.001) found, number of cells: 8
  <<Writing 8 under-determined cells to set underdeterminedCells
    Face interpolation weight check OK.
    Face volume ratio check OK.

Failed 1 mesh checks.
"""

CHECKMESH_CLEAN = """\
Checking geometry...
    Overall domain bounding box (0 0 0) (15.6 4.4 23.4)
    Cell determinant (wellposedness) : minimum: 0.5 average: 1.43
    Face interpolation weight check OK.
    Face volume ratio check OK.

Mesh OK.
"""


def test_parse_detects_underdetermined_cells():
    """parse_checkmesh_warnings must return a small_determinant warning."""
    warnings = parse_checkmesh_warnings(CHECKMESH_WITH_UNDERDETERMINED)
    det_warnings = [w for w in warnings if w["code"] == "small_determinant"]
    assert len(det_warnings) == 1
    assert det_warnings[0]["count"] == 8
    assert det_warnings[0]["level"] == 1


def test_parse_no_false_positive_on_clean_mesh():
    warnings = parse_checkmesh_warnings(CHECKMESH_CLEAN)
    det_warnings = [w for w in warnings if w["code"] == "small_determinant"]
    assert len(det_warnings) == 0


def test_checkmesh_command_includes_allgeometry():
    """The runner must call checkMesh with -allGeometry to detect determinant issues."""
    import inspect
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    source = inspect.getsource(STLGenRunner._remove_disconnected_cells)
    assert "-allGeometry" in source, (
        "_remove_disconnected_cells calls checkMesh without -allGeometry — "
        "under-determined cells will not be detected"
    )


