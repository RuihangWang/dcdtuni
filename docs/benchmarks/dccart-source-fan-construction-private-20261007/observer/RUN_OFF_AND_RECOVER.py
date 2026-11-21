from pathlib import Path
import json,os,hashlib,subprocess,time,datetime,shlex,re,signal
T=Path(__file__).resolve().parent
O=T.parent/'genuine-wall-earliest-discovery-observer-private-20261007'
Q=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/physical-point-genuine-producer-root-20261007/actual-native')
N=T/'native/dcCartMesh'; APP=N/'platforms/linux64GccDPInt32Opt/bin/dcCartMesh'; LIB=N/'platforms/linux64GccDPInt32Opt/lib/libdcCartMesh.so'
def info(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
flags=dict(DCCART_CONFORMING_CONES='1',DCCART_CONE_LITERAL_UNIONS='1',DCCART_CONE_SAME_PHYSICAL_ROLE='1',DCCART_CROSS_PARENT_WEIGHT_UNIONS='1',DCCART_COMPLETE_CAVITY_CONTEXT='1',DCCART_COMPLETE_CAVITY_REPAIR='1')
env={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_'))}
env.update(flags)
env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'))
case=T/'cases/OFF/Mesh'
assert not (T/'logs/OFF.json').exists() and not (case/'constant/polyMesh').exists()
script='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nexport LD_DEBUG=libs\nexec '+shlex.join([str(APP),'-case',str(case),'-overwrite','-timeLimit','110'])+'\n'
argv=['taskset','-c','10','bash','--noprofile','--norc','-c',script]
receipt=dict(argv=argv,nativeArgv=[str(APP),'-case',str(case),'-overwrite','-timeLimit','110'],cwd=str(case),flags=flags,CLISeconds=110,watchdogSeconds=120,pair=json.loads((T/'logs/private-pair.json').read_text()),utcStart=datetime.datetime.now(datetime.timezone.utc).isoformat())
start=time.monotonic()
with (T/'logs/OFF.stdout').open('xb') as so,(T/'logs/OFF.stderr').open('xb') as se:
 proc=subprocess.Popen(argv,cwd=case,env=env,stdout=so,stderr=se,start_new_session=True);receipt['pid']=proc.pid
 timed=False
 try:rc=proc.wait(timeout=120)
 except subprocess.TimeoutExpired:
  timed=True;os.killpg(proc.pid,signal.SIGTERM)
  try:rc=proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
receipt.update(returncode=rc,timedOut=timed,seconds=time.monotonic()-start,utcStop=datetime.datetime.now(datetime.timezone.utc).isoformat())
(T/'logs/OFF.json').write_text(json.dumps(receipt,indent=2)+'\n')
results={}
for kind in ('ON','OFF'):
 out=(T/f'logs/{kind}.stdout').read_text(errors='replace')
 err=(T/f'logs/{kind}.stderr').read_text(errors='replace')
 record=json.loads((T/f'logs/{kind}.json').read_text()) if (T/f'logs/{kind}.json').exists() else dict(collectorFailedAfterNative=True,nativeExitCodeNotPersisted=True,argv=['same RUN_AUTHORIZED_PAIR.py actual argv; see stored script'],scope='ON native run completed before parsing exception; not rerun')
 result=dict(record)
 result['nativeTotalSeconds']=float(re.search(r'^Total ([0-9.e+-]+) s$',out,re.M)[1])
 result['nativeCompletionEnd']=bool(re.search(r'^End$',out,re.M))
 result['pair']=json.loads((T/'logs/private-pair.json').read_text())
 result['privateLIBRuntimeInit']=any('calling init: '+str(LIB) in x for x in err.splitlines())
 result['privateNativePIDFromLoader']=re.search(r'^\s*(\d+):\s*calling init: '+re.escape(str(LIB)),err,re.M)[1]
 oldout=(O/f'logs/{kind}.stdout').read_text(errors='replace')
 captures=lambda s:re.findall(r'PRIVATE physical-point capture observation: rank (\d+), original point (\d+), existing refusal (\d+)',s)
 result['originalSeedAndRefusalSequenceExact']=captures(out)==captures(oldout)
 result['captures']=captures(out)
 result['originalDiscoveryCountsExact']=re.findall(r'complete/badCells/physicalPoints/seeds (\S+)',out)==re.findall(r'complete/badCells/physicalPoints/seeds (\S+)',oldout)
 result['originalCapturedStagedCommittedExact']=re.findall(r'captured/staged/committed (\S+)',out)==re.findall(r'captured/staged/committed (\S+)',oldout)
 result['geometryRecordCount']=out.count('PRIVATE physical-point geometry observation: ')
 result['observations']=[line.lstrip('"') for line in out.splitlines() if 'PRIVATE physical-point' in line and 'geometry observation' not in line or line.startswith(('Normal complete-cavity lineage:','Total '))]
 result['meshFiles']={}
 for p in sorted((T/f'cases/{kind}/Mesh/constant/polyMesh').iterdir()):
  if p.is_file():
   ref=Q/f'cases/normal-{kind}/Mesh/constant/polyMesh/{p.name}'
   result['meshFiles'][p.name]=dict(**info(p),referenceSHA256=sha(ref),byteEqual=p.read_bytes()==ref.read_bytes())
 result['rawInputHeld']=sha(T/f'cases/{kind}/model/source.cfdrun')=='bbd9daefecc28cb48e2e5d9d21a9d31abdf8f73cf5d171c950ecc94b4bae0c62'
 (T/f'logs/{kind}.recovered-analysis.json').write_text(json.dumps(result,indent=2)+'\n')
 results[kind]=result
 print(kind,json.dumps({k:result[k] for k in ('nativeTotalSeconds','nativeCompletionEnd','privateLIBRuntimeInit','originalSeedAndRefusalSequenceExact','originalDiscoveryCountsExact','originalCapturedStagedCommittedExact','geometryRecordCount','rawInputHeld')}),flush=True)
 print('meshFileCount',len(result['meshFiles']),'allByteEqual',all(v['byteEqual'] for v in result['meshFiles'].values()),flush=True)
(T/'logs/paired-run-analysis.json').write_text(json.dumps(results,indent=2)+'\n')
