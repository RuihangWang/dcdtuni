"""Liquid flow quantities and directions computed from a DAE result.json.

The fixture is a cut of a real Shinaide DAE run (CDU m_30 with pump and HX,
pipes m_31/m_32 with every tee, the reversed flow valve
m_32/valve_1778059294098, rack m_1 with servers e_1/e_2) at samples 0, 5 and
10, between which the pump slowed to 0.7 and the valve closed to 0.3.
liquid_flow_expected.json is the cross-implementation contract: the React
side (liquidFlow.ts) is tested against the same numbers.
"""
import copy
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import liquid_flow as lf  # noqa: E402

TESTDATA = os.path.abspath(os.path.join(HERE, "..", "..", "..", "shared", "testdata"))
FIXTURE = os.path.join(TESTDATA, "liquid_flow_fixture.json")
EXPECTED = os.path.join(TESTDATA, "liquid_flow_expected.json")
VALVE_EDGE = "m_32_control_valve_1778059294098"


@pytest.fixture(scope="module")
def data():
    with open(FIXTURE) as f:
        return json.load(f)


def edge(frame, edge_id):
    return next(e for e in frame["edges"] if e["edgeId"] == edge_id)


def target(frame, kind, model, equip="", part=""):
    return next(t for t in frame["targets"]
                if (t["kind"], t["modelUid"], t["equipmentUid"], t["liquidPart"]) == (kind, model, equip, part))


def test_edges_follow_the_actual_flow_not_the_modelled_direction(data):
    fr = lf.flow_frame(data, 0)
    valve = edge(fr, VALVE_EDGE)
    i = data["edge_ids"].index(VALVE_EDGE)
    m = data["edge_flow_kg_s"][i][0]
    assert m < 0 and valve["reversed"] is True and valve["flowKgS"] == m
    assert valve["upstream"] == data["edge_to"][i] and valve["downstream"] == data["edge_from"][i]
    assert valve["massFlowKgS"] == -m
    rho = data["fluid"]["rho_kg_m3"]
    assert valve["volumeFlowLpm"] == pytest.approx(-m / rho * 60000, rel=1e-15)
    D = data["edge_D_m"][i]
    assert valve["diameterM"] == D == 0.025
    assert valve["velocityMS"] == pytest.approx(-m / (rho * math.pi * D * D / 4), rel=1e-15)
    pump = edge(fr, "m_30_pump")
    assert pump["reversed"] is False and pump["upstream"] == data["edge_from"][data["edge_ids"].index("m_30_pump")]
    assert pump["velocityMS"] is None and pump["diameterM"] is None


def test_cell_arrows_are_unit_tangents_times_the_flow_sign(data):
    fr = lf.flow_frame(data, 2)
    assert len(fr["cells"]) == len(data["cell_edge_ids"])
    by_edge = {}
    for c, e in enumerate(data["cell_edge_ids"]):
        by_edge.setdefault(e, []).append(c)
    checked = 0
    for e, cells in by_edge.items():
        m = data["edge_flow_kg_s"][data["edge_ids"].index(e)][2]
        for k, c in enumerate(cells):
            cell = fr["cells"][c]
            assert cell["edgeId"] == e and cell["position"] == data["cell_positions"][c]
            d = cell["direction"]
            assert math.isclose(math.hypot(*d), 1.0, rel_tol=1e-12)
            a = data["cell_positions"][cells[max(k - 1, 0)]]
            b = data["cell_positions"][cells[min(k + 1, len(cells) - 1)]]
            t = [b[j] - a[j] for j in range(3)]
            # The arrow points along the pipe in the direction the water moves.
            assert sum(d[j] * t[j] for j in range(3)) * m > 0
            assert cell["massFlowKgS"] == abs(m)
            checked += 1
    assert checked == len(data["cell_edge_ids"])


def test_part_flows_follow_the_temperature_joins(data):
    fr = lf.flow_frame(data, 1)
    flows = {e: data["edge_flow_kg_s"][i][1] for i, e in enumerate(data["edge_ids"])}
    rack = data["object_bindings"]["m_1"]["edge_ids"]
    supply = target(fr, "manifold", "m_1", part="supply-manifold")
    assert supply["massFlowKgS"] == pytest.approx(sum(abs(flows[e]) for e in rack if e.endswith("_branch_in")))
    assert supply["velocityMS"] is None
    # The valve's own edge; the CDU is its supply node's throughput = pump flow.
    assert target(fr, "valve", "m_32", "valve_1778059294098")["massFlowKgS"] == abs(flows[VALVE_EDGE])
    assert target(fr, "cdu", "m_30")["massFlowKgS"] == pytest.approx(abs(flows["m_30_pump"]), rel=1e-12)
    assert target(fr, "pump", "m_30", "m_30_pump")["massFlowKgS"] == abs(flows["m_30_pump"])
    assert target(fr, "hx", "m_30", "m_30_hx")["massFlowKgS"] == abs(flows["m_30_hx"])
    # A tee passes what enters it: inflow equals outflow on a real solution.
    tee = "m_31_tee_tee_1778059219789"
    inflow = outflow = 0.0
    for i, e in enumerate(data["edge_ids"]):
        m = flows[e]
        if data["edge_to"][i] == tee:
            inflow, outflow = inflow + max(m, 0), outflow + max(-m, 0)
        if data["edge_from"][i] == tee:
            inflow, outflow = inflow + max(-m, 0), outflow + max(m, 0)
    assert target(fr, "tee", "m_31", "tee_1778059219789")["massFlowKgS"] == pytest.approx(inflow, rel=1e-15)
    assert inflow == pytest.approx(outflow, rel=1e-12)
    # Servers: their cold-plate flow, joined by cp edge id.
    assert target(fr, "server", "m_1", "e_2")["massFlowKgS"] == abs(flows["m_1_e_2_cp_CP-CU_1"])
    assert target(fr, "server", "m_1", "e_2")["group"] == "servers"
    # Pipes: cell-weighted mean of their edges' |flow|.
    cells = [c for c, e in enumerate(data["cell_edge_ids"]) if e in data["object_bindings"]["m_31"]["edge_ids"]]
    mean = 0.0
    for c in cells:
        mean += abs(flows[data["cell_edge_ids"][c]])
    assert target(fr, "pipe", "m_31")["massFlowKgS"] == pytest.approx(mean / len(cells), rel=1e-12)


