from pathlib import Path
from datetime import datetime,timezone
import subprocess,json,os,time,signal,hashlib
O=Path(__file__).resolve().parent
D=O.parent.parent
W=O/'reviewtree';T=O/'toolchain/node-v24.12.0-linux-x64';FE=W/'src/frontend/react'
expected='2b6f666eaa19f7d4812fac688f1cf4a36fe6e1c8'
assert subprocess.check_output(['git','-C',str(D),'rev-parse','HEAD']).decode().strip()==expected
assert not subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1'])
os.sched_setaffinity(0,{34,35})
env=os.environ.copy();env.update({'PATH':str(T/'bin')+':/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin:'+env.get('PATH',''),'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2','NUMEXPR_NUM_THREADS':'2','TMPDIR':str(O/'tmp'),'TMP':str(O/'tmp'),'TEMP':str(O/'tmp'),'npm_config_cache':str(O/'npm-cache'),'XDG_CACHE_HOME':str(O/'xdg-cache'),'XDG_CONFIG_HOME':str(O/'xdg-config'),'XDG_DATA_HOME':str(O/'xdg-data'),'NODE_OPTIONS':'--max-old-space-size=4096','NO_COLOR':'1','TERM':'dumb','CI':'1'})
for n in ['xdg-config','xdg-data']:(O/n).mkdir(exist_ok=True)
argv=['/usr/bin/time','-v','-o',str(O/'logs/vitest.time.txt'),str(T/'bin/node'),str(FE/'node_modules/vitest/vitest.mjs'),'run','--maxWorkers=2','--minWorkers=2','--reporter=default','--reporter=json','--outputFile='+str(O/'logs/vitest.result.json')]
meta={'argv':argv,'cwd':str(FE),'publicHead':expected,'startUtc':datetime.now(timezone.utc).isoformat(),'testTimeoutSeconds':900,'meshBudgetNotChanged':True,'fileFilter':None,'testsMocksSkipsModified':False,'affinity':sorted(os.sched_getaffinity(0)),'environment':{k:env[k] for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','TMPDIR','npm_config_cache','XDG_CACHE_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','NODE_OPTIONS','CI','NO_COLOR']},'existingCloneModulesModified':False}
(O/'VITEST-ARGV.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n')
start=time.monotonic();timeout=False
with (O/'logs/vitest.stdout').open('w') as out,(O/'logs/vitest.stderr').open('w') as err:
 p=subprocess.Popen(argv,cwd=FE,env=env,stdout=out,stderr=err,start_new_session=True)
 (O/'VITEST-PROCESS.json').write_text(json.dumps({'ownedHeadPid':p.pid,'pgid':os.getpgid(p.pid),'affinity':sorted(os.sched_getaffinity(p.pid)),'spawnUtc':meta['startUtc']},indent=2)+'\n')
 print(json.dumps({'phase':'vitest-started','pid':p.pid,'timeout':900,'source':expected}),flush=True)
 try:rc=p.wait(timeout=900)
 except subprocess.TimeoutExpired:
  timeout=True;os.killpg(p.pid,signal.SIGTERM)
  try:rc=p.wait(timeout=10)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait(timeout=10)
meta.update({'rc':rc,'timedOut':timeout,'elapsedSeconds':time.monotonic()-start,'finishUtc':datetime.now(timezone.utc).isoformat(),'buildExecuted':False})
if (O/'logs/vitest.result.json').exists():
 result=json.loads((O/'logs/vitest.result.json').read_text());meta['counters']={k:result[k] for k in ['numTotalTestSuites','numPassedTestSuites','numFailedTestSuites','numPendingTestSuites','numTotalTests','numPassedTests','numFailedTests','numPendingTests','numTodoTests','success'] if k in result}
meta['headAfter']=subprocess.check_output(['git','-C',str(D),'rev-parse','HEAD']).decode().strip();meta['statusAfter']=subprocess.check_output(['git','-C',str(D),'status','--porcelain=v1']).decode();meta['stashAfter']=subprocess.check_output(['git','-C',str(D),'stash','list','--format=%gd %H %s']).decode()
(O/'VITEST-RESULT.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n');print(json.dumps({'phase':'vitest-finished','rc':rc,'timeout':timeout,'elapsed':meta['elapsedSeconds'],'counters':meta.get('counters',{})}),flush=True)
