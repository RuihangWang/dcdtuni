"""Compare exact normal-UI output with independently assembled thermal balances.
Never advance a production DAEExperiment; no production residual/JVP calls.
Continuous reference uses each accepted sampled value held across its BE step.
"""
from pathlib import Path
import json,hashlib,sys,time,os
HERE=Path(__file__).resolve().parent
I=HERE.parents[2]
sys.path.insert(0,str(I/'src'));sys.path.insert(0,str(I/'tools'))
import numpy as np
import torch
from scipy.sparse import csc_matrix,bmat
from scipy.sparse.linalg import splu,expm_multiply
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from shinaide_liquid_verification import assemble

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 start=time.monotonic();torch.set_num_threads(2);torch.set_num_interop_threads(1)
 R=HERE.parent/'completed'; identities={k:{'sha256':sha(R/k),'bytes':(R/k).stat().st_size} for k in ['input.liquidrun','executed.liquidrun','result.json','checkpoint.json','executed-inputs.json']}
 identities['assembler']={'sha256':sha(I/'tools/shinaide_liquid_verification.py')}
 raw=json.loads((R/'executed.liquidrun').read_text());result=json.loads((R/'result.json').read_text());cp=json.loads((R/'checkpoint.json').read_text());ledger=json.loads((R/'executed-inputs.json').read_text())
 assert raw['schemaVersion']==4 and raw['events']==[] and raw['controllers']==[]
 n=raw['time']['N_t'];dt=raw['time']['dt_s'];assert n==50 and dt==.5
 assert result['completed'] and result['converged'] and result['status']=='completed' and cp['seq']==n-1 and cp['run_status']=='completed'
 assert result['time_s']==[i*dt for i in range(n)] and ledger['run_id']==result['run_id']==cp['run_id'] and ledger['complete_original_horizon'] and ledger['accepted_sample_count']==n
 assert cp['applied_inputs']==[{'seq':11,'time_s':5.5,'heat_multiplier':1.2}]
 d=load_dae_input(raw);eq=d.equation_graph;layout=eq.state_layout
 assert result['node_ids']==eq.graph.node_ids and result['edge_ids']==eq.graph.edge_str_ids
 assert [x['edge_id'] for x in result['cp_details']]==[eq.graph.edge_str_ids[int(e)] for e in eq._ids['cp']]
 assert result['hx_edge_ids']==[eq.graph.edge_str_ids[int(e)] for e in eq._ids['hx']]
 names={'water_temperature':'cell_temp_K','solid_temperature':'chip_temp_K','hx_secondary_temperature':'secondary_supply_K','primary_return_temperature':'primary_return_K'}
 values={k:np.array(result[v],dtype=np.float64) for k,v in names.items()}
 for k,v in values.items(): assert v.shape==(layout[k].stop-layout[k].start,n) and np.isfinite(v).all()
 flow=np.array(result['edge_flow_kg_s']);assert flow.shape==(len(result['edge_ids']),n)
 state0=np.zeros(eq.size);state0[layout['flow']]=flow[:,0]
 for k,v in values.items():state0[layout[k]]=v[:,0]
 r=assemble(d,state0);observed=np.concatenate(list(values.values()),axis=0).T
 assert result['cell_edge_ids']==[eq.graph.edge_str_ids[int(e)] for e in r['owner']]
 theta=[d.parameters(i).detach().numpy() for i in range(n)];dynamic_mask=np.zeros(eq.parameter_count,dtype=bool)
 for key in ['heat_W','primary_inlet_K','cell_heat_W']:dynamic_mask[eq.parameter_layout[key]]=True
 assert all(np.array_equal(v[~dynamic_mask],theta[0][~dynamic_mask]) for v in theta)
 max_flow_change=float(np.abs(flow-flow[:,[0]]).max());assert max_flow_change<1e-9
 A=csc_matrix(r['A']);L=splu(csc_matrix(np.eye(len(r['x0'])))-dt*A)
 discrete=r['x0'].copy();continuous=r['x0'].copy();discrete_errors=[0.];continuous_errors=[0.];node_errors=[];segments=set()
 for i in range(n):
  actual_nodes=np.array(result['node_temperature_K'])[:,i]
  node_errors.append(float(np.abs(r['S']@observed[i]+r['g']-actual_nodes).max()))
  if i==0:continue
  p={key:theta[i][sl] for key,sl in eq.parameter_layout.items()};b=r['b'].copy()
  b[r['sw']]+=(p['cell_heat_W']-r['p']['cell_heat_W'])/r['cw']
  b[r['ss']]+=(p['heat_W']-r['p']['heat_W'])/r['cs']
  b[r['sp']]+=r['p']['primary_flow_kg_s']*r['p']['primary_cp']/r['cpri']*(p['primary_inlet_K']-r['p']['primary_inlet_K'])
  discrete=L.solve(discrete+dt*b)
  aug=bmat([[A,csc_matrix(b[:,None])],[None,csc_matrix((1,1))]],format='csc')*dt
  continuous=expm_multiply(aug,np.r_[continuous,1.],traceA=float(aug.diagonal().sum()))[:-1]
  discrete_errors.append(float(np.abs(observed[i]-discrete).max()));continuous_errors.append(float(np.abs(observed[i]-continuous).max()));segments.add(b.tobytes())
  if time.monotonic()-start>120:raise TimeoutError('independent oracle 120 s inter-step limit')
 checks={'all_50_actual_samples':len(discrete_errors)==n,'fixed_nonthermal_parameters':True,'fixed_flows':max_flow_change<1e-9,'independent_BE_matches_product':max(discrete_errors)<1e-7,'independent_node_mixing_matches_product':max(node_errors)<1e-7,'finite_continuous_reference':bool(np.isfinite(continuous).all())}
 report={'scope':'Actual normal-UI full graph/grid/online trace. Independent thermal balance assembly and BE linear solve; separate continuous sampled-hold matrix exponential. No DAEExperiment, residual/JVP, shortened trajectory, resampling, source forcing, or hydraulic solve.', 'passed':all(checks.values()),'gates':checks,'run_id':result['run_id'],'input_identities':identities,'N_t':n,'dt_s':dt,'dynamic_thermal_states':len(r['x0']),'original_online_ledger':cp['applied_inputs'],'max_flow_change_kg_s':max_flow_change,'max_independent_BE_error_K':max(discrete_errors),'max_independent_node_mixing_error_K':max(node_errors),'max_continuous_sampled_hold_error_K':max(continuous_errors),'final_continuous_sampled_hold_error_K':continuous_errors[-1],'distinct_affine_forcings':len(segments),'BE_errors_by_sample_K':discrete_errors,'continuous_errors_by_sample_K':continuous_errors,'wall_seconds':time.monotonic()-start}
 (HERE/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n');print(json.dumps({k:report[k] for k in ['passed','N_t','dynamic_thermal_states','max_independent_BE_error_K','max_independent_node_mixing_error_K','max_continuous_sampled_hold_error_K','wall_seconds']}))
 return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
