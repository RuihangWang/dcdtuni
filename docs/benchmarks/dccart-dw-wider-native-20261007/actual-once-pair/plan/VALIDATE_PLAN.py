from pathlib import Path
import argparse,hashlib,json,os,runpy,subprocess,sys,time
F=Path(__file__).resolve().parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);a=parser.parse_args();out=Path(a.out).resolve();assert out.is_relative_to(B) and not out.exists();out.mkdir(parents=True);start=time.monotonic();children=[]
 def no_child(*args,**kwargs):children.append('forbidden-child-attempt');raise AssertionError('Preparation validation cannot launch compiler/APP/checkMesh/children')
 oldP,oldR=subprocess.Popen,subprocess.run;subprocess.Popen=no_child;subprocess.run=no_child
 try:
  module=runpy.run_path(str(F/'RUN.py'),run_name='__runtime_plan_validation__');plan=module['plan'];T=module['T'];CASE=module['CASE'];assert not T.exists() and not CASE.exists();assert plan['CPU']=='32,33,34,35' and plan['CLISeconds']==110 and plan['nativeWatchdogSeconds']==plan['checkWatchdogSeconds']==120 and plan['stockOptions']==['-allTopology','-allGeometry'] and plan['MPI'] is False
  before=module['guards']();assert len(before['groups']['normal']['source39'])==39 and len(before['groups']['normal']['Main29'])==29 and len(before['groups']['normal']['pair'])==2 and len(before['groups']['authorizedI4'])==4 and len(before['groups']['roles'])==8 and len(before['groups']['privateNewSource39'])==39 and len(before['groups']['originalInput12'])==12
  build=Path(plan['buildFinalManifest']['path']);rows=json.loads(build.read_text())['files'];assert len(rows)==33 and all(module['info'](build.parent/r)==v for r,v in rows.items());baseline=Path(plan['baselineComparison']['root']);rows0=json.loads((baseline/'MANIFEST.json').read_text())['files'];assert len(rows0)==77 and all(module['info'](baseline/r)==v for r,v in rows0.items())
  env0=os.environ.copy()
  try:
   for k in ['DCCART_CONE_KERNEL_TEST','DCCART_CONE_QUALITY_KERNEL_CANDIDATES','DCCART_CONE_NONORTH_KERNEL_CANDIDATES','DCCART_PRIVATE_DW_WIDER_SEARCH','CART_CONTROLLED','LD_PRELOAD','LD_AUDIT','LD_DEBUG','LD_LIBRARY_PATH']:os.environ[k]='controlled-inherited-not-authority'
   e0=module['env_for']('DEFAULT_OFF');e1=module['env_for']('WIDER_ON');delta={k:[e0.get(k),e1.get(k)] for k in set(e0)|set(e1) if e0.get(k)!=e1.get(k)};assert delta=={'DCCART_PRIVATE_DW_WIDER_SEARCH':[None,'1']};assert not any(k in e0 or k in e1 for k in plan['forbiddenVariables']);assert all(e0[k]==e1[k]=='1' for k in plan['commonFlags']);assert all(e0[k]==e1[k]=='' for k in ['CUDA_VISIBLE_DEVICES','LD_LIBRARY_PATH']);assert not any(k in e0 or k in e1 for k in ['LD_PRELOAD','LD_AUDIT','LD_DEBUG']);assert not any(k.startswith('CART_') for k in e0|e1);assert all(e0[k]==e1[k]=='1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']);assert all(Path(e0[k]).resolve().is_relative_to(T.resolve()) and e0[k]==e1[k] for k in ['TMPDIR','TMP','TEMP','XDG_CACHE_HOME','CCACHE_DIR','PYTHONPYCACHEPREFIX'])
  finally:os.environ.clear();os.environ.update(env0)
  mesh=baseline/'cases/AXIS_SHARED/Mesh/constant/polyMesh';sets=module['parse_sets'](mesh);assert len(sets['sets'].get('underdeterminedCells',[]))==1 and len(sets['sets'].get('nonOrthoFaces',[]))==1;assert not sets['sets'].get('lowWeightFaces',[]) and not sets['sets'].get('lowVolRatioFaces',[]) and not sets['sets'].get('skewFaces',[]);geometry=module['actual_mesh'](mesh);assert len(geometry['points'])==1704 and len(geometry['faces'])==7514 and len(geometry['neighbour'])==6847 and len(geometry['boundaryFaceIDs'])==667 and len(geometry['boundaryPointIDs'])==660
  probe=module['compare_science'](mesh,mesh);assert probe['allEightScientificBytesExact'] and probe['topologyFourAndZonesThreeExact'] and probe['allAddedStockSetsEmpty'] and not probe['writtenPointsChanged'] and probe['allBoundaryPointBitsHeld'];assert not probe['changedBoundaryPoints']
  argv=sys.argv[:];refused=[]
  try:
   sys.argv=[str(F/'RUN.py'),'--approved-plan-manifest-sha','0'*64]
   try:module['main']();raise AssertionError('Missing execution authorization accepted')
   except SystemExit as e:assert 'PREPARED ONLY' in str(e);refused.append('missing-execute-authorization')
   sys.argv=[str(F/'RUN.py'),'--execute-authorized','--approved-plan-manifest-sha','0'*64]
   try:module['main']();raise AssertionError('Wrong exact manifest accepted')
   except AssertionError as e:assert 'exact immutable plan manifest' in str(e);refused.append('wrong-exact-manifest-sha')
  finally:sys.argv=argv
  manifest=json.loads((F/'MANIFEST.json').read_text());assert all(module['info'](F/r)==v for r,v in manifest['files'].items());assert not T.exists() and not CASE.exists() and not children;after=module['guards']();assert before==after;compile((F/'RUN.py').read_text(),str(F/'RUN.py'),'exec');compile(Path(__file__).read_text(),str(__file__),'exec')
  result={'rc':0,'elapsedSeconds':time.monotonic()-start,'scope':'PREPARE ONLY pure file/role/input guards and read-only actual original stock/binary science parser validation; no child/native/checker/build/casecopy','actualBuildPayloadsHeld':33,'originalActualSharedPayloadsHeld':77,'actualNewSource39Held':39,'normal70PlusAuthorizedI4Held':True,'ownedRoles8Held':True,'originalInput12Held':True,'variants':plan['variants'],'exactOnlyEnvironmentDelta':delta,'serialCPU':plan['CPU'],'CLISeconds':110,'watchdogSeconds':120,'strictStockOptions':plan['stockOptions'],'originalBaselineRawSetCounts':{k:len(v) for k,v in sets['sets'].items()},'actualBinaryReferenceCounts':{'points':len(geometry['points']),'faces':len(geometry['faces']),'internalFaces':len(geometry['neighbour']),'boundaryFaces':len(geometry['boundaryFaceIDs']),'boundaryPoints':len(geometry['boundaryPointIDs'])},'readOnlyParserNoMeshMutation':True,'rejections':refused,'runtimeDirectoryAbsent':True,'caseDirectoryAbsent':True,'nativeBuildOrCaseExecutionPerformed':False,'childProcessAttempts':children};(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 finally:subprocess.Popen,subprocess.run=oldP,oldR
if __name__=='__main__':main()
