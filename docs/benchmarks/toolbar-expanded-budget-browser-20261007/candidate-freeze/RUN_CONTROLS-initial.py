from pathlib import Path
import os,json,subprocess,time,signal,sys
P=Path(__file__).resolve().parent;O=P.parent;mode=sys.argv[1];assert mode in ['baseline','candidate','final'];L=P/mode;L.mkdir();(L/'tmp').mkdir();FE=P/('baseline-tree' if mode=='baseline' else 'reviewtree')/'src/frontend/react';T=O/'toolchain/node-v24.12.0-linux-x64';os.sched_setaffinity(0,{34,35});env=os.environ.copy();env.update({'PATH':str(T/'bin')+':'+env['PATH'],'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2','NUMEXPR_NUM_THREADS':'2','TMPDIR':str(L/'tmp'),'TMP':str(L/'tmp'),'TEMP':str(L/'tmp'),'XDG_CACHE_HOME':str(L/'tmp'),'NO_COLOR':'1','CI':'1'})
files=['test/toolbarExpandedBudget.test.tsx'] if mode=='baseline' else ['test/toolbarExpandedBudget.test.tsx','test/layoutMetrics.test.ts','test/toolbarPinnedControls.test.tsx','test/toolbarRemeasure.test.tsx','test/responsiveToolbarFrame.test.tsx','test/responsiveToolbarActions.test.tsx','test/panelResizerKeyboard.test.tsx']
argv=['/usr/bin/time','-v','-o',str(L/'time.txt'),str(T/'bin/node'),str(FE/'node_modules/vitest/vitest.mjs'),'run',*files,'--config','vite.private.config.ts','--maxWorkers=1','--minWorkers=1','--reporter=default','--reporter=json','--outputFile='+str(L/'vitest.json')];meta={'mode':mode,'argv':argv,'cwd':str(FE),'allowedCpus':sorted(os.sched_getaffinity(0)),'timeoutSeconds':120,'fullSuite':False,'controlledDomSizes':'actual Root measurement input; not CSS pixels','environment':{k:env[k] for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','TMPDIR']}};(L/'argv.json').write_text(json.dumps(meta,indent=2)+'\n');t=time.monotonic();timeout=False
with (L/'stdout').open('w') as out,(L/'stderr').open('w') as err:
 p=subprocess.Popen(argv,cwd=FE,env=env,stdout=out,stderr=err,start_new_session=True)
 try:rc=p.wait(timeout=120)
 except subprocess.TimeoutExpired:
  timeout=True;os.killpg(p.pid,signal.SIGTERM)
  try:rc=p.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);rc=p.wait(timeout=5)
meta.update({'rc':rc,'timedOut':timeout,'elapsedS':time.monotonic()-t});(L/'result.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))
if (L/'vitest.json').exists():
 j=json.loads((L/'vitest.json').read_text());print(json.dumps({k:j.get(k) for k in ['numTotalTests','numPassedTests','numFailedTests','numPendingTests']}))
 for suite in j.get('testResults',[]):
  for a in suite.get('assertionResults',[]):
   if a['status']=='failed':print('FAIL',a['fullName'],'\n'.join(a.get('failureMessages',[]))[:3000])
