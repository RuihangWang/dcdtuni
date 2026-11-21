from pathlib import Path
import argparse,json,hashlib,sys,os,subprocess,numpy as np
import independent_equations as G
from reproduce_sources import regenerate
from package_paths import optional_output
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--out');ap.add_argument('--replay-tag',choices=['2p0','1p0','0p5','0p25','all']);args=ap.parse_args();out=optional_output(args.out,P)
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
source_rows=regenerate()
js=subprocess.run(['/usr/bin/node',str(P/'scripts/check_project_hash.cjs')],capture_output=True,text=True,check=True)
canonical=json.loads(js.stdout)
rows=[];errs={family:[] for family in ['node','water','solid','HXsecondary','HXprimary']}
families={'node':('node_temperature_K',slice(0,229)),'water':('cell_temp_K',slice(229,1121)),'solid':('chip_temp_K',slice(1121,1177)),'HXsecondary':('secondary_supply_K',slice(1177,1178)),'HXprimary':('primary_return_K',slice(1178,1179))}
fields=['node_pressure_Pa','edge_flow_kg_s','node_temperature_K','cell_temp_K','chip_temp_K','secondary_supply_K','primary_return_K','edge_outlet_temp_K']
for tag in ['2p0','1p0','0p5','0p25']:
 R=next((P/'evidence/gui').glob('run-'+tag+'-*'));wrapper=json.loads((R/'input.json').read_text());raw=wrapper['liquidrun'];cp=json.loads((R/'checkpoint.json').read_text());result=json.loads((R/'result.json').read_text());executed=json.loads((R/'executed.liquidrun').read_text());exmeta=json.loads((R/'executed-inputs.json').read_text());prior=json.loads((R/'replayed-result.json').read_text())
 assert raw==json.loads((R/'input.liquidrun').read_text())==cp['liquidrun'];assert result['completed'] and result['status']==cp['run_status']=='completed'
 original_sha=digest({'liquidrun':raw,'controllers':wrapper['controllers']});assert original_sha==wrapper['config_hash']==cp['config_hash']==result['config_hash']==exmeta['original_config_hash']
 ex_sha=digest({'liquidrun':executed,'controllers':[]});assert ex_sha==exmeta['executed_config_hash'];assert executed['manufactured_benchmark']==raw['manufactured_benchmark'] and executed['source_project']==raw['source_project']
 times=np.asarray(result['time_s']);dt=raw['time']['dt_s'];assert np.array_equal(times,np.arange(raw['time']['N_t'])*dt) and times[-1]==8.
 assert all(np.array_equal(np.asarray(result[f],dtype=np.float64).view(np.uint64),np.asarray(prior[f],dtype=np.float64).view(np.uint64)) for f in fields)
 T=np.concatenate([np.array(result[f]).T for f in ['node_temperature_K','cell_temp_K','chip_temp_K','secondary_supply_K','primary_return_K']],axis=1)
 exact=G.b[None,:]+times[:,None]*G.v[None,:]+times[:,None]**2*G.a[None,:]
 lu=G.splu(G.D+G.diags(G.C/dt));e=np.zeros_like(exact)
 for i in range(1,len(times)):e[i]=lu.solve(G.C/dt*e[i-1]+G.C*G.a*dt)
 thermal_delta=float(np.max(np.abs(T-exact-e)))
 for family,(field,sl) in families.items():errs[family].append(float(np.max(np.abs(np.array(result[field])[:,-1]-exact[-1,sl]))))
 source=np.zeros_like(T)
 for name,sl in G.source_slices.items():source[:,sl]=np.array(raw['manufactured_benchmark']['sources'][name]['values']).T
 balances=[]
 for i in range(len(times)):
  derivative=G.v if i==0 else (T[i]-T[i-1])/dt
  reject=np.sum(G.p['primary_flow_kg_s']*G.p['primary_cp']*(T[i,-1:]-G.p['primary_inlet_K']))
  balances.append(float(G.Q[G.N:-1].sum()+source[i,G.N:].sum()-np.dot(G.C,derivative)-reject))
 ratio=max(abs(x)/(max(1e-7,256*np.finfo(float).eps)*(1+abs(d['heat_input_W']))) for x,d in zip(balances,result['diagnostics']));assert ratio<=1.
 row={'tag':tag,'run_id':result['run_id'],'samples':len(times),'actual_source':raw['source_project']['sha256'],'config_hash':original_sha,'executed_config_hash':ex_sha,'independent_thermal_BE_delta_inf_K':thermal_delta,'original_energy_gate_ratio':ratio,'copied_original_replay_bitexact':True}
 if args.replay_tag in (tag,'all'):
  sys.path.insert(0,str(P/'code/product-source/src'));import torch
  torch.set_num_threads(2)
  from backend.liquidcooling.dae_runtime import DAESimulation
  sim=DAESimulation.from_checkpoint(raw,cp);replayed=sim.result()
  bits={f:bool(np.array_equal(np.asarray(result[f],dtype=np.float64).view(np.uint64),np.asarray(replayed[f],dtype=np.float64).view(np.uint64))) for f in fields}
  assert all(bits.values()) and sim.config_hash==original_sha and sim.run_id==result['run_id'];row['fresh_public_replay_all_eight_arrays_bitexact']=bits
 rows.append(row)
orders={f:[float(np.log(v[i]/v[i+1])/np.log(2)) for i in range(3)] for f,v in errs.items()};assert all(.85<=v<=1.15 for x in orders.values() for v in x)
summary={'scope':'relocatable package-local source-regeneration, actual JS canonical source, original runtime/accepted hashes, independent thermal/energy/EOC and optional fresh public v3 replay','sources':source_rows,'canonical':canonical,'runs':rows,'original_coarse_grids':[2,1,.5,.25],'final_EOCs':orders,'original_band':[.85,1.15],'affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES')}
if out:(out/'package-verification.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
