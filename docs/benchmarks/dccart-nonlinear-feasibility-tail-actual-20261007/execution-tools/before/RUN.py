from pathlib import Path
import argparse,datetime,hashlib,json,math,os,re,shlex,shutil,signal,struct,subprocess,time
F=Path(__file__).resolve().parent
plan=json.loads((F/'run-plan.json').read_text());T=Path(plan['runtimeDirectory']);CASE=Path(plan['caseRoot']);B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
ACTUAL_PAIR=None
SOURCE_BINDINGS=json.loads((F/'source-cohort-bindings.json').read_text())
def fresh_build_binding(approved):
 build=Path(plan['freshBuildDirectory']);manifest=build/'MANIFEST.json'
 assert info(manifest)['sha256']==approved,'ROOT must approve exact produced fresh build MANIFEST before any runtime write/Popen'
 m=json.loads(manifest.read_text());assert all(info(build/rel)==v for rel,v in m['files'].items()),'Produced fresh build evidence drift'
 authorization=json.loads((build/'authorization-binding.json').read_text());assert authorization['exactApprovedPlanManifestSHA']==info(F/'MANIFEST.json')['sha256'],'Fresh build must use this exact frozen plan'
 rec=json.loads((build/'receipt.json').read_text());assert rec['build']['exitCode']==0 and not rec['build']['timedOut'],'Fresh build not qualified'
 comp=json.loads((build/'source/actual-compilation-units.json').read_text());assert comp['objectsSeeded']==0 and len(comp['objects'])==6 and len(comp['units'])==6 and sorted(comp['units'])==sorted(json.loads((F/'build-plan.json').read_text())['sixUnits']),'Fresh six compilation units required'
 built=json.loads((build/'source/final-source39.json').read_text());assert len(built)==39
 for rel,row in SOURCE_BINDINGS.items():
  assert built[rel]['sha256']==row['plannedSource']['sha256'] and built[rel]['bytes']==row['plannedSource']['bytes']
  assert info(Path(plan['nativeRoot'])/rel)==row['plannedSource']
 pair=json.loads((build/'elf/paired-identities.json').read_text())
 for k in ('APP','LIB'):
  p=Path(pair[k]['path']);assert p.resolve().is_relative_to(Path(plan['nativeRoot']).resolve()) and info(p)=={j:pair[k][j] for j in ('sha256','bytes')}
 assert pair['candidateSource']==SOURCE_BINDINGS[json.loads((F/'build-plan.json').read_text())['candidateHeader']]['plannedSource']
 assert pair['actualNativeRun'] is False and pair['resolvedNewPrivateLibrary']==pair['LIB']['path']
 assert pair['LIB']['path'] in (build/'elf/APP-ldd.stdout').read_text()
 return pair,{'path':str(manifest),**info(manifest),'files':len(m['files']),'freshUnits':comp['units'],'objectsSeeded':0,'APPCaseInvocationDuringBuild':False}
def uint64(x):
 return type(x) is int and 0<=x<2**64
def summary_row(x):
 assert isinstance(x,list) and len(x)==18 and x[0] in (0,1)
 assert all(type(v) is int for v in x)
 assert all(v>=0 for v in x[1:8]) and all(uint64(v) for v in x[8:13])
 assert all(-2**63<=v<2**63 for v in x[13:18])
 return {'observed':bool(x[0]),'scope':'already-owned complete stencil; invalid placeholders are unobserved'}
