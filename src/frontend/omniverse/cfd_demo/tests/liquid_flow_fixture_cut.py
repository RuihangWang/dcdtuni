"""Cut the shared liquid-flow fixture from a real DAE result.json.

    python liquid_flow_fixture_cut.py <result.json> <out fixture.json>

Keeps, at samples 0, 5 and 10, a small closed neighbourhood of the network:
CDU m_30 (pump, HX and the supply feed, so node m_30_supply is closed), tee
m_31_tee_tee_1778059219789 with all three of its edges, the reversed flow
valve m_32/valve_1778059294098 with the pipes on both sides, and rack m_1
with servers e_1 and e_2 and both manifold headers. Bindings keep only the
kept edges and nodes. Other tee nodes reached by a kept edge keep only that
edge; only the CLOSED_NODES are mass-closed. Arrays are re-indexed
consistently; no value is recomputed.
"""
from __future__ import annotations

import json
import sys

SAMPLES = (0, 5, 10)
VALVE = 'm_32/valve_1778059294098'
BINDINGS = ('m_30', 'm_31', 'm_32', 'm_1', 'm_1/e_1', 'm_1/e_2', VALVE)
# Nodes whose every edge is kept: a tee, the CDU supply, both valve ends.
CLOSED_NODES = ('m_31_tee_tee_1778059219789', 'm_30_supply',
                'm_32_control_valve_1778059294098_in', 'm_32_control_valve_1778059294098_out')
SERVERS = ('e_1', 'e_2')


def cut(full: dict) -> dict:
    bindings = full['object_bindings']
    edge_index = {e: i for i, e in enumerate(full['edge_ids'])}
    edges: list[str] = []

    def add(edge):
        if edge in edge_index and edge not in edges:
            edges.append(edge)

    for edge in bindings['m_30']['edge_ids'] + bindings[VALVE]['edge_ids']:
        add(edge)
    for edge in bindings['m_1']['edge_ids']:
        if edge.endswith('_header') or any(edge.startswith(f'm_1_{s}_') for s in SERVERS):
            add(edge)
    for node in CLOSED_NODES:
        for i, e in enumerate(full['edge_ids']):
            if full['edge_from'][i] == node or full['edge_to'][i] == node:
                add(e)
    edges.sort(key=edge_index.__getitem__)
    e_idx = [edge_index[e] for e in edges]
    nodes = []
    for i in e_idx:
        for n in (full['edge_from'][i], full['edge_to'][i]):
            if n not in nodes:
                nodes.append(n)
    node_index = {n: i for i, n in enumerate(full['node_ids'])}
    nodes.sort(key=node_index.__getitem__)
    n_idx = [node_index[n] for n in nodes]
    kept = set(edges)
    c_idx = [c for c, e in enumerate(full['cell_edge_ids']) if e in kept]
    cp_idx = [k for k, d in enumerate(full['cp_details']) if d['edge_id'] in kept]
    hx_idx = [k for k, e in enumerate(full['hx_edge_ids']) if e in kept]

    def rows(matrix, index):
        return [[matrix[i][s] for s in SAMPLES] for i in index]

    out_bindings = {}
    for key in BINDINGS:
        b = json.loads(json.dumps(bindings[key]))
        b['edge_ids'] = [e for e in b['edge_ids'] if e in kept]
        b['node_ids'] = [n for n in b['node_ids'] if n in nodes]
        out_bindings[key] = b
    racks = []
    for rack in full['rack_summaries']:
        if rack['rack_id'] != 'm_1':
            continue
        rack = json.loads(json.dumps(rack))
        rack['servers'] = [s for s in rack['servers'] if s['server_id'] in SERVERS]
        for server in rack['servers']:
            for key, value in list(server.items()):
                if isinstance(value, list) and len(value) == len(full['time_s']):
                    server[key] = [value[s] for s in SAMPLES]
        racks.append(rack)
    details = []
    for k in cp_idx:
        d = json.loads(json.dumps(full['cp_details'][k]))
        for key, value in list(d.items()):
            if isinstance(value, list):
                d[key] = [value[s] for s in SAMPLES]
        details.append(d)
    return {
        'run_id': full['run_id'], 'config_hash': full['config_hash'], 'solver': full['solver'],
        'status': full['status'], 'fluid': full['fluid'],
        'time_s': [full['time_s'][s] for s in SAMPLES],
        'heat_multipliers': [full['heat_multipliers'][s] for s in SAMPLES],
        'controls': [full['controls'][s] for s in SAMPLES],
        'edge_ids': edges,
        'edge_from': [full['edge_from'][i] for i in e_idx],
        'edge_to': [full['edge_to'][i] for i in e_idx],
        'edge_types': [full['edge_types'][i] for i in e_idx],
        'edge_D_m': [full['edge_D_m'][i] for i in e_idx],
        'edge_stagnant': [full['edge_stagnant'][i] for i in e_idx],
        'edge_flow_kg_s': rows(full['edge_flow_kg_s'], e_idx),
        'edge_outlet_temp_K': rows(full['edge_outlet_temp_K'], e_idx),
        'node_ids': nodes,
        'node_positions_m': [full['node_positions_m'][i] for i in n_idx],
        'node_temperature_K': rows(full['node_temperature_K'], n_idx),
        'node_pressure_Pa': rows(full['node_pressure_Pa'], n_idx),
        'node_observable': rows(full['node_observable'], n_idx),
        'cell_edge_ids': [full['cell_edge_ids'][c] for c in c_idx],
        'cell_positions': [full['cell_positions'][c] for c in c_idx],
        'cell_temp_K': rows(full['cell_temp_K'], c_idx),
        'cp_details': details,
        'chip_temp_K': rows(full['chip_temp_K'], cp_idx),
        'hx_edge_ids': [full['hx_edge_ids'][k] for k in hx_idx],
        'primary_return_K': rows(full['primary_return_K'], hx_idx),
        'secondary_supply_K': rows(full['secondary_supply_K'], hx_idx),
        'object_bindings': out_bindings,
        'rack_summaries': racks,
    }


if __name__ == '__main__':
    with open(sys.argv[1]) as f:
        full = json.load(f)
    fixture = cut(full)
    fixture['note'] = (f"Cut of real Shinaide DAE run {full['run_id']} (transient, pump 1.0 -> 0.7 after "
                       "sample 3, valve m_32_control_valve_1778059294098 0.3 and heat x1.2 after sample 6) "
                       f"at samples {list(SAMPLES)}, by liquid_flow_fixture_cut.py.")
    with open(sys.argv[2], 'w') as f:
        json.dump(fixture, f)
        f.write('\n')
    print(f"{len(fixture['edge_ids'])} edges, {len(fixture['node_ids'])} nodes, "
          f"{len(fixture['cell_edge_ids'])} cells -> {sys.argv[2]}")
