import argparse,pathlib,json,hashlib,os,subprocess,time,signal
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve();F=pathlib.Path(__file__).resolve().parent
a=argparse.ArgumentParser();a.add_argument('--source-root',required=True);a.add_argument('--output',required=True)
a.add_argument('--node-bin',required=True);a.add_argument('--node-modules',required=True);a.add_argument('--python',required=True)
a.add_argument('--owned-kit-root',required=True);a.add_argument('--kit-python',required=True);args=a.parse_args()
assert set(os.sched_getaffinity(0))<={36,37},'use taskset -c 36-37'
S=pathlib.Path(args.source_root).resolve();O=pathlib.Path(args.output).resolve()
assert S.is_relative_to(B)and O.is_relative_to(B/'.codex-tmp')and not O.exists()and O!=S and not O.is_relative_to(S)
def sha(b):return hashlib.sha256(b).hexdigest()
manifest=json.loads((F/'MANIFEST.json').read_text())
for r in manifest['files']:
 p=F/r['path'];assert p.is_file()and not p.is_symlink()and p.stat().st_size==r['bytes']and sha(p.read_bytes())==r['sha256'],r['path']
records=json.loads((F/'SOURCE-CLOSURE.json').read_text())['records']
for r in records:
 rel=pathlib.Path(r['path']);assert not rel.is_absolute()and'..'not in rel.parts and not(set(rel.parts)&{'node_modules','.codex-tmp','dist','.vite'})
 p=S/rel;assert p.resolve().is_relative_to(S)and p.is_file()
 assert p.stat().st_size==r['bytes']and sha(p.read_bytes())==r['sha256'],r['path']
targets=json.loads((F/'SOURCE-INDEX.json').read_text())['targets']
for r in targets:
 p=S/r['path']
 if r['beforeExists']:assert p.is_file()and sha(p.read_bytes())==r['beforeSHA256']
 else:assert not p.exists()
M=pathlib.Path(args.node_modules).resolve();N=pathlib.Path(args.node_bin).resolve()/'node';E=pathlib.Path(args.python).resolve();K=pathlib.Path(args.kit_python).resolve();D=pathlib.Path(args.owned_kit_root).resolve()
assert all(p.is_relative_to(B)for p in [M,N,E,K,D])and K.is_relative_to(D)
O.mkdir();T=O/'reviewtree';(O/'logs').mkdir();(O/'tmp').mkdir()
for r in records:
 dst=T/r['path'];dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes((S/r['path']).read_bytes())
for r in targets:
 if r['kind']=='test':
  dst=T/r['path'];dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes((F/'after'/r['path']).read_bytes())
FE=T/'src/frontend/react';PT=T/'src/frontend/omniverse/cfd_demo/tests';(FE/'node_modules').symlink_to(M,target_is_directory=True)
env=dict(os.environ,CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(O/'tmp'),TEMP=str(O/'tmp'),TMP=str(O/'tmp'),GCOV_PREFIX=str(O/'tmp/gcov'),GCOV_PREFIX_STRIP='0',XDG_DATA_HOME=str(O/'tmp/xdg-data'),XDG_CACHE_HOME=str(O/'tmp/xdg-cache'),XDG_CONFIG_HOME=str(O/'tmp/xdg-config'),OMP_NUM_THREADS='2')
results=[]
def live(pid):
 try:
  s=pathlib.Path(f'/proc/{pid}/stat').read_text();f=s[s.rfind(')')+2:].split()
  return dict(pid=pid,start=f[19],pgid=int(f[2]),sid=int(f[3]),uid=pathlib.Path(f'/proc/{pid}').stat().st_uid)
 except(FileNotFoundError,ProcessLookupError):return None
def cleanup(lease):
 members=[]
 for q in pathlib.Path('/proc').iterdir():
  if q.name.isdecimal():
   r=live(int(q.name))
   if r and r['pgid']==lease['pgid']:
    assert r['uid']==lease['uid']and r['sid']==lease['sid'];members.append(r)
 if not members:return
 leader=live(lease['pid'])
 if leader:assert leader==lease
 try:os.killpg(lease['pgid'],signal.SIGTERM)
 except ProcessLookupError:return
 deadline=time.monotonic()+2
 while time.monotonic()<deadline:
  remaining=[r for r in [live(m['pid'])for m in members]if r and r['pgid']==lease['pgid']and r['sid']==lease['sid']and r['uid']==lease['uid']]
  if not remaining:return
  time.sleep(.05)
 try:os.killpg(lease['pgid'],signal.SIGKILL)
 except ProcessLookupError:pass
