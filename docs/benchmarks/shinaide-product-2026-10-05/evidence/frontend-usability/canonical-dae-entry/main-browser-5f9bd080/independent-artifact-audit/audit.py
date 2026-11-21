
"""Pure artifact/hash audit. No runtime import, graph compile, replay, solver, or HTTP."""
from pathlib import Path
import json,hashlib,copy,math,collections,subprocess,datetime,sys
MAIN=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
IT=MAIN/'.codex-tmp/goal-20261005/integration'
B=MAIN/'.codex-tmp/goal-20261005/canonical-dae-entry-20261005'
OUT=IT/'.codex-tmp/canonical-dae-steady-independent-20261005'
SERVED='5f9bd080c367db13a7f4e6a534a4811154eb7487'
RUNS={'steady':'9091fb6e333a45fe850f9e3bcfa138dd','transient':'bcd47fca756d4994bcfc0f78de84b226'}
FILES=['input.json','input.liquidrun','result.json','checkpoint.json','executed.liquidrun','executed-inputs.json']
checks=[]
def check(name,value):
    checks.append({'name':name,'passed':bool(value)})
    if not value:raise AssertionError(name)
def j(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def ident(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def finite_walk(x):
    if isinstance(x,bool) or x is None or isinstance(x,str):return 0
    if isinstance(x,(int,float)):
        if not math.isfinite(x):raise AssertionError('nonfinite numeric artifact')
        return 1
    if isinstance(x,list):return sum(finite_walk(v) for v in x)
    if isinstance(x,dict):return sum(finite_walk(v) for v in x.values())
    raise TypeError(type(x))
def diff(a,b,path=''):
    if type(a)!=type(b):return [{'path':path,'before':a,'after':b}]
    if isinstance(a,dict):
        out=[]
        for k in sorted(a.keys()|b.keys()):
            if k not in a or k not in b:out.append({'path':path+'/'+k,'before':a.get(k),'after':b.get(k),'missingBefore':k not in a,'missingAfter':k not in b})
            else:out.extend(diff(a[k],b[k],path+'/'+k))
        return out
    if isinstance(a,list):
        if len(a)!=len(b):return [{'path':path,'before':a,'after':b}]
        return sum([diff(x,y,path+'/'+str(i)) for i,(x,y) in enumerate(zip(a,b))],[])
    return [] if a==b else [{'path':path,'before':a,'after':b}]
def graph_audit(l,r):
    ids=[n['id'] for n in l['nodes']];es=[e['id'] for e in l['edges']]
    check('unique graph node IDs '+r['run_id'],len(ids)==len(set(ids)))
    check('unique graph edge IDs '+r['run_id'],len(es)==len(set(es)))
    adj={n:set() for n in ids}
    for e in l['edges']:
        check('graph endpoints exist '+e['id']+' '+r['run_id'],e['from'] in adj and e['to'] in adj)
        adj[e['from']].add(e['to']);adj[e['to']].add(e['from'])
    left=set(ids);cc=[]
    while left:
        seen=set();stack=[next(iter(left))]
        while stack:
            v=stack.pop()
            if v in seen:continue
            seen.add(v);stack.extend(adj[v]-seen)
        cc.append(len(seen));left-=seen
    check('result node order bound '+r['run_id'],ids==r['node_ids'])
    check('result edge order bound '+r['run_id'],es==r['edge_ids'])
    hx=[e['id'] for e in l['edges'] if e['type']=='heat_exchanger']
    check('result hx order bound '+r['run_id'],hx==r['hx_edge_ids'])
    check('object bindings exact '+r['run_id'],l['object_bindings']==r['object_bindings'])
    cps={e['id'] for e in l['edges'] if e['type']=='cold_plate'}
    check('result cp identity coverage '+r['run_id'],cps=={c['edge_id'] for c in r['cp_details']})
    return {'nodes':len(ids),'edges':len(es),'racks':len(l['racks']),'coldPlates':len(cps),'heatExchangers':len(hx),'undirectedComponentSizes':sorted(cc),'edgeTypes':dict(collections.Counter(e['type'] for e in l['edges'])),'objectBindingCount':len(l['object_bindings'])}
def pack_audit(l,provenance):
    q=copy.deepcopy(l);pack=q.pop('parameter_pack');bindings={'edges':{}}
    if q.get('fluid_ref') is not None:
        ref=q.pop('fluid_ref');ov=q.get('fluid',{});v=copy.deepcopy(pack['profiles'][ref]['params']);v.update(ov)
        q['fluid']=v;bindings['fluid']={'profile':ref,'overrides':ov}
    for e in q['edges']:
        if e.get('parameter_ref') is not None:
            ref=e.pop('parameter_ref');ov=e.get('params',{})
            check('profile type '+e['id'],pack['profiles'][ref]['type']==e['type'])
            v=copy.deepcopy(pack['profiles'][ref]['params']);v.update(ov);e['params']=v
            bindings['edges'][e['id']]={'profile':ref,'overrides':ov}
    effective=sorted([{'id':e['id'],'type':e['type'],'params':e.get('params',{})} for e in q['edges']],key=lambda e:str(e['id']))
    packsha=digest(pack);resolvedsha=digest({'fluid':q.get('fluid'),'edges':effective})
    check('independent parameter pack digest',packsha==provenance['parameter_pack']['sha256'])
    check('independent resolved parameter digest',resolvedsha==provenance['parameter_pack']['resolved_sha256'])
    check('independent profile bindings',bindings==provenance['parameter_pack']['bindings'])
    return {'packSHA256':packsha,'resolvedSHA256':resolvedsha,'boundProfileCount':len(bindings['edges']),'source':pack['source'],'id':pack['id'],'revision':pack['revision'],'syntheticDisclosurePresent':'Synthetic' in pack.get('description','') and 'not' in pack.get('description','').lower()}
def samples(x,n,path=''):
    if isinstance(x,dict):
        for k,v in x.items():
            if k.endswith(('_K','_Pa','_kg_s','_W')) and isinstance(v,list):
                check('sample dimension '+path+'/'+k,len(v)==n)
            elif isinstance(v,(list,dict)):samples(v,n,path+'/'+k)
    elif isinstance(x,list):
        for i,v in enumerate(x):
            if isinstance(v,(dict,list)):samples(v,n,path+'/'+str(i))
def json_summary_difference(a,b):
    ds=diff(a,b)
    return {'count':len(ds),'paths':[d['path'] for d in ds],'topGroups':dict(collections.Counter(d['path'].split('/')[1] for d in ds))}
OUT.mkdir(parents=True,exist_ok=True)
projectpaths=[B/n for n in ['Shinaide_canonical_before.cfdproj','Shinaide_canonical_after.cfdproj','Shinaide_normalized_steady.cfdproj','Shinaide_normalized_transient.cfdproj']]
heatpath=IT/'docs/benchmarks/shinaide-product-2026-10-05/evidence/frontend-usability/canonical-dae-entry/main-browser-5f9bd080/heat-audit-final.json'
inputpaths=projectpaths+[B/'Liquid/log'/('run-'+v)/f for v in RUNS.values() for f in FILES]+[heatpath]
before={str(p):ident(p) for p in inputpaths}
helper=IT/'src/frontend/shared/js/cfdrun_export.js'
node=r"""const fs=require('fs');const h=require(process.argv[1]);console.log(JSON.stringify(process.argv.slice(2).map(p=>({path:p,hash:h.projectContentHashSync(JSON.parse(fs.readFileSync(p,'utf8')))}))))"""
hashes={x['path']:x['hash'] for x in json.loads(subprocess.check_output(['/usr/bin/node','-e',node,str(helper),*[str(p) for p in projectpaths]],text=True))}
projects={p.stem:j(p) for p in projectpaths}
report={'format':'canonical-dae-browser-artifact-independent-audit/v1','auditedAtUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'servedSourceCommit':SERVED,'mainHEAD':subprocess.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'],text=True).strip(),'integrationHEAD':subprocess.check_output(['git','-C',str(IT),'rev-parse','HEAD'],text=True).strip(),'scope':'Read-only hash/JSON/finite/shape/identity inspection of existing actual browser runs; no graph compile, physical solve, replay, tests, HTTP, or MMS rerun.','inputFiles':list(before.values()),'canonicalProjectHashes':hashes,'runs':{}}
loaded={}
for mode,rid in RUNS.items():
    d=B/'Liquid/log'/('run-'+rid);allj={f:j(d/f) for f in FILES};loaded[mode]=allj
    inp=allj['input.json'];l=allj['input.liquidrun'];r=allj['result.json'];cp=allj['checkpoint.json'];ex=allj['executed.liquidrun'];ei=allj['executed-inputs.json']
    finite_count=sum(finite_walk(v) for v in allj.values())
    check(mode+' input snapshots exact',l==inp['liquidrun']==cp['liquidrun'])
    check(mode+' no controllers/events',l['controllers']==[] and inp['controllers']==[] and l['events']==[])
    mh=digest(l);ch=digest({'liquidrun':l,'controllers':inp['controllers']})
    check(mode+' config hash all artifacts',ch==inp['config_hash']==cp['config_hash']==r['config_hash']==ei['original_config_hash'])
    pv=inp['metadata']['parameter_provenance']
    check(mode+' manifest hash',mh==pv['manifest_sha256'])
    check(mode+' result provenance exact',pv==r['parameter_provenance'])
    check(mode+' source project provenance exact',l['source_project']==pv['source_project'])
    saved=B/('Shinaide_normalized_'+mode+'.cfdproj')
    check(mode+' saved canonical source binding',hashes[str(saved)]==l['source_project']['sha256'])
    check(mode+' result status truly completed',r['status']=='completed' and r['completed'] is True and r['converged'] is True and r['error'] is None)
    check(mode+' checkpoint completed',cp['run_status']=='completed' and cp['error'] is None)
    check(mode+' exact execution cpu2',l['execution']=={'backend':'pytorch','device':'cpu','dtype':'float64','max_threads':2} and inp['metadata']['execution']['actual_threads']==2)
    check(mode+' analysis labels',l['analysis']==r['analysis']==inp['metadata']['analysis'] and r['analysis']=={'mode':mode,'initialization':'steady'})
    n=l['time']['N_t'];dt=l['time']['dt_s'];times=[float(i*dt) for i in range(n)]
    check(mode+' exact time axis',r['time_s']==times and n==(1 if mode=='steady' else 181))
    check(mode+' exact checkpoint sample',cp['seq']==n-1)
    check(mode+' accepted complete horizon',ei['accepted_sample_count']==n and ei['original_sample_count']==n and ei['accepted_time_s']==times[-1] and ei['complete_original_horizon'] is True)
    check(mode+' no online commands',r['applied_inputs']==cp['applied_inputs']==[])
    check(mode+' no queued online targets',cp['pending_pump_rpm_ratios']==cp['pending_valve_openings']==cp['pending_primary_inlet_K']=={})
    check(mode+' constant accepted controls',r['heat_multipliers']==[1.0]*n and cp['heat_multipliers']==[1.0]*n)
    for k in ['pump_rpm_ratios','valve_openings','primary_inlet_K']:
        check(mode+' checkpoint controls '+k,r[k]==cp[k])
        check(mode+' constant controls '+k,len(r[k])==n and all(x==r[k][0] for x in r[k]))
    dims={}
    rowcounts={'node_pressure_Pa':len(l['nodes']),'node_temperature_K':len(l['nodes']),'edge_flow_kg_s':len(l['edges']),'edge_outlet_temp_K':len(l['edges']),'chip_temp_K':56,'water_outlet_temp_K':56,'primary_return_K':1,'secondary_supply_K':1,'cell_temp_K':len(r['cell_edge_ids'])}
    for k,count in rowcounts.items():
        check(mode+' array rows '+k,len(r[k])==count)
        check(mode+' array samples '+k,all(len(row)==n for row in r[k]));dims[k]=[count,n]
    for k in ['controls','diagnostics']:check(mode+' sample dimensions '+k,len(r[k])==n)
    samples(r['cp_details'],n,'cp_details');samples(r['rack_summaries'],n,'rack_summaries')
    check(mode+' cell geometry binding',len(r['cell_positions'])==len(r['cell_edge_ids']) and set(r['cell_edge_ids']).issubset(set(r['edge_ids'])))
    eh=digest({'liquidrun':ex,'controllers':inp['controllers']})
    check(mode+' executed config binding',eh==ei['executed_config_hash'])
    collapsed=copy.deepcopy(ex);collapsed['source']=l['source'];series=[]
    for i,e in enumerate(collapsed['edges']):
        for k,v in list(e.get('params',{}).items()):
            orig=l['edges'][i].get('params',{}).get(k)
            if isinstance(v,list) and not isinstance(orig,list):
                check(mode+' accepted edge scalar '+e['id']+'/'+k,len(v)==n and all(x==orig for x in v))
                series.append(e['id']+'/'+k);e['params'][k]=orig
    for i,c in enumerate(collapsed['primary_circuits']):
        for k,v in list(c.items()):
            orig=l['primary_circuits'][i].get(k)
            if isinstance(v,list) and not isinstance(orig,list):
                check(mode+' accepted circuit scalar '+k,len(v)==n and all(x==orig for x in v));series.append(c['edge_id']+'/'+k);c[k]=orig
    check(mode+' executed controls only same physics',collapsed==l)
    g=graph_audit(l,r);pa=pack_audit(l,pv)
    savedj=j(saved);exp=savedj['runDefaults']['liquid']['experiment']
    normalized_saved_pack=copy.deepcopy(exp['parameterPack'])
    pack_schema_defaults=[]
    for pid,profile in normalized_saved_pack['profiles'].items():
        if 'description' not in profile:
            profile['description']='';pack_schema_defaults.append('/profiles/'+pid+'/description')
    check(mode+' saved materialized parameter pack plus declared empty descriptions',normalized_saved_pack==l['parameter_pack'])
    report.setdefault('savedPackSchemaDefaults',{})[mode]=pack_schema_defaults
    for k in ['analysis','newton','execution','primary_circuits']:check(mode+' saved dae '+k,exp['dae'][k]==l[k])
    check(mode+' no generated load step',exp['settings']['loadStep'] is False)
    cpq={}
    for rack in l['racks']:
        for server in rack['servers']:
            for i,pair in enumerate(server['cp_cu_pairs']):cpq[(rack['id'],server['id']+'/'+str(i))]=(pair['edge_id'],pair['Q_W'])
    cp_tr=[t for t in savedj['transient']['tracks'] if t['param']=='cp.heat']
    check(mode+' t0 cp heat complete',len(cp_tr)==56==len(cpq))
    for t in cp_tr:
        edgeid,qw=cpq[(t['modelUid'],t['target'])]
        check(mode+' t0 cp heat '+edgeid,t['keyframes']==[{'t':0,'value':qw}] and next(e for e in l['edges'] if e['id']==edgeid)['params']['Q_W']==qw)
    check(mode+' all saved tracks t0 only',all(len(t['keyframes'])==1 and t['keyframes'][0]['t']==0 for t in savedj['transient']['tracks']))
    report['runs'][mode]={'runId':rid,'schemaVersion':l['schemaVersion'],'version':l['version'],'manifestSHA256':mh,'configSHA256':ch,'sourceProjectSHA256':l['source_project']['sha256'],'executedFileSHA256':sha(d/'executed.liquidrun'),'executedConfigSHA256':eh,'parameterPack':pa,'graph':g,'execution':inp['metadata']['execution'],'analysis':r['analysis'],'N_t':n,'dt_s':dt,'timesFirstLast':[times[0],times[-1]],'expectedDurationS':r['expected_duration_s'],'status':r['status'],'completed':r['completed'],'numericallyConverged':r['converged'],'epochs':r['epochs'],'finalLoss':r['final_loss'],'finiteNumericValuesInspected':finite_count,'dimensions':dims,'appliedInputsCount':len(r['applied_inputs']),'acceptedScalarSeriesCount':len(series),'acceptedScalarSeriesKeys':series,'diagnosticsFirst':r['diagnostics'][0],'diagnosticsLast':r['diagnostics'][-1],'maxAbsDiagnosticValues':{k:max(abs(x[k]) for x in r['diagnostics']) for k in r['diagnostics'][0]},'savedTrackCount':len(savedj['transient']['tracks']),'savedTrackGroups':dict(collections.Counter(t['param'] for t in savedj['transient']['tracks'])),'experimentDurationS':exp['duration_s'],'experimentDtS':exp['dt_s'],'timeline':{k:savedj['transient'][k] for k in ['mode','airMode','liquidMode','durationS','gridS','start']},'resultAndCheckpointIdsBound':r['run_id']==cp['run_id']==inp['run_id']==rid,'resultJSONSHA256':sha(d/'result.json')}
