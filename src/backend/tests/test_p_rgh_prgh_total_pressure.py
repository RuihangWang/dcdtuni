"""ACU/PCU pressure boundary conditions for p_rgh.

Supply keeps a specified velocity and therefore uses fixedFluxPressure.
Return is the room pressure reference and uses fixedValue p_rgh = 0.
"""
from pathlib import Path
import pytest

from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry
from backend.aircooling.runner.fields import FieldGenerator


@pytest.fixture
def generator(tmp_path: Path) -> FieldGenerator:
    reg = BridgeBCRegistry()
    reg.register("acu1_supply_patch_1",
                 BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0))
    reg.register("acu1_return_patch_1",
                 BCSpec(bc_type="acu_return", flow_rate=4.0))
    return FieldGenerator(case_dir=tmp_path, bridge_registry=reg)


def _write_field(generator: FieldGenerator, patches):
    path = generator.case_dir / "0" / "p_rgh"
    path.parent.mkdir(parents=True, exist_ok=True)
    generator._imported_bc_data = generator._collect_imported_bc_data()
    generator._write_p_rgh_field(path, patches)
    return path.read_text(encoding="utf-8")


def _patch_block(text: str, patch_name: str) -> str:
    start = text.find(patch_name)
    assert start != -1, f"patch {patch_name} not emitted"
    brace_open = text.find("{", start)
    brace_close = text.find("}", brace_open)
    return text[start:brace_close + 1]


def test_acu_return_uses_fixed_value_zero_pressure(generator):
    text = _write_field(generator, [("acu1_return_patch_1", "patch")])
    block = _patch_block(text, "acu1_return_patch_1")
    assert "fixedValue" in block, (
        f"acu_return should use fixedValue p_rgh=0, got:\n{block}"
    )
    assert "value           uniform 0;" in block
    assert "fixedFluxPressure" not in block
    assert "prghTotalPressure" not in block


def test_pcu_return_uses_same_fixed_value_zero_pressure(generator):
    generator.bridge_registry.register(
        "PCU_2_return", BCSpec(bc_type="acu_return", flow_rate=4.0)
    )
    text = _write_field(generator, [("PCU_2_return", "patch")])
    block = _patch_block(text, "PCU_2_return")
    assert "fixedValue" in block
    assert "value           uniform 0;" in block
    assert "fixedFluxPressure" not in block
    assert "prghTotalPressure" not in block


def test_acu_supply_uses_fixed_flux_pressure(generator):
    """ACU supply has specified velocity (flowRateInletVelocity), so p_rgh
    must be fixedFluxPressure — pressure adjusts to maintain the flow."""
    text = _write_field(generator, [("acu1_supply_patch_1", "patch")])
    block = _patch_block(text, "acu1_supply_patch_1")
    assert "fixedFluxPressure" in block, (
        f"acu_supply should use fixedFluxPressure (velocity is specified), "
        f"got:\n{block}"
    )
    assert "prghTotalPressure" not in block


def test_fan_inlet_uses_fixed_flux_pressure(generator):
    """fan_inlet has specified flow rate — fixedFluxPressure."""
    generator.bridge_registry.register(
        "fan1_inlet", BCSpec(bc_type="fan_inlet", flow_rate=0.5))
    text = _write_field(generator, [("fan1_inlet", "patch")])
    fan_block = _patch_block(text, "fan1_inlet")
    assert "fixedFluxPressure" in fan_block, (
        f"fan_inlet should use fixedFluxPressure, got:\n{fan_block}"
    )
    assert "prghTotalPressure" not in fan_block


def test_fan_outlet_uses_fixed_flux_pressure(generator):
    """fan_outlet has specified velocity (flowRateInletVelocity) — needs
    fixedFluxPressure, same as acu_supply."""
    generator.bridge_registry.register(
        "fan1_outlet", BCSpec(bc_type="fan_outlet", flow_rate=0.5, power=500))
    text = _write_field(generator, [("fan1_outlet", "patch")])
    fan_block = _patch_block(text, "fan1_outlet")
    assert "fixedFluxPressure" in fan_block, (
        f"fan_outlet should use fixedFluxPressure, got:\n{fan_block}"
    )
    assert "prghTotalPressure" not in fan_block


def test_wall_uses_fixed_flux_pressure(generator):
    text = _write_field(generator, [("room_wall_1", "wall")])
    wall_block = _patch_block(text, "room_wall_1")
    assert "fixedFluxPressure" in wall_block
