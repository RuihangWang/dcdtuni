import json
import math
import os
from pathlib import Path

import pytest

from agent.scenarios.air.placement import intake_normal, rewrite_uids, slot_pose, world_box
from agent.scenarios.air.spec import Footprint, Row

REPO = Path(__file__).resolve().parents[3]
FP = Footprint(0.6, 1.2)


def _flat(box):
    return tuple(v for pair in box for v in pair)


def test_slot_pose_box_and_intake_for_all_orientations():
    for axis in ("x", "y"):
        for front in ("+", "-"):
            row = Row("R", axis, 4.0, 2.0, front, ("RACK",) * 3)
            loc, q = slot_pose(row, 0.6, FP, floor_y=0.0)
            (x0, x1), _, (z0, z1) = world_box(loc, q, (0.6, 2.0, 1.2))
            along, across = ((x0, x1), (z0, z1)) if axis == "x" else ((z0, z1), (x0, x1))
            assert along == pytest.approx((2.6, 3.2))
            assert across == pytest.approx((3.4, 4.6))
            n = intake_normal(q)
            k = 2 if axis == "x" else 0  # perpendicular scene axis: z for x-rows, x for y-rows
            assert abs(abs(n[k]) - 1.0) < 1e-12
            assert math.copysign(1.0, n[k]) == (1.0 if front == "+" else -1.0)


def test_slot_pose_reproduces_the_racks_of_a_real_case():
    proj = json.loads((REPO / "case/Base_coldAisleIso/Base_coldAisleIso.cfdproj").read_text())
    for rack in proj["constructions"]["racks"].values():
        g = rack["geometry"]
        size = (g["size"]["x"], g["size"]["y"], g["size"]["z"])
        (x0, x1), _, (z0, z1) = world_box(g["location"], g["quaternion"], size)
        n = intake_normal(g["quaternion"])
        if abs(n[2]) > 0.5:
            row = Row("R", "x", (z0 + z1) / 2, x0, "+" if n[2] > 0 else "-", ("RACK",))
        else:
            row = Row("R", "y", (x0 + x1) / 2, z0, "+" if n[0] > 0 else "-", ("RACK",))
        loc, q = slot_pose(row, 0.0, Footprint(size[0], size[2]), floor_y=g["location"]["y"])
        assert (loc["x"], loc["y"], loc["z"]) == pytest.approx((g["location"]["x"], g["location"]["y"], g["location"]["z"]), abs=1e-9)
        assert _flat(world_box(loc, q, size)) == pytest.approx(_flat(world_box(g["location"], g["quaternion"], size)), abs=1e-9)


def test_rewrite_uids_makes_clones_unique_and_keeps_the_template():
    tmpl = {"uid": "m_1", "constructions": {"servers": [{"uid": "s_1"}, {"uid": "s_2"}]}, "meta": {"name": "Rack"}}
    clones = [rewrite_uids(tmpl, lambda old, i=i: f"{old}__c{i}") for i in range(3)]
    uids = [u for c in clones for u in (c["uid"], *[s["uid"] for s in c["constructions"]["servers"]])]
    assert len(uids) == len(set(uids)) == 9
    assert tmpl["uid"] == "m_1"


def test_uid_dictionary_keys_and_references_move_with_the_objects():
    original={"uid":"rack","constructions":{"servers":{"server":{"uid":"server","objectPath":"constructions.racks.rack.servers.server"}}}}
    clone=rewrite_uids(original,lambda old:old+"_new")
    assert clone["uid"]=="rack_new"
    server=clone["constructions"]["servers"]["server_new"]
    assert server["uid"]=="server_new" and server["objectPath"]=="constructions.racks.rack_new.servers.server_new"
    assert "server" in original["constructions"]["servers"]
