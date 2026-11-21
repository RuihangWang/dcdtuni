from pathlib import Path
import json,numpy as np,hashlib,collections
T=Path(__file__).resolve().parent
raw=json.loads((T/'actual/input.liquidrun').read_text());b=raw['manufactured_benchmark'];r=json.loads((T/'actual/result.json').read_text());ref=np.load(T/'references/target-dt-0p5.npz');c=np.load(T/'references/independent-cycle-construction.npz');water=[];seen=collections.Counter()
for edge in r['cell_edge_ids']:
 water.append(f'{edge}/cell/{seen[edge]}');seen[edge]+=1
ids={'node_pressure_Pa':r['node_ids'],'node_temperature_K':r['node_ids'],'flow_kg_s':r['edge_ids'],'water_temperature_K':water,'solid_temperature_K':[x['edge_id']for x in r['cp_details']],'hx_secondary_K':r['hx_edge_ids'],'hx_primary_K':r['hx_edge_ids']};initial={}
for name,expected in ids.items():
 rec=b['initial_state'][name];assert len(rec['ids'])==len(set(rec['ids'])) and set(rec['ids'])==set(expected);d=dict(zip(rec['ids'],rec['values']));v=np.array([d[i]for i in expected]);initial[name]={'ids':len(expected),'max_vs_original_independent_reference':float(np.abs(v-ref[name][0]).max())};assert np.array_equal(v,ref[name][0])
xd=c['xamplitude']*(2*np.pi/32.)*np.cos(c['xphase']);offset=0;rates={}
for name in ['water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K']:
 expected=ids[name];rec=b['initial_temperature_rate'][name];assert len(rec['ids'])==len(set(rec['ids'])) and set(rec['ids'])==set(expected);d=dict(zip(rec['ids'],rec['values']));v=np.array([d[i]for i in expected]);a=xd[offset:offset+len(expected)];rates[name]={'ids':len(expected),'max_vs_original_analytic_derivative':float(np.abs(v-a).max())};assert np.array_equal(v,a);offset+=len(expected)
profiles=raw['parameter_pack']['profiles'];edges=[]
for e in raw['edges']:
 p={**profiles.get(e.get('parameter_ref'),{}).get('params',{}),**e.get('params',{})};edges.append({'id':e['id'],'type':e['type'],'from':e['from'],'to':e['to'],'n_cells':p.get('n_cells') if e['type']=='pipe'else None})
layout={'nodes':[{'id':n['id'],'type':n['type']}for n in raw['nodes']],'edges':edges};sha=hashlib.sha256(json.dumps(layout,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest();assert sha==b['graph_layout_sha256']
report={'independently_recomputed_graph_layout_sha256':sha,'initial_state_all_complete_unique_IDs_and_exact_reference':initial,'initial_derivatives_all_complete_unique_IDs_and_exact_analytic_rate':rates,'source_units':b['units'],'source_sampling':b['sampling'],'source_reference':b['source_reference'],'source_sha256':b['source_sha256'],'source_project':raw.get('source_project'),'config_hash':r['config_hash']}
(T/'outputs/identity-initial-audit.json').write_text(json.dumps(report,indent=2)+'\n')
