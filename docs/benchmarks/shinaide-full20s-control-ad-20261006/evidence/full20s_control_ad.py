import os,sys,json,time,hashlib,dataclasses,traceback
from pathlib import Path
R=Path(__file__).resolve().parent;I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration');sys.path.insert(0,str(I/'src'))
class Tee:
 def __init__(self,a,b):self.a=a;self.b=b
 def write(self,s):self.a.write(s);self.b.write(s);self.b.flush()
 def flush(self):self.a.flush();self.b.flush()
log=(R/'worker-v1.log').open('w');sys.stdout=Tee(sys.stdout,log);sys.stderr=Tee(sys.stderr,log)
import torch,numpy as np
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input,DAESimulation
from backend.liquidcooling.dae_experiment import DAEExperiment
from backend.liquidcooling.runtime import _canonical_hash
from collections import Counter
torch.set_num_threads(2);torch.set_num_interop_threads(1)
def emit(k,v):print(k,json.dumps(v,allow_nan=False),flush=True)
raw=json.loads((R/'actual/input.liquidrun').read_text());accepted=json.loads((R/'actual/executed.liquidrun').read_text());record=json.loads((R/'actual/result.json').read_text());cp=json.loads((R/'actual/checkpoint.json').read_text());receipt=json.loads((R/'actual/executed-inputs.json').read_text());req=json.loads((R/'actual/input.json').read_text())
assert cp['version']==3 and cp['seq']==10 and record['time_s']==list(range(0,21,2))
assert cp['liquidrun']==raw==req['liquidrun'] and req['config_hash']==cp['config_hash']==record['config_hash']==receipt['original_config_hash']
assert receipt['accepted_sample_count']==11 and receipt['complete_original_horizon']
assert record['completed'] and record['converged'] and record['status']=='completed'
assert raw.get('manufactured_benchmark') is None and accepted.get('manufactured_benchmark') is None
assert record['applied_inputs']==cp['applied_inputs'] and len(cp['applied_inputs'])==1
command=cp['applied_inputs'][0];assert command['seq']==1 and command['time_s']==2. and command['heat_multiplier']==1.2
assert command['pump_rpm_ratios']=={'m_30_pump':.95} and command['valve_openings']=={'m_32_control_valve_1778059294098':.9} and command['primary_inlet_K']=={'m_30_hx':294.15}
definition=admit_dae_runtime_input(raw);admitted_accepted=admit_dae_runtime_input(accepted);eq=definition.equation_graph;g=eq.graph
assert definition._manufactured_sources is None and eq.size==1693 and g.N_nodes==229 and g.N_edges==286
assert _canonical_hash(definition.manifest.model_dump(by_alias=True,mode='json',exclude_none=True),[])==record['config_hash']
assert _canonical_hash(admitted_accepted.manifest.model_dump(by_alias=True,mode='json',exclude_none=True),[])==receipt['executed_config_hash']
assert raw['nodes']==accepted['nodes'] and [e['id'] for e in raw['edges']]==[e['id'] for e in accepted['edges']]==record['edge_ids']==list(g.edge_str_ids)
assert [n['id'] for n in raw['nodes']]==record['node_ids']==list(g.node_ids)
fields={'solid_temperature':'chip_temp_K','node_temperature':'node_temperature_K','pressure':'node_pressure_Pa','flow':'edge_flow_kg_s','water_temperature':'cell_temp_K','edge_outlet_temperature':'edge_outlet_temp_K','primary_return_temperature':'primary_return_K','hx_secondary_temperature':'secondary_supply_K'}
states={};parameters={};trajectories=[]
pumpid=next(iter(command['pump_rpm_ratios']));valveid=next(iter(command['valve_openings']));active_sign=None
initial_reference=None

