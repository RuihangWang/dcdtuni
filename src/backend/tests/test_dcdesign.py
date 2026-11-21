"""agent.dcdesign: family templates and the design compiler (structural checks).

Export, mesh and solve checks of the compiled projects run through the scripted
API (gate G-D0/G-D1 of the ISPO plan); these tests check what can be checked on
the JSON alone.
"""
import copy
import dataclasses
import itertools
import json

import pytest

from agent.dcdesign import compiler as C
from agent.dcdesign import families as F
from agent.dcdesign import spec as S

BASE = json.loads(F.BASE_PROJECT.read_text())
CASES = [(f, (16 if S.FAMILIES[f]["units"] == "perimeter" else None)) for f in S.FAMILIES]


def _boxes(p):
    c = p["constructions"]
    return {o["meta"]["name"]: F.aabb(o["geometry"]) for kind in ("racks", "acus", "cdus", "boxes") for o in c[kind].values()}


def _overlap(a, b, tol=1e-6):
    return all(min(a[i + 1], b[i + 1]) - max(a[i], b[i]) > tol for i in (0, 2, 4))


@pytest.mark.parametrize("family,tiles", CASES)
def test_family_template_structure(family, tiles):
    p = F.build_family(family, tiles)
    c = p["constructions"]
    hall = F.hall_layout(BASE)
    kind, cont = S.FAMILIES[family]["units"], S.FAMILIES[family]["containment"]
    assert sorted(a["meta"]["name"] for a in c["acus"].values()) == list(S.unit_ids(family))
    assert len(c["baffles"]) == {"none": 0, "cold": 3, "hot": 6}[cont]
    assert all(b["meta"]["enabled"] for b in c["baffles"].values())
    # racks, servers, CDU, liquid network and heat budget are the base ones
    for key in ("racks", "cdus", "pipeSegments", "pipeJunctions", "liquidFlowNetworks", "walls"):
        assert c[key] == BASE["constructions"][key], key
    assert p["runDefaults"]["hybridHeatBudget"] == BASE["runDefaults"]["hybridHeatBudget"]
    # one mesh setting for every family: unit surfaces at the level of the cells around them
    mc, base_mc = p["runDefaults"]["meshConfig"], BASE["runDefaults"]["meshConfig"]
    assert mc["acuRefinementLevel"] == [F.UNIT_SURFACE_LEVEL] * 2
    assert {k: v for k, v in mc.items() if k != "acuRefinementLevel"} == \
        {k: v for k, v in base_mc.items() if k != "acuRefinementLevel"}
    # no two pieces of equipment intersect, everything inside the room
    boxes = _boxes(p)
    for (na, a), (nb, b) in itertools.combinations(boxes.items(), 2):
        assert not _overlap(a, b), (na, nb)
    for b in boxes.values():
        assert hall.room_x[0] - 1e-9 <= b[0] and b[1] <= hall.room_x[1] + 1e-9
        assert hall.room_z[0] - 1e-9 <= b[4] and b[5] <= hall.room_z[1] + 1e-9
    # one footprint cutout per object on the raised floor, none stale
    floor = F._floor(p, "Raised_Floor")
    assert sorted(cu["sourceName"] for cu in floor["geometry"]["cutouts"]) == sorted(boxes)
    # tracks: two per running unit, none dangling
    uids = {o["uid"] for kind in c.values() if isinstance(kind, dict) for o in kind.values() if isinstance(o, dict) and "uid" in o}
    tracks = p["transient"]["tracks"]
    assert all(t["modelUid"] in uids for t in tracks)
    for a in c["acus"].values():
        mine = sorted(t["param"] for t in tracks if t["modelUid"] == a["uid"])
        assert mine == ["acu.airflow", "acu.supplyTemp"]
    if kind == "perimeter":
        top = F._floor(p, "Raised_Floor")["geometry"]["faces"]["top"]["patches"]
        assert len(top) == tiles and len(floor["geometry"]["openings"]) == tiles
        za, zb = hall.cold_aisle_z
        fz = floor["geometry"]["location"]["z"]
        assert all(za - 1e-9 <= t["y"] + fz and t["y"] + fz + t["h"] <= zb + 1e-9 for t in top)
        for a in c["acus"].values():
            b = F.aabb(a["geometry"])
            assert b[3] < hall.rack_top_y                       # below the containment roof
            gap = min(abs(b[4] - hall.rows[0].zmin), abs(b[5] - hall.rows[0].zmin),
                      abs(b[4] - hall.rows[1].zmax), abs(b[5] - hall.rows[1].zmax))
            assert gap >= 1.0                                   # service clearance to the rack rears
            assert a["geometry"]["supplyFaces"][0]["faceId"] == "bottom"
    if family == "PERIM_HAC":
        zones = [hall.rear_zone_z(r) for r in hall.rows]
        for a in c["acus"].values():
            b = F.aabb(a["geometry"])
            assert any(z0 - 1e-9 <= b[4] and b[5] <= z1 + 1e-9 for z0, z1 in zones)
            assert hall.row_x[0] <= b[0] and b[1] <= hall.row_x[1]


