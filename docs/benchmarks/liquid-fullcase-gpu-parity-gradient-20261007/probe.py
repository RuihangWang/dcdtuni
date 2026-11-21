from pathlib import Path
import json,time,copy,os,sys
import numpy as np
import torch
from backend.liquidcooling.dae_runtime import DAESimulation
R=Path(sys.argv[1]);label=sys.argv[2];device=sys.argv[3];multiplier=float(sys.argv[4]);gradients=bool(int(sys.argv[5]));steps=int(sys.argv[6])
I=R.parents[3]
source=I/'.codex-tmp/dae-controls-root-replay-20261007/gui-response-fullcase/checkpoint.json'
raw=json.loads(source.read_text())['liquidrun'];raw['execution']['device']=device
assert raw['time']=={'dt_s':2.0,'N_t':11,'bdf_order':1} and len(raw['nodes'])==229 and len(raw['edges'])==286
assert raw['fluid']=={'rho_kg_m3':1032.,'cp_J_kgK':3850.,'mu_Pa_s':.0018,'k_W_mK':.48}
assert os.environ['CUDA_VISIBLE_DEVICES']==('GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93' if device=='cuda:0' else '')
if device=='cuda:0':torch.cuda.set_per_process_memory_fraction(256*1024*1024/torch.cuda.get_device_properties(0).total_memory,0)
t=time.monotonic();sim=DAESimulation(raw);x=torch.tensor(multiplier,dtype=torch.float64,device=device,requires_grad=gradients);sim.experiment.set_heat_multiplier(x);states=[sim.experiment.tensor_state]
for i in range(steps):
 sim.step();states.append(sim.experiment.tensor_state);print(json.dumps({'phase':'accepted-step','label':label,'seq':i+1,'wall_s':time.monotonic()-t}),flush=True)
y=sim.experiment.tensor_series('solid_temperature');objectives=torch.stack([y[:,-1].sum(),y[0,-1]])
assert str(y.device)==str(torch.device(device)) and tuple(y.shape)==(56,steps+1)
# Save physical data before adjoint, so timeout cannot discard accepted states.
np.savez_compressed(R/'trajectory.npz',states=torch.stack(states).detach().cpu().numpy(),solid=y.detach().cpu().numpy(),objectives=objectives.detach().cpu().numpy())
(R/'frames.json').write_text(json.dumps(sim.frames,separators=(',',':')))
initial={'label':label,'phase':'forward-completed','wall_s':time.monotonic()-t,'steps':steps,'actual_execution':sim.metadata['execution'],'unknowns':states[0].numel(),'gradient_requested':gradients,'multiplier':multiplier,'diagnostics':sim.latest_frame['diagnostics'],'heat_multipliers':sim.experiment.accepted_heat_multipliers,'gradient':[]}
(R/'forward-completed.json').write_text(json.dumps(initial,indent=2));print(json.dumps(initial),flush=True)
if gradients:
 for j in range(2):
  g,=torch.autograd.grad(objectives[j],x,retain_graph=j==0);assert torch.isfinite(g) and str(g.device)==str(torch.device(device));initial['gradient'].append(g.detach().cpu().item());print(json.dumps({'phase':'gradient-completed','objective':j,'gradient':initial['gradient'][-1],'wall_s':time.monotonic()-t}),flush=True)
initial.update(status='PASS',wall_s=time.monotonic()-t,gradient_device=str(x.device) if gradients else None,cuda_peak_bytes=torch.cuda.max_memory_allocated(0) if device=='cuda:0' else 0)
(R/'result.json').write_text(json.dumps(initial,indent=2));print(json.dumps(initial),flush=True)
