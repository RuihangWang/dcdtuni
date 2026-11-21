from pathlib import Path
import sys,os,json,copy,hashlib,time
Q=Path(os.environ["DCTWIN_MMS_WORK"])
sys.path.insert(0,str(Path(os.environ['DCTWIN_MMS_REPO'])/'src'))
import numpy as np,torch
torch.set_num_threads(1);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
from backend.liquidcooling.solver import dae_newton
from backend.liquidcooling.manufactured_benchmark import initial_vectors,compiled_ids,canonical_digest,graph_layout
from backend.liquidcooling.liquidrun_schema import Rack
from backend.liquidcooling.liquidrun_dae_schema import DAEParameterPack
START=time.monotonic()
def forbidden(*a,**k):raise RuntimeError('PREPARATION cannot call product Newton or residual')
for name in ['_newton','solve_steady','backward_euler_step','consistent_initialize']:setattr(dae_newton,name,forbidden)
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def normalize(v):
 v=copy.deepcopy(v);v.pop('source',None);v.pop('projectName',None);v.pop('manufactured_benchmark',None)
 for k in ['sha256','project_name']:v['source_project'].pop(k,None)
 for k in ['dt_s','N_t']:v['time'].pop(k,None)
 v['analysis'].pop('initialization',None)
 v['execution'].pop('device',None);v['execution'].pop('max_threads',None)
 v['transient'].pop('durationS',None)
 v['racks']=[Rack.model_validate(x).model_dump(by_alias=True,mode='json',exclude_none=True) for x in v['racks']]
 v['parameter_pack']=DAEParameterPack.model_validate(v['parameter_pack']).model_dump(by_alias=True,mode='json',exclude_none=True)
 def constant(x):
  if not isinstance(x,list):return x
  assert x and all(t==x[0] for t in x),'Physical dynamic schedules were not registered to change'
  return x[0]
 for n in v['nodes']:
  if n.get('bc'):
   for f in ['P_Pa','T_K']:
    if f in n['bc']:n['bc'][f]=constant(n['bc'][f])
 for e in v['edges']:
  for f in {'pump':['rpm_ratio','flow_set_kg_s'],'cold_plate':['Q_W','dT_set_K'],'valve':['opening'],'heat_exchanger':['T_set_K']}.get(e['type'],[]):
   if f in e['params']:e['params'][f]=constant(e['params'][f])
 for p in v['primary_circuits']:
  for f in ['flow_kg_s','inlet_K']:p[f]=constant(p[f])
 return v
raw=json.loads((Q/'compiled/Baseline.liquidrun').read_text());baseline=admit_dae_runtime_input({**raw,'execution':{**raw['execution'],'device':'cpu','max_threads':1}})
eq0=baseline.equation_graph;eq0.residual=forbidden;eq0.problem.residual=forbidden
base=baseline.manifest.model_dump(by_alias=True,mode='json',exclude_none=True);theta0=baseline.parameters(0)
baseProject=json.loads((Q/'inputs/Baseline.cfdproj').read_text())
allowed={('name',),('meta','projectName'),('meta','casePath'),('meta','description'),('meta','provenance','current_full_mms'),('runDefaults','liquid','inheritResults'),('runDefaults','liquid','experiment','dt_s'),('runDefaults','liquid','experiment','duration_s'),('runDefaults','liquid','experiment','dae','analysis','initialization'),('runDefaults','liquid','experiment','dae','manufactured_benchmark'),('runDefaults','liquid','experiment','dae','execution','device'),('runDefaults','liquid','experiment','dae','execution','max_threads'),('transient','gridS'),('transient','durationS')}
def project_diff(a,b,p=()):
 if p in allowed:return [list(p)] if a!=b else []
 if isinstance(a,dict) and isinstance(b,dict):
  out=[]
  for k in sorted(set(a)|set(b)):
   if k not in a or k not in b:
    assert p+(k,) in allowed,'Unregistered project field '+str(p+(k,));out.append(list(p+(k,)))
   else:out+=project_diff(a[k],b[k],p+(k,))
  return out
 assert a==b,'Unregistered project change '+str(p)
 return []
