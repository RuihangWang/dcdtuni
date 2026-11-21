"""air-v1 design -> contract build steps (no build runs here; the real build is in test_smoke_platform)."""
import json
import math
from pathlib import Path

import pytest

from agent.scenarios.air import compile as comp
from agent.scenarios.air.placement import slot_pose, world_box
from agent.scenarios.air.rules import row_length
from agent.scenarios.air.spec import Design, Footprint, Row, Setpoint, Task
from agent.tools.edits import ProjectView, compile_edits

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "case/agent/air-v1-inrow-s01-v2/project.cfdproj"
TASK = Task(room_x_m=12.0, room_y_m=9.0, room_h_m=3.6, n_racks=8, n_perimeter_units=2, n_inrow_units=4)
SLOTS = ("RACK", "RACK", "IRC", "RACK", "RACK", "IRC")


def design(axis="x", arch="SLAB_INROW_HAC"):
    rows = (Row("R1", axis, 3.0, 2.0, "-", SLOTS), Row("R2", axis, 5.4, 2.0, "+", SLOTS))
    return Design(arch, rows, containment_pairs=(("R1", "R2"),),
                  setpoints=tuple(Setpoint(u, 0.7, 22.0) for u in ("R1:2", "R1:5", "R2:2", "R2:5")))


def template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def compiled(edit, document=None, start=0):
    return compile_edits([edit], ProjectView(document=document or template()), start=start)


def by_verb(steps, verb):
    return [s for s in steps if s["verb"] == verb]


@pytest.mark.parametrize("q", [(math.sqrt(.5), 0., 0., math.sqrt(.5)), (-.5, -.5, -.5, .5),
                               (0., -math.sqrt(.5), 0., math.sqrt(.5)), (0., 0., 0., 1.), (.1, .7, -.3, .64)])
def test_euler_yxz_round_trip_reproduces_the_quaternion(q):
    norm = math.sqrt(sum(c * c for c in q))
    q = tuple(c / norm for c in q)
    back = comp.euler_yxz_to_quaternion(comp.quaternion_to_euler_yxz(q))
    sign = 1.0 if sum(a * b for a, b in zip(q, back)) >= 0 else -1.0  # q and -q are the same turn
    assert back == pytest.approx(tuple(sign * c for c in q), abs=1e-12)


def test_steps_place_every_slot_where_slot_pose_puts_it():
    steps = compiled(comp.DesignToProject(design(), TASK))
    places = {s["args"][1]["name"]: s["args"][1] for s in by_verb(steps, "placeModel")}
    for row in design().rows:
        offset = 0.0
        for index, kind in enumerate(row.slots):
            fp = TASK.irc if kind == "IRC" else TASK.rack
            location, quaternion = slot_pose(row, offset, fp, 0.0)
            placed = places[f"{row.id}_{kind}_{index:02d}"]
            assert placed["location"] == location
            assert placed["rotationDeg"]["y"] == pytest.approx(
                math.degrees(2 * math.atan2(quaternion["y"], quaternion["w"])) % 360)
            assert (placed["size"]["W"], placed["size"]["D"]) == (fp.width_m, fp.depth_m)
            offset += fp.width_m
    assert len(by_verb(steps, "duplicateModel")) == TASK.n_racks
    assert len(by_verb(steps, "createDefaultAcuModel")) == TASK.n_inrow_units


def test_references_point_at_the_create_steps_of_their_own_model():
    start = 5
    steps = compiled(comp.DesignToProject(design(), TASK), start=start)
    creates = {start + i for i, s in enumerate(steps)
               if s["verb"] in ("duplicateModel", "createDefaultAcuModel", "createDefaultBaffleModel")}
    patches = {start + i for i, s in enumerate(steps) if s["verb"] == "addPatch"}
    for s in steps:
        for arg in s["args"][:2]:
            if isinstance(arg, str) and arg.startswith("$") and not arg.startswith("$$"):
                target = int(arg[1:])
                assert target in creates | patches, s
    # every IRC: front supply at the set-point, back return
    supply = [s["args"] for s in by_verb(steps, "updateAcuPatchPhysics") if s["args"][2] == "volumeFlowRate"]
    assert [a[3] for a in supply] == pytest.approx([0.7 * 2.8] * 4)
    faces = [s["args"][1] for s in by_verb(steps, "addPatch")]
    assert faces == ["front", "back"] * 4


