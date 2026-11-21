import pathlib,json,hashlib,os,shutil,subprocess,time,signal,datetime,argparse
parser=argparse.ArgumentParser();parser.add_argument('--execute-build-authorized',action='store_true');parser.add_argument('--approved-proposal-manifest-sha',required=True);args=parser.parse_args()
if not args.execute_build_authorized:raise SystemExit('Prepared only: ROOT fresh-build authorization required')
assert args.approved_proposal_manifest_sha=='fd32675eba9a2df4b8c11cf4cc666359d3907c4af7aa5765ab412ce100fbf41f'
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni');A=B/'.codex-tmp/goal-20261005/air';N=A/'src/backend/aircooling/third_parties/dcCartMesh';P=A/'.codex-tmp/physical-source-fan-constant-axis-construction-private-20261007';R=P/'physical-shared-edge-bounded-eight-build-plan-v1';T=R/'build';F=T/'native/dcCartMesh';OF=pathlib.Path('/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni');V=P/'physical-shared-edge-bounded-eight-source-proposal-v1'
def h(p):
 p=pathlib.Path(p);s=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''):s.update(c)
 return {'sha256':s.hexdigest(),'bytes':p.stat().st_size}
def j(p,o):
 p.parent.mkdir(parents=True,exist_ok=True)
 data=json.dumps(o,indent=2)+'\n'
 if p.exists():
  assert p.read_text()==data,('existing receipt differs',p)
 else:
  with p.open('x') as f:f.write(data)
G=json.loads((R/'guards/current-source-cohort.json').read_text())
assert not T.exists(),'Exclusive fresh build only; no rerun or object seed'
for d in ['guards','source','logs','elf','tmp','cache']:(T/d).mkdir(parents=True,exist_ok=True)
scope={'__file__':str(R/'CONTROL_HELPERS.py')}
exec((R/'CONTROL_HELPERS.py').read_text(),scope)
controls=scope['controls'];runtimes=scope['runtimes'];rootWatch=scope['rootWatch']
prepared=json.loads((R/'manifest.json').read_text());assert all(h(R/n)==v for n,v in prepared['files'].items()),'Prepared packet drift'
rootBefore=rootWatch();j(T/'guards/root2308-before.json',rootBefore)
assert rootBefore==json.loads((R/'guards/root2308-latest-actual.json').read_text()),'Root source cohort changed since approved plan'
j(T/'guards/git-readonly-before.json',scope['gitSnapshot']())
j(T/'guards/IT-dist-readonly-before.json',scope['distObservation']())
before=controls();j(T/'guards/before.json',before)
for k in G:assert before[k]==G[k],k
runtimeBefore=runtimes();j(T/'guards/runtime-before.json',runtimeBefore)
packets=[V,B/'.codex-tmp/physical-decision-analysis-independent-20261007/FROZEN-eight-source-proposal-independent-review',P/'decision-observer-determinant-map-native-run-proof-v3']
def frozen():
 o={}
 for r in packets:
  m=json.loads((r/'manifest.json').read_text());bad=[]
  files=m['files'];items=files.items() if isinstance(files,dict) else ((x['path'],{'sha256':x['sha256'],'bytes':x['bytes']}) for x in files)
  for n,v in items:
   if h(r/n)!=v:bad.append(n)
  assert not bad,(r,bad)
  o[str(r)]={'manifest':h(r/'manifest.json'),'payloadCount':len(m['files']),'mismatches':bad}
 return o