def run(name,argv,cwd,expected,childenv=env):
 start=time.monotonic();timed=False
 with(O/'logs'/f'{name}.stdout').open('wb')as out,(O/'logs'/f'{name}.stderr').open('wb')as err:
  p=subprocess.Popen(argv,cwd=cwd,env=childenv,stdout=out,stderr=err,start_new_session=True);lease=live(p.pid);assert lease and lease['pgid']==p.pid and lease['sid']==p.pid
  try:
   try:rc=p.wait(timeout=60)
   except subprocess.TimeoutExpired:timed=True;cleanup(lease);rc=p.wait(timeout=3)
  finally:
   cleanup(lease)
   if p.poll()is None:p.wait(timeout=3)
 r=dict(name=name,argv=argv,cwd=str(cwd),rc=rc,timeout=timed,elapsedSeconds=time.monotonic()-start,lease=lease,expected=expected)
 (O/(name+'.json')).write_text(json.dumps(r,indent=2)+'\n');results.append(r);print(json.dumps(r),flush=True)
 assert not timed and ((rc==0)if expected=='PASS'else rc!=0),name
h='src/frontend/omniverse/cfd_demo/tests/test_dataset_scene_race.py';rt='src/frontend/omniverse/cfd_demo/tests/test_render_command_routing.py'
try:
 run('baseline-fe',[str(N),str(M/'vitest/vitest.mjs'),'run','test/cameraPurposeProtocol.test.ts','test/cameraReadbackMounted.test.ts','--maxWorkers=2','--minWorkers=2'],FE,'RED')
 run('baseline-py',[str(E),'-B','-m','pytest','-q',str(PT/'test_camera_handoff_purpose.py'),'-p','no:cacheprovider','--basetemp',str(O/'tmp/baseline-py')],PT,'RED')
 for rel in [h,rt]:(T/rel).write_bytes((F/'before'/rel).read_bytes())
 run('original-routing-baseline',[str(E),'-B','-m','pytest','-q',str(PT/'test_render_command_routing.py'),'-p','no:cacheprovider','--basetemp',str(O/'tmp/routing-baseline')],PT,'RED')
finally:
 for r in targets:(T/r['path']).write_bytes((F/'after'/r['path']).read_bytes())
run('candidate-fe',[str(N),str(M/'vitest/vitest.mjs'),'run','test/cameraPurposeProtocol.test.ts','test/cameraReadbackMounted.test.ts','test/cameraReadbackScope.test.ts','test/RendererControl.test.ts','test/ovProtocol.test.ts','test/nativeGridAckTransport.test.ts','test/ovReturnDeadline.test.tsx','--maxWorkers=2','--minWorkers=2'],FE,'PASS')
run('candidate-py',[str(E),'-B','-m','pytest','-q',*[str(PT/n)for n in ['test_camera_handoff_purpose.py','test_render_command_routing.py','test_dataset_scene_race.py','test_native_grid_ack_json_transport.py']],'-p','no:cacheprovider','--basetemp',str(O/'tmp/candidate-py')],PT,'PASS')
old=json.loads((F/'OFFICIAL-KIT-CPU-ENV-REFERENCE.json').read_text());kenv=dict(env,**old['environment'])
for v in old['environment'].values():
 for path in str(v).split(':'):
  if path.startswith('/'):assert pathlib.Path(path).resolve().is_relative_to(D)
run('owned-kit-usd',[str(K),'-B',str(F/'RUN_KIT_CPU.py'),'--review-root',str(T),'--owned-root',str(D)],O,'PASS',kenv)
cenv=dict(kenv);cenv['PYTHONPATH']=str(D/'src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release/kit/kernel/py')+':'+cenv['PYTHONPATH']
ext='src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py'
run('real-carb-v1-red',[str(K),'-B',str(F/'RUN_ACTUAL_CARB_FLUSH.py'),'--extension',str(F/'rejected-v1-extension.py')],O,'RED',cenv)
run('real-carb-v2-pass',[str(K),'-B',str(F/'RUN_ACTUAL_CARB_FLUSH.py'),'--extension',str(T/ext)],O,'PASS',cenv)
run('candidate-types',[str(N),str(M/'typescript/bin/tsc'),'--noEmit'],FE,'PASS')
held=[]
for r in records:
 assert sha((S/r['path']).read_bytes())==r['sha256'],r['path'];held.append(dict(path=r['path'],sha256=r['sha256'],held=True))
(O/'SOURCE-HELD.json').write_text(json.dumps(held,indent=2)+'\n')
(O/'RESULTS.json').write_text(json.dumps(dict(status='PASS',results=results,CPU=sorted(os.sched_getaffinity(0)),CUDA_VISIBLE_DEVICES='',scope='controlled CPU client/route and officialUSD only; no browser, GPU, media, latency or nativeGrid encoded proof'),indent=2)+'\n')
