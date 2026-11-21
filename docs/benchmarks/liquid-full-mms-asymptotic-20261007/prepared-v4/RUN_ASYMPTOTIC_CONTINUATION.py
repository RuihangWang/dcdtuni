"""PREPARED ONLY. Explicit later Root authorization and immutable manifest required."""
from pathlib import Path
import argparse,json,hashlib,os,sys,time,gzip,signal,subprocess,shutil,datetime,traceback
HERE=Path(__file__).resolve().parent
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
A=B/'.codex-tmp/goal-20261005/air'
LEVELS=(('0p03125',.03125,257),('0p015625',.015625,513))
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def exact_rows(root,rows):
 for r in rows:
  p=Path(r.get('absolutePath','')) if 'absolutePath' in r else root/r['path']
  assert p.is_file() and p.stat().st_size==r['bytes'] and sha(p)==r['sha256'],str(p)
def save(p,o):
 with p.open('x',encoding='utf-8') as f:json.dump(o,f,indent=2,allow_nan=False);f.write('\n')
def proc_start(pid):
 try:return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
 except (FileNotFoundError,ProcessLookupError):return None
def kill_owned(p,ticks):
 # Never signal a reused PID or another group; check again for each signal.
 for sig in (signal.SIGTERM,signal.SIGKILL):
  if p.poll() is not None:return
  if proc_start(p.pid)!=ticks:raise RuntimeError('process ownership lost before timeout signal')
  try:
   if os.getpgid(p.pid)!=p.pid:raise RuntimeError('unexpected child process group')
   os.killpg(p.pid,sig)
  except ProcessLookupError:return
  try:p.wait(timeout=3 if sig==signal.SIGTERM else 5);return
  except subprocess.TimeoutExpired:pass
def run(label,cmd,cwd,env,deadline,out):
 allowance=deadline-time.monotonic()
 assert allowance>0,'owned study/grid deadline exhausted'
 start=time.monotonic();p=None;ticks=None;timed=False;exception=None
 logs=out/'logs';logs.mkdir(exist_ok=True)
 with (logs/(label+'.stdout')).open('xb') as so,(logs/(label+'.stderr')).open('xb') as se:
  try:
   p=subprocess.Popen(cmd,cwd=cwd,env=env,stdout=so,stderr=se,start_new_session=True)
   ticks=proc_start(p.pid);assert ticks is not None,'child disappeared before ownership capture'
   try:p.wait(timeout=allowance)
   except subprocess.TimeoutExpired:timed=True;kill_owned(p,ticks)
  except BaseException as e:
   exception=repr(e)
   if p is not None and p.poll() is None:kill_owned(p,ticks)
  finally:
   result={'label':label,'argv':cmd,'cwd':str(cwd),'pid':p.pid if p else None,'startTicks':ticks,'returncode':p.returncode if p else None,'timedOut':timed,'exception':exception,'elapsedSeconds':time.monotonic()-start,'allowanceSeconds':allowance,
    'env':{k:env.get(k) for k in ('CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','PYTHONDONTWRITEBYTECODE','TMP','TMPDIR','TEMP','XDG_CACHE_HOME','PYTHONPATH','MMS_VARIANT','MMS_ASYMPTOTIC_TAG')}}
   # Raw execution/exit status is persisted before output decoding.
   save(logs/(label+'.status.json'),result)
 if exception or timed or not p or p.returncode!=0:raise RuntimeError(label+' failed; no retry; inspect raw logs/status')
 return result
def current_guards():
 o=json.loads((HERE/'current-normal48-guards.json').read_text());bad=[]
 for r in o['guards']:
  p=Path(r['path'])
  if not p.is_file() or p.stat().st_size!=r['bytes'] or sha(p)!=r['sha256']:bad.append(r['path'])
 assert not bad,'normal liquid source drift: '+repr(bad)
 return {'scope':o['scope'],'count':len(o['guards']),'drifts':bad,'UITransitionHandling':o['UITransitionHandling']}
def extract(g,entry,destination=None):
 source=HERE/'original-data'/entry['archive_path']
 assert sha(source)==entry['archive_sha256']
 data=gzip.decompress(source.read_bytes())
 assert len(data)==entry['original_bytes'] and hashlib.sha256(data).hexdigest()==entry['original_sha256']
 dst=g/(destination or entry['original_path']);dst.parent.mkdir(parents=True,exist_ok=True)
 with dst.open('xb') as f:f.write(data)
 return {'path':str(dst),'bytes':len(data),'sha256':entry['original_sha256']}
