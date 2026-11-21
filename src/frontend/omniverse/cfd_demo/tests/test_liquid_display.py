"""Liquid result display rules shared by Three.js and Kit: the colouring
quantity (temperature / flow / velocity / pressure) of every network part and
pipe cell, and the flow-arrow layout (liquid_flow.flow_arrows).

The fixture is the C1 cut of a real Shinaide DAE run (liquid_flow_fixture.json:
CDU m_30, pipes m_31/m_32 with tees and the reversed flow valve, rack m_1 with
servers e_1/e_2, samples 0/5/10 of a run whose pump slowed to 0.7 and whose
valve closed to 0.3). liquid_display_expected.json is the cross-implementation
contract: the React side (liquidDisplay.ts, liquidFlow.ts) is tested against
the same numbers.
"""
import copy
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import liquid_colour as lc  # noqa: E402
from dc.cfd_bridge import liquid_flow as lf  # noqa: E402

TESTDATA = os.path.abspath(os.path.join(HERE, "..", "..", "..", "shared", "testdata"))
FIXTURE = os.path.join(TESTDATA, "liquid_flow_fixture.json")
EXPECTED = os.path.join(TESTDATA, "liquid_display_expected.json")
VALVE_EDGE = "m_32_control_valve_1778059294098"


def display_fixture(raw):
    """The flow fixture plus the two bindings the display rules add parts for:
    a ring-feed pipe_junction, and an untee'd pipe whose binding lists no
    edges (the shared contract and the React test apply the same patch)."""
    raw = copy.deepcopy(raw)
    raw["object_bindings"]["ring_supply_feed_tee"] = {
        "label": "Supply_Feed_Tee", "kind": "pipe_junction",
        "node_ids": ["ring_supply_feed_tee_junction"], "edge_ids": []}
    raw["object_bindings"]["cdu_supply_feed"] = {
        "label": "CDU_supply_Feed", "kind": "pipe_segment", "node_ids": [], "edge_ids": []}
    return raw


@pytest.fixture(scope="module")
def data():
    with open(FIXTURE) as f:
        return display_fixture(json.load(f))


def part(frame, kind, model, equip="", side=""):
    return next((p for p in frame["parts"]
                 if (p["kind"], p["modelUid"], p["equipmentUid"], p["liquidPart"]) == (kind, model, equip, side)),
                None)


def node_p(data, node, s):
    return data["node_pressure_Pa"][data["node_ids"].index(node)][s]


# ---------------------------------------------------------------- quantities

def test_temperature_parts_are_the_colour_rule_and_cells_are_cell_temperatures(data):
    for s in range(3):
        fr = lc.quantity_frame(data, s, "temperature")
        colours = lc.LiquidResult(data).frame(s)
        expect = {(t["kind"], t["modelUid"], t["equipmentUid"], t["liquidPart"]): t["T"]
                  for t in colours["targets"] if t["group"] == "network"}
        got = {(p["kind"], p["modelUid"], p["equipmentUid"], p["liquidPart"]): p["value"] for p in fr["parts"]}
        assert got == expect
        assert got[("junction", "ring_supply_feed_tee", "", "")] == \
            data["node_temperature_K"][data["node_ids"].index("ring_supply_feed_tee_junction")][s]
        assert fr["cells"] == [row[s] for row in data["cell_temp_K"]]
        assert fr["unit"] == "K"


def test_flow_and_velocity_parts_follow_the_flow_rule(data):
    s = 1
    flow = lf.flow_frame(data, s)
    lpm = lc.quantity_frame(data, s, "flow")
    vel = lc.quantity_frame(data, s, "velocity")
    assert lpm["unit"] == "L/min" and vel["unit"] == "m/s"
    for t in flow["targets"]:
        if t["group"] != "network" or t["kind"] in ("pump", "hx"):
            continue
        p = part(lpm, t["kind"], t["modelUid"], t["equipmentUid"], t["liquidPart"])
        assert p["value"] == t["volumeFlowLpm"]
    # Pumps and heat exchangers are table rows, not coloured parts.
    assert not any(p["kind"] in ("pump", "hx") for p in lpm["parts"])
    # A cell's flow is its edge's |flow| in L/min; its velocity its edge's.
    for c, cell in enumerate(flow["cells"]):
        assert lpm["cells"][c] == cell["volumeFlowLpm"]
        assert vel["cells"][c] == cell["velocityMS"]
    # A manifold's velocity is its header's (the flow rule has none for it).
    rack = data["object_bindings"]["m_1"]["edge_ids"]
    header = next(e for e in rack if "_mf_supply" in e and not e.endswith("_branch_in"))
    edge = next(e for e in flow["edges"] if e["edgeId"] == header)
    assert part(vel, "manifold", "m_1", side="supply-manifold")["value"] == edge["velocityMS"]
    # Tees and the CDU have no diameter: no velocity, so they are not painted.
    assert part(vel, "tee", "m_31", "tee_1778059219789") is None and part(vel, "cdu", "m_30") is None
    assert part(vel, "valve", "m_32", "valve_1778059294098")["value"] == pytest.approx(
        next(e for e in flow["edges"] if e["edgeId"] == VALVE_EDGE)["velocityMS"], rel=1e-15)


