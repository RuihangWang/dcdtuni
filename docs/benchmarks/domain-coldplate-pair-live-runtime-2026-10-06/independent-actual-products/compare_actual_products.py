from pathlib import Path
import hashlib,json,struct,math,subprocess
T=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
O=Path(__file__).parent
N=T/'.codex-tmp/cases/DomainColdPlate_Browser_CUA/Liquid/log/run-5aa7f3340e0849bdb6772d3d73c73965'
E=T/'.codex-tmp/domain-coldplate-normal-browser-20261006'
def sha(b):return hashlib.sha256(b).hexdigest()
def put(n,x):(O/n).write_text(json.dumps(x,indent=2,ensure_ascii=True,allow_nan=False)+'\n')
oldman=(O/'before-manifest.json').read_bytes()
assert sha(oldman)=='ee9918aed66069545048dc9d4c670d109dcc61759def22f3c40ead9f6c686e40'
bm=json.loads(oldman)
for r in bm['files']:
 b=(O/r['path']).read_bytes();assert len(b)==r['bytes'] and sha(b)==r['sha256']
(O/'after').mkdir(exist_ok=False)
names=['executed.liquidrun','input.liquidrun','executed-inputs.json','checkpoint.json','result.json','input.json']
ident=[];full={};num={};allDiff=[]
def walk(a,b,path='$',diff=None,count=None):
 if type(a) is not type(b):
  diff.append({'path':path,'old':a,'new':b,'reason':'type'});return
 if isinstance(a,dict):
  for k in sorted(a.keys()|b.keys()):
   if k not in a or k not in b:diff.append({'path':path+'.'+k,'old':a.get(k),'new':b.get(k),'reason':'missing'})
   else:walk(a[k],b[k],path+'.'+k,diff,count)
 elif isinstance(a,list):
  if len(a)!=len(b):diff.append({'path':path,'oldLength':len(a),'newLength':len(b),'reason':'length'})
  else:
   for i,(x,y) in enumerate(zip(a,b)):walk(x,y,path+f'[{i}]',diff,count)
 elif isinstance(a,float):
  assert math.isfinite(a) and math.isfinite(b)
  count['floatLeaves']+=1
  if struct.pack('>d',a)!=struct.pack('>d',b):diff.append({'path':path,'old':a,'new':b,'reason':'floatBits','oldHex':struct.pack('>d',a).hex(),'newHex':struct.pack('>d',b).hex()})
 elif a!=b:diff.append({'path':path,'old':a,'new':b,'reason':'value'})
 elif isinstance(a,int) and not isinstance(a,bool):count['equalIntegerLeaves']+=1
data={}
for n in names:
 before=(N/n).read_bytes();(O/'after'/n).write_bytes(before);after=(N/n).read_bytes();assert before==after==(O/'after'/n).read_bytes()
 ident.append({'source':str(N/n),'copy':'after/'+n,'bytes':len(before),'sha256':sha(before),'sourceBeforeAfterAndCopyByteEqual':True})
 a=json.loads((O/'before'/n).read_bytes());b=json.loads(before);data[n]=(a,b)
 diff=[];count={'floatLeaves':0,'equalIntegerLeaves':0};walk(a,b,diff=diff,count=count)
 full[n]=diff;num[n]=count;allDiff.extend(dict(file=n,**r) for r in diff)
assert len(allDiff)==19
expected={
 'executed.liquidrun':{'$.source_project.sha256'},
 'input.liquidrun':{'$.source_project.sha256'},
 'executed-inputs.json':{'$.run_id','$.original_config_hash','$.executed_config_hash'},
 'checkpoint.json':{'$.run_id','$.liquidrun.source_project.sha256','$.config_hash'},
 'result.json':{'$.run_id','$.config_hash','$.parameter_provenance.source_project.sha256','$.parameter_provenance.manifest_sha256'},
 'input.json':{'$.run_id','$.config_hash','$.project_revision','$.liquidrun.source_project.sha256','$.metadata.config_hash','$.metadata.parameter_provenance.source_project.sha256','$.metadata.parameter_provenance.manifest_sha256'}}
