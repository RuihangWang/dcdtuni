from pathlib import Path
import argparse,datetime,hashlib,json,os,re,shlex,signal,subprocess,time
T=Path(__file__).resolve().parent.parent;P=T.parent;N=P/'native/dcCartMesh';OF=Path('/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni');Q=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/physical-point-genuine-producer-root-20261007/actual-native')
def info(p):return {'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest(),'bytes':Path(p).stat().st_size}
def write(p,v):
 with Path(p).open('x') as f:f.write(json.dumps(v,indent=2)+'\n')
def controls(template):return {k:{p:info(p) for p in template[k]} for k in template}
def bounded(name,argv,cwd,env,limit):
 start=time.monotonic();rec={'argv':argv,'cwd':str(cwd),'startUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'watchdogSeconds':limit,'environment':{k:env.get(k) for k in ('CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','TMPDIR','LD_DEBUG')},'nativeFlags':{k:v for k,v in env.items() if k.startswith(('DCCART_','CART_'))}}
 write(T/'logs'/f'{name}.started.json',rec)
 try:
  with (T/'logs'/f'{name}.stdout').open('xb') as so,(T/'logs'/f'{name}.stderr').open('xb') as se:
   proc=subprocess.Popen(argv,cwd=cwd,env=env,stdout=so,stderr=se,start_new_session=True);rec['pid']=proc.pid
   rec['startTicks']=Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19]
   timed=False
   try:rc=proc.wait(timeout=limit)
   except subprocess.TimeoutExpired:
    timed=True;os.killpg(proc.pid,signal.SIGTERM)
    try:rc=proc.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
   rec.update(returncode=rc,timedOut=timed)
 except BaseException as e:
  rec.update(collectorException=type(e).__name__+': '+str(e))
  raise
 finally:
  rec.update(seconds=time.monotonic()-start,stopUTC=datetime.datetime.now(datetime.timezone.utc).isoformat());write(T/'logs'/f'{name}.status.json',rec)
 return rec
def metadata_output_proof(kind):
 key='Mesh/constant/dcCartSourceRegions.json';src=T/'input'/key;dst=T/'cases'/kind/key
 old=json.loads(src.read_text());new=json.loads(dst.read_text())
 def stable(value):
  owned=json.loads(json.dumps(value));assert isinstance(owned,dict) and owned.get('format')=='dcdtuni.native-source-regions/v1'
  assert isinstance(owned.get('regions'),list)
  for row in owned['regions']:
   assert isinstance(row,dict) and isinstance(row.get('patchRanges'),list)
   for patch in row['patchRanges']:
    assert isinstance(patch,dict) and all(type(patch.get(k)) is int and patch[k]>=0 for k in ('patchIndex','startFace','nFaces'))
    del patch['startFace'];del patch['nFaces']
  return owned
 assert stable(old)==stable(new),'Source metadata schema/UID/inventory/roles changed beyond derived startFace/nFaces'
 raw=(T/'cases'/kind/'Mesh/constant/polyMesh/boundary').read_text();assert len(raw.encode())<=65536
 clean=re.sub(r'/\*.*?\*/','',raw,flags=re.S);clean=re.sub(r'//[^\n]*','',clean)
 match=re.search(r'\n\s*(\d+)\s*\(\s*(.*?)\s*\)\s*$',clean,re.S);assert match,'Unknown boundary dictionary list'
 count=int(match[1]);body=match[2];assert count<=10000
 pattern=re.compile(r'\s*("[^"\n]+"|[^\s{}()]+)\s*\{([^{}]*)\}',re.S)
 patches=[];pos=0
 for m in pattern.finditer(body):
  assert not body[pos:m.start()].strip(),'Unparsed boundary content';pos=m.end();name=m[1].strip('"');block=m[2]
  vals={}
  for key in ('startFace','nFaces'):
   values=re.findall(r'\b'+key+r'\s+(\d+)\s*;',block);assert len(values)==1;vals[key]=int(values[0])
  patches.append(dict(patchIndex=len(patches),patchName=name,**vals))
 assert len(patches)==count and not body[pos:].strip(),'Incomplete boundary parse'
 bound=[]
 for row in new['regions']:
  for patch in row['patchRanges']:
   assert patch['patchIndex']<len(patches);actual=patches[patch['patchIndex']];assert patch==actual,'Derived metadata does not match actual written boundary range'
   bound.append({'sourceRegionUid':row['sourceRegionUid'],'metadata':patch,'actualBoundary':actual})
 diffs=[]
 for i,(a,b) in enumerate(zip(old['regions'],new['regions'])):
  for q,(x,y) in enumerate(zip(a['patchRanges'],b['patchRanges'])):
   for field in ('startFace','nFaces'):
    if x[field]!=y[field]:diffs.append({'path':f'/regions/{i}/patchRanges/{q}/{field}','before':x[field],'after':y[field]})
 return {'nativeBefore':info(src),'nativeAfter':info(dst),'stableSchemaAndAllIdentityExact':True,'onlyAllowedDerivedScalarChanges':diffs,'boundaryFile':info(T/'cases'/kind/'Mesh/constant/polyMesh/boundary'),'completeBoundaryPatches':patches,'boundRows':bound,'source':'APP3098-3118 after mesh write; dcCartSourceRegions.H163-195 writes OFstream output, actual patch.start()/size() at185-188'}

parser=argparse.ArgumentParser();parser.add_argument('--execute-authorized',action='store_true');args=parser.parse_args()
if not args.execute_authorized:raise SystemExit('Prepared only: no execution without ROOT authorization')
plan=json.loads((T/'plan.json').read_text());manifest=json.loads((T/'manifest.json').read_text());assert all(info(T/p)==v for p,v in manifest['files'].items()),'Plan payload drift'
G=json.loads((T/'guards/preparation-controls.json').read_text());assert controls(G)==G;write(T/'guards/continuation-run-before.json',controls(G))
APP=Path(plan['pair']['APP']['path']);LIB=Path(plan['pair']['LIB']['path']);CHECK=Path(plan['stockCheckMesh']['path'])
assert info(APP)=={k:plan['pair']['APP'][k] for k in ['sha256','bytes']};assert info(LIB)=={k:plan['pair']['LIB'][k] for k in ['sha256','bytes']};assert info(CHECK)=={k:plan['stockCheckMesh'][k] for k in ['sha256','bytes']}
inputs=json.loads((T/'input-bindings.json').read_text());meshNames=('points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones')
base={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_'))};base.update(plan['sixLegacyFlags']);base.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'));summary={}
for kind in ('ON',):
 case=T/'cases'/kind/'Mesh';assert not (case/'constant/polyMesh').exists()
 for rel,v in inputs.items():assert info(T/'cases'/kind/rel)=={k:v[k] for k in ['sha256','bytes']}
 env=base.copy()
 if kind=='ON':env['DCCART_EARLIEST_PHYSICAL_POINT_REPAIR']='1'
 assert (kind=='OFF' and 'DCCART_EARLIEST_PHYSICAL_POINT_REPAIR' not in env) or env.get('DCCART_EARLIEST_PHYSICAL_POINT_REPAIR')=='1'
 native=[str(APP),'-case',str(case),'-overwrite','-timeLimit','110']
 shell='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nexport LD_DEBUG=libs\nexec '+shlex.join(native)+'\n';argv=['taskset','-c','10,11','bash','--noprofile','--norc','-c',shell]
 rec=bounded(kind+'.native',argv,case,env,120)
 # Persist raw status before interpreting or decoding any native output.
 out=(T/'logs'/f'{kind}.native.stdout').read_text(errors='replace');err=(T/'logs'/f'{kind}.native.stderr').read_text(errors='replace');loader=any('calling init: '+str(LIB) in x for x in err.splitlines());total=re.search(r'^Total ([0-9.e+-]+) s$',out,re.M)
 mesh={}
 for name in meshNames:
  p=case/'constant/polyMesh'/name
  if p.is_file():
   ref=Q/f'cases/normal-{kind}/Mesh/constant/polyMesh/{name}';mesh[name]={'path':str(p),**info(p),'referenceOriginal':info(ref),'byteEqualOriginal':p.read_bytes()==ref.read_bytes()}
 write(T/'logs'/f'{kind}.mesh-before-checker.json',mesh)
 nativeRelevant=[x for x in out.splitlines() if any(z in x for z in ('physical-point lifecycle','Normal complete-cavity lineage:','Normal complete-cavity repair:','postwriter coupled certificate:','Total '))]
 result={'nativeStatus':rec,'nativeTotalSeconds':float(total[1]) if total else None,'nativeEnd':bool(re.search(r'^End$',out,re.M)),'actualPrivateLIBInit':loader,'nativeRelevantLines':nativeRelevant,'meshFiles':mesh,'quality':'not checked'}
 if rec.get('returncode')==0 and len(mesh)==8:
  if not loader:raise RuntimeError('Private native library runtime initialization not proven; do not qualify this run')
  checkargv=[str(CHECK),'-case',str(case),'-allTopology','-allGeometry'];checkshell='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nunset LD_DEBUG\nexec '+shlex.join(checkargv)+'\n';crec=bounded(kind+'.checkMesh',['taskset','-c','10,11','bash','--noprofile','--norc','-c',checkshell],case,env,120)
  cout=(T/'logs'/f'{kind}.checkMesh.stdout').read_text(errors='replace');result['checkMeshStatus']=crec;result['literalMeshOK']=bool(re.search(r'^Mesh OK\.',cout,re.M));result['failureAndWarningLines']=[x for x in cout.splitlines() if '***' in x or re.match(r'^Failed \d+ mesh checks',x)];result['quality']='literal checker stdout required; exit code is not quality verdict'
  aftermesh={name:info(case/'constant/polyMesh'/name) for name in meshNames};assert all(aftermesh[k]=={f:mesh[k][f] for f in ['sha256','bytes']} for k in aftermesh)
  write(T/'logs'/f'{kind}.mesh-after-checker.json',aftermesh)
 result['nativeDerivedMetadata']=metadata_output_proof(kind)
 for rel,v in inputs.items():
  if rel!='Mesh/constant/dcCartSourceRegions.json':assert info(T/'cases'/kind/rel)=={k:v[k] for k in ['sha256','bytes']}
 write(T/'logs'/f'{kind}.analysis.json',result);summary[kind]=result
 assert controls(G)==G
write(T/'logs/ON-continuation-summary.json',summary);write(T/'guards/continuation-run-after.json',controls(G));assert controls(G)==G
print(json.dumps({k:{f:v.get(f) for f in ('nativeTotalSeconds','actualPrivateLIBInit','literalMeshOK','nativeRelevantLines','failureAndWarningLines')} for k,v in summary.items()},indent=2))
