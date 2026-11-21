"""Replay actual archived browser trajectories from executed.liquidrun alone.

CPU only, one core. The original project/input/checkpoint/result stay read-only.
The checkpoint is used once to construct the accepted input artifact; the
standalone solve receives only that v3 file, with no live-command replay API.
"""
import hashlib,json,os,signal,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']='';os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))

def deadline(*_):raise TimeoutError('90-second accepted input replay budget exhausted')
signal.signal(signal.SIGALRM,deadline);signal.alarm(90)
import numpy as np
import torch
torch.set_num_threads(1);torch.set_num_interop_threads(1)
from backend.liquidcooling.accepted_run_input import build_accepted_manifest
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.runner import run_liquid_solve
cases=[('nv',root/'case/NV_PEK02_Mock/Liquid/log/run-a6dcf80290bf4aa8ae4fda622e6ee585'),
       ('shinaide',root/'case/Shinaide_Liquid/Liquid/log/run-737a0ecc25e342acb7932356222e566f')]
started=time.perf_counter();reports=[]
fields=('node_pressure_Pa','edge_flow_kg_s','node_temperature_K','chip_temp_K','cell_temp_K','primary_return_K','secondary_supply_K','edge_outlet_temp_K')
for name,folder in cases:
    begin=time.perf_counter()
    checkpoint_file=folder/'checkpoint.json';result_file=folder/'result.json'
    originals={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in (checkpoint_file,result_file,folder/'input.liquidrun')}
    checkpoint=json.loads(checkpoint_file.read_text());expected=json.loads(result_file.read_text())
    definition=load_dae_input(checkpoint['liquidrun'])
    executed=build_accepted_manifest(definition,checkpoint['heat_multipliers'])
    file=folder/'executed.liquidrun'
    if file.exists():
        if json.loads(file.read_text())!=executed:raise ValueError('existing accepted input artifact has different data')
    else:file.write_text(json.dumps(executed,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    replay_dir=folder.parent/f'accepted-replay-{name}-20260926'
    result=run_liquid_solve(file,case_dir=replay_dir)
    files=sorted((replay_dir/'log').glob('run-*/result.json'),key=lambda p:p.stat().st_mtime_ns)
    actual=json.loads(files[-1].read_text())
    checks={}
    for key in fields:
        a=np.asarray(actual[key],dtype=float);b=np.asarray(expected[key],dtype=float)
        if a.shape!=b.shape:raise RuntimeError(f'{name} {key} changed sample/component count')
        difference=float(np.abs(a-b).max()) if a.size else 0.
        checks[key]={'max_abs_difference':difference,'exactly_equal':actual[key]==expected[key]}
        if not np.allclose(a,b,atol=1e-8,rtol=1e-12):raise RuntimeError(f'{name} {key} replay differs by {difference}')
    if actual['time_s']!=expected['time_s'] or not result.converged:raise RuntimeError('replay horizon or root acceptance differs')
    if executed['events']:raise RuntimeError('accepted schedules were not fully materialized')
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest for path,digest in originals.items()):
        raise RuntimeError('original archived input/checkpoint/result were mutated')
    reports.append({'case':name,'original_run_id':expected['run_id'],'replay_run_id':actual['run_id'],
        'executed_input':str(file.relative_to(root)),'executed_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
        'replay_result':str(files[-1].relative_to(root)),'samples':len(actual['time_s']),
        'dt_s':executed['time']['dt_s'],'horizon_s':actual['time_s'][-1],
        'execution':executed['execution'],'checks':checks,'original_artifacts_unchanged':True,
        'max_energy_residual_W':max(abs(d['energy_balance_W']) for d in actual['diagnostics']),
        'walltime_seconds':time.perf_counter()-begin})
report={'status':'passed','scope':'actual NV/Shinaide browser trajectories replayed through standalone v3 file entry; no checkpoint passed to solver',
 'cases':reports,'cpu_affinity':sorted(os.sched_getaffinity(0)),'threads':torch.get_num_threads(),
 'cuda_visible_devices':os.environ['CUDA_VISIBLE_DEVICES'],'total_walltime_seconds':time.perf_counter()-started,
 'source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in (
    'src/backend/liquidcooling/accepted_run_input.py','src/backend/liquidcooling/dae_experiment.py',
    'src/backend/liquidcooling/dae_runtime.py','src/backend/liquidcooling/runtime_batch.py',
    'src/backend/liquidcooling/solver/dae_newton.py','src/backend/liquidcooling/solver/equation_graph.py')}}
(root/'docs/benchmarks/accepted-input-replay-2026-09-26.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report),flush=True)
