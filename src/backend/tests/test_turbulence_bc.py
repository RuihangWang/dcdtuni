"""Unit tests for turbulence BC physical-grounding refactor.

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md
"""
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from loguru import logger as loguru_logger

from backend.aircooling.runner.fields import FieldGenerator
from backend.aircooling.runner.stlgen_runner import (
    PatchGeometry,
    _compute_boundary_perimeter,
)


# --- Test 5: perimeter extraction from 2 triangles forming a rectangle -----

def test_perimeter_extraction_from_2_triangles():
    """Two triangles sharing one diagonal → boundary = 4 rectangle edges (1.04m)."""
    # Rectangle in XY plane: 0.12m wide × 0.40m tall, origin at (0, 0, 0)
    # Triangle 1: (0,0,0) - (0.12,0,0) - (0.12,0.40,0)
    # Triangle 2: (0,0,0) - (0.12,0.40,0) - (0,0.40,0)
    # Shared edge: (0,0,0) - (0.12,0.40,0)  → NOT on boundary
    triangles = [
        ((0.0, 0.0, 0.0), (0.12, 0.0, 0.0), (0.12, 0.40, 0.0)),
        ((0.0, 0.0, 0.0), (0.12, 0.40, 0.0), (0.0, 0.40, 0.0)),
    ]
    perimeter = _compute_boundary_perimeter(triangles)
    expected = 2 * (0.12 + 0.40)  # = 1.04
    assert perimeter == pytest.approx(expected, rel=1e-4)


# --- Test 6: perimeter fallback when extraction yields zero ---------------

def test_perimeter_zero_for_closed_shell():
    """A patch where every edge is shared by 2+ triangles (closed topology)
    has no boundary → perimeter = 0. Caller falls back to circular D_h."""
    # Tetrahedron: 4 triangles, each edge shared by exactly 2 faces → closed.
    a = (0.0, 0.0, 0.0)
    b = (1.0, 0.0, 0.0)
    c = (0.5, 1.0, 0.0)
    d = (0.5, 0.5, 1.0)
    triangles = [
        (a, b, c),  # bottom
        (a, b, d),  # front
        (b, c, d),  # right
        (a, c, d),  # left
    ]
    perimeter = _compute_boundary_perimeter(triangles)
    assert perimeter == pytest.approx(0.0, abs=1e-9)


def test_perimeter_empty_input():
    """Zero triangles → perimeter 0."""
    assert _compute_boundary_perimeter([]) == 0.0


# ---------------------------------------------------------------------------
# Test helpers for FieldGenerator-scoped tests
# ---------------------------------------------------------------------------


class _FakeBridgeRegistry:
    """Minimal stand-in for BridgeBCRegistry.items() iteration."""

    def __init__(self, entries: Dict[str, Dict[str, Any]]):
        self._entries = entries

    def items(self):
        # FieldGenerator._collect_imported_bc_data iterates as (patch, spec)
        # and reads spec.bc_type, .flow_rate, etc. We mimic with SimpleNamespace.
        for patch, spec in self._entries.items():
            yield patch, SimpleNamespace(
                bc_type=spec.get("bc_type"),
                temperature=spec.get("temperature"),
                flow_rate=spec.get("flow_rate"),
                power=spec.get("power"),
                sigma=spec.get("sigma"),
                perforated=spec.get("perforated"),
                heat_flux=spec.get("heat_flux"),
                physical_flow_area=spec.get("physical_flow_area"),
            )

    def __iter__(self):
        return iter(self._entries.keys())

    def __len__(self):
        return len(self._entries)


def _make_field_gen(
    tmp_path: Path,
    patch_name: str,
    bc_type: str,
    flow_rate: float,
    area: float,
    perimeter: float,
    bbox_dims=(0.12, 0.40, 0.0),
) -> FieldGenerator:
    """Construct a FieldGenerator with a single patch in its bridge registry."""
    geom = {patch_name: PatchGeometry(area=area, perimeter=perimeter, bbox_dims=bbox_dims)}
    reg = _FakeBridgeRegistry({patch_name: {"bc_type": bc_type, "flow_rate": flow_rate}})
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={},
        room_config={},
        patch_geometry=geom,
        bridge_registry=reg,
    )
    # Force lazy collect
    fg._collect_data()
    return fg


