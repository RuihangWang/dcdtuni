from pathlib import Path
import sys,json,os,copy,hashlib,time,traceback,signal
T=Path(os.environ["DCTWIN_MMS_WORK"])
sys.path.insert(0,str(Path(os.environ['DCTWIN_MMS_REPO'])/'src'))
import torch,numpy as np
torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import DAESimulation,admit_dae_runtime_input
start=time.monotonic()
def digest_tensor(v):return hashlib.sha256(v.detach().cpu().numpy().tobytes()).hexdigest()
assert len(sys.argv)==2 and sys.argv[1] in ('0p03125','0p015625')
(T/'results').mkdir(exist_ok=True)
prepared=json.loads((T/'PREPARED-IDENTITIES.json').read_text());identity=next(r for r in prepared['rows'] if r['tag']==sys.argv[1])
def held_sources():
 for row in json.loads((T/'SOURCE-MAP-backend.json').read_text())['rows']:
  for path in [row['path'],row['snapshot']]:
   assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==row['sha256'], 'Source drift '+path
held_sources()
print('CURRENT_FULL_MMS_START',json.dumps({'tag':sys.argv[1],'identity':identity,'affinity':sorted(os.sched_getaffinity(0)),'cuda':os.environ.get('CUDA_VISIBLE_DEVICES'),'preregistered':hashlib.sha256((T/'PREREGISTRATION.json').read_bytes()).hexdigest()}),flush=True)
runs=[]
for tag in (sys.argv[1],):
 input_path=T/'compiled'/('Shinaide_Current_Glycol25_FullMMS_'+tag+'.liquidrun')
 assert hashlib.sha256(input_path.read_bytes()).hexdigest()==identity['liquidrunRawSHA256']
 raw=json.loads(input_path.read_text());assert raw['execution']['device']=='cpu' and raw['execution']['max_threads']==2
 sim=DAESimulation(raw,run_id='current-full-Glycol25-MMS-'+tag)
 runstart=time.monotonic()
 frames=[sim.latest_frame]
 stop_requested=[False]
 def request_stop(signum,frame):stop_requested[0]=True
 signal.signal(signal.SIGTERM,request_stop)
 def save_partial():
  # Only accepted public product frames are collected; numerical state/root
  # is never changed by the collector or the graceful termination flag.
  partial={name:np.array([f[key] for f in frames]) for name,key in
   {'node_pressure_Pa':'node_pressure_Pa','flow_kg_s':'edge_flow_kg_s',
    'node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temperature_K',
    'solid_temperature_K':'chip_temp_K','hx_secondary_K':'secondary_supply_K',
    'hx_primary_K':'primary_return_K'}.items()}
  np.savez(T/f'results/partial-accepted-{tag}.npz',times=[f['time_s'] for f in frames],**partial)
  (T/f'results/partial-accepted-{tag}.json').write_text(json.dumps(frames))
 try:
  while not sim.done:
   if stop_requested[0]:raise InterruptedError('Original registered deadline termination requested')
   f=sim.step();frames.append(f);print('MMS',tag,f['seq'],f['diagnostics']['scaled_residual'],flush=True)
  if stop_requested[0]:raise InterruptedError('Original registered deadline termination requested')
 except BaseException:
  save_partial()
  raise
 eq=sim.definition.equation_graph
 assert eq.size==1693
 (T/f'results/actual-source-options-and-layout-{tag}.json').write_text(json.dumps({'backendModule':__import__('backend.liquidcooling.dae_runtime',fromlist=['x']).__file__,'newton':sim.definition.manifest.newton.model_dump(),'freePressureNativeOrder':eq._free.cpu().numpy().tolist(),'state_layout':{k:[v.start,v.stop] for k,v in eq.state_layout.items()},'effective_cpu':sorted(os.sched_getaffinity(0)),'cuda':os.environ.get('CUDA_VISIBLE_DEVICES')},indent=2))
 assert sim.definition.equation_graph.size==1693
 target=np.load(T/f'reference/target-{tag}.npz');mapping={'node_pressure_Pa':'node_pressure_Pa','flow_kg_s':'edge_flow_kg_s','node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temperature_K','solid_temperature_K':'chip_temp_K','hx_secondary_K':'secondary_supply_K','hx_primary_K':'primary_return_K'}
 errors={};arrays={}
 for name,key in mapping.items():
  actual=np.array([f[key] for f in frames]);desired=target[name];diff=actual-desired
  errors[name]=dict(final_inf=float(np.abs(diff[-1]).max()),whole_trajectory_inf=float(np.abs(diff).max()),all_entries=diff.size)
  arrays[name+'_actual']=actual;arrays[name+'_reference']=desired;arrays[name+'_error']=diff
 free=eq._free.cpu().numpy()
 family_order=list(mapping)
 flat_actual=np.concatenate([arrays[k+'_actual'][:,free] if k=='node_pressure_Pa' else arrays[k+'_actual'] for k in family_order],axis=1)
 flat_reference=np.concatenate([arrays[k+'_reference'][:,free] if k=='node_pressure_Pa' else arrays[k+'_reference'] for k in family_order],axis=1)
 assert flat_actual.shape==(len(frames),1693) and flat_reference.shape==flat_actual.shape
 assert np.isfinite(flat_actual).all() and np.isfinite(flat_reference).all()
 assert np.array_equal(flat_actual[0].view(np.uint64),flat_reference[0].view(np.uint64)),'all1693 initial-state bits'
 arrays.update(flat1693_actual=flat_actual,flat1693_reference=flat_reference,flat1693_error=flat_actual-flat_reference)
 np.savez(T/f'results/newton-vs-manufactured-{tag}.npz',times=target['times'],**arrays)
 (T/f'results/mms-frames-{tag}.json').write_text(json.dumps(frames,indent=2))
 runs.append(dict(tag=tag,dt=sim.definition.dt,frames=len(frames),errors=errors,max_scaled_residual=max(f['diagnostics']['scaled_residual'] for f in frames),max_energy_balance_W=max(abs(f['diagnostics']['energy_balance_W']) for f in frames),max_mass_kg_s=max(f['diagnostics']['mass_residual_kg_s'] for f in frames),max_momentum_Pa=max(f['diagnostics']['momentum_residual_Pa'] for f in frames),elapsed_s=time.monotonic()-runstart))
 (T/f'results/mms-fine-progress-{tag}.json').write_text(json.dumps(runs,indent=2))
orders={name:[float(np.log(runs[i]['errors'][name]['final_inf']/runs[i+1]['errors'][name]['final_inf'])/np.log(2)) for i in range(len(runs)-1)] for name in ('node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K')}
(T/f'results/mms-asymptotic-single-{tag}.json').write_text(json.dumps(dict(scope='CURRENT Glycol25 full1693 current React product-chain MMS; same physical Baseline; synthetic forcing; independent review and convergence acceptance pending',runs=runs,orders=orders,status='completed_original_Newton_and_energy_gates',elapsed_s=time.monotonic()-start),indent=2))
held_sources()
print('MMS COMPLETE',orders,flush=True)
