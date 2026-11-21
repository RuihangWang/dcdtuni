"""A region sealed off by full-block panels or containment gets a pressure
reference of its own: its ACU returns become pressure outlets instead of
fixed flows that would have to balance its fans to round-off (CMI-Guichou)."""
import struct

import pytest
from pathlib import Path

from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry
from backend.aircooling.runner.fields import FieldGenerator
from backend.aircooling.runner.pressure_regions import patch_regions, read_label_list

HEADER = """FoamFile
{{
    version     2.0;
    format      {fmt};
    class       {cls};
    object      {obj};
}}
"""
# One face per patch; cells 0-1 are region 0 (the room), 2-3 region 1 (an aisle).
PATCHES = [("AC_1_Top_patch", 0), ("AC_2_Top_patch", 1), ("AC_3_Top_patch", 2),
           ("AC_4_Top_patch", 3), ("AC_1_Bottom_patch", 0), ("AC_2_Bottom_patch", 0),
           ("AC_3_Bottom_patch", 1), ("AC_4_Bottom_patch", 1), ("S_inlet_patch", 0),
           ("S_outlet_p0", 2)]


def _ascii_list(cls, obj, values):
    body = f"{len(values)}\n(\n" + "\n".join(map(str, values)) + "\n)\n"
    return HEADER.format(fmt="ascii", cls=cls, obj=obj) + body


def _mesh(root: Path, regions=((0, 1), (2, 3)), binary_set=False, patches=PATCHES):
    poly = root / "Mesh" / "processor0" / "constant" / "polyMesh"
    (poly / "sets").mkdir(parents=True)
    (poly / "owner").write_text(_ascii_list("labelList", "owner", [c for _, c in patches]))
    entries = "".join(
        f"    {n}\n    {{\n        type            patch;\n        nFaces          1;\n"
        f"        startFace       {i};\n    }}\n" for i, (n, _) in enumerate(patches))
    (poly / "boundary").write_text(
        HEADER.format(fmt="ascii", cls="polyBoundaryMesh", obj="boundary")
        + f"{len(patches)}\n(\n{entries})\n")
    for rid, cells in enumerate(regions):
        f = poly / "sets" / f"region{rid}"
        if binary_set:
            f.write_bytes(HEADER.format(fmt="binary", cls="cellSet", obj=f.name).encode()
                          + f"{len(cells)}\n(".encode() + struct.pack(f"<{len(cells)}i", *cells) + b")\n")
        else:
            f.write_text(_ascii_list("cellSet", f.name, list(cells)))


def _gen(tmp_path: Path) -> FieldGenerator:
    reg = BridgeBCRegistry()
    for i in range(1, 5):
        reg.register(f"AC_{i}_Bottom_patch", BCSpec(
            bc_type="acu_supply", temperature=20.0, flow_rate=2.0,
            return_patch_name=f"AC_{i}_Top_patch"))
        reg.register(f"AC_{i}_Top_patch", BCSpec(bc_type="acu_return", flow_rate=2.0))
    gen = FieldGenerator(case_dir=tmp_path, bridge_registry=reg)
    gen._imported_bc_data = gen._collect_imported_bc_data()
    return gen


NAMES = {n for n, _ in PATCHES if n.startswith("AC_")}


def test_patch_regions_reads_ascii_and_binary_sets(tmp_path):
    for binary in (False, True):
        root = tmp_path / ("b" if binary else "a")
        _mesh(root, binary_set=binary)
        regions = patch_regions(root / "Mesh")
        assert regions["AC_1_Top_patch"] == {0}
        assert regions["AC_3_Top_patch"] == {1}
        assert regions["S_outlet_p0"] == {1}
        assert read_label_list(root / "Mesh/processor0/constant/polyMesh/sets/region1").tolist() == [2, 3]


def test_sealed_region_returns_become_pressure_outlets(tmp_path):
    _mesh(tmp_path)
    pinned = _gen(tmp_path)._pinned_acu_returns(NAMES)
    # AC_1 stays the room's reference, AC_2 keeps its supply's flow, and the
    # aisle's returns (AC_3, AC_4) are freed: the aisle needs its own reference.
    assert pinned == {"AC_2_Top_patch": 2.0}


def test_one_region_mesh_keeps_single_reference(tmp_path):
    _mesh(tmp_path, regions=((0, 1, 2, 3),))
    pinned = _gen(tmp_path)._pinned_acu_returns(NAMES)
    assert set(pinned) == {"AC_2_Top_patch", "AC_3_Top_patch", "AC_4_Top_patch"}


def test_cell_count_comes_from_the_owner_note(tmp_path):
    # Cell 4 owns no face (it only neighbours), but it is in region 1.
    _mesh(tmp_path, regions=((0, 1), (2, 3, 4)))
    owner = tmp_path / "Mesh/processor0/constant/polyMesh/owner"
    owner.write_text(owner.read_text().replace(
        "object      owner;", 'note        "nPoints:8  nCells:5  nFaces:10  nInternalFaces:0";\n    object      owner;'))
    assert patch_regions(tmp_path / "Mesh")["AC_3_Top_patch"] == {1}


