"""Audit actual browser artifacts with the unchanged independent NumPy oracle.
No product F, Newton or observed solution creates reference values or sources.
Product graph/index/physical parameters are admitted input data only.
"""
from pathlib import Path
import sys,json,hashlib,copy
import numpy as np
import torch
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
from backend.liquidcooling.runtime import _canonical_hash
sys.path.insert(0,str(Path(__file__).resolve().parent/'references'))
from independent_trajectory import IndependentTrajectory
T=Path(__file__).resolve().parent
raw=json.loads((T/'actual/input.liquidrun').read_text())
executed=json.loads((T/'actual/executed.liquidrun').read_text())
checkpoint=json.loads((T/'actual/checkpoint.json').read_text())
wrapped=json.loads((T/'actual/input.json').read_text())
receipt=json.loads((T/'actual/executed-inputs.json').read_text())
result=json.loads((T/'actual/result.json').read_text())
assert result['status']=='completed' and result['completed'] and result['error'] is None
assert checkpoint['seq']==16 and checkpoint['run_status']=='completed' and checkpoint['error'] is None
assert raw==checkpoint['liquidrun']==wrapped['liquidrun']
assert _canonical_hash(raw,[])==checkpoint['config_hash']==result['config_hash']==receipt['original_config_hash']
assert _canonical_hash(executed,[])==receipt['executed_config_hash']
assert raw['manufactured_benchmark']==executed['manufactured_benchmark']
assert receipt['accepted_sample_count']==receipt['original_sample_count']==17 and receipt['accepted_time_s']==8. and receipt['complete_original_horizon']
assert result['time_s']==raw['manufactured_benchmark']['times_s']==[i*.5 for i in range(17)]
b=raw['manufactured_benchmark'];body=copy.deepcopy(b);source_sha=body.pop('source_sha256')
assert hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()==source_sha
# Real normal admission/compiler supplies coefficients/index data, no solve.
definition=admit_dae_runtime_input(raw);eq=definition.equation_graph
ref=IndependentTrajectory(eq,definition.parameters(0))
retained=np.load(T/'references/independent-cycle-construction.npz')
cycle_equal={k:np.array_equal(getattr(ref,k),retained[k]) for k in retained.files}
assert all(cycle_equal.values()),cycle_equal
assert np.array_equal(ref.B@ref.C,np.zeros((229,58),dtype=np.int64))
g=eq.graph;p=ref.p;ids=ref.ids;ni,ne,nw,nc,nh=g.N_nodes,g.N_edges,g.N_cells,g.N_cp,g.N_hx
water_ids=[]
for edge,a,z in zip(g.pipe_indices.edge_ids.tolist(),g.pipe_indices.cell_start.tolist(),g.pipe_indices.cell_end.tolist()):
 water_ids.extend(f'{g.edge_str_ids[edge]}/cell/{j}' for j in range(z-a))
compiled={'nodes':list(g.node_ids),'edges':list(g.edge_str_ids),'water':water_ids,'solid':[g.edge_str_ids[j]for j in ids['cp']],'hx':[g.edge_str_ids[j]for j in ids['hx']]}
assert result['node_ids']==compiled['nodes'] and result['edge_ids']==compiled['edges'] and result['hx_edge_ids']==compiled['hx']
assert result['cell_edge_ids']==[x.rsplit('/cell/',1)[0]for x in water_ids]
smap={'momentum_Pa':'edges','water_W':'water','solid_W':'solid','hx_secondary_W':'hx','hx_primary_W':'hx'}
source={}
for name,key in smap.items():
 field=b['sources'][name];assert len(field['ids'])==len(set(field['ids'])) and set(field['ids'])==set(compiled[key])
 rows=dict(zip(field['ids'],field['values']));source[name]=np.asarray([rows[i]for i in compiled[key]],float).T
 assert source[name].shape==(17,len(compiled[key]))
