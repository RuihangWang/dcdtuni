"""Portable PRIVATE A reproduction: CPU only, source guarded, no live APIs."""
from pathlib import Path
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
F=Path(__file__).resolve().parent
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--cpu',type=int,default=36);p.add_argument('--build',action='store_true');p.add_argument('--native-only',action='store_true');a=p.parse_args()
if a.cpu not in (30,31,36,37,38,39):raise SystemExit('CPU outside reviewed pools; author36–37, independent reviewer30–31, Root38–39')
if a.native_only and a.build:raise SystemExit('native-only excludes frontend/types/build')
O=Path(a.output).resolve()
if not O.is_relative_to(B) or O==B or O.exists():raise SystemExit('output must be new directory strictly inside dcdtuni')
manifest=json.loads((F/'MANIFEST.json').read_text());bad=[]
for row in manifest['files']:
 q=F/row['path']
 if not q.is_file() or q.stat().st_size!=row['bytes'] or hashlib.sha256(q.read_bytes()).hexdigest()!=row['sha256']:bad.append(row['path'])
if bad:raise SystemExit('frozen integrity mismatch '+repr(bad))
O.mkdir();(O/'logs').mkdir();
for name in ('candidate','before','tests','harness','diagnostic-harness','dependency-extra','previous-F1','independent-F1-review'):
 shutil.copytree(F/name,O/name,ignore=shutil.ignore_patterns('node_modules','dist','*.tsbuildinfo','__pycache__'))
# All mutations below are confined to this fresh O clone. Never chmod F.
for q in O.rglob('*'):
 if q.is_file():q.chmod(0o644)
