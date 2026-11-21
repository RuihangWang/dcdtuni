from pathlib import Path
import argparse,hashlib,json,os,runpy,subprocess,sys,time
F=Path(__file__).resolve().parent
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);a=parser.parse_args();out=Path(a.out).resolve();assert out.is_relative_to(B) and not out.exists();out.mkdir(parents=True);start=time.monotonic();children=[]
 def no_child(*args,**kwargs):children.append('forbidden-child-attempt');raise AssertionError('Plan validation cannot launch any child, compiler, APP or checker')
 oldP,oldR=subprocess.Popen,subprocess.run;subprocess.Popen=no_child;subprocess.run=no_child
 try:
  module=runpy.run_path(str(F/'BUILD.py'),run_name='__plan_validation__');plan=module['plan'];T=module['T'];assert not T.exists();cohort=json.loads((F/'source-cohort-bindings.json').read_text());assert len(cohort)==39;changed=[r for r,v in cohort.items() if v['actualBuiltF3Before']!=v['plannedSource']];assert changed==[plan['candidateHeader']];assert sum(v['onlyHeaderDelta'] for v in cohort.values())==1
  files=[p for p in (F/'source-cohort').rglob('*') if p.is_file()];assert len(files)==39 and not any(p.is_symlink() for p in (F/'source-cohort').rglob('*'));assert not list((F/'source-cohort').rglob('*.o')) and not (F/'source-cohort/platforms').exists();assert all(p.name not in ['dcCartMesh','libdcCartMesh.so'] for p in files)
  before=module['guards']();assert len(before['normal']['source39'])==39 and len(before['normal']['Main29'])==29 and len(before['normal']['pair'])==2 and len(before['authorizedI4'])==4 and len(before['roles'])==8
  oldmanifest=Path(next(p for p in plan['externalReferences'] if p.endswith('/FINAL_MANIFEST.json')));oldrows=json.loads(oldmanifest.read_text())['files'];assert len(oldrows)==84 and all(module['info'](oldmanifest.parent/r)==v for r,v in oldrows.items())
  env0=os.environ.copy()
  try:
   for k in ['DCCART_PRIVATE_DW_WIDER_SEARCH','DCCART_CONE_KERNEL_TEST','CART_CONTROLLED','LD_PRELOAD','LD_AUDIT','LD_DEBUG','LD_LIBRARY_PATH','GXX']:os.environ[k]='controlled-inherited-value-not-authority'
   env=module['env_for_build']();assert not any(k.startswith(('DCCART_','CART_')) for k in env);assert all(k not in env for k in ['LD_PRELOAD','LD_AUDIT','LD_DEBUG']);assert env['LD_LIBRARY_PATH']=='' and env['GXX']==plan['compiler']['path'] and env['CUDA_VISIBLE_DEVICES']=='';assert env['WM_NCOMPPROCS']=='2';assert all(env[k]=='1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']);assert all(Path(env[k]).resolve().is_relative_to(T.resolve()) for k in ['TMPDIR','TMP','TEMP','XDG_CACHE_HOME','CCACHE_DIR','PYTHONPYCACHEPREFIX'])
  finally:os.environ.clear();os.environ.update(env0)
  argv=sys.argv[:];refused=[]
  try:
   sys.argv=[str(F/'BUILD.py'),'--approved-plan-manifest-sha','0'*64]
   try:module['main']();raise AssertionError('Missing ROOT explicit authorization accepted')
   except SystemExit as e:assert 'PREPARED ONLY' in str(e);refused.append('missing-execute-authorization')
   sys.argv=[str(F/'BUILD.py'),'--execute-build-authorized','--approved-plan-manifest-sha','0'*64]
   try:module['main']();raise AssertionError('Wrong exact manifest accepted')
   except AssertionError as e:assert 'exact immutable build plan manifest' in str(e);refused.append('wrong-exact-manifest-sha')
  finally:sys.argv=argv
  manifest=json.loads((F/'MANIFEST.json').read_text());assert all(module['info'](F/r)==v for r,v in manifest['files'].items());assert not T.exists() and not children;after=module['guards']();assert before==after;compile((F/'BUILD.py').read_text(),str(F/'BUILD.py'),'exec');compile(Path(__file__).read_text(),str(__file__),'exec')
  result={'rc':0,'scope':'preparation-only real file/role/immutable reference guards and child-forbidden controlled validation; not compiler/native/quality testing','elapsedSeconds':time.monotonic()-start,'plannedSources':39,'actualBasePayloads':84,'normalNative39':39,'Main29':29,'normalELFs':2,'authorizedUI4':4,'actualRoles':8,'onlySourceDelta':changed,'sixUnits':plan['sixUnits'],'compileWorkers':2,'compileCPU':plan['CPU'],'compileWatchdogSeconds':180,'rejections':refused,'contaminatedEnvironmentCleared':True,'guardsBeforeAfterExact':True,'buildDirectoryAbsent':True,'APP_LIBBuildPerformed':False,'nativeRunPerformed':False,'childProcessAttempts':children,'noObjectOrBinarySeeded':True};(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 finally:subprocess.Popen,subprocess.run=oldP,oldR
if __name__=='__main__':main()
