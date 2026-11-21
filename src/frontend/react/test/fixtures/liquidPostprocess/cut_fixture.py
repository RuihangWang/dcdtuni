"""Cut the liquid post-processing fixtures from real Shinaide DAE results.

    python cut_fixture.py transient <run-b9bca1f1…/result.json> <out.json>
    python cut_fixture.py steady    <run-b5f84495…/result.json> <out.json>

transient: run b9bca1f1 (pump speed 1.0 -> 0.7 at t=8 s; valve
m_32_control_valve_1778059294098 opening 0.3 and heat x1.2 at t=14 s) at
samples 0, 5 and 10. Keeps CDU m_30 (pump, HX, both feeds),
rack m_1 complete (4 servers, both headers), its supply spoke and its return
spoke through flow valve 1778059337713 (modelled ring -> rack, solved rack ->
ring, i.e. reversed). Every node listed in CLOSED_NODES keeps every incident
edge, so the mass balance closes there; other kept nodes keep only the kept
edges.

steady: run b5f84495 (one accepted sample, written before results carried
their topology) with the same CDU and rack m_1 edges that exist in it.

Every kept array is re-indexed consistently and every value is copied
verbatim; nothing is recomputed. System-wide quantities (diagnostics,
controls, provenance) are copied whole for the kept samples.
"""
from __future__ import annotations

import json
import sys

RACK = 'm_1'
CDU = 'm_30'
VALVE = 'm_32/valve_1778059337713'
CLOSED_NODES = ('m_30_supply', 'm_30_return', 'm_30_pump_discharge',
                'm_1_mf_supply_bottom', 'm_1_mf_return_bottom', 'm_1_mf_supply', 'm_1_mf_return',
                'm_32_control_valve_1778059337713_in', 'm_32_control_valve_1778059337713_out')
EXTRA_BINDINGS = ('m_31', 'm_32', 'cdu_supply_feed', 'cdu_return_feed', 'ring_supply_feed_tee', 'ring_return_feed_tee')


