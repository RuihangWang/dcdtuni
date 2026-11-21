#!/usr/bin/env python3
"""Fresh owned CPU reproduction; frozen payload and normal repo stay read-only."""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,subprocess,time
P=Path(__file__).resolve().parent
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
I=B/'.codex-tmp/goal-20261005/integration'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
a=argparse.ArgumentParser();a.add_argument('--out',required=True);a.add_argument('--cpu',type=int,default=36);a.add_argument('--types',action='store_true');a=a.parse_args()
if a.cpu not in (30,31,36,37,38):raise SystemExit('Choose an independently owned permitted CPU 30/31/36/37/38')
O=Path(a.out).resolve()
if B/'.codex-tmp' not in O.parents or I==O or I in O.parents or P==O or P in O.parents:raise SystemExit('Fresh output must be in a separate B/.codex-tmp directory')
if O.exists():raise SystemExit('Output already exists; use a fresh name')
manifest=json.loads((P/'MANIFEST.json').read_text())
for row in manifest['files']:
 q=P/row['path']
 if not q.is_file() or q.stat().st_size!=row['bytes'] or digest(q)!=row['sha256']:raise SystemExit('Frozen payload drift: '+row['path'])
deps=json.loads((P/'dependencies.json').read_text())
for row in deps['entries']:
 q=Path(row['path'])
 if not q.is_file() or digest(q)!=row['sha256']:raise SystemExit('Installed dependency drift: '+row['path'])
O.mkdir();C=O/'candidate';shutil.copytree(P/'candidate',C)
for q in C.rglob('*'):
 if q.is_file():q.chmod(0o644)
R=C/'src/frontend/react';(R/'node_modules').symlink_to(deps['nodeModules'],target_is_directory=True)
rows=json.loads((P/'apply-map.json').read_text())['targets'];prod=[r for r in rows if r['kind']=='production'];steps=[]
# Fingerprint current normal inputs read-only; source movement is recorded,
# never overwritten or used to replace the immutable author inputs.
normalBefore={r['target']:(digest(Path(r['normalSourcePath'])) if Path(r['normalSourcePath']).exists() else None) for r in rows}
os.sched_setaffinity(0,{a.cpu})
for phase in ['before','after-focused']+(['types'] if a.types else []):
 E=O/phase;E.mkdir()
 for row in prod:
  q=C/row['target'];q.write_bytes((P/'before'/row['target']).read_bytes() if phase=='before' else (P/'candidate'/row['target']).read_bytes())
 env=os.environ.copy();env.update({'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TMPDIR':str(E/'tmp'),'XDG_CACHE_HOME':str(E/'cache'),'LRM_EVIDENCE_DIR':str(E),'FORCE_COLOR':'0'})
 for key in ['TMPDIR','XDG_CACHE_HOME']:Path(env[key]).mkdir()
 if phase=='types':argv=['/usr/bin/node',str(R/'node_modules/typescript/bin/tsc'),'--noEmit']
 else:
  files=['test/LiquidRuntimeMonitor.failure.test.tsx'] if phase=='before' else [str(q.relative_to(R)) for q in sorted((R/'test').glob('*.test.*'))]
  argv=['/usr/bin/node',str(R/'node_modules/vitest/vitest.mjs'),'run','--config',str(R/'vite.private.ts'),'--cache=false','--maxWorkers=1','--minWorkers=1',*files]
 started=time.monotonic();run=subprocess.run(argv,cwd=R,env=env,capture_output=True,text=True,timeout=120)
 (E/'stdout.log').write_text(run.stdout);(E/'stderr.log').write_text(run.stderr)
 expected=1 if phase=='before' else 0
 okay=run.returncode==expected
 plain=re.sub(chr(27)+r'\[[0-?]*[ -/]*[@-~]', '', run.stdout)
 if phase=='before':okay=okay and bool(re.search(r'Tests\s+9 failed \| 2 passed \(11\)',plain))
 if phase!='types':
  actual=json.loads((E/'actual-import-source.json').read_text())
  for row in prod:
   path=str((C/row['target']).resolve());want=row['beforeSha256'] if phase=='before' else row['afterSha256']
   okay=okay and actual.get(path)==want
 rec={'phase':phase,'argv':argv,'cwd':str(R),'rc':run.returncode,'expectedRc':expected,'expected':okay,'elapsedSec':time.monotonic()-started,'cpu':a.cpu,'testSha256':digest(R/'test/LiquidRuntimeMonitor.failure.test.tsx')}
 (E/'run.json').write_text(json.dumps(rec,indent=2));steps.append(rec)
 if not okay:break
normalAfter={r['target']:(digest(Path(r['normalSourcePath'])) if Path(r['normalSourcePath']).exists() else None) for r in rows}
receipt={'frozenManifestSha256':digest(P/'MANIFEST.json'),'steps':steps,'normalTargetsHeld':normalBefore==normalAfter,'normalSnapshot':normalAfter,'scope':'controlled HTTP/client/mounted UI only; no normal mutation/API/browser/native/solver/Kit/GPU/build/Git','passed':all(r['expected'] for r in steps) and normalBefore==normalAfter}
(O/'RESULTS.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2));raise SystemExit(0 if receipt['passed'] else 1)
