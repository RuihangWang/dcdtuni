"""TDD: checkMesh output parser for structured mesh quality warnings.

Root cause of GTC solver NaN: 3 cells with zero internal faces (fully
enclosed by wall patches) cause DILU preconditioner division by zero.
checkMesh detects this but the result was only used as a pass/fail gate.

New behavior: parse checkMesh output into severity-leveled warnings
that the frontend can display in a dialog before solving.
"""
import pytest

from backend.aircooling.runner.mesh_quality import parse_checkmesh_warnings, parse_checkmesh_regions


# ── Fixtures: real checkMesh output snippets ──

CHECKMESH_GTC_FAIL = """\
Checking topology...
    Boundary definition OK.
    Cell to face addressing OK.
    Point usage OK.
    Upper triangular ordering OK.
    Face vertices OK.
    Number of regions: 6 (OK).

Checking patch topology for multiply connected surfaces...
    Patch               Faces    Points   Surface topology
    left_fan_wall_room_crac_1_Front_patch 42       56       ok (non-closed singly connected)

Checking faceZone topology for multiply connected surfaces...
    No faceZones found.

Checking basic cellZone addressing...
    No cellZones found.

Checking geometry...
    Overall domain bounding box (-3.199 -0.608 -2.019) (34.109 5.608 21.129)
    Mesh has 3 geometric (1e-15) directions (3D)
    Mesh has 3 solution (1e-06) directions (3D)
    Boundary openness (2.2e-16 1.3e-16 -3.8e-17) OK.
    Max cell openness = 3.8e-16 OK.
    Max aspect ratio = 11.4 OK.
    Minimum face area = 1.2e-07. Maximum face area = 0.0289. Face area magnitudes OK.
    Min volume = 2.3e-11. Max volume = 3.9e-05.  Total volume = 3028.5. Cell volumes OK.
    Mesh non-orthogonality Max: 55.2 average: 5.1
    Non-orthogonality check OK.
  <<Writing 3 cells with zero or one non-boundary face to set oneInternalFaceCells
    Minimum face area = 1.2e-07. Maximum face area = 0.0289. Face area magnitudes OK.
   *Number of regions: 6
    The mesh has multiple regions which are not connected by any face.
  <<Writing region information to "0/cellToRegion"
  <<Writing region 0 (point connected) with 2043877 cells to cellSet region0
  <<Writing region 1 (point connected) with 60809 cells to cellSet region1
  <<Writing region 2 (point connected) with 62620 cells to cellSet region2
  <<Writing region 3 (fully disconnected) with 1 cells to cellSet region3
  <<Writing region 4 (fully disconnected) with 1 cells to cellSet region4
  <<Writing region 5 (fully disconnected) with 1 cells to cellSet region5
  <<Writing 20862 points that are in multiple regions to set multiRegionPoints
 ***Error in face tets: 8 faces with low quality or negative volume decomposition tets.
    Cell determinant (wellposedness) : minimum: 0 average: 1.64651
 ***Cells with small determinant (< 0.001) found, number of cells: 3
 ***Concave cells (using face planes) found, number of cells: 105382

Failed 3 mesh checks.
"""

CHECKMESH_SKEW_FAIL = """\
Checking geometry...
    Overall domain bounding box (0 0 0) (10 5 10)
    Max cell openness = 1.2e-16 OK.
    Max aspect ratio = 8.5 OK.
    Minimum face area = 0.001. Maximum face area = 0.04.
    Min volume = 1e-06. Max volume = 6.4e-05. Cell volumes OK.
    Mesh non-orthogonality Max: 42 average: 4.2
    Non-orthogonality check OK.
    Face pyramids OK.
    Max skewness = 12.3 -- ***FAILED (limit: 4)
        9 faces with skewness > 4.
   ***Max internal skewness exceeded: 12.3 > 4
    Face flatness (1 = flat, 0 = butterfly) : min = 0.8 average = 0.99

Failed 1 mesh checks.
"""

# OpenFOAM 2412 output from the archived Shinaide air-only mesh smoke.
CHECKMESH_SHINAIDE_FAIL = """\
Checking geometry...
    Mesh non-orthogonality Max: 58.2211 average: 9.53963
    Non-orthogonality check OK.
 ***Max skewness = 5.1291, 4 highly skew faces detected which may impair the quality of the results
   <<Writing 4 skew faces to set skewFaces
 ***Cells with small determinant (< 0.001) found, number of cells: 6
 ***Concave cells (using face planes) found, number of cells: 13363
   <<Writing 13363 concave cells to set concaveCells
Failed 3 mesh checks.
"""

