import os,sys,json,hashlib,time
from pathlib import Path
R=Path(__file__).resolve().parent
sys.path.insert(0,'/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src')
import torch
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
from backend.liquidcooling.runtime import _canonical_hash
from collections import Counter
rawdir=R/'actual/run-e512006bbf0e4c6db16d918b35818867'
orig=json.loads((rawdir/'input.liquidrun').read_text()); accepted=json.loads((rawdir/'executed.liquidrun').read_text()); record=json.loads((rawdir/'result.json').read_text()); checkpoint=json.loads((rawdir/'checkpoint.json').read_text()); request=json.loads((rawdir/'input.json').read_text());receipt=json.loads((rawdir/'executed-inputs.json').read_text())
torch.set_num_threads(2);torch.set_num_interop_threads(1)
assert orig.get('manufactured_benchmark') is None and accepted.get('manufactured_benchmark') is None
assert request['liquidrun']==orig and checkpoint['liquidrun']==orig
assert request['config_hash']==record['config_hash']==checkpoint['config_hash']==receipt['original_config_hash']
assert record['completed'] and record['converged'] and record['status']=='completed'
assert orig['nodes']==accepted['nodes'] and [e['id'] for e in orig['edges']]==[e['id'] for e in accepted['edges']]
assert receipt['accepted_sample_count']==11 and receipt['complete_original_horizon']
t=time.time();definition=admit_dae_runtime_input(accepted);eq=definition.equation_graph;g=eq.graph
assert definition.manifest.manufactured_benchmark is None and definition._manufactured_sources is None
pumpids=[g.edge_str_ids[i] for i in g.pump_indices.edge_ids.tolist()];valveids=[g.edge_str_ids[i] for i in g.valve_indices.edge_ids.tolist()];cpids=[g.edge_str_ids[i] for i in g.cp_indices.edge_ids.tolist()]
assert len(orig['nodes'])==229 and len(orig['edges'])==286 and len(cpids)==56 and len(valveids)==14
out={'scope':'actual unforced GUI completed transient and actual accepted replay manifest','input_source_project':orig['source_project'],'requestConfig':request['config_hash'],'acceptedConfigDeclared':receipt['executed_config_hash'],'acceptedConfigTyped':_canonical_hash(definition.manifest.model_dump(by_alias=True,mode='json',exclude_none=True),[]),'load_seconds':time.time()-t,'graph':{'nodes':len(g.node_ids),'edges':len(g.edge_str_ids),'coldplates':len(cpids),'valves':len(valveids),'pumpids':pumpids,'valveids':valveids,'cpids':cpids,'size':eq.size,'state_layout':{k:[v.start,v.stop] for k,v in eq.state_layout.items()},'parameter_layout':{k:[v.start,v.stop] for k,v in eq.parameter_layout.items()},'differential_states':int(eq.differential_mask.sum()),'steady_neutral_storage':int(eq.steady_neutral_storage_mask.sum())},'newton':vars(definition.newton_options),'accepted_heat_multipliers':checkpoint['heat_multipliers'],'accepted_pump_rpm_ratios':checkpoint['pump_rpm_ratios'],'accepted_valve_openings':checkpoint['valve_openings'],'accepted_primary_inlet_K':checkpoint['primary_inlet_K'],'source_forcing':None,'frames':record['time_s']}
(R/'preflight.json').write_text(json.dumps(out,indent=2));print(json.dumps({k:v for k,v in out.items() if k not in ['graph','accepted_valve_openings','accepted_pump_rpm_ratios','accepted_primary_inlet_K']},indent=2));print('GRAPH',out['graph']['nodes'],out['graph']['edges'],out['graph']['coldplates'],out['graph']['valves'],'size',eq.size,'differential',out['graph']['differential_states'],'neutral',out['graph']['steady_neutral_storage']);print('PUMP',pumpids,'FIRSTVALVE',valveids[0])
