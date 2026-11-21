"""Debug test: isolate whether expression BCs cause T divergence.

GTC case diverges at Time=1 with 316 uniformFixedValue expression BCs.
Hypothesis: the expression evaluation (weightAverage across patches)
may produce NaN or extreme values on under-resolved outlet patches.

Diagnostic: add a static_outlet_temperature mode that replaces
the live expression BC with a pre-computed fixedValue. If the solver
converges with fixedValue but not expression, the expression BC is
the root cause.
"""
import re
from pathlib import Path

import pytest

from backend.aircooling.runner.fields import FieldGenerator


def _make_gen_with_fan_pair(tmp_path, *, flow_rate=0.1, power=300.0):
    """FieldGenerator with one fan-server (inlet + outlet)."""
    gen = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={"roomTemperature": 22.0},
        room_config={},
        patch_geometry={},
        bridge_registry=None,
    )
    gen._imported_bc_data = {
        "srv1_outlet_p0": {
            "bc_type": "fan_outlet",
            "power": power,
            "flow_rate": flow_rate,
        },
        "srv1_inlet_patch": {
            "bc_type": "fan_inlet",
            "flow_rate": flow_rate,
        },
    }
    gen._find_server_inlet_patch = lambda name: "srv1_inlet_patch"
    return gen


# ── RED test 1: static_outlet_temperature mode should produce fixedValue ──

def test_static_outlet_temp_uses_fixed_value(tmp_path):
    """When static_outlet_temperature=True, fan_outlet T BC should be
    fixedValue (T_ambient + ΔT) instead of uniformFixedValue expression.

    This isolates whether the expression BC causes solver divergence.
    """
    gen = _make_gen_with_fan_pair(tmp_path, flow_rate=0.1, power=300.0)
    t_path = tmp_path / "T"
    patches = [
        ("srv1_outlet_p0", "patch"),
        ("srv1_inlet_patch", "patch"),
    ]

    # ΔT = P / (ρ · Cp · Q) = 300 / (1.19 × 1005 × 0.1) ≈ 2.509 K
    gen._write_t_field(t_path, patches, strict=True,
                       static_outlet_temperature=True)

    content = t_path.read_text()

    # Must NOT contain uniformFixedValue or expression
    assert "uniformFixedValue" not in content, \
        "static mode should not use uniformFixedValue expression"
    assert "weightAverage" not in content, \
        "static mode should not reference weightAverage"

    # Must contain fixedValue with the pre-computed T
    assert "fixedValue" in content
    # Check the value is T_ambient + ΔT ≈ 297.635
    m = re.search(r"srv1_outlet_p0.*?value\s+uniform\s+([\d.]+)", content, re.DOTALL)
    assert m is not None, "srv1_outlet_p0 should have a value field"
    t_val = float(m.group(1))
    expected = 295.15 + 300.0 / (1.19 * 1005.0 * 0.1)
    assert abs(t_val - expected) < 0.01, \
        f"Expected T≈{expected:.2f}, got {t_val:.2f}"


# ── RED test 2: default mode still uses expression BC (no regression) ──

def test_default_mode_uses_expression_bc(tmp_path):
    """Default behavior must remain: uniformFixedValue with expression."""
    gen = _make_gen_with_fan_pair(tmp_path)
    t_path = tmp_path / "T"
    patches = [
        ("srv1_outlet_p0", "patch"),
        ("srv1_inlet_patch", "patch"),
    ]
    gen._write_t_field(t_path, patches, strict=True)

    content = t_path.read_text()
    assert "uniformFixedValue" in content
    assert "weightAverage" in content