def test_pressure_is_absolute_kpa_interpolated_along_each_edge(data):
    s = 2
    fr = lc.quantity_frame(data, s, "pressure")
    assert fr["unit"] == "kPa"
    by_edge = {}
    for c, e in enumerate(data["cell_edge_ids"]):
        by_edge.setdefault(e, []).append(c)
    for e, cells in by_edge.items():
        i = data["edge_ids"].index(e)
        pa, pb = node_p(data, data["edge_from"][i], s), node_p(data, data["edge_to"][i], s)
        for k, c in enumerate(cells):
            expect = (pa + (pb - pa) * (k + 0.5) / len(cells)) / 1000
            assert fr["cells"][c] == pytest.approx(expect, rel=1e-15)
    assert part(fr, "tee", "m_31", "tee_1778059219789")["value"] == pytest.approx(
        node_p(data, "m_31_tee_tee_1778059219789", s) / 1000, rel=1e-15)
    assert part(fr, "cdu", "m_30")["value"] == pytest.approx(node_p(data, "m_30_supply", s) / 1000, rel=1e-15)
    # The valve has no cells: the mean of its two end pressures.
    i = data["edge_ids"].index(VALVE_EDGE)
    ends = (node_p(data, data["edge_from"][i], s) + node_p(data, data["edge_to"][i], s)) / 2 / 1000
    assert part(fr, "valve", "m_32", "valve_1778059294098")["value"] == pytest.approx(ends, rel=1e-15)


def test_display_domain_spans_parts_and_cells(data):
    for q in lc.QUANTITIES:
        fr = lc.quantity_frame(data, 0, q)
        values = [p["value"] for p in fr["parts"]] + [v for v in fr["cells"] if v is not None]
        assert fr["domain"] == [min(values), max(values)]
        whole = lc.quantity_global_domain(data, q)
        for s in range(3):
            lo, hi = lc.quantity_frame(data, s, q)["domain"]
            assert whole[0] <= lo and hi <= whole[1]


def test_a_pipe_junction_shows_its_node(data):
    """A pipe_junction model (a ring-feed tee) is coloured by its node: its
    temperature and pressure, and for flow what passes through it."""
    node = "ring_supply_feed_tee_junction"
    i = data["node_ids"].index(node)
    flow = lf.flow_frame(data, 1)
    through = lf.node_throughput(flow, node)
    feed = data["edge_flow_kg_s"][data["edge_ids"].index("cdu_supply_feed")][1]
    assert through == pytest.approx(feed, rel=1e-15)  # the feed is all that enters it
    assert part(lc.quantity_frame(data, 1, "flow"), "junction", "ring_supply_feed_tee")["value"] == pytest.approx(
        through / data["fluid"]["rho_kg_m3"] * 60000, rel=1e-15)
    assert part(lc.quantity_frame(data, 1, "pressure"), "junction", "ring_supply_feed_tee")["value"] == pytest.approx(
        data["node_pressure_Pa"][i][1] / 1000, rel=1e-15)
    assert part(lc.quantity_frame(data, 1, "velocity"), "junction", "ring_supply_feed_tee") is None
    # Kit paints it by its model uid, like a pipe.
    kit = lc.LiquidResult(data).frame(1, None, quantity="flow")
    assert any(t["kind"] == "junction" and t["modelUid"] == "ring_supply_feed_tee" for t in kit["targets"])


