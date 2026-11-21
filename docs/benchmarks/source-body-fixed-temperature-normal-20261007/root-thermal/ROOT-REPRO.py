#!/usr/bin/env python3
from pathlib import Path
import argparse,json,hashlib,shutil,subprocess,os,time
ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);args=ap.parse_args()
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');original=B/'.codex-tmp/fixed-temperature-patch-binding-private-20261007';F=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/fixed-temperature-patch-binding-private-20261007/FROZEN-candidate-v1')
if (F/'manifest.json').exists():
 for row in json.loads((F/'manifest.json').read_text())['payload']:
  by=(F/row['path']).read_bytes()
  if len(by)!=row['bytes']or hashlib.sha256(by).hexdigest()!=row['sha256']:raise RuntimeError('Frozen payload mismatch:'+row['path'])
for file,key in [('normal-before.json',None),('upstream-input-bindings.json',None),('frontend-provider-bindings.json','inputs'),('actual-frontend-provider-bindings.json','inputs')]:
 rows=json.loads((F/file).read_text());rows=rows[key]if key else rows
 for name,row in rows.items():
  p=Path(row.get('path',name));by=p.read_bytes()
  if len(by)!=row['bytes']or hashlib.sha256(by).hexdigest()!=row['sha256']:raise RuntimeError('source/provider/input changed:'+str(p))
O=Path(args.out).resolve()
if not O.is_relative_to((B/'.codex-tmp').resolve())or O.exists():raise RuntimeError('out must be fresh owned B/.codex-tmp path')
O.mkdir(parents=True)
for d in ['candidate','baseline','scripts']:shutil.copytree(F/d,O/d)
for n in ['normal-before.json','upstream-input-bindings.json','frontend-provider-bindings.json','actual-frontend-provider-bindings.json','SCOPE.json','apply-map.json']:shutil.copyfile(F/n,O/n)
for p in O.rglob('*'):p.chmod(0o755 if p.is_dir()else 0o644)
for p in (O/'scripts').iterdir():
 if p.suffix in ['.py','.ts','.cjs']:p.write_text(p.read_text().replace(str(original),str(O)).replace("B/'.codex-tmp/fixed-temperature-patch-binding-private-20261007'",'Path('+repr(str(O))+')'))
for d in ['logs','evidence','exports','cases']:(O/d).mkdir(exist_ok=True)
env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1');receipts=[]
steps=[('bundle',['node',str(O/'scripts/bundle.cjs')],{})]
for sup in ['0','1','2']:
 for mode in ['normal','candidate']:steps.append((mode+'-'+sup,['node',str(O/'scripts/observe.cjs')],{'PRODUCER_MODE':mode,'SUPPLEMENTAL':sup}))
steps += [('contracts',['/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python',str(O/'scripts/prepare-regression-contract.py')],{}),('assess',['/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python',str(O/'scripts/assess-thermal.py')],{})]
for name,argv,extra in steps:
 e=env.copy();e.update(extra);t=time.monotonic();cp=subprocess.run(['taskset','-c','6,7']+argv,capture_output=True,text=True,env=e,timeout=55);(O/'logs'/f'repro-{name}.stdout').write_text(cp.stdout);(O/'logs'/f'repro-{name}.stderr').write_text(cp.stderr);receipts.append({'name':name,'rc':cp.returncode,'seconds':time.monotonic()-t,'argv':argv});(O/'REPRO-steps.json').write_text(json.dumps(receipts,indent=2)+'\n')
 if cp.returncode:raise RuntimeError('replay failed:'+name+'; literal logs retained')
a=json.loads((O/'evidence/assertions.json').read_text());raw=O/'cases/regression/candidate/wall-fixed-patch/model/source.cfdrun';summary={'frozenManifestSHA256':hashlib.sha256((F/'manifest.json').read_bytes()).hexdigest()if(F/'manifest.json').exists()else None,'fixturePairs':a['fixturePairs'],'checks':a['checks'],'failed':a['failed'],'knownSeparateLegacyOrSeedRefusals':len(a['knownSeparateLegacyOrSeedRefusals']),'candidateSourceSHA256':hashlib.sha256((O/'candidate/src/frontend/shared/js/cfdrun_build.js').read_bytes()).hexdigest(),'actualRawSHA256':hashlib.sha256(raw.read_bytes()).hexdigest(),'actualMeshInput':str(raw.parents[1]),'normalApplied':False,'nativeExecuted':False};(O/'REPRO-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
