"""Gate-placement contract for the fan_outlet T BC raise.

The user's previous instruction was clear: no silent fallback for the
fan_outlet temperature-rise BC (ΔT = P / ρ·Cp·Q); a missing paired inlet
patch or zero mass flow must be raised so the solver never runs with
silently-wrong physics.

The user's 2026-04-18 follow-up: that strict raise belongs on the
SOLVE path only. The mesh pipeline must not be blocked by BC issues
(createBaffles just needs files to exist; the final solve-prep step
validates). So `generate_fields(..., strict=False)` emits a placeholder
BC + warning log while `strict=True` (default) preserves the raise.

These tests pin both branches.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.aircooling.runner.fields import FieldGenerator


def _make_gen_with_one_fan_outlet(tmp_path, *, with_paired_inlet: bool, flow_rate: float = 0.1):
    gen = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={"initTemperature": 293.15},
        room_config={},
        patch_geometry={},
        bridge_registry=None,
    )
    # Inject a minimal "bridge" data dict that mimics what _collect_data
    # normally populates from a real bridge_registry. The test exercises
    # _write_t_field directly, so we stub _collect_data out and set state.
    gen._imported_bc_data = {
        "B01_outlet_p0": {
            "bc_type": "fan_outlet",
            "power": 300.0,
            "flow_rate": flow_rate,
        },
    }
    if with_paired_inlet:
        gen._imported_bc_data["B01_inlet_patch"] = {
            "bc_type": "fan_inlet",
            "flow_rate": flow_rate,
        }
    # _find_server_inlet_patch normally walks the registry; stub to our inlet.
    gen._find_server_inlet_patch = lambda name: "B01_inlet_patch"
    return gen


def test_strict_true_raises_when_paired_inlet_missing(tmp_path):
    gen = _make_gen_with_one_fan_outlet(tmp_path, with_paired_inlet=False)
    t_path = tmp_path / "T"
    patches = [("B01_outlet_p0", "patch")]  # note: inlet patch NOT in mesh
    with pytest.raises(RuntimeError, match=r"paired inlet patch .*B01_inlet_patch.* missing"):
        gen._write_t_field(t_path, patches, strict=True)


def test_strict_false_emits_placeholder_when_paired_inlet_missing(tmp_path):
    gen = _make_gen_with_one_fan_outlet(tmp_path, with_paired_inlet=False)
    t_path = tmp_path / "T"
    patches = [("B01_outlet_p0", "patch")]
    # Must NOT raise; must write a file with placeholder fixedValue BC.
    gen._write_t_field(t_path, patches, strict=False)
    assert t_path.exists()
    text = t_path.read_text()
    assert "B01_outlet_p0" in text
    assert "fixedValue" in text
    # Must NOT emit the ΔT temperature-rise expression block for this patch.
    assert "weightAverage(T)" not in text
    assert "expression" not in text


def test_strict_true_raises_when_mass_flow_zero(tmp_path):
    gen = _make_gen_with_one_fan_outlet(tmp_path, with_paired_inlet=True, flow_rate=0.0)
    t_path = tmp_path / "T"
    patches = [
        ("B01_outlet_p0", "patch"),
        ("B01_inlet_patch", "patch"),
    ]
    with pytest.raises(RuntimeError, match=r"mass flow is zero"):
        gen._write_t_field(t_path, patches, strict=True)


def test_strict_false_emits_placeholder_when_mass_flow_zero(tmp_path):
    gen = _make_gen_with_one_fan_outlet(tmp_path, with_paired_inlet=True, flow_rate=0.0)
    t_path = tmp_path / "T"
    patches = [
        ("B01_outlet_p0", "patch"),
        ("B01_inlet_patch", "patch"),
    ]
    # Must NOT raise; placeholder fixedValue replaces the real ΔT block.
    gen._write_t_field(t_path, patches, strict=False)
    assert t_path.exists()
    text = t_path.read_text()
    assert "B01_outlet_p0" in text
    assert "fixedValue" in text
    assert "expression" not in text, \
        "placeholder must not emit the temperature-rise expression block"


# --- Plumbing contract tests -------------------------------------------------
# Verify the strict flag threads through the runner's call sites so the gate
# placement matches the intended contract (mesh-path tolerant, solve-path strict).

import re
import textwrap


def _method_body(name: str) -> str:
    src = (Path(__file__).resolve().parent.parent / "aircooling" / "runner" / "stlgen_runner.py").read_text()
    m = re.search(rf"^(    )def {re.escape(name)}\(", src, re.MULTILINE)
    assert m, f"{name} not found"
    start = m.start()
    next_m = re.search(r"^(    )def |^class ", src[m.end():], re.MULTILINE)
    end = m.end() + next_m.start() if next_m else len(src)
    return textwrap.dedent(src[start:end])


def test_run_mesh_only_calls_setup_with_strict_false():
    """run_mesh_only explicitly passes strict=False so a BC issue doesn't
    block mesh generation. Solve-only / full-simulation default to strict=True."""
    body = _method_body("run_mesh_only")
    # each step runs through _mesh_step(name, fn, **kwargs)
    assert ("_setup_complete_case_in_mesh(strict=False)" in body
            or "self._setup_complete_case_in_mesh, strict=False)" in body), \
        "run_mesh_only must pass strict=False to _setup_complete_case_in_mesh"


def test_run_solve_only_uses_strict_default():
    """run_solve_only keeps the strict default (True)."""
    body = _method_body("run_solve_only")
    assert "_setup_complete_case_in_mesh(strict=False)" not in body
    assert "_setup_complete_case_in_mesh(" in body, \
        "run_solve_only must still invoke case setup (just with strict default)"


def test_run_simulation_uses_strict_default():
    body = _method_body("run_simulation")
    assert "strict=False" not in body
    assert ("_setup_complete_case_in_mesh(" in body
            or "self._setup_complete_case_in_mesh)" in body)


def test_regenerate_fields_for_baffles_uses_strict_false():
    """Intermediate fields for createBaffles are always non-strict: the
    final solve-prep step re-validates with strict=True if the user solves."""
    body = _method_body("_regenerate_fields_for_baffles")
    assert "strict=False" in body, \
        "_regenerate_fields_for_baffles must pass strict=False"