state_map={'node_pressure_Pa':'node_pressure_Pa','flow_kg_s':'edge_flow_kg_s','node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temp_K','solid_temperature_K':'chip_temp_K','hx_secondary_K':'secondary_supply_K','hx_primary_K':'primary_return_K'}
actual={n:np.asarray(result[key],float).T for n,key in state_map.items()}
target={n:[] for n in state_map};source_errors={n:0. for n in source};continuous_energy=[]
for i,t in enumerate(result['time_s']):
 desired,rate,forcing,audit=ref.evaluate(t)
 for name in target:target[name].append(desired[name])
 for name in source:source_errors[name]=max(source_errors[name],float(np.abs(source[name][i]-forcing[name]).max()))
 continuous_energy.append(audit)
target={n:np.asarray(v)for n,v in target.items()}
original_target=np.load(T/'references/target-dt-0p5.npz')
reference_equal={n:np.array_equal(v,original_target[n]) for n,v in target.items()}
assert all(reference_equal.values()),reference_equal
errors={n:{'all_entries':v.size,'whole_trajectory_inf':float(np.abs(v-target[n]).max()),'final_inf':float(np.abs(v[-1]-target[n][-1]).max())}for n,v in actual.items()}
# Source equality is independent of product equations/Newton.
assert max(source_errors.values())<1e-6,source_errors
rho=p['fluid_rho'][0];cp=p['fluid_cp'][0];fr=ref.fr;to=ref.to
rates={}
for name,key in [('water_temperature_K','water'),('solid_temperature_K','solid'),('hx_secondary_K','hx'),('hx_primary_K','hx')]:
 r=b['initial_temperature_rate'][name];record=dict(zip(r['ids'],r['values']));initial=np.asarray([record[x]for x in compiled[key]])
 rates[name]=np.concatenate([initial[None,:],np.diff(actual[name],axis=0)/.5])
rows=[];cp_physical=[];cp_augmented=[];full_residual=[]
for i,t in enumerate(result['time_s']):
 pressure=actual['node_pressure_Pa'][i];m=actual['flow_kg_s'][i];node=actual['node_temperature_K'][i]
 water=actual['water_temperature_K'][i];solid=actual['solid_temperature_K'][i];sec=actual['hx_secondary_K'][i];pri=actual['hx_primary_K'][i]
 up=np.where(m>=0,fr,to);down=np.where(m>=0,to,fr);w=np.abs(m)
 eps=-np.expm1(-p['cp_UA']/(w[ids['cp']]*cp));qcp=w[ids['cp']]*cp*eps*(solid-node[up[ids['cp']]])
 outlet=node[up].copy()
 outlet[ids['pipe']]=np.where(m[ids['pipe']]>=0,water[eq._pipe_last.cpu().numpy()],water[eq._pipe_first.cpu().numpy()])
 outlet[ids['cp']]=node[up[ids['cp']]]+eps*(solid-node[up[ids['cp']]])
 outlet[ids['hx']]=sec
 incoming=np.bincount(down,weights=w,minlength=ni);outgoing=np.bincount(up,weights=w,minlength=ni)
 received=np.bincount(down,weights=w*cp*outlet,minlength=ni)
 mix=incoming*cp*node-received
 upstream=np.where(m[ref.cell_edge]>=0,np.where(ref.first,node[fr[ref.cell_edge]],water[ref.prev]),np.where(ref.last,node[to[ref.cell_edge]],water[ref.next]))
 rw=rho*cp*p['cell_volume']*rates['water_temperature_K'][i]-w[ref.cell_edge]*cp*(upstream-water)-p['cell_heat_W']-source['water_W'][i]
 physical_cp=p['heat_W']-qcp-p['cp_Csolid']*rates['solid_temperature_K'][i]
 rs=-physical_cp-source['solid_W'][i]
 exchange=p['hx_UA']*(sec-pri)
 rh=rho*cp*p['secondary_volume']*rates['hx_secondary_K'][i]-w[ids['hx']]*cp*(node[up[ids['hx']]]-sec)+exchange-source['hx_secondary_W'][i]
 rp=p['primary_rho']*p['primary_cp']*p['primary_volume']*rates['hx_primary_K'][i]-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-pri)-exchange-source['hx_primary_W'][i]
 mass=ref.B@m;hydro=ref.headloss(m)-pressure[fr]+pressure[to]-source['momentum_Pa'][i]
 storage=(rho*cp*p['cell_volume']*rates['water_temperature_K'][i]).sum()+(p['cp_Csolid']*rates['solid_temperature_K'][i]).sum()+(rho*cp*p['secondary_volume']*rates['hx_secondary_K'][i]).sum()+(p['primary_rho']*p['primary_cp']*p['primary_volume']*rates['hx_primary_K'][i]).sum()
 physical=p['heat_W'].sum()+p['cell_heat_W'].sum();manufactured=sum(source[k][i].sum()for k in ['water_W','solid_W','hx_secondary_W','hx_primary_W'])
 rejection=(p['primary_flow_kg_s']*p['primary_cp']*(pri-p['primary_inlet_K'])).sum()
 boundary=((outgoing*cp*node-received)[ref.bc|ref.bcT]).sum()
 energy=physical+manufactured+boundary-storage-rejection
 # Existing residual scale is data only; all residual blocks above are NumPy.
 residual=np.concatenate([mass[ref.free],hydro,mix,rw,rs,rh,rp])
 scale=eq.residual_scale.detach().cpu().numpy();scaled=float(np.abs(residual/scale).max())
 row={'time_s':t,'mass_inf_kg_s':float(np.abs(mass).max()),'hydraulic_forced_inf_Pa':float(np.abs(hydro).max()),'mixing_inf_W':float(np.abs(mix[~ref.bcT]).max()),'water_storage_forced_inf_W':float(np.abs(rw).max()),'CP_storage_forced_inf_W':float(np.abs(rs).max()),'HX_secondary_forced_inf_W':float(np.abs(rh).max()),'HX_primary_forced_inf_W':float(np.abs(rp).max()),'independent_scaled_residual_inf':scaled,'physical_heat_W':float(physical),'manufactured_external_W':float(manufactured),'storage_W':float(storage),'primary_rejection_W':float(rejection),'boundary_exchange_W':float(boundary),'augmented_total_energy_balance_W':float(energy),'physical_only_energy_gap_W':float(physical+boundary-storage-rejection),'CP_physical_balance_sum_W':float(physical_cp.sum()),'CP_manufactured_source_sum_W':float(source['solid_W'][i].sum())}
 rows.append(row);cp_physical.append(physical_cp);cp_augmented.append(physical_cp+source['solid_W'][i]);full_residual.append(residual)