def parse_veto(out,kind):
 prefix='PRIVATE finite DW veto observation: '
 lines=[line for line in out.splitlines() if line.startswith(prefix)]
 if kind=='TRACE_OFF':
  assert not lines,'DefaultOFF emitted new trace'
  return {'requested':False,'records':[],'complete':False,'status':'default-off/no record'}
 if not lines:return {'requested':True,'records':[],'complete':False,'status':'not-observed; apply may have returned before serializer'}
 assert len(lines)==1,'Unexpected repeated serial trace stage'
 raw=lines[0];assert len(raw.encode())+1<=65536,'Trace serial record+newline exceeds original cap'
 d=json.loads(raw[len(prefix):],parse_int=int);assert type(d.get('version')) is int and d['version']==1 and type(d.get('incomplete')) is bool
 if 'recordUnavailable' in d:
  assert set(d)=={'version','incomplete','recordUnavailable'} and d['incomplete'] and type(d['recordUnavailable']) is str and d['recordUnavailable'] in {'serialized-line-cap','serialization-exception'},'Unknown unavailable reason/schema'
  return {'requested':True,'records':[d],'complete':False,'status':d['recordUnavailable']+'; no evidence promoted','recordUnavailable':d['recordUnavailable']}
 ps=d['proposals'];fs=d['fractions'];assert isinstance(ps,list) and isinstance(fs,list) and len(ps)<=16 and len(fs)<=128
 assert type(d['seen']) is int and d['seen']>=len(ps) and type(d['fractionSeen']) is int and d['fractionSeen']>=len(fs)
 for i,p in enumerate(ps):
  assert type(p['index']) is int and p['index']==i and type(p['dimension']) is int and p['dimension'] in (3,9) and isinstance(p['moving'],list) and 1<=len(p['moving'])<=3 and p['dimension']==3*len(p['moving']) and all(type(v) is int and 0<=v<2**63 for v in p['moving'])
  assert len(p['oldPointBits'])==3*len(p['moving']) and all(uint64(v) for v in p['oldPointBits'])
  assert len(p['parametersBits'])==p['dimension'] and len(p['bestParametersBits'])==p['dimension']
  assert all(uint64(v) for v in p['parametersBits']+p['bestParametersBits'])
  assert all(uint64(p[k]) for k in ['firstObjectiveBits','lastObjectiveBits','firstHardBits','lastHardBits'])
  assert all(type(p[k]) is int for k in ['evalCalls','evalValid','requests','iterations','projectionUpdates','firstInvalid','breakReason','improved','bestHardImproved','requestCap','timeCap'])
  assert 0<=p['evalValid']<=p['evalCalls'] and p['evalCalls']==p['requests'] and 0<=p['requests']<=256 and 0<=p['iterations']<=32 and type(p['projectionUpdates']) is int and p['projectionUpdates']>=0
  assert p['firstInvalid'] in (0,1,2,3) and p['breakReason'] in (0,1,2)
  assert all(p[k] in (0,1) for k in ['improved','bestHardImproved','requestCap','timeCap'])
  summary_row(p['before'])
 for r in fs:
  assert type(r['proposal']) is int and 0<=r['proposal']<len(ps) and type(r['checkpoint']) is int and r['checkpoint'] in (0,1)
  assert uint64(r['fractionBits']) and len(r['pointBits'])==3*len(ps[r['proposal']]['moving']) and all(uint64(v) for v in r['pointBits'])
  assert type(r['veto']) is int and type(r['called']) is int and r['veto'] in range(8) and r['called']==(127 if r['veto']==0 else 2**r['veto']-1)
  assert type(r['hardCalled']) is int and r['hardCalled'] in (0,1) and bool(r['hardCalled'])==bool(r['called']&4)
  if r['hardCalled']:assert type(r['oldHardDW']) is int and type(r['nextHardDW']) is int and r['oldHardDW']>=0 and r['nextHardDW']>=0
  else:assert r['oldHardDW'] is None and r['nextHardDW'] is None
  summary_row(r['after'])
 complete=not d['incomplete'] and d['seen']==len(ps) and d['fractionSeen']==len(fs)
 return {'requested':True,'records':[d],'complete':complete,'status':'complete' if complete else 'capped/incomplete; partial rows diagnostic only','proposalRows':len(ps),'fractionRows':len(fs),'nativeToWrittenOrSourceIdentityProven':False,'firstVetoScope':'first combined actually-called leaf only; no internal subpredicate inference'}

def info(p):
 p=Path(p);h=hashlib.sha256()
 with p.open('rb') as s:
  for c in iter(lambda:s.read(1<<20),b''):h.update(c)
 return {'sha256':h.hexdigest(),'bytes':p.stat().st_size}