def cut(full: dict, samples: tuple[int, ...]) -> dict:
    bindings = full['object_bindings']
    edge_index = {e: i for i, e in enumerate(full['edge_ids'])}
    has_topology = 'edge_from' in full
    edges: list[str] = []

    def add(edge):
        if edge in edge_index and edge not in edges:
            edges.append(edge)

    for key in (CDU, RACK, VALVE):
        for edge in bindings.get(key, {}).get('edge_ids', []):
            add(edge)
    if has_topology:
        for node in CLOSED_NODES:
            for i, e in enumerate(full['edge_ids']):
                if full['edge_from'][i] == node or full['edge_to'][i] == node:
                    add(e)
    else:
        for e in ('cdu_supply_feed', 'cdu_return_feed'):
            add(e)
    edges.sort(key=edge_index.__getitem__)
    e_idx = [edge_index[e] for e in edges]
    kept = set(edges)
    node_index = {n: i for i, n in enumerate(full['node_ids'])}
    if has_topology:
        nodes = []
        for i in e_idx:
            for n in (full['edge_from'][i], full['edge_to'][i]):
                if n not in nodes:
                    nodes.append(n)
    else:
        nodes = [n for n in full['node_ids'] if n.startswith(f'{CDU}_') or n.startswith(f'{RACK}_')]
    nodes.sort(key=node_index.__getitem__)
    n_idx = [node_index[n] for n in nodes]
    c_idx = [c for c, e in enumerate(full['cell_edge_ids']) if e in kept]
    cp_idx = [k for k, d in enumerate(full['cp_details']) if d['edge_id'] in kept]
    hx_idx = [k for k, e in enumerate(full['hx_edge_ids']) if e in kept]

    def rows(matrix, index):
        return [[matrix[i][s] for s in samples] for i in index]

    def pick(series):
        return [series[s] for s in samples]

    out_bindings = {}
    for key, b in bindings.items():
        keep_key = key in (CDU, RACK, VALVE) or key in EXTRA_BINDINGS or key.startswith(f'{RACK}/')
        if not keep_key:
            continue
        b = json.loads(json.dumps(b))
        b['edge_ids'] = [e for e in b['edge_ids'] if e in kept]
        b['node_ids'] = [n for n in b['node_ids'] if n in nodes]
        out_bindings[key] = b
    racks = []
    for rack in full['rack_summaries']:
        if rack['rack_id'] != RACK:
            continue
        rack = json.loads(json.dumps(rack))
        for obj in (rack, *rack['servers']):
            for key, value in list(obj.items()):
                if isinstance(value, list) and len(value) == len(full['time_s']) and key != 'servers':
                    obj[key] = pick(value)
        racks.append(rack)
    details = []
    for k in cp_idx:
        d = json.loads(json.dumps(full['cp_details'][k]))
        for key, value in list(d.items()):
            if isinstance(value, list):
                d[key] = pick(value)
        details.append(d)
    out = {key: full[key] for key in ('run_id', 'config_hash', 'solver', 'status', 'completed', 'converged',
                                      'final_loss', 'epochs', 'fluid', 'parameter_provenance', 'error')
           if key in full}
    if 'analysis' in full:
        out['analysis'] = full['analysis']
    if 'expected_duration_s' in full:
        out['expected_duration_s'] = full['expected_duration_s']
    out.update({
        'time_s': pick(full['time_s']),
        'heat_multipliers': pick(full['heat_multipliers']),
        'controls': pick(full['controls']),
        'diagnostics': pick(full['diagnostics']),
        'pump_rpm_ratios': pick(full['pump_rpm_ratios']),
        'valve_openings': pick(full['valve_openings']),
        'edge_ids': edges,
        'edge_flow_kg_s': rows(full['edge_flow_kg_s'], e_idx),
        'edge_outlet_temp_K': rows(full['edge_outlet_temp_K'], e_idx),
        'node_ids': nodes,
        'node_temperature_K': rows(full['node_temperature_K'], n_idx),
        'node_pressure_Pa': rows(full['node_pressure_Pa'], n_idx),
        'cell_edge_ids': [full['cell_edge_ids'][c] for c in c_idx],
        'cell_positions': [full['cell_positions'][c] for c in c_idx],
        'cell_temp_K': rows(full['cell_temp_K'], c_idx),
        'cp_details': details,
        'chip_temp_K': rows(full['chip_temp_K'], cp_idx),
        'water_outlet_temp_K': rows(full['water_outlet_temp_K'], cp_idx),
        'hx_edge_ids': [full['hx_edge_ids'][k] for k in hx_idx],
        'primary_return_K': rows(full['primary_return_K'], hx_idx),
        'secondary_supply_K': rows(full['secondary_supply_K'], hx_idx),
        'object_bindings': out_bindings,
        'rack_summaries': racks,
    })
    if 'primary_inlet_K' in full:
        out['primary_inlet_K'] = pick(full['primary_inlet_K'])
    if has_topology:
        out.update({
            'edge_from': [full['edge_from'][i] for i in e_idx],
            'edge_to': [full['edge_to'][i] for i in e_idx],
            'edge_types': [full['edge_types'][i] for i in e_idx],
            'edge_D_m': [full['edge_D_m'][i] for i in e_idx],
            'edge_stagnant': [full['edge_stagnant'][i] for i in e_idx],
            'node_positions_m': [full['node_positions_m'][i] for i in n_idx],
            'node_observable': rows(full['node_observable'], n_idx),
        })
    return out


if __name__ == '__main__':
    mode, source, target = sys.argv[1:4]
    with open(source) as f:
        full = json.load(f)
    samples = (0, 5, 10) if mode == 'transient' else (0,)
    fixture = cut(full, samples)
    fixture['note'] = (f"Cut of real Shinaide DAE run {full['run_id']} at samples {list(samples)} "
                       f"by cut_fixture.py ({mode}); values copied verbatim from its result.json.")
    with open(target, 'w') as f:
        json.dump(fixture, f)
        f.write('\n')
    print(f"{len(fixture['edge_ids'])} edges, {len(fixture['node_ids'])} nodes, "
          f"{len(fixture['cell_edge_ids'])} cells, {len(fixture['cp_details'])} plates -> {target}")
