from pathlib import Path
import argparse,json,hashlib,os,subprocess,signal,time,sys
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
F=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--cpu',type=int,default=30);p.add_argument('--baseline',action='store_true');p.add_argument('--build',action='store_true');a=p.parse_args()
O=Path(a.output).resolve();assert O.parent==B/'.codex-tmp' and not O.exists(),'Require new direct B/.codex-tmp child';assert 0<=a.cpu<64
sha=lambda v:hashlib.sha256(v).hexdigest()
m=json.loads((F/'MANIFEST.json').read_text())
for e in m['files']:
 v=(F/e['path']).read_bytes();assert len(v)==e['bytes'] and sha(v)==e['sha256'],e['path']
s=json.loads((F/'before-source.json').read_text());I=Path(s['I']);assert I==B/'.codex-tmp/goal-20261005/integration'
for e in s['files']:
 v=(I/e['path']).read_bytes();assert len(v)==e['bytes'] and sha(v)==e['sha256'],e['path']
for e in json.loads((F/'installed-dependency-fence.json').read_text())['files']:
 v=Path(e['path']).read_bytes();assert len(v)==e['bytes'] and sha(v)==e['sha256'],e['path']
O.mkdir();C=O/'candidate'
for e in s['files']:
 dst=C/e['path'];dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes((I/e['path']).read_bytes())
for e in json.loads((F/'apply-map.json').read_text())['targets']:
 if a.baseline and e['scope']=='production':continue
 dst=C/e['target'];dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes((F/e['candidate']).read_bytes())
R=C/'src/frontend/react';(R/'node_modules').symlink_to(I/'src/frontend/react/node_modules',target_is_directory=True)
for d in ['logs','cache','tmp']: (O/d).mkdir()
config="import base from './vite.config'\nimport {defineConfig} from 'vite'\nexport default defineConfig({...base,cacheDir:"+json.dumps(str(O/'cache/vite'))+",test:{...base.test,cache:false,maxWorkers:1,minWorkers:1},build:{...base.build,outDir:"+json.dumps(str(O/'dist'))+",emptyOutDir:true}})\n"
(R/'review.vite.config.ts').write_text(config)
env=dict(os.environ);env.update({'TMPDIR':str(O/'tmp'),'XDG_CACHE_HOME':str(O/'cache'),'npm_config_cache':str(O/'cache/npm'),'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
def run(label,args,expected=0):
 t=time.monotonic()
 with (O/f'logs/{label}.stdout').open('wb') as out,(O/f'logs/{label}.stderr').open('wb') as err:
  child=subprocess.Popen(['/usr/bin/taskset','-c',str(a.cpu),'/usr/bin/node',*args],cwd=R,env=env,stdout=out,stderr=err,start_new_session=True);timeout=False
  try: rc=child.wait(timeout=120)
  except subprocess.TimeoutExpired:
   timeout=True;os.killpg(child.pid,signal.SIGTERM)
   try:rc=child.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);rc=child.wait()
 receipt={'argv':args,'cpu':a.cpu,'rc':rc,'expected':expected,'timeout':timeout,'seconds':time.monotonic()-t};(O/f'logs/{label}.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
 assert rc==expected and not timeout,label
 if expected:
  text=(O/f'logs/{label}.stdout').read_text();assert '8 failed | 9 passed (17)' in text and 'Unhandled Errors' not in text
v=['node_modules/vitest/vitest.mjs','run','--config','review.vite.config.ts','--cache=false']
if a.baseline:run('baseline-mounted',v+['test/wholeObjectShiftMounted.test.tsx'],1)
else:
 tests=['wholeObjectShiftMounted.test.tsx','wholeObjectShiftViewport.test.ts','liquidEquipmentVisualInteraction.test.ts','pduSceneDisplay.test.ts','rackFaceGesture.test.ts','selection.test.ts','selectionHighlight.test.ts','viewportTabGate.test.ts','viewportPointerChain.test.ts','faceDragActions.test.ts','faceDragAnchor.test.ts','faceDragMath.test.ts','ovEditBridge.test.ts']
 run('related',v+['test/'+f for f in tests]);run('types',['node_modules/typescript/bin/tsc','--noEmit'])
 if a.build:run('build',['node_modules/vite/bin/vite.js','build','--config','review.vite.config.ts'])
for e in s['files']:
 v=(I/e['path']).read_bytes();assert len(v)==e['bytes'] and sha(v)==e['sha256'],e['path']
print('Normal I source guard: 0 drift; private-only replay complete',flush=True)
