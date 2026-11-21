"""Replay actual whole React files with normal backend consumers; dictionary stage only."""
import argparse,hashlib,json,os,re,shutil,sys,time,traceback
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
 from backend.aircooling.runner.stlgen_runner import STLGenRunner, _case_locked
 runner=STLGenRunner(case_dir=case,cfdrun_path=manifest,expected_cfdrun_sha256=sha(original_manifest))
 @_case_locked
 def stage_only(runner):
  runner._verify_hybrid_source_stls();phase('actual-source-STL-hashes-verified',verifiedSource=getattr(runner,'_source_stls_verified',False),pinnedManifest=getattr(runner,'_verified_source_cfdrun_sha256',None))
  runner._load();phase('actual-loaded-split-other-buckets',loadedFiles={k:len(v) for k,v in runner._stl_solids.items()})
  if (tri/carrier_path).read_bytes()!=original_carrier or sorted(runner._stl_solids.get(carrier_path,[]))!=expected_names:raise RuntimeError('carrier was split/reassigned/re-written during actual load')
  if len(list(tri.glob('*liquid_transport*.stl')))!=1:raise RuntimeError('unexpected split transport carrier files')
  runner._prepare_runtime_metadata();phase('actual-runtime-metadata',bridgePatchCount=len(runner._bridge_registry))
  runner._generate_mesh_dicts();phase('actual-mesh-dictionaries-generated')
  runner._ensure_mesh_system_files();phase('actual-minimal-system-files')
  runner._write_dcCart_mesh_dict(case/'Mesh/system');phase('actual-dcCart-dictionary-written')
  # Invoke the real inventory and authority readers while the normal input lock is held.
  from backend.aircooling.runner.point_repair_source_contract import collect_actual_inventory,derive_singleton_authority
  from backend.aircooling.runner.weight_union_source_contract import derive_weight_authority
  snappy=(case/'Mesh/system/snappyHexMeshDict').read_bytes()
  dc=(case/'Mesh/system/dcCartMeshDict').read_text()
  match=re.search(r"wallPairZones\s*\(([^)]*)\)\s*;",dc)
  zones=set(match.group(1).split()) if match else set()
  receipt['actualExecutionIdentityDuringStage']=runner._execution_cfdrun_sha256
  try:
   inv=collect_actual_inventory(raw,tri_surface_dir=tri,snappy_bytes=snappy,wall_pair_zones=zones,verified_original_receipt=runner._point_repair_source_receipt)
   (out/'ACTUAL-SOURCE-INVENTORY.json').write_text(json.dumps(inv,indent=2))
   absence=derive_singleton_authority(original_manifest,locked_raw_sha256=runner._execution_cfdrun_sha256,derived_data=runner._data,resolved_run=runner._resolved,actual_region_inventory=inv)
   weight=derive_weight_authority(original_manifest,locked_raw_sha256=runner._execution_cfdrun_sha256,derived_data=runner._data,resolved_run=runner._resolved,actual_region_inventory=inv,necessary_absence=absence)
   contracts={'singleton':absence,'weight':weight}
   (out/'ACTUAL-SOURCE-AUTHORITIES.json').write_text(json.dumps(contracts,indent=2))
   receipt['sourceAdmission']={'inventoryOk':True,'eligible':all(row['eligible'] for row in contracts.values()),'contracts':contracts}
  except Exception as exc:
   receipt['sourceAdmission']={'inventoryOk':False,'eligible':False,'exception':{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}}
   (out/'ACTUAL-SOURCE-INVENTORY-REFUSAL.json').write_text(json.dumps(receipt['sourceAdmission'],indent=2))
  phase('actual-source-inventory-and-authority-observed',sourceAdmission=receipt['sourceAdmission'])
 stage_only(runner)
 if runner._execution_cfdrun_sha256 is not None:raise RuntimeError('normal case decorator did not clear execution identity')
 receipt['actualCaseLockReleased']=True
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
 receipt.update(sourceAfter=source_after,sourceManifestUnchanged=True,wholeCarrierUnchanged=True,carrierSha256=sha(original_carrier),carrierFacetCount=sum(r['sourceFacetCount'] for r in expected),actualLoadedCarrierSolids=sorted(runner._stl_solids[carrier_path]),actualWallRefinementBlock=carrier_block,systemFiles={p.name:{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted((case/'Mesh/system').iterdir()) if p.is_file()},ok=True)
 phase('actual-owned-whole-carrier-wall-contract-passed');print(json.dumps({'ok':True,'elapsedS':time.monotonic()-start,'carrierSolids':len(expected_names),'sourceUidCount':len(contributors),'carrierFacets':receipt['carrierFacetCount'],'meshRun':False,'caseDir':str(case)}))
except Exception as exc:
 receipt.update(ok=False,error={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()});phase('actual-stage-refused');traceback.print_exc();sys.exit(1)
finally:
 receipt['elapsedS']=time.monotonic()-start;(out/'RECEIPT.json').write_text(json.dumps(receipt,indent=2))