arrays={}
for n in actual:arrays[n+'_actual']=actual[n];arrays[n+'_reference']=target[n];arrays[n+'_error']=actual[n]-target[n]
np.savez(T/'outputs/all_state_formula_comparison.npz',times=result['time_s'],**arrays)
np.savez(T/'outputs/independent_balances.npz',CP_physical_balance_W=np.asarray(cp_physical),CP_MMS_augmented_balance_W=np.asarray(cp_augmented),independent_residual=np.asarray(full_residual))
report={'scope':'actual completed normal browser MMS run; continuous formula and independent NumPy equations, no F oracle','counts':ref.counts,'source_sha256':source_sha,'original_and_final_source_exactly_equal':True,'original_checkpoint_input_exact':True,'accepted_count':17,'grid_seconds':result['time_s'],'cycle_reference_exact':cycle_equal,'continuous_reference_exact_original_target':reference_equal,'source_formula_error_inf':source_errors,'errors':errors,'sample_balances':rows,'continuous_reference_audits':continuous_energy,'all_56_CP_all_samples':{'entries':len(cp_physical)*nc,'physical_balance_abs_max_W':float(np.abs(cp_physical).max()),'manufactured_augmented_abs_max_W':float(np.abs(cp_augmented).max()),'interpretation':'physical Q alone differs from coolant+storage by the negative declared solid MMS source; include that source for the manufactured equation'},'limitation':'single dt0.5 browser run cannot establish EOC; coarse final HXsec original-band failure remains'}
(T/'outputs/continuous-audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps({'counts':ref.counts,'errors':errors,'source_formula_error_inf':source_errors,'max_independent_scaled':max(r['independent_scaled_residual_inf']for r in rows),'max_augmented_energy_W':max(abs(r['augmented_total_energy_balance_W'])for r in rows),'CP':report['all_56_CP_all_samples']},indent=2),flush=True)
