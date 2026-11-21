"""Liquid result colours computed by Kit from a granted DAE result.json.

The fixture is cut from a real Shinaide differentiable-DAE run (rack m_1 with
two servers, CDU m_30, pipes m_31/m_32, one tee, one flow valve, 3 samples).
liquid_colour_expected.json is the cross-implementation contract: the React
side (liquidTempColor) is tested against the same numbers."""
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import liquid_colour as lc  # noqa: E402

TESTDATA = os.path.abspath(os.path.join(HERE, "..", "..", "..", "shared", "testdata"))
FIXTURE = os.path.join(TESTDATA, "liquid_result_fixture.json")
EXPECTED = os.path.join(TESTDATA, "liquid_colour_expected.json")


@pytest.fixture(scope="module")
def result():
    with open(FIXTURE) as f:
        return lc.LiquidResult(json.load(f))


def by_key(frame):
    return {(t["kind"], t["modelUid"], t["equipmentUid"], t["liquidPart"]): t for t in frame["targets"]}


def test_coolwarm_table_matches_the_react_ramp_ends_and_midpoint():
    lut = lc.coolwarm_lut()
    assert len(lut) == 256
    assert lut[0] == (59, 76, 192) and lut[255] == (180, 4, 38)
    assert lut[127] == (254, 254, 255) and lut[128] == (255, 254, 254)  # Math.round, not banker's


def test_every_liquid_part_gets_a_target(result):
    kinds = sorted({t[0] for t in result.targets})
    assert kinds == ["cdu", "manifold", "pipe", "server", "tee", "valve"]
    parts = {(t[1], t[3]) for t in result.targets if t[0] == "manifold"}
    assert parts == {("m_1", "supply-manifold"), ("m_1", "return-manifold")}


def test_rules_follow_the_documented_joins(result):
    f = by_key(result.frame(1))
    s = 1
    rack = result.data["object_bindings"]["m_1"]["edge_ids"]
    sup = result.edge_mean([e for e in rack if e.endswith("_branch_in")], s)
    ret = result.edge_mean([e for e in rack if e.endswith("_branch_out")], s)
    assert f[("manifold", "m_1", "", "supply-manifold")]["T"] == pytest.approx(sup)
    assert f[("manifold", "m_1", "", "return-manifold")]["T"] == pytest.approx(ret)
    assert ret > sup  # return water is warmer than supply
    assert f[("cdu", "m_30", "", "")]["T"] == pytest.approx(result.node_temp("m_30_supply", s))
    assert f[("tee", "m_31", "tee_1778059219789", "")]["T"] == pytest.approx(
        result.node_temp("m_31_tee_tee_1778059219789", s))
    # The flow valve's own edge wins over the tee node at the same fitting.
    valve = f[("valve", "m_32", "valve_1778059294098", "")]
    assert valve["T"] == pytest.approx(result.edge_mean(["m_32_control_valve_1778059294098"], s))
    assert ("tee", "m_32", "valve_1778059294098", "") not in f
    # Servers join chips by cold-plate edge id, not by list order.
    assert f[("server", "m_1", "e_2", "")]["T"] == pytest.approx(result.data["chip_temp_K"][1][s])


def test_domains_are_per_group_and_pinnable(result):
    fr = result.frame(-1)
    assert fr["sample"] == 2
    net = fr["groups"]["network"]["domain"]
    temps = [t["T"] for t in fr["targets"] if t["group"] == "network"]
    assert net == [min(temps), max(temps)]
    pinned = result.frame(0, {"network": [290.0, 320.0]})
    assert pinned["groups"]["network"]["source"] == "explicit"
    assert pinned["groups"]["chips"]["source"] == "frame min..max"
    chips = [row[0] for row in result.data["chip_temp_K"]]
    assert pinned["groups"]["chips"]["domain"] == [min(chips), max(chips)]
    with pytest.raises(ValueError):
        result.frame(0, {"network": [300.0, 300.0]})
    with pytest.raises(ValueError):
        result.frame(3)


def test_legacy_results_without_bindings_are_refused():
    with pytest.raises(ValueError, match="object_bindings"):
        lc.LiquidResult({"time_s": [0.0], "cell_temp_K": []})
    assert "object_bindings" in lc.missing_keys({"time_s": [0.0]})


def test_expected_contract_file_is_reproduced(result):
    """The shared contract: regenerate with `python -m ... --write` only on a
    deliberate rule change, and the React test must follow."""
    with open(EXPECTED) as f:
        expected = json.load(f)
    for sample, exp in expected["frames"].items():
        fr = result.frame(int(sample))
        got = by_key(fr)
        exp = dict(exp)
        domains = exp.pop("__domains__")
        assert {g: v["domain"] for g, v in fr["groups"].items()} == domains
        assert set(got) == {tuple(k.split("|")) for k in exp}
        for k, v in exp.items():
            g = got[tuple(k.split("|"))]
            assert g["T"] == pytest.approx(v["T"], abs=1e-9) and g["rgb"] == v["rgb"]


def test_bind_result_refuses_changed_files_and_foreign_projects():
    import hashlib
    raw = open(FIXTURE, "rb").read()
    granted = {"resultSha256": hashlib.sha256(raw).hexdigest(), "projectUid": "proj_Shinaide",
               "projectContentSha256": "a" * 64}
    result, matches = lc.bind_result(granted, raw, "proj_Shinaide", "a" * 64)
    assert result.samples == 3 and matches is True
    assert lc.bind_result(granted, raw, "proj_Shinaide", "b" * 64)[1] is False  # edited since: labelled, allowed
    with pytest.raises(ValueError, match="changed since"):
        lc.bind_result(granted, raw + b" ", "proj_Shinaide", "a" * 64)  # a newer step landed / tampered
    with pytest.raises(ValueError, match="belongs to project"):
        lc.bind_result(granted, raw, "proj_Other", "a" * 64)
    with pytest.raises(ValueError, match="belongs to project"):
        lc.bind_result(granted, raw, None, "a" * 64)  # no scene loaded
