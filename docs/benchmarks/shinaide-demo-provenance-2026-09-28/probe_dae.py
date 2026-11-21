"""Stage 4 demo-parameter probe: measure the primary-return temperature rise of
the Shinaide DAE case under a load step, exactly the way the React buildExperiment
compiles it (per-cold-plate Q_W events). Read-only physics probe; no repo writes."""
import json, sys
import numpy as np
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.dae_runtime import DAESimulation

path, factor, step_at = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
supply_step_K = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
supply_step_at = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
raw = json.load(open(path))
if not raw.get('events'):
    for edge in raw['edges']:
        if edge['type'] == 'cold_plate':
            raw['events'].append({'time_s': step_at, 'target': edge['id'], 'field': 'Q_W',
                                  'value': float(edge['params']['Q_W']) * factor})
load_dae_input(raw)
sim = DAESimulation(raw)

def row(f):
    diag = f.get('diagnostics') or {}
    return [f['time_s'], f['primary_return_K'][0], f['secondary_supply_K'][0],
            max(f['chip_temp_K']), float(diag.get('energy_balance_W', 0.0))]

rows = [row(sim.latest_frame)]
supply_done = False
while not sim.done:
    if supply_step_K and not supply_done and rows[-1][0] >= supply_step_at:
        hx = sim.metadata['hx_edge_ids']
        sim.set_inputs(primary_inlet_K={hx[0]: 293.15 + supply_step_K})
        supply_done = True
    rows.append(row(sim.step()))
arr = np.array(rows)
out = {
    'factor': factor, 'step_at': step_at,
    'supply_step_K': supply_step_K, 'supply_step_at': supply_step_at,
    'n_frames': len(rows),
    't': arr[:, 0].tolist(),
    'primary_return_K': arr[:, 1].tolist(),
    'secondary_supply_K': arr[:, 2].tolist(),
    'chip_max_K': arr[:, 3].tolist(),
    'energy_balance_max_abs_W': float(np.abs(arr[:, 4]).max()),
    'T_pr_initial_K': float(arr[0, 1]), 'T_pr_final_K': float(arr[-1, 1]),
    'dT_primary_return_K': float(arr[-1, 1] - arr[0, 1]),
    'dT_chip_max_K': float(arr[-1, 3] - arr[0, 3]),
}
print(json.dumps(out, indent=1))
