"""Stdlib-only read audit of already completed artifacts; no solver execution."""
from pathlib import Path
import json,hashlib,math,heapq,collections,shutil,datetime
HERE=Path(__file__).resolve().parent
P=HERE.parent
I=P.parents[1]
V=I/'.codex-tmp/liquid-trajectory-ad-probe-v2-20261006'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ident(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def read(p):return json.loads(p.read_text())
def main():
 completed=P/'completed';oracle=P/'root-independent-oracle';execution=V/'run-b69f-actual-thermal'
 raw=read(completed/'executed.liquidrun');actual=read(completed/'result.json');probe=read(execution/'result.json');terminal=read(execution/'worker-terminal.json');sensitivity=read(oracle/'sensitivity.json');refinement=read(oracle/'refinement.json')
 for name,row in read(completed/'manifest.json').items():
  path=completed/name;assert ident(path)['bytes']==row['bytes'] and sha(path)==row['sha256']
 for row in read(V/'manifest.json')['files']:
  path=V/row['path'];assert path.stat().st_size==row['bytes'] and sha(path)==row['sha256']
 for row in probe['sources']:
  path=I/row['repoRelative'];assert path.stat().st_size==row['bytes'] and sha(path)==row['sha256']
 for row in probe['input_identities'].values():
  path=Path(row['path']);assert path.stat().st_size==row['bytes'] and sha(path)==row['sha256']
 assert sensitivity['input_sha256']==sha(completed/'executed.liquidrun') and sensitivity['result_sha256']==sha(completed/'result.json') and sensitivity['assembler_sha256']==sha(I/'tools/shinaide_liquid_verification.py')
 assert probe['clock']['N_t']==50 and all(x['samples']==50 and x['state_size']==1128 for x in probe['runs']) and len(probe['runs'])==5
 assert terminal['returncode']==1 and terminal['timed_out'] is False and terminal['incomplete'] is False and terminal['events']==[] and probe['passed'] is False
 flow={eid:actual['edge_flow_kg_s'][i][0] for i,eid in enumerate(actual['edge_ids'])};counts=collections.Counter(actual['cell_edge_ids']);rho=raw['fluid']['rho_kg_m3'];graph=collections.defaultdict(list)
 for edge in raw['edges']:
  f=flow[edge['id']]
  if f==0:continue
  up,down=(edge['from'],edge['to']) if f>0 else (edge['to'],edge['from']);params=edge['params'];points=params.get('waypoints_m',[])
  length=sum(math.dist(a,b) for a,b in zip(points,points[1:]));diameter=params.get('D_m',0);volume=math.pi*diameter*diameter*length/4
  residence=rho*volume/abs(f) if edge['type']=='pipe' else 0
  graph[up].append((down,residence,edge['id'],counts[edge['id']]))
 start='m_30_supply';dist={start:0};paths={start:[]};queue=[(0,start)]
 while queue:
  cost,node=heapq.heappop(queue)
  if cost!=dist[node]:continue
  for down,residence,eid,ncell in graph[node]:
   value=cost+residence
   if value<dist.get(down,float('inf')):
    dist[down]=value;paths[down]=paths[node]+[{'edge_id':eid,'pipe_residence_s':residence,'cells':ncell}];heapq.heappush(queue,(value,down))
 routes=[]
 for edge in raw['edges']:
  if edge['type']!='cold_plate':continue
  up=edge['from'] if flow[edge['id']]>0 else edge['to'];assert up in dist
  routes.append({'edge_id':edge['id'],'pipe_residence_s':dist[up],'cells':sum(x['cells'] for x in paths[up]),'path':paths[up]})
 routes.sort(key=lambda x:x['pipe_residence_s']);solid=math.fsum(row[-1] for row in actual['chip_temp_K'])/len(actual['chip_temp_K'])
 final=sensitivity['final'];directions=probe['directions'];heat_delta=directions['post_t0_heat_amplitude']['AD']-final['heat_mean_solid'];inlet_delta=directions['post_t0_primary_inlet_amplitude']['AD']-final['inlet_mean_solid']
 primary=actual['primary_return_K'][0];secondary=actual['secondary_supply_K'][0];circuit=raw['primary_circuits'][0];ua=raw['parameter_pack']['profiles']['hx-demo']['params']['UA_W_per_K'];capacity=circuit['rho_kg_m3']*circuit['cp_J_kgK']*circuit['primary_volume_m3'];mc=circuit['flow_kg_s']*circuit['cp_J_kgK']
 snippets={}
 selections={'src/backend/liquidcooling/dae_experiment.py':[(79,84)],'src/backend/liquidcooling/solver/equation_graph.py':[(493,505)],'src/backend/liquidcooling/graphcompiling/compiler.py':[(208,222),(254,280)],'tools/shinaide_liquid_verification.py':[(76,81),(108,110),(135,169)]}
 for name,ranges in selections.items():
  lines=(I/name).read_text().splitlines();snippets[name]={'identity':ident(I/name),'lines':{str(n):lines[n-1] for lo,hi in ranges for n in range(lo,hi+1)}}
 report={'schema':'dcdtuni-independent-actual-thermal-trajectory-review/v1','createdUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reviewOutcome':'Scope sound; original v2 AD probe remains FAIL due an insensitive inlet objective. New independently justified objective recommended without changing the guard.','executionScope':'This audit used only stdlib JSON/source/hash reads and route arithmetic. No DAE, oracle, sensitivity, FD or solver was rerun; no browser/API/service/process mutation. Only audit files created inside the repository.',
 'run_id':actual['run_id'],'completedInputsVerified':6,'designPayloadsVerified':len(read(V/'manifest.json')['files']),'productionSourceFilesVerified':len(probe['sources']),
 'actualBaselineObjective':{'mean_solid_K':solid,'AD_baseline_mean_solid_K':probe['runs'][0]['objective_K'],'difference_K':probe['runs'][0]['objective_K']-solid,'caveat':'This direct audit verifies the final mean objective only; the probe did not publish all recomputed thermal states for an all-state equality gate.'},
 'v2NumericalOutcome':{'passed':False,'heat_direction':directions['post_t0_heat_amplitude'],'inlet_direction':directions['post_t0_primary_inlet_amplitude'],'five_full_trajectories':True,'workerReturncode':terminal['returncode'],'naturalExit':True,'workerPIDNoLongerPresent':not (Path('/proc')/str(terminal['pid'])).exists(),'timeoutTerminationExercised':False,'fuserEvents':terminal['events'],'lifecycleLimitation':'Actual natural-exit recording is proved; mocked ownership tests do not constitute an actual timeout/fuser termination validation.'},
 'independentPhysicalSensitivityReview':{'equation':'(I-dt*A)s_i=s_(i-1)+dt*db_i; s_0=0. Heat forcing db_solid=(Q_i-Q_0)/Csolid; inlet forcing db_primary=m_primary*cp_primary*(Tin_i-Tin_0)/Cprimary. Same static A, actual accepted .5 s grid and flows.','sensitivityFinal':final,'heatAD_minusIndependent':heat_delta,'inletSolidAD_minusIndependent':inlet_delta,'interpretation':'The observed near-zero inlet-to-solid derivative is independently reproduced; it is an insensitive objective within this horizon, not an established AD fault.','recommendedInletObjective':'mean primary_return_temperature at final actual sample; separately retain mean solid_temperature as heat objective. Preserve all five full trajectories, epsilon=1e-4 and nonzero-derivative guard.','scopeLimit':'No event-time derivative, UI/JSON differentiation, dynamic hydraulic/property derivative, second-order derivative or global differentiability proof.'},
 'transportInterpretation':{'formula':'pipe mean residence = rho*pi*D^2*polyline_length/(4*abs(actual_flow)) summed along actual positive-flow routes from HX supply to cold-plate inlet. Matches compiler straight waypoint polyline length.','shortest':routes[0],'longest':routes[-1],'coldPlatesReached':len(routes),'horizon_s':24.5,'caveat':'Mean residence is not a strict delay: finite-volume upwinding and backward Euler yield small instantaneous tails. This is mock-parameter geometry; no vendor calibration or measured transport claim.'},
 'twoFluidPrimaryChannel':{'UA_W_K':ua,'mcp_W_K':mc,'primary_capacity_J_K':capacity,'local_primary_relaxation_s':capacity/(ua+mc),'actual_primary_return_final_minus_initial_K':primary[-1]-primary[0],'actual_primary_return_peak_minus_initial_K':max(primary)-primary[0],'actual_secondary_final_minus_initial_K':secondary[-1]-secondary[0],'actual_secondary_peak_minus_initial_K':max(secondary)-secondary[0]},
 'independentOracleReview':{'BE_equation':'(I-dt*A)x_i=x_(i-1)+dt*b_i','continuousReference':'exp of augmented affine matrix for each actual accepted b_i held across original interval, not continuously interpolated ramp forcing.','thermal_states':refinement['dynamic_thermal_states'],'max_BE_product_error_K':refinement['max_independent_BE_error_K'],'max_node_mixing_error_K':refinement['max_independent_node_mixing_error_K'],'independent_substep_errors_K':refinement['independent_substep_reference']['max_error_K'],'independent_substep_orders':refinement['independent_substep_reference']['observed_orders'],'interpretation':'Independent BE matches original 50-sample product and first-order temporal refinement is demonstrated for the sampled-hold reference. This is not a production dt-refinement run or MMS construction.'},
 'boundFiles':[ident(completed/name) for name in read(completed/'manifest.json')]+[ident(V/name) for name in ['trajectory_ad_probe.py','owned_worker.py','source-identity.json','manifest.json']]+[ident(execution/name) for name in ['result.json','worker-terminal.json']]+[ident(oracle/name) for name in ['oracle.py','result.json','refinement.py','refinement.json','sensitivity.py','sensitivity.json']]}
 assert abs(heat_delta)<1e-12 and abs(inlet_delta)<1e-30 and abs(probe['runs'][0]['objective_K']-solid)<1e-10
 (HERE/'source-excerpts.json').write_text(json.dumps(snippets,indent=2)+'\n');(HERE/'audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
 snapshots=HERE/'reviewed-reports';snapshots.mkdir()
 for source,label in [(execution/'result.json','ad-v2-red-result.json'),(execution/'worker-terminal.json','ad-v2-natural-exit.json'),(oracle/'result.json','independent-BE-result.json'),(oracle/'refinement.json','independent-substep-result.json'),(oracle/'sensitivity.json','independent-sensitivity.json')]:shutil.copyfile(source,snapshots/label)
 rows=[]
 for p in sorted(HERE.rglob('*')):
  if p.is_file() and p.name!='manifest.json':rows.append({'path':str(p.relative_to(HERE)),'bytes':p.stat().st_size,'sha256':sha(p)})
 manifest={'schema':'dcdtuni-independent-trajectory-review-manifest/v1','files':rows};(HERE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(json.dumps({'audit':str(HERE/'audit.json'),'manifestSHA':sha(HERE/'manifest.json'),'payloads':len(rows),'bytes':sum(x['bytes'] for x in rows),'v2Passed':False,'heatIndependentDifference':heat_delta,'inletSolidIndependentDifference':inlet_delta,'shortestResidence_s':routes[0]['pipe_residence_s'],'longestResidence_s':routes[-1]['pipe_residence_s']}))
if __name__=='__main__':main()
