#!/usr/bin/env python3
"""Focused real-store/controller CPU replay. No service/API/browser/native/build."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,time,re,signal
F=Path(__file__).resolve().parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration';FE=I/'src/frontend/react'
ap=argparse.ArgumentParser();ap.add_argument('--cpu',type=int,default=36,choices=range(36,40));ap.add_argument('--output',type=Path);ap.add_argument('--cohort',choices=['both','before','candidate'],default='both');a=ap.parse_args()
O=(a.output or B/'.codex-tmp'/('ov-scene-content-identity-repro-'+str(time.time_ns()))).resolve();assert O.is_relative_to(B/'.codex-tmp') and not O.exists()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if (F/'MANIFEST.json').exists():
 for r in json.loads((F/'MANIFEST.json').read_text())['payloads']:assert sha(F/r['path'])==r['sha256'] and (F/r['path']).stat().st_size==r['bytes']
bindings=json.loads((F/'test-input-bindings.json').read_text())
def guard():
 for r in bindings:assert sha(Path(r['path']))==r['sha256'],'readonly source drift '+r['path']
guard();O.mkdir();(O/'logs').mkdir();results=[]
production=['src/renderer/RendererControl.tsx','src/renderer/ovEditBridge.ts','src/liquid/closedCabinetDisplay.ts']
existing=['RendererControl.test.ts','ovEditBridge.test.ts','ovWholeSelection.test.tsx','cameraReadbackMounted.test.ts','closedCabinetDisplayRevision.test.ts']
for cohort in (['before','candidate'] if a.cohort=='both' else [a.cohort]):
 root=O/cohort/'src/frontend/react';src=root/'src';src.mkdir(parents=True)
 for p in (FE/'src').iterdir():
  if p.name not in ['renderer','liquid']:(src/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for directory in ['renderer','liquid']:
  q=src/directory;q.mkdir()
  for p in (FE/'src'/directory).iterdir():
   if 'src/'+directory+'/'+p.name not in production:(q/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for rel in production:
  material=F/cohort/'src/frontend/react'/rel;target=root/rel;shutil.copyfile(material,target);assert not target.is_symlink() and sha(target)==sha(material)
 (root/'node_modules').symlink_to((FE/'node_modules').resolve(),target_is_directory=True);(root.parent/'shared').symlink_to(I/'src/frontend/shared',target_is_directory=True)
 (root/'test').mkdir()
 tests=['ovSceneContentIdentity.test.ts']+(existing if cohort=='candidate' else [])
 for name in tests:shutil.copyfile(F/'candidate/src/frontend/react/test'/name,root/'test'/name)
 shutil.copyfile(F/'candidate/src/frontend/react/tsconfig.json',root/'tsconfig.json');(root/'tmp').mkdir();(root/'cache').mkdir()
 config=root/'vite.repro.config.mts';config.write_text("import {defineConfig} from 'vite'\nexport default defineConfig({root:"+json.dumps(str(root))+",cacheDir:"+json.dumps(str(root/'cache/vite'))+",test:{environment:'jsdom',cache:false,pool:'threads',poolOptions:{threads:{singleThread:true}},maxWorkers:1,minWorkers:1}})\n")
 argv=['taskset','-c',str(a.cpu),'/usr/bin/node',str(root/'node_modules/vitest/vitest.mjs'),'run',*[('test/'+n) for n in tests],'--config',str(config),'--cache=false','--maxWorkers=1','--minWorkers=1']
 env=os.environ.copy();env.update(TMPDIR=str(root/'tmp'),XDG_CACHE_HOME=str(root/'cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1');t=time.monotonic();timed=False
 with (O/'logs'/f'{cohort}.stdout').open('w') as out,(O/'logs'/f'{cohort}.stderr').open('w') as err:
  p=subprocess.Popen(argv,cwd=root,env=env,stdout=out,stderr=err,start_new_session=True)
  try:rc=p.wait(timeout=120)
  except subprocess.TimeoutExpired:
   timed=True;os.killpg(p.pid,signal.SIGTERM)
   try:rc=p.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait()
 row={'cohort':cohort,'argv':argv,'rc':rc,'timeout':timed,'elapsedSeconds':time.monotonic()-t};(O/'logs'/f'{cohort}.status.json').write_text(json.dumps(row,indent=2)+'\n');results.append(row);print(json.dumps(row),flush=True)
 guard();assert not timed
 text=(O/'logs'/f'{cohort}.stdout').read_text()
 if cohort=='before':assert rc==1 and re.search(r'Tests\s+6 failed\s*\|\s*5 passed',text),'inspect preserved before logs; no fabricated RED count'
 else:assert rc==0 and re.search(r'Tests\s+52 passed',text),'inspect preserved candidate logs'
 guard()
(O/'RESULT.json').write_text(json.dumps({'results':results,'scope':'Controlled media only; actual source/store/controller/bridge; no native selection ingress or pixel acceptance'},indent=2)+'\n');print('REPRO_COMPLETE '+str(O),flush=True)
