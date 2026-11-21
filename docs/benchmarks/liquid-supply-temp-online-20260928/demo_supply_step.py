"""Stage 1 receipt demo: 12-frame transient with an online supply-temperature
step at frame 6 (290 -> 295 K). Isolated CPU run, no server, no GPU.
Records per-frame accepted inlet + primary return, applied_inputs, and the
executed-manifest trace; asserts the return temperature responds upward.
"""
import hashlib, json, pathlib, time
import torch
from backend.liquidcooling.dae_runtime import DAESimulation
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest

raw = manifest()
raw['events'] = []
N = 13
raw['time']['N_t'] = N

def stretch(container):
    for key, val in list(container.items()):
        if isinstance(val, list) and len(val) == 4 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in val):
            container[key] = val + [val[-1]] * (N - 4)

for edge in raw['edges']:
    stretch(edge.get('params', {}))
for node in raw.get('nodes', []):
    stretch(node.get('bc', {}))
raw['primary_circuits'][0]['flow_kg_s'] = [1.0] * N
raw['primary_circuits'][0]['inlet_K'] = [290.0] * N

sim = DAESimulation(raw)
frames = []
t0 = time.perf_counter()
for step in range(1, 13):
    if step == 6:
        sim.set_inputs(primary_inlet_K={'cdu_hx': 295.0})
    f = sim.step()
    frames.append({'seq': f['seq'], 'time_s': f['time_s'],
                   'accepted_inlet_K': f['controls']['primary_inlet_K']['cdu_hx'],
                   'primary_return_K': f['primary_return_K'][0],
                   'newton_residual': f['newton']['scaled_residual_inf'],
                   'energy_balance_W': f['diagnostics']['energy_balance_W']})
wall = time.perf_counter() - t0
executed = sim.executed_manifest()
pre = frames[4]['primary_return_K']
post = frames[-1]['primary_return_K']
repo = pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
out = {
    'demo': 'stage1-online-supply-temp-12frame',
    'step_command_at_seq': 6, 'command_K': 295.0, 'planned_K': 290.0,
    'primary_return_pre_K': pre, 'primary_return_post_K': post,
    'response_upward': post > pre,
    'response_magnitude_K': post - pre,
    'max_newton_residual': max(f['newton_residual'] for f in frames),
    'max_energy_balance_W': max(abs(f['energy_balance_W']) for f in frames),
    'wall_seconds': round(wall, 3),
    'applied_inputs': sim.experiment.applied_inputs,
    'executed_inlet_trace': executed['primary_circuits'][0]['inlet_K'],
    'executed_source': executed['source'],
    'executed_N_t': executed['time']['N_t'],
    'frames': frames,
    'source_sha256': {n: hashlib.sha256((repo / p).read_bytes()).hexdigest() for n, p in {
        'dae_experiment.py': 'src/backend/liquidcooling/dae_experiment.py',
        'dae_runtime.py': 'src/backend/liquidcooling/dae_runtime.py',
        'accepted_run_input.py': 'src/backend/liquidcooling/accepted_run_input.py',
    }.items()},
    'execution': {'device': 'cpu', 'threads': torch.get_num_threads(), 'gpu': 'none'},
}
assert out['response_upward'], 'supply-temp step did not raise primary return'
assert out['executed_inlet_trace'] == [290.0] * 6 + [295.0] * 7, out['executed_inlet_trace']
assert out['executed_N_t'] == 13
pathlib.Path(__file__).resolve().parent.joinpath('demo_12frame.json').write_text(json.dumps(out, indent=2))
print(json.dumps({k: v for k, v in out.items() if k not in ('frames', 'source_sha256')}, indent=2))
