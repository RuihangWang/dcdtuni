from pathlib import Path
import json,hashlib,time,copy,os,gc
import numpy as np
import torch
from backend.liquidcooling.dae_runtime import DAESimulation
R=Path(__file__).resolve().parent
I=R.parents[1]
source=I/'.codex-tmp/dae-controls-root-replay-20261007/gui-response-fullcase/checkpoint.json'
cp=json.loads(source.read_text());raw=cp['liquidrun']
assert raw['analysis']=={'mode':'transient','initialization':'steady'}
assert raw['time']=={'dt_s':2.0,'N_t':11,'bdf_order':1}
assert len(raw['nodes'])==229 and len(raw['edges'])==286
assert raw['fluid']['rho']==1032.0 and raw['fluid']['cp']==3850.0
assert os.environ['CUDA_VISIBLE_DEVICES']=='GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93'
arrays={}; receipts=[]
def run(label,device,multiplier,gradient):
    started=time.monotonic();input=copy.deepcopy(raw)
    input['execution']['device']=device
    sim=DAESimulation(input)
    x=torch.tensor(multiplier,dtype=torch.float64,device=device,requires_grad=gradient)
    sim.experiment.set_heat_multiplier(x)
    states=[sim.experiment.tensor_state]
    while not sim.done:
        sim.step();states.append(sim.experiment.tensor_state)
        print(json.dumps({'phase':'accepted-step','probe':label,'seq':len(states)-1,'seconds':time.monotonic()-started}),flush=True)
    series=sim.experiment.tensor_series('solid_temperature')
    assert tuple(series.shape)==(56,11) and str(series.dtype)=='torch.float64'
    assert str(series.device)==str(torch.device(device))
    objectives=torch.stack((series[:,-1].sum(),series[0,-1]))
    grad=[]
    if gradient:
        for j in range(2):
            g,=torch.autograd.grad(objectives[j],x,retain_graph=j==0)
            assert str(g.device)==str(torch.device(device)) and torch.isfinite(g)
            grad.append(g.detach().cpu().item())
    arrays[label+'_states']=torch.stack(states).detach().cpu().numpy()
    arrays[label+'_solid']=series.detach().cpu().numpy()
    arrays[label+'_objectives']=objectives.detach().cpu().numpy()
    receipt={'probe':label,'seconds':time.monotonic()-started,'actual_execution':sim.metadata['execution'],'gradient_device':str(x.device) if gradient else None,'gradient':grad,'unknowns':states[0].numel(),'frames':len(states),'final_seq':sim.experiment.seq,'heat_multipliers':sim.experiment.accepted_heat_multipliers,'diagnostics':sim.latest_frame['diagnostics']}
    receipts.append(receipt)
    print(json.dumps({'phase':'closed-probe',**receipt}),flush=True)
    del sim,states,series,x,objectives
    gc.collect()
    return grad
cpu_grad=np.array(run('cpu','cpu',1.2,True))
# The only CUDA context created by this process is on the approved physical GPU 0 UUID.
torch.cuda.set_per_process_memory_fraction(256*1024*1024/torch.cuda.get_device_properties(0).total_memory,0)
gpu_grad=np.array(run('gpu','cuda:0',1.2,True))
peak=torch.cuda.max_memory_allocated(0)
assert peak<256*1024*1024
np.testing.assert_allclose(arrays['cpu_states'],arrays['gpu_states'],rtol=1e-10,atol=1e-7)
np.testing.assert_allclose(cpu_grad,gpu_grad,rtol=1e-8,atol=1e-7)
fd=[]
for eps in [1e-3,5e-4]:
    for sign in [-1,1]:run('fd_'+str(eps)+'_'+str(sign),'cpu',1.2+sign*eps,False)
    numerical=(arrays['fd_'+str(eps)+'_1_objectives']-arrays['fd_'+str(eps)+'_-1_objectives'])/(2*eps)
    np.testing.assert_allclose(cpu_grad,numerical,rtol=1e-5,atol=1e-5)
    fd.append({'eps':eps,'finite_difference':numerical.tolist(),'adjoint':cpu_grad.tolist(),'absolute_difference':abs(numerical-cpu_grad).tolist(),'relative_difference':(abs(numerical-cpu_grad)/abs(cpu_grad)).tolist()})
np.savez_compressed(R/'trajectories.npz',**arrays)
result={'status':'PASS','input_checkpoint_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'held_input_except_execution':True,'physical_gpu_uuid':os.environ['CUDA_VISIBLE_DEVICES'],'cuda_peak_allocated_bytes':peak,'cpu_gpu_max_state_difference':float(abs(arrays['cpu_states']-arrays['gpu_states']).max()),'cpu_gpu_gradient_difference':abs(cpu_grad-gpu_grad).tolist(),'finite_difference':fd,'probes':receipts,'scope':'full physical engineering graph and GUI input; tensor online heat derivative, not JSON compiler derivative; synthetic component coefficients remain uncalibrated'}
(R/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps(result),flush=True)