# --- Test 1: fan_outlet uses I=10% -----------------------------------------

def test_compute_turbulence_bc_fan_outlet_uses_10_percent(tmp_path):
    """fan_outlet bc_type → I=0.10, and the computed k matches 1.5·(I·U)²."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, I, L, *_ = result
    assert I == pytest.approx(0.10, rel=1e-9)
    # U = 0.1553/0.048 = 3.2354  →  k = 1.5·(0.10·3.2354)² = 0.15702
    assert k == pytest.approx(0.15702, rel=1e-3)


# --- Test 2: acu_supply uses I=10% -----------------------------------------

def test_compute_turbulence_bc_acu_supply_uses_10_percent(tmp_path):
    """acu_supply bc_type → I=0.10, k = 1.5·(0.10·10)² = 1.5."""
    fg = _make_field_gen(
        tmp_path, "acu_top", bc_type="acu_supply",
        flow_rate=10.0, area=1.0, perimeter=4.0,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    k, eps, I, L, *_ = result
    assert I == pytest.approx(0.10, rel=1e-9)
    assert k == pytest.approx(1.5, rel=1e-3)


# --- Test 3: unknown bc_type falls back to default I ------------------------

def test_compute_turbulence_bc_unknown_bc_type_falls_back(tmp_path):
    """Unmapped bc_type → TURBULENCE_INTENSITY_DEFAULT (0.05), warning logged.

    loguru does not integrate with pytest caplog by default; we use a custom
    sink that captures messages into a list for this test.
    """
    messages: list = []
    sink_id = loguru_logger.add(lambda msg: messages.append(str(msg)), level="WARNING")
    try:
        fg = _make_field_gen(
            tmp_path, "mystery", bc_type="unknown_type",
            flow_rate=1.0, area=0.1, perimeter=1.3,
        )
        result = fg._compute_turbulence_bc("mystery")
    finally:
        loguru_logger.remove(sink_id)

    assert result is not None
    _k, _eps, I, _L, *_ = result
    assert I == pytest.approx(0.05, rel=1e-9)
    assert any("unknown_type" in m for m in messages), (
        f"Expected warning about 'unknown_type' in captured messages: {messages!r}"
    )


# --- Test 4: rectangular D_h (4A/P) vs circular (2·√(A/π)) ------------------

def test_rectangular_Dh_used_when_perimeter_positive(tmp_path):
    """Given 0.12×0.40 rectangle (A=0.048, P=1.04):
       D_h_rect = 4A/P   = 0.18462
       D_h_circ = 2√(A/π) = 0.24726
       L = 0.07·D_h_rect = 0.01292
       ε = 0.09^0.75 · k^1.5 / L
    Assert ε matches rectangular-D_h derivation, not circular."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, _I, L, *_ = result
    assert L == pytest.approx(0.07 * (4 * 0.048 / 1.04), rel=1e-3)  # 0.01292
    expected_eps = (0.09 ** 0.75) * (k ** 1.5) / L
    assert eps == pytest.approx(expected_eps, rel=1e-3)
    # Sanity: rectangular ε is >1.2× circular ε
    D_h_circ = 2.0 * math.sqrt(0.048 / math.pi)
    L_circ = 0.07 * D_h_circ
    eps_circ = (0.09 ** 0.75) * (k ** 1.5) / L_circ
    assert eps > eps_circ * 1.2


# --- Test 7: _write_turbulence_field emits native BC types -----------------

