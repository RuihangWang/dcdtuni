from pathlib import Path
import json,re,hashlib,base64,struct
O=Path(__file__).parent;C=O/'case-copy'
def sha(b):return hashlib.sha256(b).hexdigest()
def put(n,x):(O/n).write_text(json.dumps(x,indent=2,ensure_ascii=True)+'\n')
d=json.loads((C/'P15_TypedAdmission_BrowserProof_Only.cfdproj').read_text());desc=d['meta']['description'];proof=json.loads(desc)
assert proof['schema']=='normal-react-p15-typed-admission-v1'
(O/'browser-proof.json').write_bytes(desc.encode('utf8'))  # unescaped original string bytes, no JSON reserialization
assert len(desc.encode('utf-16-le'))//2==1050111
for k in ['preAX','startAX','nativeAX','finalAX','finalLogText']:
 (O/(k+'.txt')).write_text(proof[k])
def jpgdim(b):
 assert b[:2]==b'\xff\xd8' and b[-2:]==b'\xff\xd9'
 p=2
 while p<len(b):
  assert b[p]==255
  while p<len(b) and b[p]==255:p+=1
  mark=b[p];p+=1
  if mark in [0xd8,0xd9] or 0xd0<=mark<=0xd7:continue
  n=int.from_bytes(b[p:p+2],'big')
  if mark in [0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf]:
   return {'width':int.from_bytes(b[p+5:p+7],'big'),'height':int.from_bytes(b[p+3:p+5],'big'),'precision':b[p+2]}
  p+=n
 raise ValueError('No JPEG SOF')
images=[]
for k in ['preScreenshot','finalScreenshot']:
 text=proof[k]
 if text.startswith('data:image/jpeg;base64,'):text=text.split(',',1)[1]
 b=base64.b64decode(text,validate=True);dim=jpgdim(b);p=O/(k+'.jpg');p.write_bytes(b)
 images.append({'field':k,'path':p.name,'bytes':len(b),'sha256':sha(b),**dim,'scope':'ROOT actual CUA screenshot encoded in proof-only project; independent audit checks decoding/header/dimensions, not a new screenshot or visual inspection'})
native=(C/'Mesh/log.mpi_solver').read_bytes();assert sha(native)=='8d7638f1beee4ea4a8b6f0fcf1ca282fadb2899e03349de6e63e54bb6970cb96'
lines=native.decode().splitlines();contexts={};obs={};repairs={};excerpts=[]
for i,l in enumerate(lines,1):
 m=re.search(r'Normal complete-cavity installed context: rank (\d+), proposed/captured/current (\d+)/(\d+)/(\d+), refused (\d+), capture (\S+) s',l)
 if m:
  rank,a,b,c,r,t=m.groups();contexts[int(rank)]={'rank':int(rank),'proposed':int(a),'captured':int(b),'current':int(c),'captureRefused':int(r),'captureSeconds':float(t),'line':i}
 m=re.search(r'Normal complete-cavity current admission observations: rank (\d+), local legacy accepted (\d+); first-predicate refusals ([^;]+);',l)
 if m:
  rank,legacy,counttext=m.groups();counts={k:int(v) for k,v in re.findall(r'([\w-]+)=(\d+)',counttext)}
  obs[int(rank)]={'rank':int(rank),'localLegacyAccepted':int(legacy),'firstPredicateRefusals':counts,'line':i}
 m=re.search(r'Normal complete-cavity repair: rank (\d+), current/attempted/generated/prepared/committed (\d+)/(\d+)/(\d+)/(\d+)/(\d+)',l)
 if m:
  rank,*v=map(int,m.groups());repairs[rank]=dict(rank=rank,**dict(zip(['current','attempted','generated','prepared','committed'],v)),line=i)
 if any(x in l for x in ['Normal complete-cavity installed context:','Normal complete-cavity current admission observations:','Normal complete-cavity repair:','Conforming literal two-cell union experiment:','Total ']):excerpts.append({'line':i,'text':l})