def test_template_rack_is_kept_until_copied_then_removed_with_everything_else():
    doc = template()
    inv = comp.template_inventory(doc)
    steps = compiled(comp.DesignToProject(design(), TASK), document=doc)
    removes = by_verb(steps, "removeModels")
    assert inv["rackUid"] not in removes[0]["args"][0]
    assert set(removes[0]["args"][0]) | {inv["rackUid"]} == set(inv["modelUids"])
    assert removes[-1]["args"][0] == [inv["rackUid"]]
    assert steps.index(removes[-1]) > max(i for i, s in enumerate(steps) if s["verb"] == "duplicateModel")
    assert steps[1] == {"verb": "applyRoom", "args": [[[0.0, 0.0], [12.0, 0.0], [12.0, 9.0], [0.0, 9.0]], 3.6]}


@pytest.mark.parametrize("axis", ["x", "y"])
def test_containment_panels_close_the_aisle(axis):
    d = design(axis)
    steps = compiled(comp.DesignToProject(d, TASK))
    panels = {s["args"][1]["name"]: s["args"][1] for s in by_verb(steps, "placeModel")
              if s["args"][1]["name"].startswith("contain_")}
    inv = comp.template_inventory(template())
    height = max(inv["rackHeightM"], inv["acuHeightM"])  # the taller unit sets the roof
    length = row_length(d.rows[0], TASK)
    width = 5.4 - 3.0 - TASK.rack.depth_m

    def bounds(panel):
        q = comp.euler_yxz_to_quaternion(panel["rotationDeg"])
        size = (panel["size"]["W"], panel["size"]["H"], panel["size"]["D"])
        return [tuple(round(v, 9) for v in b) for b in world_box_general(panel["location"], q, size)]

    along, across = (0, 2) if axis == "x" else (2, 0)
    roof = bounds(panels["contain_00_roof"])
    assert roof[1] == (height, height)
    assert roof[along] == (2.0, 2.0 + length)
    assert roof[across] == pytest.approx((3.0 + TASK.rack.depth_m / 2, 3.0 + TASK.rack.depth_m / 2 + width))
    for end, at in (("end0", 2.0), ("end1", 2.0 + length)):
        b = bounds(panels[f"contain_00_{end}"])
        assert b[along] == (at, at) and b[1] == (0.0, height)
        assert b[across] == roof[across]
    # 1.9558 m racks under a 2.0 m roof: one solid strip over each of the eight racks
    assert sorted(n for n in panels if "_fill_" not in n) == ["contain_00_end0", "contain_00_end1", "contain_00_roof"]
    assert len(panels) == 3 + TASK.n_racks
    cooling = [s["args"][1:] for s in by_verb(steps, "setModelCooling")]
    assert cooling.count(["type", "perforated"]) == 3


def world_box_general(location, q, size):
    x, y, z, w = q
    def rot(v):
        vx, vy, vz = v
        tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
        return (vx + w * tx + y * tz - z * ty, vy + w * ty + z * tx - x * tz, vz + w * tz + x * ty - y * tx)
    pts = [rot((a, b, c)) for a in (0, size[0]) for b in (0, size[1]) for c in (0, size[2])]
    return [(min(p[i] for p in pts) + location[k], max(p[i] for p in pts) + location[k])
            for i, k in enumerate("xyz")]


