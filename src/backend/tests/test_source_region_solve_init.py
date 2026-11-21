"""Full load on an actual React-exported region case must pin the owned input.
The complete solve-only transition with native/quality receipts is independently
exercised in the frozen browser-regression packet; this guards its load entry.
"""
from pathlib import Path
import hashlib
import shutil
import pytest
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
from backend.aircooling.runner.stlgen_runner import STLGenRunner

FIXTURE = Path(__file__).parent / "fixtures/source_region_solve_init"

@pytest.mark.parametrize("source_mode", ["file", "owned_snapshot"])
def test_full_load_pins_regions_only_manifest_from_real_constructor(tmp_path, source_mode):
    case = tmp_path / "case"
    logical = case / "model" / "actual_gui_solve.cfdrun"
    logical.parent.mkdir(parents=True)
    payload = (FIXTURE / "actual_gui_solve.cfdrun").read_bytes()
    logical.write_bytes(payload)
    tri = case / "Mesh" / "constant" / "triSurface"
    shutil.copytree(FIXTURE / "triSurface", tri)
    sha = hashlib.sha256(payload).hexdigest()
    kwargs = {}
    if source_mode == "owned_snapshot":
        kwargs["input_snapshot"] = CfdrunSnapshot(payload, case, logical,
            expected_sha256=sha, stl_batch_sha256=surface_batch_sha256(case))
        # The RAM source remains authoritative even when the optional replay
        # artifact changes; this tests the actual full load, not JSON-only load.
        logical.write_bytes(b"optional replay artifact")
    runner = STLGenRunner(case, logical, expected_cfdrun_sha256=sha, **kwargs)
    assert not getattr(runner, "_verified_source_cfdrun_sha256", None)
    runner._load()
    assert runner._verified_source_cfdrun_sha256 == sha
    assert runner._pinned_source_manifest()["geometryExport"]["sourceRegions"]
    assert runner._read_cfdrun_bytes() == payload
