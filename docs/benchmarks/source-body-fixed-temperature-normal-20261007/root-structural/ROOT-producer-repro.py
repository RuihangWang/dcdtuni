#!/usr/bin/env python3
from pathlib import Path
import argparse,json,hashlib,shutil,os,subprocess,time
ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);args=ap.parse_args()
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');original=B/'.codex-tmp/point-source-role-producer-private-20261007';F=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/point-source-role-producer-private-20261007/FROZEN-candidate-v1')
if not(F/'manifest.json').exists():F=F/'FROZEN-candidate-v1'
m=json.loads((F/'manifest.json').read_text())
for row in m['payload']:
 p=F/row['path'];by=p.read_bytes()
 if len(by)!=row['bytes']or hashlib.sha256(by).hexdigest()!=row['sha256']:raise RuntimeError('Frozen payload mismatch:'+row['path'])
for file,dictkey in [('normal-before.json',None),('upstream-input-bindings.json',None),('frontend-provider-bindings.json','inputs')]:
 rows=json.loads((F/file).read_text());rows=rows[dictkey]if dictkey else rows
 for key,row in rows.items():
  p=Path(row.get('path',key));by=p.read_bytes()
  if len(by)!=row['bytes']or hashlib.sha256(by).hexdigest()!=row['sha256']:raise RuntimeError('current source/input/provider differs:'+str(p))
O=Path(args.out).resolve()
if not O.is_relative_to((B/'.codex-tmp').resolve())or O.exists():raise RuntimeError('out must be fresh owned directory under B/.codex-tmp')
O.mkdir(parents=True)
shutil.copytree(F/'candidate',O/'candidate');shutil.copytree(F/'scripts',O/'scripts')
for name in ['normal-before.json','SCOPE.json','apply-map.json','normal-to-private.patch','upstream-input-bindings.json','frontend-provider-bindings.json']:shutil.copyfile(F/name,O/name)
for p in O.rglob('*'):
 if p.is_dir():p.chmod(0o755)
 else:p.chmod(0o644)
for p in (O/'scripts').iterdir():
 if p.suffix in ['.py','.ts','.cjs']:p.write_text(p.read_text().replace(str(original),str(O)).replace("B/'.codex-tmp/point-source-role-producer-private-20261007'",'Path('+repr(str(O))+')'))
for d in ['logs','evidence','exports','cases']:(O/d).mkdir(exist_ok=True)
env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
steps=[('bundle',['node',str(O/'scripts/bundle.cjs')],{}),('normal-main',['node',str(O/'scripts/observe.cjs')],{'PRODUCER_MODE':'normal','SUPPLEMENTAL':'0'}),('candidate-main',['node',str(O/'scripts/observe.cjs')],{'PRODUCER_MODE':'candidate','SUPPLEMENTAL':'0'}),('normal-supplement',['node',str(O/'scripts/observe.cjs')],{'PRODUCER_MODE':'normal','SUPPLEMENTAL':'1'}),('candidate-supplement',['node',str(O/'scripts/observe.cjs')],{'PRODUCER_MODE':'candidate','SUPPLEMENTAL':'1'}),('normal-contracts',['/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python',str(O/'scripts/prepare-regression-contract.py')],{}),('assertions',['/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python',str(O/'scripts/assess.py')],{})]
receipts=[]
for tag,argv,extra in steps:
 e=env.copy();e.update(extra);t=time.monotonic();cp=subprocess.run(['taskset','-c','6,7']+argv,capture_output=True,text=True,env=e,timeout=55)
 (O/'logs'/f'repro-{tag}.stdout').write_text(cp.stdout);(O/'logs'/f'repro-{tag}.stderr').write_text(cp.stderr);receipts.append({'tag':tag,'argv':argv,'rc':cp.returncode,'seconds':time.monotonic()-t})
 (O/'repro-steps.json').write_text(json.dumps(receipts,indent=2)+'\n')
 if cp.returncode:raise RuntimeError('repro step failed:'+tag+'; exact raw logs retained')
q=json.loads((O/'evidence/assertions.json').read_text());raw=O/'cases/regression/candidate/held-explicit-wall/model/source.cfdrun';summary={'frozenManifestSHA256':hashlib.sha256((F/'manifest.json').read_bytes()).hexdigest(),'fixturePairs':q['fixturePairs'],'assertions':q['checks'],'failed':q['failed'],'knownOriginalRefusalsCount':len(q['knownPreexistingFailures']),'heldWallRawSHA256':hashlib.sha256(raw.read_bytes()).hexdigest(),'freshActualMeshInput':str(raw.parents[1]),'candidateSourceSHA256':hashlib.sha256((O/'candidate/src/frontend/shared/js/cfdrun_build.js').read_bytes()).hexdigest(),'normalApplied':False,'nativeRun':False};(O/'REPRO-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