def test_write_turbulence_field_renders_native_bc_types(tmp_path):
    """Flow inlets should render as turbulentIntensityKineticEnergyInlet (k) /
    turbulentMixingLengthDissipationRateInlet (ε). Wall patches remain
    kqRWallFunction / epsilonWallFunction."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )

    patches = [("fan_p0", "patch"), ("wall_1", "wall")]

    k_path = tmp_path / "k"
    fg._write_turbulence_field(
        k_path, patches, internal_value=0.1,
        field_class="volScalarField", dimensions="[0 2 -2 0 0 0 0]",
        field_name="k",
    )
    k_text = k_path.read_text()
    assert "turbulentIntensityKineticEnergyInlet" in k_text
    # Assert the fan patch block carries the intensity value
    assert "intensity       0.1" in k_text
    # Wall patch still uses the wall function
    assert "kqRWallFunction" in k_text

    eps_path = tmp_path / "epsilon"
    fg._write_turbulence_field(
        eps_path, patches, internal_value=0.01,
        field_class="volScalarField", dimensions="[0 2 -3 0 0 0 0]",
        field_name="epsilon",
    )
    eps_text = eps_path.read_text()
    assert "turbulentMixingLengthDissipationRateInlet" in eps_text
    assert "mixingLength" in eps_text
    assert "epsilonWallFunction" in eps_text


# --- Task 1: Physical-range clamps (spec T4) ---

def test_k_clamp_ceiling_at_5(tmp_path):
    """k values above K_MAX_PHYSICAL (5.0) are clamped down.

    Uses physical_flow_area to produce U_phys > 18 m/s → k > 5 before clamping.
    """
    # physical_flow_area=0.001 → U_phys = 0.15/0.001 = 150 → k_raw = 1.5·(0.1·150)² = 337.5
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=0.001,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, _I, _L, *_ = result
    assert k <= 5.0, f"k={k} should be clamped to K_MAX_PHYSICAL=5.0"


def test_k_floor_at_0_1(tmp_path):
    """k values below K_MIN_PHYSICAL (0.1) are raised to floor."""
    fg = _make_field_gen(
        tmp_path, "acu_slow", bc_type="acu_supply",
        flow_rate=0.001, area=2.0, perimeter=6.0,
    )
    result = fg._compute_turbulence_bc("acu_slow")
    assert result is not None
    k, eps, _I, _L, *_ = result
    assert k >= 0.1, f"k={k} should be raised to K_MIN_PHYSICAL=0.1"


def test_epsilon_clamp_ceiling_at_100(tmp_path):
    """epsilon values above EPS_MAX_PHYSICAL (100) are clamped down.

    Uses physical_flow_area=0.001 → U_phys=150 m/s → k_raw=337.5 → eps >> 100.
    """
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=0.001,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    _k, eps, _I, _L, *_ = result
    assert eps <= 100.0, f"eps={eps} should be clamped to EPS_MAX_PHYSICAL=100.0"


def test_epsilon_floor_at_0_01(tmp_path):
    """epsilon values below EPS_MIN_PHYSICAL (0.01) are raised to floor."""
    fg = _make_field_gen(
        tmp_path, "acu_slow", bc_type="acu_supply",
        flow_rate=0.001, area=2.0, perimeter=6.0,
    )
    result = fg._compute_turbulence_bc("acu_slow")
    assert result is not None
    _k, eps, _I, _L, *_ = result
    assert eps >= 0.01, f"eps={eps} should be raised to EPS_MIN_PHYSICAL=0.01"


def test_k_in_range_passes_through(tmp_path):
    """k values within [K_MIN, K_MAX] are not modified."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, _eps, _I, _L, *_ = result
    U = 0.1553 / 0.048
    expected_k = 1.5 * (0.10 * U) ** 2
    assert k == pytest.approx(expected_k, rel=1e-3)


