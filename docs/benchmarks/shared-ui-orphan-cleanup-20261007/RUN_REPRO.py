import sys;sys.dont_write_bytecode=True
import argparse,json,os,signal,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--node-bin',required=True);p.add_argument('--python',required=True);p.add_argument('--build',action='store_true');a=p.parse_args()
R=Path(a.repo).resolve();assert (R/'src/frontend/react/package.json').is_file()
# Only generated replay files/caches are created in the chosen checkout.
O=R/'.codex-tmp/orphan-shared-ui-replay'/str(time.time_ns());O.mkdir(parents=True);os.sched_setaffinity(0,{36,37})
env=os.environ.copy();env.update(PATH=str(Path(a.node_bin).resolve())+':'+env.get('PATH',''),CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(R/'src')+':'+str(R/'src/backend'),TMPDIR=str(O/'tmp'),XDG_CACHE_HOME=str(O/'xdg'),npm_config_cache=str(O/'npm'));(O/'tmp').mkdir()
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:env[key]='2'
tests=['test/orphanModules.test.ts','test/ovLiquid.test.tsx','test/sharedOvPostprocessAdapter.test.ts','test/sharedDisplayUiOwnership.test.tsx','test/airSurfaceFragment.test.ts','test/commonRenderIntent.test.ts','test/sharedPostprocessDriver.test.ts','test/ppColormaps.test.ts','test/constantCommonLut.test.tsx','test/liquidTempColor.test.ts','test/liquidVisualRevision.test.ts','test/ppCaseBinding.test.tsx','test/liquidRunFollow.test.tsx','test/resultPlayback.test.ts','test/liquidRuntimeClient.test.ts','test/components/PostprocessTimeSelection.test.tsx','test/components/PostprocessPlayhead.test.tsx','test/liquidPanelPlayhead.test.tsx']
commands=[('frontend',['npm','test','--',*tests,'--maxWorkers=2','--minWorkers=1'],R/'src/frontend/react'),('backend-grant',[str(Path(a.python).resolve()),'-B','-m','pytest','-q','src/backend/tests/test_canonical_render_grant.py','--basetemp='+str(O/'pytest-temp')],R)]
if a.build:commands.append(('production-build',['npm','run','build'],R/'src/frontend/react'))
receipts=[]
for name,argv,cwd in commands:
 log=O/(name+'.log');start=time.time()
 with log.open('wb')as out:
  child=subprocess.Popen(argv,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
  try:rc=child.wait(timeout=300)
  except subprocess.TimeoutExpired:
   os.killpg(child.pid,signal.SIGTERM)
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
   rc=124
 receipts.append({'name':name,'argv':argv,'returncode':rc,'elapsed_seconds':time.time()-start,'log':str(log)})
 print(json.dumps(receipts[-1]),flush=True)
 if rc:break
(O/'receipts.json').write_text(json.dumps(receipts,indent=2)+'\n');sys.exit(receipts[-1]['returncode'])