def test_region_sets_from_another_mesh_are_ignored(tmp_path):
    _mesh(tmp_path, regions=((0, 1), (2, 3, 9)))
    assert patch_regions(tmp_path / "Mesh") is None
    assert len(_gen(tmp_path)._pinned_acu_returns(NAMES)) == 3


def test_no_mesh_keeps_single_reference(tmp_path):
    assert len(_gen(tmp_path)._pinned_acu_returns(NAMES)) == 3


def test_unbalanced_airflow_is_reported(tmp_path):
    from loguru import logger
    _mesh(tmp_path)
    gen = _gen(tmp_path)
    # Region 0's server inlet draws far more than its supplies and pinned return leave.
    gen._imported_bc_data["S_inlet_patch"] = {"bc_type": "fan_inlet", "flow_rate": 50.0}
    messages = []
    sink = logger.add(messages.append, level="WARNING")
    try:
        gen._pinned_acu_returns(NAMES)
    finally:
        logger.remove(sink)
    assert any("INTO the room" in str(m) for m in messages)


def test_overlapping_region_sets_are_ignored(tmp_path):
    # A stale set left by an earlier mesh with the same cell count.
    _mesh(tmp_path, regions=((0, 1), (2, 3), (1, 2)))
    assert patch_regions(tmp_path / "Mesh") is None


def test_region_sets_must_cover_every_cell(tmp_path):
    _mesh(tmp_path, regions=((0, 1), (2,)))
    assert patch_regions(tmp_path / "Mesh") is None


# A contained cold aisle (cells 2-3) fed by two in-row supplies: it holds the
# supplies and the server inlet, every return and the server outlet are in the room.
COLD = [("AC_1_Top_patch", 0), ("AC_2_Top_patch", 1), ("AC_3_Top_patch", 0),
        ("AC_4_Top_patch", 1), ("AC_1_Bottom_patch", 0), ("AC_2_Bottom_patch", 1),
        ("AC_3_Bottom_patch", 2), ("AC_4_Bottom_patch", 3), ("S_inlet_patch", 2),
        ("S_outlet_p0", 0)]


def _cold_gen(tmp_path):
    _mesh(tmp_path, patches=COLD)
    gen = _gen(tmp_path)
    gen._imported_bc_data["S_inlet_patch"] = {"bc_type": "fan_inlet", "flow_rate": 3.0}
    gen._imported_bc_data["S_outlet_p0"] = {"bc_type": "fan_outlet", "flow_rate": 3.0}
    return gen


def test_sealed_supply_region_supplies_become_pressure_inlets(tmp_path):
    gen = _cold_gen(tmp_path)
    names = {n for n, _ in COLD}
    assert gen._sealed_region_supplies(names) == {"AC_3_Bottom_patch", "AC_4_Bottom_patch"}
    # The room keeps its single reference; no error for the return-less aisle.
    assert set(gen._pinned_acu_returns(names)) == {
        "AC_2_Top_patch", "AC_3_Top_patch", "AC_4_Top_patch"}

    gen._free_supplies = gen._sealed_region_supplies(names)
    patches = [(n, "patch") for n, _ in COLD]
    gen._write_u_field(tmp_path / "U", patches)
    gen._write_p_rgh_field(tmp_path / "p_rgh", patches)
    u, p = (tmp_path / "U").read_text(), (tmp_path / "p_rgh").read_text()

    def block(text, name):
        i = text.index(f"    {name}\n")
        return text[i:text.index("}", i)]
    for n in ("AC_3_Bottom_patch", "AC_4_Bottom_patch"):
        assert "pressureInletOutletVelocity" in block(u, n)
        assert "totalPressure" in block(p, n)
    # Supplies in the room keep their fixed flow.
    assert "pressureInletOutletVelocity" not in block(u, "AC_1_Bottom_patch")
    assert "fixedFluxPressure" in block(p, "AC_1_Bottom_patch")


def test_no_return_no_supply_region_still_reports_an_imbalance(tmp_path):
    from loguru import logger
    gen = _cold_gen(tmp_path)
    gen._imported_bc_data["S_inlet_patch"] = {"bc_type": "fan_inlet", "flow_rate": 3.0}
    # Make the aisle supply-free: its two supplies move to the room.
    for n in ("AC_3_Bottom_patch", "AC_4_Bottom_patch"):
        gen._imported_bc_data[n] = {"bc_type": "wall"}
    messages = []
    sink = logger.add(messages.append, level="ERROR")
    try:
        gen._pinned_acu_returns({n for n, _ in COLD})
    finally:
        logger.remove(sink)
    assert any("no ACU return" in str(m) for m in messages)


