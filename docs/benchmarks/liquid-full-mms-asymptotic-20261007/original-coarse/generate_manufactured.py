from pathlib import Path
import sys,os,json,numpy as np,torch
T=Path(__file__).resolve().parent;sys.path.insert(0,str(T/'after/src'))
torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
from backend.liquidcooling.manufactured_benchmark import canonical_digest,graph_layout,compiled_ids
from independent_trajectory import IndependentTrajectory
raw=json.loads((T/'results/after-transient-base.liquidrun').read_text());d=admit_dae_runtime_input(raw)
eq=d.equation_graph;ref=IndependentTrajectory(eq,d.parameters(0));ids=compiled_ids(eq)
imap={'node_pressure_Pa':'nodes','flow_kg_s':'edges','node_temperature_K':'nodes','water_temperature_K':'water','solid_temperature_K':'solid','hx_secondary_K':'hx','hx_primary_K':'hx'}
smap={'momentum_Pa':'edges','water_W':'water','solid_W':'solid','hx_secondary_W':'hx','hx_primary_W':'hx'}
state,rate,source,audit=ref.evaluate(0.)
vector=lambda name,value:{'ids':ids[imap[name]],'values':value.tolist()}
probe=[];np.savez(T/'results/independent-cycle-construction.npz',B=ref.B,C=ref.C,a0=ref.a0,amplitude=ref.amplitude,phase=ref.phase,xbase=ref.xbase,xamplitude=ref.xamplitude,xphase=ref.xphase)
for t in [0.,.37,1.23,2.71,4.19,7.83,8.]:
 st,rt,so,au=ref.evaluate(t);assert au['all_node_mass_inf']<1e-12;assert au['node_mixing_inf_W']<1e-8
 probe.append({'t':t,'audit':au,'state':{k:v.tolist() for k,v in st.items()},'temperature_rate':{k:v.tolist() for k,v in rt.items()},'sources':{k:v.tolist() for k,v in so.items()}})
(T/'results/independent-probe-trajectories.json').write_text(json.dumps(probe,indent=2))
for dt in (2.,1.,.5,.25):
 times=[i*dt for i in range(round(8/dt)+1)];targets=[ref.evaluate(t) for t in times]
 benchmark={'format':'dcdtuni-manufactured-benchmark','version':1,'provenance':'synthetic-research','formula_id':'cycle-storage-sinusoidal-20261006-v1','source_reference':'Independent NumPy constructive cycle basis, component laws, storage trajectories and node mixing; no product residual or normal DAE output used to manufacture forcing.','graph_layout_sha256':canonical_digest(graph_layout(raw)),'units':{'pressure':'Pa','temperature':'K','mass_flow':'kg/s','power':'W','time':'s','temperature_rate':'K/s'},'sampling':'endpoint_hold','times_s':times,'sources':{name:{'ids':ids[key],'values':np.stack([v[2][name] for v in targets],axis=1).tolist()} for name,key in smap.items()},'initial_state':{name:vector(name,value) for name,value in state.items()},'initial_temperature_rate':{name:vector(name,value) for name,value in rate.items()},'flow_signs':{'ids':ids['edges'],'values':np.sign(state['flow_kg_s']).tolist()}}
 benchmark['source_sha256']=canonical_digest(benchmark)
 p=json.loads((T/'inputs/transient-base.cfdproj').read_text());ex=p['runDefaults']['liquid']['experiment']
 ex['dt_s']=dt;ex['duration_s']=8;ex['dae']['analysis']={'mode':'transient','initialization':'consistent'};ex['dae']['manufactured_benchmark']=benchmark
 p['transient']['gridS']=dt;p['transient']['durationS']=8
 name=str(dt).replace('.','p')
 (T/f'inputs/manufactured-dt-{name}.cfdproj').write_text(json.dumps(p,indent=2))
 np.savez(T/f'results/target-dt-{name}.npz',times=times,**{k:np.stack([v[0][k] for v in targets]) for k in state})
 print('generated',dt,len(times),benchmark['source_sha256'],flush=True)
(T/'results/manufacturing-summary.json').write_text(json.dumps({'counts':ref.counts,'exact_integer_BC_zero':bool(np.all(ref.B@ref.C==0)),'zero_mass_sources':True,'affinity':sorted(os.sched_getaffinity(0)),'cuda':os.environ.get('CUDA_VISIBLE_DEVICES'),'probe_summary':[{'t':v['t'],**v['audit']} for v in probe],'source_generation_called_product_residual':False,'source_generation_called_newton_or_normal_solution':False},indent=2))