for name in ('joint-actual-native-reply.json',):shutil.copy2(F/'logs'/name,O/'logs'/name);(O/'logs'/name).chmod(0o644)
react=O/'candidate/src/frontend/react';vendor=Path(json.loads((F/'runtime-dependencies.json').read_text())['nodeModules'])
(react/'node_modules').symlink_to(vendor,target_is_directory=True)
for sub in ('tmp','cache','pycache'):(O/sub).mkdir()
env=os.environ.copy();env.update(DCDTUNI_PRIVATE_VITE_CACHE=str(O/'cache/vite'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PXR_WORK_THREAD_LIMIT='1',TMPDIR=str(O/'tmp'),XDG_CACHE_HOME=str(O/'cache'),PYTHONPYCACHEPREFIX=str(O/'pycache'),npm_config_cache=str(O/'cache/npm'))
K=B/'src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release';U=Path('/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.usd.libs-6354e1268d43fec9');native=env.copy();native.update(PYTHONPATH=str(U),LD_LIBRARY_PATH=':'.join([str(U/'bin'),str(K/'kit'),str(K/'kit/python/lib')]))
P='src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
shutil.copytree(O/'candidate'/P,O/'diagnostic-harness/runtime'/P,dirs_exist_ok=True)
shutil.copytree(O/'candidate/src/frontend/shared',O/'diagnostic-harness/runtime/src/frontend/shared',dirs_exist_ok=True)
rows=[]
def run(name,cmd,cwd,e,expect=0):
 start=time.monotonic();r=subprocess.run(['taskset','-c',str(a.cpu),*cmd],cwd=cwd,env=e,capture_output=True,text=True,timeout=115)
 (O/'logs'/f'{name}.stdout').write_text(r.stdout);(O/'logs'/f'{name}.stderr').write_text(r.stderr)
 row={'name':name,'command':cmd,'cwd':str(cwd),'elapsedSec':time.monotonic()-start,'rc':r.returncode,'expectedRC':expect}
 rows.append(row);(O/'steps.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps(row),flush=True)
 if r.returncode!=expect:raise SystemExit('step failed '+name)
 return r
py=str(K/'kit/python/bin/python3')
run('normal-portable-controlled-unit',[sys.executable,str(O/'candidate/src/frontend/omniverse/cfd_demo/tests/test_render_output_observations.py')],O,env)
for name,path in [('actual-IExt-literalAPI-CPUUSD-A','tests/native_a.py'),('camera-coherence','diagnostic-harness/tests/camera_coherence_runtime.py'),('actual-grid-IExt-route','harness/actual_extension_grid_route.py'),('grid-late-worker-source-fences','harness/joint_cpu_contract.py')]:run(name,[py,str(O/path)],O,native)
run('last-metadata-source-time-fences',[py,str(O/'tests/late_metadata_sideeffects.py')],O,native)
# Replay the independent exact product counterexamples. Only the fresh O
# literal path is replaced; parameters, mutations, and hard assertions held.
replay_names=('late_public_getter_pin.py','initial_paired_owner_mismatch.py','initial_paired_same_label_referent.py')
old_helper=O/'previous-F1/render_output_observations.py'
live_helper=O/'diagnostic-harness/runtime'/P/'render_output_observations.py'
new_helper=live_helper.read_bytes()
for phase,expect in (('product-RED-before-F1',1),('product-GREEN-after-F2',0)):
 target=O/'replays'/phase;target.mkdir(parents=True)
 live_helper.write_bytes(old_helper.read_bytes() if expect else new_helper)
 try:
  for name in replay_names:
   src=(O/'independent-F1-review'/name).read_text()
   marker="/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/render-output-independent-fresh-v1-20261007"
   if src.count(marker)!=1:raise SystemExit('independent replay exact-path anchor changed')
   (target/name).write_text(src.replace(marker,str(O)))
   run(phase+'-'+name,[py,str(target/name)],O,native,expect)
 finally:
  live_helper.write_bytes(new_helper)
if not a.native_only:
 base=O/'contract-baseline'
 for sub in ('src/frontend/react','src/frontend/shared'):shutil.copytree(O/'candidate'/sub,base/sub,ignore=shutil.ignore_patterns('node_modules','dist','*.tsbuildinfo'))
 for rel in ('src/frontend/react/src/renderer/ovDisplayDiagnostics.ts','src/frontend/shared/schema/render-display-diagnostics.schema.json'):shutil.copy2(O/'before'/rel,base/rel)
 (base/'src/frontend/react/node_modules').symlink_to(vendor,target_is_directory=True)
 config="import base from './vite.config';\nif(!base||typeof base!=='object')throw new Error('Unsupported private runner config');\nexport default {...base,cacheDir:process.env.DCDTUNI_PRIVATE_VITE_CACHE};\n"
 for path in (react,base/'src/frontend/react'):(path/'vite.private-repro.ts').write_text(config)
 v=['/usr/bin/node',str(vendor/'vitest/vitest.mjs'),'run','--config','vite.private-repro.ts','--cache=false','--maxWorkers=1','--minWorkers=1']
 red=run('new-contract-baseline-NOT-product-defect',[*v,'test/ovRenderOutputObservations.test.ts'],base/'src/frontend/react',env,1)
 if not re.search(r'2 failed\s*\|\s*33 passed',red.stdout):raise SystemExit('unexpected baseline failure; not the preserved new-contract RED')
 run('focused-FE75',[*v,'test/ovRenderOutputObservations.test.ts','test/ovDisplayDiagnostics.test.ts','test/ovDisplayDiagnostics.native.test.ts','test/ovDisplayDiagnostics.selector.test.ts','test/gridRasterDiagnosticUnion.test.ts'],react,env)
 if a.build:
  run('typescript',['/usr/bin/node',str(vendor/'typescript/bin/tsc'),'--noEmit'],react,env)
  be=env.copy();be['DCDTUNI_OV_RETURN_DIAGNOSTICS']='bounded-display-v1'
  run('production-build',['/usr/bin/node',str(vendor/'vite/bin/vite.js'),'build','--config','vite.private-repro.ts'],react,be)
(O/'SUMMARY.json').write_text(json.dumps({'scope':'PRIVATE CPU fixture reproduction; actual production routes/public source+CPUUSD; controlled native values only, not live/media/layout/pixels','steps':rows,'rawMetadataNotEncodedSameFrameProof':True,'layoutProof':'unresolved','pixelVerified':False},indent=2)+'\n')
print('ALL_EXPECTED_STEPS_COMPLETE',flush=True)