for n,d in full.items():assert {r['path'] for r in d}==expected[n] and all(r['reason']=='value' for r in d)
put('after-identity.json',ident)
put('all-19-typed-product-differences.json',{'files':full,'changedLeafCount':19,'comparison':'dict/list shape + scalar type + Float64 bit pattern; no initial exclusions','numericLeafCounts':num,'allOtherFieldsExact':True})
projects=[
 T/'.codex-tmp/cases/DomainAsset_Compat_Browser_CUA/DomainAsset_BoxCdu_After.cfdproj',
 T/'.codex-tmp/cases/DomainColdPlate_Browser_CUA/DomainColdPlate.cfdproj']
(O/'projects').mkdir(exist_ok=False)
projectRows=[]
for p in projects:
 b=p.read_bytes();q=O/'projects'/p.name;q.write_bytes(b);assert p.read_bytes()==b==q.read_bytes()
 projectRows.append({'source':str(p),'copy':str(q.relative_to(O)),'bytes':len(b),'sha256':sha(b)})
pd=[];pc={'floatLeaves':0,'equalIntegerLeaves':0};walk(json.loads((O/'projects'/projects[0].name).read_bytes()),json.loads((O/'projects'/projects[1].name).read_bytes()),diff=pd,count=pc)
assert len(pd)==1 and pd[0]['path']=='$.meta.casePath'
put('project-byte-and-semantic-comparison.json',{'inputs':projectRows,'allTypedDifferences':pd,'otherFieldsExact':True,'includesUnknownTopLevelExtensions':True})
# Independent JSON/crypto implementation only, no product module import.
js="""const fs=require('fs'),crypto=require('crypto');
function sort(x){if(Array.isArray(x))return x.map(sort);if(x&&typeof x==='object'){const o=Object.create(null);for(const k of Object.keys(x).sort())Object.defineProperty(o,k,{value:sort(x[k]),enumerable:true});return o;}return x}
for(const p of process.argv.slice(2)){const d=JSON.parse(fs.readFileSync(p,'utf8'));console.log(JSON.stringify({path:p,sha256:crypto.createHash('sha256').update(JSON.stringify(sort(d))).digest('hex')}));}
"""
(O/'independent-saved-project-hash.cjs').write_text(js)
rr=subprocess.run(['node',str(O/'independent-saved-project-hash.cjs'),*[str(O/r['copy']) for r in projectRows]],capture_output=True,text=True,check=True,timeout=10,cwd=O)
(O/'saved-project-hash-stdout.jsonl').write_text(rr.stdout)
hashRows=[json.loads(l) for l in rr.stdout.splitlines()]
def canonicalHash(x):return sha(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())
links=[]
for i,which in enumerate(['before','after']):
 inp=data['input.liquidrun'][i];ex=data['executed.liquidrun'][i];led=data['executed-inputs.json'][i];r=data['result.json'][i];ch=data['checkpoint.json'][i];raw=data['input.json'][i]
 H=hashRows[i]['sha256'];assert inp['source_project']['sha256']==ex['source_project']['sha256']==H
 orig=canonicalHash({'liquidrun':inp,'controllers':[]});executed=canonicalHash({'liquidrun':ex,'controllers':[]});manifestH=canonicalHash(inp)
 assert orig==led['original_config_hash']==r['config_hash']==ch['config_hash']==raw['config_hash']==raw['metadata']['config_hash']
 assert executed==led['executed_config_hash']
 assert manifestH==r['parameter_provenance']['manifest_sha256']==raw['metadata']['parameter_provenance']['manifest_sha256']
 assert r['parameter_provenance']['source_project']['sha256']==raw['metadata']['parameter_provenance']['source_project']['sha256']==ch['liquidrun']['source_project']['sha256']==H
 assert r['status']=='completed' and r['completed'] and r['converged'] and r['error'] is None
 assert len(r['time_s'])==50 and r['time_s']==[.5*k for k in range(50)]
 assert led['accepted_sample_count']==led['original_sample_count']==50 and led['complete_original_horizon']
 links.append({'which':which,'sourceProjectCanonicalHash':H,'originalConfigHash':orig,'executedConfigHash':executed,'provenanceManifestHash':manifestH,'allRecordedHashLinksMatch':True,'samples':50,'finalTime':24.5,'execution':inp['execution'],'status':r['status'],'nodeCount':len(r['node_ids']),'edgeCount':len(r['edge_ids'])})
