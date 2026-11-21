from pathlib import Path
import argparse,json,hashlib,os,shutil,subprocess,time,signal,datetime,shlex,re
F=Path(__file__).resolve().parent
plan=json.loads((F/'plan.json').read_text());T=Path(plan['buildDirectory']);N=Path(plan['workingNativeRoot']);OF=Path(plan['OF']);B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
def info(p):
 p=Path(p);h=hashlib.sha256()
 with p.open('rb') as s:
  for c in iter(lambda:s.read(1<<20),b''):h.update(c)
 return {'sha256':h.hexdigest(),'bytes':p.stat().st_size}
def write(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as s:s.write(json.dumps(v,indent=2)+'\n')
def role(pid):
 p=Path('/proc',pid);s=(p/'stat').read_text().rsplit(')',1)[1].split();uid=int(next(x.split()[1] for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')))
 return {'uid':uid,'start':s[19],'cwd':os.readlink(p/'cwd'),'argvSHA256':hashlib.sha256((p/'cmdline').read_bytes()).hexdigest()}
def bridge():
 p=OF/'src';assert p.is_symlink() and (p/'meshTools/lnInclude').is_dir();return {'path':str(p),'link':os.readlink(p),'resolved':str(p.resolve()),'createAllowed':False}
def guards():
 expected=json.loads((F/'guards/normal-scope.json').read_text());normal={g:{p:info(p) for p in rows} for g,rows in expected.items()};assert normal==expected,'Focused AIR39/Main29/normalpair drift; no rollback is requested'
 ui=json.loads((F/'guards/authorized-I4-transition.json').read_text());u={r['path']:info(r['path']) for r in ui['rows']};assert all(u[r['path']]==r['currentAuthorizedAfter'] for r in ui['rows']),'Root-authorized I4 after changed; only explicit rebaseline permitted'
 oldroles=json.loads((F/'guards/owned-roles.json').read_text());roles={pid:role(pid) for pid in oldroles};assert roles==oldroles,'Owned roles changed; do not restart/kill from collector'
 cohort=json.loads((F/'source-cohort-bindings.json').read_text());assert len(cohort)==39
 for rel,row in cohort.items():
  assert info(row['actualBuiltF3Source'])==row['actualBuiltF3Before'],'Base actual built F3 source drift'
  assert info(F/'source-cohort'/rel)==row['plannedSource'],'Planned source drift'
 for p,v in plan['externalReferences'].items():assert info(p)==v,'Frozen reference drift:'+p
 for row in plan['baseActualBuiltF3Pair'].values():assert info(row['path'])=={k:row[k] for k in ['sha256','bytes']},'Base pair drift'
 assert bridge()==plan['OFHeaderBridge'],'OF header bridge changed; never create/repair it'
 assert info(plan['compiler']['path'])=={k:plan['compiler'][k] for k in ['sha256','bytes']},'Compiler changed'
 return {'normal':normal,'authorizedI4':u,'roles':roles,'bridge':bridge(),'basePair':plan['baseActualBuiltF3Pair'],'plannedSources':{rel:r['plannedSource'] for rel,r in cohort.items()},'scope':'focused normal74file guards+roles8, base39 source+pair+refs; no whole-repo hash or oldI2229 rollback'}
def env_for_build():
 env={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_')) and k not in ['LD_PRELOAD','LD_AUDIT','LD_DEBUG','LD_LIBRARY_PATH','GXX']}
 env.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',LD_LIBRARY_PATH='',GXX=plan['compiler']['path'],CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',WM_NCOMPPROCS='2',PYTHONDONTWRITEBYTECODE='1',PYTHONPYCACHEPREFIX=str(T/'cache/pycache'),TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'),CCACHE_DISABLE='1')
 assert not any(k.startswith(('DCCART_','CART_')) for k in env);return env
def owned_build(cmd,env):
 rec={'command':cmd,'cwd':str(N),'startUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'watchdogSeconds':plan['watchdogSeconds'],'workers':2,'CPU':plan['CPU'],'environment':{k:env[k] for k in ['GXX','CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','WM_NCOMPPROCS','TMPDIR','TMP','TEMP','XDG_CACHE_HOME','CCACHE_DIR','CCACHE_DISABLE','PYTHONPYCACHEPREFIX']},'DCCART_CARTVariables':[],'APPExecution':False,'objectsSeeded':0};write(T/'logs/build-started.json',rec);start=time.monotonic();timed=False
 try:
  with (T/'logs/build.stdout').open('xb') as out,(T/'logs/build.stderr').open('xb') as err:
   proc=subprocess.Popen(cmd,cwd=N,env=env,stdout=out,stderr=err,start_new_session=True);ticks=Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19];write(T/'logs/build-process.json',{'pid':proc.pid,'startTicks':ticks,'newSession':True})
   try:rc=proc.wait(timeout=plan['watchdogSeconds'])
   except subprocess.TimeoutExpired:
    timed=True
    if proc.poll() is None:
     assert Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19]==ticks and os.getpgid(proc.pid)==proc.pid;os.killpg(proc.pid,signal.SIGTERM)
    try:rc=proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
     assert Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19]==ticks and os.getpgid(proc.pid)==proc.pid;os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
   rec.update(exitCode=rc,timedOut=timed)
 except BaseException as e:rec['collectorException']=type(e).__name__+':'+str(e);raise
 finally:rec.update(elapsedSeconds=time.monotonic()-start,endUTC=datetime.datetime.now(datetime.timezone.utc).isoformat());write(T/'logs/build-status.json',rec)
 return rec

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute-build-authorized',action='store_true');parser.add_argument('--approved-plan-manifest-sha',required=True);args=parser.parse_args()
 if not args.execute_build_authorized:raise SystemExit('PREPARED ONLY; separate ROOT exact build authorization required')
 assert info(F/'MANIFEST.json')['sha256']==args.approved_plan_manifest_sha,'ROOT must approve exact immutable build plan manifest'
 manifest=json.loads((F/'MANIFEST.json').read_text());assert all(info(F/p)==v for p,v in manifest['files'].items()),'Frozen plan drift'
 assert T.resolve().is_relative_to(B) and N.resolve().is_relative_to(T.resolve()) and T.name=='build';assert not T.exists(),'Exclusive once fresh build; no retry, object seed, or replacement'
 before=guards();oldmanifest=Path(next(p for p in plan['externalReferences'] if p.endswith('/FINAL_MANIFEST.json')));data=json.loads(oldmanifest.read_text());assert all(info(oldmanifest.parent/p)==v for p,v in data['files'].items()),'Actual builtF3 immutable84 payload drift'
 for name in ['source','logs','guards','elf','tmp','cache']:(T/name).mkdir(parents=True,exist_ok=True)
 (T/'build.once').open('x').close();write(T/'authorization-binding.json',{'exactApprovedPlanManifestSHA':args.approved_plan_manifest_sha,'scope':plan['scope'],'APPOrCheckerExecution':False});write(T/'guards/before.json',before)
 cohort=json.loads((F/'source-cohort-bindings.json').read_text());clones={}
 for rel,row in cohort.items():
  src=F/'source-cohort'/rel;dst=N/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o755 if rel=='Allwmake' else 0o644);assert not dst.is_symlink() and dst.stat().st_ino!=src.stat().st_ino;assert info(dst)==row['plannedSource'];clones[rel]={'source':str(src),'actualBuildInput':str(dst),**info(dst),'byValueIndependentInode':True}
 assert len(clones)==39 and not any(p.is_symlink() for p in N.rglob('*')) and not(N/'platforms').exists() and not list(N.rglob('*.o'));write(T/'source/fresh-before.json',{'clones':clones,'objectsSeeded':0,'binariesSeeded':0,'onlyHeaderDiffVersusActualBuiltF3':plan['candidateHeader']})
 env=env_for_build();shell='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nexec bash ./Allwmake --cart-only -j2\n';cmd=['taskset','-c',plan['CPU'],'bash','--noprofile','--norc','-c',shell];result=None
 try:result=owned_build(cmd,env)
 finally:
  after=guards();write(T/'guards/after.json',after);assert after==before,'Focused current guard changed during build; no automatic repair or normal rollback'
  assert all(info(N/rel)==row['plannedSource'] for rel,row in cohort.items()),'Compiled source changed';write(T/'source/final-source39.json',clones)
 stdout=(T/'logs/build.stdout').read_text(errors='replace');commands=[];units=[]
 for line in stdout.splitlines():
  if ' -c ' in line and ' -o Make/' in line:
   words=shlex.split(line);assert Path(words[0]).name in ['g++','x86_64-linux-gnu-g++-11'];src=words[words.index('-c')+1];commands.append(line);units.append(src)
 objects={str(p.relative_to(N)):info(p) for p in sorted(N.rglob('*.o'))};deps={str(p.relative_to(N)):info(p) for p in sorted(N.rglob('*.C.dep'))};write(T/'source/actual-compilation-units.json',{'actualCompilerCommandLines':commands,'units':units,'expectedSix':plan['sixUnits'],'objects':objects,'dependencies':deps,'objectsSeeded':0,'warningsRetainedRaw':True})
 receipt={'build':result,'scope':'fresh paired private build only; no APP/checkMesh case invocation','normalFocusedGuardBeforeAfterHeld':True,'actualF3Source39OneHeaderDelta':True,'normalOrScienceAcceptance':False}
 if result['exitCode']==0 and not result['timedOut']:
  assert len(units)==6 and sorted(units)==sorted(plan['sixUnits']) and len(objects)==6,'Must actually compile all six fresh units'
  h=N/plan['candidateHeader'];assert info(h)['sha256']==plan['candidateHeaderSHA256'];dep=N/'src/mesh/dcCartMesh/Make/linux64GccDPInt32Opt/dcCartCutCells/dcCartCutCells.C.dep';assert dep.exists() and 'dcCartFinitePointRepair.H' in dep.read_text();alias=N/'src/mesh/dcCartMesh/lnInclude/dcCartFinitePointRepair.H';assert alias.resolve()==h.resolve() and info(alias)==info(h);write(T/'source/candidate-consumer-proof.json',{'candidate':{'path':str(h),**info(h)},'actualLibraryCutCellsDependency':{'path':str(dep),**info(dep)},'flattenedHeaderAlias':{'path':str(alias),'resolved':str(alias.resolve()),**info(alias)},'APPDependencyDoesNotClaimCandidateDirectInclude':True,'actualSixFreshUnits':units})
  app=N/'platforms/linux64GccDPInt32Opt/bin/dcCartMesh';lib=N/'platforms/linux64GccDPInt32Opt/lib/libdcCartMesh.so';assert app.is_file() and lib.is_file();libenv=env.copy();libenv['LD_LIBRARY_PATH']=str(lib.parent)+':'+str(OF/'lib/sys-mpich')+':'+str(OF/'lib')
  for name,argv in [('APP-readelf',['readelf','-d',str(app)]),('LIB-readelf',['readelf','-d',str(lib)]),('APP-file',['file',str(app)]),('LIB-file',['file',str(lib)]),('APP-ldd',['ldd',str(app)]),('LIB-ldd',['ldd',str(lib)])]:
   q=subprocess.run(argv,env=libenv,capture_output=True,timeout=15);(T/'elf'/(name+'.stdout')).write_bytes(q.stdout);(T/'elf'/(name+'.stderr')).write_bytes(q.stderr);write(T/'elf'/(name+'.json'),{'command':argv,'rc':q.returncode,'LD_LIBRARY_PATH':libenv['LD_LIBRARY_PATH'],'scope':'ELF loader inspection, not APP case invocation'});assert q.returncode==0
  assert str(lib) in (T/'elf/APP-ldd.stdout').read_text() and 'not found' not in (T/'elf/APP-ldd.stdout').read_text() and 'not found' not in (T/'elf/LIB-ldd.stdout').read_text();pair={'APP':{'path':str(app),**info(app)},'LIB':{'path':str(lib),**info(lib)},'basePairReferenceOnly':plan['baseActualBuiltF3Pair'],'resolvedNewPrivateLibrary':str(lib),'actualNativeRun':False,'candidateSource':info(h)};write(T/'elf/paired-identities.json',pair);receipt['newPair']=pair
 write(T/'receipt.json',receipt);files={str(p.relative_to(T)):info(p) for p in sorted(T.rglob('*')) if p.is_file() and not set(p.relative_to(T).parts)&{'native','tmp','cache'}};write(T/'MANIFEST.json',{'files':files,'count':len(files),'totalBytes':sum(v['bytes'] for v in files.values()),'scope':'actual private paired build only; fresh native runtime and strictMesh pending','actualPairSourceManifest':str(T/'source/final-source39.json')});print(json.dumps({'build':result,'freshUnits':units,'sourceHeaderSHA':plan['candidateHeaderSHA256'],'actualMeshAcceptance':False},indent=2));assert result['exitCode']==0 and not result['timedOut'],'Build failed: preserve logs, no retry'
if __name__=='__main__':main()