reg=json.loads((Q/'PREREGISTRATION.json').read_text());generated={r['tag']:r for r in json.loads((Q/'GENERATION-RECEIPT.json').read_text())['rows']};rows=[]
for grid in reg['grids']:
 tag=grid['tag'];p=Q/'compiled'/('Shinaide_Current_Glycol25_FullMMS_'+tag+'.liquidrun');v=json.loads(p.read_text())
 print('ADMIT_CURRENT_REACT',tag,flush=True)
 d=admit_dae_runtime_input({**v,'execution':{**v['execution'],'device':'cpu','max_threads':1}});eq=d.equation_graph;eq.residual=forbidden;eq.problem.residual=forbidden
 current=d.manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
 assert normalize(base)==normalize(current),'Full manifest coefficients/topology/BC beyond registered changes must match baseline'
 assert (eq.size,eq.graph.N_nodes,eq.graph.N_edges,eq.graph.N_cells,eq.graph.N_cp,eq.graph.N_hx)==(1693,229,286,892,56,1)
 assert eq.state_layout==eq0.state_layout and compiled_ids(eq)==compiled_ids(eq0)
 static=['_from','_to','_cells','_first','_last','_free','_bc_mask','_bc_T_mask','_closed_edges','_dead_edges','_flow_edges','_hx_setpoint','_cp_rise','differential_mask','state_scale','residual_scale']
 for name in static:assert torch.equal(getattr(eq,name),getattr(eq0,name)),name
 assert d.dt==grid['dt_s'] and d.sample_count==grid['N_t']
 mb=d.manifest.manufactured_benchmark
 assert mb.source_sha256==generated[tag]['sourceSHA256'] and mb.graph_layout_sha256==canonical_digest(graph_layout(v))
 theta_sha=hashlib.sha256()
 for i in range(d.sample_count):
  theta=d.parameters(i)
  for name,sl in eq0.parameter_layout.items():
   assert not name.startswith('mms_')
   actual=theta[eq.parameter_layout[name]];expected=theta0[sl]
   assert torch.equal(actual,expected),'Held physical theta '+name+' at '+str(i)
   theta_sha.update(actual.detach().numpy().tobytes())
 target=np.load(Q/'reference'/('target-'+tag+'.npz'))
 names=['node_pressure_Pa','flow_kg_s','node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K']
 expected=np.concatenate([target[k][0,eq._free.numpy()] if k=='node_pressure_Pa' else target[k][0] for k in names])
 y,dy=initial_vectors(eq,mb,d.parameters(0));assert np.array_equal(y.numpy().view(np.uint64),expected.view(np.uint64)),'All1693 bound initial bits'
 ids=compiled_ids(eq);smap={'momentum_Pa':'edges','water_W':'water','solid_W':'solid','hx_secondary_W':'hx','hx_primary_W':'hx'}
 coverage={}
 for name,key in smap.items():
  record=getattr(mb.sources,name);assert record.ids==ids[key] and len(record.values)==len(ids[key]) and all(len(row)==d.sample_count for row in record.values)
  coverage[name]={'allIDsExact':True,'ids':len(record.ids),'N_t':d.sample_count}
 project=Q/'inputs'/('Shinaide_Current_Glycol25_FullMMS_'+tag+'.cfdproj');diff=project_diff(baseProject,json.loads(project.read_text()))
 comp=json.loads((Q/'compiled'/('Shinaide_Current_Glycol25_FullMMS_'+tag+'.compile.json')).read_text())
 rows.append({'tag':tag,'projectRawSHA256':digest(project),'canonicalProjectSHA256':comp['canonicalProjectSHA256'],'liquidrunRawSHA256':digest(p),'sourceSHA256':mb.source_sha256,'graphLayoutSHA256':mb.graph_layout_sha256,'time':v['time'],'fullPhysicalManifestHeld':True,'compiledStaticTopologyMasksScalesHeld':static,'allNonMMS5086ThetaEverySampleBitExact':True,'sampledPhysicalThetaDigest':theta_sha.hexdigest(),'initial1693BitsExact':True,'sourceFullIDCoverage':coverage,'projectChangedPaths':diff,'productResidualOrNewtonCalled':False})
 print('ADMITTED_ALL_IDENTITY',json.dumps(rows[-1]),flush=True)
for filename in ['SOURCE-MAP-backend.json','SOURCE-MAP-react-baseline.json','SOURCE-MAP-react.json']:
 source=json.loads((Q/filename).read_text())
 for row in source['rows']:
  if isinstance(row.get('original'),dict):
   for key in ['original','snapshot']:
    value=row[key];assert digest(value['path'])==value['sha256'],key+' source drift '+value['path']
  else:
   original=row.get('source') or row.get('path')
   if original:assert digest(original)==row['sha256'],'Original source drift '+original
   snapshot=row.get('snapshot')
   if snapshot:assert digest(snapshot)==row['sha256'],'Snapshot source drift '+snapshot
receipt={'status':'actual_current_React_and_backend_input_admission_passed_no_numerical_acceptance','rows':rows,'counts':{'states':1693,'nodes':229,'edges':286,'water':892,'solid':56,'HXsecondary':1,'HXprimary':1},'originalNewton':raw['newton'],'fluid':raw['fluid'],'allSourceMapsHeld':True,'productResidualCalled':False,'productNewtonCalled':False,'ordinarySolvedOutputUsed':False,'elapsed_s':time.monotonic()-START,'affinity':sorted(os.sched_getaffinity(0))}
(Q/'PREPARED-IDENTITIES.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PREPARED_INPUT_IDENTITIES_COMPLETE',flush=True)
