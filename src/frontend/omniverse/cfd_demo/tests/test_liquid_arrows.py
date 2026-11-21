"""Liquid flow arrows in Kit: the layout from liquid_flow.flow_arrows, the
per-frame motion, and the point-instanced cones authored in USD (pxr only).

The browser sends the display options and scales (setLiquidFrame display.arrows);
Kit lays the arrows out from the verified result itself, the same rule the
Three.js view uses, on each edge's own polyline (Kit has no rendered tube
paths), and leaves server branches to the Three.js view.
"""
import copy
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import liquid_arrows as la  # noqa: E402
from dc.cfd_bridge import liquid_colour as lc  # noqa: E402
from dc.cfd_bridge import liquid_flow as lf  # noqa: E402

TESTDATA = os.path.abspath(os.path.join(HERE, "..", "..", "..", "shared", "testdata"))
OPTS = {"density": 1, "speedScale": 2, "animate": True, "colour": "velocity",
        "mass": [0.0037, 0.51], "velocity": [0.001, 0.072], "colormap": "viridis"}


@pytest.fixture(scope="module")
def data():
    with open(os.path.join(TESTDATA, "liquid_flow_fixture.json")) as f:
        return json.load(f)


def test_options_are_validated():
    assert la.parse_arrow_options(None) is None
    assert la.parse_arrow_options(OPTS)["density"] == 1
    for bad in ({**OPTS, "density": 9}, {**OPTS, "colour": "pink"}, {**OPTS, "mass": [1, 0]},
                {**OPTS, "colormap": "rainbow"}, {**OPTS, "extra": 1}, {**OPTS, "speedScale": -1}):
        with pytest.raises(ValueError):
            la.parse_arrow_options(bad)


def test_one_arrow_per_moving_cell_and_header_cell_none_on_branches(data):
    spec = la.arrow_spec(data, 0, OPTS)
    layout = lf.flow_arrows(data, 0)
    assert len(spec["arrows"]) == len(layout["arrows"])
    assert {a["edgeId"] for a in spec["arrows"]} == {a["edgeId"] for a in layout["arrows"]}
    assert spec["skippedBranches"] == len([b for b in layout["branches"] if not b["stagnant"]])
    with_density = la.arrow_spec(data, 0, {**OPTS, "density": 3})
    assert len(with_density["arrows"]) == 3 * len(spec["arrows"])


def test_at_rest_each_arrow_stands_on_its_cell_lifted_over_its_pipe(data):
    spec = la.arrow_spec(data, 0, {**OPTS, "animate": False})
    motion = la.ArrowMotion(spec)
    positions, orientations, scales = motion.place(12.3)  # not animated: time does not matter
    layout = lf.flow_arrows(data, 0)
    straight = 0
    for i, a in enumerate(layout["arrows"]):
        p, (w, x, y, z), s = positions[i], orientations[i], scales[i]
        lift = spec["arrows"][i]["lift"]
        d = a["direction"]
        if abs(d[1]) < 0.7:  # a horizontal pipe: the arrow rides on its crown
            assert p == pytest.approx([a["position"][0], a["position"][1] + lift, a["position"][2]], abs=1e-9)
        else:  # a vertical one (a manifold header): on its axis, as a collar wider than the pipe
            assert p == pytest.approx(a["position"], abs=1e-9)
        # The cone's +Y axis points along the flow: along the pipe's run at the
        # cell (exactly, on a straight stretch; following the bend at a corner).
        axis = [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)]
        along = sum(axis[k] * d[k] for k in range(3))
        assert along > 0
        straight += along > 0.999
        assert s[1] > s[0] == s[2] > 0
    assert straight >= 0.75 * len(layout["arrows"])  # the rest stand at a corner of their run


def test_size_and_colour_follow_the_browser_scales(data):
    spec = la.arrow_spec(data, 2, OPTS)
    layout = lf.flow_arrows(data, 2)
    lo, hi = OPTS["mass"]
    for a, item in zip(layout["arrows"], spec["arrows"]):
        n = max(0.0, min(1.0, (a["massFlowKgS"] - lo) / (hi - lo)))
        size = 0.007 + 0.021 * math.sqrt(n)
        if abs(a["direction"][1]) >= 0.7:  # a collar on a vertical header is at least 1.5 pipe radii
            D = data["edge_D_m"][data["edge_ids"].index(a["edgeId"])]
            assert item["radius"] == pytest.approx(max(size, 0.75 * D), rel=1e-12)
        else:
            assert item["radius"] == pytest.approx(size, rel=1e-12)
        if a["velocityMS"] is not None:
            assert spec["bins"][item["bin"]] == pytest.approx(
                [c / 255 for c in lc.value_to_rgb8(a["velocityMS"], *OPTS["velocity"], OPTS["colormap"])], abs=0.5 / 255 + 1 / 32)
    plain = la.arrow_spec(data, 2, {**OPTS, "colour": "plain"})
    assert len(plain["bins"]) == 1 and {a["bin"] for a in plain["arrows"]} == {0}


def test_arrows_move_forward_along_their_pipe_and_wrap_in_their_stretch(data):
    spec = la.arrow_spec(data, 0, OPTS)
    motion = la.ArrowMotion(spec)
    p0, o0, _ = motion.place(1.0)
    p1, _, _ = motion.place(1.05)
    forward = wrapped = 0
    for i, item in enumerate(spec["arrows"]):
        d = [p1[i][k] - p0[i][k] for k in range(3)]
        step = math.sqrt(sum(v * v for v in d))
        expect = item["speed"] * OPTS["speedScale"] * 0.05
        w, x, y, z = o0[i]
        axis = [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)]
        if step > 3 * expect + 1e-9:
            wrapped += 1
            continue
        assert sum(axis[k] * d[k] for k in range(3)) > 0
        forward += 1
    assert forward > 0.9 * len(spec["arrows"])


def test_usd_point_instancer(data):
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateInMemory()
    spec = la.arrow_spec(data, 0, OPTS)
    motion = la.ArrowMotion(spec)
    path = la.author_arrows(stage, spec, motion.place(0.0))
    inst = UsdGeom.PointInstancer(stage.GetPrimAtPath(path))
    assert inst and len(inst.GetPositionsAttr().Get()) == len(spec["arrows"])
    assert len(inst.GetPrototypesRel().GetTargets()) == len(spec["bins"])
    assert sorted(set(inst.GetProtoIndicesAttr().Get())) == sorted({a["bin"] for a in spec["arrows"]})
    assert stage.GetPrimAtPath(path).GetCustomDataByKey("dcdtuni:liquidFlow") is True
    p1 = motion.place(0.5)
    la.update_arrows(stage, path, p1)
    assert list(inst.GetPositionsAttr().Get()[0]) == pytest.approx(p1[0][0], abs=1e-6)
    la.remove_arrows(stage)
    assert not stage.GetPrimAtPath(path)
