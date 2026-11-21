
from pathlib import Path
import json,hashlib,re,os,datetime
os.sched_setaffinity(0,{31})
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');O=B/'.codex-tmp/dccart-kernel-three-flag-tiny-plan-shared-20261007';F=O/'FROZEN-plan-v1';R=O/'runtime';W=B/'.codex-tmp/dccart-kernel-flags-independent-review-20261007/runtime-review'
def digest(p):
 b=Path(p).read_bytes();return {'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
def load(p):return json.loads(Path(p).read_text(),parse_int=int)
plan=load(F/'plan.json');inputs=load(F/'input-bindings.json');assert len(inputs)==12
manifest=load(F/'MANIFEST.json');assert digest(F/'MANIFEST.json')['sha256']=='5fb38c7faef9d2003e8895ad3b50a5c3ec55b82a5770caf0acd39ddd33406129';assert all(digest(F/p)==v for p,v in manifest['files'].items())
primary={str(p.relative_to(O)):digest(p) for p in sorted(R.rglob('*')) if p.is_file()};assert len(primary)==34
summary=load(R/'logs/pair-summary.json');assert set(summary)=={'BASE','AXIS'}
auth=load(R/'authorization-binding.json');assert auth['exactApprovedPlanManifestSHA']==digest(F/'MANIFEST.json')['sha256'];assert auth['pair']==plan['pair'];assert auth['runtimeFlagSupplied'] is True
original=[];sets={};native={};metadata={};mesh={};observations={}
family=['underdeterminedCells','nonOrthoFaces','lowWeightFaces','lowVolRatioFaces']
def parse_set(p,expected_class):
 s=p.read_text();assert re.search(r'\bclass\s+'+expected_class+r'\s*;',s);assert re.search(r'\bobject\s+'+p.name+r'\s*;',s)
 clean=re.sub(r'/\*.*?\*/','',s,flags=re.S);clean=re.sub(r'//[^\n]*','',clean)
 m=re.search(r'}\s*(\d+)\s*\((.*?)\)\s*$',clean,re.S);assert m,'incomplete stock list '+str(p)
 assert re.fullmatch(r'\s*(?:\d+\s*)*',m[2]);v=[int(x) for x in m[2].split()];assert len(v)==int(m[1]) and len(set(v))==len(v)
 return sorted(v)
def stable(v):
 v=json.loads(json.dumps(v));assert v['format']=='dcdtuni.native-source-regions/v1'
 for r in v['regions']:
  for p in r['patchRanges']:
   assert all(type(p[x]) is int and p[x]>=0 for x in ['patchIndex','startFace','nFaces']);del p['startFace'];del p['nFaces']
 return v
def boundary(p):
 raw=p.read_text();assert len(raw.encode())<=65536;clean=re.sub(r'/\*.*?\*/','',raw,flags=re.S);clean=re.sub(r'//[^\n]*','',clean)
 m=re.search(r'\n\s*(\d+)\s*\(\s*(.*?)\s*\)\s*$',clean,re.S);assert m
 patches=[];pos=0
 for q in re.finditer(r'\s*("[^"\n]+"|[^\s{}()]+)\s*\{([^{}]*)\}',m[2],re.S):
  assert not m[2][pos:q.start()].strip();pos=q.end();row={'patchIndex':len(patches),'patchName':q[1].strip('"')}
  for key in ['startFace','nFaces']:
   n=re.findall(r'\b'+key+r'\s+(\d+)\s*;',q[2]);assert len(n)==1;row[key]=int(n[0])
  patches.append(row)
 assert not m[2][pos:].strip() and len(patches)==int(m[1]);return patches
for kind in ['BASE','AXIS']:
 C=O/'cases'/kind;assert C.resolve().is_relative_to(B) and not C.is_symlink()
 for rel,v in inputs.items():
  assert not(C/rel).is_symlink();old=digest(F/'input'/rel);assert old=={k:v[k] for k in ['sha256','bytes']};now=digest(C/rel)
  if rel!='Mesh/constant/dcCartSourceRegions.json':assert now==old
  original.append({'kind':kind,'path':rel,'original':old,'actual':now,'derivedNativeMetadata':rel=='Mesh/constant/dcCartSourceRegions.json'})
 nstatus=load(R/f'logs/{kind}.native.status.json');cstatus=load(R/f'logs/{kind}.checkMesh.status.json')
 for mode,status in [('native',nstatus),('checkMesh',cstatus)]:
  assert status['returncode']==0 and status['timedOut'] is False and status['watchdogSeconds']==120
  assert status['cwd']==str(C/'Mesh') and status['environment']['CUDA_VISIBLE_DEVICES']=='';assert status['argv'][:3]==['taskset','-c','36,37']
  assert status['nativeFlags']=={**plan['commonFlags'],**(plan['axisOnlyDelta'] if kind=='AXIS' else {})}
  if mode=='checkMesh':assert plan['stockCheckMesh']['path'] in status['argv'][-1] and '-allTopology -allGeometry' in status['argv'][-1]
  else:assert plan['pair']['APP']['path'] in status['argv'][-1] and '-overwrite -timeLimit 110' in status['argv'][-1]
  stdout=(R/f'logs/{kind}.{mode}.stdout').read_text();assert 'nProcs : 1' in stdout and 'Host   : 40901' in stdout and f'PID    : {status["pid"]}' in stdout
  assert load(R/f'logs/{kind}.{mode}.started.json')=={k:v for k,v in status.items() if k not in ['pid','startTicks','returncode','timedOut','seconds','stopUTC']}
 s=(R/f'logs/{kind}.native.stdout').read_text();e=(R/f'logs/{kind}.native.stderr').read_text();calls=[x for x in e.splitlines() if 'calling init: '+plan['pair']['LIB']['path'] in x];assert len(calls)==1;assert 'FOAM FATAL' not in s+e
 prefix='PRIVATE physical-point decision observation: ';obs=[json.loads(x[len(prefix):],parse_int=int) for x in s.splitlines() if x.startswith(prefix)]
 assert obs==summary[kind]['decisionObservations'];assert len(obs)==1 and obs[0]['rank']==0 and obs[0]['complete']==1 and obs[0]['rows']==len(obs[0]['attempts'])
 observations[kind]={'rank':obs[0]['rank'],'complete':obs[0]['complete'],'rows':obs[0]['rows'],'stackBytes':obs[0]['stackBytes'],'capturePassing':sum(x['capture']['ok'] for x in obs[0]['attempts']),'stageEvaluated':sum(x['stage']['evaluated'] for x in obs[0]['attempts']),'commitEvaluated':sum(x['commit']['evaluated'] for x in obs[0]['attempts']),'determinantMapCapture':obs[0]['determinantMapCapture']}
 cout=(R/f'logs/{kind}.checkMesh.stdout').read_text();failed=re.findall(r'^Failed (\d+) mesh checks\.',cout,re.M);assert len(failed)==1 and not re.search(r'^Mesh OK\.',cout,re.M)
 assert summary[kind]['strictAccepted'] is False and summary[kind]['actualPrivateLIBInit'] is True and summary[kind]['quality']=='strict RED'
 files={p.name:digest(p) for p in (C/'Mesh/constant/polyMesh').iterdir() if p.is_file()};assert set(files)=={'points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones'}
 assert files==load(R/f'logs/{kind}.mesh-before-checker.json')==load(R/f'logs/{kind}.mesh-after-checker.json')==summary[kind]['meshFiles'];mesh[kind]=files
 sets[kind]={}
 for name in family:
  p=C/'Mesh/constant/polyMesh/sets'/name;sets[kind][name]=parse_set(p,'cellSet' if name=='underdeterminedCells' else 'faceSet') if p.exists() else []
 countlines={'underdeterminedCells':r'Cells with small determinant .*?number of cells: (\d+)','lowWeightFaces':r'Faces with small interpolation weight .*?number of faces: (\d+)','lowVolRatioFaces':r'Faces with small volume ratio .*?number of faces: (\d+)','nonOrthoFaces':r'Number of severely non-orthogonal .*?faces: (\d+)'}
 for name,pattern in countlines.items():
  m=re.search(pattern,cout);assert len(sets[kind][name])==(int(m[1]) if m else 0)
 qfiles={str(p.relative_to(C/'Mesh/constant/polyMesh')):digest(p) for p in (C/'Mesh/constant/polyMesh/sets').rglob('*') if p.is_file()};assert qfiles==summary[kind]['qualitySets']
 current=load(C/'Mesh/constant/dcCartSourceRegions.json');old=load(F/'input/Mesh/constant/dcCartSourceRegions.json');assert stable(current)==stable(old)
 patches=boundary(C/'Mesh/constant/polyMesh/boundary');bind=[]
 for row in current['regions']:
  for p in row['patchRanges']:
   assert p==patches[p['patchIndex']];bind.append({'sourceRegionUid':row['sourceRegionUid'],'patch':p})
 assert summary[kind]['nativeDerivedMetadata']['stableSchemaAndAllIdentityExact'] is True
 assert summary[kind]['nativeDerivedMetadata']['nativeBefore']==digest(F/'input/Mesh/constant/dcCartSourceRegions.json') and summary[kind]['nativeDerivedMetadata']['nativeAfter']==digest(C/'Mesh/constant/dcCartSourceRegions.json')
 metadata[kind]={'schemaUIDInventoryRolesUnchangedExceptDerivedRanges':True,'regions':len(current['regions']),'patchCount':len(patches),'boundOriginalSourceRanges':bind,'hash':digest(C/'Mesh/constant/dcCartSourceRegions.json')}
 native[kind]={'nativeRC':nstatus['returncode'],'nativeOuterSeconds':nstatus['seconds'],'stockRC':cstatus['returncode'],'stockOuterSeconds':cstatus['seconds'],'timedOut':False,'privateLIBInit':calls,'nativeTotalSeconds':float(re.search(r'^Total ([0-9.]+) s$',s,re.M)[1]),'failedChecks':int(failed[0]),'strictAccepted':False,'kernelLines':[x for x in s.splitlines() if 'axis-plane kernel' in x]}
for n in ['faces','owner','neighbour','boundary']:assert mesh['BASE'][n]==mesh['AXIS'][n]
changes={k:{'removed':sorted(set(sets['BASE'][k])-set(sets['AXIS'][k])),'added':sorted(set(sets['AXIS'][k])-set(sets['BASE'][k]))} for k in family};assert all(not v['added'] for v in changes.values())
root=load(O/'ROOT-stock-sets-independent-offline.json');assert root['sets']==sets and root['changes']==changes
h=[];guardCount=0;guardBytes=0
for key in ['sourceCohortFile','privateSourceCohortFile','servedFilesFile']:
 expected=load(F/plan[key]);groups=expected if key=='sourceCohortFile' else {key:expected}
 for name,rows in groups.items():
  for p,v in rows.items():
   q=digest(p);assert q==v;guardCount+=1;guardBytes+=q['bytes']
 h.append({'key':key,'groups':{n:len(v) for n,v in groups.items()}})
roles=load(F/plan['ownedRolesFile']);actualRoles={}
for pid in roles:
 d=Path('/proc',pid);sp=d.joinpath('stat').read_text().rsplit(')',1)[1].split();uid=int(next(x.split()[1] for x in d.joinpath('status').read_text().splitlines() if x.startswith('Uid:')));actualRoles[pid]={'uid':uid,'start':sp[19],'cwd':os.readlink(d/'cwd'),'argvSHA256':hashlib.sha256(d.joinpath('cmdline').read_bytes()).hexdigest()}
assert actualRoles==roles
for v in plan['pair'].values():assert digest(v['path'])=={k:v[k] for k in ['sha256','bytes']}
v=plan['stockCheckMesh'];assert digest(v['path'])=={k:v[k] for k in ['sha256','bytes']}
for p,v in plan['externalReferences'].items():assert digest(p)==v
actualGroups={}
for key in ['sourceCohortFile','privateSourceCohortFile','servedFilesFile']:actualGroups[key]=load(F/plan[key])
actualGroups['roles']=actualRoles;fingerprint=hashlib.sha256(json.dumps(actualGroups,sort_keys=True,separators=(',',':')).encode()).hexdigest()
recorded=[load(p) for p in sorted((R/'guards').glob('*.json'))];assert len(recorded)==6 and all(x['sourceFingerprint']==fingerprint for x in recorded)
result={'scope':'independent primary read plus pure offline parse; no native/compiler/service/browser/Git mutation','createdUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'planManifestSHA256':digest(F/'MANIFEST.json')['sha256'],'primaryReadCount':len(primary),'primaryReadBytes':sum(v['bytes'] for v in primary.values()),'primaryBindings':primary,'originalInputs':original,'native':native,'sets':sets,'changes':changes,'topologyIdentical':['faces','owner','neighbour','boundary'],'meshFiles':mesh,'metadata':metadata,'physicalObservationSummary':observations,'currentGuardCount':guardCount,'currentGuardBytes':guardBytes,'currentOwnedRolesExact':True,'currentOwnedRoleCount':len(roles),'currentRecordedFingerprintEqual':fingerprint,'allSixRecordedGuardsExact':True,'allAddedSetsEmpty':True,'strictAcceptance':False,'formalFrozenAuthorPacketBound':False}
(W/'primary-independent-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['primaryReadCount','primaryReadBytes','native','sets','changes','currentGuardCount','currentGuardBytes','currentOwnedRolesExact','allSixRecordedGuardsExact','strictAcceptance']},indent=2))
