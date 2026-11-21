import ast,json,os,sys,hashlib
from pathlib import Path
import torch
from agent._runtime import REPO_ROOT
from agent.training.shinaide_six_family_numeric_head_v1 import compose_target_score
from agent.training.mfpg_ppo_formal_supervisor_v2 import write
source=REPO_ROOT/'src/agent/training/shinaide_language_validation_worker_v4.py'
assert hashlib.sha256(source.read_bytes()).hexdigest()=='2c7685677887f446162711ca57eacc92cd4c3d26a464f1787ff323cf24b7644d'
assert sorted(os.sched_getaffinity(0))==[18,19] and os.environ.get('CUDA_VISIBLE_DEVICES')==''
run=(REPO_ROOT/sys.argv[1]).resolve();assert run.is_relative_to(REPO_ROOT/'_tmp');run.mkdir();directory=run/'attempt-00';directory.mkdir()
tree=ast.parse(source.read_text());execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute');loop=next(n for n in ast.walk(execute) if isinstance(n,ast.For) and n.lineno==158)
function=ast.FunctionDef(name='pure_evidence_control_flow',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[n for n in loop.body if 169<=n.lineno<=183],decorator_list=[])
grad=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gradients');module=ast.fix_missing_locations(ast.Module(body=[grad,function],type_ignores=[]))
torch.set_num_threads(2);torch.manual_seed(20261012);hook=torch.nn.Linear(4,2,dtype=torch.float64);parameter=hook.weight;prefix=parameter.sum()*.1
first={'prefix_log_density':prefix,'token_log_densities':torch.stack([prefix]),'last_hidden':torch.zeros(4,dtype=torch.float64),'sampling_replay_prefix_logdensity_abs':.006,'sampling_replay_logprob_max_abs':.006}
class Trace:
 def identity(self):return {'format':'pure-recording-fixture','steps':[{'token_id':1}],'actual_model_forward':False}
trace=Trace();write(directory/'trace.json',trace.identity())
def raw_numeric(*a,**kw):return {'latent':torch.tensor([.25],dtype=torch.float64),'free_values':torch.tensor([20.],dtype=torch.float64),'full_values':torch.tensor([20.],dtype=torch.float64),'geometry_log_density':parameter.sum()*.2,'conditional_control_log_density':parameter.sum()*.3,'free_names':['toy'],'fixed_names':[],'action':{'toy':20.}}
env=dict(torch=torch,REPO_ROOT=REPO_ROOT,save=write,trace=trace,teacher=False,idx=0,item={'index':0,'mode':'engine_admitted'},directory=directory,run=run,rows=[],criteria={'per_sample_joint_log_density_max_abs':.005},hook=hook,projectors=None,gen=None,compose_target_score=compose_target_score,raw_numeric=raw_numeric,teacher_result=lambda *a:(first,object(),'engine_admitted',None))
exec(compile(module,str(source)+'::AST169-183-pure-recording-fixture','exec'),env)
try:env['pure_evidence_control_flow']();raise AssertionError('gate failed to reject')
except ValueError as exc:assert str(exc)=='behavior/fullteacher prefix likelihood gate'
persisted=json.loads((run/'attempts.json').read_text())[0];resident=env['rows'][0]
assert 'complete_numeric_sample' not in persisted and 'status' not in persisted and resident['complete_numeric_sample']['latent']==[.25] and not (directory/'validation.json').exists()
print(json.dumps({'reproduced':True,'missing_fields':sorted(set(resident)-set(persisted)),'actual_model_weights_forward_GPU_PDE_train':0}))
