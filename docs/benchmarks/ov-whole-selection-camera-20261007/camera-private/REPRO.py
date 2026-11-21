#!/usr/bin/env python3
"""Source-bound camera fence unit replay; no build, browser, Kit or normal writes."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,time,re,signal
F=Path(__file__).resolve().parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration';FE=I/'src/frontend/react'
ap=argparse.ArgumentParser();ap.add_argument('--cpu',type=int,default=36,choices=range(36,40));ap.add_argument('--output',type=Path);a=ap.parse_args()
O=(a.output or B/'.codex-tmp'/('ov-common-camera-owner-repro-'+str(time.time_ns()))).resolve();assert O.parent==B/'.codex-tmp' and O.name.startswith('ov-common-camera-owner-repro-') and not O.exists()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for r in json.loads((F/'MANIFEST.json').read_text())['payloads']:assert sha(F/r['path'])==r['sha256'] and (F/r['path']).stat().st_size==r['bytes']
for r in json.loads((F/'test-input-bindings.json').read_text()):assert sha(Path(r['path']))==r['sha256'],'readonly dependency drift '+r['path']
O.mkdir();(O/'logs').mkdir();results=[]
for cohort in ['before','candidate']:
 root=O/cohort/'src/frontend/react';src=root/'src';src.mkdir(parents=True)
 for p in (FE/'src').iterdir():
  if p.name!='renderer':(src/p.name).symlink_to(p,target_is_directory=p.is_dir())
 renderer=src/'renderer';renderer.mkdir()
 for p in (FE/'src/renderer').iterdir():
  if p.name!='RendererControl.tsx':(renderer/p.name).symlink_to(p,target_is_directory=p.is_dir())
 material=F/('source' if cohort=='before' else 'candidate')/'src/frontend/react/src/renderer/RendererControl.tsx'
 shutil.copyfile(material,renderer/'RendererControl.tsx');assert not (renderer/'RendererControl.tsx').is_symlink() and sha(renderer/'RendererControl.tsx')==sha(material)
 (root/'node_modules').symlink_to((FE/'node_modules').resolve(),target_is_directory=True)
 (root.parent/'shared').symlink_to(I/'src/frontend/shared',target_is_directory=True)
 (root/'test').mkdir()
 for p in (F/'candidate/src/frontend/react/test').iterdir():shutil.copyfile(p,root/'test'/p.name)
 shutil.copyfile(F/'candidate/src/frontend/react/tsconfig.json',root/'tsconfig.json')
 (root/'tmp').mkdir();(root/'cache').mkdir()
 config=root/'vite.repro.config.mts';config.write_text("import {defineConfig} from 'vite'\nexport default defineConfig({root:"+json.dumps(str(root))+",cacheDir:"+json.dumps(str(root/'cache/vite'))+",test:{environment:'jsdom',cache:false,pool:'threads',poolOptions:{threads:{singleThread:true}},maxWorkers:1,minWorkers:1}})\n")
 argv=['taskset','-c',str(a.cpu),'/usr/bin/node',str(root/'node_modules/vitest/vitest.mjs'),'run','test/cameraReadbackScope.test.ts','test/cameraReadbackMounted.test.ts','test/RendererControl.test.ts','--config',str(config),'--cache=false','--maxWorkers=1','--minWorkers=1']
 env=os.environ.copy();env.update(TMPDIR=str(root/'tmp'),XDG_CACHE_HOME=str(root/'cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1');t=time.monotonic();timed=False
 with (O/'logs'/f'{cohort}.stdout').open('w') as out,(O/'logs'/f'{cohort}.stderr').open('w') as err:
  p=subprocess.Popen(argv,cwd=root,env=env,stdout=out,stderr=err,start_new_session=True)
  try:rc=p.wait(timeout=120)
  except subprocess.TimeoutExpired:
   timed=True;os.killpg(p.pid,signal.SIGTERM)
   try:rc=p.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait()
 expected=1 if cohort=='before' else 0;row={'cohort':cohort,'argv':argv,'rc':rc,'expectedRc':expected,'timeout':timed,'elapsedSeconds':time.monotonic()-t};(O/'logs'/f'{cohort}.status.json').write_text(json.dumps(row,indent=2)+'\n');results.append(row);print(json.dumps(row),flush=True)
 assert not timed and rc==expected
 summary=r'Tests\s+7 failed\s*\|\s*20 passed' if cohort=='before' else r'Tests\s+27 passed'
 assert re.search(summary,(O/'logs'/f'{cohort}.stdout').read_text()),'unexpected test summary; preserve raw and review'
for r in json.loads((F/'test-input-bindings.json').read_text()):assert sha(Path(r['path']))==r['sha256'],'dependency changed during replay'
(O/'RESULT.json').write_text(json.dumps({'results':results,'scope':'CPU source contract/mounted controlled transport only; no real media/camera source attribution'},indent=2)+'\n');print('REPRO_COMPLETE '+str(O),flush=True)
