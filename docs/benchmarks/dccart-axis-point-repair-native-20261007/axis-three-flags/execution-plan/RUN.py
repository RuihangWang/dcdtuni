from pathlib import Path
import argparse,datetime,hashlib,json,os,re,shlex,signal,subprocess,time
F=Path(__file__).resolve().parent
plan=json.loads((F/'plan.json').read_text())
T=Path(plan['runtimeDirectory']);CASE=Path(plan['caseRoot'])
def info(p):
 q=Path(p);return {'sha256':hashlib.sha256(q.read_bytes()).hexdigest(),'bytes':q.stat().st_size}
def write(p,v):
 with Path(p).open('x') as f:f.write(json.dumps(v,indent=2)+'\n')
def literal_block(p):
 s=Path(p).read_text();a=s.index(plan['sourceC']['startAnchor']);b=s.index(plan['sourceC']['endAnchor'],a);return s[a:b].encode()
def role_info(pid):
 d=Path('/proc',pid);s=d.joinpath('stat').read_text().rsplit(')',1)[1].split();uid=int(next(x.split()[1] for x in d.joinpath('status').read_text().splitlines() if x.startswith('Uid:')))
 return {'uid':uid,'start':s[19],'cwd':os.readlink(d/'cwd'),'argvSHA256':hashlib.sha256(d.joinpath('cmdline').read_bytes()).hexdigest()}
def guards():
 groups={}
 for key in ['sourceCohortFile','privateSourceCohortFile','servedFilesFile']:
  expect=json.loads((F/plan[key]).read_text())
  if key=='sourceCohortFile':
   actual={g:{p:info(p) for p in rows} for g,rows in expect.items()}
  else:actual={p:info(p) for p in expect}
  assert actual==expect,'Current reviewed source/pair/served bytes drift: '+key;groups[key]=actual
 expected=json.loads((F/plan['ownedRolesFile']).read_text());actual={pid:role_info(pid) for pid in expected};assert actual==expected,'Owned role changed';groups['roles']=actual
 for p,expected in plan['externalReferences'].items():assert info(p)==expected,'Reviewed external reference drift: '+p
 for k in ['APP','LIB']:
  v=plan['pair'][k];assert info(v['path'])=={x:v[x] for x in ['sha256','bytes']}
 v=plan['stockCheckMesh'];assert info(v['path'])=={x:v[x] for x in ['sha256','bytes']}
 source=plan['sourceC'];x=literal_block(source['actualF3C']['path']);y=literal_block(source['actualNormalC']['path']);assert x==y and hashlib.sha256(x).hexdigest()==source['blockSHA256']
 return {'sourceGroups':{k:len(v) if k!='sourceCohortFile' else {g:len(rows) for g,rows in v.items()} for k,v in groups.items()},'sourceFingerprint':hashlib.sha256(json.dumps(groups,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'pair':plan['pair'],'kernelLiteralEqual':True,'noNetworkOrGPURequest':True}
def env_for(kind):
 assert kind in ['BASE','AXIS']
 env={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_')) and k not in ['LD_PRELOAD','LD_AUDIT','LD_DEBUG']}
 env.update(plan['commonFlags']);env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPYCACHEPREFIX=str(T/'cache/pycache'),TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'))
 if kind=='AXIS':env.update(plan['axisOnlyDelta'])
 assert not any(k in env for k in plan['forbiddenVariables'])
 assert {k:v for k,v in env.items() if k.startswith('DCCART_')}=={**plan['commonFlags'],**(plan['axisOnlyDelta'] if kind=='AXIS' else {})}
 return env
def bounded(name,argv,cwd,env):
 rec={'argv':argv,'cwd':str(cwd),'startUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'watchdogSeconds':120,'nativeFlags':{k:v for k,v in env.items() if k.startswith('DCCART_')},'environment':{k:env.get(k) for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','TMPDIR','XDG_CACHE_HOME']}};start=time.monotonic()
 write(T/'logs'/f'{name}.started.json',rec)
 try:
  with (T/'logs'/f'{name}.stdout').open('xb') as so,(T/'logs'/f'{name}.stderr').open('xb') as se:
   proc=subprocess.Popen(argv,cwd=cwd,env=env,stdout=so,stderr=se,start_new_session=True);rec['pid']=proc.pid;rec['startTicks']=Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19];timed=False
   try:rc=proc.wait(timeout=120)
   except subprocess.TimeoutExpired:
    timed=True
    if proc.poll() is None:
     assert Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19]==rec['startTicks'] and os.getpgid(proc.pid)==proc.pid
     os.killpg(proc.pid,signal.SIGTERM)
    try:rc=proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
     assert Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19]==rec['startTicks'] and os.getpgid(proc.pid)==proc.pid
     os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
   rec.update(returncode=rc,timedOut=timed)
 except BaseException as e:
  rec['collectorException']=type(e).__name__+': '+str(e);raise
 finally:
  rec.update(seconds=time.monotonic()-start,stopUTC=datetime.datetime.now(datetime.timezone.utc).isoformat());write(T/'logs'/f'{name}.status.json',rec)
 return rec
