import os,sys,json,hashlib
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parent;I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration');sys.path.insert(0,str(I/'src'))
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
import torch
torch.set_num_threads(2)
d=json.loads((R/'actual/run-e512006bbf0e4c6db16d918b35818867/executed.liquidrun').read_text());actual=json.loads((R/'actual/run-e512006bbf0e4c6db16d918b35818867/result.json').read_text());result=json.loads((R/'RESULT.json').read_text());definition=admit_dae_runtime_input(d);eq=definition.equation_graph
assert definition._manufactured_sources is None
ids=[n['id'] for n in d['nodes']];edges=[e['id'] for e in d['edges']];assert ids==actual['node_ids'] and edges==actual['edge_ids'];assert ids==list(eq.graph.node_ids) and edges==list(eq.graph.edge_str_ids)
index={s:i for i,s in enumerate(ids)};a=np.array([index[e['from']] for e in d['edges']]);b=np.array([index[e['to']] for e in d['edges']]);cpids=np.array([i for i,e in enumerate(d['edges']) if e['type']=='cold_plate']);hxids=np.array([i for i,e in enumerate(d['edges']) if e['type']=='heat_exchanger']);pipeids=np.array([i for i,e in enumerate(d['edges']) if e['type']=='pipe']);assert len(cpids)==56 and len(hxids)==1
cells=actual['cell_edge_ids'];assert len(cells)==892;cellmap={e:np.flatnonzero(np.array(cells)==e) for e in edges if e in cells};boundary=np.array([i for i,n in enumerate(d['nodes']) if n.get('bc')])
state_layout=eq.state_layout;parameter_layout=eq.parameter_layout;states=np.load(R/'states.npz');old=states['previous'];out=[]
for run in result['solves']:
 y=states[run['label']];theta=definition.parameters(1,pump_rpm_ratio=torch.tensor([run['pump']],dtype=torch.float64),valve_opening=torch.tensor([run['valve']]+[1.]*13,dtype=torch.float64)).numpy();p={k:theta[s] for k,s in parameter_layout.items()};s={k:y[sl] for k,sl in state_layout.items()};rate={k:(y[sl]-old[sl])/2. for k,sl in state_layout.items()};m=s['flow'];node=s['node_temperature'];solid=s['solid_temperature'];water=s['water_temperature'];sec=s['hx_secondary_temperature'];pri=s['primary_return_temperature'];rho=p['fluid_rho'].item();cpf=p['fluid_cp'].item()
 # Arithmetic below uses the copied graph incidence and output contract; it never calls the production residual or diagnostics.
 mass=np.zeros(229);np.add.at(mass,a,m);np.add.at(mass,b,-m);up=np.where(m>=0,a,b);down=np.where(m>=0,b,a);weight=np.abs(m);outlet=node[up].copy()
 for i in pipeids:
  positions=cellmap[edges[i]];outlet[i]=water[positions[-1] if m[i]>=0 else positions[0]]
 adv=weight[cpids]*cpf;eff=-np.expm1(-p['cp_UA']/adv);cpheat=adv*eff*(solid-node[up[cpids]]);outlet[cpids]=node[up[cpids]]+eff*(solid-node[up[cpids]]);outlet[hxids]=sec
 node_imbalance=np.zeros(229);np.add.at(node_imbalance,up,weight*cpf*node[up]);np.add.at(node_imbalance,down,-weight*cpf*outlet)
 storage_water=np.sum(rho*cpf*p['cell_volume']*rate['water_temperature']);storage_solid=np.sum(p['cp_Csolid']*rate['solid_temperature']);storage_secondary=np.sum(rho*cpf*p['secondary_volume']*rate['hx_secondary_temperature']);storage_primary=np.sum(p['primary_rho']*p['primary_cp']*p['primary_volume']*rate['primary_return_temperature']);storage=storage_water+storage_solid+storage_secondary+storage_primary
 Q=p['heat_W'].sum()+p['cell_heat_W'].sum();reject=np.sum(p['primary_flow_kg_s']*p['primary_cp']*(pri-p['primary_inlet_K']));exchange_boundary=node_imbalance[boundary].sum();energy=Q+exchange_boundary-storage-reject
 cp_res=p['cp_Csolid']*rate['solid_temperature']-p['heat_W']+cpheat
 hxexchange=p['hx_UA']*(sec-pri);secres=rho*cpf*p['secondary_volume']*rate['hx_secondary_temperature']-weight[hxids]*cpf*(node[up[hxids]]-sec)+hxexchange;prires=p['primary_rho']*p['primary_cp']*p['primary_volume']*rate['primary_return_temperature']-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-pri)-hxexchange
 item={'label':run['label'],'all229_mass_inf_kg_s':float(np.max(np.abs(mass))),'all229_node_enthalpy_inf_W':float(np.max(np.abs(node_imbalance))),'all56_CP_energy_inf_W':float(np.max(np.abs(cp_res))),'HX_secondary_energy_inf_W':float(np.max(np.abs(secres))),'HX_primary_energy_inf_W':float(np.max(np.abs(prires))),'heat_W':float(Q),'storage_components_W':{'water':float(storage_water),'solid':float(storage_solid),'HX_secondary':float(storage_secondary),'HX_primary':float(storage_primary)},'primary_rejection_W':float(reject),'boundary_exchange_W':float(exchange_boundary),'total_energy_residual_W':float(energy),'production_energy_difference_W':float(energy-run['diagnostics']['energy_balance_W'])}
 assert item['all229_mass_inf_kg_s']<=1e-10 and item['all56_CP_energy_inf_W']<=1e-6 and max(item['HX_secondary_energy_inf_W'],item['HX_primary_energy_inf_W'])<=1e-6 and abs(energy)<=1e-6
 out.append(item)
(R/'independent-numpy-balances.json').write_text(json.dumps({'scope':'independent NumPy arithmetic of mass, 56 solid balances, two HX volume balances, and total physical heat/storage/rejection at every AD/FD accepted root; same model parameters, not hardware calibration','records':out},indent=2));print(json.dumps({'roots':len(out),'max_mass':max(x['all229_mass_inf_kg_s'] for x in out),'max_CP':max(x['all56_CP_energy_inf_W'] for x in out),'max_total_energy':max(abs(x['total_energy_residual_W']) for x in out),'max_HX':max(max(x['HX_secondary_energy_inf_W'],x['HX_primary_energy_inf_W']) for x in out)},indent=2))
