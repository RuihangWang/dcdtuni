"""Offline scalar reference replay of every actual performed query, never stage timing."""
import argparse,hashlib,importlib.util,json,os,struct,sys,time
from pathlib import Path
import numpy as np
ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--baseline',type=Path,required=True);ap.add_argument('--capture',type=Path,required=True);ap.add_argument('--start',type=int,required=True);ap.add_argument('--stop',type=int,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
for p in [a.repo,a.baseline,a.capture,a.out]:
 if not p.resolve().is_relative_to(B):raise RuntimeError('path outside B')
if a.out.exists():raise RuntimeError('expected new output')
a.out.mkdir();sys.path.insert(0,str(a.repo/'src'))
name='backend.aircooling.runner._offline_scalar_reference';spec=importlib.util.spec_from_file_location(name,a.baseline);baseline=importlib.util.module_from_spec(spec);sys.modules[name]=baseline;spec.loader.exec_module(baseline)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();raw=(a.capture/'actual-ray-queries.jsonl').read_bytes();queries=[json.loads(line) for line in raw.splitlines()];stage=json.loads((a.capture/'RECEIPT.json').read_text())
if not stage['ok'] or stage['rayObservation']['queryCount']!=len(queries) or not (0<=a.start<a.stop<=len(queries)):raise RuntimeError('incomplete capture or invalid query range')
epochs={};t=time.monotonic();rows_out=[]
receipt={'schema':'dcdtuni.offline-scalar-actual-query-equivalence/v1','offlineOnly':True,'includedInProductStageTime':False,'baselineSourceSha256':sha(a.baseline),'captureQueriesSha256':hashlib.sha256(raw).hexdigest(),'sourceManifestSha256':stage['manifestSha256'],'range':[a.start,a.stop],'totalActualQueries':len(queries),'allowedCpus':sorted(os.sched_getaffinity(0)),'perGroupBudgetSeconds':300,'matched':0,'rows':rows_out}
for index in range(a.start,a.stop):
 query=queries[index];epoch=query['epoch']
 if epoch not in epochs:
  folder=a.capture/('ray-epoch-'+str(epoch));meta=json.loads((folder/'META.json').read_text())
  if sha(folder/'triangles.npy')!=meta['trianglesSha256'] or sha(folder/'surface_ids.npy')!=meta['surfaceIdsSha256'] or meta['sourceManifestSha256']!=stage['manifestSha256']:raise RuntimeError('actual epoch changed')
  xyz=np.load(folder/'triangles.npy',allow_pickle=False);ids=np.load(folder/'surface_ids.npy',allow_pickle=False)
  if xyz.shape!=(meta['count'],3,3) or xyz.dtype!=np.float64 or len(ids)!=meta['count'] or not np.isfinite(xyz).all():raise RuntimeError('actual parsed scene invalid')
  refs=meta['surfaceRefs'];triangles=xyz.tolist();scene=[(refs[int(owner)][0],refs[int(owner)][1],tuple(tuple(vertex) for vertex in triangle)) for owner,triangle in zip(ids,triangles)];epochs[epoch]=scene
  receipt.setdefault('epochs',{})[str(epoch)]={'count':len(scene),'trianglesSha256':meta['trianglesSha256'],'surfaceIdsSha256':meta['surfaceIdsSha256'],'surfaceRefCount':len(refs)}
 before=time.monotonic();value=baseline._nearest_ray_clearance(tuple(query['center']),tuple(query['direction']),epochs[epoch],{tuple(ref) for ref in query['ignored']});actual=None if value is None else struct.pack('>d',value).hex();equal=actual==query['returnedBits'];row={'index':index,'epoch':epoch,'expectedBits':query['returnedBits'],'scalarBits':actual,'equal':equal,'scalarSeconds':time.monotonic()-before};rows_out.append(row);receipt['matched']+=int(equal);receipt['elapsedS']=time.monotonic()-t
 (a.out/'RECEIPT.json').write_text(json.dumps(receipt,indent=2))
 if not equal:print('ACTUAL_SCALAR_MISMATCH',json.dumps(row));raise SystemExit(1)
 if (index-a.start+1)%10==0:print('OFFLINE_PROGRESS',json.dumps({'range':[a.start,a.stop],'matched':receipt['matched'],'elapsedS':receipt['elapsedS']}),flush=True)
 if time.monotonic()-t>300:raise RuntimeError('offline group budget exceeded')
receipt['ok']=True;receipt['elapsedS']=time.monotonic()-t;(a.out/'RECEIPT.json').write_text(json.dumps(receipt,indent=2));print('OFFLINE_SCALAR_EQUIVALENT',json.dumps({'ok':True,'range':[a.start,a.stop],'matched':receipt['matched'],'elapsedS':receipt['elapsedS']}))
