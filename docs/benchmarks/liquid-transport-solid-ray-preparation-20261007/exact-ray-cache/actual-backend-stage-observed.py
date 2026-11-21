"""Replay actual whole React files with normal backend consumers; dictionary stage only."""
import argparse,hashlib,json,os,shutil,sys,time,traceback
from pathlib import Path
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--fixture',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
repo=a.repo.resolve();fixture=a.fixture.resolve();out=a.out.resolve()
if any(not p.is_relative_to(B) for p in [repo,fixture,out]) or out==B or out.exists():raise RuntimeError('expected new B-contained out and B-contained original source/fixture')
out.mkdir();case=out/'stage-case';tri=case/'Mesh/constant/triSurface';model=case/'model';tri.mkdir(parents=True);model.mkdir()
sha=lambda x:hashlib.sha256(x).hexdigest()
source_paths=['src/backend/aircooling/runner/stlgen_runner.py','src/backend/aircooling/runner/cfdrun_schema.py','src/backend/aircooling/runner/cfdrun_reader.py','src/backend/aircooling/runner/stl_geometry_index.py']
source_before={p:sha((repo/p).read_bytes()) for p in source_paths}
receipt={'schema':'dcdtuni.actual-whole-react-backend-stage/v1','repo':str(repo),'fixture':str(fixture),'out':str(out),'sourceBefore':source_before,'nativeMeshRun':False,'solverRun':False,'allowedCpus':sorted(os.sched_getaffinity(0)),'stages':[]}
start=time.monotonic()
def phase(name,**extra):
 receipt['stages'].append(dict(name=name,elapsedS=time.monotonic()-start,**extra));(out/'RECEIPT.json').write_text(json.dumps(receipt,indent=2))