a=loaded['steady']['input.liquidrun'];t=loaded['transient']['input.liquidrun']
physicalkeys=['nodes','edges','racks','fluid','gravity','initial_conditions','object_bindings','parameter_pack','primary_circuits','newton','execution','units','equation_system','controllers','events']
for k in physicalkeys:check('steady/transient identical physical manifest '+k,a[k]==t[k])
for k in ['geometry','models','constructions','instrumentation','meta']:check('steady/transient saved identical '+k,projects['Shinaide_normalized_steady'][k]==projects['Shinaide_normalized_transient'][k])
check('transient liquid schedule identity count',len(t['transient']['tracks'])==62)
report['crossRunPhysicalIdentity']={'exactEqualManifestKeys':physicalkeys,'exactEqualSavedProjectKeys':['geometry','models','constructions','instrumentation','meta'],'manifestChangedKeys':[k for k in a.keys()|t.keys() if a.get(k)!=t.get(k)],'savedProjectDiff':json_summary_difference(projects['Shinaide_normalized_steady'],projects['Shinaide_normalized_transient'])}
raw=projects['Shinaide_canonical_before'];sv=projects['Shinaide_normalized_steady']
check('raw missing experiment',raw.get('runDefaults',{}).get('liquid',{}).get('experiment') is None)
check('raw and saved canonical distinct',hashes[str(B/'Shinaide_canonical_before.cfdproj')]!=hashes[str(B/'Shinaide_normalized_steady.cfdproj')])
report['normalSaveScope']={'rawInputSchema':raw['schemaVersion'],'savedSchema':sv['schemaVersion'],'rawExperimentMissing':True,'canonicalHashMatchesRuntimeSource':True,'byteIdentityWithRawBefore':False,'projectRevisionReported':loaded['steady']['input.json']['project_revision'],'preSaveMemorySnapshotFileAvailable':False,'explanation':'Runtime source hash exactly matches normal-saved canonical disk content. No preSave in-memory snapshot file was captured; metadata/revision byte identity with that memory snapshot is not claimed. NormalSave v4→v5 normalization and explicit synthetic parameter materialization change canonical content relative to raw missing-experiment input.','normalizationDiff':json_summary_difference(raw,sv)}
ha=j(heatpath);finiteheat=finite_walk(ha);tr=loaded['transient']['result.json']
check('heat audit raw expected SHA',sha(heatpath)=='914bd534575bf6da2c52c052b87b8aebb6ccc63950af28951b1b00a9b3626de6')
check('heat audit exact accepted canonical JSON binding',ha['accepted_input_sha256']==digest(loaded['transient']['executed.liquidrun']))
check('heat audit run/config binding',ha['liquid_run_id']==RUNS['transient'] and ha['liquid_config_hash']==tr['config_hash'] and ha['project_uid']==sv['uid'])
check('heat audit accepted times exact',ha['time_s']==tr['time_s'] and ha['heat_multipliers']==tr['heat_multipliers'])
check('heat audit completion/audit flags',all(ha[k] is True for k in ['accepted_prefix_complete','identity_verified','auditable','handoff_ready']))
check('heat audit 56x181 complete',ha['audited_server_count']==56 and ha['checked_server_samples']==56*181 and ha['unchecked_server_samples']==0 and len(ha['servers'])==56 and len(ha['totals'])==181 and ha['uncooled_server_uids']==[])
residuals=[]
for server in ha['servers']:
    check('heat server actual sample count '+server['server_uid'],len(server['samples'])==181)
    for i,rec in enumerate(server['samples']):
        check('heat sample checked '+server['server_uid']+'/'+str(i),rec['checked'] is True and rec['reason'] is None and rec['time_s']==float(i))
        calc=rec['cp_input_W']-rec['liquid_removed_W']-rec['solid_storage_rate_W']
        check('heat sample formula '+server['server_uid']+'/'+str(i),calc==rec['closure_residual_W'])
        residuals.append(abs(calc))
