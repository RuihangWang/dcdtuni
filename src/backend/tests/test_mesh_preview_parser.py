"""Regression tests for MeshPreviewLoader parser rewrite (numpy bulk parsing)."""
import pytest
from pathlib import Path
from backend.aircooling.runner.postprocess import MeshPreviewLoader

CASE_DIR = Path(__file__).resolve().parents[3] / "case"

MESH_DIR_SMALL = CASE_DIR / "Base_leakage" / "Mesh"
MESH_DIR_TINY = CASE_DIR / "DAML-Testbed" / "Mesh"


def _have_mesh(mesh_dir: Path) -> bool:
    return (mesh_dir / "constant" / "polyMesh" / "boundary").exists()


@pytest.mark.skipif(
    not _have_mesh(MESH_DIR_SMALL),
    reason="Base_leakage mesh not available",
)
class TestParsePointsSmall:
    def test_points_shape(self):
        pts = MeshPreviewLoader._parse_points(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "points"
        )
        assert pts.ndim == 2
        assert pts.shape[1] == 3
        assert pts.shape[0] > 100_000

    def test_points_not_nan(self):
        pts = MeshPreviewLoader._parse_points(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "points"
        )
        import numpy as np
        assert not np.any(np.isnan(pts))

    def test_points_reasonable_range(self):
        pts = MeshPreviewLoader._parse_points(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "points"
        )
        assert pts.min() > -2.0
        assert pts.max() < 12.0


@pytest.mark.skipif(
    not _have_mesh(MESH_DIR_SMALL),
    reason="Base_leakage mesh not available",
)
class TestParseFacesSmall:
    def test_faces_count(self):
        boundary = MeshPreviewLoader._parse_boundary(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "boundary"
        )
        assert len(boundary) > 0
        min_start = min(p["startFace"] for p in boundary)
        max_end = max(p["startFace"] + p["nFaces"] for p in boundary)
        total = max_end - min_start
        faces = MeshPreviewLoader._parse_faces_range(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "faces",
            min_start,
            total,
        )
        assert len(faces) == total

    def test_faces_have_vertices(self):
        boundary = MeshPreviewLoader._parse_boundary(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "boundary"
        )
        min_start = min(p["startFace"] for p in boundary)
        faces = MeshPreviewLoader._parse_faces_range(
            MESH_DIR_SMALL / "constant" / "polyMesh" / "faces",
            min_start,
            100,
        )
        for face in faces:
            assert len(face) >= 3
            assert all(isinstance(v, int) for v in face)


@pytest.mark.skipif(
    not _have_mesh(MESH_DIR_SMALL),
    reason="Base_leakage mesh not available",
)
class TestFullLoadSmall:
    def test_load_returns_data(self):
        loader = MeshPreviewLoader(MESH_DIR_SMALL)
        binary, meta = loader.load()
        assert len(binary) > 0
        assert len(meta) > 0

    def test_load_patch_metadata(self):
        loader = MeshPreviewLoader(MESH_DIR_SMALL)
        _, meta = loader.load()
        for m in meta:
            assert "name" in m
            assert "nPoints" in m
            assert "nTriangles" in m
            assert m["nPoints"] > 0
            assert m["nTriangles"] > 0

    def test_load_idempotent(self):
        loader = MeshPreviewLoader(MESH_DIR_SMALL)
        b1, m1 = loader.load()
        b2, m2 = loader.load()
        assert b1 is b2
        assert m1 is m2
