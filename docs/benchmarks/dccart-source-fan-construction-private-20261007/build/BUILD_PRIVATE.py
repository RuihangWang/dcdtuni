import pathlib,json,hashlib,os,shutil,subprocess,time,signal,datetime
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni');A=B/'.codex-tmp/goal-20261005/air';N=A/'src/backend/aircooling/third_parties/dcCartMesh';P=A/'.codex-tmp/physical-source-fan-constant-axis-construction-private-20261007';T=P/'build-proof-v1';F=P/'native/dcCartMesh';OF=pathlib.Path('/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni')
def h(p):
 s=hashlib.sha256()
 with open(p,'rb') as f:
  for c in iter(lambda:f.read(1<<20),b''):s.update(c)
 return {'sha256':s.hexdigest(),'bytes':pathlib.Path(p).stat().st_size}
def j(p,o):p.write_text(json.dumps(o,indent=2)+'\n')
G=json.loads((A/'.codex-tmp/genuine-wall-earliest-source-geometry-observer-private-v2-20261007/guards/after.json').read_text())
def controls():return {k:{p:h(p) for p in G[k]} for k in ['source39','Main29','pair']}
before=controls();j(T/'guards/before.json',before)
for k in before:
 for p,v in before[k].items():assert v==G[k][p],(k,p)
manifest=json.loads((P/'manifest.json').read_text());assert all(h(P/p)==v for p,v in manifest['files'].items())
bridge=OF/'src';assert bridge.is_symlink() and (bridge/'meshTools/lnInclude').is_dir()
j(T/'guards/of-bridge.json',{'path':str(bridge),'link':os.readlink(bridge),'resolved':str(bridge.resolve()),'created':False})
cohort={}
for p,v in before['source39'].items():
 rel=pathlib.Path(p).relative_to(N);dst=F/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
 if dst.is_symlink():raise RuntimeError('non-value source copy')
 os.chmod(dst,0o644 if dst.suffix in ['.C','.H'] else dst.stat().st_mode&0o777)
 cohort[str(rel)]={'original':p,'before':v,'private':h(dst)}
crel=pathlib.Path('src/mesh/dcCartMesh/dcCartCutCells/dcCartCutCells.C');shutil.copy2(P/'source/dcCartCutCells.C.after',F/crel);os.chmod(F/crel,0o644);cohort[str(crel)]['private']=h(F/crel)
assert cohort[str(crel)]['private']['sha256']=='7bd6dd05e48d483d9e103dbb27eec1948c1b34fa520f321ea914307e18b33bef'
assert sum(v['before']!=v['private'] for v in cohort.values())==1
j(T/'source/private-source39.json',cohort)
objects={}
rels=['applications/utilities/mesh/generation/dcCartMesh/Make/linux64GccDPInt32Opt/dcCartMesh.o']+['src/mesh/dcCartMesh/Make/linux64GccDPInt32Opt/'+q+'/'+q+'.o' for q in ['dcCartGrid','dcCartSurfaces','dcCartMeshBuilder','dcCartOctree']]
for rel in rels:
 src=N/rel;dst=F/rel;assert src.is_file();dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst);assert src.stat().st_ino!=dst.stat().st_ino;objects[rel]={'source':str(src),'binding':h(src),'copied':h(dst),'method':'copy2 by value; normal source39 and pair held; unchanged translation unit source identity'}
assert not (F/'src/mesh/dcCartMesh/Make/linux64GccDPInt32Opt/dcCartCutCells/dcCartCutCells.o').exists()
j(T/'objects/by-value-bindings.json',objects)
for p in F.rglob('*'):
 if p.is_symlink():raise RuntimeError('symlink present before build '+str(p))
env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'))
cmd=['taskset','-c','10,11','bash','--noprofile','--norc','-c','source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nexec bash ./Allwmake --cart-only -j2']
j(T/'logs/build-command.json',{'argv':cmd,'cwd':str(F),'envWhitelist':{k:env[k] for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','TMPDIR','TMP','TEMP','XDG_CACHE_HOME','CCACHE_DIR']},'watchdogSeconds':180,'normalBuild':False,'APPExecution':False})
start=time.monotonic();utc=datetime.datetime.now(datetime.timezone.utc).isoformat();timed=False
with open(T/'logs/build.stdout','wb') as out,open(T/'logs/build.stderr','wb') as err:
 proc=subprocess.Popen(cmd,cwd=F,env=env,stdout=out,stderr=err,start_new_session=True)
 j(T/'logs/build-start.json',{'pid':proc.pid,'startUTC':utc,'startTicks':pathlib.Path('/proc/'+str(proc.pid)+'/stat').read_text().split()[21]})
 print('PRIVATE BUILD START',proc.pid,flush=True)
 try:rc=proc.wait(timeout=180)
 except subprocess.TimeoutExpired:
  timed=True;os.killpg(proc.pid,signal.SIGTERM)
  try:rc=proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
j(T/'logs/build-result.json',{'exitCode':rc,'timedOut':timed,'elapsedSeconds':time.monotonic()-start,'startUTC':utc,'endUTC':datetime.datetime.now(datetime.timezone.utc).isoformat()})
after=controls();j(T/'guards/after.json',after);assert after==before
j(T/'guards/frozen13-after.json',{'manifestSHA':h(P/'manifest.json'),'files':{p:h(P/p) for p in manifest['files']},'exact':all(h(P/p)==v for p,v in manifest['files'].items())})
assert all(h(P/p)==v for p,v in manifest['files'].items())
assert all(h(F/p)==v['private'] for p,v in cohort.items())
if rc==0:
 app=F/'platforms/linux64GccDPInt32Opt/bin/dcCartMesh';lib=F/'platforms/linux64GccDPInt32Opt/lib/libdcCartMesh.so';assert app.exists() and lib.exists()
 libenv=env.copy();libenv['LD_LIBRARY_PATH']=str(lib.parent)+':'+str(OF/'lib/sys-mpich')+':'+str(OF/'lib')
 for name,argv in [('APP-readelf',['readelf','-d',str(app)]),('LIB-readelf',['readelf','-d',str(lib)]),('APP-file',['file',str(app)]),('LIB-file',['file',str(lib)]),('APP-ldd',['ldd',str(app)]),('LIB-ldd',['ldd',str(lib)])]:
  q=subprocess.run(argv,env=libenv,capture_output=True,timeout=15);(T/'elf'/f'{name}.stdout').write_bytes(q.stdout);(T/'elf'/f'{name}.stderr').write_bytes(q.stderr);j(T/'elf'/f'{name}.json',{'argv':argv,'exitCode':q.returncode,'LD_LIBRARY_PATH':libenv['LD_LIBRARY_PATH'],'scope':'static ELF/dynamic-loader dependency inspection; no APP mesh invocation'})
 assert str(lib) in (T/'elf/APP-ldd.stdout').read_text()
 j(T/'elf/paired-identities.json',{'APP':{'path':str(app),**h(app)},'LIB':{'path':str(lib),**h(lib)},'normalPair':before['pair'],'resolvedPrivateLibrary':str(lib),'actualNativeRun':False})
print('PRIVATE BUILD END',rc,'TIMEOUT',timed,flush=True)
