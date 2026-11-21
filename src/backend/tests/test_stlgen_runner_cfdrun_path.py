"""STLGenRunner default-path contract: raises FileNotFoundError when no
.cfdrun exists in model/, with NO silent fallback to any other JSON manifest.

Policy source: docs/superpowers/specs/2026-04-17-cfdrun-naming-cleanup-design.md
"""
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


@pytest.fixture
def empty_case(tmp_path: Path) -> Path:
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_default_init_raises_when_no_cfdrun_exists(empty_case):
    """No .cfdrun in model/ → FileNotFoundError on load, even if another
    JSON manifest is present. No silent fallback to any non-.cfdrun file
    (spec §Data flow + the 2026-04-18 no-silent-fallback policy)."""
    # A stray JSON file in model/ must not act as a silent fallback.
    (empty_case / "model" / "stray_legacy.json").write_text(
        json.dumps({"room": {"height": 3.0}, "meshConfig": {}}), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError, match=r"no .*\.cfdrun"):
        STLGenRunner(empty_case)


def test_default_init_discovers_cfdrun_by_glob(empty_case):
    """When a *.cfdrun file exists in model/, the runner auto-picks it."""
    cfdrun_content = json.dumps({
        "room": {"height": 3.0, "plane": [], "locationInMesh": {"x": 0, "y": 0, "z": 1.5}},
        "meshConfig": {"baseSize": 0.2, "maxGlobalCells": 1000000,
                       "blockMeshBounds": {"min": {"x": 0, "y": 0, "z": 0},
                                           "max": {"x": 1, "y": 1, "z": 3}}},
        "floors": {},
    })
    (empty_case / "model" / "Base_demo.cfdrun").write_text(cfdrun_content, encoding="utf-8")
    runner = STLGenRunner(empty_case)
    assert runner.cfdrun_path.name == "Base_demo.cfdrun"


def test_multiple_cfdrun_files_raises(empty_case):
    """Ambiguous state: >1 .cfdrun in model/ → RuntimeError."""
    (empty_case / "model" / "A.cfdrun").write_text("{}", encoding="utf-8")
    (empty_case / "model" / "B.cfdrun").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"multiple.*\.cfdrun"):
        STLGenRunner(empty_case)


def test_explicit_cfdrun_path_overrides_glob(empty_case):
    """Passing cfdrun_path= bypasses the glob — explicit path wins."""
    (empty_case / "model" / "Ambiguous.cfdrun").write_text("{}", encoding="utf-8")
    elsewhere = empty_case / "model" / "Chosen.cfdrun"
    elsewhere.write_text(
        json.dumps({"room": {"height": 3.0}, "meshConfig": {}}),
        encoding="utf-8",
    )
    runner = STLGenRunner(empty_case, cfdrun_path=elsewhere)
    assert runner.cfdrun_path == elsewhere.resolve()
