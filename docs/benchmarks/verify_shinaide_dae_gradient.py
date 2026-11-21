"""Bounded CPU check of the NEW common DAE adjoint, not the split baseline."""
import copy,hashlib,json,os,resource,signal,sys,time
from pathlib import Path

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'src'))
for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[name]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))
signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(TimeoutError('60-second DAE gradient budget')))
signal.alarm(60)
import torch
torch.set_num_threads(1);torch.set_num_interop_threads(1)
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.dae_experiment import DAEExperiment
from backend.liquidcooling.solver.dae_newton import solve_steady

fixture=root/'docs/benchmarks/fixtures/shinaide-dae-v3-mock.liquidrun'
raw=json.loads(fixture.read_text());raw['events']=[];raw['time'].update(dt_s=5.,N_t=4)
# The source has scalar schedules. This validation horizon replaces its events
# explicitly; source project and source fixture remain read-only.
started=time.perf_counter()


def trajectory(alpha,beta):
    definition=load_dae_input(raw);base_parameters=definition.parameters
    layout=definition.equation_graph.parameter_layout
    def parameters(index,heat_multiplier=1.):
        theta=base_parameters(index,heat_multiplier=heat_multiplier)
        if index:
            parts=[]
            for name,sl in layout.items():
                part=theta[sl]
                if name=='pump_rpm_ratio':part=part*beta
                parts.append(part)
            theta=torch.cat(parts)
        return theta
    definition.parameters=parameters
    run=DAEExperiment(definition);run.set_heat_multiplier(alpha)
    while not run.done:run.step()
    loss=definition.equation_graph.unpack(run.tensor_state)['solid_temperature'].mean()
    return loss,run

alpha=torch.tensor(1.2,dtype=torch.float64,requires_grad=True)
beta=torch.tensor(.9,dtype=torch.float64,requires_grad=True)
a=time.perf_counter();loss,run=trajectory(alpha,beta);forward=time.perf_counter()-a
a=time.perf_counter();da,db=torch.autograd.grad(loss,(alpha,beta));backward=time.perf_counter()-a
h=1e-4
fd_a=(trajectory(1.2+h,.9)[0]-trajectory(1.2-h,.9)[0])/(2*h)
fd_b=(trajectory(1.2,.9+h)[0]-trajectory(1.2,.9-h)[0])/(2*h)
relative=lambda x,y:abs(float(x-y))/max(1e-8,abs(float(y)))
for actual,fd in ((da,fd_a),(db,fd_b)):
    if not bool(torch.isfinite(actual)) or relative(actual,fd)>1e-4:raise RuntimeError('DAE full-trajectory gradient failed central difference check')
# Separately test direct steady solve against exact total-power balance.
eq=run.equation_graph;scale=torch.tensor(1.2,dtype=torch.float64,requires_grad=True)
theta=run.definition.parameters(0,heat_multiplier=scale)
state,_=solve_steady(eq.problem,run.tensor_state.detach(),theta,
    conserved=(eq.steady_neutral_storage_mask,run.tensor_state.detach()))
primary=eq.unpack(state)['primary_return_temperature'].sum()
steady_grad,=torch.autograd.grad(primary,scale)
p=run.definition.manifest.primary_circuits[0]
exact=21000./(p.flow_kg_s*p.cp_J_kgK)
if abs(float(steady_grad)-exact)>1e-7:raise RuntimeError('primary-return steady gradient violates energy balance')
report={'status':'passed','scope':'new unified Newton DAE, full 3-step trajectory on Shinaide geometry; mock parameters, no NV calibration',
    'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
    'source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in (
        'src/backend/liquidcooling/solver/dae_newton.py','src/backend/liquidcooling/solver/equation_graph.py',
        'src/backend/liquidcooling/dae_experiment.py','src/backend/liquidcooling/liquidrun_dae_schema.py')},
    'affinity':sorted(os.sched_getaffinity(0)),'nice':os.getpriority(os.PRIO_PROCESS,0),
    'torch_threads':torch.get_num_threads(),'cuda_visible_devices':os.environ['CUDA_VISIBLE_DEVICES'],
    'variables':eq.size,'dt_s':5.,'steps':3,'objective':'mean cold-plate solid temperature in K at t=15 s',
    'baseline_liquid_heat_W':21000.,'heat_multiplier':1.2,'pump_multiplier':.9,'central_difference_h':h,
    'heat_gradient':{'AD':float(da),'FD':float(fd_a),'relative_error':relative(da,fd_a)},
    'pump_gradient':{'AD':float(db),'FD':float(fd_b),'relative_error':relative(db,fd_b)},
    'primary_steady_gradient_K':{'AD':float(steady_grad),'analytic':exact},
    'walltime_seconds':{'forward':forward,'backward':backward,'total':time.perf_counter()-started},
    'peak_rss_MiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.}
output=Path(__file__).with_name('shinaide-dae-gradient-2026-09-26.json')
output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n');print(json.dumps(report),flush=True)
