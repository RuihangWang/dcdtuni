import os,sys,json,hashlib,time,traceback,dataclasses,shutil
from pathlib import Path
R=Path(__file__).resolve().parent
class Tee:
 def __init__(self,stream,log):self.stream=stream;self.log=log
 def write(self,text):self.stream.write(text);self.log.write(text);self.log.flush()
 def flush(self):self.stream.flush();self.log.flush()
log=(R/'worker-v1.log').open('w');sys.stdout=Tee(sys.stdout,log);sys.stderr=Tee(sys.stderr,log)
I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
sys.path.insert(0,str(I/'src'))
import numpy as np
import torch
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
from backend.liquidcooling.dae_experiment import DAEExperiment
from backend.liquidcooling.solver.dae_newton import backward_euler_step

torch.set_num_threads(2);torch.set_num_interop_threads(1)
def emit(label,value):
 print(label,json.dumps(value,allow_nan=False),flush=True)
def js(value):
 if isinstance(value,torch.Tensor):
  return value.detach().cpu().tolist()
 if dataclasses.is_dataclass(value):return dataclasses.asdict(value)
 return value
rawdir=R/'actual/run-e512006bbf0e4c6db16d918b35818867'
raw=json.loads((rawdir/'executed.liquidrun').read_text());original=json.loads((rawdir/'result.json').read_text())
definition=admit_dae_runtime_input(raw);eq=definition.equation_graph;g=eq.graph
assert definition._manufactured_sources is None and definition.manifest.manufactured_benchmark is None
assert eq.size==1693 and g.N_nodes==229 and g.N_edges==286
sourceguards=[]
for module in list(sys.modules.values()):
 name=getattr(module,'__file__',None)
 if not name:continue
 p=Path(name).resolve()
 if p.is_file() and p.is_relative_to(I/'src/backend'):
  content=p.read_bytes();rel=p.relative_to(I);target=R/'source-snapshot'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
  sourceguards.append({'path':str(p),'copy':str(target.relative_to(R)),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
(R/'imported-source-before.json').write_text(json.dumps(sourceguards,indent=2))
t=time.perf_counter();host=DAEExperiment(definition,record_frames=True);previous=host.tensor_state.detach()
fields={'solid_temperature':'chip_temp_K','node_temperature':'node_temperature_K','pressure':'node_pressure_Pa','flow':'edge_flow_kg_s','water_temperature':'cell_temp_K','edge_outlet_temperature':'edge_outlet_temp_K','primary_return_temperature':'primary_return_K','hx_secondary_temperature':'secondary_supply_K'}
def compare_actual(y,theta,seq):
 values=eq.unpack(y,theta);out={}
 for field,key in fields.items():
  got=values[field].detach().cpu().numpy();expected=np.asarray(original[key],dtype=np.float64)[:,seq]
  assert got.shape==expected.shape,(field,got.shape,expected.shape)
  out[field]={'count':got.size,'bit_exact':bool(np.array_equal(got.view(np.uint64),expected.view(np.uint64))),'max_abs':float(np.max(np.abs(got-expected)))}
 return out
p0=definition.parameters(0);initmatch=compare_actual(previous,p0,0)
emit('INITIALIZED',{'seconds':time.perf_counter()-t,'actual_match':initmatch,'initializer':host.initializer})
assert all(x['max_abs']<=1e-7 for x in initmatch.values())
assert len(host.pump_rpm_control_edge_ids)==1 and len(host.valve_edge_ids)==14
pumpid=host.pump_rpm_control_edge_ids[0];valveid=host.valve_edge_ids[0]
p=previous.new_tensor(.95,requires_grad=True);v=previous.new_tensor(.9,requires_grad=True)
host.set_inputs(pump_rpm_ratios={pumpid:p},valve_openings={valveid:v});t=time.perf_counter();host.step()
yad=host.tensor_state;theta_ad=definition.parameters(1,pump_rpm_ratio=p.reshape(1),valve_opening=torch.cat((v.reshape(1),v.new_ones(13))))
match=compare_actual(yad,theta_ad,1)
assert all(x['max_abs']<=1e-7 for x in match.values())
objective=host.tensor_series('solid_temperature')[:,-1].mean()
assert objective.requires_grad
ad=torch.autograd.grad(objective,(p,v))
assert all(bool(torch.isfinite(x)) and float(x)!=0 for x in ad)
emit('AD',{'seconds':time.perf_counter()-t,'J_mean56solid_K':float(objective.detach()),'pump_gradient_K_per_rpm_ratio':float(ad[0]),'valve_gradient_K_per_opening':float(ad[1]),'actual_match':match})

time_s=previous.new_tensor(2.);dt=previous.new_tensor(2.)
base_sign=None;solve_records=[];states={'previous':previous.cpu().numpy(),'host_baseline':yad.detach().cpu().numpy()}
def solve(pp,vv,label):
 global base_sign
 theta=definition.parameters(1,pump_rpm_ratio=previous.new_tensor([pp]),valve_opening=previous.new_tensor([vv]+[1.]*13))
 assert theta[eq.parameter_layout['heat_W']].sum().item()==25200.
 assert theta[eq.parameter_layout['primary_inlet_K']].tolist()==[294.15]
 t=time.perf_counter();y,report=backward_euler_step(eq.problem,previous,theta,time=time_s,dt=dt,options=definition.newton_options)
 ydot=torch.where(eq.differential_mask,(y-previous)/dt,torch.zeros_like(y));diagnostics={k:float(value) for k,value in eq.diagnostics(time_s,y,ydot,theta).items()}
 eq.validate_parameters(theta);eq.problem.validate(y);eq.validate_state(y,theta)
 assert diagnostics['scaled_residual']<=1e-10
 assert abs(diagnostics['energy_balance_W'])<=1e-7*(1+25200.)
 flow=eq.unpack(y,theta)['flow'];sign=torch.sign(flow)
 if base_sign is None:base_sign=sign
 assert torch.equal(sign,base_sign),'fixed smooth flow-sign regime changed'
 assert not bool(eq._closed_valves.any()) and not bool(eq._pump_flow.any())
 value=float(eq.unpack(y,theta)['solid_temperature'].mean())
 item={'label':label,'pump':pp,'valve':vv,'objective_K':value,'seconds':time.perf_counter()-t,'newton':js(report),'diagnostics':diagnostics,'constraints':'original parameter/state validators accepted; all 14 valves remain open; pump not flow-held; all 286 flow signs unchanged','flow_min_abs_kg_s':float(flow.abs().min())}
 solve_records.append(item);states[label]=y.detach().cpu().numpy();emit('SOLVE',item);return y,theta,value
base,theta_base,jbase=solve(.95,.9,'baseline')
assert torch.equal(base,yad.detach()),'isolated public BE differs from host tensor-control step'
fd=[]
for label,index in [('pump',0),('valve',1)]:
 for h in [1e-2,1e-3,1e-4]:
  plus=[.95,.9];minus=plus.copy();plus[index]+=h;minus[index]-=h
  _,_,jp=solve(*plus,f'{label}_plus_{h:g}');_,_,jm=solve(*minus,f'{label}_minus_{h:g}')
  slope=(jp-jm)/(2*h);derivative=float(ad[index]);absolute=abs(slope-derivative);relative=absolute/max(abs(derivative),1e-30)
  item={'control':label,'h':h,'AD_K_per_unit':derivative,'centered_FD_K_per_unit':slope,'absolute_error':absolute,'relative_error':relative};fd.append(item);emit('FD',item)
# Original scaled Newton Jacobian and independent adjoint defect at the same accepted BE root.
t=time.perf_counter();scale=eq.state_scale;rescale=eq.residual_scale;z=(base.detach()/scale).requires_grad_(True);theta=theta_base.detach()
def scaled_residual(zz):
 y=zz*scale;ydot=torch.where(eq.differential_mask,(y-previous)/dt,torch.zeros_like(y));return eq.problem.residual(time_s,y,ydot,theta)/rescale
jac=torch.autograd.functional.jacobian(scaled_residual,z,vectorize=True).detach()
assert jac.shape==(1693,1693) and bool(torch.isfinite(jac).all())
singular=torch.linalg.svdvals(jac);rhs=torch.zeros_like(z);sl=eq.state_layout['solid_temperature'];rhs[sl]=scale[sl]/56.
lam,info=torch.linalg.solve_ex(jac.T,rhs);assert int(info)==0
adjoint_defect=float((jac.T@lam-rhs).abs().max());adjoint_gate=max(1e-8,128*torch.finfo(z.dtype).eps)*(1+float(rhs.abs().max()));assert adjoint_defect<=adjoint_gate
jacinfo={'shape':list(jac.shape),'finite':True,'rank_at_default_torch_matrix_rank':int(torch.linalg.matrix_rank(jac)),'singular_min':float(singular.min()),'singular_max':float(singular.max()),'condition_2':float(singular.max()/singular.min()),'adjoint_solve_info':int(info),'adjoint_defect_inf':adjoint_defect,'original_adjoint_gate':adjoint_gate,'seconds':time.perf_counter()-t}
emit('JACOBIAN',jacinfo)
np.savez_compressed(R/'states.npz',**states,scaled_jacobian=jac.cpu().numpy())
accept=[]
for name in ['pump','valve']:
 item=[a for a in fd if a['control']==name and a['h']==1e-4][0]
 ok=item['relative_error']<=1e-4 or item['absolute_error']<=1e-8
 accept.append({'control':name,'finest_step_pass':ok,'predeclared_FD_gate':'relative <=1e-4 OR absolute <=1e-8 K/unit at h=1e-4'});assert ok,item
held=[]
for item in sourceguards+json.loads((R/'normal-before-guards.json').read_text()):
 data=Path(item['path']).read_bytes();held.append({'path':item['path'],'before_sha':item['sha256'],'after_sha':hashlib.sha256(data).hexdigest(),'same_bytes':len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256']})
assert all(x['same_bytes'] for x in held)
(R/'normal-after-guards.json').write_text(json.dumps(held,indent=2))
out={'scope':'one actual unforced backward-Euler 2 s window; initial physical state fixed; not whole-20s trajectory or steady objective','actual_run':'e512006bbf0e4c6db16d918b35818867','request_config':original['config_hash'],'objective':'mean solid temperature of all 56 coldplates at t=2 s (K)','heat_W':25200.,'primary_inlet_K':294.15,'pump_id':pumpid,'pump_baseline':.95,'valve_id':valveid,'valve_baseline':.9,'other_valves':1.,'initial_actual_match':initmatch,'t2_actual_match':match,'host_vs_public_BE_bit_exact':bool(torch.equal(base,yad.detach())),'AD':{'pump':float(ad[0]),'valve':float(ad[1])},'FD':fd,'acceptance':accept,'solves':solve_records,'jacobian':jacinfo,'normal_guards_count':len(held),'normal_guards_zero_drift':True,'source_forcing':None,'threads':2,'cpu_affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'dtype':str(base.dtype),'device':str(base.device),'normal_newton_options':dataclasses.asdict(definition.newton_options)}
(R/'RESULT.json').write_text(json.dumps(out,indent=2,allow_nan=False));emit('DONE',{'AD':out['AD'],'acceptance':accept,'guards':len(held)})