assert set(contexts)==set(obs)==set(repairs)==set(range(8))
assert [obs[r]['localLegacyAccepted'] for r in range(8)]==[0,86,9,89,4,13,20,2]
assert sum(x['localLegacyAccepted'] for x in obs.values())==223
assert sum(x['captured'] for x in contexts.values())==10 and sum(x['current'] for x in contexts.values())==0
assert obs[1]['firstPredicateRefusals']['legacy']==4 and obs[2]['firstPredicateRefusals']['legacy']==6
for r in range(8):
 assert sum(obs[r]['firstPredicateRefusals'].values())==contexts[r]['captured']
 assert all(v==0 for k,v in obs[r]['firstPredicateRefusals'].items() if k!='legacy')
 assert all(repairs[r][k]==0 for k in ['current','attempted','generated','prepared','committed'])
assert re.search(r'^Total 150\.993 s$',native.decode(),re.M)
full=proof['finalLogText'];assert 'job 0212ac8d' in full
for text in ['Failed 2 mesh checks.','number of cells: 831','Writing 82 faces','returncode=1','--expected-cfdrun-sha256 fb230e1c1bddbd831cfc368e95e09b040f13b3802123bcda2d0d4d1e6b0d5a3d']:
 assert text in full
assert proof['consoleErrors']==[]
assert proof['solverGate']['exists'] and proof['solverGate']['disabled']
controls={x['id']:x for x in proof['finalControls']['controls']}
assert controls['mesh-run-btn']['exists'] and not controls['mesh-run-btn']['disabled']
assert not controls['mesh-abort-btn']['exists'] and not controls['solve-run-btn']['exists']
scripts=[x for x in proof['finalControls']['scripts'] if not x.startswith('data:')]
assert '/assets/index-Bh3uEUXL.js' in scripts
project=C/'Shinaide_TypedAdmissionP15.cfdproj';inp=C/'model/Shinaide_TypedAdmissionP15.cfdrun'
assert len(project.read_bytes())==436806 and sha(project.read_bytes())=='3f1fa196b045cf25e470981de5a010a250f04139bd7f6653df809201060ac10c'
assert len(inp.read_bytes())==483193 and sha(inp.read_bytes())=='fb230e1c1bddbd831cfc368e95e09b040f13b3802123bcda2d0d4d1e6b0d5a3d'
proc=json.loads((O/'actual-native-observation.json').read_text());nativeObs=[x for x in proc['processes'] if Path(x['exe']).name=='dcCartMesh']
assert len(proc['processes'])==11 and len(nativeObs)==8
nativeCPUs=[]
for p in nativeObs:
 assert p['exe_sha256']=='c2013d92578935d496b4b9c3b3f2277418cd8da7671f11061ff255d207a4098d'
 assert any(x['sha256']=='9386aa9b337c03a75267215f030cc50130c0dbde24d66e356ccf90fc359b3615' for x in p['mapped_libs'])
 affinity=re.search(r'^Cpus_allowed_list:\\s*(\\d+)\\s*$',p['status'],re.M)
 assert affinity
 nativeCPUs.append(int(affinity.group(1)))
assert sorted(nativeCPUs)==list(range(16,24))
assert '88987445c6c7775377c443101dcd18a57fd7aff2f9952187ed77322fff3b5194' in full
first=json.loads((O/'observer-originals/first-native-observation.json').read_text());assert first['processes']==[]
weight=[]
for r in [1,2]:
 phases=[]
 for phase in ['prefix','final']:
  p=C/f'Mesh/processor{r}/conforming-weight-unions-installed-{phase}-rank{r}.json';v=json.loads(p.read_text())
  assert v['failures']==0 and v['rawSha256']==sha(inp.read_bytes())
  phases.append({'phase':phase,'path':str(p.relative_to(O)),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes()),'failures':v['failures'],'records':len(v['records']),'coupledEarliestCertificateUnchanged':v['coupledEarliestCertificateUnchanged']})
 weight.append({'rank':r,'phases':phases,'scope':'Existing W packet installed lineage, NOT a serialized complete-cavity View or proof of disjointness'})
