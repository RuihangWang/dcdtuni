"""Shadow-drift check for the cfdrun V3 Stage-1 resolver.

`shadow_drift(raw)` is the runner-facing API (used behind DCDT_CFDRUN_V3_SHADOW).
It must report zero drift for the 7 stakeholder cases: the backend-derived config
equals the meshGeometry blob config the runner uses today. The manifests are
the frozen config goldens test_derived_config_parity.py reads (case/*/model is
gitignored).
"""
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.derived_config import derive_run_config, shadow_drift

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "cfdrun_cases" / "configs"
CASES = [
    "Base_leakage", "Alibaba-sige", "Base604", "GTC",
    "Tri_angle", "Slab", "Shinaide_Liquid",
]


def _raw(case: str):
    path = GOLDEN_DIR / f"{case}.json"
    assert path.is_file(), f"no frozen .cfdrun config for {case}: {path}"
    return json.loads(path.read_text())


@pytest.mark.parametrize("case", CASES)
def test_no_shadow_drift(case):
    raw = _raw(case)
    # `meshMode` is retired (2026-09-09): the derived value follows meshEngine,
    # while exports made before that carry the old 'structured'. Give the blob
    # the derived value so the key drops out of the comparison, as the parity
    # tests do; everything else must still match.
    # `meshEngine` is likewise newer than these goldens (the frontend emits it
    # now; test_derived_config_parity::test_v4_export_mesh_config_parity pins
    # it against a current export): a golden without it takes the derived one.
    blob_mc = (raw.get("meshGeometry") or {}).get("meshConfig")
    if blob_mc is not None:
        derived = derive_run_config(raw)["meshConfig"]
        blob_mc["meshMode"] = derived["meshMode"]
        blob_mc.setdefault("meshEngine", derived.get("meshEngine"))
    assert shadow_drift(raw) == [], f"{case}: shadow drift"