check('heat max residual exactly reported',max(residuals)==ha['max_abs_closure_residual_W'])
report['heatAudit']={'identity':ident(heatpath),'acceptedCanonicalJSONSHA256':ha['accepted_input_sha256'],'executedFileByteSHA256':sha(B/'Liquid/log'/('run-'+RUNS['transient'])/'executed.liquidrun'),'finiteNumericValuesInspected':finiteheat,'servers':len(ha['servers']),'checkedServerSamples':len(residuals),'uncheckedServerSamples':ha['unchecked_server_samples'],'maxAbsClosureResidualW':max(residuals),'statusScope':'Existing actual heat-audit response inspected; formula recomputed only from already-produced sample values, not thermal solve/MMS.','flags':{k:ha[k] for k in ['accepted_prefix_complete','identity_verified','auditable','handoff_ready']}}
report['runs']['transient']['maxAbsoluteFieldChangeFromInitial']={k:max(abs(x-xrow[0]) for xrow in tr[k] for x in xrow) for k in report['runs']['transient']['dimensions']}
report['interpretation']={'transientBaseline':'Steady initialization with no scheduled nonzero-time keyframes and no online commands; recorded state arrays are a constant baseline. This is genuine time-marching product completion, not a new disturbed-transient or MMS validation.','syntheticParameters':'Saved synthetic/mock parameter pack and primary circuit are disclosed and hash-bound; no measured/calibrated vendor data claim.','physicalCorrectnessScope':'Recorded numerical convergence, conservation diagnostics and existing HeatAudit closure inspected; no independent PDE/DAE recomputation, gradient/AD test or MMS rerun.'}
check('all inputs unchanged during independent audit',all(ident(p)==before[str(p)] for p in inputpaths))
report['inputBytesFrozenDuringAudit']=True;report['checks']=checks;report['checkCount']=len(checks);report['allChecksPassed']=all(c['passed'] for c in checks)
(OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
print(json.dumps({'allChecksPassed':report['allChecksPassed'],'checks':len(checks),'steady':{'graph':report['runs']['steady']['graph'],'time':report['runs']['steady']['timesFirstLast'],'source':report['runs']['steady']['sourceProjectSHA256']},'transient':{'time':report['runs']['transient']['timesFirstLast'],'frames':report['runs']['transient']['N_t'],'source':report['runs']['transient']['sourceProjectSHA256']},'heatAudit':report['heatAudit'],'report':ident(OUT/'report.json')},indent=2))
