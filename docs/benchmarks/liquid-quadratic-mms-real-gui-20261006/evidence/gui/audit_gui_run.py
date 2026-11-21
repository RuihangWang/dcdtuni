from pathlib import Path
import os,sys,json,hashlib,shutil,copy,time,collections
import numpy as np
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
F=B/'.codex-tmp/full-mms-quadratic-product-private-20261006/FROZEN-v1'
O=B/'.codex-tmp/quadratic-gui-artifact-review-private-20261006'
tag,rid=sys.argv[1:3];dts={'2p0':2.,'1p0':1.,'0p5':.5,'0p25':.25};dt=dts[tag]
D=B/'.codex-tmp/full-mms-quadratic-design-private-20261006/proposed-lowflow-cases'/('dt-'+str(dt))/'Liquid/log'/('run-'+rid)
assert O.resolve().is_relative_to(B) and D.resolve().is_relative_to(B)
r0=json.loads((D/'result.json').read_text());cp0=json.loads((D/'checkpoint.json').read_text())
assert r0['status']=='completed' and r0['completed'] and cp0['run_status']=='completed' and cp0['run_id']==rid
R=O/('run-'+tag+'-'+rid);R.mkdir(parents=True,exist_ok=False)
copyrows=[]
for name in ['input.json','input.liquidrun','checkpoint.json','result.json','executed.liquidrun','executed-inputs.json']:
 src=D/name;before=src.read_bytes();(R/name).write_bytes(before);after=src.read_bytes()
 assert before==after;copyrows.append({'path':str(src),'private_copy':name,'bytes':len(before),'sha256':hashlib.sha256(before).hexdigest(),'source_stable_before_after_copy':True})
(R/'artifact-copy-receipt.json').write_text(json.dumps(copyrows,indent=2))
wrapper=json.loads((R/'input.json').read_text());raw=wrapper['liquidrun'];cp=json.loads((R/'checkpoint.json').read_text());result=json.loads((R/'result.json').read_text());executed=json.loads((R/'executed.liquidrun').read_text());executedmeta=json.loads((R/'executed-inputs.json').read_text());standalone=json.loads((R/'input.liquidrun').read_text())
assert standalone==raw==cp['liquidrun'] and wrapper['controllers']==cp['controllers']==[] and result['run_id']==rid
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
actualconfig=digest({'liquidrun':raw,'controllers':[]})
assert actualconfig==wrapper['config_hash']==cp['config_hash']==result['config_hash']
frozenraw=json.loads((F/f'results/compiled-dt-{tag}.liquidrun').read_text());frozencp=json.loads((F/f'results/checkpoint-{tag}.json').read_text())
def differences(a,b,path='$'):
 out=[]
 if isinstance(a,dict) and isinstance(b,dict):
  for k in sorted(set(a)|set(b)):
   if k not in a or k not in b:out.append({'path':path+'.'+k,'kind':'missing','actual_present':k in a,'frozen_present':k in b,'actual':a.get(k),'frozen':b.get(k)})
   else:out.extend(differences(a[k],b[k],path+'.'+k))
 elif isinstance(a,list) and isinstance(b,list):
  if len(a)!=len(b):out.append({'path':path,'kind':'length','actual':len(a),'frozen':len(b)})
  else:
   for i,(aa,bb) in enumerate(zip(a,b)):out.extend(differences(aa,bb,path+'['+str(i)+']'))
 elif type(a)!=type(b) or a!=b:out.append({'path':path,'kind':'type' if a==b else 'value','actual':a,'frozen':b,'actual_type':type(a).__name__,'frozen_type':type(b).__name__})
 return out
