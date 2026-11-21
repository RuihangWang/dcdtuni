"""Small, fully specified .liquidrun DAE loops for the 2026-10 correctness fixes.

One CDU-like loop: the return node is the single pressure datum, the pump lifts
the coolant into the HX secondary, a supply pipe feeds one rack branch of cold
plates in series, and a return pipe closes the loop. Every value is written
here explicitly (no parameter pack), so a test can derive its expected answer
from these numbers alone.
"""
from __future__ import annotations

import copy

RHO, CP, MU = 1000., 4000., 1e-3


def loop_manifest(*, plates=(300.,), rpm=1., hx_model=None, hx_mode=None, T_set_K=None,
                  UA_hx=800., primary_flow=.3, primary_inlet=293.15, n_t=1, dt=1.,
                  analysis=None, version=3, pump_rise_m=0., extra_branch=None,
                  valve_opening=None, ua_plate=60., sub_heat=200.):
    """Return (raw manifest dict). Plates are in series on one server branch.

    pump_rise_m lifts every node except the return datum by that height: the
    pump edge rises (a non-pipe edge with no waypoints) and the return pipe
    descends by the same amount, so the loop's elevations close exactly.
    extra_branch: None | 'parallel' adds a second one-plate rack branch whose
    return carries a control valve with the given opening; 'isolated_loop'
    adds a sub-rack of two PARALLEL plates (sub_cpA, sub_cpB, sub_heat W each)
    fed by sub_pipe and returned through sub_valve.
    """
    z = pump_rise_m
    nodes = [
        {'id': 'ret', 'type': 'pressure_bc', 'position_m': [0., 0., 0.], 'bc': {'P_Pa': 150000.}},
        {'id': 'dis', 'type': 'junction', 'position_m': [0., z, 0.]},
        {'id': 'sup', 'type': 'junction', 'position_m': [0., z, 1.]},
        {'id': 'mf_supply', 'type': 'junction', 'position_m': [2., z, 1.]},
        {'id': 'mf_return', 'type': 'junction', 'position_m': [2., z, 0.]},
    ]
    edges = [
        {'id': 'pump', 'type': 'pump', 'from': 'ret', 'to': 'dis',
         'params': {'H0_m': 8., 'a_coef': 4e7, 'rpm_ratio': rpm}},
        {'id': 'hx', 'type': 'heat_exchanger', 'from': 'dis', 'to': 'sup',
         'params': {'dp_a': 2e5, 'dp_b': 1e3, 'UA_W_per_K': UA_hx}},
        {'id': 'supply_pipe', 'type': 'pipe', 'from': 'sup', 'to': 'mf_supply',
         'params': {'D_m': .02, 'roughness_m': 1.5e-6, 'n_cells': 3,
                    'waypoints_m': [[0., z, 1.], [2., z, 1.]], 'K_minor_per_bend': .5}},
        {'id': 'return_pipe', 'type': 'pipe', 'from': 'mf_return', 'to': 'ret',
         'params': {'D_m': .02, 'roughness_m': 1.5e-6, 'n_cells': 3,
                    'waypoints_m': [[2., z, 0.], [0., 0., 0.]], 'K_minor_per_bend': .5}},
    ]
    if hx_model is not None:
        edges[1]['params']['hxModel'] = hx_model
    if hx_mode is not None:
        edges[1]['params']['mode'] = hx_mode
        edges[1]['params']['T_set_K'] = T_set_K
    servers = []

    def branch(prefix, heats, inlet, outlet, valve=None):
        previous = inlet
        pairs = []
        for i, q in enumerate(heats):
            last = i == len(heats) - 1
            nxt = outlet if last and valve is None else f'{prefix}_cp{i}_out'
            if nxt != outlet:
                nodes.append({'id': nxt, 'type': 'junction', 'position_m': [2., z, .5]})
            edge_id = f'{prefix}_cp{i}'
            edges.append({'id': edge_id, 'type': 'cold_plate', 'from': previous, 'to': nxt,
                          'params': {'dp_a': 1e6, 'dp_b': 4e3, 'UA_W_per_K': ua_plate,
                                     'C_solid_J_per_K': 200., 'V_fluid_m3': 5e-6, 'Q_W': q}})
            pairs.append({'id': f'pair{i}', 'edge_id': edge_id, 'Q_W': q})
            previous = nxt
        if valve is not None:
            edges.append({'id': f'{prefix}_valve', 'type': 'valve', 'from': previous, 'to': outlet,
                          'params': {'D_m': .015, 'Kv_max': 2.5, 'opening': valve,
                                     'characteristic': 'linear'}})
        servers.append({'id': prefix, 'cp_cu_pairs': pairs})

    branch('srv_a', list(plates), 'mf_supply', 'mf_return')
    if extra_branch == 'parallel':
        branch('srv_b', [150.], 'mf_supply', 'mf_return',
               valve=1. if valve_opening is None else valve_opening)
    elif extra_branch == 'isolated_loop':
        # A sub-rack whose two plates are in PARALLEL behind one return valve:
        # closing the valve leaves a stagnant region that is not a tree.
        nodes.extend([{'id': 'sub_in', 'type': 'junction', 'position_m': [3., z, 1.]},
                      {'id': 'sub_out', 'type': 'junction', 'position_m': [3., z, 0.]}])
        edges.append({'id': 'sub_pipe', 'type': 'pipe', 'from': 'mf_supply', 'to': 'sub_in',
                      'params': {'D_m': .015, 'roughness_m': 1.5e-6, 'n_cells': 2,
                                 'waypoints_m': [[2., z, 1.], [3., z, 1.]], 'K_minor_per_bend': .5}})
        for name in ('sub_cpA', 'sub_cpB'):
            edges.append({'id': name, 'type': 'cold_plate', 'from': 'sub_in', 'to': 'sub_out',
                          'params': {'dp_a': 1e6, 'dp_b': 4e3, 'UA_W_per_K': ua_plate,
                                     'C_solid_J_per_K': 200., 'V_fluid_m3': 5e-6, 'Q_W': sub_heat}})
            servers.append({'id': name.replace('sub_cp', 'srv_'), 'cp_cu_pairs': [{'id': 'p', 'edge_id': name, 'Q_W': sub_heat}]})
        edges.append({'id': 'sub_valve', 'type': 'valve', 'from': 'sub_out', 'to': 'mf_return',
                      'params': {'D_m': .015, 'Kv_max': 2.5, 'opening': 1. if valve_opening is None else valve_opening,
                                 'characteristic': 'linear'}})
    raw = {
        'format': 'liquidrun', 'schemaVersion': 3, 'version': '3.0',
        'equation_system': 'incompressible_thermal_dae',
        'source': 'unit-test', 'projectName': 'CorrectnessLoop',
        'source_project': {'format': 'cfdproj', 'schema_version': 4,
                           'project_uid': 'correctness-loop', 'project_name': 'CorrectnessLoop'},
        'units': {'length': 'm', 'time': 's', 'temperature': 'K', 'pressure': 'Pa',
                  'mass_flow': 'kg/s', 'power': 'W'},
        'execution': {'backend': 'pytorch', 'device': 'cpu', 'dtype': 'float64', 'max_threads': 1},
        'gravity': [0., -9.81, 0.],
        'fluid': {'rho_kg_m3': RHO, 'cp_J_kgK': CP, 'mu_Pa_s': MU, 'k_W_mK': .6},
        'time': {'dt_s': dt, 'N_t': n_t, 'bdf_order': 1},
        'nodes': nodes, 'edges': edges,
        'racks': [{'id': 'rack', 'servers': servers}],
        'events': [],
        'initial_conditions': {'T_fluid_K': 300., 'T_solid_K': 310., 'P_uniform_Pa': 150000.},
        'object_bindings': {},
        'controllers': [],
        'analysis': analysis or ({'mode': 'steady', 'initialization': 'steady'} if n_t == 1
                                 else {'mode': 'transient', 'initialization': 'steady'}),
        'newton': {'max_iterations': 40, 'atol': 1e-10, 'rtol': 1e-10, 'max_backtracks': 18},
        'primary_circuits': [{
            'edge_id': 'hx', 'flow_kg_s': primary_flow, 'inlet_K': primary_inlet,
            'cp_J_kgK': 4180., 'rho_kg_m3': 998., 'primary_volume_m3': .01,
            'secondary_volume_m3': .005, 'source': 'mock',
            'source_reference': 'unit-test loop; every value is stated in dae_loop_fixture.py'}],
    }
    if version == 4:
        raw.update(schemaVersion=4, version='4.0')
        raw['source_project']['schema_version'] = 5
        raw['transient'] = {'durationS': max(dt * (n_t - 1), dt), 'start': {'kind': 'steadyConverged'},
                            'tracks': []}
    return copy.deepcopy(raw)
