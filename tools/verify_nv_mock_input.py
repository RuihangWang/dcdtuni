"""Load and validate the generated NV mock DAE input on CPU1; never solve it."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
if hasattr(os,'sched_getaffinity'):
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
if hasattr(os,'getpriority'):
    os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--input',type=Path)
parser.add_argument('--project',type=Path)
parser.add_argument('--report',type=Path)
args=parser.parse_args()
sys.path.insert(0,str(args.repo/'src'))
import torch
torch.set_num_threads(1);torch.set_num_interop_threads(1)
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
input_path=args.input or args.repo/'docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun'
project_path=args.project or args.repo/'src/frontend/react/test/fixtures/cases/NV_PEK02_mock.cfdproj'
raw=json.loads(input_path.read_text())
source_hash=hashlib.sha256(project_path.read_bytes()).hexdigest()
if raw['source_project']['sha256']!=source_hash:
    raise ValueError('Derived input is not bound to this saved cfdproj hash')
loaded=load_dae_input(raw)
graph=loaded.equation_graph.graph
valves=[e for e in raw['edges'] if e['type']=='valve']
if len(valves)!=102 or any(e['params']['opening']!=1 for e in valves):
    raise ValueError('Expected 102 fixed-open authored valves')
if (graph.N_nodes,graph.N_edges,graph.N_hx)!=(316,348,2):
    raise ValueError('Unexpected compiled graph inventory')
theta0=loaded.parameters(0)
theta5=loaded.parameters(5)
if theta0.device.type!='cpu' or theta5.device.type!='cpu':
    raise ValueError('CPU-only verification contract violated')
report={'scope':'strict schema, source hash, graph/equation assembly and parameter schedules only; no solve or gradients run',
    'source_project_sha256':source_hash,'liquidrun_sha256':hashlib.sha256(input_path.read_bytes()).hexdigest(),
    'schema_version':3,'nodes':graph.N_nodes,'edges':graph.N_edges,'heat_exchangers':graph.N_hx,'racks':len(raw['racks']),
    'valves':len(valves),'samples':loaded.sample_count,'dt_s':loaded.dt,'mode':loaded.mode,'initialization':loaded.initialization,
    'parameter_count':theta0.numel(),'parameters_at_load_step_changed':not torch.equal(theta0,theta5),
    'device':str(theta0.device),'dtype':str(theta0.dtype),'torch_threads':torch.get_num_threads(),
    'solver_executed':False,'physics_validation_complete':False}
if not report['parameters_at_load_step_changed']:raise ValueError('Authored load-step schedule was not admitted')
if args.report:
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2),flush=True)