try:
 original_manifest=(fixture/'transport.cfdrun').read_bytes();raw=json.loads(original_manifest)
 expected=[r for r in raw['geometryExport']['sourceRegions'].values() if r['operation']=='liquidTransportUnionBoundary']
 if len(expected)!=6:raise RuntimeError('expected six original owned transport exterior regions')
 carrier_path='merged_liquid_transport.stl';expected_names=sorted(r['solidName'] for r in expected);contributors=expected[0]['carrierContributorUids'];carrier_uid=expected[0]['carrierUid']
 if len(contributors)!=6 or any(r['carrierContributorUids']!=contributors or r['carrierUid']!=carrier_uid for r in expected):raise RuntimeError('carrier identities differ')
 manifest=model/'transport.cfdrun';shutil.copy2(fixture/'transport.cfdrun',manifest)
 files={}
 for path in sorted(fixture.glob('*.stl')):
  data=path.read_bytes();files[path.name]={'sha256':sha(data),'bytes':len(data)};shutil.copy2(path,tri/path.name)
 original_carrier=(tri/carrier_path).read_bytes();receipt.update(manifestSha256=sha(original_manifest),files=files,carrierUid=carrier_uid,carrierContributorUids=contributors,sourceEntityUids=sorted(r['sourceEntityUid'] for r in expected),expectedTransportSolids=expected_names)
 phase('copied-actual-whole-files',originalGroupedFileCount=len(files))
 sys.path.insert(0,str(repo/'src'))
 from backend.aircooling.runner.stlgen_runner import STLGenRunner
 import backend.aircooling.runner.stlgen_runner as consumer
 import resource,struct,numpy as np
 receipt['actualConsumerFile']=str(Path(consumer.__file__).resolve());receipt['actualConsumerSha256']=sha(Path(consumer.__file__).read_bytes())
 epochs=[];queries=[];batch_calls=0;batch_max=0;batch_errors=0;ray_seconds=0.
 raw_nearest=consumer._nearest_ray_clearance;raw_batch=consumer._ray_triangle_distances_batch
 def observed_batch(np,origin,direction,v0,edge1,edge2):
  global batch_calls,batch_max,batch_errors
  batch_calls+=1;batch_max=max(batch_max,len(v0))
  try:return raw_batch(np,origin,direction,v0,edge1,edge2)
  except Exception:
   batch_errors+=1;raise
 def observed_nearest(center,direction,scene,ignored):
  global ray_seconds
  t=time.monotonic();result=raw_nearest(center,direction,scene,ignored);ray_seconds+=time.monotonic()-t
  if isinstance(scene,consumer._SceneRayCache):
   epoch=next((i for i,(value,_) in enumerate(epochs) if value is scene),None)
   if epoch is None:
    epoch=len(epochs);capture=out/('ray-epoch-'+str(epoch));capture.mkdir();xyz=np.asarray([row[2] for row in scene.rows],dtype=np.float64);refs=scene._surface_refs
    np.save(capture/'triangles.npy',xyz,allow_pickle=False);np.save(capture/'surface_ids.npy',scene._surface_ids,allow_pickle=False)
    meta={'schema':'dcdtuni.actual-parsed-STL-ray-epoch/v1','epoch':epoch,'count':len(scene.rows),'fastEligible':scene._np is not None,'sourceManifestSha256':receipt['manifestSha256'],'surfaceRefs':refs,'trianglesSha256':sha((capture/'triangles.npy').read_bytes()),'surfaceIdsSha256':sha((capture/'surface_ids.npy').read_bytes())};(capture/'META.json').write_text(json.dumps(meta,indent=2));epochs.append((scene,meta))
   row={'epoch':epoch,'center':list(center),'direction':list(direction),'ignored':sorted(list(ignored)),'returnedBits':None if result is None else struct.pack('>d',result).hex()};queries.append(row)
   with (out/'actual-ray-queries.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
  return result
 consumer._nearest_ray_clearance=observed_nearest;consumer._ray_triangle_distances_batch=observed_batch
 runner=STLGenRunner(case_dir=case,cfdrun_path=manifest,expected_cfdrun_sha256=sha(original_manifest))
 runner._verify_hybrid_source_stls();phase('actual-source-STL-hashes-verified',verifiedSource=getattr(runner,'_source_stls_verified',False),pinnedManifest=getattr(runner,'_verified_source_cfdrun_sha256',None))
 runner._load();phase('actual-loaded-split-other-buckets',loadedFiles={k:len(v) for k,v in runner._stl_solids.items()})
 if (tri/carrier_path).read_bytes()!=original_carrier or sorted(runner._stl_solids.get(carrier_path,[]))!=expected_names:raise RuntimeError('carrier was split/reassigned/re-written during actual load')
 if len(list(tri.glob('*liquid_transport*.stl')))!=1:raise RuntimeError('unexpected split transport carrier files')
 runner._prepare_runtime_metadata();phase('actual-runtime-metadata',bridgePatchCount=len(runner._bridge_registry))
 runner._generate_mesh_dicts();phase('actual-mesh-dictionaries-generated')
 runner._ensure_mesh_system_files();phase('actual-minimal-system-files')
 runner._write_dcCart_mesh_dict(case/'Mesh/system');phase('actual-dcCart-dictionary-written')
 text=(case/'Mesh/system/snappyHexMeshDict').read_text()
 marker='    merged_liquid_transport\n    {'
 if marker not in text or 'merged_liquid_transport.stl' not in text or 'name merged_liquid_transport;' not in text:raise RuntimeError('whole carrier not declared in real snappy geometry/refinement input')
 carrier_block=text[text.index(marker):];carrier_block=carrier_block[:carrier_block.index('\n    }')+7]
 if 'patchInfo { type wall; inGroups (wall); }' not in carrier_block or 'faceType internal' in carrier_block:raise RuntimeError('transport carrier not emitted as actual wall refinement surface')
 for name in expected_names:
  if f'{name} {{ name {name}; }}' not in text or name not in carrier_block:raise RuntimeError('source-owned transport region absent in wall/region dictionary '+name)
 if (tri/carrier_path).read_bytes()!=original_carrier or manifest.read_bytes()!=original_manifest:raise RuntimeError('carrier or source manifest changed during dictionary stage')
 source_after={p:sha((repo/p).read_bytes()) for p in source_paths}
 if source_before!=source_after:raise RuntimeError('normal backend source changed during replay')
 receipt.update(rayObservation={'epochs':[meta for _,meta in epochs],'queryCount':len(queries),'batchCalls':batch_calls,'maxBatchTriangles':batch_max,'batchErrors':batch_errors,'raySeconds':ray_seconds,'maxRssKiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},sourceAfter=source_after,sourceManifestUnchanged=True,wholeCarrierUnchanged=True,carrierSha256=sha(original_carrier),carrierFacetCount=sum(r['sourceFacetCount'] for r in expected),actualLoadedCarrierSolids=sorted(runner._stl_solids[carrier_path]),actualWallRefinementBlock=carrier_block,systemFiles={p.name:{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted((case/'Mesh/system').iterdir()) if p.is_file()},ok=True)
 phase('actual-owned-whole-carrier-wall-contract-passed');print(json.dumps({'ok':True,'elapsedS':time.monotonic()-start,'carrierSolids':len(expected_names),'sourceUidCount':len(contributors),'carrierFacets':receipt['carrierFacetCount'],'meshRun':False,'caseDir':str(case)}))
except Exception as exc:
 receipt.update(ok=False,error={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()});phase('actual-stage-refused');traceback.print_exc();sys.exit(1)
finally:
 receipt['elapsedS']=time.monotonic()-start;(out/'RECEIPT.json').write_text(json.dumps(receipt,indent=2))
