from pathlib import Path
import os,json,hashlib,copy,math,struct,gzip,numpy as np
os.sched_setaffinity(0,{39})
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni'); A=B/'.codex-tmp/goal-20261005/air'; O=A/'.codex-tmp/full-mms-asymptotic-runtime-v4-20261007-exclusive'; F=A/'.codex-tmp/full-mms-asymptotic-continuation-prepared-v4-shared-20261007'; Z=B/'.codex-tmp/goal-20261005/integration/.codex-tmp/shinaide-ring-full-mms-private-20261006/FROZEN-v1'; W=B/'.codex-tmp/mms-v4-actual-offline-independent-20261007'
records={}
def read(p):
 p=Path(p); data=p.read_bytes(); row={'path':str(p),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}; previous=records.setdefault(str(p),row); assert previous==row,('read-drift',str(p)); return data
def load(p):return json.loads(read(p))
def bits(a,b):
 a=np.asarray(a,dtype=np.float64); b=np.asarray(b,dtype=np.float64);return a.shape==b.shape and a.tobytes()==b.tobytes()
def norm(a):return {'final_inf':float(np.max(np.abs(a[-1]))),'whole_trajectory_inf':float(np.max(np.abs(a))),'all_entries':int(a.size)}
def checked_npz(p):
 read(p);return np.load(p,allow_pickle=False)
def verify_manifest(root,name,expected):
 assert hashlib.sha256(read(root/name)).hexdigest()==expected
 m=load(root/name); rows=m.get('payload',m.get('payloads'));total=0
 for row in rows:
  data=read(root/row['path']);assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],row['path'];total+=len(data)
 return {'manifestSHA':expected,'payloadCount':len(rows),'payloadBytes':total,'FULLMismatchCount':0}
prepared=verify_manifest(F,'manifest.json','4ee6429209cbf4428b58609a1b0d68da173f96838b35081c4642fcb63cd4f381')
zm=load(Z/'manifest.json');zsha=hashlib.sha256(read(Z/'manifest.json')).hexdigest();assert zsha=='49c2e46f714605d9a60288f55334e9bfa28d76d3867edf1675fb8f93005aef45'
original=verify_manifest(Z,'manifest.json',zsha)
cohort=load(F/'cohort-copy-bindings.json'); copies=[x for x in cohort['copies'] if x['path'].startswith('after/')]; assert len(copies)==505 and sum(x['bytes'] for x in copies)==8198768
before=load(O/'execution-before.json');after=load(O/'execution-after.json');assert before['manifestSHA']==prepared['manifestSHA'];assert after['completedNewLevels']==2 and after['failure'] is None and after['afterVerificationError'] is None and after['newNewtonLevelsOnly']==['0p03125','0p015625'] and after['oldGridNewtonReruns']==0 and after['normalSourceWrites']==0 and after['serviceCalls']==0 and after['GPU'] is False and after['elapsedSeconds']<1800
assert before['CPU']==[10,11] and before['perGridSeconds']==900 and before['studyDeadlineSeconds']==1800 and before['productionDeadlineChanged'] is False
for key in ('normal',):assert before[key]['count']==after[key]['count']==48 and before[key]['drifts']==after[key]['drifts']==[]
normal=load(F/'current-normal48-guards.json');assert len(normal['guards'])==48
for row in normal['guards']:
 data=read(row['path']);assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