def metadata_output_proof(kind):
 key='Mesh/constant/dcCartSourceRegions.json';src=F/'input'/key;dst=CASE/kind/key
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
 raw=(CASE/kind/'Mesh/constant/polyMesh/boundary').read_text();assert len(raw.encode())<=65536
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
 return {'nativeBefore':info(src),'nativeAfter':info(dst),'stableSchemaAndAllIdentityExact':True,'onlyAllowedDerivedScalarChanges':diffs,'boundaryFile':info(CASE/kind/'Mesh/constant/polyMesh/boundary'),'completeBoundaryPatches':patches,'boundRows':bound,'source':'APP3098-3118 after mesh write; dcCartSourceRegions.H163-195 writes OFstream output, actual patch.start()/size() at185-188'}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute-authorized',action='store_true');parser.add_argument('--approved-plan-manifest-sha',required=True);args=parser.parse_args()
 if not args.execute_authorized:raise SystemExit('PREPARED ONLY; no native execution without separate ROOT authorization')
 assert info(F/'MANIFEST.json')['sha256']==args.approved_plan_manifest_sha,'ROOT must approve exact immutable plan manifest'
 manifest=json.loads((F/'MANIFEST.json').read_text());assert all(info(F/p)==v for p,v in manifest['files'].items()),'Immutable plan payload drift'
 B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
 assert T.resolve().is_relative_to(B) and CASE.resolve().is_relative_to(B) and T.name=='runtime' and CASE.name=='cases'
 assert not T.exists(),'Once-only runtime directory already exists; no retry or replacement'
 assert guards()
 bm=plan['buildFinalManifest'];assert info(bm['path'])=={k:bm[k] for k in ['sha256','bytes']}
 bd=Path(bm['path']).parent;contents=json.loads(Path(bm['path']).read_text());assert all(info(bd/p)==v for p,v in contents['files'].items()),'Private F3 reviewed build payload drift'
 inputs=json.loads((F/'input-bindings.json').read_text());assert len(inputs)==12
 for kind in plan['variants']:
  root=CASE/kind;assert root.exists() and not(root/'Mesh/constant/polyMesh').exists()
  assert {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}==set(inputs)
  for rel,v in inputs.items():assert info(root/rel)=={k:v[k] for k in ['sha256','bytes']} and info(F/'input'/rel)=={k:v[k] for k in ['sha256','bytes']}
 base=env_for('BASE');axis=env_for('AXIS');delta={k:[base.get(k),axis.get(k)] for k in set(base)|set(axis) if base.get(k)!=axis.get(k)};assert set(delta)==set(plan['axisOnlyDelta']) and all(v==[None,'1'] for v in delta.values())
 T.mkdir();(T/'logs').mkdir();(T/'guards').mkdir();(T/'tmp').mkdir();(T/'cache').mkdir();write(T/'environment-difference.json',delta);write(T/'authorization-binding.json',{'exactApprovedPlanManifestSHA':args.approved_plan_manifest_sha,'pair':plan['pair'],'scope':plan['scope'],'runtimeFlagSupplied':True})
 APP=Path(plan['pair']['APP']['path']);LIB=Path(plan['pair']['LIB']['path']);CHECK=Path(plan['stockCheckMesh']['path']);N=Path(plan['nativeRoot']);summary={};meshNames=['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones']
 write(T/'guards/run-before.json',guards())
 try:
  for kind in plan['variants']:
   write(T/'guards'/f'{kind}.before.json',guards())
   case=CASE/kind/'Mesh';assert not(case/'constant/polyMesh').exists()
   for rel,v in inputs.items():assert info(CASE/kind/rel)=={k:v[k] for k in ['sha256','bytes']}
   (T/f'{kind}.once').open('x').close();env=env_for(kind)
   native=[str(APP),'-case',str(case),'-overwrite','-timeLimit','110']
   shell='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nexport LD_DEBUG=libs\nexec '+shlex.join(native)+'\n'
   rec=bounded(kind+'.native',['taskset','-c',plan['CPU'],'bash','--noprofile','--norc','-c',shell],case,env)
   out=(T/'logs'/f'{kind}.native.stdout').read_text(errors='replace');err=(T/'logs'/f'{kind}.native.stderr').read_text(errors='replace');loader=any('calling init: '+str(LIB) in l for l in err.splitlines());mesh={name:info(case/'constant/polyMesh'/name) for name in meshNames if(case/'constant/polyMesh'/name).is_file()};write(T/'logs'/f'{kind}.mesh-before-checker.json',mesh)
   observations=[];incomplete=[];prefix='PRIVATE physical-point decision observation: '
   for line in out.splitlines():
    if line.startswith(prefix):
     payload=line[len(prefix):]
     if not payload.startswith('{'):incomplete.append(payload);continue
     observations.append(json.loads(payload,parse_int=int))
   result={'nativeStatus':rec,'actualPrivateLIBInit':loader,'physicalObserverCommonBothExact1':True,'decisionObservations':observations,'incompleteObservationLines':incomplete,'rawUint64Decoder':'Python json parse_int=int; no JS number identity promotion','nativeRelevantLines':[x for x in out.splitlines() if any(k in x for k in ['axis-plane kernel','physical-point lifecycle','Normal complete-cavity','postwriter coupled certificate:','Total '])],'literalMeshOK':False,'quality':'not checked','meshFiles':mesh}
   if rec.get('returncode')==0 and not rec.get('timedOut') and len(mesh)==8 and loader:
    assert not incomplete,'Physical observer output incomplete'
    chk=[str(CHECK),'-case',str(case),'-allTopology','-allGeometry'];cs='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nunset LD_DEBUG\nexec '+shlex.join(chk)+'\n'
    cr=bounded(kind+'.checkMesh',['taskset','-c',plan['CPU'],'bash','--noprofile','--norc','-c',cs],case,env);cout=(T/'logs'/f'{kind}.checkMesh.stdout').read_text(errors='replace');result['checkMeshStatus']=cr;result['literalMeshOK']=bool(re.search(r'^Mesh OK\.',cout,re.M)) and not re.search(r'^Failed \d+ mesh checks',cout,re.M);result['strictAccepted']=cr.get('returncode')==0 and not cr.get('timedOut') and result['literalMeshOK'];result['quality']='strict PASS' if result['strictAccepted'] else 'strict RED'
    result['failureAndWarningLines']=[x for x in cout.splitlines() if '***' in x or re.match(r'^Failed \d+ mesh checks',x)]
    result['qualitySets']={str(p.relative_to(case/'constant/polyMesh')):info(p) for p in (case/'constant/polyMesh/sets').rglob('*') if p.is_file()} if(case/'constant/polyMesh/sets').exists() else {}
    after={name:info(case/'constant/polyMesh'/name) for name in meshNames};assert after==mesh,'Checker changed written scientific mesh';write(T/'logs'/f'{kind}.mesh-after-checker.json',after)
    result['nativeDerivedMetadata']=metadata_output_proof(kind)
   else:result['strictAccepted']=False;result['quality']='native nonqualification RED; no checker acceptance'
   for rel,v in inputs.items():
    if rel!='Mesh/constant/dcCartSourceRegions.json':assert info(CASE/kind/rel)=={k:v[k] for k in ['sha256','bytes']},'Original input changed beyond native-derived patch ranges'
   write(T/'logs'/f'{kind}.analysis.json',result);summary[kind]=result;write(T/'guards'/f'{kind}.after.json',guards())
  comparisons={name:{kind:info(CASE/kind/'Mesh/constant/polyMesh'/name) if(CASE/kind/'Mesh/constant/polyMesh'/name).exists() else None for kind in plan['variants']} for name in meshNames}
  write(T/'logs/pair-mesh-identities.json',comparisons);write(T/'logs/pair-summary.json',summary)
 finally:write(T/'guards/run-after.json',guards())
 print(json.dumps({k:{f:v.get(f) for f in ['quality','strictAccepted','nativeRelevantLines','failureAndWarningLines']} for k,v in summary.items()},indent=2))
if __name__=='__main__':main()