def test_base_layout_matches_the_known_hall():
    hall = F.hall_layout(BASE)
    assert hall.row_x == pytest.approx((1.4907375, 7.4907375))
    assert hall.cold_aisle_z == pytest.approx((2.8169, 4.0199), abs=1e-6)
    assert hall.rack_top_y == pytest.approx(2.847)
    # the CAC panels coincide with the base project's (disabled) containment
    cac = {b["meta"]["name"]: b["geometry"] for b in F.build_family("INROW_CAC")["constructions"]["baffles"].values()}
    base = {b["meta"]["name"]: b["geometry"] for b in BASE["constructions"]["baffles"].values()}
    for mine, theirs in (("cac_end_low_x", "con_f"), ("cac_end_high_x", "con_b"), ("cac_roof", "con_b_copy")):
        for k in "xyz":
            assert cac[mine]["location"][k] == pytest.approx(base[theirs]["location"][k], abs=1e-6)
            assert cac[mine]["size"][k] == pytest.approx(base[theirs]["size"][k], abs=1e-6)
        assert cac[mine]["quaternion"] == base[theirs]["quaternion"]


def test_compile_writes_controls_into_tracks():
    task = S.Task(it_scale=1.2)
    d = S.Design(family="PERIM_CAC", tile_count=12, acu_supply_c=(18.0, 19.0, 20.0, 21.0),
                 acu_flow_fraction=(0.5, 0.6, 0.7, 0.8), cdu_primary_inlet_c=24.0,
                 cdu_primary_flow_kg_s=1.5, cdu_pump_ratio=0.8)
    p = C.compile_design(task, d)
    tracks = p["transient"]["tracks"]
    uid = p["meta"]["dcdesign"]["unit_uids"]
    val = {(t["modelUid"], t["param"]): t["keyframes"][0]["value"] for t in tracks}
    for i, n in enumerate(S.unit_ids("PERIM_CAC")):
        assert val[(uid[n], "acu.supplyTemp")] == d.acu_supply_c[i]
        assert val[(uid[n], "acu.airflow")] == pytest.approx(d.acu_flow_fraction[i] * S.CATALOG["acu_rated_flow_m3s"])
    cdu = next(iter(p["constructions"]["cdus"]))
    assert (val[(cdu, "cdu.primaryInletT")], val[(cdu, "cdu.primaryFlow")], val[(cdu, "pump.rpm")]) == (24.0, 1.5, 0.8)
    base_power = sum(t["keyframes"][0]["value"] for t in BASE["transient"]["tracks"] if t["param"] == "server.power")
    power = sum(t["keyframes"][0]["value"] for t in tracks if t["param"] == "server.power")
    budget = sum(r["total_it_power_W"] for r in p["runDefaults"]["hybridHeatBudget"]["servers"].values())
    assert power == pytest.approx(1.2 * base_power) and budget == pytest.approx(1.2 * base_power)
    assert p["meta"]["dcdesign"]["design"]["tile_count"] == 12


