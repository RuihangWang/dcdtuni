"""Tests for reconstructParMesh crash recovery in _run_parallel_mesh_pipeline."""
import pytest
from pathlib import Path


POLY_REQUIRED = ("points", "faces", "owner", "neighbour", "boundary")


class TestReconstructRecovery:
    """Verify the recovery logic when reconstructParMesh crashes."""

    def _make_poly_dir(self, tmp_path: Path, files: dict[str, str]) -> Path:
        poly = tmp_path / "constant" / "polyMesh"
        poly.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (poly / name).write_text(content)
        return poly

    def test_continues_when_all_mesh_files_exist_and_nonempty(self, tmp_path):
        """Crash recovery succeeds: all 5 files exist with content."""
        from backend.aircooling.runner.stlgen_runner import STLGenRunner

        poly = self._make_poly_dir(
            tmp_path, {f: "data" for f in POLY_REQUIRED}
        )
        assert STLGenRunner._verify_reconstructed_mesh(poly)

    def test_raises_when_mesh_file_missing(self, tmp_path):
        """Crash recovery fails: a required file is absent."""
        from backend.aircooling.runner.stlgen_runner import STLGenRunner

        files = {f: "data" for f in POLY_REQUIRED if f != "boundary"}
        poly = self._make_poly_dir(tmp_path, files)
        assert not STLGenRunner._verify_reconstructed_mesh(poly)

    def test_raises_when_mesh_file_empty(self, tmp_path):
        """Crash recovery fails: a required file exists but is 0 bytes."""
        from backend.aircooling.runner.stlgen_runner import STLGenRunner

        files = {f: "data" for f in POLY_REQUIRED}
        files["faces"] = ""  # empty
        poly = self._make_poly_dir(tmp_path, files)
        assert not STLGenRunner._verify_reconstructed_mesh(poly)

    def test_all_five_files_are_checked(self, tmp_path):
        """Each of the 5 required files must be present and non-empty."""
        import shutil
        from backend.aircooling.runner.stlgen_runner import STLGenRunner

        for missing in POLY_REQUIRED:
            sub = tmp_path / f"case_{missing}"
            files = {f: "data" for f in POLY_REQUIRED if f != missing}
            poly = self._make_poly_dir(sub, files)
            assert not STLGenRunner._verify_reconstructed_mesh(poly), (
                f"Should fail when '{missing}' is missing"
            )
