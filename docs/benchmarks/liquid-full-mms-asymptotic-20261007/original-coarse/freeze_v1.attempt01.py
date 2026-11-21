"""Freeze completed PRIVATE full-system MMS evidence; no normal source mutation."""
from pathlib import Path
import datetime,gzip,hashlib,json,os,shutil,stat,sys
ROOT=Path(__file__).resolve().parent
IT=ROOT.parents[1]
F=ROOT/'FROZEN-v1'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as stream:
  for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
 return h.hexdigest()
def write(p,value):p.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
if F.exists():raise FileExistsError('immutable frozen destination exists')
assert ROOT.is_relative_to(IT/'.codex-tmp')
mapping=json.loads((ROOT/'normal-before-after-mapping.json').read_text())
for row in mapping:
 normal=IT/row['path']
 current=sha(normal) if normal.exists() else None
 assert current==row['before_sha256'],row['path']+' normal before drift'
 assert sha(ROOT/'after'/row['path'])==row['after_sha256']
before=json.loads((ROOT/'source-before.json').read_text())
for row in before:
 rel=Path(row['path']).relative_to(IT)
 assert sha(ROOT/'before'/rel)==row['sha256'],str(rel)+' execution snapshot changed'
workers=[]
names=('backend_tests.py','run_final.py','run_refinement.py','fine_newton.py','sensitivity.py','vitest.mjs')
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit() or int(proc.name)==os.getpid():continue
 try:
  cmd=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
  if str(ROOT) in cmd and any(n in cmd for n in names):
   workers.append({'pid':int(proc.name),'cmdline':cmd})
 except (OSError,ProcessLookupError):pass
assert not workers,'owned numerical workers still active'
# Record normal source drift separately; immutable owned snapshots carry execution identities.
drift=[]
for row in before:
 normal=Path(row['path']);current=sha(normal) if normal.is_file() else None
 if current!=row['sha256']:drift.append({'path':str(normal),'execution_snapshot_sha256':row['sha256'],'current_normal_sha256':current})
write(ROOT/'results/final-freeze-preflight.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'affinity':sorted(os.sched_getaffinity(0)),'cuda':os.environ.get('CUDA_VISIBLE_DEVICES'),'all_ten_normal_targets_before':True,'original_snapshots_verified':len(before),'concurrent_normal_other_drift':drift,'active_owned_numerical_workers':workers,'normal_write':False})
# Prospective sources and protected old inputs are primary external references.
reference_paths=set(x['path'] for x in json.loads((ROOT/'tooling/references.json').read_text()))
reference_paths.update(str(IT/x['path']) for x in mapping if x['before_sha256'] is not None)
reference_paths.update(str(IT/r) for r in [
 'AGENTS.md','case/Shinaide_Liquid/Shinaide_liquid.cfdproj',
 'case/Shinaide_Liquid/Shinaide_liquid_ring_rear.cfdproj',
 'case/MMS_Product/MMS_product.cfdproj',
 'docs/benchmarks/fixtures/mms-product-v3.liquidrun',
 'docs/developer/liquid_dae_mms_product_derivation.md',
 'src/frontend/react/src/liquid/dynamicLiquidGraph.ts',
 'src/backend/liquidcooling/graphcompiling/compiler.py',
 '.codex-tmp/shinaide-liquid-ring-rear-private-plan-20261006/FROZEN-v2/manifest.json',
 '.codex-tmp/meshgen-compound-seam-actual-capture-preview-20261006/FROZEN-audit-v1/manifest.json'])
references=[]
for s in sorted(reference_paths):
 p=Path(s);assert p.is_file(),s
 references.append({'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)})
F.mkdir()
archives=[];originals=[];compressed_original_bytes=0
exclude={'tmp','cache','node_modules','FROZEN-v1'}
paths=[]
for p in sorted(ROOT.rglob('*')):
 rel=p.relative_to(ROOT)
 if any(part in exclude or part=='__pycache__' for part in rel.parts) or p.suffix=='.pyc':continue
 if p.is_symlink():raise ValueError('unexpected symlink '+str(rel))
 if p.is_file():paths.append((p,rel))
for p,rel in paths:
 digest=sha(p);size=p.stat().st_size
 compress=(rel.parts[0] not in ('before','after') and size>=262144 and p.suffix in ('.json','.cfdproj','.liquidrun','.npz'))
 originals.append({'path':rel.as_posix(),'bytes':size,'sha256':digest,'compressed':compress})
 if compress:
  out=F/'archives'/Path(rel.as_posix()+'.gz');out.parent.mkdir(parents=True,exist_ok=True)
  with p.open('rb') as inp,out.open('xb') as raw:
   with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=6) as packed:shutil.copyfileobj(inp,packed)
  archives.append({'original_path':rel.as_posix(),'original_bytes':size,'original_sha256':digest,'archive_path':out.relative_to(F).as_posix(),'archive_bytes':out.stat().st_size,'archive_sha256':sha(out)})
  compressed_original_bytes+=size
 else:
  out=F/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,out)
  assert sha(out)==digest
write(F/'archive-map.json',{'format':'lossless-raw-payload-map','version':1,'entries':archives})
write(F/'original-payload-map.json',{'version':1,'count':len(originals),'bytes':sum(x['bytes'] for x in originals),'payloads':originals})
payloads=[{'path':p.relative_to(F).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(F.rglob('*')) if p.is_file()]
manifest={'schemaVersion':1,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'frozen_root':str(F),'execution_root':str(ROOT),'scope':'PRIVATE opt-in full ring hydraulic/thermal MMS; numerical evidence only; no normal apply/UI acceptance','excluded':['tmp','cache','node_modules','__pycache__','pyc'],'payload_count':len(payloads),'payload_bytes':sum(x['bytes'] for x in payloads),'payloads':payloads,'references':references,'reference_count':len(references),'archive_count':len(archives),'compressed_original_bytes':compressed_original_bytes,'original_payload_count':len(originals),'original_payload_bytes':sum(x['bytes'] for x in originals)}
write(F/'manifest.json',manifest)
# Full reread verification including lossless original byte identity.
for row in payloads:
 p=F/row['path'];assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256']
for row in references:
 p=Path(row['path']);assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256']
for row in archives:
 h=hashlib.sha256();size=0
 with gzip.open(F/row['archive_path'],'rb') as inp:
  for chunk in iter(lambda:inp.read(1048576),b''):h.update(chunk);size+=len(chunk)
 assert size==row['original_bytes'] and h.hexdigest()==row['original_sha256']
verification={'all_payloads_verified':True,'all_references_verified':True,'all_archives_original_byte_verified':True,'payload_count':len(payloads),'reference_count':len(references),'archive_count':len(archives),'manifest_sha256':sha(F/'manifest.json'),'REPORT_sha256':sha(F/'REPORT.md'),'DERIVATION_sha256':sha(F/'DERIVATION.md'),'source_patch_sha256':sha(F/'source.patch'),'normal_ten_targets_before':True,'active_owned_numerical_workers':[],'normal_apply':False}
write(F/'verification.json',verification)
for p in F.rglob('*'):
 if p.is_file():p.chmod(stat.S_IRUSR|stat.S_IRGRP|stat.S_IROTH)
print(json.dumps({**verification,'frozen_root':str(F),'payload_bytes':manifest['payload_bytes'],'original_payload_bytes':manifest['original_payload_bytes'],'original_payload_count':manifest['original_payload_count'],'verification_sha256':sha(F/'verification.json')},indent=2))
