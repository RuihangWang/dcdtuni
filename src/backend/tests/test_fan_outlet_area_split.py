"""A fan server's outlet sub-patches share its airflow and power by exported
area, not by count; a sub-patch the STL export clipped away gets nothing
(DAML-Testbed patched: RTXpro6000 p0 missing, p2 0.008 m2 at 1/3 of the flow,
T ran unbounded and the solve diverged)."""
import pytest

from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry
from backend.aircooling.runner.fields import FieldGenerator
from backend.aircooling.runner.stlgen_runner import PatchGeometry


def _gen(tmp_path, areas, tv=None):
    reg = BridgeBCRegistry()
    reg.register("S_inlet_patch", BCSpec(bc_type="fan_inlet", flow_rate=0.5, power=1000.0))
    for n in ("p0", "p1", "p2"):
        reg.register(f"S_outlet_{n}", BCSpec(bc_type="fan_outlet", flow_rate=0.5 / 3,
                                             power=1000.0 / 3, timeVarying=tv or {}))
    geom = {f"S_outlet_{n}": PatchGeometry(area=a, perimeter=1.0, bbox_dims=(0.0, 0.1, 0.1))
            for n, a in areas.items()}
    gen = FieldGenerator(case_dir=tmp_path, bridge_registry=reg, patch_geometry=geom)
    gen._collect_data()
    return gen._imported_bc_data


def test_flow_and_power_follow_area_and_skip_clipped_patch(tmp_path):
    d = _gen(tmp_path, {"p1": 0.047, "p2": 0.008})
    assert d["S_outlet_p0"]["flow_rate"] == 0.0
    assert d["S_outlet_p1"]["flow_rate"] == pytest.approx(0.5 * 0.047 / 0.055)
    assert d["S_outlet_p2"]["flow_rate"] == pytest.approx(0.5 * 0.008 / 0.055)
    total_q = sum(d[f"S_outlet_{n}"]["flow_rate"] for n in ("p0", "p1", "p2"))
    total_p = sum(d[f"S_outlet_{n}"]["power"] for n in ("p0", "p1", "p2"))
    assert total_q == pytest.approx(0.5) and total_p == pytest.approx(1000.0)
    # equal velocity and equal dT on every opening
    u1 = d["S_outlet_p1"]["flow_rate"] / 0.047
    u2 = d["S_outlet_p2"]["flow_rate"] / 0.008
    assert u1 == pytest.approx(u2)
    assert d["S_outlet_p1"]["power"] / d["S_outlet_p1"]["flow_rate"] == pytest.approx(1000.0 / 0.5)


def test_equal_areas_keep_the_even_split(tmp_path):
    d = _gen(tmp_path, {"p0": 0.01, "p1": 0.01, "p2": 0.01})
    assert all(d[f"S_outlet_{n}"]["flow_rate"] == pytest.approx(0.5 / 3) for n in ("p0", "p1", "p2"))


def test_schedules_scale_with_the_share(tmp_path):
    rows = [{"t": 0.0, "value": 0.5 / 3}, {"t": 10.0, "value": 0.2 / 3}]
    d = _gen(tmp_path, {"p1": 0.03, "p2": 0.01}, tv={"airflow": rows})
    got = d["S_outlet_p1"]["timeVarying"]["airflow"]
    assert [r["value"] for r in got] == pytest.approx([0.5 * 0.75, 0.2 * 0.75])


def test_inlet_pairs_with_a_meshed_outlet_not_the_clipped_one(tmp_path):
    # p0 has no exported area and is not in the mesh: the inlet must still
    # find p1 and draw its air instead of falling back to a wall
    reg = BridgeBCRegistry()
    reg.register("S_inlet_patch", BCSpec(bc_type="fan_inlet", flow_rate=0.5, power=1000.0))
    for n in ("p0", "p1", "p2"):
        reg.register(f"S_outlet_{n}", BCSpec(bc_type="fan_outlet", flow_rate=0.5 / 3, power=1000.0 / 3))
    geom = {n: PatchGeometry(area=a, perimeter=1.0, bbox_dims=(0.0, 0.1, 0.1))
            for n, a in (("S_outlet_p1", 0.047), ("S_outlet_p2", 0.008), ("S_inlet_patch", 0.0876))}
    gen = FieldGenerator(case_dir=tmp_path, bridge_registry=reg, patch_geometry=geom)
    gen._collect_data()
    mesh = {"S_inlet_patch", "S_outlet_p1", "S_outlet_p2"}
    assert gen._find_server_outlet_patch("S_inlet_patch", mesh) == "S_outlet_p1"
    patches = [(n, "patch") for n in sorted(mesh)]
    (tmp_path / "0").mkdir()
    gen._write_u_field(tmp_path / "0" / "U", patches)
    u = (tmp_path / "0" / "U").read_text()
    block = u[u.index("    S_inlet_patch\n"):]
    block = block[:block.index("}") + 1]
    assert "flowRateOutletVelocity" in block and "noSlip" not in block
