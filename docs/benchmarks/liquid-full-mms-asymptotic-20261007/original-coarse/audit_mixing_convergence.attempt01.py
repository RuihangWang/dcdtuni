"""Recompute read-only graph regime and convergence summaries from retained actual data.
No Newton, product F, native mesh, normal source write or service action.
"""
from pathlib import Path
import json,math
import numpy as np
ROOT=Path(__file__).resolve().parent
def recompute():
 raw=json.loads((ROOT/'results/manufactured-dt-1p0.liquidrun').read_text())
 b=raw['manufactured_benchmark']
 nodes=raw['nodes']; index={n['id']:i for i,n in enumerate(nodes)}
 m=dict(zip(b['initial_state']['flow_kg_s']['ids'],b['initial_state']['flow_kg_s']['values']))
 profiles=raw['parameter_pack']['profiles']
 deps=[set() for _ in nodes];anchors=set()
 for e in raw['edges']:
  up,down=(e['from'],e['to']) if m[e['id']]>0 else (e['to'],e['from'])
  u,d=index[up],index[down]
  params={**profiles.get(e.get('parameter_ref'),{}).get('params',{}),**e.get('params',{})}
  if e['type'] in ('pipe','heat_exchanger'):anchors.add(d)
  elif e['type']=='cold_plate':
   if params['UA_W_per_K']>0:anchors.add(d)
   deps[d].add(u)
  else:deps[d].add(u)
 for i,n in enumerate(nodes):
  if 'T_K' in n.get('bc',{}):anchors.add(i)
 unreachable=[]
 for start in range(len(nodes)):
  todo=[start];seen=set();found=False
  while todo:
   x=todo.pop()
   if x in seen:continue
   seen.add(x)
   if x in anchors:found=True;break
   todo.extend(deps[x]-seen)
  if not found:unreachable.append(start)
 a=np.load(ROOT/'results/independent-cycle-construction.npz')
 base=a['C']@a['a0'];bound=np.abs(a['C'])@np.abs(a['amplitude'])
 assert np.array_equal(a['B']@a['C'],np.zeros((a['B'].shape[0],a['C'].shape[1]),dtype=a['B'].dtype))
 proof={'all_nodes_reach_anchor':not unreachable,'anchor_count':len(anchors),'nodes':len(nodes),'unreachable':unreachable,
        'all_time_flow_margin_min_kg_s':float(np.min(np.abs(base)-bound)),
        'sinusoidal_variation_relative_bound_max':float(np.max(bound/np.abs(base))),
        'argument':'Positive fixed-sign weights; node mixing is an anchored M-matrix, every dependency component reaches a positive storage/fixed-T anchor. Analytic N(t) is nonsingular throughout the bounded trajectory.',
        'manufacturing_residual_used':False}
 assert not unreachable and proof['all_time_flow_margin_min_kg_s']>1e-8
 j=json.loads((ROOT/'results/mms-refinement.json').read_text());runs=j['runs']
 thermal=['node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K']
 whole={k:[math.log2(runs[i]['errors'][k]['whole_trajectory_inf']/runs[i+1]['errors'][k]['whole_trajectory_inf']) for i in range(5)] for k in thermal}
 orders={k:v[1:4] for k,v in whole.items()}
 convergence={'metric':'L-infinity over every retained time sample and every state entry within each thermal block',
              'selected_dt':[r['dt'] for r in runs[1:5]],'band':[.85,1.15],'orders':orders,
              'passes':all(.85<=v<=1.15 for row in orders.values() for v in row),
              'all_six_level_whole_orders':whole,'all_six_level_final_orders':j['orders'],
              'normal_UI_acceptance':False,'coarse_final_HXsecondary_not_passed':True}
 return {'results/mixing-and-regime-proof.json':proof,'results/whole-trajectory-convergence.json':convergence}
if __name__=='__main__':
 generated=recompute()
 for rel,value in generated.items():
  retained=json.loads((ROOT/rel).read_text())
  assert retained==value,rel+' differs'
 print(json.dumps({'mode':'read-only retained-data recomputation','exact_summary_match':True,'files':list(generated)},indent=2))