put('independent-hash-links.json',{'scope':'Saved disk-form JSON hashes independently computed by builtin fs/crypto; configuration and provenance hashes independently computed by Python stdlib. No compiler/solver/product helper was executed.','links':links,'hashDifferencesExplanation':'Only saved project meta.casePath differs; its canonical hash is part of source_project, which propagates into whole input/executed/provenance hashes. No physics/control/sample/parameter leaf differs.'})
em=(E/'manifest.json').read_bytes();assert sha(em)=='228234d9a83a4abb4649193b3b841199f429aa172f87a95310e9cbd965ff5948'
emj=json.loads(em);assert emj['payloadCount']==9 and emj['payloadBytes']==1649566
for r in emj['files']:
 b=(E/r['path']).read_bytes();assert len(b)==r['bytes'] and sha(b)==r['sha256']
(O/'consumed-browser-manifest.json').write_bytes(em)
browser=json.loads((E/'browser-proof.json').read_text())['evidence']
assert browser['errors']==[] and browser['entry']==['/assets/index-Cv1ZvInu.js']
(O/'browser-primary').mkdir(exist_ok=False)
for n in ['auditFinished.txt','latest.txt','logText.txt','preparedReady.txt']:
 b=(E/n).read_bytes();(O/'browser-primary'/n).write_bytes(b);assert (E/n).read_bytes()==b
assert '2800' in browser['auditFinished'] and '3.38e-8 W' in browser['auditFinished']
put('browser-primary-verification.json',{'manifestPath':str(E/'manifest.json'),'sha256':sha(em),'count':9,'bytes':1649566,'allPayloadSHASizeVerified':True,'entry':browser['entry'],'errors':browser['errors'],'completedUtc':browser['completedUtc'],'heatAuditAXScope':'Actual browser reports 56 servers / 2800 samples / max3.38e-8 W; no new API fetch or heat recomputation in this audit.','proofIsRootActualBrowserNotIndependentBrowserExecution':True})
sources=[]
for name in ['src/frontend/shared/js/cfdrun_export.js','src/frontend/shared/js/cfdproj_transient.js','src/frontend/react/src/liquid/liquidExperiment.ts','src/frontend/react/src/schema/liquidrunDAE.ts','src/backend/liquidcooling/runtime.py','src/backend/liquidcooling/liquidrun_dae_schema.py','src/backend/liquidcooling/run_manager.py']:
 p=T/name;b=p.read_bytes();sources.append({'path':str(p),'bytes':len(b),'sha256':sha(b)})
put('consumed-hash-source-identity.json',sources)
assert (O/'before-manifest.json').read_bytes()==oldman
for r in ident:assert sha((N/Path(r['source']).name).read_bytes())==r['sha256']
assert (E/'manifest.json').read_bytes()==em
put('comparison-result.json',{'status':'actual CP run numeric/physical parity verified','oldRun':'d64757e3ed55493d9f283fb888018d5e','newRun':'5aa7f3340e0849bdb6772d3d73c73965','beforeManifestSHA':sha(oldman),'rawProductChangedLeaves':19,'unexplainedDifferences':[],'projectChangedLeaves':1,'projectDifference':'meta.casePath','allFloatingLeavesBitExact':True,'otherGraphPhysicsControlsSchemaBindingsExact':True,'allHashesIndependentlyRecomputed':True,'sourceCopiedBeforeAfterStable':True,'runtimeScope':'ROOT ordinary React Cv1 CPU float64 2 threads, 50 samples dt.5 final24.5s; full domain architecture not accepted','executionInAudit':'read-only stdlib / builtin fs-crypto audit only; no compiler/solver/tests/API/UI/service control'})
print(json.dumps({'products':6,'changedLeaves':19,'projectChangedLeaves':1,'allNumericFloatBitsExact':True,'allConfigAndSourceHashesMatch':True,'newResultNodes':links[1]['nodeCount'],'newResultEdges':links[1]['edgeCount']}))