def test_clamp_ceiling_logs_warning(tmp_path):
    """Hitting the k ceiling emits a WARNING log.

    Uses physical_flow_area=0.001 → U_phys=150 → k_raw=337.5 > K_MAX_PHYSICAL=5.0,
    so the clamp warning fires.
    """
    messages: list = []
    sink_id = loguru_logger.add(lambda msg: messages.append(str(msg)), level="WARNING")
    try:
        fg = _make_field_gen_with_phys_area(
            tmp_path, "fan_p0", bc_type="fan_outlet",
            flow_rate=0.15, area=0.0015, perimeter=0.16,
            physical_flow_area=0.001,
        )
        fg._compute_turbulence_bc("fan_p0")
    finally:
        loguru_logger.remove(sink_id)
    assert any("clamped" in m.lower() for m in messages), (
        f"Expected 'clamped' warning in: {messages!r}"
    )


# --- Task 2: physical_flow_area field on BCSpec ---

from backend.aircooling.runner.bridge_bc import BCSpec


def test_bcspec_accepts_physical_flow_area():
    """BCSpec should accept an optional physical_flow_area field."""
    spec = BCSpec(bc_type="fan_outlet", flow_rate=0.15, physical_flow_area=0.019)
    assert spec.physical_flow_area == pytest.approx(0.019)


def test_bcspec_physical_flow_area_defaults_none():
    """When not set, physical_flow_area defaults to None."""
    spec = BCSpec(bc_type="fan_outlet", flow_rate=0.15)
    assert spec.physical_flow_area is None


# --- Task 3: Physical velocity derivation + intensity rescaling ---------------


def _make_field_gen_with_phys_area(
    tmp_path: Path,
    patch_name: str,
    bc_type: str,
    flow_rate: float,
    area: float,
    perimeter: float,
    physical_flow_area: float = None,
) -> FieldGenerator:
    """Like _make_field_gen but also sets physical_flow_area on the BCSpec."""
    geom = {patch_name: PatchGeometry(area=area, perimeter=perimeter, bbox_dims=(0.12, 0.40, 0.0))}
    spec_dict = {"bc_type": bc_type, "flow_rate": flow_rate}
    if physical_flow_area is not None:
        spec_dict["physical_flow_area"] = physical_flow_area
    reg = _FakeBridgeRegistry({patch_name: spec_dict})
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={},
        room_config={},
        patch_geometry=geom,
        bridge_registry=reg,
    )
    fg._collect_data()
    return fg


def test_fan_outlet_uses_physical_velocity_for_k(tmp_path):
    """fan_outlet with physical_flow_area: k from U_phys, not U_face."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=0.019,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k = result[0]
    U_phys = 0.15 / 0.019
    expected_k = 1.5 * (0.10 * U_phys) ** 2
    assert k == pytest.approx(expected_k, rel=1e-2), (
        f"k={k} should use U_phys={U_phys:.2f}, not U_face=100"
    )


def test_fan_outlet_without_phys_area_falls_back(tmp_path):
    """fan_outlet WITHOUT physical_flow_area: U_phys = min(U_face, 5.0)."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=None,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k = result[0]
    expected_k = 1.5 * (0.10 * 5.0) ** 2  # U_phys = min(100, 5) = 5
    assert k == pytest.approx(expected_k, rel=1e-2)


def test_acu_supply_unchanged_by_physical_velocity(tmp_path):
    """acu_supply: U_phys = U_face (no physical_flow_area → unchanged)."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "acu_top", bc_type="acu_supply",
        flow_rate=4.0, area=2.4, perimeter=7.2,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    k, _eps, I = result[0], result[1], result[2]
    U_face = 4.0 / 2.4
    expected_k = 1.5 * (I * U_face) ** 2
    assert k == pytest.approx(max(expected_k, 0.1), rel=1e-2)


def test_intensity_rescaled_in_k_field_output(tmp_path):
    """k field file writes rescaled I_eff for fan_outlet, not physical I."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=0.019,
    )
    patches = [("fan_p0", "patch"), ("wall_1", "wall")]
    k_path = tmp_path / "k"
    fg._write_turbulence_field(
        k_path, patches, internal_value=0.1,
        field_class="volScalarField", dimensions="[0 2 -2 0 0 0 0]",
        field_name="k",
    )
    k_text = k_path.read_text()
    import re
    m = re.search(r"intensity\s+([0-9.e+-]+)", k_text)
    assert m is not None, "intensity line not found in k field"
    written_I = float(m.group(1))
    U_phys = 0.15 / 0.019
    U_face = 0.15 / 0.0015
    I_eff = 0.10 * U_phys / U_face
    assert written_I == pytest.approx(I_eff, rel=1e-2), (
        f"Written intensity {written_I} should be I_eff={I_eff:.6f}"
    )


