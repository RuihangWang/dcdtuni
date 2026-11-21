"""T on a server fan inlet must be zeroGradient, not inletOutlet.

A fan inlet's U is flowRateOutletVelocity, which removes every reverse
component (`nUp = max(nUp, 0)`), so the patch never carries real backflow.
Its faces with no outward flow carry phi = +-0 in round-off. inletOutlet
sets valueFraction = 1 - pos0(phi), so a -0 face is treated as backflow and
pinned to the stale initial inletValue. On cut cells those faces then
conduct heat into the room: on Tri_angle about 118 W of a 6.25 kW load,
the whole 2.3 % air-side heat gap
(docs/benchmarks/tri-angle-heat-gap-2026-10-25/).
"""
import re

from backend.aircooling.runner.fields import FieldGenerator


def _t_block(text, patch):
    m = re.search(r"\n\s*" + re.escape(patch) + r"\s*\n\s*\{(.*?)\}", text, re.S)
    assert m, f"{patch} missing from T"
    return m.group(1)


def test_fan_inlet_temperature_is_zero_gradient(tmp_path):
    gen = FieldGenerator(case_dir=tmp_path, simulation_config={"initTemperature": 295.15},
                         room_config={}, patch_geometry={}, bridge_registry=None)
    gen._imported_bc_data = {
        "B01_inlet_patch": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "ACU_1_Top_patch": {"bc_type": "acu_return", "flow_rate": 1.0},
    }
    t_path = tmp_path / "T"
    gen._write_t_field(t_path, [("B01_inlet_patch", "patch"), ("ACU_1_Top_patch", "patch")], strict=True)
    text = t_path.read_text()
    inlet = _t_block(text, "B01_inlet_patch")
    assert "zeroGradient" in inlet and "inletOutlet" not in inlet and "inletValue" not in inlet
    # The other sink, the ACU return, already had this BC.
    assert "zeroGradient" in _t_block(text, "ACU_1_Top_patch")
