from pathlib import Path
import json,os,subprocess,time,signal,hashlib
R=Path(__file__).resolve().parent; a=json.loads((R/'authorization.json').read_text()); I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration'); B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni'); FE=I/'src/frontend/react'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def guard():
 d={**a['sourceBefore'],**{r['target']:r['afterSha256'] for r in a['targets']}}
 assert all(sha(I/r)==h for r,h in d.items()); assert all(sha(B/r)==h for r,h in a['mainBefore'].items())
guard(); env=os.environ.copy(); env.update(TMPDIR=str(R/'tmp'),XDG_CACHE_HOME=str(R/'cache'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1'); steps=[]
suites=['ovSceneContentIdentity.test.ts','RendererControl.test.ts','ovEditBridge.test.ts','ovWholeSelection.test.tsx','cameraReadbackMounted.test.ts','closedCabinetDisplayRevision.test.ts']
commands=[('frontend52',['taskset','-c','36','/usr/bin/node',str(FE/'node_modules/vitest/vitest.mjs'),'run',*[('test/'+p) for p in suites],'--config',str(R/'vite.normal.config.ts'),'--cache=false','--maxWorkers=1','--minWorkers=1'],120),('types',['taskset','-c','37','/usr/bin/node',str(FE/'node_modules/typescript/bin/tsc'),'--noEmit','--project',str(FE/'tsconfig.json')],120),('production-build',['taskset','-c','36','/usr/bin/node',str(FE/'node_modules/vite/bin/vite.js'),'build','--config',str(R/'vite.normal.config.ts')],180)]
for label,argv,budget in commands:
 t=time.monotonic();timeout=False
 with (R/'logs'/f'{label}.stdout').open('w') as out,(R/'logs'/f'{label}.stderr').open('w') as err:
  p=subprocess.Popen(argv,cwd=FE,env=env,stdout=out,stderr=err,start_new_session=True)
  try:rc=p.wait(timeout=budget)
  except subprocess.TimeoutExpired:
   timeout=True;os.killpg(p.pid,signal.SIGTERM)
   try:rc=p.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait()
 row={'label':label,'argv':argv,'rc':rc,'timeout':timeout,'seconds':time.monotonic()-t};steps.append(row);guard();(R/'validation.json').write_text(json.dumps({'steps':steps,'sourceGuardHeld':True,'Main29Held':True},indent=2)+'\n');print(json.dumps(row),flush=True);assert rc==0 and not timeout
 if label=='frontend52':assert '52 passed' in (R/'logs/frontend52.stdout').read_text()
print('VALIDATION_COMPLETE',flush=True)