# --- Task 4: ACU intensity bump + ACU-only seed (spec T3) ---

def test_acu_supply_uses_10_percent_intensity(tmp_path):
    """acu_supply bc_type should use I=0.10."""
    fg = _make_field_gen(
        tmp_path, "acu_top", bc_type="acu_supply",
        flow_rate=4.0, area=2.4, perimeter=7.2,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    _k, _eps, I, _L = result[:4]
    assert I == pytest.approx(0.10, rel=1e-9), f"acu_supply I should be 0.10, got {I}"


def _make_multi_patch_field_gen(tmp_path, patches_config):
    """Build a FieldGenerator with multiple patches in its bridge registry."""
    geom = {}
    reg_entries = {}
    for cfg in patches_config:
        name = cfg["name"]
        geom[name] = PatchGeometry(
            area=cfg["area"], perimeter=cfg.get("perimeter", 4.0),
            bbox_dims=cfg.get("bbox_dims", (0.12, 0.40, 0.0)),
        )
        entry = {"bc_type": cfg["bc_type"], "flow_rate": cfg["flow_rate"]}
        if "physical_flow_area" in cfg:
            entry["physical_flow_area"] = cfg["physical_flow_area"]
        reg_entries[name] = entry
    reg = _FakeBridgeRegistry(reg_entries)
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={},
        room_config={},
        patch_geometry=geom,
        bridge_registry=reg,
    )
    fg._collect_data()
    return fg


def test_internal_field_seed_excludes_fan_server(tmp_path):
    """Seed should come from ACU patches only, NOT fan-server."""
    fg = _make_multi_patch_field_gen(tmp_path, [
        {"name": "acu_supply_1", "bc_type": "acu_supply", "flow_rate": 4.0, "area": 2.4, "perimeter": 7.2},
        {"name": "fan_p0", "bc_type": "fan_outlet", "flow_rate": 0.15, "area": 0.0015, "perimeter": 0.16,
         "physical_flow_area": 0.019},
    ])
    patches = [("acu_supply_1", "patch"), ("fan_p0", "patch")]
    k_seed, eps_seed = fg._compute_internal_field_seed(patches)
    assert k_seed == pytest.approx(0.1, rel=0.5), (
        f"k_seed={k_seed} should be ACU-only (≈0.1, not polluted by fan)"
    )


def test_internal_field_seed_no_acu_uses_hotroom_fallback(tmp_path):
    """No ACU patches → hotRoom canonical (k=0.1, eps=0.01)."""
    fg = _make_multi_patch_field_gen(tmp_path, [
        {"name": "fan_p0", "bc_type": "fan_outlet", "flow_rate": 0.15, "area": 0.0015, "perimeter": 0.16,
         "physical_flow_area": 0.019},
    ])
    patches = [("fan_p0", "patch")]
    k_seed, eps_seed = fg._compute_internal_field_seed(patches)
    assert k_seed == pytest.approx(0.1, rel=1e-6)
    assert eps_seed == pytest.approx(0.01, rel=1e-6)


def test_internal_field_seed_respects_floor(tmp_path):
    """Seed never drops below K_MIN_PHYSICAL / EPS_MIN_PHYSICAL."""
    fg = _make_multi_patch_field_gen(tmp_path, [
        {"name": "acu_slow", "bc_type": "acu_supply", "flow_rate": 0.001, "area": 2.0, "perimeter": 6.0},
    ])
    patches = [("acu_slow", "patch")]
    k_seed, eps_seed = fg._compute_internal_field_seed(patches)
    assert k_seed >= 0.1
    assert eps_seed >= 0.01