def test_world_box_general_agrees_with_the_yaw_only_placement_helper():
    for yaw in (0.0, 90.0, 180.0, 270.0):
        q = comp.euler_yxz_to_quaternion({"x": 0.0, "y": yaw, "z": 0.0})
        loc = {"x": 1.0, "y": 0.0, "z": 2.0}
        quat = {"x": q[0], "y": q[1], "z": q[2], "w": q[3]}
        expected = world_box(loc, quat, (0.6, 2.0, 1.2))
        got = world_box_general(loc, q, (0.6, 2.0, 1.2))
        assert [tuple(round(v, 9) for v in b) for b in got] == [tuple(round(v, 9) for v in b) for b in expected]


def test_gap_and_short_unit_get_solid_fill_strips():
    slots = ("RACK", "GAP", "IRC", "RACK")
    rows = (Row("R1", "x", 3.0, 2.0, "-", slots), Row("R2", "x", 5.4, 2.0, "+", slots))
    d = Design("SLAB_INROW_HAC", rows, containment_pairs=(("R1", "R2"),),
               setpoints=(Setpoint("R1:2", .5, 20.), Setpoint("R2:2", .5, 20.)))
    task = Task(room_x_m=12.0, room_y_m=9.0, room_h_m=3.6, n_racks=4, n_inrow_units=2)
    steps = compiled(comp.DesignToProject(d, task))
    fills = sorted(s["args"][1]["name"] for s in by_verb(steps, "placeModel") if "_fill_" in s["args"][1]["name"])
    # the rack (1.9558 m) is shorter than nothing, the GAP is open to the floor, the cooler (2.0 m) is the top
    assert fills == ["contain_00_R1_fill_00", "contain_00_R1_fill_01", "contain_00_R1_fill_03",
                     "contain_00_R2_fill_00", "contain_00_R2_fill_01", "contain_00_R2_fill_03"]
    gap = next(s["args"][1] for s in by_verb(steps, "placeModel") if s["args"][1]["name"] == "contain_00_R1_fill_01")
    assert gap["location"]["y"] == 0.0 and gap["size"]["H"] == pytest.approx(2.0)
    assert [s["args"][1:] for s in by_verb(steps, "setModelCooling")].count(["type", "wall"]) == 6


def test_illegal_or_unsupported_designs_build_nothing():
    with pytest.raises(comp.IllegalDesign):
        comp.DesignToProject(design(arch="SLAB_INROW_CAC"), TASK)  # a hot aisle cannot be cold-contained
    perimeter = Design("RF_PERIM_OPEN", (Row("R1", "x", 3.0, 2.0, "+", ("RACK",) * 4),
                                         Row("R2", "x", 5.6, 2.0, "-", ("RACK",) * 4)))
    with pytest.raises((NotImplementedError, comp.IllegalDesign)):
        comp.DesignToProject(perimeter, Task(12.0, 9.0, 3.6, 8, n_perimeter_units=2))
    with pytest.raises(ValueError, match="porosity"):
        comp.DesignToProject(design(), TASK, leak_open_area_pct=0.0)
    with pytest.raises(NotImplementedError, match="equal-depth"):
        comp.DesignToProject(design(), Task(12.0, 9.0, 3.6, 8, n_inrow_units=4, irc=Footprint(0.6, 1.0)))
    with pytest.raises(ValueError, match="above the 1.0 m room"):
        compiled(comp.DesignToProject(design(), Task(12.0, 9.0, 1.0, 8, n_inrow_units=4)))


def test_liquid_template_is_refused():
    doc = template()
    doc.setdefault("runDefaults", {}).setdefault("liquid", {})["enabled"] = True
    with pytest.raises(NotImplementedError, match="liquid"):
        compiled(comp.DesignToProject(design(), TASK), document=doc)


def test_to_dict_carries_the_design_and_the_compiler_version():
    row = comp.DesignToProject(design(), TASK, name="demo").to_dict()
    assert row["op"] == "DesignToProject" and row["compiler"] == comp.VERSION
    assert tuple(row["design"]["rows"][0]["slots"]) == SLOTS
    json.dumps(row)