def test_a_pipe_binding_without_edges_owns_the_edge_named_after_it(data):
    """Results compiled before the binding fix list no edges for an untee'd pipe
    (its single edge is named after the pipe uid); the display joins it anyway."""
    patched = copy.deepcopy(data)
    cells = [row[0] for e, row in zip(patched["cell_edge_ids"], patched["cell_temp_K"]) if e == "cdu_supply_feed"]
    assert cells
    T = part(lc.quantity_frame(patched, 0, "temperature"), "pipe", "cdu_supply_feed")["value"]
    assert T == pytest.approx(sum(cells) / len(cells), rel=1e-15)
    flow = next(t for t in lf.flow_frame(patched, 0)["targets"] if t["modelUid"] == "cdu_supply_feed")
    i = patched["edge_ids"].index("cdu_supply_feed")
    assert flow["massFlowKgS"] == abs(patched["edge_flow_kg_s"][i][0])


def test_colour_tables_match_the_editor_presets():
    assert lc.colormap_lut("coolwarm") == lc.coolwarm_lut()
    viridis = lc.colormap_lut("viridis")
    assert viridis[0] == (68, 1, 84) and viridis[255] == (253, 231, 37)
    jet = lc.colormap_lut("jet")
    assert jet[0] == (0, 0, 128) and jet[255] == (128, 0, 0)
    with pytest.raises(ValueError):
        lc.colormap_lut("rainbow")


def test_kit_frames_paint_the_chosen_quantity_on_the_browser_domain(data):
    result = lc.LiquidResult(data)
    domain = lc.quantity_global_domain(data, "flow")
    fr = result.frame(1, {"network": domain}, quantity="flow", colormap="viridis")
    assert fr["quantity"] == "flow" and fr["groups"]["network"]["source"] == "explicit"
    q = lc.quantity_frame(data, 1, "flow")
    for t in fr["targets"]:
        if t["group"] != "network":
            assert "T" in t  # chips stay chip temperature
            continue
        p = part(q, t["kind"], t["modelUid"], t["equipmentUid"], t["liquidPart"])
        assert t["value"] == p["value"]
        assert t["rgb"] == list(lc.value_to_rgb8(p["value"], domain[0], domain[1], "viridis"))
    # The temperature frame without arguments is unchanged (the original contract).
    plain = result.frame(1)
    assert plain["quantity"] == "temperature"
    assert all("T" in t for t in plain["targets"])
    with pytest.raises(ValueError):
        result.frame(1, None, quantity="enthalpy")


# ---------------------------------------------------------------- arrows

def test_arrows_leave_out_server_branches_and_follow_the_actual_flow(data):
    s = 0
    flow = lf.flow_frame(data, s)
    arrows = lf.flow_arrows(data, s)
    edges = {a["edgeId"] for a in arrows["arrows"]}
    assert not any(e.endswith("_branch_in") or e.endswith("_branch_out") for e in edges)
    for a in arrows["arrows"]:
        if a["kind"] != "cell":
            continue
        cell = flow["cells"][a["cell"]]
        assert a["position"] == cell["position"] and a["direction"] == cell["direction"]
        assert a["massFlowKgS"] == cell["massFlowKgS"] and a["velocityMS"] == cell["velocityMS"]
    # The reversed valve spoke points from the valve's actual upstream.
    spoke = [a for a in arrows["arrows"] if a["edgeId"] == "m_32_spoke_ring_return_tee_m_20_before_control"]
    assert spoke


