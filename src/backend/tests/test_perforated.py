"""Unit tests for the perforated-plate Idelchik formula."""
import math

import pytest

from backend.aircooling.runner.bridge_bc import (
    BCSpec,
    BridgeBCRegistry,
    PerforatedSpec,
    perforated_to_porous_baffle,
)
from backend.aircooling.runner.cfdrun_schema import CFDExport


def test_schema_preserves_perforated_on_baffle_physics():
    """Regression: BafflePhysics.perforated must survive pydantic
    model_validate / model_dump round-trip. A missing schema field
    silently degrades every perforated baffle to wall_baffle because the
    perforated-physics lookup reads phys.get('perforated')."""
    raw = {
        "version": "1.0",
        "source": "frontend",
        "projectName": "test",
        "room": {"height": 3.0, "plane": [], "locationInMesh": {"x": 0, "y": 0, "z": 0}},
        "baffles": {
            "B1": {
                "stlFile": "merged_baffle.stl",
                "physics": {
                    "baffleType": "perforated",
                    "perforated": {"shape": "rect", "holeDiameterMm": 5.0, "openAreaPct": 40.0},
                },
            }
        },
    }
    dumped = CFDExport.model_validate(raw).model_dump(mode="json")
    perf = dumped["baffles"]["B1"]["physics"]["perforated"]
    assert perf is not None, "perforated dropped by schema"
    assert perf["shape"] == "rect"
    assert perf["openAreaPct"] == 40.0


def test_schema_preserves_perforated_on_floor_opening_physics():
    """Regression: FloorOpeningPhysics.perforated must survive."""
    raw = {
        "version": "1.0",
        "source": "frontend",
        "projectName": "test",
        "room": {"height": 3.0, "plane": [], "locationInMesh": {"x": 0, "y": 0, "z": 0}},
        "floors": {
            "F1": {
                "stlFile": "merged_floor_ceiling.stl",
                "openings": [{
                    "solidName": "Op1",
                    "physics": {
                        "type": "perforated",
                        "perforated": {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0},
                    },
                }],
            }
        },
    }
    dumped = CFDExport.model_validate(raw).model_dump(mode="json")
    perf = dumped["floors"]["F1"]["openings"][0]["physics"]["perforated"]
    assert perf is not None, "floor opening perforated dropped by schema"
    assert perf["openAreaPct"] == 64.0


GOLDEN_CIRCLE = [
    # (open_area_pct, expected_K)  — φ=1, computed analytically from
    # K = (1 + 0.707·√(1-σ) - σ)² / σ²  (Idelchik §8.2 eq. 8-4)
    (20.0, 51.27),
    (40.0, 8.232),
    (64.0, 1.499),
    (80.0, 0.416),
]


@pytest.mark.parametrize("open_area_pct,expected_K", GOLDEN_CIRCLE)
def test_circle_golden_values(open_area_pct, expected_K):
    spec = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=open_area_pct)
    D, I, length = perforated_to_porous_baffle(spec)
    assert D == 0.0
    assert length == 1.0
    assert math.isclose(I, expected_K, rel_tol=0.01), f"I={I} vs expected {expected_K}"


def test_shape_ordering_at_fixed_sigma():
    """At σ=0.5, K(circle) < K(rect) < K(diamond) — φ rises with perimeter."""
    kwargs = dict(holeDiameterMm=3.0, openAreaPct=50.0)
    _, k_circle, _ = perforated_to_porous_baffle(PerforatedSpec(shape="circle", **kwargs))
    _, k_rect, _ = perforated_to_porous_baffle(PerforatedSpec(shape="rect", **kwargs))
    _, k_diamond, _ = perforated_to_porous_baffle(PerforatedSpec(shape="diamond", **kwargs))
    assert k_circle < k_rect < k_diamond


def test_sigma_extremes_are_clamped():
    """σ=0 would divide by zero; must clamp to avoid NaN/Inf."""
    spec = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=0.0)
    _, I, _ = perforated_to_porous_baffle(spec)
    assert math.isfinite(I) and I > 1e4  # large but finite
    spec = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=100.0)
    _, I, _ = perforated_to_porous_baffle(spec)
    assert math.isfinite(I) and I >= 0.0


def test_unknown_shape_falls_back_to_circle():
    spec = PerforatedSpec(shape="octagon", holeDiameterMm=3.0, openAreaPct=64.0)
    _, I_hex, _ = perforated_to_porous_baffle(spec)
    _, I_circle, _ = perforated_to_porous_baffle(
        PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=64.0)
    )
    assert math.isclose(I_hex, I_circle, rel_tol=1e-9)


def test_bcspec_accepts_perforated_field():
    spec = BCSpec(
        bc_type="perforated_baffle",
        perforated=PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=64.0),
    )
    assert spec.perforated is not None
    assert spec.perforated.openAreaPct == 64.0


def test_bcspec_registry_roundtrip():
    reg = BridgeBCRegistry()
    reg.register("baffle_A_master", BCSpec(
        bc_type="perforated_baffle",
        perforated=PerforatedSpec(openAreaPct=40.0),
    ))
    got = reg.get("baffle_A_master")
    assert got is not None
    assert got.bc_type == "perforated_baffle"
    assert got.perforated.openAreaPct == 40.0


