from pathlib import Path
import json,hashlib,os,stat,time,concurrent.futures
O=Path(__file__).parent
C=O/'case-copy'
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
copyManifest=O/'complete-case-copy-manifest.json'
expected=json.loads(copyManifest.read_text())
copyRows={r['path']:r for r in expected['files']}
assert len(copyRows)==373
paths=[]
for root,ds,fs in os.walk(O,followlinks=False):
 for n in ds+fs:assert not (Path(root)/n).is_symlink()
 for n in fs:
  p=Path(root)/n
  if p.name in ['manifest.json','final-freeze-receipt.json']:continue
  assert stat.S_ISREG(p.stat().st_mode)
  paths.append(p)
paths=sorted(paths,key=lambda p:p.relative_to(O).as_posix())
start=time.monotonic()
def verify(p):
 before=p.stat();h=digest(p);after=p.stat()
 assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
 rel=p.relative_to(O).as_posix()
 if rel.startswith('case-copy/'):
  x=copyRows[rel[len('case-copy/'):]]
  assert h==x['sha256'] and before.st_size==x['bytes']
 return {'path':rel,'bytes':before.st_size,'sha256':h}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(verify,paths))
assert {r['path'][len('case-copy/'):] for r in rows if r['path'].startswith('case-copy/')}==set(copyRows)
receipt={'schema':'P15-independent-final-freeze/v1','caseFilesRehashed':len(copyRows),'caseBytes':expected['payloadBytes'],'allCopiedCaseFilesMatchOriginalCopyManifest':True,'allPayloadStatsStableDuringFinalHash':True,'noSymlinks':True,'finalHashElapsedSeconds':time.monotonic()-start,'elapsedScope':'Independent archive/hash work only, not native or SSE runtime','sourceCopyManifestSHA256':digest(copyManifest),'CPU':[36,37,38,39],'CUDA_VISIBLE_DEVICES':'','productOrServiceMutation':False}
rp=O/'final-freeze-receipt.json';rp.write_text(json.dumps(receipt,indent=2)+'\n');rows.append({'path':rp.name,'bytes':rp.stat().st_size,'sha256':digest(rp)});rows.sort(key=lambda r:r['path'])
m={'schema':'P15-typed-admission-independent/v1','job':'0212ac8d','scope':'Original full case and actual browser/native evidence; first-refusal diagnosis and unimplemented journal design','payloadCount':len(rows),'payloadBytes':sum(r['bytes'] for r in rows),'files':rows,'sourceCase':expected['sourceCase'],'sourceCaseCopyManifestSHA256':digest(copyManifest),'nativeReturncode':1,'strictFailedChecks':2,'D':831,'W':82,'nativeTotalSeconds':150.993,'doesNotEstablishMeshAcceptance':True}
mp=O/'manifest.json';mp.write_text(json.dumps(m,indent=2)+'\n')
assert sum(r['bytes'] for r in rows)==m['payloadBytes']
print(json.dumps({'output':str(O),'payloadCount':len(rows),'payloadBytes':m['payloadBytes'],'manifestSHA256':digest(mp),'caseCopyManifestSHA256':digest(copyManifest),'caseFilesRehashed':len(copyRows),'hashElapsedSeconds':receipt['finalHashElapsedSeconds']}),flush=True)
