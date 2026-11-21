from pathlib import Path
import sys,json,os,time
T=Path(__file__).resolve().parent;sys.path.insert(0,str(T/'after/src'))
import torch,numpy as np
torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.solver.dae_newton import backward_euler_step
raw=json.loads((T/'results/manufactured-dt-1p0.liquidrun').read_text());sim=DAESimulation(raw,run_id='private-mms-sensitivity')
d=sim.definition;eq=d.equation_graph;prev=sim.experiment.tensor_state.detach();base=d.parameters(1).detach();p=eq._theta(base)
target=np.load(T/'results/target-dt-1p0.npz');m=base.new_tensor(target['flow_kg_s'][1]);rows=[];start=time.monotonic()
def setpart(theta,name,val):
 sl=eq.parameter_layout[name];return torch.cat((theta[:sl.start],val,theta[sl.stop:]))
def objective(alpha,kind,coupled):
 theta=base
 if kind=='heat':
  theta=setpart(theta,'heat_W',p['heat_W']*alpha)
  if coupled:theta=setpart(theta,'mms_solid_W',p['mms_solid_W']-(alpha-1)*p['heat_W'])
 else:
  source=p['mms_momentum_Pa']
  if kind=='pump':
   idx=eq._ids['pump'];rate=p['pump_rpm_ratio']*alpha;theta=setpart(theta,'pump_rpm_ratio',rate)
   constant=-p['fluid_rho']*p['gravity']*(p['pump_H0']-p['pump_a']*(m[idx]/p['fluid_rho'])**2)
   delta=constant*(rate**2-p['pump_rpm_ratio']**2)
  else:
   idx=eq._ids['valve'];opening=p['valve_opening']*alpha;theta=setpart(theta,'valve_opening',opening)
   char=eq._characteristic
   def kv(a):return p['valve_Kv']*torch.where(char==1,50.**(a-1),torch.where(char==2,torch.sqrt(a),a))
   coeff=1e5*(p['fluid_rho']/1000)*(3600/p['fluid_rho'])**2*m[idx]*torch.sqrt(m[idx]**2+1e-16)
   delta=coeff*(1/kv(opening)**2-1/kv(p['valve_opening'])**2)
  if coupled:theta=setpart(theta,'mms_momentum_Pa',source.index_copy(0,idx,source[idx]+delta))
 y,r=backward_euler_step(eq.problem,prev,theta,time=1.,dt=1.,options=d.newton_options)
 yd=torch.where(eq.differential_mask,y-prev,torch.zeros_like(y))
 diag=eq.diagnostics(y.new_tensor(1.),y,yd,theta)
 tolerance=max(1e-7,256*torch.finfo(y.dtype).eps)*(1+abs(float(diag['heat_input_W'].detach())))
 assert abs(float(diag['energy_balance_W'].detach()))<=tolerance
 obs=y[eq.state_layout['solid_temperature']].mean() if kind=='heat' else y[eq.state_layout['flow']][idx].mean()
 return obs,r,diag
for kind,coupled in [('heat',False),('heat',True),('pump',True),('valve',True)]:
 center=.9 if kind=='valve' else 1.;alpha=torch.tensor(center,dtype=torch.float64,requires_grad=True);obs,r,diag=objective(alpha,kind,coupled);ad=torch.autograd.grad(obs,alpha)[0]
 h=.001
 with torch.no_grad():plus,rp,dp=objective(alpha.detach()+h,kind,coupled);minus,rm,dm=objective(alpha.detach()-h,kind,coupled)
 fd=(plus-minus)/(2*h);rel=float(abs(ad-fd)/(abs(ad)+abs(fd)+1e-12))
 if coupled:
  assert abs(float(ad))<1e-7 and abs(float(fd))<1e-7,(kind,float(ad),float(fd))
 else:assert rel<1e-4 and abs(float(ad))>1e-6,(float(ad),float(fd),rel)
 row=dict(parameter=kind,parameter_scale_center=center,source_dependency='independently regenerated physical-source dependence; constructed target sensitivity zero' if coupled else 'source samples fixed; physical heat changes',observable='mean56solid_K' if kind=='heat' else 'mean_component_flow_kg_s',AD=float(ad),FD=float(fd),central_h=h,relative_error=rel,independent_target=0. if coupled else None,reports=[r.__dict__,rp.__dict__,rm.__dict__],diagnostics={k:float(v.detach()) for k,v in diag.items()})
 rows.append(row);print('SENSITIVITY',kind,coupled,float(ad),float(fd),rel,flush=True)
(T/'results/mms-sensitivities.json').write_text(json.dumps(dict(status='passed',rows=rows,scope='actual original implicit Newton first derivatives, independent physical Pa/W source recipes; fixed-source heat nonzero control',affinity=sorted(os.sched_getaffinity(0)),elapsed_s=time.monotonic()-start),indent=2))