aggregate=load(O/'asymptotic-results.json');families=['node_pressure_Pa','flow_kg_s','node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K'];counts=[229,286,229,892,56,1,1];thermal=families[2:];rows=[]
frameNames=dict(zip(families,['node_pressure_Pa','edge_flow_kg_s','node_temperature_K','cell_temperature_K','chip_temp_K','secondary_supply_K','primary_return_K']))
for tag,dt,N in [('0p03125',.03125,257),('0p015625',.015625,513)]:
 G=O/('grid-'+tag); statuses=[]
 for stage in ['generate-new-grid','compile-original-React-producer','original-Newton-new-grid','independent-original-thermal-recurrence']:
  s=load(G/'logs'/(stage+'.status.json'));assert s['returncode']==0 and not s['timedOut'] and s['exception'] is None;assert s['cwd']==str(G);assert s['env']['CUDA_VISIBLE_DEVICES']=='' and s['env']['MMS_ASYMPTOTIC_TAG']==tag and s['env']['PYTHONPATH']==str(G/'after/src');read(G/'logs'/(stage+'.stdout'));read(G/'logs'/(stage+'.stderr'));statuses.append(s)
 react=read(G/'logs/compile-original-React-producer.stdout').decode();assert '1 passed' in react and 'fine.test.ts' in react
 for c in copies:
  for p in [F/c['path'],G/c['path'],Path(c['source'])]:
   data=read(p);assert len(data)==c['bytes'] and hashlib.sha256(data).hexdigest()==c['sha256']
 for worker in ['generate_asymptotic.py','newton_asymptotic.py','independent_asymptotic_recurrence.py','independent_trajectory.py','independent_project_fingerprint.cjs']:
  assert read(G/worker)==read(F/worker)
 assert read(G/'fine.test.ts')==read(F/'fine.test.ts')
 binding=load(G/'input-binding.json');assert binding['sourceCount']==505 and binding['sourceBytes']==8198768 and binding['levelsOnly']==[tag]
 for x in binding['original']:
  d=read(x['path']);assert len(d)==x['bytes'] and hashlib.sha256(d).hexdigest()==x['sha256']
 raw=checked_npz(G/'results'/('newton-vs-manufactured-'+tag+'.npz'));target=checked_npz(G/'results'/('target-dt-'+tag+'.npz')); summary=load(G/'results/mms-asymptotic-single.json')['runs'][0];opts=load(G/'results/actual-source-options-and-layout.json');assert summary['tag']==tag and summary['dt']==dt and summary['frames']==N and summary['checkpoint_replay'] is False;assert opts['effective_cpu']==[10,11] and opts['cuda']=='' and opts['backendModule']==str(G/'after/src/backend/liquidcooling/dae_runtime.py')
 assert opts['newton']=={'max_iterations':40,'atol':1e-10,'rtol':1e-10,'max_backtracks':18};assert opts['freePressureNativeOrder']==[i for i in range(229) if i!=1]
 times=np.arange(N,dtype=np.float64)*dt;assert bits(raw['times'],times) and bits(target['times'],times) and times[-1]==8
 errors={}
 for k,width in zip(families,counts):
  a=raw[k+'_actual'];r=raw[k+'_reference'];e=raw[k+'_error'];assert a.shape==r.shape==e.shape==(N,width) and all(x.dtype==np.float64 and np.isfinite(x).all() for x in (a,r,e));assert bits(a-r,e) and bits(r,target[k]) and bits(a[0],r[0]);errors[k]=norm(e);assert errors[k]==summary['errors'][k]
 for suffix in ('actual','reference','error'):
  flat=np.concatenate([raw[families[0]+'_'+suffix][:,opts['freePressureNativeOrder']]]+[raw[k+'_'+suffix] for k in families[1:]],axis=1);assert flat.shape==(N,1693) and bits(flat,raw['flat1693_'+suffix])
 assert bits(raw['flat1693_actual']-raw['flat1693_reference'],raw['flat1693_error']);assert bits(raw['flat1693_actual'][0],raw['flat1693_reference'][0])
 run=load(G/'results'/('manufactured-dt-'+tag+'.liquidrun'));old=load(G/'results/original-anchor-0p0625.liquidrun');bench=run['manufactured_benchmark'];ob=old['manufactured_benchmark'];assert run['schemaVersion']==4 and run['time']==dict(old['time'],dt_s=dt,N_t=N) and run['analysis']=={'mode':'transient','initialization':'consistent'}
 restored=copy.deepcopy(run);anchor=copy.deepcopy(old);del restored['manufactured_benchmark'];del anchor['manufactured_benchmark'];restored['time']['dt_s']=old['time']['dt_s'];restored['time']['N_t']=old['time']['N_t'];restored['source_project']['sha256']=old['source_project']['sha256'];assert restored==anchor
 nodeids=[x['id'] for x in run['nodes']];edgeids=[x['id'] for x in run['edges']];assert len(nodeids)==229 and len(set(nodeids))==229 and len(edgeids)==286 and len(set(edgeids))==286
 waterids=[x['id']+'/cell/'+str(i) for x in run['edges'] if x['type']=='pipe' for i in range(x['params']['n_cells'])];solidids=[x['id'] for x in run['edges'] if x['type']=='cold_plate'];hxids=[x['id'] for x in run['edges'] if x['type']=='heat_exchanger'];ids=dict(zip(families,[nodeids,edgeids,nodeids,waterids,solidids,hxids,hxids]));assert list(map(len,ids.values()))==counts
 for k in families:
  a=bench['initial_state'][k];b=ob['initial_state'][k];assert a['ids']==b['ids']==ids[k] and bits(a['values'],b['values']) and bits(a['values'],raw[k+'_actual'][0])
 assert bench['initial_temperature_rate']==ob['initial_temperature_rate'] and bench['flow_signs']['ids']==edgeids
 signs=np.array(bench['flow_signs']['values']);assert np.all(np.sign(raw['flow_kg_s_actual'])==signs) and np.all(np.sign(raw['flow_kg_s_reference'])==signs)
 srcids={'momentum_Pa':edgeids,'water_W':waterids,'solid_W':solidids,'hx_secondary_W':hxids,'hx_primary_W':hxids};scalars=0
 for k in srcids:
  q=bench['sources'][k];v=np.asarray(q['values'],dtype=np.float64);assert q['ids']==srcids[k] and v.shape==(len(srcids[k]),N) and np.isfinite(v).all();scalars+=v.size
 assert scalars==1236*N and scalars<4000000 and bits(bench['times_s'],times)
 layout={'nodes':[{'id':x['id'],'type':x['type']} for x in run['nodes']],'edges':[{'id':x['id'],'type':x['type'],'from':x['from'],'to':x['to'],'n_cells':x['params'].get('n_cells') if x['type']=='pipe' else None} for x in run['edges']]};assert hashlib.sha256(json.dumps(layout,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()==bench['graph_layout_sha256']
 assert len(json.dumps(bench,allow_nan=False).encode())<32*1024*1024
 typed=copy.deepcopy(bench);typed['times_s']=list(map(float,typed['times_s']))
 for q in typed['sources'].values():q['values']=[[float(v) for v in row] for row in q['values']]
 for family in ('initial_state','initial_temperature_rate'):
  for q in typed[family].values():q['values']=list(map(float,q['values']))
 typed['flow_signs']['values']=list(map(float,typed['flow_signs']['values']));typed.pop('source_sha256');canon=json.dumps(typed,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode();assert hashlib.sha256(canon).hexdigest()==bench['source_sha256'] and len(canon)<32*1024*1024
 gate=load(G/'results/generated-source-gates.json');assert gate['source_scalars']==scalars and gate['samples']==N and gate['source_caps_unchanged'] and gate['original_coefficient_bits_exact'] and gate['compact_bytes']<32*1024*1024
 actualcycle=checked_npz(G/'results/fine-independent-cycle-construction.npz');frozencycle=checked_npz(G/'results/frozen-original-cycle.npz');coef=[]
 for k in ['B','C','a0','amplitude','phase','xbase','xamplitude','xphase']:
  x=actualcycle[k];y=frozencycle[k];assert x.dtype==y.dtype and x.shape==y.shape and x.tobytes()==y.tobytes();coef.append({'name':k,'shape':list(x.shape),'dtype':str(x.dtype),'sha256':hashlib.sha256(x.tobytes()).hexdigest()})
 frames=load(G/'results'/('mms-frames-'+tag+'.json'));assert len(frames)==N
 for k in families:assert bits(np.asarray([f[frameNames[k]] for f in frames],dtype=np.float64).reshape(N,counts[families.index(k)]),raw[k+'_actual'])
 for i,f in enumerate(frames):assert f['seq']==i and f['time_s']==times[i]
 diagmap={'max_scaled_residual':'scaled_residual','max_energy_balance_W':'energy_balance_W','max_mass_kg_s':'mass_residual_kg_s','max_momentum_Pa':'momentum_residual_Pa'};diagnostics={}
 for k,field in diagmap.items():
  value=max(abs(f['diagnostics'][field]) for f in frames[1:]);assert value==summary[k];diagnostics[k]=value
 assert all(f['diagnostics']['physical_heat_input_W']==21000 for f in frames[1:]);assert all(f['controls']==frames[0]['controls'] for f in frames)
 recur=load(G/'recurrence-result.json');assert recur['unknowns']==1179 and recur['nodes']==229 and recur['storages']==950 and recur['affinity']==[10,11] and recur['CUDA_VISIBLE_DEVICES']=='';rec=recur['runs'][0];assert rec['tag']==tag and rec['count']==N and rec['dt']==dt and rec['initial_error_exact_zero'] and rec['all_storage_formula_bit_exact']
 rr=checked_npz(G/'independent-error-recurrence.npz');con=rr[tag+'_independent_error_contributions'];total=rr[tag+'_independent_total_error'];product=rr[tag+'_product_error'];assert con.shape==(N,1179,4) and total.shape==product.shape==(N,1179) and all(np.isfinite(x).all() for x in (con,total,product));assert bits(rr[tag+'_times'],times) and bits(con.sum(axis=2),total) and bits(np.concatenate([raw[k+'_error'] for k in thermal],axis=1),product)
 cursor=0;indErrors={}
 for k,width in zip(thermal,counts[2:]):
  e=total[:,cursor:cursor+width];p=product[:,cursor:cursor+width];v={'independent_final_inf_K':float(np.max(np.abs(e[-1]))),'independent_whole_inf_K':float(np.max(np.abs(e))),'product_prediction_diff_inf_K':float(np.max(np.abs(p-e)))};assert v==rec['errors'][k];indErrors[k]=v;cursor+=width
 assert cursor==1179;assert float(total[-1,-2])==rec['final_HXsecondary_signed_error_K'] and float(total[-1,-1])==rec['final_HXprimary_signed_error_K'] and bits(con[-1,-2],rec['HXsecondary_final_contributions_K']) and bits(con[-1,-1],rec['HXprimary_final_contributions_K']);assert max(v['product_prediction_diff_inf_K'] for v in indErrors.values())<5e-10
 combined=next(x for x in aggregate['runs'] if x['tag']==tag)
 for k,v in summary.items():assert combined[k]==v
 assert combined['independentRecurrence']==rec and combined['gridElapsedSecondsIncludingPrep']<900
 rows.append({'tag':tag,'dt':dt,'frames':N,'statuses':[{'stage':s['label'],'returncode':s['returncode'],'elapsedSeconds':s['elapsedSeconds']} for s in statuses],'full1693FlatBitsExact':True,'all7BlockReferenceTargetBitsExact':True,'allFramesActualBitsExact':True,'initialAll7TypedIEEEBitsExact':True,'all505OriginalSourcesActual2GridExact':True,'sourceSHA':bench['source_sha256'],'sourceCompactBytesSchemaTyped':len(canon),'sourceScalars':scalars,'sourceIDsNativeOrderCounts':[len(x) for x in srcids.values()],'coefficient8':coef,'stateNorms':errors,'diagnostics':diagnostics,'independent1179':indErrors,'continuousForcingResidualReported':rec['continuous_original_forcing_residual_inf_W'],'recurrenceResidualReported':rec['independent_error_recurrence_residual_inf_W']})
originalSummary=load(F/'original-data/results/mms-refinement.json');assert aggregate['originalCoarseRuns']==originalSummary['runs'] and aggregate['originalCoarseFinalOrders']==originalSummary['orders'];assert aggregate['originalCoarseHXsecGateRemainsRED'] is True and aggregate['newFinerRatesDoNotRewriteOriginalGate'] is True and aggregate['noHydraulicTemporalOrderClaim'] is True
anchors=[]
for anchor in originalSummary['runs'][-2:]:
 tag=anchor['tag'];p=O/'readonly-old-anchor-replay/results'/('newton-vs-manufactured-'+tag+'.npz');r=checked_npz(p)
 archived=read(F/'original-data/archives/results'/('newton-vs-manufactured-'+tag+'.npz.gz'));assert gzip.decompress(archived)==read(p)
 for k,v in anchor['errors'].items():assert bits(r[k+'_actual']-r[k+'_reference'],r[k+'_error']) and norm(r[k+'_error'])==v
 assert aggregate['runs'][len(anchors)]==anchor;anchors.append({'tag':tag,'rawFile':records[str(p)],'all7RawNormsExact':True,'frozenCompressedAnchorExact':True,'oldNewtonRerun':False})
rates={k:{metric:[math.log(aggregate['runs'][i]['errors'][k][metric]/aggregate['runs'][i+1]['errors'][k][metric],2) for i in range(3)] for metric in ['final_inf','whole_trajectory_inf']} for k in thermal};assert rates==aggregate['rates']
for path,row in records.items():
 d=Path(path).read_bytes();assert len(d)==row['bytes'] and hashlib.sha256(d).hexdigest()==row['sha256'],('after-drift',path)
result={'schema':'independent-closed-MMS-v4-numerical-audit-v1','scope':'Read-only raw array arithmetic, hashes and norms; no generator/React/product admission/Newton/LU/reference solve/native/GPU/service/normal mutations','CPU':[39],'preparedFULL':prepared,'originalFULL':original,'twoActualGridRows':rows,'oldAnchors':anchors,'EOCRecomputedExact':rates,'originalCoarseHXsecREDPreserved':True,'currentBackend48NoDrift':True,'overallAfter':after,'zeroReadHashDrifts':True,'typedNumericSourceSHASemantics':'Vector/Matrix Numbers float normalization before canonical digest as reviewed schema; raw JSON integer-vs-float text hash is not product source hash','limitations':['Synthetic manufactured original mock pack, T8, CPU only; not ordinary-mode/converged real CFD/physical validation or GPU/derivative proof','Raw arrays and source bytes review independent; original1179 matrix/forcing solver executed only by authorized prior runner, not rerun by auditor','Residual values from real frame/recurrence output; do not claim an independent residual oracle evaluation','Backend48 disk guards do not prove loaded service memory identity; no browser/pixel/UI acceptance']}
(W/'FINAL-actual-array-audit.json').write_text(json.dumps(result,indent=2)+'\n');(W/'FINAL-read-bindings.json').write_text(json.dumps({'readCount':len(records),'readBytes':sum(x['bytes'] for x in records.values()),'zeroAfterDrifts':True,'records':list(records.values())},indent=2)+'\n');print(json.dumps({'status':'PASS','grids':2,'bindings':len(records),'bytes':sum(x['bytes'] for x in records.values()),'maxProductIndependentDiffK':[max(v['product_prediction_diff_inf_K'] for v in row['independent1179'].values()) for row in rows],'rates':rates},indent=2))
