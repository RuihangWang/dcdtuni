"""Private source/controlled UI/literal API + CPUUSD tests; no live SDK APIs."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,signal,time
F=Path(__file__).resolve().parent
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
a=argparse.ArgumentParser();a.add_argument('--output',required=True);a.add_argument('--cpu',type=int,default=30);args=a.parse_args()
assert args.cpu in (30,31,38,39), 'CPU outside independent/Root reviewed pools'
manifest=json.loads((F/'MANIFEST.json').read_text())
for row in manifest['files']:
 p=F/row['path'];assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],str(p)
for row in json.loads((F/'EXTERNAL-GUARDS.json').read_text())['rows']:
 p=Path(row['path']);assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],str(p)
O=Path(args.output).resolve();assert O.is_relative_to(B/'.codex-tmp') and not O.exists() and not O.is_relative_to(F)
O.mkdir(exist_ok=False)
for name in ('candidate','before','tests','harness'):shutil.copytree(F/name,O/name)
for p in O.rglob('*'):
 if p.is_file():p.chmod(0o644)
for name in ('logs','cache','tmp'):(O/name).mkdir()
for p in (O/'candidate').rglob('*.py'):compile(p.read_text(),str(p),'exec')
env=dict(os.environ)
for key in ('LD_PRELOAD','LD_AUDIT','PYTHONPATH'):env.pop(key,None)
env.update(CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(O/'tmp'),TMP=str(O/'tmp'),TEMP=str(O/'tmp'),XDG_CACHE_HOME=str(O/'cache'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PXR_WORK_THREAD_LIMIT='1')
K=B/'src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release';U=Path('/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.usd.libs-6354e1268d43fec9')
native=dict(env,PYTHONPATH=str(U),LD_LIBRARY_PATH=':'.join([str(U/'bin'),str(K/'kit'),str(K/'kit/python/lib')]))
py='/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python';sdkpy=str(K/'kit/python/bin/python3');rows=[]
def run(label,argv,e,expected=0):
 cmd=['/usr/bin/taskset','-c',str(args.cpu),*argv];start=time.monotonic();timed=False
 with (O/'logs'/f'{label}.stdout').open('xb')as so,(O/'logs'/f'{label}.stderr').open('xb')as se:
  p=subprocess.Popen(cmd,cwd=O,env=e,stdout=so,stderr=se,start_new_session=True)
  try:p.wait(timeout=115)
  except subprocess.TimeoutExpired:
   timed=True
   if p.poll()is None and os.getpgid(p.pid)==p.pid:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=2)
    except subprocess.TimeoutExpired:
     if p.poll()is None and os.getpgid(p.pid)==p.pid:os.killpg(p.pid,signal.SIGKILL)
     p.wait(timeout=2)
 row=dict(label=label,argv=cmd,rc=p.returncode,expectedRC=expected,timedOut=timed,elapsedSeconds=time.monotonic()-start)
 (O/'logs'/f'{label}.status.json').write_text(json.dumps(row,indent=2)+'\n');rows.append(row);print(json.dumps(row),flush=True)
 assert not timed and p.returncode==expected,label
T=O/'candidate/src/frontend/omniverse/cfd_demo/tests'
run('portable-owned',[py,str(T/'test_owned_output_layout.py')],env)
run('literal-public-delegate',[py,str(O/'tests/actual_public_delegate.py')],env)
run('portable-F2-regression',[py,str(T/'test_render_output_observations.py')],env)
for case in ('duck','late-owner'):
 for mode,expected in (('before',1),('candidate',0)):
  run(case+'-'+mode,[sdkpy,str(O/'tests/literal_api_cpu_usd_negative.py'),mode,case],native,expected)
run('stable-local-lease',[sdkpy,str(O/'tests/coordinator_stable_lease.py'),'candidate'],native)
run('actual-IExt-common-grid',[sdkpy,str(O/'tests/actual_extension_common_grid.py')],native)
for row in json.loads((F/'EXTERNAL-GUARDS.json').read_text())['rows']:
 p=Path(row['path']);assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],str(p)
(O/'SUMMARY.json').write_text(json.dumps({'scope':'PRIVATE source CPU and controlled carriers only; no live UI/Kit/GPU/media/pixels','steps':rows,'canonicalGridStateRoute':True,'pixelVerified':False,'frameSourceBindingProof':'unresolved','positiveProductionStreamTransform':False},indent=2)+'\n')