def test_closed_unit_has_no_ports_and_no_tracks():
    d = C.reference_design("INROW_OPEN")
    d = S.Design(**{**d.to_json(), "closed_units": (2,)})
    p = C.compile_design(S.Task(), d)
    n = S.unit_ids("INROW_OPEN")[2]
    a = next(a for a in p["constructions"]["acus"].values() if a["meta"]["name"] == n)
    assert a["geometry"]["supplyFaces"] == [] and a["geometry"]["returnFaces"] == []
    assert not any(t["modelUid"] == a["uid"] for t in p["transient"]["tracks"])
    assert p["meta"]["dcdesign"]["closed_units"] == [n]


@pytest.mark.parametrize("family,tiles", CASES)
def test_reference_design_compiles(family, tiles):
    d = C.reference_design(family, tiles)
    assert S.validate(S.Task(), d) == []
    p = C.compile_design(S.Task(), d)
    assert p["meta"]["dcdesign"]["family"] == family


def test_illegal_designs_are_rejected():
    ok = C.reference_design("PERIM_OPEN")
    bad = [
        {**ok.to_json(), "tile_count": 7},
        {**ok.to_json(), "acu_supply_c": (10.0,) * 4},
        {**ok.to_json(), "acu_flow_fraction": (0.5,) * 3},
        {**ok.to_json(), "closed_units": (0, 1, 2, 3)},
        {**C.reference_design("INROW_CAC").to_json(), "tile_count": 8},
    ]
    for kw in bad:
        with pytest.raises(C.IllegalDesign):
            C.compile_design(S.Task(), S.Design(**kw))
    with pytest.raises(C.IllegalDesign, match="not allowed"):
        C.compile_design(S.Task(allowed_families=("INROW_OPEN",)), ok)


@pytest.mark.parametrize("family,tiles", CASES)
def test_running_units_carry_one_return_probe_in_front_of_their_return_face(family, tiles):
    design = C.reference_design(family, tiles)
    closed = (0,) if S.FAMILIES[family]["units"] == "perimeter" else ()
    design = dataclasses.replace(design, closed_units=closed)
    p = C.compile_design(S.Task(), design)
    probes = p["instrumentation"]["probes"]
    names = list(S.unit_ids(family))
    mine = {v["meta"]["name"]: v for v in probes.values() if v["meta"]["name"].startswith("return_")}
    assert sorted(mine) == sorted(f"return_{n}" for i, n in enumerate(names) if i not in closed)
    assert all(v["meta"]["enabled"] for v in mine.values())
    # base probes stay as they were (disabled)
    assert all(not v["meta"]["enabled"] for k, v in probes.items() if not k.startswith("dcd_return_"))
    boxes = _boxes(p)
    acus = {a["meta"]["name"]: a for a in p["constructions"]["acus"].values()}
    for n, v in mine.items():
        unit = n[len("return_"):]
        q = v["geometry"]["location"]
        b = F.aabb(acus[unit]["geometry"])
        # outside every object, at the offset from the unit's box along exactly one axis
        for name, ob in boxes.items():
            assert not (ob[0] < q["x"] < ob[1] and ob[2] < q["y"] < ob[3] and ob[4] < q["z"] < ob[5]), (n, name)
        gaps = [max(b[0] - q["x"], q["x"] - b[1], 0), max(b[2] - q["y"], q["y"] - b[3], 0), max(b[4] - q["z"], q["z"] - b[5], 0)]
        assert sorted(round(g, 9) for g in gaps) == [0, 0, round(C.RETURN_PROBE_OFFSET_M, 9)], (n, gaps)
