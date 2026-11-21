from pathlib import Path
import os,json,subprocess,time,signal,hashlib
P=Path(__file__).resolve().parent;O=P.parent;D=O.parent.parent;C=P/'actual-export-audit';C.mkdir()
T=O/'toolchain/node-v24.12.0-linux-x64';FE=P/'reviewtree/src/frontend/react'
assert subprocess.check_output(['git','-C',str(D),'rev-parse','HEAD']).decode().strip()=='2b6f666eaa19f7d4812fac688f1cf4a36fe6e1c8'
assert not subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1'])
os.sched_setaffinity(0,{34,35});env=os.environ.copy();env.update({'PATH':str(T/'bin')+':'+env['PATH'],'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2','NUMEXPR_NUM_THREADS':'2','TMPDIR':str(C/'tmp'),'TMP':str(C/'tmp'),'TEMP':str(C/'tmp'),'XDG_CACHE_HOME':str(C/'cache'),'DCDTUNI_CARRIER_DIAGNOSTICS':'closed-edge-v1','CARRIER_DIAG_RECEIPT':str(C/'actual-receipt.json'),'CARRIER_DIAG_STL':str(C/'merged_liquid_transport.stl'),'NO_COLOR':'1','CI':'1'})
for n in ['tmp','cache','logs']:(C/n).mkdir(exist_ok=True)
argv=['/usr/bin/time','-v','-o',str(C/'logs/time.txt'),str(T/'bin/node'),str(FE/'node_modules/vitest/vitest.mjs'),'run','test/carrierActualExportAudit.test.ts','--maxWorkers=1','--minWorkers=1','--reporter=default']
meta={'argv':argv,'cwd':str(FE),'timeoutSeconds':60,'affinity':sorted(os.sched_getaffinity(0)),'exportCallsPlanned':1,'onlyOldFixture':True,'notFullSuite':True,'environment':{k:env[k] for k in ['CUDA_VISIBLE_DEVICES','TMPDIR','DCDTUNI_CARRIER_DIAGNOSTICS','CARRIER_DIAG_RECEIPT']}}
(C/'argv.json').write_text(json.dumps(meta,indent=2)+'\n');start=time.monotonic();timedout=False
with (C/'logs/stdout').open('w') as out,(C/'logs/stderr').open('w') as err:
 p=subprocess.Popen(argv,cwd=FE,env=env,stdout=out,stderr=err,start_new_session=True)
 (C/'process.json').write_text(json.dumps({'ownedPid':p.pid,'pgid':os.getpgid(p.pid),'affinity':sorted(os.sched_getaffinity(p.pid))},indent=2)+'\n')
 try:rc=p.wait(timeout=60)
 except subprocess.TimeoutExpired:
  timedout=True;os.killpg(p.pid,signal.SIGTERM)
  try:rc=p.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait(timeout=5)
meta.update({'rc':rc,'timedOut':timedout,'elapsedSeconds':time.monotonic()-start,'cloneStatusAfter':subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1']).decode()})
(C/'result.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))
