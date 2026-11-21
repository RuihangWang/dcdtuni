from pathlib import Path
import sys,json,os,copy,hashlib,time,traceback
T=Path(__file__).resolve().parent
variant=sys.argv[1] if len(sys.argv)>1 else 'after'
sys.path.insert(0,str(T/variant/'src'))
import torch,numpy as np
torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import DAESimulation,admit_dae_runtime_input
start=time.monotonic()
def digest_tensor(v):return hashlib.sha256(v.detach().cpu().numpy().tobytes()).hexdigest()
if variant in ('before','after-default'):
 if variant=='after-default':raise ValueError('use after default mode argument')
if len(sys.argv)>2 and sys.argv[2]=='default':
 rows=[]
 for name,p in [('ring-steady',T/'results/after-baseline.liquidrun'),('ring-transient',T/'results/after-transient-base.liquidrun'),('old-MMS',T.parents[1]/'docs/benchmarks/fixtures/mms-product-v3.liquidrun')]:
  raw=json.loads(p.read_text());sim=DAESimulation(raw,run_id='private-default-parity')
  if not sim.done:sim.step()
  checkpoint=sim.checkpoint();restored=DAESimulation.from_checkpoint(raw,checkpoint)
  if not sim.done:
   sim.step();restored.step()
  assert restored.frames==sim.frames
  eq=sim.definition.equation_graph;theta=sim.definition.parameters(0)
  rows.append(dict(name=name,parameter_count=eq.parameter_count,parameter_layout={k:[v.start,v.stop] for k,v in eq.parameter_layout.items()},state_layout={k:[v.start,v.stop] for k,v in eq.state_layout.items()},theta_sha=digest_tensor(theta),state_sha=digest_tensor(sim.experiment.tensor_state),frames=sim.frames,checkpoint=checkpoint,replay_exact=True,config_hash=sim.config_hash))
  print('DEFAULT',variant,name,flush=True)
 (T/f'results/default-{variant}.json').write_text(json.dumps(rows,indent=2));sys.exit(0)

runs=[]
for tag in ('0p125','0p0625'):
 raw=json.loads((T/f'results/manufactured-dt-{tag}.liquidrun').read_text());sim=DAESimulation(raw,run_id='private-full-MMS-'+tag)
 runstart=time.monotonic();checkpoint=None
 while not sim.done:
  f=sim.step();print('MMS',tag,f['seq'],f['diagnostics']['scaled_residual'],flush=True)
  if f['seq']==2:checkpoint=sim.checkpoint()
 frames=sim.frames
 if tag=='1p0':
  restored=DAESimulation.from_checkpoint(raw,checkpoint)
  while not restored.done:restored.step()
  assert restored.frames==frames,'manufactured checkpoint replay mismatch'
  (T/'results/manufactured-checkpoint.json').write_text(json.dumps(checkpoint,indent=2))
 target=np.load(T/f'results/target-dt-{tag}.npz');mapping={'node_pressure_Pa':'node_pressure_Pa','flow_kg_s':'edge_flow_kg_s','node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temperature_K','solid_temperature_K':'chip_temp_K','hx_secondary_K':'secondary_supply_K','hx_primary_K':'primary_return_K'}
 errors={};arrays={}
 for name,key in mapping.items():
  actual=np.array([f[key] for f in frames]);desired=target[name];diff=actual-desired
  errors[name]=dict(final_inf=float(np.abs(diff[-1]).max()),whole_trajectory_inf=float(np.abs(diff).max()),all_entries=diff.size)
  arrays[name+'_actual']=actual;arrays[name+'_reference']=desired;arrays[name+'_error']=diff
 np.savez(T/f'results/newton-vs-manufactured-{tag}.npz',times=target['times'],**arrays)
 (T/f'results/mms-frames-{tag}.json').write_text(json.dumps(frames,indent=2))
 runs.append(dict(tag=tag,dt=sim.definition.dt,frames=len(frames),errors=errors,max_scaled_residual=max(f['diagnostics']['scaled_residual'] for f in frames),max_energy_balance_W=max(abs(f['diagnostics']['energy_balance_W']) for f in frames),max_mass_kg_s=max(f['diagnostics']['mass_residual_kg_s'] for f in frames),max_momentum_Pa=max(f['diagnostics']['momentum_residual_Pa'] for f in frames),checkpoint_replay=(tag=='1p0'),elapsed_s=time.monotonic()-runstart))
 (T/'results/mms-fine-progress.json').write_text(json.dumps(runs,indent=2))
runs=json.loads((T/'results/mms-acceptance.json').read_text())['runs']+runs
orders={name:[float(np.log(runs[i]['errors'][name]['final_inf']/runs[i+1]['errors'][name]['final_inf'])/np.log(2)) for i in range(len(runs)-1)] for name in ('node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K')}
(T/'results/mms-refinement.json').write_text(json.dumps(dict(scope='PRIVATE full hydraulic/thermal manufactured product-chain numerical evidence; independent review/normal UI acceptance pending',runs=runs,orders=orders,status='completed_original_Newton_and_energy_gates',elapsed_s=time.monotonic()-start),indent=2))
print('MMS COMPLETE',orders,flush=True)
