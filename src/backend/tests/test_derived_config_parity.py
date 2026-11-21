"""Golden-parity tests for the cfdrun V3 Stage-1 config resolver.

Golden truth = the meshGeometry.{meshConfig,simulationConfig} block in each
case's browser-exported .cfdrun (what the frontend buildCfdrunJson() produces).
The backend resolver must reproduce it byte-exact from ROOT config + the 3
carried geometry/UI fields.

case/*/model is gitignored export output, so the goldens are frozen copies in
fixtures/cfdrun_cases/configs/<case>.json: the four config blocks the resolver
reads (root and meshGeometry meshConfig/simulationConfig) plus the export's
schemaVersion/name/runId, which date it. To refresh one, re-export the case and
copy those keys over.
"""
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.derived_config import (
    derive_mesh_config,
    derive_run_config,
    derive_sim_config,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "cfdrun_cases" / "configs"

# Stakeholder-selected primary verification cases.
CASE_DIRS = [
    "Base_leakage", "Alibaba-sige", "Base604", "GTC",
    "Tri_angle", "Slab", "Shinaide_Liquid",
]

# Broader guard: every named (non-dataset) case that has a meshGeometry block.
# Liquid, LiquidSingle and Polygon_Room have no golden: their exports predate
# meshMode (legacy cartesianMode) and must be re-exported before they can join.
ALL_NAMED = CASE_DIRS + [
    "Alibaba-hongchou", "Base2-9", "Base_coldAisleIso", "CMI-China",
    "CMI-Guichou", "DAML-Testbed", "STDCT", "Shinaide", "SingleRack",
]


def _load(case: str):
    path = GOLDEN_DIR / f"{case}.json"
    assert path.is_file(), f"no frozen .cfdrun config for {case}: {path}"
    raw = json.loads(path.read_text())
    mg = raw.get("meshGeometry") or {}
    return raw, mg


def _is_current_format(blob_mesh_config: dict) -> bool:
    """The current frontend emits `meshMode` and no longer emits the legacy
    `cartesianMode` boolean. An on-disk .cfdrun that still carries
    `cartesianMode` (or lacks `meshMode`) is a stale export from before the
    structured-default change (commit 5af4e80) and must be re-exported before it
    can be compared byte-exact against the current resolver."""
    return "meshMode" in blob_mesh_config and "cartesianMode" not in blob_mesh_config


@pytest.mark.parametrize("case", CASE_DIRS)
def test_mesh_config_parity(case):
    raw, mg = _load(case)
    blob_mc = mg["meshConfig"]
    got = derive_mesh_config(
        raw.get("meshConfig") or {},
        raw.get("simulationConfig") or {},
        block_mesh_bounds=blob_mc.get("blockMeshBounds"),
    )
    # `meshMode` is retired (2026-09-09): the runner derives the semantics
    # from meshEngine, and exports made before that carry the old
    # 'structured' value. Everything else must still match byte for byte.
    got.pop("meshMode", None); blob_mc = dict(blob_mc); blob_mc.pop("meshMode", None)
    # `meshEngine` came after these goldens were exported (pinned against a
    # current export by test_v4_export_mesh_config_parity): absent in one, it
    # is not compared.
    if "meshEngine" not in blob_mc:
        got.pop("meshEngine", None)
    assert got == blob_mc, f"{case}: meshConfig mismatch"


@pytest.mark.parametrize("case", CASE_DIRS)
def test_sim_config_parity(case):
    raw, mg = _load(case)
    blob_sc = mg["simulationConfig"]
    got = derive_sim_config(
        raw.get("simulationConfig") or {},
        room_temperature=blob_sc["roomTemperature"],
        monitoring=blob_sc["monitoring"],
    )
    assert got == blob_sc, f"{case}: simulationConfig mismatch"


@pytest.mark.parametrize("case", ALL_NAMED)
def test_run_config_facade_parity(case):
    raw, mg = _load(case)
    if not _is_current_format(mg.get("meshConfig") or {}):
        pytest.skip(f"{case}: stale-format .cfdrun (legacy cartesianMode / no meshMode) — re-export to include")
    got = derive_run_config(raw)
    got_mc = dict(got["meshConfig"]); got_mc.pop("meshMode", None)
    mg_mc = dict(mg["meshConfig"]); mg_mc.pop("meshMode", None)
    if "meshEngine" not in mg_mc:
        got_mc.pop("meshEngine", None)
    assert got_mc == mg_mc, f"{case}: meshConfig"
    assert got["simulationConfig"] == mg["simulationConfig"], f"{case}: simulationConfig"


def test_stakeholder_cases_are_current_format():
    """Guard: none of the 7 primary cases may be stale-format (they gate P1)."""
    stale = [c for c in CASE_DIRS if not _is_current_format(_load(c)[1].get("meshConfig") or {})]
    assert not stale, f"stakeholder cases have stale exports, re-export first: {stale}"


V4_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "cfdrun_cases" / "configs_v4" / "Tri_angle.json"


def test_v4_export_mesh_config_parity():
    """A current (schema 4) browser export: the resolver reproduces its
    meshGeometry.meshConfig byte for byte, meshEngine included."""
    raw = json.loads(V4_GOLDEN.read_text())
    assert raw["schemaVersion"] == 4 and "meshEngine" in raw["meshGeometry"]["meshConfig"]
    assert derive_run_config(raw)["meshConfig"] == raw["meshGeometry"]["meshConfig"]
