"""Tests for MeshPreviewCache mtime-based invalidation."""
import time
import pytest
from pathlib import Path


class TestMeshPreviewCache:
    """Verify the MeshPreviewCache wrapper."""

    def _make_boundary(self, mesh_dir: Path, content: str = "boundary data") -> Path:
        poly = mesh_dir / "constant" / "polyMesh"
        poly.mkdir(parents=True, exist_ok=True)
        bp = poly / "boundary"
        bp.write_text(content)
        return bp

    def test_creates_loader_on_first_call(self, tmp_path):
        from backend.aircooling.runner.postprocess import MeshPreviewCache

        cache = MeshPreviewCache()
        mesh_dir = tmp_path / "Mesh"
        self._make_boundary(mesh_dir)

        loader = cache.get(mesh_dir)
        assert loader is not None
        assert str(loader.mesh_dir) == str(mesh_dir.resolve())

    def test_returns_cached_loader_when_mtime_unchanged(self, tmp_path):
        from backend.aircooling.runner.postprocess import MeshPreviewCache

        cache = MeshPreviewCache()
        mesh_dir = tmp_path / "Mesh"
        self._make_boundary(mesh_dir)

        loader1 = cache.get(mesh_dir)
        loader2 = cache.get(mesh_dir)
        assert loader1 is loader2

    def test_invalidates_cache_when_boundary_mtime_changes(self, tmp_path):
        from backend.aircooling.runner.postprocess import MeshPreviewCache

        cache = MeshPreviewCache()
        mesh_dir = tmp_path / "Mesh"
        bp = self._make_boundary(mesh_dir, "v1")

        loader1 = cache.get(mesh_dir)

        time.sleep(0.05)
        bp.write_text("v2")

        loader2 = cache.get(mesh_dir)
        assert loader2 is not loader1

    def test_invalidates_cache_when_mesh_dir_changes(self, tmp_path):
        from backend.aircooling.runner.postprocess import MeshPreviewCache

        cache = MeshPreviewCache()
        dir_a = tmp_path / "A" / "Mesh"
        dir_b = tmp_path / "B" / "Mesh"
        self._make_boundary(dir_a)
        self._make_boundary(dir_b)

        loader_a = cache.get(dir_a)
        loader_b = cache.get(dir_b)
        assert loader_a is not loader_b

    def test_no_monkey_patched_attributes_on_loader(self, tmp_path):
        """mtime must NOT be stored on the MeshPreviewLoader instance."""
        from backend.aircooling.runner.postprocess import MeshPreviewCache

        cache = MeshPreviewCache()
        mesh_dir = tmp_path / "Mesh"
        self._make_boundary(mesh_dir)

        loader = cache.get(mesh_dir)
        assert not hasattr(loader, '_boundary_mtime'), (
            "_boundary_mtime should not be monkey-patched onto the loader"
        )

    def test_reset_clears_cache(self, tmp_path):
        from backend.aircooling.runner.postprocess import MeshPreviewCache

        cache = MeshPreviewCache()
        mesh_dir = tmp_path / "Mesh"
        self._make_boundary(mesh_dir)

        loader1 = cache.get(mesh_dir)
        cache.reset()
        loader2 = cache.get(mesh_dir)
        assert loader2 is not loader1
