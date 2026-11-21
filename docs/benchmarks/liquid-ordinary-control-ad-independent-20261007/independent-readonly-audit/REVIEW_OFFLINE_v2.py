from pathlib import Path
import json,hashlib,numpy as np,datetime
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration'
F=B/'.codex-tmp/shinaide-ring-full20s-control-ad-private-20261006/FROZEN-v1'
P=B/'.codex-tmp/shinaide-ring-physical-control-ad-private-20261006/FROZEN-v1'
def same(a,b):return a.shape==b.shape and np.array_equal(a.view(np.uint64),b.view(np.uint64))
def identity(p):
 b=p.read_bytes();return {'path':str(p),'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
def verify_packet(root):
 m=json.loads((root/'manifest.json').read_text());bad=[]
 for x in m['payload']:
  d=identity(root/x['path'])
  if d['sha256']!=x['sha256'] or d['bytes']!=x['bytes']:bad.append(x['path'])
 assert not bad
 return {'manifest':identity(root/'manifest.json'),'count':len(m['payload']),'bytes':sum(x['bytes'] for x in m['payload']),'bad':bad}
def guards(root):
 a=json.loads((root/'normal-before-guards.json').read_text());b=json.loads((root/'imported-source-before.json').read_text());u={x['path']:x for x in a+b};changed=[]
 for x in u.values():
  d=identity(Path(x['path']))
  if d['sha256']!=x['sha256'] or d['bytes']!=x['bytes']:changed.append({'before':x,'now':d})
 return {'records':len(a+b),'unique':len(u),'changed':changed,'current':list(map(lambda x:identity(Path(x['path'])),u.values()))}
packets={'physical2s':verify_packet(P),'full20s':verify_packet(F)}
source={'physical2s':guards(P),'full20s':guards(F)};assert not source['physical2s']['changed'] and not source['full20s']['changed']
raw=json.loads((F/'actual/input.liquidrun').read_text());accepted=json.loads((F/'actual/executed.liquidrun').read_text());record=json.loads((F/'actual/result.json').read_text());result=json.loads((F/'RESULT.json').read_text());layout=json.loads((F/'layout-metadata.json').read_text());cp=json.loads((F/'actual/checkpoint.json').read_text());request=json.loads((F/'actual/input.json').read_text())
assert cp['liquidrun']==request['liquidrun']==raw and raw.get('manufactured_benchmark') is None
assert cp['applied_inputs']==record['applied_inputs'] and len(cp['applied_inputs'])==1
command=cp['applied_inputs'][0];assert command['seq']==1 and command['time_s']==2 and command['heat_multiplier']==1.2
sl={k:slice(*v) for k,v in layout['state_layout'].items()};pl={k:slice(*v) for k,v in layout['parameter_layout'].items()}
ys=np.load(F/'states.npz');ts=np.load(F/'parameters.npz');base=ys['baseline_AD'];theta=ts['baseline_AD'];assert base.shape==(1693,11) and theta.shape==(5086,11)
assert len(ys.files)==len(ts.files)==13 and set(ys.files)==set(ts.files)
ids=[n['id'] for n in raw['nodes']];eids=[e['id'] for e in raw['edges']];assert ids==record['node_ids'] and eids==record['edge_ids']
ni={v:i for i,v in enumerate(ids)};frm=np.array([ni[e['from']] for e in raw['edges']]);to=np.array([ni[e['to']] for e in raw['edges']]);cps=np.array([i for i,e in enumerate(raw['edges']) if e['type']=='cold_plate']);hxs=np.array([i for i,e in enumerate(raw['edges']) if e['type']=='heat_exchanger']);pipes=np.array([i for i,e in enumerate(raw['edges']) if e['type']=='pipe']);valves=[e['id'] for e in raw['edges'] if e['type']=='valve'];pumps=[e['id'] for e in raw['edges'] if e['type']=='pump'];assert len(cps)==56 and len(hxs)==1 and len(pipes)==214 and len(valves)==14 and len(pumps)==1
assert list(command['pump_rpm_ratios'])==pumps and list(command['valve_openings'])==[valves[0]]
selected_commands=[]
for i in range(11):
 p=theta[pl['pump_rpm_ratio'],i];v=theta[pl['valve_opening'],i];inlet=theta[pl['primary_inlet_K'],i]
 assert p.tolist()==[1 if i==0 else .95] and v.tolist()==([1]*14 if i==0 else [.9]+[1]*13)
 assert inlet.tolist()==[293.15 if i==0 else 294.15]
 assert float(theta[pl['heat_W'],i].sum())==(21000 if i==0 else 25200)
 assert bool(np.all(theta[pl['cp_dT_set'],i]==0)) and bool(np.all(theta[pl['hx_T_set'],i]==0))
 selected_commands.append({'seq':i,'time':i*2,'pump':p.tolist(),'all14valves':v.tolist(),'primaryInlet':inlet.tolist(),'heatW':float(theta[pl['heat_W'],i].sum()),'complete5086VectorRecorded':True})
assert all(r['all_parameters_bit_exact'] for r in layout['accepted_manifest_vs_original_plus_true_commands']) and len(layout['accepted_manifest_vs_original_plus_true_commands'])==11
variations=[]
for run in result['trajectories']:
 name=run['label'];y=ys[name];t=ts[name];assert same(y[:,0],base[:,0]) and same(t[:,0],theta[:,0])
 if name!='baseline_AD':
  field='pump_rpm_ratio' if name.startswith('pump') else 'valve_opening';index=pl[field].start
  diff=np.flatnonzero(np.any(t.view(np.uint64)!=theta.view(np.uint64),axis=1));assert diff.tolist()==[index]
  expected=run['pump_command'] if field=='pump_rpm_ratio' else run['valve_command'];assert np.all(t[index,1:]==expected)
  variations.append({'label':name,'onlyChanged5086Row':int(index),'field':field,'t0StateAndThetaBitExact':True,'samples1to10Held':True})
actual7={}
for field,key in [('solid_temperature','chip_temp_K'),('node_temperature','node_temperature_K'),('flow','edge_flow_kg_s'),('water_temperature','cell_temp_K'),('primary_return_temperature','primary_return_K'),('hx_secondary_temperature','secondary_supply_K')]:
 a=base[sl[field]];b=np.asarray(record[key],dtype=np.float64);assert same(a,b);actual7[key]={'shape':list(a.shape),'bitExact':True}
free=[i for i,n in enumerate(raw['nodes']) if not n.get('bc')];pressure=theta[pl['pressure_bc']].copy();pressure[free]=base[sl['pressure_free']];assert same(pressure,np.asarray(record['node_pressure_Pa'],dtype=np.float64));actual7['node_pressure_Pa']={'shape':list(pressure.shape),'bitExact':True}
means={name:float(ys[name][sl['solid_temperature'],-1].mean()) for name in ys.files};fd=[]
for row in result['FD']:
 h=row['h'];prefix=row['control'];slope=(means[f'{prefix}_plus_{h:g}']-means[f'{prefix}_minus_{h:g}'])/(2*h);ad=result['AD'][prefix];error=abs(slope-ad);rel=error/max(abs(ad),1e-30)
 assert np.isfinite(ad) and ad!=0
 fd.append({'control':prefix,'h':h,'rawNumpyFD':slope,'reportedTorchObjectiveFD':row['FD_K_per_unit'],'difference':slope-row['FD_K_per_unit'],'AD':ad,'absError':error,'relativeAuthorDefinition':rel,'relativeRootV1Definition':error/max(abs(ad),abs(slope)),'originalFinestGate':bool(rel<=1e-4 or error<=1e-8) if h==.0001 else None})
assert all(x['originalFinestGate'] for x in fd if x['h']==.0001)
cellmap={e:np.flatnonzero(np.asarray(record['cell_edge_ids'])==e) for e in eids if e in record['cell_edge_ids']};bounds=np.array([i for i,n in enumerate(raw['nodes']) if n.get('bc')]);balance=[];stored=json.loads((F/'independent-numpy-balances.json').read_text());initial=[]
for run in result['trajectories']:
 label=run['label'];yall=ys[label];thall=ts[label]
 for step in range(11):
  y=yall[:,step];old=yall[:,step-1] if step else y;s={k:y[x] for k,x in sl.items()};rate={k:(y[x]-old[x])/2 for k,x in sl.items()};p={k:thall[x,step] for k,x in pl.items()}
  m=s['flow'];w=abs(m);assert np.min(w)>0
  up=np.where(m>=0,frm,to);dn=np.where(m>=0,to,frm);T=s['node_temperature'];cpf=float(p['fluid_cp'][0]);rho=float(p['fluid_rho'][0]);outlet=T[up].copy()
  for edge in pipes:
   ix=cellmap[eids[edge]];outlet[edge]=s['water_temperature'][ix[-1] if m[edge]>=0 else ix[0]]
  adv=w[cps]*cpf;eff=-np.expm1(-p['cp_UA']/adv);power=adv*eff*(s['solid_temperature']-T[up[cps]])
  outlet[cps]=T[up[cps]]+eff*(s['solid_temperature']-T[up[cps]]);outlet[hxs]=s['hx_secondary_temperature']
  mass=np.zeros(len(ids));np.add.at(mass,frm,m);np.add.at(mass,to,-m)
  nodal=np.zeros(len(ids));np.add.at(nodal,up,w*cpf*T[up]);np.add.at(nodal,dn,-w*cpf*outlet)
  cp_error=p['cp_Csolid']*rate['solid_temperature']+power-p['heat_W']
  exchange=p['hx_UA']*(s['hx_secondary_temperature']-s['primary_return_temperature'])
  hxs_error=rho*cpf*p['secondary_volume']*rate['hx_secondary_temperature']+exchange-w[hxs]*cpf*(T[up[hxs]]-s['hx_secondary_temperature'])
  hxp_error=p['primary_rho']*p['primary_cp']*p['primary_volume']*rate['primary_return_temperature']-exchange-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-s['primary_return_temperature'])
  storage=sum([np.sum(rho*cpf*p['cell_volume']*rate['water_temperature']),np.sum(p['cp_Csolid']*rate['solid_temperature']),np.sum(rho*cpf*p['secondary_volume']*rate['hx_secondary_temperature']),np.sum(p['primary_rho']*p['primary_cp']*p['primary_volume']*rate['primary_return_temperature'])])
  rejection=np.sum(p['primary_flow_kg_s']*p['primary_cp']*(s['primary_return_temperature']-p['primary_inlet_K']));Q=np.sum(p['heat_W'])+np.sum(p['cell_heat_W']);boundary=np.sum(nodal[bounds]);energy=Q+boundary-storage-rejection
  row={'trajectory':label,'seq':step,'massInf':float(np.max(abs(mass))),'CPenergyInf':float(np.max(abs(cp_error))),'HXenergyInf':float(max(np.max(abs(hxs_error)),np.max(abs(hxp_error)))),'allNodeEnthalpyInf':float(np.max(abs(nodal))),'heatW':float(Q),'storageW':float(storage),'rejectionW':float(rejection),'boundaryW':float(boundary),'energyW':float(energy),'productionEnergyDeltaW':float(energy-run['frames'][step]['diagnostics']['energy_balance_W'])}
  assert row['massInf']<=1e-10
  if step:
   assert max(row['CPenergyInf'],row['HXenergyInf'],abs(row['energyW']))<=1e-6;balance.append(row)
  else:
   row['originalHeatScaledGate']=max(1e-7,256*np.finfo(np.float64).eps)*(1+abs(Q));row['dynamicAbsolute1e6GatePassed']=bool(abs(energy)<=1e-6);assert abs(energy)<=row['originalHeatScaledGate'];initial.append(row)
