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

from backend.liquidcooling.manufactured_benchmark import canonical_digest,STATE_FIELDS,SOURCE_FIELDS,compiled_ids
from independent_trajectory import IndependentTrajectory
raw=json.loads((T/'results/manufactured-dt-1p0.liquidrun').read_text())
definition=admit_dae_runtime_input(raw);eq=definition.equation_graph;ref=IndependentTrajectory(eq,definition.parameters(0))
negative=[]
def testnegative(name,mutate,initial=False,seal=True):
 r=copy.deepcopy(raw);mutate(r);b=r['manufactured_benchmark']
 if seal:
  b.pop('source_sha256',None);b['source_sha256']=canonical_digest(b)
 try:
  (DAESimulation if initial else admit_dae_runtime_input)(r)
 except Exception as exc:negative.append(dict(name=name,rejected=True,error_type=type(exc).__name__,error=str(exc)[:1500]));print('REJECT',name,type(exc).__name__,flush=True)
 else:raise AssertionError('negative admitted: '+name)
testnegative('unknown-family',lambda r:r['manufactured_benchmark']['sources'].update(junction_W={'ids':[],'values':[]}))
testnegative('wrong-units',lambda r:r['manufactured_benchmark']['units'].update(power='kW'))
testnegative('unknown-ID',lambda r:r['manufactured_benchmark']['sources']['momentum_Pa']['ids'].__setitem__(0,'not-an-edge'))
testnegative('duplicate-ID',lambda r:r['manufactured_benchmark']['sources']['solid_W']['ids'].__setitem__(0,r['manufactured_benchmark']['sources']['solid_W']['ids'][1]))
testnegative('missing-storage',lambda r:r['manufactured_benchmark']['initial_state']['water_temperature_K']['ids'].pop())
testnegative('missing-source-row',lambda r:r['manufactured_benchmark']['sources']['water_W']['values'].pop())
testnegative('short-source-series',lambda r:r['manufactured_benchmark']['sources']['momentum_Pa']['values'][0].pop())
testnegative('grid-mismatch',lambda r:r['manufactured_benchmark']['times_s'].__setitem__(1,.33))
testnegative('graph-digest',lambda r:r['manufactured_benchmark'].update(graph_layout_sha256='0'*64))
testnegative('source-digest',lambda r:r['manufactured_benchmark'].update(source_sha256='0'*64),seal=False)
testnegative('NaN',lambda r:r['manufactured_benchmark']['sources']['solid_W']['values'][0].__setitem__(0,float('nan')),seal=False)
testnegative('bool-number',lambda r:r['manufactured_benchmark']['sources']['solid_W']['values'][0].__setitem__(0,True))
testnegative('nonpositive-K',lambda r:r['manufactured_benchmark']['initial_state']['solid_temperature_K']['values'].__setitem__(0,-1.))
testnegative('zero-flow-regime',lambda r:r['manufactured_benchmark']['initial_state']['flow_kg_s']['values'].__setitem__(0,0.))
testnegative('wrong-initial-derivative',lambda r:r['manufactured_benchmark']['initial_temperature_rate']['water_temperature_K']['values'].__setitem__(0,1.),initial=True)
bcid=eq.graph.node_ids[int(torch.nonzero(eq._bc_mask)[0])]
def bad_datum(r):
 v=r['manufactured_benchmark']['initial_state']['node_pressure_Pa'];i=v['ids'].index(bcid);v['values'][i]+=1.
testnegative('wrong-pressure-datum',bad_datum,initial=True)
testnegative('steady-benchmark',lambda r:r['analysis'].update(mode='steady',initialization='steady'))
testnegative('byte-cap-before-graph',lambda r:r['manufactured_benchmark'].update(source_reference='x'*(32*1024*1024)),seal=False)
ids=compiled_ids(eq)
def pack(st,rt,src):
 y=torch.zeros(eq.size,dtype=eq.dtype);yd=torch.zeros_like(y)
 for name,(field,key) in STATE_FIELDS.items():
  val=y.new_tensor(st[name])
  if field=='pressure_free':val=val[eq._free]
  y[eq.state_layout[field]]=val
  if name in rt:yd[eq.state_layout[field]]=y.new_tensor(rt[name])
 theta=definition.parameters(0)
 for name,field in SOURCE_FIELDS.items():
  part=eq.parameter_layout[field];theta=torch.cat((theta[:part.start],theta.new_tensor(src[name]),theta[part.stop:]))
 return y,yd,theta
pointwise=[]
for t in (0.,.37,1.23,2.71,4.19,7.83,8.):
 st,rt,src,audit=ref.evaluate(t);y,yd,theta=pack(st,rt,src)
 residual=eq.residual(y.new_tensor(t),y,yd,theta);diag=eq.diagnostics(y.new_tensor(t),y,yd,theta)
 norms={k:float(residual[sl].abs().max()) for k,sl in eq.state_layout.items() if sl.stop>sl.start}
 scaled=float((residual/eq.residual_scale).abs().max())
 assert scaled<=definition.newton_options.atol,(t,scaled,norms)
 eq.validate_state(y,theta)
 pointwise.append(dict(time_s=t,continuous_residual_scaled_inf=scaled,unscaled_blocks=norms,diagnostics={k:float(v) for k,v in diag.items()},independent=audit))
y,yd,theta=pack(*ref.evaluate(0.)[:3]);bad=y.clone();bad[eq.state_layout['flow']]*=-1
try:eq.validate_state(bad,theta)
except ValueError as exc:negative.append(dict(name='accepted-state-sign-crossing',rejected=True,error=str(exc)))
else:raise AssertionError('flow sign crossing admitted')
(T/'results/backend-contract.json').write_text(json.dumps(dict(negative=negative,pointwise=pointwise,counts=ref.counts,status='passed',affinity=sorted(os.sched_getaffinity(0)),elapsed_s=time.monotonic()-start),indent=2))
print('POINTWISE CONTRACT PASSED',flush=True)

runs=[]
for tag in ('2p0','1p0','0p5','0p25'):
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
 (T/'results/mms-progress.json').write_text(json.dumps(runs,indent=2))
orders={name:[float(np.log(runs[i]['errors'][name]['final_inf']/runs[i+1]['errors'][name]['final_inf'])/np.log(2)) for i in range(3)] for name in ('node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K')}
(T/'results/mms-acceptance.json').write_text(json.dumps(dict(scope='PRIVATE full hydraulic/thermal manufactured product-chain numerical evidence; independent review/normal UI acceptance pending',runs=runs,orders=orders,status='completed_original_Newton_and_energy_gates',elapsed_s=time.monotonic()-start),indent=2))
print('MMS COMPLETE',orders,flush=True)
