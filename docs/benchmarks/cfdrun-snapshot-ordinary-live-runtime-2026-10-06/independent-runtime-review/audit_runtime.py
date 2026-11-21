from pathlib import Path
import hashlib,json,re,shutil
T=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
E=T/'.codex-tmp/ram-ordinary-archive-live-20261006'
O=Path(__file__).parent
def sha(b):return hashlib.sha256(b).hexdigest()
def put(n,d): (O/n).write_text(json.dumps(d,indent=2,ensure_ascii=True)+'\n')
manbytes=(E/'manifest.json').read_bytes()
assert sha(manbytes)=='2ce1c134255cd5397190f2c1818da472b5affe825ef6ea82a4cbcb7e1233b545'
m=json.loads(manbytes);rows=m['files'];idx={r['path']:r for r in rows}
assert len(idx)==len(rows)==m['payloadCount']==883
verified=[]
for r in rows:
 p=E/r['path'];assert p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(E.resolve())
 b=p.read_bytes();assert len(b)==r['bytes'] and sha(b)==r['sha256']
 verified.append(r)
assert sum(r['bytes'] for r in rows)==m['payloadBytes']==20475462
(O/'consumed-original-manifest.json').write_bytes(manbytes)
put('all-payload-verification.json',{'sourceManifest':str(E/'manifest.json'),'sha256':sha(manbytes),'count':883,'bytes':20475462,'allSizeAndSHAExact':True,'files':verified})
def copy(src,dst):
 b=(E/src).read_bytes();r=idx[src];assert len(b)==r['bytes'] and sha(b)==r['sha256']
 p=O/'primary'/dst;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
 assert (E/src).read_bytes()==b==p.read_bytes()
 return {'source':src,'copy':str(p.relative_to(O)),'bytes':len(b),'sha256':sha(b)}
copies=[]
for n in ['README.md','source-controls-before-second-publication.json','mesh-input-first-capture.json','solve-input-first-capture.json','second-input-first-capture.json']:
 copies.append(copy(n,n))
Hs=['388285708580f98030380f72159e73d31a184d9f13bd896d9f66f32008f7da4f','bd0c082213534b2eaa80d03925a5bd39002674c128dcdab7d70486e31b8aac07','b7a63e2ed74af7341df81ceff8c7d60fa3ad7503f944d42ccc624a8f58111ddb']
B='87e20ad7866eebca37074b782d43e80a557b9106b4f9764183930505d092a906'
groups=[
 ['mesh-initial/model/RamSnapshot_Archive.cfdrun'],
 ['solve-input-first-capture/model/RamSnapshot_Archive.cfdrun','solve-input-first-capture/log/2026-10-05-23-48-35_RamSnapshot_Archive_CUA/model/RamSnapshot_Archive.cfdrun','first-solve-complete/model/RamSnapshot_Archive.cfdrun','first-solve-complete/log/2026-10-05-23-48-35_RamSnapshot_Archive_CUA/model/RamSnapshot_Archive.cfdrun','second-solve-complete/log/2026-10-05-23-48-35_RamSnapshot_Archive_CUA/model/RamSnapshot_Archive.cfdrun','second-input-first-capture/log/2026-10-05-23-48-35_RamSnapshot_Archive_CUA/model/RamSnapshot_Archive.cfdrun'],
 ['second-input-first-capture/model/RamSnapshot_Archive.cfdrun','second-input-first-capture/log/2026-10-05-23-50-05_RamSnapshot_Archive_CUA/model/RamSnapshot_Archive.cfdrun','second-solve-complete/model/RamSnapshot_Archive.cfdrun','second-solve-complete/log/2026-10-05-23-50-05_RamSnapshot_Archive_CUA/model/RamSnapshot_Archive.cfdrun']]