def snapshot_imports():
 rows=[]
 for m in list(sys.modules.values()):
  fn=getattr(m,'__file__',None)
  if not fn:continue
  p=Path(fn).resolve()
  if p.is_file() and p.is_relative_to(I/'src/backend'):
   data=p.read_bytes();q=R/'source-snapshot'/p.relative_to(I);q.parent.mkdir(parents=True,exist_ok=True)
   if q.exists():assert q.read_bytes()==data
   else:q.write_bytes(data)
   rows.append({'path':str(p),'copy':str(q.relative_to(R)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 (R/'imported-source-before.json').write_text(json.dumps(rows,indent=2));return rows
imports=snapshot_imports()

def check_host(host,label,compare=False):
 global initial_reference,active_sign
 ss=host.tensor_series('solid_temperature');assert ss.shape==(56,11)
 arrs={};checks={}
 for f,key in fields.items():
  got=host.tensor_series(f).detach().numpy();arrs[f]=got
  if compare:
   ref=np.asarray(record[key],dtype=np.float64);assert got.shape==ref.shape
   checks[f]={'shape':list(got.shape),'sample_bit_exact':[bool(np.array_equal(got[:,i].view(np.uint64),ref[:,i].view(np.uint64))) for i in range(11)],'max_abs_by_sample':[float(np.max(np.abs(got[:,i]-ref[:,i]))) for i in range(11)]}
   assert all(checks[f]['sample_bit_exact']),(label,f,checks[f])
 y=np.stack([x.detach().numpy() for x in host._states],axis=1);th=np.stack([x.detach().numpy() for x in host._parameters],axis=1)
 # Read-only internal state serialization is evidence for independent conservation, not an API bypass.
 states[label]=y;parameters[label]=th
 if initial_reference is None:initial_reference=y[:,0].copy()
 assert np.array_equal(y[:,0].view(np.uint64),initial_reference.view(np.uint64)),'fixed actual initial state drift'
 frames=host.frames;assert len(frames)==11 and [x['seq'] for x in frames]==list(range(11)) and [x['time_s'] for x in frames]==record['time_s']
 for i,f in enumerate(frames):
  assert f['newton']['scaled_residual_inf']<=1e-10
  assert abs(f['diagnostics']['energy_balance_W'])<=max(1e-7,256*np.finfo(np.float64).eps)*(1+abs(f['diagnostics']['heat_input_W']))
  assert f['diagnostics']['mass_residual_kg_s']<=1e-10 and f['diagnostics']['momentum_residual_Pa']<=1e-6
  assert not bool(eq._closed_valves.any()) and not bool(eq._pump_flow.any())
  flow=arrs['flow'][:,i];sign=np.sign(flow)
  if active_sign is None:active_sign=sign.copy()
  assert np.array_equal(sign,active_sign),'fixed active flow-sign regime changed'
  eq.validate_parameters(host._parameters[i]);eq.problem.validate(host._states[i]);eq.validate_state(host._states[i],host._parameters[i])
 if compare:
  assert host.applied_inputs==record['applied_inputs']
  for a,b in [(host.accepted_heat_multipliers,record['heat_multipliers']),(host.accepted_pump_rpm_ratios,record['pump_rpm_ratios']),(host.accepted_valve_openings,record['valve_openings']),(host.accepted_primary_inlet_K,record['primary_inlet_K'])]:assert a==b
 return checks,frames,float(np.min(np.abs(arrs['flow'])))

def trajectory(pp,vv,label,ad=False):
 t=time.perf_counter();host=DAEExperiment(definition,record_frames=True)
 assert host.seq==0 and host.tensor_state.requires_grad is False
 assert host.pump_rpm_control_edge_ids==[pumpid] and len(host.valve_edge_ids)==14 and host.valve_edge_ids[0]==valveid
 p=host.tensor_state.new_tensor(pp,requires_grad=ad);v=host.tensor_state.new_tensor(vv,requires_grad=ad)
 # Exact actual accepted command BEFORE the seq1 BE root. Heat/Tin and step time are never shifted.
 host.set_inputs(heat_multiplier=command['heat_multiplier'],pump_rpm_ratios={pumpid:p},valve_openings={valveid:v},primary_inlet_K=command['primary_inlet_K'])
 root_graphs=[]
 while not host.done:
  host.step();s=host.tensor_state;root_graphs.append({'seq':host.seq,'requires_grad':s.requires_grad,'grad_fn':type(s.grad_fn).__name__ if s.grad_fn else None})
 assert host.seq==10
 checks,frames,minflow=check_host(host,label,compare=label=='baseline_AD')
 J=host.tensor_series('solid_temperature')[:,-1].mean()
 grads=None
 if ad:
  assert J.requires_grad and all(x['requires_grad'] for x in root_graphs)
  ta=time.perf_counter();grads=torch.autograd.grad(J,(p,v));assert all(bool(torch.isfinite(x)) and float(x)!=0 for x in grads)
  emit('FULL_TRAJECTORY_AD',{'label':label,'backward_seconds':time.perf_counter()-ta,'J_K':float(J.detach()),'pump_AD':float(grads[0]),'valve_AD':float(grads[1])})
 out={'label':label,'pump_command':pp,'valve_command':vv,'objective_final_mean56solid_K':float(J.detach()),'sample_count':11,'seconds':time.perf_counter()-t,'root_graphs':root_graphs,'min_abs_flow_kg_s':minflow,'actual_match':checks,'frames':frames,'applied_inputs':host.applied_inputs}
 trajectories.append(out);(R/'progress.json').write_text(json.dumps({'completed_trajectories':len(trajectories),'latest_label':label,'latest_objective_K':out['objective_final_mean56solid_K']},indent=2));emit('TRAJECTORY',{'label':label,'seconds':out['seconds'],'objective':out['objective_final_mean56solid_K'],'all_actual_arrays_bit_exact':bool(checks) and all(all(x['sample_bit_exact']) for x in checks.values()),'max_scaled_residual':max(x['newton']['scaled_residual_inf'] for x in frames),'max_energy_gap_W':max(abs(x['diagnostics']['energy_balance_W']) for x in frames)})
 return float(J.detach()),grads,host

preflight={'run_id':record['run_id'],'original_config':record['config_hash'],'accepted_config':receipt['executed_config_hash'],'checkpoint_version':3,'online_commands':cp['applied_inputs'],'graph_nodes':g.N_nodes,'graph_edges':g.N_edges,'edge_type_counts':dict(Counter(x['type'] for x in raw['edges'])),'states':eq.size,'differential_states':int(eq.differential_mask.sum()),'parameter_pack':{'source':raw['parameter_pack']['source'],'revision':raw['parameter_pack']['revision'],'description':raw['parameter_pack']['description']},'source_forcing':None,'objective':'final t20 mean of all56 coldplate solid temperatures K, derivative w.r.t. unique seq1 held command values; fixed actual t0, heat1.2/Tin294.15, other13valves1; no initial steady derivative','predeclared_finest_FD_gate':'relative<=1e-4 OR absolute<=1e-8 K/unit at h1e-4','original_newton_options':dataclasses.asdict(definition.newton_options),'cpu_affinity':sorted(os.sched_getaffinity(0)),'threads':torch.get_num_threads(),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES')}
(R/'preflight.json').write_text(json.dumps(preflight,indent=2));emit('PREFLIGHT',preflight)
base,ad,host=trajectory(.95,.9,'baseline_AD',True)
# This explicitly tests the actual v3 replay API using the original immutable checkpoint.
t=time.perf_counter();restored=DAESimulation.from_checkpoint(cp);rr=restored.result();replay={}
for key in fields.values():
 aa=np.asarray(rr[key],dtype=np.float64);bb=np.asarray(record[key],dtype=np.float64);replay[key]={'shape':list(aa.shape),'bit_exact':bool(np.array_equal(aa.view(np.uint64),bb.view(np.uint64))),'max_abs':float(np.max(np.abs(aa-bb)))};assert replay[key]['bit_exact']
assert rr['applied_inputs']==record['applied_inputs'] and rr['time_s']==record['time_s']
(R/'checkpoint-replay.json').write_text(json.dumps({'seconds':time.perf_counter()-t,'original_checkpoint_version':3,'original_checkpoint_unchanged':cp==json.loads((R/'actual/checkpoint.json').read_text()),'all_eight_arrays':replay,'applied_inputs_exact':True,'time_grid_exact':True},indent=2));emit('CHECKPOINT_REPLAY',{'seconds':time.perf_counter()-t,'all_eight_arrays_bit_exact':True})
fd=[]
for name,idx in [('pump',0),('valve',1)]:
 for h in [1e-2,1e-3,1e-4]:
  plus=[.95,.9];minus=plus.copy();plus[idx]+=h;minus[idx]-=h
  jp,_,_=trajectory(*plus,f'{name}_plus_{h:g}');jm,_,_=trajectory(*minus,f'{name}_minus_{h:g}')
  value=(jp-jm)/(2*h);aa=float(ad[idx]);ab=abs(value-aa);re=ab/max(abs(aa),1e-30);item={'control':name,'h':h,'AD_K_per_unit':aa,'FD_K_per_unit':value,'absolute_error':ab,'relative_error':re};fd.append(item);emit('FD',item)
  if h==1e-4:assert re<=1e-4 or ab<=1e-8,item
# Only the final accepted local BE Jacobian is factored here; the actual full AD used all10 original implicit transpose gates.
y=host._states[-1].detach();old=host._states[-2].detach();theta=host._parameters[-1].detach();scale=eq.state_scale;z=(y/scale).requires_grad_(True);rescale=eq.residual_scale
with torch.enable_grad():
 def residual(zz):
  yy=zz*scale;dy=torch.where(eq.differential_mask,(yy-old)/2.,torch.zeros_like(yy));return eq.problem.residual(yy.new_tensor(20.),yy,dy,theta)/rescale
 jac=torch.autograd.functional.jacobian(residual,z,vectorize=True).detach();sv=torch.linalg.svdvals(jac);rank=int(torch.linalg.matrix_rank(jac));rhs=torch.zeros_like(z);rhs[eq.state_layout['solid_temperature']]=scale[eq.state_layout['solid_temperature']]/56.;dual,info=torch.linalg.solve_ex(jac.T,rhs);defect=float((jac.T@dual-rhs).abs().max());gate=max(1e-8,128*torch.finfo(z.dtype).eps)*(1+float(rhs.abs().max()));assert int(info)==0 and defect<=gate
jacinfo={'scope':'local t20 BE Jacobian; not trajectory Hessian','shape':list(jac.shape),'finite':bool(torch.isfinite(jac).all()),'rank':rank,'singular_min':float(sv.min()),'singular_max':float(sv.max()),'condition_2':float(sv.max()/sv.min()),'transpose_defect_inf':defect,'original_gate':gate,'info':int(info)};emit('FINAL_LOCAL_JACOBIAN',jacinfo)
np.savez_compressed(R/'states.npz',**states);np.savez_compressed(R/'parameters.npz',**parameters);np.savez_compressed(R/'final-local-jacobian.npz',scaled_jacobian=jac.numpy())
held=[]
for x in json.loads((R/'normal-before-guards.json').read_text())+imports:
 data=Path(x['path']).read_bytes();held.append(dict(x,after_sha256=hashlib.sha256(data).hexdigest(),same_bytes=len(data)==x['bytes'] and hashlib.sha256(data).hexdigest()==x['sha256']))
assert all(x['same_bytes'] for x in held);(R/'normal-after-guards.json').write_text(json.dumps(held,indent=2))
result={'preflight':preflight,'baseline_objective_K':base,'AD':{'pump':float(ad[0]),'valve':float(ad[1])},'FD':fd,'trajectories':trajectories,'checkpoint_replay':replay,'final_local_jacobian':jacinfo,'normal_guards_records':len(held),'normal_guards_unique':len(set(x['path'] for x in held)),'normal_zero_drift':True,'scope':'actual full20s11frames baseline bitexact, two held command derivatives first order; FD variants are PRIVATE unobserved counterfactual trajectories with same immutable input and same heat/Tin/initial graph','not_claimed':'hardware calibration; second derivatives; all valves; steady derivative; arbitrary events/topology; coarse fullMMS EOC'};(R/'RESULT.json').write_text(json.dumps(result,indent=2,allow_nan=False));emit('DONE',{'J':base,'AD':result['AD'],'FD':fd,'guard_zero_drift':True})