# ---------------------------------------------------------------------------
# ACU airflow balance (owner 2026-11-10): the ACUs of sealed regions follow the
# servers, supply = return, nearest the configured flows.
# ---------------------------------------------------------------------------

ALL = {n for n, _ in PATCHES}


def _hot_gen(tmp_path, server_q):
    # PATCHES: region 1 (cells 2-3) is a sealed hot aisle holding the returns of
    # AC_3/AC_4 and the server outlet; every supply and the server inlet are in the room.
    _mesh(tmp_path)
    gen = _gen(tmp_path)
    gen._imported_bc_data["S_inlet_patch"] = {"bc_type": "fan_inlet", "flow_rate": server_q}
    gen._imported_bc_data["S_outlet_p0"] = {"bc_type": "fan_outlet", "flow_rate": server_q}
    return gen


def test_sealed_hot_aisle_acus_follow_the_servers(tmp_path):
    gen = _hot_gen(tmp_path, 6.0)
    receipt = gen._balance_sealed_acu_airflow(ALL)
    data = gen._imported_bc_data
    # The aisle's two units carry the 6 m3/s its servers blow, 3 each; the room's keep 2.
    assert data["AC_3_Bottom_patch"]["flow_rate"] == pytest.approx(3.0)
    assert data["AC_4_Bottom_patch"]["flow_rate"] == pytest.approx(3.0)
    assert data["AC_1_Bottom_patch"]["flow_rate"] == pytest.approx(2.0)
    assert {u["supply"] for u in receipt["units"]} == {"AC_3_Bottom_patch", "AC_4_Bottom_patch"}
    # The pinned room return takes its supply's (unchanged) flow.
    assert gen._pinned_acu_returns(ALL) == {"AC_2_Top_patch": 2.0}


def test_balanced_flows_are_left_alone(tmp_path):
    gen = _hot_gen(tmp_path, 4.0)
    assert gen._balance_sealed_acu_airflow(ALL) is None
    assert gen._imported_bc_data["AC_3_Bottom_patch"]["flow_rate"] == 2.0


def test_one_region_mesh_is_not_balanced(tmp_path):
    _mesh(tmp_path, regions=((0, 1, 2, 3),))
    gen = _gen(tmp_path)
    gen._imported_bc_data["S_outlet_p0"] = {"bc_type": "fan_outlet", "flow_rate": 9.0}
    assert gen._balance_sealed_acu_airflow(ALL) is None


def test_sealed_cold_aisle_supplies_deliver_the_server_draw(tmp_path):
    gen = _cold_gen(tmp_path)
    names = {n for n, _ in COLD}
    gen._balance_sealed_acu_airflow(names)
    data = gen._imported_bc_data
    assert data["AC_3_Bottom_patch"]["flow_rate"] == pytest.approx(1.5)
    assert data["AC_4_Bottom_patch"]["flow_rate"] == pytest.approx(1.5)
    # Their returns, in the room, are pinned to the same 1.5.
    pinned = gen._pinned_acu_returns(names)
    assert pinned["AC_3_Top_patch"] == pytest.approx(1.5)
    assert pinned["AC_4_Top_patch"] == pytest.approx(1.5)


def test_a_region_no_acu_can_balance_keeps_the_configured_flows(tmp_path):
    gen = _hot_gen(tmp_path, 6.0)
    # The aisle loses its returns: nothing can carry its servers' air.
    for i in (3, 4):
        gen._imported_bc_data[f"AC_{i}_Bottom_patch"]["return_patch_name"] = None
    assert gen._balance_sealed_acu_airflow(ALL) is None
    assert gen._imported_bc_data["AC_3_Bottom_patch"]["flow_rate"] == 2.0


def test_generate_fields_writes_the_balance_receipt_and_the_audit_reads_it(tmp_path):
    from backend.aircooling.runner.bc_audit import audit
    gen = _hot_gen(tmp_path, 6.0)
    gen._acu_balance_done = True
    gen._acu_balance = gen._balance_sealed_acu_airflow(ALL)
    run = tmp_path / "run"
    (run / "0").mkdir(parents=True)
    patches = [(n, "patch") for n, _ in PATCHES]
    gen._free_supplies = set()
    gen._pinned_returns = gen._pinned_acu_returns(ALL)
    gen._write_u_field(run / "0" / "U", patches)
    manifest = {"boundaryBindings": {
        "a": {"patchName": "AC_3_Bottom_patch", "bcType": "acu_supply",
              "parameters": {"flowRateM3s": 2.0}}}}
    issues = [i for i in audit(run, manifest)["issues"] if i["kind"] == "value"]
    assert issues and "3" in issues[0]["detail"]          # no receipt: 3.0 vs bound 2.0
    (run / "acu-airflow-balance.json").write_text(__import__("json").dumps(gen._acu_balance))
    assert not [i for i in audit(run, manifest)["issues"] if i["kind"] == "value"]