def test_header_arrows_carry_what_is_still_to_be_delivered_or_already_collected(data):
    s = 2
    arrows = lf.flow_arrows(data, s)
    branches = {(b["serverUid"], b["side"]): b for b in arrows["branches"] if b["rackUid"] == "m_1"}
    assert set(branches) == {("e_1", "supply"), ("e_1", "return"), ("e_2", "supply"), ("e_2", "return")}
    for b in branches.values():
        i = data["edge_ids"].index(b["edgeId"])
        assert b["flowKgS"] == data["edge_flow_kg_s"][i][s]
        node = data["edge_to"][i] if b["side"] == "supply" else data["edge_from"][i]
        assert b["tap"] == data["node_positions_m"][data["node_ids"].index(node)]
    headers = [a for a in arrows["arrows"] if a["kind"] == "header"]
    assert headers
    for a in headers:
        side = "supply" if "_mf_supply" in a["edgeId"] else "return"
        d, p = a["direction"], a["position"]
        total = 0.0
        for (srv, sd), b in branches.items():
            if sd != side:
                continue
            along = sum((b["tap"][j] - p[j]) * d[j] for j in range(3))
            if (side == "supply" and along > 0) or (side == "return" and along < 0):
                total += b["flowKgS"]
        assert a["massFlowKgS"] == pytest.approx(abs(total), rel=1e-15) and a["massFlowKgS"] > 0
    # Above the top server the supply header carries nothing: no arrow there.
    top = max(b["tap"][1] for b in branches.values())
    supply_cells = [c for c, e in enumerate(data["cell_edge_ids"]) if "_mf_supply" in e and "branch" not in e]
    above = [c for c in supply_cells if data["cell_positions"][c][1] > top]
    assert above and not any(a["cell"] in above for a in headers if "_mf_supply" in a["edgeId"])


def test_stagnant_edges_have_no_arrows(data):
    flagged = copy.deepcopy(data)
    pipe_edge = "m_31_seg0"
    flagged["edge_stagnant"][flagged["edge_ids"].index(pipe_edge)] = True
    arrows = lf.flow_arrows(flagged, 0)
    assert not any(a["edgeId"] == pipe_edge for a in arrows["arrows"])


def test_arrow_domains_cover_arrows_and_branches(data):
    for s in range(3):
        fr = lf.flow_arrows(data, s)
        masses = [a["massFlowKgS"] for a in fr["arrows"]] + [b["massFlowKgS"] for b in fr["branches"]]
        assert fr["domains"]["mass"] == [min(masses), max(masses)]
    whole = lf.arrow_global_domains(data)
    for s in range(3):
        lo, hi = lf.flow_arrows(data, s)["domains"]["mass"]
        assert whole["mass"][0] <= lo and hi <= whole["mass"][1]


def test_expected_contract_file_is_reproduced(data):
    """Regenerate with tests/_write_liquid_display_expected.py only on a
    deliberate rule change; the React test reads the same file."""
    with open(EXPECTED) as f:
        expected = json.load(f)
    assert expected["samples"] == [0, 1, 2]
    for q in lc.QUANTITIES:
        for s in expected["samples"]:
            _assert_close(lc.quantity_frame(data, s, q), expected["quantities"][q][str(s)], f"{q} {s}")
        _assert_close(lc.quantity_global_domain(data, q), expected["globalDomains"][q], f"{q} global")
    for s in expected["samples"]:
        _assert_close(lf.flow_arrows(data, s), expected["arrows"][str(s)], f"arrows {s}")
    _assert_close(lf.arrow_global_domains(data), expected["arrowGlobalDomains"], "arrow global")
    for key, rgb in expected["colours"].items():
        cmap, value, lo, hi = key.split("|")
        assert list(lc.value_to_rgb8(float(value), float(lo), float(hi), cmap)) == rgb


def _assert_close(got, exp, where):
    if isinstance(exp, dict):
        assert isinstance(got, dict) and set(got) == set(exp), where
        for k in exp:
            _assert_close(got[k], exp[k], f"{where}.{k}")
    elif isinstance(exp, list):
        assert isinstance(got, list) and len(got) == len(exp), where
        for i, (g, e) in enumerate(zip(got, exp)):
            _assert_close(g, e, f"{where}[{i}]")
    elif isinstance(exp, float) and not isinstance(exp, bool):
        assert got == pytest.approx(exp, rel=1e-12, abs=1e-15), where
    else:
        assert got == exp, where


def test_kit_parses_the_browser_display_choice():
    assert lc.parse_display(None) == ("temperature", None)
    assert lc.parse_display({"quantity": "pressure", "colormap": "jet"}) == ("pressure", "jet")
    assert lc.parse_display({"quantity": "flow"}) == ("flow", None)
    # The arrows ride along (liquid_arrows.parse_arrow_options checks them).
    assert lc.parse_display({"quantity": "flow", "colormap": "viridis", "arrows": None}) == ("flow", "viridis")
    for bad in ({"quantity": "enthalpy"}, {"quantity": "flow", "colormap": "rainbow"},
                {"quantity": "flow", "extra": 1}, ["flow"], {"colormap": 3}):
        with pytest.raises(ValueError):
            lc.parse_display(bad)