browser=json.loads((E/'browser-proof.json').read_text())['evidence']
chains=[];excerpts={}
for i,(names,H,log,key) in enumerate(zip(groups,Hs,['meshLog.txt','solveLog.txt','secondLog.txt'],['meshTerminal','solveStarted','secondStarted'])):
 b=(E/names[0]).read_bytes();assert len(b)==[20299,19585,19587][i] and sha(b)==H
 assert all((E/n).read_bytes()==b for n in names)
 lines=(E/log).read_text().splitlines()
 cli=[{'line':j+1,'text':l} for j,l in enumerate(lines) if '--cfdrun-stdin' in l and '--expected-cfdrun-sha256 '+H in l and '--expected-stl-batch-sha256 '+B in l]
 owned=[{'line':j+1,'text':l} for j,l in enumerate(lines) if 'Owned input snapshot: '+str(len(b))+' bytes, SHA-256 '+H+', STL batch '+B in l]
 assert cli and owned and H in browser[key] and B in browser[key]
 chains.append({'phase':['mesh','firstSolve','secondSolve'][i],'sha256':H,'bytes':len(b),'allRawCopiesByteEqual':True,'paths':names,'CLI':cli,'ownedSnapshot':owned,'browserKey':key,'browserProofSHA':idx['browser-proof.json']['sha256']})
 copies.append(copy(names[0],['mesh-input.cfdrun','first-input.cfdrun','second-input.cfdrun'][i]))
 excerpts[log]=[{'line':j+1,'text':l} for j,l in enumerate(lines) if any(k in l for k in ['Owned input snapshot:','--cfdrun-stdin','checkMesh -allTopology','Mesh OK.','SIMPLE solution converged','PIMPLE: converged','TRANSIENT_COMPLETED','作业结束'])]
p1='first-solve-complete/log/2026-10-05-23-48-35_RamSnapshot_Archive_CUA/'
p2='second-solve-complete/log/2026-10-05-23-48-35_RamSnapshot_Archive_CUA/'
a={k[len(p1):]:r for k,r in idx.items() if k.startswith(p1)}
b={k[len(p2):]:r for k,r in idx.items() if k.startswith(p2)}
assert a.keys()==b.keys() and len(a)==194
stable=[]
for n in sorted(a):
 assert (E/p1/n).read_bytes()==(E/p2/n).read_bytes()
 assert (a[n]['bytes'],a[n]['sha256'])==(b[n]['bytes'],b[n]['sha256'])
 stable.append({'path':n,'bytes':a[n]['bytes'],'sha256':a[n]['sha256']})
put('first-archive-194-byte-comparison.json',{'beforePrefix':p1,'afterPrefix':p2,'count':194,'allBytesEqual':True,'files':stable})
batch=[]
for root in ['first-solve-complete','second-solve-complete']:
 pre=root+'/Mesh/constant/triSurface/'
 rr=sorted((k[len(pre):],r) for k,r in idx.items() if k.startswith(pre))
 digest=hashlib.sha256(b'dcdtuni.triSurface-batch/v1\0');digest.update(len(rr).to_bytes(8,'big'))
 for n,r in rr:
  name=n.encode();payload=(E/pre/n).read_bytes()
  digest.update(len(name).to_bytes(8,'big'));digest.update(name)
  digest.update(len(payload).to_bytes(8,'big'));digest.update(hashlib.sha256(payload).digest())
 assert digest.hexdigest()==B
 batch.append({'root':root,'sha256':digest.hexdigest(),'count':len(rr),'files':[dict(path=n,**{k:r[k] for k in ['bytes','sha256']}) for n,r in rr]})
