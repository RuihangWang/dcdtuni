#!/usr/bin/env python3
"""Frozen joint PRIVATE source-bound repro; no normal mutation, service, GPU or API."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,time
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration';F=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--cpu',type=int,default=36);p.add_argument('--output',required=True);p.add_argument('--narrow',action='store_true');p.add_argument('--build',action='store_true');p.add_argument('--native-only',action='store_true');a=p.parse_args()
if a.native_only and a.build:raise SystemExit('--native-only does not execute frontend/types/build')
if a.cpu not in(36,37,38,39):raise SystemExit('CPU36-39 only; parent owns allocation')
O=Path(a.output).resolve()
if not O.is_relative_to(B/'.codex-tmp')or O.exists():raise SystemExit('Output must be fresh under dcdtuni/.codex-tmp')
sha=lambda q:hashlib.sha256(q.read_bytes()).hexdigest()
def verify():
 m=json.loads((F/'manifest.json').read_text());bad=[x['path']for x in m['files']if not(F/x['path']).is_file()or sha(F/x['path'])!=x['sha256']]
 if bad:raise RuntimeError('Frozen payload drift: '+str(bad))
 return{'manifestSHA256':sha(F/'manifest.json'),'payloadCount':len(m['files'])}
dep=json.loads((F/'dependency-identity.json').read_text());nm=Path(dep['normalNodeModules']);sdk=Path(dep['installedSDKEntry'])
if sha(sdk)!=dep['rawEntrySHA256']or sha(nm/'three/package.json')!=dep['threePackageSHA256']or sha(nm/'three/build/three.module.js')!=dep['threeModuleSHA256']:raise SystemExit('Source-bound installed SDK/Three changed')
frozenBefore=verify();O.mkdir();shutil.copytree(F,O/'packet',copy_function=shutil.copyfile);P=O/'packet';R=P/'candidate/src/frontend/react';(R/'node_modules').symlink_to(nm,target_is_directory=True);(P/'harness/node_modules').symlink_to(nm,target_is_directory=True);(P/'diagnostic-harness/runtime').symlink_to('../candidate',target_is_directory=True)
for x in ['env/tmp','env/cache']:(O/x).mkdir(parents=True,exist_ok=True)
env=os.environ.copy();env.update(TMPDIR=str(O/'env/tmp'),TMP=str(O/'env/tmp'),TEMP=str(O/'env/tmp'),XDG_CACHE_HOME=str(O/'env/cache'),PYTHONPYCACHEPREFIX=str(O/'env/cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PXR_WORK_THREAD_LIMIT='1')
rows=json.loads((F/'apply-map.json').read_text())['targets'];watch=[root/x['target']for root in(B,I)for x in rows]+[sdk,nm/'three/package.json',nm/'three/build/three.module.js']+[I/x for x in('case/Shinaide_Liquid/Shinaide_liquid.cfdproj','case/DAML-Testbed/DMAL_Testbed.cfdproj')]
sample=lambda:{str(q):sha(q)if q.exists()else None for q in watch};before=sample();prod=[x for x in rows if x['kind']=='production'];candidateBefore={x['target']:sha(P/'candidate'/x['target'])for x in prod}
receipt={'scope':'PRIVATE actual CPU USD/installed API/Three/React controlled carriers. Layout remains unresolved by default; no live Kit/GPU/browser/pixel/mesh/CFD acceptance.','frozenBefore':frozenBefore,'normalBefore':before,'candidateBefore':candidateBefore,'steps':[]}
def run(label,cmd,cwd,e=env,expected=0):
 t=time.monotonic();z=subprocess.run(cmd,cwd=cwd,env=e,capture_output=True);(O/(label+'.stdout')).write_bytes(z.stdout);(O/(label+'.stderr')).write_bytes(z.stderr);row={'label':label,'command':cmd,'cwd':str(cwd),'returncode':z.returncode,'expectedReturncode':expected,'elapsedSeconds':time.monotonic()-t};receipt['steps'].append(row);(O/'RECEIPT.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(row),flush=True)
 if z.returncode!=expected:raise RuntimeError('Repro step failed: '+label)
 return z
node=shutil.which('node');run('actual-Three-projection',['taskset','-c',str(a.cpu),node,str(P/'harness/produce_actual_three_projection.mjs'),str(P/'harness/canonical-grid.json')],R)
if sha(O/'actual-Three-projection.stdout')!=dep['actualThreeProjectionFixtureSHA256']:raise RuntimeError('Installed Three fixture byte identity changed')
K=B/'src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release/kit';U=Path('/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.usd.libs-6354e1268d43fec9');ne=env.copy();ne.update(PYTHONPATH=str(U),LD_LIBRARY_PATH=str(U/'bin')+':'+str(K)+':'+str(K/'python/lib'))
cmd=lambda rel,extra=[]:['taskset','-c',str(a.cpu),str(K/'python/bin/python3'),str(P/rel)]+extra
run('literal-v1-4-RED',cmd('harness/replay_literal_v1_counterexamples.py'),P,ne)
for ref in(True,False):run('safe-ordinals-'+('F2-RED'if ref else'corrected'),cmd('harness/safe_ordinal_retirement.py',['--reference']if ref else[]),P,ne)
for ref in(True,False):run('retirement-floors-'+('F1-RED'if ref else'corrected'),cmd('harness/retirement_floors.py',['--expect-red']if ref else[]),P,ne)
for ref in(True,False):run('actual-IExt-'+('original-RED'if ref else'corrected'),cmd('harness/actual_extension_grid_route.py',['--reference']if ref else[]),P,ne)
for ref in(True,False):run('joint-native-'+('original-RED'if ref else'corrected'),cmd('harness/joint_cpu_contract.py',['--reference']if ref else[]),P,ne)
run('v2-full-actual-CPUUSD',cmd('harness/v2_cpu_contract.py'),P,ne)
for name in('native_runtime','camera_coherence_runtime','nonzero_time_pending_runtime','authored_negative_runtime','root_camera_probe_candidate'):run('diagnostic-'+name,cmd('diagnostic-harness/tests/'+name+'.py'),P/'diagnostic-harness',ne)
if not a.native_only:
 # Baseline product source is restored only in this fresh private copy; freeze/normal are never written.
 paths=['src/frontend/react/src/renderer/displayRaster.ts','src/frontend/react/src/renderer/ovDisplayDiagnostics.ts','src/frontend/shared/schema/render-display-diagnostics.schema.json'];held={q:(P/'candidate'/q).read_bytes()for q in paths}
 base=['taskset','-c',str(a.cpu),node,str(R/'node_modules/vitest/vitest.mjs'),'run'];flags=['--cache=false','--maxWorkers=1','--minWorkers=1']
 try:
  for q in paths:(P/'candidate'/q).write_bytes((P/('before-correction'if q.endswith('displayRaster.ts')else'source-before')/q).read_bytes())
  run('original-CSS-diagnostic-RED',base+['test/gridRasterCssCapability.test.ts','test/gridRasterDiagnosticUnion.test.ts']+flags,R,expected=1)
 finally:
  for q,v in held.items():(P/'candidate'/q).write_bytes(v)
 files=json.loads((F/'frontend-suites.json').read_text())
 if a.narrow:files=['test/'+q for q in('gridRasterCssCapability.test.ts','gridRasterDiagnosticUnion.test.ts','gridRasterPresentation.test.ts','gridRasterSharedController.test.tsx','ovDisplayDiagnostics.test.ts','ovDisplayDiagnostics.selector.test.ts')]
 e=env.copy();e['DCDTUNI_OV_RETURN_DIAGNOSTICS']='bounded-display-v1';run('frontend-joint',base+files+flags,R,e)
 for label,token in(('off',None),('wrong-token','1')):
  e=env.copy();e.pop('DCDTUNI_OV_RETURN_DIAGNOSTICS',None)
  if token is not None:e['DCDTUNI_OV_RETURN_DIAGNOSTICS']=token
  run('selector-'+label,base+['test/ovDisplayDiagnostics.selector.test.ts']+flags,R,e)
 if not a.narrow or a.build:run('types',['taskset','-c',str(a.cpu),node,str(R/'node_modules/typescript/bin/tsc'),'--noEmit'],R)
 if a.build:run('production-build',['taskset','-c',str(a.cpu),node,str(R/'node_modules/vite/bin/vite.js'),'build'],R)
receipt.update(frozenAfter=verify(),normalAfter=sample(),candidateAfter={x['target']:sha(P/'candidate'/x['target'])for x in prod});receipt['normalHeld']=receipt['normalBefore']==receipt['normalAfter'];receipt['normalExternalDrift']=[x for x in before if before[x]!=receipt['normalAfter'][x]];receipt['candidateHeld']=receipt['candidateBefore']==receipt['candidateAfter'];receipt['SDKHeld']=sha(sdk)==dep['rawEntrySHA256'];receipt['samplerHeld']=sha(P/'candidate/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/grid_viewport.py')==dep['samplerSHA256'];receipt['returncode']=0
if(R/'dist/assets').exists():receipt['bundle']=[{'name':q.name,'bytes':q.stat().st_size,'sha256':sha(q)}for q in sorted((R/'dist/assets').glob('*.js'))]
(O/'RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({k:receipt[k]for k in('normalHeld','normalExternalDrift','candidateHeld','SDKHeld','samplerHeld','returncode')}))
if not receipt['candidateHeld']or not receipt['SDKHeld']or not receipt['samplerHeld']:raise SystemExit(1)