CHECKMESH_OK = """\
Checking geometry...
    Overall domain bounding box (0 0 0) (10 5 10)
    Max cell openness = 1.2e-16 OK.
    Max aspect ratio = 3.2 OK.
    Min volume = 1e-05. Max volume = 6.4e-05. Cell volumes OK.
    Mesh non-orthogonality Max: 32 average: 3.1
    Non-orthogonality check OK.
    Max skewness = 1.8 OK.
    Face pyramids OK.

Mesh OK.
"""


# ── RED tests ──

class TestIsolatedCells:
    """Level 1 (CRITICAL): cells with zero or one non-boundary face."""

    def test_detects_isolated_cells(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"]
        assert len(isolated) == 1
        w = isolated[0]
        assert w["level"] == 1
        assert w["count"] == 3

    def test_isolated_cells_message_is_human_readable(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"][0]
        assert "3" in isolated["message"]

    def test_no_isolated_cells_in_clean_mesh(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"]
        assert len(isolated) == 0


class TestHighSkewness:
    """Level 2 (SEVERE): faces with skewness exceeding threshold."""

    def test_detects_high_skewness(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_SKEW_FAIL)
        skew = [w for w in warnings if w["code"] == "high_skewness"]
        assert len(skew) == 1
        w = skew[0]
        assert w["level"] == 2
        assert w["max_value"] == pytest.approx(12.3)

    def test_no_skewness_warning_in_clean_mesh(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        skew = [w for w in warnings if w["code"] == "high_skewness"]
        assert len(skew) == 0

    def test_detects_shinaide_openfoam_2412_format(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_SHINAIDE_FAIL)
        skew = [w for w in warnings if w["code"] == "high_skewness"]
        assert len(skew) == 1
        assert skew[0]["level"] == 2
        assert skew[0]["max_value"] == pytest.approx(5.1291)
        assert skew[0]["count"] == 4


class TestConcaveCells:
    def test_detects_shinaide_concave_cells_without_blocking_on_its_own(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_SHINAIDE_FAIL)
        concave = [w for w in warnings if w["code"] == "concave_cells"]
        assert len(concave) == 1
        assert concave[0]["count"] == 13363
        assert concave[0]["level"] == 3

    def test_detects_existing_gtc_concave_cells(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        concave = [w for w in warnings if w["code"] == "concave_cells"]
        assert len(concave) == 1
        assert concave[0]["count"] == 105382

    def test_clean_mesh_has_no_concave_warning(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        assert all(w["code"] != "concave_cells" for w in warnings)


class TestSmallDeterminant:
    """Level 1 (CRITICAL): cells with near-zero determinant."""

    def test_detects_small_determinant(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        det = [w for w in warnings if w["code"] == "small_determinant"]
        assert len(det) == 1
        w = det[0]
        assert w["level"] == 1
        assert w["count"] == 3


class TestNegativeVolumeTets:
    """Level 2 (SEVERE): faces with bad tet decomposition."""

    def test_detects_bad_tets(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        tets = [w for w in warnings if w["code"] == "bad_face_tets"]
        assert len(tets) == 1
        w = tets[0]
        assert w["level"] == 2
        assert w["count"] == 8


class TestCleanMesh:
    """No warnings for a healthy mesh."""

    def test_clean_mesh_returns_empty(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        assert warnings == []

    def test_overall_ok_flag(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        assert all(w["level"] > 0 for w in warnings) or len(warnings) == 0


class TestWarningStructure:
    """Each warning has required fields."""

    def test_warning_has_required_keys(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        assert len(warnings) > 0
        for w in warnings:
            assert "level" in w
            assert "code" in w
            assert "message" in w
            assert isinstance(w["level"], int)
            assert 1 <= w["level"] <= 3


# ── Isolated cells WITHOUT disconnected regions (STDCT-type) ──

CHECKMESH_ISOLATED_ONLY = """\
Checking topology...
    Boundary definition OK.
    Cell to face addressing OK.
    Point usage OK.
    Upper triangular ordering OK.
    Face vertices OK.
    Number of regions: 1 (OK).

Checking geometry...
    Overall domain bounding box (-1.5 0 -2) (15.5 4 12)
    Mesh has 3 geometric (1e-15) directions (3D)
    Mesh has 3 solution (1e-06) directions (3D)
    Boundary openness (1e-16 2e-16 -1e-17) OK.
    Max cell openness = 2e-16 OK.
    Max aspect ratio = 9.2 OK.
    Minimum face area = 5e-07. Maximum face area = 0.02.
    Min volume = 8e-11. Max volume = 2.7e-05. Cell volumes OK.
    Mesh non-orthogonality Max: 48.3 average: 4.8
    Non-orthogonality check OK.
  <<Writing 5 cells with zero or one non-boundary face to set oneInternalFaceCells
    Max skewness = 2.1 OK.
    Face pyramids OK.

Failed 1 mesh checks.
"""


class TestIsolatedCellsWithoutDisconnectedRegions:
    """STDCT scenario: isolated cells exist but mesh is single-region."""

    def test_detects_isolated_cells(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_ISOLATED_ONLY)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"]
        assert len(isolated) == 1
        assert isolated[0]["count"] == 5

    def test_no_disconnected_regions(self):
        regions = parse_checkmesh_regions(CHECKMESH_ISOLATED_ONLY)
        disconnected = [r for r in regions if r["type"] == "fully disconnected"]
        assert len(disconnected) == 0

    def test_strategy2_needed_when_no_regions(self):
        """When no fully-disconnected regions exist but isolated cells do,
        the removal must use the oneInternalFaceCells inversion strategy."""
        regions = parse_checkmesh_regions(CHECKMESH_ISOLATED_ONLY)
        disconnected = [r for r in regions if r["type"] == "fully disconnected"]
        warnings = parse_checkmesh_warnings(CHECKMESH_ISOLATED_ONLY)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"]
        assert len(disconnected) == 0, "Strategy 1 should NOT trigger"
        assert len(isolated) == 1, "Strategy 2 SHOULD trigger"
        assert isolated[0]["count"] == 5


# ── Region parsing for automated cleanup ──

class TestParseRegions:
    """parse_checkmesh_regions extracts region connectivity info."""

    def test_finds_disconnected_regions(self):
        regions = parse_checkmesh_regions(CHECKMESH_GTC_FAIL)
        disconnected = [r for r in regions if r["type"] == "fully disconnected"]
        assert len(disconnected) == 3
        assert all(r["cells"] == 1 for r in disconnected)
        assert {r["name"] for r in disconnected} == {"region3", "region4", "region5"}

    def test_finds_connected_regions(self):
        regions = parse_checkmesh_regions(CHECKMESH_GTC_FAIL)
        connected = [r for r in regions if r["type"] == "point connected"]
        assert len(connected) == 3
        assert connected[0]["cells"] == 2043877  # largest

    def test_no_regions_in_single_region_mesh(self):
        regions = parse_checkmesh_regions(CHECKMESH_OK)
        assert len(regions) == 0  # single-region mesh has no region lines

    def test_returns_cellset_names_for_removal(self):
        regions = parse_checkmesh_regions(CHECKMESH_GTC_FAIL)
        to_remove = [r["name"] for r in regions if r["type"] == "fully disconnected"]
        assert to_remove == ["region3", "region4", "region5"]


# ── Multi-region warning in parse_checkmesh_warnings ──

class TestDisconnectedRegionsWarning:
    """Multi-region meshes emit a 'disconnected_regions' warning via the
    existing meshWarnings pipeline.  The runner does NOT auto-delete
    regions — the warning surfaces in the frontend dialog so the user
    can decide (it might be legitimate containment)."""

    def test_multi_region_emits_warning(self):
        """GTC 6-region mesh must produce a disconnected_regions warning."""
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        region_warns = [w for w in warnings if w["code"] == "disconnected_regions"]
        assert len(region_warns) == 1

    def test_warning_level_is_critical(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        w = [w for w in warnings if w["code"] == "disconnected_regions"][0]
        assert w["level"] == 1

    def test_warning_includes_region_count(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        w = [w for w in warnings if w["code"] == "disconnected_regions"][0]
        assert w["count"] == 6

    def test_warning_message_mentions_containment(self):
        """Message must hint that multi-region CAN be valid (containment)."""
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        w = [w for w in warnings if w["code"] == "disconnected_regions"][0]
        assert "containment" in w["message"].lower()

    def test_single_region_no_warning(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        region_warns = [w for w in warnings if w["code"] == "disconnected_regions"]
        assert len(region_warns) == 0

    def test_single_region_isolated_only_no_region_warning(self):
        """STDCT-type: 1 region + isolated cells → no region warning."""
        warnings = parse_checkmesh_warnings(CHECKMESH_ISOLATED_ONLY)
        region_warns = [w for w in warnings if w["code"] == "disconnected_regions"]
        assert len(region_warns) == 0

    def test_warning_includes_region_details(self):
        """Warning dict has 'regions' list with per-region info for frontend."""
        warnings = parse_checkmesh_warnings(CHECKMESH_GTC_FAIL)
        w = [w for w in warnings if w["code"] == "disconnected_regions"][0]
        assert "regions" in w
        assert len(w["regions"]) == 6
        largest = max(w["regions"], key=lambda r: r["cells"])
        assert largest["cells"] == 2043877


# Basic checkMesh (no -allTopology) still detects multi-region via
# "Number of regions: N" line in topology section.

CHECKMESH_BASIC_MULTI_REGION = """\
Checking topology...
    Boundary definition OK.
    Cell to face addressing OK.
    Point usage OK.
    Upper triangular ordering OK.
    Face vertices OK.
    Number of regions: 3 (OK).

Checking geometry...
    Overall domain bounding box (0 0 0) (30 5 20)
    Max cell openness = 2e-16 OK.
    Max aspect ratio = 8.1 OK.
    Min volume = 1e-06. Max volume = 4e-05. Cell volumes OK.
    Mesh non-orthogonality Max: 45 average: 4.5
    Non-orthogonality check OK.
    Max skewness = 2.3 OK.

Mesh OK.
"""


class TestBasicCheckMeshMultiRegion:
    """Server endpoint runs checkMesh without -allTopology. The basic
    output has 'Number of regions: N' but no per-region detail lines.
    We must still emit a warning when N > 1."""

    def test_basic_multi_region_emits_warning(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_BASIC_MULTI_REGION)
        region_warns = [w for w in warnings if w["code"] == "disconnected_regions"]
        assert len(region_warns) == 1

    def test_basic_multi_region_count(self):
        warnings = parse_checkmesh_warnings(CHECKMESH_BASIC_MULTI_REGION)
        w = [w for w in warnings if w["code"] == "disconnected_regions"][0]
        assert w["count"] == 3

    def test_basic_multi_region_no_detail_regions(self):
        """Without -allTopology, per-region cell counts are unavailable."""
        warnings = parse_checkmesh_warnings(CHECKMESH_BASIC_MULTI_REGION)
        w = [w for w in warnings if w["code"] == "disconnected_regions"][0]
        assert w["regions"] == []

    def test_basic_single_region_no_warning(self):
        """'Number of regions: 1' must NOT trigger a warning."""
        warnings = parse_checkmesh_warnings(CHECKMESH_OK)
        region_warns = [w for w in warnings if w["code"] == "disconnected_regions"]
        assert len(region_warns) == 0


def test_full_geometry_interpolation_and_volume_failures_keep_counts_and_thresholds():
    output = """ ***Faces with small interpolation weight (< 0.05) found, number of faces: 31
 ***Faces with small volume ratio (< 1e-2) found, number of faces: 60
Failed 2 mesh checks."""
    findings = {w["code"]: w for w in parse_checkmesh_warnings(output)}
    assert findings["low_interpolation_weight"]["count"] == 31
    assert findings["low_interpolation_weight"]["threshold"] == 0.05
    assert findings["low_volume_ratio"]["count"] == 60
    assert findings["low_volume_ratio"]["threshold"] == 0.01
    assert all(w["level"] == 2 for w in findings.values())


def test_zero_count_geometry_reports_do_not_add_findings():
    output = """ ***Faces with small interpolation weight (< 0.05) found, number of faces: 0
 ***Faces with small volume ratio (< 0.01) found, number of faces: 0"""
    assert parse_checkmesh_warnings(output) == []
