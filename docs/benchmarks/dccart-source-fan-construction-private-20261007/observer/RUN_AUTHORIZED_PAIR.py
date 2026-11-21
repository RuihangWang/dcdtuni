from pathlib import Path
import json,os,hashlib,subprocess,time,datetime,shlex,re,signal
T=Path(__file__).resolve().parent
O=T.parent/'genuine-wall-earliest-discovery-observer-private-20261007'
Q=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/physical-point-genuine-producer-root-20261007/actual-native')
N=T/'native/dcCartMesh'
APP=N/'platforms/linux64GccDPInt32Opt/bin/dcCartMesh'
LIB=N/'platforms/linux64GccDPInt32Opt/lib/libdcCartMesh.so'
def info(p): return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size)
baseenv={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_'))}
baseenv.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'))
pair=dict(app=info(APP),lib=info(LIB),scope='PRIVATE own fresh APP/LIB; no normal binary overwritten')
for name,p in [('APP',APP),('LIB',LIB)]:
 for mode,args in [('readelf',['readelf','-d',str(p)]),('file',['file',str(p)])]:
  cp=subprocess.run(args,capture_output=True,env=baseenv,timeout=10)
  (T/f'logs/{name}.{mode}.stdout').write_bytes(cp.stdout)
  (T/f'logs/{name}.{mode}.stderr').write_bytes(cp.stderr)
  (T/f'logs/{name}.{mode}.json').write_text(json.dumps(dict(argv=args,returncode=cp.returncode),indent=2)+'\n')
(T/'logs/private-pair.json').write_text(json.dumps(pair,indent=2)+'\n')
flags0=dict(DCCART_CONFORMING_CONES='1',DCCART_CONE_LITERAL_UNIONS='1',DCCART_CONE_SAME_PHYSICAL_ROLE='1',DCCART_CROSS_PARENT_WEIGHT_UNIONS='1',DCCART_COMPLETE_CAVITY_CONTEXT='1',DCCART_COMPLETE_CAVITY_REPAIR='1')
allresults={}
for kind in ('ON','OFF'):
 flags=dict(flags0)
 if kind=='ON': flags['DCCART_EARLIEST_PHYSICAL_POINT_REPAIR']='1'
 case=T/f'cases/{kind}/Mesh'
 assert not (T/f'logs/{kind}.json').exists(), 'Do not overwrite actual run receipt'
 assert not (case/'constant/polyMesh').exists(), 'Only unrun private prepared case allowed'
 env=dict(baseenv);env.update(flags)
 script='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nexport LD_DEBUG=libs\nexec '+shlex.join([str(APP),'-case',str(case),'-overwrite','-timeLimit','110'])+'\n'
 argv=['taskset','-c','10','bash','--noprofile','--norc','-c',script]
 rawbefore=info(T/f'cases/{kind}/model/source.cfdrun')
 start=time.monotonic(); utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (T/f'logs/{kind}.stdout').open('xb') as so,(T/f'logs/{kind}.stderr').open('xb') as se:
  proc=subprocess.Popen(argv,cwd=case,env=env,stdout=so,stderr=se,start_new_session=True)
  timed=False
  try: rc=proc.wait(timeout=120)
  except subprocess.TimeoutExpired:
   timed=True;os.killpg(proc.pid,signal.SIGTERM)
   try: rc=proc.wait(timeout=5)
   except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
 seconds=time.monotonic()-start
 out=(T/f'logs/{kind}.stdout').read_text(errors='replace')
 err=(T/f'logs/{kind}.stderr').read_text(errors='replace')
 geom=[json.loads(line.split('PRIVATE physical-point geometry observation: ',1)[1]) for line in out.splitlines() if 'PRIVATE physical-point geometry observation: ' in line]
 obs=[line for line in out.splitlines() if line.startswith(('PRIVATE physical-point','Normal complete-cavity lineage:','Total '))]
 meshes={}
 for p in sorted((case/'constant/polyMesh').glob('*')):
  if p.is_file():
   ref=Q/f'cases/normal-{kind}/Mesh/constant/polyMesh/{p.name}'
   meshes[p.name]=dict(**info(p),referenceSHA256=info(ref)['sha256'] if ref.exists() else None,byteEqual=ref.exists() and p.read_bytes()==ref.read_bytes())
 old=json.loads((O/f'logs/{kind}.json').read_text())
 oldseed=[x for x in old['observations'] if ' capture observation: ' in x]
 nextseed=[x for x in obs if ' capture observation: ' in x]
 loader=any('calling init: '+str(LIB) in line for line in err.splitlines())
 result=dict(argv=argv,nativeArgv=[str(APP),'-case',str(case),'-overwrite','-timeLimit','110'],cwd=str(case),flags=flags,CLISeconds=110,watchdogSeconds=120,pid=proc.pid,pair=pair,utcStart=utc,utcStop=datetime.datetime.now(datetime.timezone.utc).isoformat(),returncode=rc,timedOut=timed,seconds=seconds,observations=obs,geometryRecordCount=len(geom),geometryRecordBytes=sum(len(line.encode()) for line in out.splitlines() if 'PRIVATE physical-point geometry observation: ' in line),allGeometryRecordsComplete=all(x.get('observationComplete') for x in geom),privateLIBRuntimeInit=loader,meshFiles=meshes,originalSeedAndRefusalSequenceByteEqual=oldseed==nextseed,rawInputHeld=rawbefore==info(T/f'cases/{kind}/model/source.cfdrun'))
 (T/f'logs/{kind}.geometry.json').write_text(json.dumps(geom,indent=2)+'\n')
 (T/f'logs/{kind}.json').write_text(json.dumps(result,indent=2)+'\n')
 allresults[kind]=result
 print(kind,json.dumps({k:result[k] for k in ('returncode','timedOut','seconds','geometryRecordCount','geometryRecordBytes','allGeometryRecordsComplete','privateLIBRuntimeInit','originalSeedAndRefusalSequenceByteEqual','rawInputHeld')}),flush=True)
 print('mesh',len(meshes),all(x['byteEqual'] for x in meshes.values()),flush=True)
 print('\n'.join(obs[:3]+obs[-3:]),flush=True)
(T/'logs/pair-summary.json').write_text(json.dumps({k:{f:v[f] for f in ('returncode','timedOut','seconds','geometryRecordCount','geometryRecordBytes','allGeometryRecordsComplete','privateLIBRuntimeInit','originalSeedAndRefusalSequenceByteEqual','rawInputHeld')} for k,v in allresults.items()},indent=2)+'\n')