def test_fields_emits_porous_baffle_from_perforated_spec():
    """When BCSpec.perforated is populated, fields.py must compute I from
    the Idelchik formula (ignoring the legacy sigma field)."""
    from backend.aircooling.runner.fields import _format_porous_baffle_block

    spec = BCSpec(
        bc_type="perforated_baffle",
        perforated=PerforatedSpec(shape="circle", openAreaPct=64.0),
    )
    lines, D, I, length = _format_porous_baffle_block("baffle_A_master", spec)
    text = "\n".join(lines)
    assert "type            porousBafflePressure" in text
    assert "patchType       cyclic" in text
    assert "length          1" in text
    assert "D               0.000000" in text
    # I ≈ 1.499 for σ=0.64
    match = [ln for ln in lines if "I " in ln]
    assert match, f"no I line in {lines}"
    import re
    m = re.search(r"I\s+([\d.]+)", match[0])
    assert m, match[0]
    assert abs(float(m.group(1)) - 1.499) < 0.01
    # The returned tuple must match the formatted text.
    assert D == 0.0
    assert abs(I - 1.499) < 0.01
    assert length == 1.0


def test_fields_falls_back_to_sigma_when_perforated_missing():
    """Legacy rack-leakage BCSpecs (sigma only, no perforated) keep
    the old viscous formula so no rack-path regression."""
    from backend.aircooling.runner.fields import _format_porous_baffle_block
    spec = BCSpec(bc_type="perforated_baffle", sigma=0.5)
    lines, D, I, length = _format_porous_baffle_block("rack_leakage_zone", spec)
    text = "\n".join(lines)
    assert "porousBafflePressure" in text
    # Legacy: D = (1-0.25)/(2·0.25) = 1.5, I = 0
    import re
    m_d = re.search(r"D\s+([\d.]+)", text)
    m_i = re.search(r"I\s+([\d.]+)", text)
    assert m_d and m_i
    assert abs(float(m_d.group(1)) - 1.5) < 0.01
    assert abs(float(m_i.group(1))) < 1e-9
    assert abs(D - 1.5) < 0.01
    assert I == 0.0


def test_stlgen_runner_builds_perforated_physics_lookup():
    """Physics dict built from cfdrun carries the perforated block for
    baffles whose physics.baffleType == 'perforated'."""
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    runner = STLGenRunner.__new__(STLGenRunner)  # skip __init__
    runner._baffle_physics = {}
    runner._stl_solids = {"merged_baffle.stl": ["Baffle_1"]}  # populate so the 2nd-pass branch handles the mock
    runner._data = {
        "baffles": {
            "Baffle_1": {
                "physics": {
                    "baffleType": "perforated",
                    "perforated": {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0},
                },
                "solid": "Baffle_1",
            }
        }
    }
    runner._build_baffle_physics_lookup()
    phys = runner._baffle_physics["Baffle_1"]
    assert phys["baffleType"] == "perforated"
    assert phys["perforated"]["openAreaPct"] == 64.0


def test_partition_wall_leakage_gated_on_rate_only(tmp_path):
    """Partition-wall hole physics with leakageRate < 100 (and no
    `resistanceEnabled` key) must still produce a populated fvOptions
    cellzone config with sigma derived from leakageRate."""
    from pathlib import Path
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    # Build a minimal helper STL so the centroid lookup succeeds.
    surf_dir = tmp_path / "constant" / "triSurface"
    surf_dir.mkdir(parents=True, exist_ok=True)
    zone_file = surf_dir / "partition_hole_part_hole_1_zone.stl"
    surface_name = "partition_hole_part_hole_1_zone"
    zone_file.write_text(
        f"solid {surface_name}\n"
        "  facet normal 0 1 0\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 0 1\n"
        "    endloop\n"
        "  endfacet\n"
        f"endsolid {surface_name}\n"
    )

    runner = STLGenRunner.__new__(STLGenRunner)
    runner.tri_surface_dir = surf_dir
    runner._data = {
        "partitionWalls": {
            "part": {
                "geometry": {
                    "size": {"D": 0.05},
                    "transformQuaternion": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                },
                "holes": [{
                    "patchId": "p1",
                    "shape": "rectangle",
                    "name": "hole_1",
                    "zoneName": "part_hole_1_zone",
                    "zoneStlFile": zone_file.name,
                    "physics": {"leakageRate": 50.0},
                }],
            },
        },
    }
    cfg = runner._build_partition_hole_fvoptions_config()
    assert cfg, "expected partition-hole cellzone config when leakageRate < 100"
    assert surface_name in cfg
    entry = cfg[surface_name]
    assert abs(entry.sigma - 0.5) < 1e-6


def test_partition_wall_leakage_100pct_skipped(tmp_path):
    """leakageRate == 100 (no resistance) must skip the cellzone config."""
    from pathlib import Path
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    surf_dir = tmp_path / "constant" / "triSurface"
    surf_dir.mkdir(parents=True, exist_ok=True)
    zone_file = surf_dir / "partition_hole_part_hole_1_zone.stl"
    zone_file.write_text(
        "solid x\n  facet normal 0 1 0\n    outer loop\n"
        "      vertex 0 0 0\n      vertex 1 0 0\n      vertex 0 0 1\n"
        "    endloop\n  endfacet\nendsolid x\n"
    )

    runner = STLGenRunner.__new__(STLGenRunner)
    runner.tri_surface_dir = surf_dir
    runner._data = {
        "partitionWalls": {
            "part": {
                "geometry": {
                    "size": {"D": 0.05},
                    "transformQuaternion": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                },
                "holes": [{
                    "patchId": "p1",
                    "shape": "rectangle",
                    "name": "hole_1",
                    "zoneName": "part_hole_1_zone",
                    "zoneStlFile": zone_file.name,
                    "physics": {"leakageRate": 100.0},
                }],
            },
        },
    }
    cfg = runner._build_partition_hole_fvoptions_config()
    assert cfg == {}, f"expected empty config at 100% leakage, got {cfg}"


def test_hexagon_sits_between_circle_and_rect():
    from backend.aircooling.runner.bridge_bc import _SHAPE_PHI
    assert _SHAPE_PHI["circle"] < _SHAPE_PHI["hexagon"] < _SHAPE_PHI["rect"]