frozenBefore=frozen();j(T/'guards/frozen-before.json',frozenBefore)
bridge=OF/'src';assert bridge.is_symlink() and (bridge/'meshTools/lnInclude').is_dir()
bridgeBefore={'path':str(bridge),'link':os.readlink(bridge),'resolved':str(bridge.resolve()),'created':False}
j(T/'guards/of-bridge-before.json',bridgeBefore)
cohort={};crel='src/mesh/dcCartMesh/dcCartCutCells/dcCartCutCells.C'
for n,v in before['source39'].items():
 rel=str(pathlib.Path(n).relative_to(N));dst=F/rel;dst.parent.mkdir(parents=True,exist_ok=True)
 if dst.exists():
  assert dst.resolve().is_relative_to(F.resolve()) and not dst.is_symlink() and dst.stat().st_ino!=pathlib.Path(n).stat().st_ino
  os.chmod(dst,0o644)
 shutil.copy2(n,dst)
 assert not dst.is_symlink() and pathlib.Path(n).stat().st_ino!=dst.stat().st_ino
 if rel==crel:
  os.chmod(dst,0o644);shutil.copy2(V/'source/dcCartCutCells.C.after-bounded-eight',dst)
 os.chmod(dst,0o755 if rel=='Allwmake' else 0o644)
 cohort[rel]={'normal':n,'before':v,'private':h(dst),'method':'by-value copy, independent inode'}
assert len(cohort)==39 and sum(x['before']!=x['private'] for x in cohort.values())==1
assert cohort[crel]['private']['sha256']=='cc7020a88395abbfa359319103c46d25f311025b32919d9cb638336b5bc6d659'
assert cohort['src/mesh/dcCartMesh/dcCartCutCells/dcCartPhysicalPointLifecycle.H']['private']['sha256']=='5bfcf76c8dbfb9787b6514fa857d5fef12960260bc169d854fc666e81836e063'
j(T/'source/private-source39.json',cohort)
j(T/'source/object-seeding.json',{'objectsSeeded':0,'reason':'Fresh six-unit compilation; no normal/private prior object or platform reused.'})
assert not any(p.is_symlink() for p in F.rglob('*'))
env=os.environ.copy()
for k in list(env):
 if k.startswith('DCCART_') or k.startswith('CART_'):env.pop(k)