assert len(balance)==130
summary={k:max(abs(row[k]) for row in balance) for k in ['massInf','CPenergyInf','HXenergyInf','allNodeEnthalpyInf','energyW','productionEnergyDeltaW']}
# original v2 worker record confirms all ten roots remain grad-enabled, exact unchanged dense solver options.
base_run=result['trajectories'][0];assert base_run['label']=='baseline_AD' and len(base_run['root_graphs'])==10 and all(x['requires_grad'] for x in base_run['root_graphs'])
expected={'max_iterations':40,'atol':1e-10,'rtol':1e-10,'max_backtracks':18,'linear_solver':'dense'};assert all(result['preflight']['original_newton_options'][k]==v for k,v in expected.items())
true_browser=B/'.codex-tmp/goal-20261005/integration/.codex-tmp/shinaide-ring-normal-browser-20261006/transient-complete-normal';six={}
for p in (F/'actual').iterdir():
 if p.is_file():
  q=true_browser/p.name
  if q.exists():six[p.name]={'frozen':identity(p),'browserArtifact':identity(q),'byteEqual':p.read_bytes()==q.read_bytes()}
assert len(six)==6 and all(x['byteEqual'] for x in six.values())
print(json.dumps({'UTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'packetFULL':packets,'currentGuards':source,'originalBrowserArtifacts':six,'selectedCommandsAll11':selected_commands,'all12CounterfactualThetaOnlySelectedScalarChanged':variations,'savedActual7stateClassesAll11bitExact':actual7,'eighthOutletClassScope':'Original worker full8/all11 bit comparison audited in source/RESULT; this offline replay does not claim new bit-exact expm1 outlet reconstruction.','all5086ParameterVectorsPublicAcceptedComparison':layout['accepted_manifest_vs_original_plus_true_commands'],'meansFromRawStates':means,'FD':fd,'independentBalanceSummary':summary,'dynamicRoots':balance,'initializers':initial,'normalSolverThresholds':expected,'noSolverOrBackendImports':True,'firstOrderOnly':True,'pending':['180-second ordinary trajectory AD/FD','GPU ordinary trajectory AD/FD parity','all fourteen valve sensitivities','arbitrary command/closed-valve/topology regime changes','coarse fullMMS HX-secondary EOC / whole L-infinity gate','empirical/vendor parameter calibration']},indent=2))
