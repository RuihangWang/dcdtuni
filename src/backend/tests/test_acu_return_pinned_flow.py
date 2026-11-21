"""An ACU return draws its own supply's airflow; one return stays the p_rgh
reference (Base2-9's eight pressure-outlet returns traded 8.85-10.36 m3/s
against 9.5 m3/s supplies and never let the return temperatures settle)."""
from pathlib import Path

import pytest

from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry
from backend.aircooling.runner.fields import FieldGenerator


def _gen(tmp_path: Path, n: int, **supply) -> FieldGenerator:
    reg = BridgeBCRegistry()
    for i in range(1, n + 1):
        reg.register(f"AC_{i}_Bottom_patch", BCSpec(
            bc_type="acu_supply", temperature=20.0, flow_rate=9.5,
            return_patch_name=f"AC_{i}_Top_patch", **supply))
        reg.register(f"AC_{i}_Top_patch", BCSpec(bc_type="acu_return", flow_rate=9.5))
    gen = FieldGenerator(case_dir=tmp_path, bridge_registry=reg)
    gen._imported_bc_data = gen._collect_imported_bc_data()
    return gen


def _names(n):
    return {f"AC_{i}_{s}_patch" for i in range(1, n + 1) for s in ("Top", "Bottom")}


def _block(text, name):
    start = text.index(f"    {name}\n")
    return text[start:text.index("}", start) + 1]


def test_all_but_one_return_pinned_to_their_supply(tmp_path):
    gen = _gen(tmp_path, 3)
    pinned = gen._pinned_acu_returns(_names(3))
    assert pinned == {"AC_2_Top_patch": 9.5, "AC_3_Top_patch": 9.5}


def test_fields_write_flow_outlet_and_flux_pressure(tmp_path):
    gen = _gen(tmp_path, 2)
    gen._pinned_returns = gen._pinned_acu_returns(_names(2))
    patches = [(n, "patch") for n in sorted(_names(2))]
    (tmp_path / "0").mkdir()
    gen._write_u_field(tmp_path / "0" / "U", patches)
    gen._write_p_rgh_field(tmp_path / "0" / "p_rgh", patches)
    u = (tmp_path / "0" / "U").read_text()
    p = (tmp_path / "0" / "p_rgh").read_text()
    assert "flowRateOutletVelocity" in _block(u, "AC_2_Top_patch")
    assert "volumetricFlowRate 9.5" in _block(u, "AC_2_Top_patch")
    assert "fixedFluxPressure" in _block(p, "AC_2_Top_patch")
    # the reference return is unchanged
    assert "pressureInletOutletVelocity" in _block(u, "AC_1_Top_patch")
    assert "fixedValue" in _block(p, "AC_1_Top_patch")


def test_single_return_and_opt_out_keep_pressure_outlets(tmp_path, monkeypatch):
    assert _gen(tmp_path, 1)._pinned_acu_returns(_names(1)) == {}
    monkeypatch.setenv("DCDTUNI_ACU_RETURN_PRESSURE", "1")
    assert _gen(tmp_path, 3)._pinned_acu_returns(_names(3)) == {}


def test_louvre_vector_supply_keeps_its_return_free(tmp_path):
    gen = _gen(tmp_path, 2, direction=[1.0, 0.0, 0.0], patch_area=0.5)
    assert gen._pinned_acu_returns(_names(2)) == {}


def test_unmeshed_supply_keeps_its_return_free(tmp_path):
    gen = _gen(tmp_path, 3)
    names = _names(3) - {"AC_3_Bottom_patch"}
    # AC_3 stays a reference, so both of the others can be pinned
    assert gen._pinned_acu_returns(names) == {"AC_1_Top_patch": 9.5, "AC_2_Top_patch": 9.5}