env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'))
cmd=['taskset','-c','10,11','bash','--noprofile','--norc','-c','source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nexec bash ./Allwmake --cart-only -j2']
j(T/'logs/build-command.json',{'argv':cmd,'cwd':str(F),'envWhitelist':{k:env[k] for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','TMPDIR','TMP','TEMP','XDG_CACHE_HOME','CCACHE_DIR']},'watchdogSeconds':180,'normalBuild':False,'APPExecution':False,'objectsSeeded':0})
start=time.monotonic();utc=datetime.datetime.now(datetime.timezone.utc).isoformat();timed=False
with (T/'logs/build.stdout').open('xb') as out,(T/'logs/build.stderr').open('xb') as err:
 proc=subprocess.Popen(cmd,cwd=F,env=env,stdout=out,stderr=err,start_new_session=True)
 j(T/'logs/build-start.json',{'pid':proc.pid,'startUTC':utc,'startTicks':pathlib.Path('/proc/'+str(proc.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]})
 print('PRIVATE BUILD START',proc.pid,flush=True)
 try:rc=proc.wait(timeout=180)
 except subprocess.TimeoutExpired:
  timed=True;os.killpg(proc.pid,signal.SIGTERM)
  try:rc=proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
result={'exitCode':rc,'timedOut':timed,'elapsedSeconds':time.monotonic()-start,'startUTC':utc,'endUTC':datetime.datetime.now(datetime.timezone.utc).isoformat()}
j(T/'logs/build-result.json',result)
after=controls();j(T/'guards/after.json',after)
runtimeAfter=runtimes();j(T/'guards/runtime-after.json',runtimeAfter)
frozenAfter=frozen();j(T/'guards/frozen-after.json',frozenAfter)
assert before==after,'source/pair/Main/IT changed during build'
assert runtimeBefore==runtimeAfter,'owned process identities changed during build'
rootAfter=rootWatch();j(T/'guards/root2308-after.json',rootAfter)
assert rootBefore==rootAfter,'Root source watch changed during build'
j(T/'guards/git-readonly-after.json',scope['gitSnapshot']())
j(T/'guards/IT-dist-readonly-after.json',scope['distObservation']())
assert frozenBefore==frozenAfter
assert {'path':str(bridge),'link':os.readlink(bridge),'resolved':str(bridge.resolve()),'created':False}==bridgeBefore
j(T/'guards/of-bridge-after.json',bridgeBefore)
assert all(h(F/n)==v['private'] for n,v in cohort.items())
for rel in cohort:
 d=T/'source/cohort'/rel;d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(F/rel,d)
if rc==0:
 app=F/'platforms/linux64GccDPInt32Opt/bin/dcCartMesh';lib=F/'platforms/linux64GccDPInt32Opt/lib/libdcCartMesh.so';assert app.is_file() and lib.is_file()
 libenv=env.copy();libenv['LD_LIBRARY_PATH']=str(lib.parent)+':'+str(OF/'lib/sys-mpich')+':'+str(OF/'lib')
 for name,argv in [('APP-readelf',['readelf','-d',str(app)]),('LIB-readelf',['readelf','-d',str(lib)]),('APP-file',['file',str(app)]),('LIB-file',['file',str(lib)]),('APP-ldd',['ldd',str(app)]),('LIB-ldd',['ldd',str(lib)])]:
  q=subprocess.run(argv,env=libenv,capture_output=True,timeout=15);(T/'elf'/f'{name}.stdout').write_bytes(q.stdout);(T/'elf'/f'{name}.stderr').write_bytes(q.stderr);j(T/'elf'/f'{name}.json',{'argv':argv,'exitCode':q.returncode,'LD_LIBRARY_PATH':libenv['LD_LIBRARY_PATH'],'scope':'ELF dependency inspection only; no APP/native case invocation'})
 assert str(lib) in (T/'elf/APP-ldd.stdout').read_text()
 assert 'not found' not in (T/'elf/APP-ldd.stdout').read_text() and 'not found' not in (T/'elf/LIB-ldd.stdout').read_text()
 shutil.copy2(app,T/'elf/dcCartMesh');shutil.copy2(lib,T/'elf/libdcCartMesh.so')
 j(T/'elf/paired-identities.json',{'APP':{'path':str(app),**h(app)},'LIB':{'path':str(lib),**h(lib)},'normalPair':before['pair'],'resolvedPrivateLibrary':str(lib),'actualNativeRun':False})
objects={str(p.relative_to(F)):h(p) for p in sorted(F.rglob('*.o'))}
j(T/'source/fresh-object-inventory.json',{'objectsSeeded':0,'sourceCloneWasExclusiveEmpty':True,'objects':objects,'objectCount':len(objects),'sourceCohort':cohort,'compileRaw':str(T/'logs/build.stdout'),'sixUnitsExpected':['dcCartGrid.C','dcCartOctree.C','dcCartSurfaces.C','dcCartMeshBuilder.C','dcCartCutCells.C','dcCartMesh.C']})
if rc==0:assert len(objects)==6,'Fresh actual object count must be six'
print('PRIVATE BUILD END',json.dumps(result),flush=True)

files={str(p.relative_to(T)):h(p) for p in sorted(T.rglob('*')) if p.is_file() and 'native' not in p.relative_to(T).parts and 'tmp' not in p.relative_to(T).parts and 'cache' not in p.relative_to(T).parts}
j(T/'manifest.json',{'files':files,'fileCount':len(files),'payloadBytes':sum(v['bytes'] for v in files.values()),'scope':'Actual fresh private bounded-eight standard build only; no APP/checker case execution','workingTree':str(F),'objectsSeeded':0})
for p in T.rglob('*'):
 if p.is_file() and 'native' not in p.relative_to(T).parts and 'tmp' not in p.relative_to(T).parts and 'cache' not in p.relative_to(T).parts:p.chmod(0o444)
