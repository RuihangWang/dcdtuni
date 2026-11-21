#!/usr/bin/env python3
from pathlib import Path
import os,json,hashlib,shutil,subprocess,time,signal
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');O=Path(__file__).resolve().parent
F=B/'.codex-tmp/project-uid-factory-private-shared-20261007/FROZEN-v2'
S=B/'.codex-tmp/project-uid-factory-private-shared-20261007/fresh-4/candidate'
assert O.is_relative_to(B/'.codex-tmp') and not (O/'cohort').exists()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for row in json.loads((F/'MANIFEST.json').read_text())['payloads']:assert sha(F/row['path'])==row['sha256']
base=O/'cohort';root=base/'src/frontend/react';root.mkdir(parents=True)
shutil.copytree(S/'src/frontend/react/src',root/'src');shutil.copytree(S/'src/frontend/shared',root.parent/'shared')
for rel in ['src/frontend/shared/js/cfdproj_state_v4.js','src/frontend/react/src/projectStore.ts','src/frontend/react/src/legacy/cfdprojModules.ts']:assert sha(base/rel)==sha(F/'candidate'/rel)
(root/'node_modules').symlink_to((S/'src/frontend/react/node_modules').resolve(),target_is_directory=True)
(root/'test').mkdir();shutil.copyfile(O/'projectUidThermalBoundary.test.ts',root/'test/projectUidThermalBoundary.test.ts')
case=base/'case/Shinaide_Liquid/Shinaide_liquid.cfdproj';case.parent.mkdir(parents=True);shutil.copyfile(F/'controlled-fixtures/Shinaide_liquid.cfdproj',case)
(root/'tmp').mkdir();(root/'cache').mkdir();config=root/'vite.audit.mts';config.write_text("import {defineConfig} from 'vite'\nexport default defineConfig({root:"+json.dumps(str(root))+",cacheDir:"+json.dumps(str(root/'cache/vite'))+",test:{environment:'jsdom',cache:false,pool:'threads',poolOptions:{threads:{singleThread:true}},maxWorkers:1,minWorkers:1}})\n")
argv=['taskset','-c','38','/usr/bin/node',str(root/'node_modules/vitest/vitest.mjs'),'run','test/projectUidThermalBoundary.test.ts','--config',str(config),'--cache=false','--maxWorkers=1','--minWorkers=1']
env=os.environ.copy();env.update(TMPDIR=str(root/'tmp'),XDG_CACHE_HOME=str(root/'cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
t=time.monotonic();timeout=False
with (O/'logs/stdout').open('w') as out,(O/'logs/stderr').open('w') as err:
 p=subprocess.Popen(argv,cwd=root,env=env,stdout=out,stderr=err,start_new_session=True)
 try:rc=p.wait(timeout=120)
 except subprocess.TimeoutExpired:
  timeout=True;os.killpg(p.pid,signal.SIGTERM)
  try:rc=p.wait(timeout=2)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait()
row={'rc':rc,'timeout':timeout,'elapsedSeconds':time.monotonic()-t,'argv':argv,'scope':'Exact frozen F2 by-value store/materializer/exporter only; controlled empty-UID canonical input. No solver or browser.'};(O/'logs/status.json').write_text(json.dumps(row,indent=2)+'\n');print(json.dumps(row))
for r in json.loads((F/'MANIFEST.json').read_text())['payloads']:assert sha(F/r['path'])==r['sha256']
assert rc==0 and not timeout