emissiondiff=differences(raw,frozenraw);typeddiff=differences(raw,frozencp['liquidrun'])
assert len(typeddiff)==1 and typeddiff[0]['path']=='$.source_project.sha256'
assert all(q['kind']=='type' or q['path']=='$.source_project.sha256' or q['kind']=='missing' and (q['path'].endswith('.description') and q['actual']=='' or q['path'].endswith('.slot_u') and q['actual']==0 or q['path'].endswith('.span_u') and q['actual']==1) for q in emissiondiff)
(R/'full-input-difference-classification.json').write_text(json.dumps({'actual_vs_frozen_emitted':emissiondiff,'actual_vs_frozen_admitted':typeddiff},indent=2))
bench=copy.deepcopy(raw['manufactured_benchmark']);sourcesha=bench.pop('source_sha256')
assert digest(bench)==sourcesha
assert raw['manufactured_benchmark']==frozencp['liquidrun']['manufactured_benchmark']
assert raw['time']==frozencp['liquidrun']['time'] and raw['analysis']=={'mode':'transient','initialization':'consistent'}
assert raw['time']['dt_s']==dt and raw['time']['N_t']==int(8/dt)+1
assert cp['seq']==raw['time']['N_t']-1 and cp['version']==3
assert executed['manufactured_benchmark']==raw['manufactured_benchmark'] and executed['source_project']==raw['source_project']
executedsha=digest({'liquidrun':executed,'controllers':[]})
assert executedmeta['executed_config_hash']==executedsha and executedmeta['original_config_hash']==actualconfig
assert executedmeta['accepted_sample_count']==executedmeta['original_sample_count']==raw['time']['N_t']
assert executedmeta['accepted_time_s']==8. and executedmeta['complete_original_horizon']
# Load the already-reviewed independent equation assembly only, without any product import.
review=B/'.codex-tmp/quadratic-mms-root-review-private-20261006/FROZEN-v1/independent_audit.py'
indsource=review.read_text().split('runs=[]')[0];(R/'independent_equations_used.py').write_text(indsource)
G={};exec(compile(indsource,str(R/'independent_equations_used.py'),'exec'),G)
N,NW,NC,NH=[G[k] for k in ['N','NW','NC','NH']];Dmatrix,C,Q=[G[k] for k in ['D','C','Q']]
base,slope,curvature=[G[k] for k in ['b','v','a']];p=G['p'];h=G['h'];m=G['m'];times=np.array(result['time_s']);assert np.array_equal(times,np.arange(raw['time']['N_t'])*dt)
mapping={'node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temp_K','solid_temperature_K':'chip_temp_K','hx_secondary_K':'secondary_supply_K','hx_primary_K':'primary_return_K'}
allT=np.concatenate([np.array(result[field]).T for field in mapping.values()],axis=1)
reference=base[None,:]+times[:,None]*slope[None,:]+times[:,None]**2*curvature[None,:]
lu=G['splu'](Dmatrix+G['diags'](C/dt));error=np.zeros_like(reference)
for i in range(1,len(times)):error[i]=lu.solve(C/dt*error[i-1]+C*curvature*dt)
predictedT=reference+error
predictedP=h['pressure0_Pa'][None,:]+times[:,None]**2*h['pressure_curvature_Pa_per_s2'][None,:]
predictedM=np.repeat(m[None,:],len(times),axis=0);up=G['up'];eff=G['eff'];fr=G['fr'];to=G['to']
predictedOut=predictedT[:,:N][:,up].copy()
for e,cells in G['pipe_cells'].items():predictedOut[:,e]=predictedT[:,N+(cells[-1] if m[e]>=0 else cells[0])]
for i,e in enumerate(G['groups']['cold_plate']):predictedOut[:,e]=predictedT[:,up[e]]+eff[i]*(predictedT[:,N+NW+i]-predictedT[:,up[e]])
for i,e in enumerate(G['groups']['heat_exchanger']):predictedOut[:,e]=predictedT[:,N+NW+NC+i]
expected={'node_pressure_Pa':predictedP,'edge_flow_kg_s':predictedM,'node_temperature_K':predictedT[:,:N],'cell_temp_K':predictedT[:,N:N+NW],'chip_temp_K':predictedT[:,N+NW:N+NW+NC],'secondary_supply_K':predictedT[:,N+NW+NC:N+NW+NC+NH],'primary_return_K':predictedT[:,-NH:],'edge_outlet_temp_K':predictedOut}
independent_delta={field:float(np.max(np.abs(np.array(result[field]).T-values))) for field,values in expected.items()}
frozenframes=json.loads((F/f'results/raw-public-frames-{tag}.json').read_text());frozenfieldmap={'cell_temp_K':'cell_temperature_K'}
same_frozen={field:bool(np.array_equal(np.array(result[field]).T.view(np.uint64),np.array([f[frozenfieldmap.get(field,field)] for f in frozenframes]).view(np.uint64))) for field in expected}
forcing=np.zeros_like(allT);sourceslices={'water_W':slice(N,N+NW),'solid_W':slice(N+NW,N+NW+NC),'hx_secondary_W':slice(N+NW+NC,N+NW+NC+NH),'hx_primary_W':slice(-NH,None)}
for name,sl in sourceslices.items():forcing[:,sl]=np.array(raw['manufactured_benchmark']['sources'][name]['values']).T
energy=[]
for i in range(len(times)):
 td=slope if i==0 else (allT[i]-allT[i-1])/dt
 reject=np.sum(p['primary_flow_kg_s']*p['primary_cp']*(allT[i,-NH:]-p['primary_inlet_K']))
 energy.append(float(Q[N:-NH].sum()+forcing[i,N:].sum()-np.dot(C,td)-reject))
