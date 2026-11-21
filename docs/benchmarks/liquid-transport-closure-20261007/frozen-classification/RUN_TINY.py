from pathlib import Path
import os,json,subprocess,time,signal,sys
L=Path(__file__).resolve().parent;O=L.parent;D=O.parent.parent;T=O/'toolchain/node-v24.12.0-linux-x64'
kind=sys.argv[1];assert kind in ['baseline','candidate'];FE=L/('baseline-reviewtree' if kind=='baseline' else 'reviewtree')/'src/frontend/react'
assert subprocess.check_output(['git','-C',str(D),'rev-parse','HEAD']).decode().strip()=='2b6f666eaa19f7d4812fac688f1cf4a36fe6e1c8';assert not subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1'])
os.sched_setaffinity(0,{34,35});outdir=L/('tiny-'+kind);outdir.mkdir();(outdir/'tmp').mkdir();env=os.environ.copy();env.update({'PATH':str(T/'bin')+':'+env['PATH'],'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2','NUMEXPR_NUM_THREADS':'2','TMPDIR':str(outdir/'tmp'),'TMP':str(outdir/'tmp'),'TEMP':str(outdir/'tmp'),'XDG_CACHE_HOME':str(outdir/'tmp'),'NO_COLOR':'1','CI':'1'})
argv=['/usr/bin/time','-v','-o',str(outdir/'time.txt'),str(T/'bin/node'),str(FE/'node_modules/vitest/vitest.mjs'),'run','test/transportCsgPrecision.test.ts','--maxWorkers=1','--minWorkers=1','--reporter=default']
meta={'argv':argv,'cwd':str(FE),'timeoutSeconds':30,'affinity':sorted(os.sched_getaffinity(0)),'testRole':'real Polygon/Plane/CSG control; no whole project export','kind':kind};(outdir/'argv.json').write_text(json.dumps(meta,indent=2)+'\n');start=time.monotonic();timeout=False
with (outdir/'stdout').open('w') as out,(outdir/'stderr').open('w') as err:
 p=subprocess.Popen(argv,cwd=FE,env=env,stdout=out,stderr=err,start_new_session=True)
 try:rc=p.wait(timeout=30)
 except subprocess.TimeoutExpired:
  timeout=True;os.killpg(p.pid,signal.SIGTERM)
  try:rc=p.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait(timeout=5)
meta.update({'rc':rc,'timedOut':timeout,'elapsedSeconds':time.monotonic()-start});(outdir/'result.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))
