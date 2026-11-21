from pathlib import Path
import argparse,hashlib,json,os,runpy,subprocess,sys,time
HERE=Path(__file__).resolve().parent;A=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air');F3=A/'.codex-tmp/full-mms-asymptotic-continuation-prepared-v3-20261007'
def info(p):return {'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest(),'bytes':Path(p).stat().st_size}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out).resolve();assert out.is_relative_to(A/'.codex-tmp') and not out.exists();out.mkdir();start=time.monotonic();children=[]
 def no_child(*args,**kwargs):children.append('forbidden');raise AssertionError('PREPARE validation cannot generate/React/solve/retry/launch children')
 oldP,oldR=subprocess.Popen,subprocess.run;subprocess.Popen=no_child;subprocess.run=no_child
 try:
  original=json.loads((F3/'manifest.json').read_text());assert info(F3/'manifest.json')['sha256']=='047086208dcdd73edbe9b7494c436b83ce866dabee8fc0b587f3d8ccd92679f1';assert len(original['payload'])==570 and all(info(F3/r['path'])=={k:r[k] for k in ['sha256','bytes']} for r in original['payload']);science=[r for r in original['payload'] if r['path'].startswith('after/')];assert len(science)==505;assert all(info(HERE/r['path'])=={k:r[k] for k in ['sha256','bytes']} for r in science);assert all(not(HERE/r['path']).is_symlink() and (HERE/r['path']).stat().st_ino!=(F3/r['path']).stat().st_ino for r in science)
  held=['generate_asymptotic.py','newton_asymptotic.py','independent_asymptotic_recurrence.py','independent_trajectory.py','vitest.fine.config.ts'];assert all(info(HERE/p)==info(F3/p) for p in held)
  oldrunner=(F3/'RUN_ASYMPTOTIC_CONTINUATION.py').read_text();newrunner=(HERE/'RUN_ASYMPTOTIC_CONTINUATION.py').read_text();old="workers=('generate_asymptotic.py','newton_asymptotic.py','independent_asymptotic_recurrence.py','independent_trajectory.py','fine.test.ts','vitest.fine.config.ts')";new=old[:-1]+",'independent_project_fingerprint.cjs')";assert newrunner==oldrunner.replace(old,new) and oldrunner.count(old)==1
  mod=runpy.run_path(str(HERE/'RUN_ASYMPTOTIC_CONTINUATION.py'),run_name='__preparation_v4__');before=mod['current_guards']();assert before['count']==48;assert mod['LEVELS']==(('0p03125',.03125,257),('0p015625',.015625,513))
  tool=json.loads((HERE/'installed-tool-bindings.json').read_text());assert all(info(r['path'])=={k:r[k] for k in ['sha256','bytes']} for r in tool['tools'])
  cause=json.loads((HERE/'V4-COMPARATOR-CAUSE-BINDING.json').read_text());assert all(info(r['source'])=={k:r[k] for k in ['sha256','bytes']} and info(HERE/r['copied'])=={k:r[k] for k in ['sha256','bytes']} for r in cause['originalV3RawCopies']);assert all(info(r['path'])=={k:r[k] for k in ['sha256','bytes']} for r in cause['actualExistingGeneratedInputs']);assert all(info(HERE/r)==v for r,v in cause['recipeSourceBindings'].items())
  proof=json.loads((HERE/'v4-readonly-fingerprint-proof/result.json').read_text());assert proof['rc']==proof['scientificStagesRun']==proof['normalSourceWrites']==0;assert proof['newProjectContentSHA']=='48f5f07b422473e078a83ac53d6bbddec57fdc970113e60962ecf12193bca705' and proof['independentlyReconstructedOldAnchorProjectSHA']=='18f38449a9f0ca355970a41e099be8e09896ac07ceb3599574b27a9af9db5c63'
  compile(newrunner,str(HERE/'RUN_ASYMPTOTIC_CONTINUATION.py'),'exec')
  for p in held:
   if p.endswith('.py'):compile((HERE/p).read_text(),str(HERE/p),'exec')
  argv=sys.argv[:];refused=[];future=A/'.codex-tmp/full-mms-asymptotic-runtime-v4-20261007-exclusive';assert not future.exists()
  try:
   sys.argv=[str(HERE/'RUN_ASYMPTOTIC_CONTINUATION.py'),'--approved-manifest-sha','0'*64,'--output-dir',str(future)]
   try:mod['main']();raise AssertionError('no execute accepted')
   except AssertionError as e:assert 'no numerical execution authorization' in str(e);refused.append('no-execute')
   sys.argv=[str(HERE/'RUN_ASYMPTOTIC_CONTINUATION.py'),'--execute-authorized','--approved-manifest-sha','0'*64,'--output-dir',str(future)]
   try:mod['main']();raise AssertionError('wrong manifest accepted')
   except AssertionError as e:assert 'approved prepared-manifest identity' in str(e);refused.append('wrong-manifest')
  finally:sys.argv=argv
  own=json.loads((HERE/'manifest.json').read_text());assert all(info(HERE/r['path'])=={k:r[k] for k in ['sha256','bytes']} for r in own['payload']);after=mod['current_guards']();assert after==before and not future.exists() and not children
  result={'rc':0,'elapsedSeconds':time.monotonic()-start,'scope':'prepare-only exact files/by-value/science505/rawV3/current48 guards, Python syntax and no-child authorization refusal; not actual React/DAE validation','originalV3Payload570Held':True,'science505ByValueExact':True,'scienceBytes':sum(r['bytes'] for r in science),'heldGeneratorNewtonReferenceConfigExact':held,'runnerOneToolCopyDeltaOnly':True,'currentNormal48BeforeAfterHeld':True,'originalV3RawLogsStatusUnchanged':True,'independentExistingProjectFingerprintProof':proof,'installedToolBindingsHeld':True,'rejections':refused,'futureRuntimeAbsent':True,'childCalls':children,'generationReactNewtonRetries':0};(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='independentExistingProjectFingerprintProof'},indent=2))
 finally:subprocess.Popen,subprocess.run=oldP,oldR
if __name__=='__main__':main()