runs=[];M='eae64d9d07933f514f191f45c12455cd1ffcc788ff619fb5d934f12fc580d530'
for root,run,H,steps,ps in [
 ('first-solve-complete','2026-10-05-23-48-35_RamSnapshot_Archive_CUA',Hs[1],2,[18,15]),
 ('second-solve-complete','2026-10-05-23-50-05_RamSnapshot_Archive_CUA',Hs[2],4,[18,15,15,16])]:
 pre=root+'/log/'+run+'/';R=E/pre
 rec=json.loads((R/'air-run-execution-receipt.json').read_text());res=rec['result']
 cv=json.loads((R/'convergence.json').read_text());st=json.loads((R/'steady_start.json').read_text())
 assert rec['status']=='accepted' and res['cfdrunSha256']==H and res['meshSha256']==M
 agg=hashlib.sha256(b'partitioned-polyMesh-v1\0')
 for rank in range(2):
  for name in ['points','faces','owner','neighbour','boundary']:
   payload=(R/f'processor{rank}/constant/polyMesh/{name}').read_bytes()
   agg.update(f'processor{rank}/{name}'.encode()+b'\0');agg.update(payload)
 assert agg.hexdigest()==M
 checked=[]
 for f in res['meshFiles']+res['fieldFiles']:
  payload=(R/f['path']).read_bytes();assert sha(payload)==f['sha256']
  if 'field' in f:
   text=payload[:128*1024].decode(errors='replace')
   assert re.search(r'\bclass\s+'+re.escape(f['foamClass'])+r'\s*;',text)
   dims=re.search(r'\bdimensions\s*\[([^\]]+)\]',text);assert dims and [float(x) for x in dims.group(1).split()]==f['dimensions']
  checked.append(f)
 assert cv['verdict']=='TRANSIENT_COMPLETED' and cv['transient']['passed'] and cv['transient']['reasons']==[]
 assert cv['transient']['steps']==steps and cv['transient']['final_time']==cv['transient']['end_time']==.02
 assert cv['transient']['outer_loop']['steps_converged']==steps and cv['transient']['outer_loop']['steps_hit_limit']==0
 assert st['convergence']['verdict']=='STEADY_CONVERGED' and st['convergence']['converged_iteration']==337 and st['actual_n_procs']==2
 assert all('phi' in v['seeded'] for v in st['cases'].values())
 for n in ['air-run-execution-receipt.json','convergence.json','steady_start.json']:
  copies.append(copy(pre+n,root+'/'+n))
 runlog=(R/(run+'.log')).read_text()
 actualP=[int(x) for x in re.findall(r'PIMPLE: converged in (\d+) iterations',runlog)]
 assert actualP==ps
 runs.append({'root':root,'run':run,'ownedSHA':H,'actualPIMPLEIterations':actualP,'resultReceipt':res,'all14ReceiptFileSHAsAndFieldHeadersMatch':True,'meshAggregateRecomputed':M,'steady':st,'convergence':cv})
 for qpath in [root+'/mesh-quality-receipt.json']:
  q=json.loads((E/qpath).read_text());assert q['status']=='accepted' and q['failedChecks']==0 and q['meshSha256']==M and q['cfdrunSha256']==H
  copies.append(copy(qpath,root+'/mesh-quality-receipt.json'))
meshQ=json.loads((E/'mesh-initial/mesh-quality-receipt.json').read_text())
assert meshQ['status']=='accepted' and meshQ['failedChecks']==0 and meshQ['cfdrunSha256']==Hs[0] and meshQ['meshSha256']==M
copies.append(copy('mesh-initial/mesh-quality-receipt.json','mesh-quality-receipt.json'))
source=[]
for name in ['src/backend/aircooling/runner/stlgen_runner.py','src/backend/aircooling/runner/run_cfd.py','src/backend/aircooling/runner/cfdrun_stl_identity.py','src/backend/aircooling/runner/air_run_result_receipt.py']:
 data=(T/name).read_bytes();source.append({'path':str(T/name),'bytes':len(data),'sha256':sha(data)})
controls=json.loads((E/'source-controls-before-second-publication.json').read_text())
for r in controls:
 if r['path'].endswith('.py'):
  actual=next(x for x in source if x['path']==str(T/r['path']))
  assert (actual['bytes'],actual['sha256'])==(r['bytes'],r['sha256'])
put('input-consumption-and-runtime.json',{'sourcePackageManifestSHA':sha(manbytes),'inputChains':chains,'batchIndependentByteFraming':batch,'meshQuality':meshQ,'runs':runs,'browserControls':browser['finalControls'],'browserErrors':browser['currentErrors'],'source':source,'limitations':['No native-process/memfd descriptor capture: no runtime seal claim and no absence claim.','Synthetic tiny zero-load channel only; not full Shinaide wind solver acceptance.','No runtime cancel/meshgen/snappy/hybrid acceptance inferred.','An accepted receipt alone is not scientific convergence; actual log and convergence JSON are separately verified.']})
put('log-excerpts-with-line-binding.json',{'sourceLogFiles':[{k:idx[n][k] for k in ['path','bytes','sha256']} for n in excerpts],'excerpts':excerpts})
put('copy-identity.json',copies)
assert (E/'manifest.json').read_bytes()==manbytes
put('audit-result.json',{'status':'scoped evidence chain verified','sourceCount':883,'sourceBytes':20475462,'sourceManifestUnchanged':True,'archivedFirstRun194AllByteEqualAfterSecond':True,'allInputsExact':True,'physicalBatchRecomputed':B,'twoTransientRuns':[2,4],'nativeRuntimeMemfdObserved':False,'testsOrBusinessExecution':False})
print(json.dumps({'verified':883,'payloadBytes':20475462,'stableFirstRun':194,'batch':B,'copies':len(copies),'runSteps':[2,4]}))
