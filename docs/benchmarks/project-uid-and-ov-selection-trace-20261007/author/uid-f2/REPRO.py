#!/usr/bin/env python3
"""By-value UID factory/store CPU replay; no API/media/native/build/normal writes."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,time,signal,re
F=Path(__file__).resolve().parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration';FE=I/'src/frontend/react'
ap=argparse.ArgumentParser();ap.add_argument('--cpu',type=int,default=38,choices=range(36,40));ap.add_argument('--output',type=Path,required=True);ap.add_argument('--cohort',choices=['both','before','candidate'],default='both');a=ap.parse_args();O=a.output.resolve();assert O.is_relative_to(B/'.codex-tmp') and not O.exists()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if (F/'MANIFEST.json').exists():
 for r in json.loads((F/'MANIFEST.json').read_text())['payloads']:assert sha(F/r['path'])==r['sha256'] and (F/r['path']).stat().st_size==r['bytes']
inputs=json.loads((F/'test-input-bindings.json').read_text())
def guard():
 for r in inputs:assert sha(Path(r['path']))==r['sha256'],'source/dependency drift '+r['path']
guard();O.mkdir();(O/'logs').mkdir();results=[]
prod=['src/frontend/shared/js/cfdproj_state_v4.js','src/frontend/react/src/projectStore.ts','src/frontend/react/src/legacy/cfdprojModules.ts']
existing=['projectStore.test.ts','projectStoreTransient.test.ts','projectFiles.test.ts','newProjectSizing.test.ts','domainCodecRegression.test.ts','ovSceneContentIdentity.test.ts','ovWholeSelection.test.tsx','ovEditBridge.test.ts']
for cohort in (['before','candidate'] if a.cohort=='both' else [a.cohort]):
 root=O/cohort/'src/frontend/react';root.mkdir(parents=True);shutil.copytree(FE/'src',root/'src');shutil.copytree(I/'src/frontend/shared',root.parent/'shared',ignore=shutil.ignore_patterns('__pycache__','.codex-tmp'))
 for rel in prod:
  p=F/cohort/rel;q=O/cohort/rel;shutil.copyfile(p,q);assert sha(p)==sha(q) and not q.is_symlink()
 (root/'node_modules').symlink_to((FE/'node_modules').resolve(),target_is_directory=True);(root/'test').mkdir();tests=['projectUid.test.ts']+(['projectUidCapabilities.test.ts']+existing if cohort=='candidate' else [])
 for n in tests:shutil.copyfile(F/'candidate/src/frontend/react/test'/n,root/'test'/n)
 shutil.copytree(F/'candidate/src/frontend/react/test/fixtures',root/'test/fixtures');shutil.copyfile(F/'candidate/src/frontend/react/tsconfig.json',root/'tsconfig.json')
 # Exact existing transient-test input, copied by value rather than replacing its scientific values.
 case=O/cohort/'case/Shinaide_Liquid/Shinaide_liquid.cfdproj';case.parent.mkdir(parents=True);shutil.copyfile(F/'controlled-fixtures/Shinaide_liquid.cfdproj',case)
 # Existing controlled media mocks identify the normal literal path. The actual private source uses a by-value OvViewport instead.
 for p in (root/'test').glob('*.test.*'):
  text=p.read_text();text=text.replace(str(FE/'src/renderer/OvViewport'),str(root/'src/renderer/OvViewport'));p.write_text(text)
 (root/'tmp').mkdir();(root/'cache').mkdir();config=root/'vite.uid-repro.mts';config.write_text("import {defineConfig} from 'vite'\nexport default defineConfig({root:"+json.dumps(str(root))+",cacheDir:"+json.dumps(str(root/'cache/vite'))+",test:{environment:'jsdom',cache:false,pool:'threads',poolOptions:{threads:{singleThread:true}},maxWorkers:1,minWorkers:1}})\n")
 argv=['taskset','-c',str(a.cpu),'/usr/bin/node',str(root/'node_modules/vitest/vitest.mjs'),'run',*[('test/'+n) for n in tests],'--config',str(config),'--cache=false','--maxWorkers=1','--minWorkers=1'];env=os.environ.copy();env.update(TMPDIR=str(root/'tmp'),XDG_CACHE_HOME=str(root/'cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1');t=time.monotonic();timeout=False
 with (O/'logs'/f'{cohort}.stdout').open('w') as out,(O/'logs'/f'{cohort}.stderr').open('w') as err:
  p=subprocess.Popen(argv,cwd=root,env=env,stdout=out,stderr=err,start_new_session=True)
  try:rc=p.wait(timeout=120)
  except subprocess.TimeoutExpired:
   timeout=True;os.killpg(p.pid,signal.SIGTERM)
   try:rc=p.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait()
 row={'cohort':cohort,'argv':argv,'rc':rc,'timeout':timeout,'elapsedSeconds':time.monotonic()-t};(O/'logs'/f'{cohort}.status.json').write_text(json.dumps(row,indent=2)+'\n');results.append(row);print(json.dumps(row),flush=True);guard();assert not timeout
 text=(O/'logs'/f'{cohort}.stdout').read_text()
 if cohort=='before':assert rc==1 and re.search(r'Tests\s+6 failed\s*\|\s*2 passed',text),'inspect preserved actual product/tooling classification'
 else:assert rc==0 and re.search(r'Tests\s+104 passed',text),'inspect preserved candidate logs'
guard();(O/'RESULT.json').write_text(json.dumps({'results':results,'scope':'Actual factory/store/hash/format source with controlled Three/OV carrier only; no browser or native acceptance'},indent=2)+'\n');print('REPRO_COMPLETE '+str(O),flush=True)