def write(p,v):
 with Path(p).open('x') as f:f.write(json.dumps(v,indent=2)+'\n')
def literal_block(p):
 s=Path(p).read_text();a=s.index(plan['sourceC']['startAnchor']);b=s.index(plan['sourceC']['endAnchor'],a);return s[a:b].encode()
def role_info(pid):
 d=Path('/proc',pid);s=(d/'stat').read_text().rsplit(')',1)[1].split();uid=int(next(x.split()[1] for x in (d/'status').read_text().splitlines() if x.startswith('Uid:')))
 return {'uid':uid,'start':s[19],'cwd':os.readlink(d/'cwd'),'argvSHA256':hashlib.sha256((d/'cmdline').read_bytes()).hexdigest()}
def guards():
 expected=json.loads((F/'guards/normal-scope.json').read_text());normal={g:{p:info(p) for p in rows} for g,rows in expected.items()};assert normal==expected,'Focused normal AIR39/Main29/pair2 drift; no rollback/restart permitted'
 ui=json.loads((F/'guards/authorized-I4-transition.json').read_text());currentui={r['path']:info(r['path']) for r in ui['rows']};assert all(currentui[r['path']]==r['currentAuthorizedAfter'] for r in ui['rows']),'Authorized I4 after changed'
 ov=json.loads((F/'guards/authorized-I5-OV-transition.json').read_text());ovActual={r['path']:info(r['path']) for r in ov['rows']};assert all(ovActual[r['path']]==r['currentAuthorizedAfter'] for r in ov['rows']),'Root-authorized I5 OV after changed; no automatic repair'
 expect=json.loads((F/'guards/base-actual-DW-source39.json').read_text());newsource={p:info(p) for p in expect};assert newsource==expect and len(newsource)==39,'Immutable actual base DW source39 changed'
 fresh={str(Path(plan['nativeRoot'])/rel):info(Path(plan['nativeRoot'])/rel) for rel in SOURCE_BINDINGS};assert all(fresh[str(Path(plan['nativeRoot'])/rel)]==row['plannedSource'] for rel,row in SOURCE_BINDINGS.items()),'Fresh compiled private source39 changed'
 expect=json.loads((F/'guards/owned-roles.json').read_text());roles={pid:role_info(pid) for pid in expect};assert roles==expect,'Owned role changed; no restart/kill permission'
 inputs=json.loads((F/'input-bindings.json').read_text());original={r:info(v['path']) for r,v in inputs.items()};assert len(original)==12 and all(original[r]=={k:v[k] for k in ['sha256','bytes']} for r,v in inputs.items()),'Original genuine twelve inputs changed'
 for p,v in plan['externalReferences'].items():assert info(p)==v,'Immutable reference changed:'+p
 for key in ['APP','LIB']:
  row=ACTUAL_PAIR[key];assert info(row['path'])=={k:row[k] for k in ['sha256','bytes']},'Actual reviewed new pair changed'
 row=plan['stockCheckMesh'];assert info(row['path'])=={k:row[k] for k in ['sha256','bytes']},'Stock checker changed'
 x=literal_block(plan['sourceC']['actualF3C']['path']);y=literal_block(plan['sourceC']['actualNormalC']['path']);assert x==y and hashlib.sha256(x).hexdigest()==plan['sourceC']['blockSHA256'],'Original kernel block changed'
 groups={'normal':normal,'authorizedI4':currentui,'authorizedI5OV':ovActual,'baseActualDWSource39':newsource,'freshPrivateSource39':fresh,'roles':roles,'originalInput12':original}
 return {'groups':groups,'fingerprint':hashlib.sha256(json.dumps(groups,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'pair':ACTUAL_PAIR,'kernelBlockLiteralEqual':True,'normalScope70PlusAuthorizedI4UIAndI5OVOnly':True,'noNetworkGPURequest':True}
def env_for(kind):
 assert kind in plan['variants'];env={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_')) and k not in ['LD_PRELOAD','LD_AUDIT','LD_DEBUG','LD_LIBRARY_PATH']};env.update(plan['commonFlags']);env.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',LD_LIBRARY_PATH='',CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',FOAM_API='2412',PYTHONDONTWRITEBYTECODE='1',PYTHONPYCACHEPREFIX=str(T/'cache/pycache'),TMPDIR=str(T/'tmp'),TMP=str(T/'tmp'),TEMP=str(T/'tmp'),XDG_CACHE_HOME=str(T/'cache'),CCACHE_DIR=str(T/'cache/ccache'))
 if kind=='TRACE_ON':env.update(plan['traceOnlyDelta'])
 assert not any(k in env for k in plan['forbiddenVariables']);assert not any(k.startswith('CART_') for k in env);assert {k:v for k,v in env.items() if k.startswith('DCCART_')}=={**plan['commonFlags'],**(plan['traceOnlyDelta'] if kind=='TRACE_ON' else {})};return env

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
     if proc.poll() is not None:rc=proc.returncode
     else:
      assert Path('/proc',str(proc.pid),'stat').read_text().rsplit(')',1)[1].split()[19]==rec['startTicks'] and os.getpgid(proc.pid)==proc.pid
      os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
   rec.update(returncode=rc,timedOut=timed)
 except BaseException as e:
  rec['collectorException']=type(e).__name__+': '+str(e);raise
 finally:
  rec.update(seconds=time.monotonic()-start,stopUTC=datetime.datetime.now(datetime.timezone.utc).isoformat());write(T/'logs'/f'{name}.status.json',rec)
 return rec
def metadata_output_proof(kind):
 key='Mesh/constant/dcCartSourceRegions.json';src=Path(json.loads((F/'input-bindings.json').read_text())[key]['path']);dst=CASE/kind/key
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


def parse_sets(mesh):
 rows={};raw={};classes={}
 if not(mesh/'sets').exists():return {'sets':rows,'raw':raw,'classes':classes}
 files=list((mesh/'sets').rglob('*'));assert len(files)<=256
 for p in files:
  assert p.is_file() and p.parent==mesh/'sets','Unknown nested stock set layout'
  d=p.read_bytes();assert len(d)<=8000000;h=d.index(b'}')+1;head=d[:h];cls=re.search(rb'class\s+(\w+)\s*;',head);obj=re.search(rb'object\s+([^;\s]+)\s*;',head);assert cls and cls[1] in [b'cellSet',b'faceSet',b'pointSet'] and obj and obj[1].decode()==p.name
  m=re.search(rb'\n\s*(\d+)\s*\((.*?)\)',d[h:],re.S);assert m;n=int(m[1]);assert n<=1000000;ids=[int(v) for v in m[2].split()];assert len(ids)==n and len(set(ids))==n and all(v>=0 for v in ids);rows[p.name]=sorted(ids);classes[p.name]=cls[1].decode();raw[p.name]=info(p)
 return {'sets':rows,'raw':raw,'classes':classes,'formatNote':'actual stock labelHashSet serialized labels parsed strictly; unrecognized layout refuses collection, not relaxed quality'}
def actual_mesh(mesh):
 def raw(name,cls):
  d=(mesh/name).read_bytes();assert len(d)<=64000000;h=d.index(b'}')+1;head=d[:h];assert b'LSB;label=32;scalar=64' in head and re.search(rb'format\s+binary;',head) and re.search(rb'class\s+'+cls+rb';',head);return d,h
 def array(d,start,width,fmt):
  m=re.search(rb'\n(\d+)\s*\(',d[start:]);assert m;n=int(m[1]);assert n<=1000000;begin=start+m.end();end=begin+n*width;assert end<len(d) and d[end:end+1]==b')';return list(struct.iter_unpack(fmt,d[begin:end])),end+1
 d,h=raw('points',b'vectorField');pp,_=array(d,h,24,'<3d');points=[tuple(v) for v in pp];assert points and all(math.isfinite(x) for p in points for x in p)
 d,h=raw('faces',b'faceCompactList');off,e=array(d,h,4,'<i');lab,e=array(d,e,4,'<i');offsets=[x[0] for x in off];labels=[x[0] for x in lab];assert offsets and offsets[0]==0 and offsets[-1]==len(labels) and all(b-a>=3 for a,b in zip(offsets,offsets[1:])) and all(0<=p<len(points) for p in labels);faces=[labels[offsets[i]:offsets[i+1]] for i in range(len(offsets)-1)]
 d,h=raw('owner',b'labelList');v,_=array(d,h,4,'<i');owner=[x[0] for x in v];d,h=raw('neighbour',b'labelList');v,_=array(d,h,4,'<i');neigh=[x[0] for x in v];assert len(owner)==len(faces) and len(neigh)<=len(owner) and all(x>=0 for x in owner+neigh)
 boundaryFaces=list(range(len(neigh),len(faces)));boundaryPoints=sorted({p for f in boundaryFaces for p in faces[f]});return {'points':points,'faces':faces,'owner':owner,'neighbour':neigh,'boundaryFaceIDs':boundaryFaces,'boundaryPointIDs':boundaryPoints}
def compare_science(a,b):
 names=['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones'];ident={n:[info(a/n),info(b/n)] for n in names};topology=all(x==y for n,(x,y) in ident.items() if n!='points');s1=parse_sets(a);s2=parse_sets(b);setNames=sorted(set(s1['sets'])|set(s2['sets'])|set(['underdeterminedCells','nonOrthoFaces','lowWeightFaces','lowVolRatioFaces','skewFaces']));result={'meshBytes':ident,'topologyFourAndZonesThreeExact':topology,'allEightScientificBytesExact':all(x==y for x,y in ident.values()),'allRawSets':{'before':s1,'after':s2},'writtenIDsAreNotNativeOrSourceUIDs':True}
 if topology:
  delta={n:{'removed':sorted(set(s1['sets'].get(n,[]))-set(s2['sets'].get(n,[]))),'added':sorted(set(s2['sets'].get(n,[]))-set(s1['sets'].get(n,[])))} for n in setNames};result.update(setChanges=delta,allAddedStockSetsEmpty=all(not row['added'] for row in delta.values()))
  va=actual_mesh(a);vb=actual_mesh(b);assert len(va['points'])==len(vb['points']) and va['faces']==vb['faces'] and va['owner']==vb['owner'] and va['neighbour']==vb['neighbour'] and va['boundaryPointIDs']==vb['boundaryPointIDs'];changed=[{'writtenPointID':i,'before_m':list(x),'after_m':list(y)} for i,(x,y) in enumerate(zip(va['points'],vb['points'])) if struct.pack('<3d',*x)!=struct.pack('<3d',*y)];boundary=set(va['boundaryPointIDs']);changedBoundary=[r for r in changed if r['writtenPointID'] in boundary];result.update(writtenPointsChanged=changed,completeBoundaryFaceCount=len(va['boundaryFaceIDs']),completeBoundaryPointIDs=va['boundaryPointIDs'],completeBoundaryPointCount=len(boundary),changedBoundaryPoints=changedBoundary,allBoundaryPointBitsHeld=not changedBoundary)
 else:result.update(setChanges=None,allAddedStockSetsEmpty=None,pointComparison='unavailable: different written topology; never compare labels as same identity')
 return result

SCIENTIFIC_NAMES=('points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones')
def require_mesh(mesh):
 mesh=Path(mesh)
 missing=[n for n in SCIENTIFIC_NAMES if not(mesh/n).is_file() or (mesh/n).is_symlink()]
 assert not missing,'Required actual scientific mesh missing/nonliteral at '+str(mesh)+':'+','.join(missing)
 return {n:info(mesh/n) for n in SCIENTIFIC_NAMES}
def pair_scientific_readback(baseline,cases):
 baseline=Path(baseline);cases=Path(cases)
 paths={'originalActualWIDER_ON':baseline/plan['baselineComparison']['meshRelativePath'],
        'newTRACE_OFF':cases/'TRACE_OFF/Mesh/constant/polyMesh',
        'newTRACE_ON':cases/'TRACE_ON/Mesh/constant/polyMesh'}
 required={k:require_mesh(p) for k,p in paths.items()}
 result={'scope':'required pure offline actual written science comparison; stock checker alone retains strict quality authority',
         'baseline':'actual WIDER_ON109, never re-executed','requiredMeshes':{k:{'path':str(paths[k]),'files':v} for k,v in required.items()},
         'allRequired8x3Present':True,
         'originalActualWIDER_ON_vs_newTRACE_OFF':compare_science(paths['originalActualWIDER_ON'],paths['newTRACE_OFF']),
         'newTRACE_OFF_vs_TRACE_ON':compare_science(paths['newTRACE_OFF'],paths['newTRACE_ON'])}
 for key,root in [('originalActualWIDER_ON',baseline/'cases/WIDER_ON'),('newTRACE_OFF',cases/'TRACE_OFF'),('newTRACE_ON',cases/'TRACE_ON')]:
  q=root/'Mesh/constant/dcCartSourceRegions.json';assert q.is_file() and not q.is_symlink(),'Required derived metadata missing/nonliteral:'+str(q)
  result[key+'Metadata']=info(q)
 result['comparisonsCompleted']=2
 a=result['originalActualWIDER_ON_vs_newTRACE_OFF'];b=result['newTRACE_OFF_vs_TRACE_ON']
 sameSets=a['allRawSets']['before']['sets']==a['allRawSets']['after']['sets'] and a['allRawSets']['before']['classes']==a['allRawSets']['after']['classes']
 result['unchangedEvidence']={'baseline_vs_TRACE_OFF_raw8_and_sets_exact':a['allEightScientificBytesExact'] and sameSets,
   'TRACE_OFF_vs_TRACE_ON_topology_zones_boundary_bits_held':b['topologyFourAndZonesThreeExact'] and b.get('allBoundaryPointBitsHeld') is True,
   'TRACE_OFF_vs_TRACE_ON_no_added_stock_sets':b.get('allAddedStockSetsEmpty') is True}
 result['allDeclaredUnchangedEvidenceHeld']=all(result['unchangedEvidence'].values())
 result['unchangedEvidenceIsNotMeshAcceptance']=True
 return result
def case_input_proof(kind,initial):
 assert kind in plan['variants']
 bindings=json.loads((F/'input-bindings.json').read_text());assert len(bindings)==12
 rows={}
 for rel,v in bindings.items():
  if not initial and rel=='Mesh/constant/dcCartSourceRegions.json':continue
  p=CASE/kind/rel;assert p.is_file() and not p.is_symlink(),'Required immutable case input missing/nonliteral:'+str(p)
  value=info(p);assert value=={k:v[k] for k in ('sha256','bytes')},'Original case input changed:'+rel
  rows[rel]=value
 assert len(rows)==(12 if initial else 11)
 return {'phase':'native-before' if initial else 'native-after immutable11','count':len(rows),'files':rows,'derivedSourceRegionsBoundSeparately':not initial}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute-authorized',action='store_true');parser.add_argument('--approved-plan-manifest-sha',required=True);parser.add_argument('--approved-build-manifest-sha',required=True);a=parser.parse_args()
 if not a.execute_authorized:raise SystemExit('PREPARED ONLY; separate ROOT exact oncepair native authorization required')
 assert info(F/'MANIFEST.json')['sha256']==a.approved_plan_manifest_sha,'ROOT must approve exact immutable plan manifest';manifest=json.loads((F/'MANIFEST.json').read_text());assert all(info(F/p)==v for p,v in manifest['files'].items()),'Immutable plan drift'
 assert T.resolve().is_relative_to(B) and CASE.resolve().is_relative_to(T.parent.resolve()) and T.name=='runtime' and CASE.name=='cases';assert not T.exists() and not CASE.exists(),'Once-only fresh runtime/cases already exist; no retry/replacement'
 global ACTUAL_PAIR
 ACTUAL_PAIR,buildBinding=fresh_build_binding(a.approved_build_manifest_sha)
 before=guards()
 baseline=Path(plan['baselineComparison']['root']);bm=json.loads((baseline/'MANIFEST.json').read_text());assert all(info(baseline/r)==v for r,v in bm['files'].items()),'Immutable actual WIDER_ON primary109 drift'
 inputs=json.loads((F/'input-bindings.json').read_text());e0=env_for('TRACE_OFF');e1=env_for('TRACE_ON');delta={k:[e0.get(k),e1.get(k)] for k in set(e0)|set(e1) if e0.get(k)!=e1.get(k)};assert delta=={'DCCART_PRIVATE_DW_VETO_OBSERVE':[None,'1']},'Both wider exact1; only new observer absent ->exact1 may differ'
 T.mkdir();CASE.mkdir()
 for name in ['logs','guards','tmp','cache']:(T/name).mkdir()
 (T/'runtime.once').open('x').close();write(T/'authorization-binding.json',{'exactApprovedPlanManifestSHA':a.approved_plan_manifest_sha,'pair':ACTUAL_PAIR,'scope':plan['scope'],'variantCount':2,'approvedFreshBuildManifest':buildBinding});write(T/'environment-difference.json',delta);write(T/'guards/run-before.json',before)
 summary={};status={'startUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':plan['scope'],'APPOrCheckerRuns':[]};meshNames=['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones'];APP=Path(ACTUAL_PAIR['APP']['path']);LIB=Path(ACTUAL_PAIR['LIB']['path']);CHECK=Path(plan['stockCheckMesh']['path']);N=Path(plan['nativeRoot'])
 try:
  for kind in plan['variants']:
   root=CASE/kind;root.mkdir();clones={}
   for rel,v in inputs.items():
    src=Path(v['path']);dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o644);assert not dst.is_symlink() and dst.stat().st_ino!=src.stat().st_ino and info(dst)=={k:v[k] for k in ['sha256','bytes']};clones[rel]={'original':str(src),'freshActualInput':str(dst),**info(dst),'independentInode':True}
   assert {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}==set(inputs);assert not(root/'Mesh/constant/polyMesh').exists();write(T/'logs'/f'{kind}.fresh-input12.json',clones);write(T/'logs'/f'{kind}.initial-input-proof.json',case_input_proof(kind,True));write(T/'guards'/f'{kind}.before.json',guards());(T/f'{kind}.once').open('x').close()
   case=root/'Mesh';env=env_for(kind);native=[str(APP),'-case',str(case),'-overwrite','-timeLimit','110'];shell='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nexport LD_DEBUG=libs\nexec '+shlex.join(native)+'\n'
   rec=bounded(kind+'.native',['taskset','-c',plan['CPU'],'bash','--noprofile','--norc','-c',shell],case,env);status['APPOrCheckerRuns'].append(kind+'.native');out=(T/'logs'/f'{kind}.native.stdout').read_text(errors='replace');err=(T/'logs'/f'{kind}.native.stderr').read_text(errors='replace');loader=any('calling init: '+str(LIB) in line for line in err.splitlines());mesh={n:info(case/'constant/polyMesh'/n) for n in meshNames if(case/'constant/polyMesh'/n).is_file()};write(T/'logs'/f'{kind}.mesh-before-checker.json',mesh)
   obs=[];incomplete=[];prefix='PRIVATE physical-point decision observation: '
   for line in out.splitlines():
    if line.startswith(prefix):
     raw=line[len(prefix):]
     if raw.startswith('{'):obs.append(json.loads(raw,parse_int=int))
     else:incomplete.append(raw)
   result={'nativeStatus':rec,'actualNewPrivateLIBInit':loader,'actualPair':ACTUAL_PAIR,'decisionObservations':obs,'incompleteObservationLines':incomplete,'physicalObserverBothExact1':True,'DWVetoObservation':parse_veto(out,kind),'rawUint64Decoder':'Python parse_int=int, no JS identity promotion','nativeRelevantLines':[x for x in out.splitlines() if any(key in x for key in ['axis-plane kernel','physical-point lifecycle','Normal complete-cavity','postwriter coupled certificate:','dcCart finite point repair:','dcCart finite point proposal:','wider search','wider-search','Total '])],'meshFiles':mesh,'strictAccepted':False,'quality':'native notQualified; stock not executed'}
   if rec.get('returncode')==0 and not rec.get('timedOut') and len(mesh)==8 and loader:
    nativeSets=parse_sets(case/'constant/polyMesh');write(T/'logs'/f'{kind}.native-sets-before-checker.json',nativeSets);check=[str(CHECK),'-case',str(case),*plan['stockOptions']];cs='source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh\nconda activate dcdtuni\nsource '+shlex.quote(str(N/'env.sh'))+'\nunset LD_DEBUG\nexec '+shlex.join(check)+'\n';cr=bounded(kind+'.checkMesh',['taskset','-c',plan['CPU'],'bash','--noprofile','--norc','-c',cs],case,env);status['APPOrCheckerRuns'].append(kind+'.checkMesh');cout=(T/'logs'/f'{kind}.checkMesh.stdout').read_text(errors='replace');failed=re.findall(r'^\s*Failed (\d+) mesh checks\.',cout,re.M);literal=bool(re.search(r'^\s*Mesh OK\.',cout,re.M)) and not re.search(r'^\s*Failed\b',cout,re.M) and bool(re.search(r'^End\s*$',cout,re.M));result.update(checkMeshStatus=cr,literalMeshOK=literal,failedMeshChecks=[int(x) for x in failed],strictAccepted=cr.get('returncode')==0 and not cr.get('timedOut') and literal,fullStockSets=parse_sets(case/'constant/polyMesh'),failureAndWarningLines=[x for x in cout.splitlines() if '***' in x or re.match(r'^\s*Failed \d+ mesh checks',x)]);result['quality']='strict PASS' if result['strictAccepted'] else 'strict RED'
    after={n:info(case/'constant/polyMesh'/n) for n in meshNames};assert after==mesh,'Stock checker changed scientific mesh';write(T/'logs'/f'{kind}.mesh-after-checker.json',after);result['nativeDerivedMetadata']=metadata_output_proof(kind);result['noSourceUIDInventoryOrRoleChange']=True
   result['nativeAfterImmutable11']=case_input_proof(kind,False)
   write(T/'logs'/f'{kind}.analysis.json',result);summary[kind]=result;after=guards();assert after==before,'Focused source/pair/role/original input guard drift';write(T/'guards'/f'{kind}.after.json',after)
  write(T/'logs/pair-summary.json',summary)
  try:comparison=pair_scientific_readback(baseline,CASE)
  except BaseException as e:
   write(T/'logs/pair-scientific-comparison-refused.json',{'collectorException':type(e).__name__+': '+str(e),'requiredArms':['TRACE_OFF','TRACE_ON'],'scientificComparisonComplete':False,'strictAcceptanceClaim':False,'automaticRetry':False})
   raise
  write(T/'logs/pair-scientific-comparison.json',comparison);status['collectorReturncode']=0
 except BaseException as e:status['collectorReturncode']=1;status['collectorException']=type(e).__name__+': '+str(e);raise
 finally:
  status['stopUTC']=datetime.datetime.now(datetime.timezone.utc).isoformat();write(T/'collector-status.json',status)
  try:after=guards();write(T/'guards/run-after.json',after);assert after==before,'Final source/pair/roles/input drift'
  except BaseException as e:write(T/'guards/run-after-refused.json',{'exception':type(e).__name__+': '+str(e),'automaticRepairOrRollback':False});raise
 print(json.dumps({k:{p:v.get(p) for p in ['quality','strictAccepted','nativeRelevantLines','failureAndWarningLines','failedMeshChecks']} for k,v in summary.items()},indent=2))
if __name__=='__main__':main()