energygate=max(abs(balance)/(max(1e-7,256*np.finfo(float).eps)*(1+abs(diag['heat_input_W']))) for balance,diag in zip(energy,result['diagnostics']))
assert energygate<=1.
print('COPIED_VERIFIED_START_REPLAY',tag,rid,actualconfig,'source',raw['source_project']['sha256'],flush=True)
# Same actual backend .py implementations as current IT; pin replay against original frozen public source.
backenddrift=[]
S=F/'source/src/backend/liquidcooling';T=B/'.codex-tmp/goal-20261005/integration/src/backend/liquidcooling'
for sourcefile in S.rglob('*.py'):
 live=T/sourcefile.relative_to(S)
 if not live.exists() or hashlib.sha256(sourcefile.read_bytes()).digest()!=hashlib.sha256(live.read_bytes()).digest():backenddrift.append(str(sourcefile.relative_to(S)))
assert not backenddrift
sys.path.insert(0,str(F/'source/src'));import torch
torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import DAESimulation,admit_dae_runtime_input
start=time.perf_counter();sim=DAESimulation.from_checkpoint(raw,cp);replayed=sim.result()
assert sim.run_id==rid and sim.config_hash==actualconfig and sim.done
replay_bits={field:bool(np.array_equal(np.asarray(result[field],dtype=np.float64).view(np.uint64),np.asarray(replayed[field],dtype=np.float64).view(np.uint64))) for field in expected}
assert all(replay_bits.values()) and np.array_equal(np.array(result['time_s']),np.array(replayed['time_s']))
accepted_admit=admit_dae_runtime_input(executed);assert accepted_admit.sample_count==raw['time']['N_t']
# Exact declared forcing and initial state remained part of both original and completed executed manifests.
summary={'tag':tag,'run_id':rid,'completed_status_from_actual_artifacts':True,'original_artifact_directory':str(D),'actual_config_hash':actualconfig,'actual_source_project_sha':raw['source_project']['sha256'],'frozen_source_project_sha':frozenraw['source_project']['sha256'],'frozen_config_hash':frozencp['config_hash'],'frozen_emission_diff_counts':dict(collections.Counter(x['kind'] for x in emissiondiff)),'typed_input_diff_only_actual_source_identity':typeddiff,'raw_and_wrapper_input_comparison':'input.json unwrapped liquidrun, equal input.liquidrun and checkpoint liquidrun; typed defaults are not byte identity','source_benchmark_sha_independently_verified':sourcesha,'dt_s':dt,'frames':len(times),'explicit_manufactured_IC_and_rates_unchanged':True,'analysis_and_physical_and_forcing_typed_equal_frozen':True,'controllers':[],'all_eight_arrays_vs_independent_prediction':independent_delta,'all_eight_arrays_bitexact_frozen_numeric_baseline':same_frozen,'original_energy_gate_max_ratio':energygate,'independent_energy_balance_inf_W':max(abs(x) for x in energy),'all_eight_arrays_public_checkpoint_replay_bitexact':replay_bits,'checkpoint_version':3,'public_replay_elapsed_s':time.perf_counter()-start,'backend_python_source_vs_current_IT_drift':backenddrift,'executed_original_config_hash_verified':actualconfig,'executed_config_hash_independently_verified':executedsha,'executed_prefix_complete_8s':True,'executed_source_same_full_benchmark_at_completion':True,'executed_strict_public_admission':True,'last_diagnostics':result['diagnostics'][-1],'scope':'actual real-GUI completed artifact copied read-only; artifact math/identity/replay accepted here; GUI pixels/API/service interaction remained Root-owned and not exercised by reviewer','affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES')}
(R/'REVIEW.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
(R/'replayed-result.json').write_text(json.dumps(replayed,indent=2,allow_nan=False))
print('GUI_ARTIFACT_REVIEW_PASS',json.dumps({'tag':tag,'rid':rid,'config':actualconfig,'source':summary['actual_source_project_sha'],'all8bits':replay_bits,'indDelta':independent_delta,'executedHash':executedsha,'replay_s':summary['public_replay_elapsed_s']},indent=2),flush=True)
