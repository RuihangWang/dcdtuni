from pathlib import Path
import os,json,subprocess,time,hashlib,argparse
p=argparse.ArgumentParser();p.add_argument('--phase',required=True);p.add_argument('--out-name',required=True);a=p.parse_args()
T=Path(__file__).resolve().parent;R=T/'candidate/src/frontend/react';rows=json.loads((T/'source-before.json').read_text());O=T/'logs'/a.out_name;O.mkdir(parents=True)
if a.phase=='before':
 for row in rows:(T/'candidate'/row['target']).write_bytes((T/'before'/row['target']).read_bytes())
env=os.environ.copy();env.update({'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TMPDIR':str(O/'tmp'),'XDG_CACHE_HOME':str(O/'cache'),'LRM_EVIDENCE_DIR':str(O)})
for key in ['TMPDIR','XDG_CACHE_HOME']:Path(env[key]).mkdir()
if a.phase=='types':argv=['/usr/bin/node',str(R/'node_modules/typescript/bin/tsc'),'--noEmit']
else:
 files=[str(q.relative_to(R)) for q in sorted((R/'test').glob('*.test.*'))] if a.phase=='focused' else ['test/LiquidRuntimeMonitor.failure.test.tsx']
 argv=['/usr/bin/node',str(R/'node_modules/vitest/vitest.mjs'),'run','--config',str(R/'vite.private.ts'),'--cache=false','--maxWorkers=1','--minWorkers=1',*files]
started=time.monotonic()
try:
 value=subprocess.run(argv,cwd=R,env=env,capture_output=True,text=True,timeout=120);(O/'stdout.log').write_text(value.stdout);(O/'stderr.log').write_text(value.stderr);record={'argv':argv,'cwd':str(R),'phase':a.phase,'rc':value.returncode,'elapsedSec':time.monotonic()-started,'testSha256':hashlib.sha256((R/'test/LiquidRuntimeMonitor.failure.test.tsx').read_bytes()).hexdigest()};(O/'run.json').write_text(json.dumps(record,indent=2));print(json.dumps(record));print(value.stdout[-2000:]);print(value.stderr[:250])
finally:
 if a.phase=='before':
  for row in rows:(T/'candidate'/row['target']).write_bytes((T/'after'/row['target']).read_bytes())
