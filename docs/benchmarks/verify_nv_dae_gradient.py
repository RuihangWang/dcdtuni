"""NV mock trajectory gradients: independent central differences, CPU only.

The original .cfdproj and derived fixture are read-only. This experiment
explicitly replaces their planned event with three trajectory input scales.
It verifies numerical differentiation, not device identification or calibration.
"""
import hashlib,json,os,resource,signal,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']='';os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))

def deadline(*_):raise TimeoutError('60-second NV gradient budget exhausted')
signal.signal(signal.SIGALRM,deadline);signal.alarm(60)
import torch
torch.set_num_threads(1);torch.set_num_interop_threads(1)
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.dae_experiment import DAEExperiment
fixture=root/'docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun'
raw=json.loads(fixture.read_text());raw['events']=[];raw['time'].update(dt_s=5.,N_t=4)
started=time.perf_counter()

def trajectory(scales):
    definition=load_dae_input(raw);base=definition.parameters;eq=definition.equation_graph
    def parameters(index,heat_multiplier=1.):
        theta=base(index,heat_multiplier=heat_multiplier)
        if index:
            parts=[]
            for name,sl in eq.parameter_layout.items():
                value=theta[sl]
                if name=='pump_rpm_ratio':value=value*scales[1]
                if name=='valve_opening':value=value*scales[2]
                parts.append(value)
            theta=torch.cat(parts)
        return theta
    definition.parameters=parameters
    run=DAEExperiment(definition,record_frames=False);run.set_heat_multiplier(scales[0])
    while not run.done:run.step()
    state=eq.unpack(run.tensor_state)
    target=state['primary_return_temperature'].mean()+.1*state['solid_temperature'].mean()
    return target,run

scales=torch.tensor([1.2,.9,.8],dtype=torch.float64,requires_grad=True)
begin=time.perf_counter();objective,run=trajectory(scales);forward=time.perf_counter()-begin
begin=time.perf_counter();gradient,=torch.autograd.grad(objective,scales);backward=time.perf_counter()-begin
h=1e-4;checks={}
for i,name in enumerate(('heat','pump_speed','valve_opening')):
    plus=scales.detach().clone();minus=plus.clone();plus[i]+=h;minus[i]-=h
    fd=(trajectory(plus)[0]-trajectory(minus)[0])/(2*h)
    rel=float((gradient[i]-fd).abs()/fd.abs().clamp_min(1e-8))
    checks[name]={'AD':float(gradient[i]),'FD':float(fd),'relative_error':rel}
    if not bool(torch.isfinite(fd)) or rel>1e-4:raise RuntimeError(f'{name} trajectory gradient failed: {checks[name]}')
eq=run.equation_graph;fields=eq.unpack(run.tensor_state)
last=run._states[-1];previous=run._states[-2];theta=run._parameters[-1]
rates=torch.where(eq.differential_mask,(last-previous)/5.,torch.zeros_like(last))
diagnostics={k:float(v.detach()) for k,v in eq.diagnostics(last.new_tensor(15.),last,rates,theta).items()}
if abs(diagnostics['energy_balance_W'])>1e-4:raise RuntimeError('coupled dynamic controls failed conservation')
report={'status':'passed','scope':'synthetic 34-rack NV inventory mock; numerical differentiation only',
 'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
 'source_sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
 'src/backend/liquidcooling/solver/dae_newton.py','src/backend/liquidcooling/solver/equation_graph.py',
 'src/backend/liquidcooling/dae_experiment.py','src/backend/liquidcooling/liquidrun_dae_schema.py')},
 'variables':eq.size,'parameters':eq.parameter_count,'dt_s':5.,'steps':3,
 'scales':scales.detach().tolist(),'input_policy':'initial equilibrium uses original coefficients; heat x1.2, all pumps x0.9 and all open valves x0.8 from the next accepted step; original 5s heat event replaced explicitly',
 'objective':'mean primary-return K + 0.1 * mean equivalent rack solid K at t=15s',
 'finite_difference_h':h,'checks':checks,'last_diagnostics':diagnostics,
 'initial_primary_return_K':eq.unpack(run._states[0])['primary_return_temperature'].detach().tolist(),
 'final_primary_return_K':fields['primary_return_temperature'].detach().tolist(),
 'initial_pump_flow_kg_s':eq.unpack(run._states[0])['flow'][eq.graph.pump_indices.edge_ids].detach().tolist(),
 'final_pump_flow_kg_s':fields['flow'][eq.graph.pump_indices.edge_ids].detach().tolist(),
 'walltime_seconds':{'forward':forward,'backward':backward,'total':time.perf_counter()-started},
 'peak_rss_MiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
 'cpu_affinity':sorted(os.sched_getaffinity(0)),'threads':torch.get_num_threads(),'cuda_validation':'not_run'}
Path(__file__).with_name('nv-dae-gradient-2026-09-26.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report),flush=True)