def main():
 ap=argparse.ArgumentParser()
 ap.add_argument('--execute-authorized',action='store_true')
 ap.add_argument('--approved-manifest-sha',required=True)
 ap.add_argument('--output-dir',required=True)
 args=ap.parse_args()
 # All approval/source/tool checks occur before any write or Popen.
 assert args.execute_authorized,'no numerical execution authorization'
 assert len(args.approved_manifest_sha)==64 and sha(HERE/'manifest.json')==args.approved_manifest_sha,'approved prepared-manifest identity'
 manifest=json.loads((HERE/'manifest.json').read_text());exact_rows(HERE,manifest['payload'])
 before=current_guards()
 tool=json.loads((HERE/'installed-tool-bindings.json').read_text())
 for r in tool['tools']:assert Path(r['path']).stat().st_size==r['bytes'] and sha(r['path'])==r['sha256'],r['path']
 out=Path(args.output_dir)
 assert out.is_absolute() and not out.exists() and not out.is_symlink()
 resolved=out.resolve();assert resolved.is_relative_to(A/'.codex-tmp') and not resolved.is_relative_to(HERE),'fresh owned AIR private output only'
 assert out.parent.is_dir(),'parent must already exist'
 assert set((10,11)).issubset(os.sched_getaffinity(0)),'runner must start with authorized CPU10-11 available'
 os.sched_setaffinity(0,{10,11})
 start=time.monotonic();whole_deadline=start+1800
 out.mkdir(exist_ok=False)
 save(out/'execution-before.json',{'UTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manifestSHA':args.approved_manifest_sha,'normal':before,'CPU':sorted(os.sched_getaffinity(0)),'studyDeadlineSeconds':1800,'perGridSeconds':900,'productionDeadlineChanged':False})
 copies=json.loads((HERE/'cohort-copy-bindings.json').read_text())['copies']
 source_rows=[r for r in copies if r['path'].startswith('after/')]
 extraction=json.loads((HERE/'archive-extraction-spec.json').read_text())['requiredOriginals']
 ex={r['original_path']:r for r in extraction}
 workers=('generate_asymptotic.py','newton_asymptotic.py','independent_asymptotic_recurrence.py','independent_trajectory.py','fine.test.ts','vitest.fine.config.ts','independent_project_fingerprint.cjs')
 outcomes=[];failure=None
 try:
  for tag,dt,n in LEVELS:
   grid_start=time.monotonic();deadline=min(whole_deadline,grid_start+900)
   current_guards()
   g=out/('grid-'+tag);g.mkdir(exist_ok=False)
   for r in source_rows:
    assert time.monotonic()<deadline,'deadline during cohort copy'
    src=HERE/r['path'];dst=g/r['path'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    assert sha(dst)==r['sha256']
   for nfile in workers:shutil.copyfile(HERE/nfile,g/nfile)
   for sub in ('inputs','results','tmp','cache'): (g/sub).mkdir(exist_ok=True)
   # Existing installed dependency tree is borrowed by explicit read-only-reference links.
   # No npm/install action; Vite and Python caches are redirected to this grid.
   nm=Path(tool['nodeModules'])
   os.symlink(nm,g/'node_modules',target_is_directory=True)
   os.symlink(nm,g/'after/src/frontend/react/node_modules',target_is_directory=True)
   # Original accepted fine compiler has no grid-root package.json; tooling metadata stays a reference.
   shutil.copyfile(HERE/'original-data/results/after-transient-base.liquidrun',g/'results/after-transient-base.liquidrun')
   input_records=[extract(g,ex['inputs/transient-base.cfdproj']),
    extract(g,ex['results/fine-independent-cycle-construction.npz'],'results/frozen-original-cycle.npz'),
    extract(g,ex['results/manufactured-dt-0p0625.liquidrun'],'results/original-anchor-0p0625.liquidrun')]
   save(g/'input-binding.json',{'original':input_records,'sourceCount':len(source_rows),'sourceBytes':sum(r['bytes'] for r in source_rows),'levelsOnly':[tag],'originalMockPackAndFullManufacturedSources':True})
   env=dict(os.environ)
   for k in ('LD_PRELOAD','LD_AUDIT','PYTHONPATH'):env.pop(k,None)
   env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(g/'after/src'),TMP=str(g/'tmp'),TMPDIR=str(g/'tmp'),TEMP=str(g/'tmp'),XDG_CACHE_HOME=str(g/'cache'),MMS_VARIANT='after',MMS_ASYMPTOTIC_TAG=tag)
   py='/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python';node='/usr/bin/node'
   statuses=[]
   statuses.append(run('generate-new-grid',[py,str(g/'generate_asymptotic.py'),tag],g,env,deadline,g))
   statuses.append(run('compile-original-React-producer',[node,str(nm/'vitest/vitest.mjs'),'run','--config',str(g/'vitest.fine.config.ts')],g,env,deadline,g))
   # Raw/typed/schema/source/initial/flow/physical gates are original; no env cap override.
   statuses.append(run('original-Newton-new-grid',[py,str(g/'newton_asymptotic.py'),'after',tag],g,env,deadline,g))
   statuses.append(run('independent-original-thermal-recurrence',[py,str(g/'independent_asymptotic_recurrence.py'),tag],g,env,deadline,g))
   exact_rows(HERE,manifest['payload']);current_guards()
   value=json.loads((g/'results/mms-asymptotic-single.json').read_text())['runs']
   assert len(value)==1 and value[0]['tag']==tag and value[0]['dt']==dt and value[0]['frames']==n
   independent=json.loads((g/'recurrence-result.json').read_text())
   assert independent['unknowns']==1179 and len(independent['runs'])==1 and independent['runs'][0]['tag']==tag
   value[0]['independentRecurrence']=independent['runs'][0]
   value[0]['gridElapsedSecondsIncludingPrep']=time.monotonic()-grid_start
   value[0]['raw1693Coverage']='flat1693_actual/reference/error saved along with original full written blocks; pressure fixed datum extra retained'
   outcomes.append(value[0]);save(out/(tag+'-completed.json'),value[0])
   assert time.monotonic()<deadline,'grid deadline exhausted during final collection'
  original=json.loads((HERE/'original-data/results/mms-refinement.json').read_text())
  anchors=original['runs'][-2:]
  # Independently re-read the existing two anchors' raw arrays, never rerun their Newton roots.
  import numpy as np
  oldread=out/'readonly-old-anchor-replay';oldread.mkdir(exist_ok=False)
  old_anchor_checks=[]
  for anchor in anchors:
   tag=anchor['tag'];archive=ex['results/newton-vs-manufactured-'+tag+'.npz']
   evidence=extract(oldread,archive)
   with np.load(evidence['path'],allow_pickle=False) as raw:
    assert len(raw['times'])==anchor['frames'] and float(raw['times'][-1])==8.
    for kind,values in anchor['errors'].items():
     delta=raw[kind+'_error'];actual=raw[kind+'_actual'];reference=raw[kind+'_reference']
     assert np.array_equal((actual-reference).view(np.uint64),delta.view(np.uint64))
     assert delta.size==values['all_entries']
     assert float(np.max(np.abs(delta[-1])))==values['final_inf']
     assert float(np.max(np.abs(delta)))==values['whole_trajectory_inf']
   old_anchor_checks.append({'tag':tag,'rawSHA256':archive['original_sha256'],'allReportedBlockErrorsRawExact':True,'oldNewtonReexecuted':False})
  save(out/'readonly-original-anchor-checks.json',old_anchor_checks)
  assert [(r['dt'],r['frames']) for r in anchors]==[(.125,65),(.0625,129)]
  import math
  thermal=('node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K')
  combined=anchors+outcomes
  rates={kind:{metric:[math.log(combined[i]['errors'][kind][metric]/combined[i+1]['errors'][kind][metric],2) for i in range(3)] for metric in ('final_inf','whole_trajectory_inf')} for kind in thermal}
  assert time.monotonic()<whole_deadline,'whole study deadline before final result'
  save(out/'asymptotic-results.json',{'scope':'same T8 manufactured full1693 only2newlevels; no ordinary/GPU/calibration/normal acceptance','runs':combined,'rates':rates,'originalCoarseRuns':original['runs'],'originalCoarseFinalOrders':original['orders'],'originalCoarseHXsecGateRemainsRED':True,'newFinerRatesDoNotRewriteOriginalGate':True,'noHydraulicTemporalOrderClaim':True})
 except BaseException as e:
  failure=repr(e)
  with (out/'failure.txt').open('x') as f:f.write(traceback.format_exc())
 finally:
  # Always preserve after controls/partial status; do not retry a failed level.
  after_error=None
  try:after=current_guards();exact_rows(HERE,manifest['payload'])
  except BaseException as e:after_error=repr(e);after={'verificationError':after_error}
  if time.monotonic()>=whole_deadline and failure is None:failure='whole-study-budget-exhausted-after-final-guards'
  save(out/'execution-after.json',{'normal':after,'completedNewLevels':len(outcomes),'failure':failure,'afterVerificationError':after_error,'elapsedSeconds':time.monotonic()-start,'newNewtonLevelsOnly':[x['tag'] for x in outcomes],'oldGridNewtonReruns':0,'normalSourceWrites':0,'serviceCalls':0,'GPU':False})
 if failure or after_error:raise RuntimeError(failure or after_error)
 assert time.monotonic()<whole_deadline,'whole study deadline expired'
 print(json.dumps({'status':'two new levels complete; original coarse RED preserved','output':str(out),'elapsedSeconds':time.monotonic()-start},indent=2))
if __name__=='__main__':main()