def test_pump_slowdown_and_valve_closure_show_in_the_samples(data):
    pump = [target(lf.flow_frame(data, s), "pump", "m_30", "m_30_pump")["massFlowKgS"] for s in range(3)]
    valve = [target(lf.flow_frame(data, s), "valve", "m_32", "valve_1778059294098")["massFlowKgS"] for s in range(3)]
    assert pump[1] < 0.75 * pump[0] and valve[2] < valve[1] < valve[0]
    domains = lf.global_domains(data)
    for s in range(3):
        fr = lf.flow_frame(data, s)
        for group, (lo, hi) in fr["domains"].items():
            assert domains[group][0] <= lo and hi <= domains[group][1]


def test_domains_and_flag_lists(data):
    fr = lf.flow_frame(data, -1)
    assert fr["sample"] == 2 and fr["timeSeconds"] == data["time_s"][2]
    net = [t["massFlowKgS"] for t in fr["targets"] if t["group"] == "network"]
    assert fr["domains"]["network"] == [min(net), max(net)]
    assert fr["domains"]["cells"] == [min(c["massFlowKgS"] for c in fr["cells"]),
                                      max(c["massFlowKgS"] for c in fr["cells"])]
    assert fr["stagnantEdges"] == [] and fr["unobservableNodes"] == []
    with pytest.raises(ValueError):
        lf.flow_frame(data, 3)


def test_stagnant_edges_and_dry_nodes_are_marked_and_carry_no_arrow(data):
    flagged = copy.deepcopy(data)
    i = flagged["edge_ids"].index(VALVE_EDGE)
    flagged["edge_stagnant"][i] = True
    dry = flagged["edge_to"][i]
    flagged["node_observable"][flagged["node_ids"].index(dry)] = [False, False, False]
    fr = lf.flow_frame(flagged, 0)
    valve = edge(fr, VALVE_EDGE)
    assert valve["stagnant"] is True and valve["massFlowKgS"] == 0.0 and valve["velocityMS"] == 0.0
    assert valve["upstream"] is None and valve["downstream"] is None and valve["reversed"] is False
    assert fr["stagnantEdges"] == [VALVE_EDGE] and fr["unobservableNodes"] == [dry]
    pipe_edge = next(e for e in flagged["cell_edge_ids"])
    flagged["edge_stagnant"][flagged["edge_ids"].index(pipe_edge)] = True
    cells = [c for c in lf.flow_frame(flagged, 0)["cells"] if c["edgeId"] == pipe_edge]
    assert cells and all(c["direction"] == [0.0, 0.0, 0.0] and c["stagnant"] and c["massFlowKgS"] == 0.0
                         for c in cells)


def test_results_without_topology_still_give_cell_arrows_and_edge_flows(data):
    old = {k: v for k, v in data.items()
           if k not in ("edge_from", "edge_to", "edge_types", "edge_D_m", "node_positions_m",
                        "node_observable", "edge_stagnant")}
    fr = lf.flow_frame(old, 0)
    assert all(math.isclose(math.hypot(*c["direction"]), 1.0) for c in fr["cells"])
    valve = edge(fr, VALVE_EDGE)
    assert valve["upstream"] is None and valve["reversed"] is True and valve["velocityMS"] is None
    kinds = {t["kind"] for t in fr["targets"]}
    assert "tee" not in kinds and "cdu" not in kinds and "pump" not in kinds  # need topology
    assert {"pipe", "manifold", "valve", "server"} <= kinds


def test_refuses_results_without_flows_or_density():
    with pytest.raises(ValueError, match="edge_flow_kg_s"):
        lf.flow_frame({"time_s": [0.0], "edge_ids": [], "fluid": {"rho_kg_m3": 1000.0}}, 0)
    with pytest.raises(ValueError, match="rho"):
        lf.flow_frame({"time_s": [0.0], "edge_ids": [], "edge_flow_kg_s": [], "fluid": {}}, 0)


def test_expected_contract_file_is_reproduced(data):
    """The shared contract: regenerate with `python liquid_flow.py FIXTURE EXPECTED`
    only on a deliberate rule change, and the React test must follow."""
    with open(EXPECTED) as f:
        expected = json.load(f)
    assert expected["samples"] == [0, 1, 2]
    for sample, exp in expected["frames"].items():
        _assert_close(lf.flow_frame(data, int(sample)), exp, f"frame {sample}")
    _assert_close(lf.global_domains(data), expected["globalDomains"], "globalDomains")


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
