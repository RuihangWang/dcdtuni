from pathlib import Path
import os,json,subprocess,time,signal
P=Path(__file__).resolve().parent;O=P.parent;D=O.parent.parent;T=O/'toolchain/node-v24.12.0-linux-x64';FE=P/'reviewtree/src/frontend/react';L=P/'focused-proposed6';L.mkdir();(L/'tmp').mkdir()
assert not subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1']);os.sched_setaffinity(0,{34,35})
env=os.environ.copy();env.update({'PATH':str(T/'bin')+':'+env['PATH'],'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2','NUMEXPR_NUM_THREADS':'2','TMPDIR':str(L/'tmp'),'TMP':str(L/'tmp'),'TEMP':str(L/'tmp'),'XDG_CACHE_HOME':str(L/'tmp'),'NO_COLOR':'1','CI':'1','HYBRID_COMPILER_OUTPUT_DIR':str(L/'actual-output')})
selector=json.loads((P/'FOCUSED-TEST-IDENTITIES.json').read_text())['filter'];argv=['/usr/bin/time','-v','-o',str(L/'time.txt'),str(T/'bin/node'),str(FE/'node_modules/vitest/vitest.mjs'),'run','test/shinaideHybridCompiler.test.ts','-t',selector,'--maxWorkers=1','--minWorkers=1','--reporter=default','--reporter=json','--outputFile='+str(L/'vitest.json')]
meta={'argv':argv,'cwd':str(FE),'timeoutSeconds':120,'affinity':sorted(os.sched_getaffinity(0)),'publicTestUnmodified':False,'testUpdates':'required legacy12+transport identity+literal closure','selectedTests':6,'fullSuite':False,'environment':{k:env[k] for k in ['CUDA_VISIBLE_DEVICES','TMPDIR','HYBRID_COMPILER_OUTPUT_DIR']}};(L/'argv.json').write_text(json.dumps(meta,indent=2)+'\n');start=time.monotonic();timeout=False
with (L/'stdout').open('w') as out,(L/'stderr').open('w') as err:
 p=subprocess.Popen(argv,cwd=FE,env=env,stdout=out,stderr=err,start_new_session=True)
 try:rc=p.wait(timeout=120)
 except subprocess.TimeoutExpired:
  timeout=True;os.killpg(p.pid,signal.SIGTERM)
  try:rc=p.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait(timeout=5)
meta.update({'rc':rc,'timedOut':timeout,'elapsedSeconds':time.monotonic()-start,'cloneStatusAfter':subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1']).decode()});(L/'result.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))
