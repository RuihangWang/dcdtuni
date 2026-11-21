"""Full-ring MMS packet with the whole-trajectory order gate registered in GATE.md.

From the repository root, with the dcdtuni environment activated:

    PYTHONPATH=src OMP_NUM_THREADS=4 python docs/benchmarks/liquid-mms-trajectory-gate-2026-10-25/run.py

1. Reads the base input and the independent constructor of the 2026-10-07
   packet (SHA-256 checked against GATE.md) and sets the forcing period to 20 s.
2. For each dt level, builds the manufactured benchmark exactly as that
   packet's generate_manufactured.py does (sources, initial state and rate at
   every sample, from NumPy only) and puts it in a copy of the base input with
   only time.dt_s, time.N_t and manufactured_benchmark changed.
3. Runs the product (DAESimulation, the same object the run sessions use) to
   the horizon and compares every accepted sample with the manufactured state.
4. Writes results.json (errors, orders, gate verdicts, diagnostics) here.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PACKET = HERE.parent / 'liquid-full-mms-asymptotic-20261007'
BASE = PACKET / 'actual-v4/grid-0p03125/results/after-transient-base.liquidrun'
CONSTRUCTOR = PACKET / 'original-coarse/independent_trajectory.py'
SHA = {BASE: '622142abe69c0b42accf2e3c0dc6abeae5d509d15717bdf7b0161e371d46a4d5',
       CONSTRUCTOR: '270f992cd63d2de1688548863f84a10ebfdcc5a4657165c7b16feb3c9db3e7eb'}
PERIOD_S, END_S = 20.0, 8.0
LEVELS = [2.0, 1.0, .5, .25, .125, .0625, .03125]
BAND = (.85, 1.15)
BLOCKS = {  # block: (frame key, manufactured key, id family)
    'node_temperature': ('node_temperature_K', 'node_temperature_K', 'nodes'),
    'water': ('cell_temperature_K', 'water_temperature_K', 'water'),
    'solid': ('chip_temp_K', 'solid_temperature_K', 'solid'),
    'hx_secondary': ('secondary_supply_K', 'hx_secondary_K', 'hx'),
    'hx_primary': ('primary_return_K', 'hx_primary_K', 'hx'),
}

for path, digest in SHA.items():
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'{path} differs from GATE.md'

torch.set_num_threads(4)
sys.path.insert(0, str(CONSTRUCTOR.parent))
from independent_trajectory import IndependentTrajectory  # noqa: E402
from backend.liquidcooling.dae_runtime import DAESimulation, admit_dae_runtime_input  # noqa: E402
from backend.liquidcooling.manufactured_benchmark import canonical_digest, compiled_ids, graph_layout  # noqa: E402

base = json.loads(BASE.read_text())
definition = admit_dae_runtime_input(base)
eq = definition.equation_graph
ids = compiled_ids(eq)
reference = IndependentTrajectory(eq, definition.parameters(0))
reference.omega = 2 * math.pi / PERIOD_S          # the one change (GATE.md)
IMAP = {'node_pressure_Pa': 'nodes', 'flow_kg_s': 'edges', 'node_temperature_K': 'nodes',
        'water_temperature_K': 'water', 'solid_temperature_K': 'solid', 'hx_secondary_K': 'hx', 'hx_primary_K': 'hx'}
SMAP = {'momentum_Pa': 'edges', 'water_W': 'water', 'solid_W': 'solid', 'hx_secondary_W': 'hx', 'hx_primary_W': 'hx'}


def level_input(dt):
    times = [i * dt for i in range(round(END_S / dt) + 1)]
    targets = [reference.evaluate(t) for t in times]
    state, rate = targets[0][0], targets[0][1]
    vector = lambda name, value: {'ids': ids[IMAP[name]], 'values': value.tolist()}
    benchmark = {
        'format': 'dcdtuni-manufactured-benchmark', 'version': 1, 'provenance': 'synthetic-research',
        'formula_id': 'cycle-storage-sinusoidal-20261006-v1',
        'source_reference': 'Independent NumPy constructive cycle basis, component laws, storage trajectories and node '
                            'mixing (2026-10-07 packet constructor, forcing period 20 s); no product residual or normal '
                            'DAE output used to manufacture forcing.',
        'graph_layout_sha256': canonical_digest(graph_layout(base)),
        'units': {'pressure': 'Pa', 'temperature': 'K', 'mass_flow': 'kg/s', 'power': 'W', 'time': 's',
                  'temperature_rate': 'K/s'},
        'sampling': 'endpoint_hold', 'times_s': times,
        'sources': {name: {'ids': ids[key], 'values': np.stack([v[2][name] for v in targets], axis=1).tolist()}
                    for name, key in SMAP.items()},
        'initial_state': {name: vector(name, value) for name, value in state.items()},
        'initial_temperature_rate': {name: vector(name, value) for name, value in rate.items()},
        'flow_signs': {'ids': ids['edges'], 'values': np.sign(state['flow_kg_s']).tolist()},
    }
    benchmark['source_sha256'] = canonical_digest(benchmark)
    raw = deepcopy(base)
    raw['time']['dt_s'] = dt
    raw['time']['N_t'] = len(times)
    raw['manufactured_benchmark'] = benchmark
    return raw, times, targets


results = {'gate': 'GATE.md', 'period_s': PERIOD_S, 'end_s': END_S, 'levels': {}}
for dt in LEVELS:
    raw, times, targets = level_input(dt)
    started = time.perf_counter()
    simulation = DAESimulation(raw)
    while not simulation.done:
        simulation.step()
    elapsed = time.perf_counter() - started
    frames = simulation.frames
    assert [round(f['time_s'] / dt) for f in frames] == list(range(len(times)))
    level = {'samples': len(times), 'wall_s': elapsed,
             'max_scaled_residual': max(f['newton']['scaled_residual_inf'] for f in frames),
             'max_energy_balance_W': max(abs(f['diagnostics']['energy_balance_W']) for f in frames),
             'trajectory_inf': {}, 'final_inf': {}}
    for block, (frame_key, made_key, _) in BLOCKS.items():
        product = np.array([f[frame_key] for f in frames], dtype=float)
        made = np.array([t[0][made_key] for t in targets], dtype=float)
        error = np.abs(product - made)
        if block == 'node_temperature':
            error = np.where(np.array([f['node_observable'] for f in frames]), error, 0.)
        level['trajectory_inf'][block] = float(error.max())
        level['final_inf'][block] = float(error[-1].max())
        level.setdefault('initial_inf', {})[block] = float(error[0].max())
    for name, key in (('pressure', 'node_pressure_Pa'), ('flow', 'edge_flow_kg_s')):
        made_key = {'pressure': 'node_pressure_Pa', 'flow': 'flow_kg_s'}[name]
        product = np.array([f[key] for f in frames], dtype=float)
        made = np.array([t[0][made_key] for t in targets], dtype=float)
        level.setdefault('hydraulic_inf', {})[name] = float(np.abs(product - made).max())
    results['levels'][str(dt)] = level
    print(f'dt={dt}: {elapsed:.1f} s, trajectory {level["trajectory_inf"]}', flush=True)

orders, final_orders, verdict = {}, {}, {}
for block in BLOCKS:
    errs = [results['levels'][str(dt)]['trajectory_inf'][block] for dt in LEVELS]
    fin = [results['levels'][str(dt)]['final_inf'][block] for dt in LEVELS]
    orders[block] = [math.log2(a / b) for a, b in zip(errs, errs[1:])]
    final_orders[block] = [math.log2(a / b) for a, b in zip(fin, fin[1:])]
    verdict[block] = all(BAND[0] <= p <= BAND[1] for p in orders[block])
    # Richardson ratio of the final-time error from the three finest levels.
    e1, e2, e3 = fin[-3:]
    h = LEVELS[-1]
    # e(dt) = a dt + b dt^2 at dt = 4h, 2h, h  ->  least squares
    A = np.array([[4 * h, 16 * h * h], [2 * h, 4 * h * h], [h, h * h]])
    a, b = np.linalg.lstsq(A, np.array([e1, e2, e3]), rcond=None)[0]
    results.setdefault('richardson', {})[block] = {'r_per_s': float(b / a) if a else None,
                                                   'r_dt': [abs(b / a) * dt if a else None for dt in LEVELS]}
g2 = all(level['max_scaled_residual'] <= base['newton']['atol'] and level['max_energy_balance_W'] <= 1e-6
         for level in results['levels'].values())
results.update(trajectory_orders=orders, final_time_orders=final_orders, G1=verdict, G1_pass=all(verdict.values()),
               G2_pass=g2)
(HERE / 'results.json').write_text(json.dumps(results, indent=1) + '\n')
print(json.dumps({'G1': verdict, 'G2': g2, 'orders': orders}, indent=1))
