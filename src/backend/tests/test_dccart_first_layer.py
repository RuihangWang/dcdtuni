"""dcCartMeshDict carries the first-layer floor keys (2026-09-22).

dcCartMesh reads ``firstLayerMinFraction`` / ``firstLayerMax`` /
``firstLayerSealGap`` from system/dcCartMeshDict: on top of the
near-coincidence weld, every axis-aligned surface class whose coordinate lies
within ``firstLayerMinFraction x (its own refinement-level spacing)`` (capped
at ``firstLayerMax`` metres) of a grid plane of that level is moved onto it,
so no wall carves a slab cell thinner than that floor -- the 6Sigma
snap-to-grid guarantee at the cut-cell scale. The runner writes all three keys
itself so a click in the web frontend and a manifest agree: the defaults are
the module constants, a manifest value travels as given (fraction and cap
clamped at 0; the seal gap may be negative, meaning "the weld tolerance").
"""
from pathlib import Path

import pytest

from backend.aircooling.runner import stlgen_runner as sr
from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig, MeshConfig
from backend.aircooling.runner.derived_config import derive_mesh_config
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _derived(mc: dict) -> dict:
    return derive_mesh_config(mc, {}, block_mesh_bounds=None)


def _dict_text(tmp_path: Path, mesh_config: dict) -> str:
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r._data = {"meshConfig": {"meshEngine": "dcCart", **mesh_config}}
    r._bg_grid_planes = {axis: [0.0, 1.0] for axis in ("x", "y", "z")}
    r._write_dcCart_mesh_dict(tmp_path)
    return (tmp_path / "dcCartMeshDict").read_text()


def test_defaults_are_the_module_constants(tmp_path):
    text = _dict_text(tmp_path, {})
    assert f"firstLayerMinFraction {sr.DCCART_FIRST_LAYER_MIN_FRACTION_DEFAULT:g};" in text
    assert f"firstLayerMax       {sr.DCCART_FIRST_LAYER_MAX_DEFAULT:g};" in text
    assert f"firstLayerSealGap   {sr.DCCART_FIRST_LAYER_SEAL_GAP_DEFAULT:g};" in text


@pytest.mark.parametrize("key, value, expected", [
    ("firstLayerMinFraction", 0.3, "firstLayerMinFraction 0.3;"),
    ("firstLayerMinFraction", "0.25", "firstLayerMinFraction 0.25;"),
    ("firstLayerMinFraction", -0.2, "firstLayerMinFraction 0;"),      # clamped: off
    ("firstLayerMax", 0.01, "firstLayerMax       0.01;"),
    ("firstLayerMax", -1, "firstLayerMax       0;"),                  # clamped: no cap
    ("firstLayerSealGap", 0.004, "firstLayerSealGap   0.004;"),
    ("firstLayerSealGap", -1, "firstLayerSealGap   -1;"),             # negative = weldTolerance
])
def test_manifest_value_is_honoured(tmp_path, key, value, expected):
    assert expected in _dict_text(tmp_path, {key: value})


@pytest.mark.parametrize("value", [None, "junk", ""])
def test_unusable_value_falls_back_to_default(tmp_path, value):
    text = _dict_text(tmp_path, {"firstLayerMinFraction": value, "firstLayerMax": value})
    assert f"firstLayerMinFraction {sr.DCCART_FIRST_LAYER_MIN_FRACTION_DEFAULT:g};" in text
    assert f"firstLayerMax       {sr.DCCART_FIRST_LAYER_MAX_DEFAULT:g};" in text


def test_weld_line_still_written_next_to_the_floor(tmp_path):
    text = _dict_text(tmp_path, {"firstLayerMinFraction": 0.3})
    assert "weldTolerance       0.0012;" in text
    assert text.index("weldTolerance") < text.index("firstLayerMinFraction")


def test_schema_declares_the_keys():
    # extra="forbid" on the V2 MeshConfig: an undeclared key would reject every
    # manifest that carries it.
    m = MeshConfig(firstLayerMinFraction=0.3, firstLayerMax=0.004, firstLayerSealGap=0.002)
    assert (m.firstLayerMinFraction, m.firstLayerMax, m.firstLayerSealGap) == (0.3, 0.004, 0.002)
    e = CFDExportMeshConfig(firstLayerMinFraction=0)
    assert e.firstLayerMinFraction == 0
    assert CFDExportMeshConfig().firstLayerMax is None


def test_derived_config_passes_the_keys_through_only_when_set():
    out = _derived({"meshEngine": "dcCart", "firstLayerMinFraction": 0.3, "firstLayerMax": 0.004})
    assert out["firstLayerMinFraction"] == 0.3
    assert out["firstLayerMax"] == 0.004
    assert "firstLayerSealGap" not in out
    absent = _derived({"meshEngine": "dcCart"})
    assert not any(k in absent for k in ("firstLayerMinFraction", "firstLayerMax", "firstLayerSealGap"))
