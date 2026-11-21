#!/usr/bin/env python3
"""Exact independent native replay only; no normal source/runtime writes."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,time
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');S=Path(__file__).resolve().parent;F=B/'.codex-tmp/ov-grid-joint-safe-ordinal-v3-private-shared-20261007/FROZEN-joint-v3';I=B/'.codex-tmp/goal-20261005/integration'
a=argparse.ArgumentParser();a.add_argument('--output',required=True);a.add_argument('--cpu',type=int,default=30);args=a.parse_args()
if args.cpu not in(30,31):raise SystemExit('CPU30/31 only under Root allocation; no allocation implied by this runner')
O=Path(args.output).resolve()
if not O.is_relative_to(B/'.codex-tmp')or O.exists():raise SystemExit('Output must be fresh inside dcdtuni/.codex-tmp')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
if sha(F/'manifest.json')!='89d62927ea50f4c4202b22304388c311db37fd163e5d2509db4e7598cee8f90f':raise SystemExit('Frozen F3 binding changed')
def verify():
 m=json.loads((F/'manifest.json').read_text());bad=[e['path']for e in m['files']if len((F/e['path']).read_bytes())!=e['bytes']or sha(F/e['path'])!=e['sha256']]
 if bad:raise RuntimeError('Frozen input changed: '+str(bad))
 return {'manifest':sha(F/'manifest.json'),'files':len(m['files']),'bytes':sum(e['bytes']for e in m['files'])}
receipt={'frozenBefore':verify(),'scope':'Independent native/CPUUSD controlled-context replay; legacy layout unresolved, no pixels/deployment/CFD acceptance'}
O.mkdir();shutil.copytree(F,O/'packet',copy_function=shutil.copyfile);P=O/'packet';(P/'diagnostic-harness/runtime').symlink_to('../candidate',target_is_directory=True)
for n in ['independent_safe_ordinals.py','independent_invalid_identity.py','independent_inclusive_boundaries.py']:shutil.copyfile(S/'harness'/n,P/'harness'/n)
for n in['env/tmp','env/cache']:(P/n).mkdir(parents=True,exist_ok=True)
rows=json.loads((F/'apply-map.json').read_text())['targets'];sample=lambda:{str(I/e['target']):sha(I/e['target'])if(I/e['target']).exists()else None for e in rows};receipt['normalBefore']=sample();receipt['steps']=[]
K=B/'src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release/kit';U=Path('/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.usd.libs-6354e1268d43fec9');env=os.environ.copy();env.update(PYTHONPATH=str(U),LD_LIBRARY_PATH=str(U/'bin')+':'+str(K)+':'+str(K/'python/lib'),TMPDIR=str(P/'env/tmp'),TMP=str(P/'env/tmp'),TEMP=str(P/'env/tmp'),XDG_CACHE_HOME=str(P/'env/cache'),PYTHONPYCACHEPREFIX=str(P/'env/cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PXR_WORK_THREAD_LIMIT='1')
deadline=time.monotonic()+120
for name in['independent_safe_ordinals.py','independent_invalid_identity.py','independent_inclusive_boundaries.py','retirement_floors.py','joint_cpu_contract.py','actual_extension_grid_route.py']:
 cmd=['taskset','-c',str(args.cpu),str(K/'python/bin/python3'),str(P/'harness'/name)];t=time.monotonic()
 try:z=subprocess.run(cmd,cwd=P,env=env,capture_output=True,timeout=max(1,deadline-time.monotonic()));rc=z.returncode;out=z.stdout;err=z.stderr
 except subprocess.TimeoutExpired as e:rc=124;out=e.stdout or b'';err=e.stderr or b''
 (O/(name+'.stdout')).write_bytes(out);(O/(name+'.stderr')).write_bytes(err);step={'argv':cmd,'rc':rc,'elapsed':time.monotonic()-t};receipt['steps'].append(step);(O/'RECEIPT.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(step),flush=True)
 if rc:raise SystemExit(rc)
receipt.update(frozenAfter=verify(),normalAfter=sample(),privateTargetHeld=all(sha(P/'candidate'/e['target'])==e['afterSHA256']for e in rows));receipt['normalHeld']=receipt['normalBefore']==receipt['normalAfter'];receipt['normalExternalChanges']=[k for k in receipt['normalBefore']if receipt['normalBefore'][k]!=receipt['normalAfter'][k]];(O/'RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'nativeGreen':True,'normalHeld':receipt['normalHeld'],'privateTargetHeld':receipt['privateTargetHeld']}))
if not receipt['privateTargetHeld']:raise SystemExit(1)