put('typed-current-runtime-analysis.json',{'job':'0212ac8d','sourceNativeLog':{'path':'case-copy/Mesh/log.mpi_solver','bytes':len(native),'sha256':sha(native)},'nativeTotalSeconds':150.993,'rankContexts':[contexts[r] for r in range(8)],'rankFirstPredicateRefusals':[obs[r] for r in range(8)],'rankRepairCounters':[repairs[r] for r in range(8)],'firstRefusalConclusion':'All10 captured complete-cavity views stop at the existing per-rank legacyAccepted!=0 predicate. No later geometry/alias/role/quality predicate was evaluated; zero counts are not passes.','legacyAcceptedSum':223,'globalLiteralSummary':{'examined':2550,'accepted':223,'protected':328,'geometry':1924,'quality':75,'cappedRanks':1,'rounds':2},'weightRank1Rank2JSON':weight,'limitations':['No complete-cavity View serialized JSON is present in this case. r1/r2 W lineage JSON is separate.','Actual overlap of legacy mutations and captured Views is unknown; aggregate counts are insufficient.','No generated candidate, actual geometry acceptance or fullmesh pass is established.']})
put('native-typed-log-excerpts.json',{'sourceSHA':sha(native),'lines':excerpts})
put('browser-proof-extraction-receipt.json',{'sourceProofProject':{'path':'case-copy/P15_TypedAdmission_BrowserProof_Only.cfdproj','bytes':(C/'P15_TypedAdmission_BrowserProof_Only.cfdproj').stat().st_size,'sha256':sha((C/'P15_TypedAdmission_BrowserProof_Only.cfdproj').read_bytes())},'descriptionCodePoints':len(desc),'descriptionUTF16CodeUnits':len(desc.encode('utf-16-le'))//2,'exactDecodedDescriptionUTF8SHA':sha(desc.encode()),'images':images,'scope':'Proof-only empty project is evidence transport, not original P15 physics input. Raw source bytes retained.','schema':proof['schema'],'startedAt':proof['startedAt'],'finishedAt':proof['finishedAt'],'scriptsWithoutDataPayloads':scripts,'consoleErrors':proof['consoleErrors'],'controls':proof['finalControls']['controls'],'solverGate':proof['solverGate']})
put('actual-result-scope.json',{'job':'0212ac8d','returncode':1,'fullCheckMeshFailedChecks':2,'D':831,'W':82,'nativeTotalSeconds':150.993,'uiFullLogTimestampAtTerminal':'2026-10-06 00:09:01','finalBrowserObservationTimestamp':proof['finishedAt'],'nativeDurationNotArchiveOrSSEWall':True,'normalRunEnabled':True,'abortAbsent':True,'solverTabActuallyDisabled':True,'solveRunButtonNotMounted':'absence must not be described as disabled','noSolverRun':True,'oldFrontendEntry':'index-Bh3uEUXL.js','firstEmptyObserverScopeCorrection':'ROOT exact-case-cwd filter omitted Mesh-cwd native descendants; retained empty record is not absence evidence; later actual11proc observation binds this run.','originalProjectSHA':sha(project.read_bytes()),'actualInputSHA':sha(inp.read_bytes()),'physicalSTLBatchExpected':'88987445c6c7775377c443101dcd18a57fd7aff2f9952187ed77322fff3b5194'})
print(json.dumps({'captured':10,'firstLegacyRefusals':10,'legacyAccepted':223,'nativeTotal':150.993,'returncode':1,'D':831,'W':82,'JPEGs':images,'descriptionCodePoints':len(desc),'descriptionUTF16':len(desc.encode('utf-16-le'))//2}))
