import os,json,re,struct,hashlib,math,time,collections
from pathlib import Path
from fractions import Fraction as Q
os.sched_setaffinity(0,{36});start=time.monotonic()
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');O=B/'.codex-tmp/tiny-native-independent-audit-20261007-v1'
F=B/'.codex-tmp/goal-20261005/air/.codex-tmp/dccart-residual-rerank-native-once-20261007-exclusive/FROZEN-actual-results-v1'
P=B/'.codex-tmp/goal-20261005/air/.codex-tmp/dccart-nonlinear-feasibility-tail-native-hook-plan-20261007/FROZEN-actual-results-v1'
def ident(p):
 a=p.read_bytes();return {'sha256':hashlib.sha256(a).hexdigest(),'bytes':len(a)}
def full(f):
 m=json.loads((f/'MANIFEST.json').read_text());assert isinstance(m['files'],dict)
 for n,d in m['files'].items():assert ident(f/n)==d,n
 assert {str(p.relative_to(f)) for p in f.rglob('*') if p.is_file()}==set(m['files'])|{'MANIFEST.json'}
 return {'manifest':ident(f/'MANIFEST.json'),'files':len(m['files']),'bytes':sum(d['bytes'] for d in m['files'].values())}
def js(n):return json.loads((F/n).read_text())
def mesh(kind):
 m=F/'cases'/kind/'Mesh/constant/polyMesh'
 def head(name,cls):
  a=(m/name).read_bytes();e=a.index(b'}')+1;h=a[:e]
  assert re.search(rb'format\s+binary;',h) and b'LSB;label=32;scalar=64' in h and re.search(rb'class\s+'+cls+rb'\s*;',h)
  return a,e
 def array(a,e,fmt):
  x=re.search(rb'\n\s*(\d+)\s*\(',a[e:]);assert x
  n=int(x[1]);assert 0<=n<=1000000;p=e+x.end();z=p+n*struct.calcsize(fmt);assert a[z:z+1]==b')'
  return [v for v in struct.iter_unpack(fmt,a[p:z])],z+1
 a,e=head('points',b'vectorField');pts,_=array(a,e,'<3d');assert all(math.isfinite(v) for p in pts for v in p)
 a,e=head('faces',b'faceCompactList');off,e=array(a,e,'<i');lab,e=array(a,e,'<i');off=[x[0] for x in off];lab=[x[0] for x in lab];assert off[0]==0 and off[-1]==len(lab)
 faces=[lab[x:y] for x,y in zip(off,off[1:])];assert all(len(v)>=3 and all(0<=p<len(pts) for p in v) for v in faces)
 a,e=head('owner',b'labelList');own,_=array(a,e,'<i');a,e=head('neighbour',b'labelList');nei,_=array(a,e,'<i');assert len(own)==len(faces)
 bf=faces[len(nei):];bp=sorted({p for v in bf for p in v});assert len(bp)==660 and len(bf)==667
 sets={}
 if (m/'sets').exists():
  for p in (m/'sets').iterdir():
   assert p.is_file();a=p.read_bytes();e=a.index(b'}')+1;assert re.search(rb'class\s+(cellSet|faceSet|pointSet)\s*;',a[:e]);v=re.search(rb'\n\s*(\d+)\s*\((.*?)\)',a[e:],re.S);assert v;ids=list(map(int,v[2].split()));assert len(ids)==int(v[1]) and len(set(ids))==len(ids);sets[p.name]=sorted(ids)
 return m,pts,faces,len(nei),bf,bp,sets
r={'scope':'Independent immutable read/math only; CPU36 single; no native/compiler/API/browser/Git/source/service mutation','frozen':full(F),'baselineFrozen':full(P)}
assert r['frozen']['manifest']['sha256']=='38c52b0cabab7412fadf2448fcc5c61d6c06f32352bc3bd9b424f2e2bcfb67df'
new={str(p.relative_to(F/'actual-source39')):ident(p) for p in (F/'actual-source39').rglob('*') if p.is_file()};old={str(p.relative_to(P/'actual-source39')):ident(p) for p in (P/'actual-source39').rglob('*') if p.is_file()};assert new.keys()==old.keys() and len(new)==39
diff=[n for n in new if new[n]!=old[n]];assert diff==['src/mesh/dcCartMesh/dcCartCutCells/dcCartFinitePointRepair.H'];r['source39Difference']=diff;r['actualPair']={n:ident(F/'actual-ELF'/n) for n in ['APP','LIB']}
b=js('actual-build33/source/actual-compilation-units.json');r['freshBuild']={'unitCount':len(b['units']),'objectsSeeded':b['objectsSeeded'],'objectCount':len(b['objects']),'dependencyCount':len(b['dependencies'])};assert r['freshBuild']=={'unitCount':6,'objectsSeeded':0,'objectCount':6,'dependencyCount':6}
for n,d in b['dependencies'].items():assert ident(F/'actual-dependencies6'/n)==d
h=F/'actual-source39/src/mesh/dcCartMesh/dcCartCutCells/dcCartFinitePointRepair.H';assert ident(h)['sha256']=='2bf2eac3a88807a7efde2ddf9802676891e0d9f771a429393adc47e8e320e9a0'
dep=(F/'actual-dependencies6/src/mesh/dcCartMesh/Make/linux64GccDPInt32Opt/dcCartCutCells/dcCartCutCells.C.dep').read_text();assert 'dcCartFinitePointRepair.H' in dep
plan=js('execution-tools/run-plan.json');r['checkerCurrent']=ident(Path(plan['stockCheckMesh']['path']));assert r['checkerCurrent']=={k:plan['stockCheckMesh'][k] for k in ('sha256','bytes')}
tr={};r['status']={};r['loaderLines']={}
for kind in ['RESIDUAL_OFF','RESIDUAL_ON']:
 native=(F/f'runtime/logs/{kind}.native.stdout').read_text();lines=[l for l in native.splitlines() if l.startswith('PRIVATE finite DW veto observation: ')];assert len(lines)==1
 tr[kind]=json.loads(lines[0].split(': ',1)[1]);assert tr[kind]['incomplete'] is False
 status={q:js(f'runtime/logs/{kind}.{q}.status.json') for q in ['native','checkMesh']};assert all(s['returncode']==0 and s['timedOut'] is False for s in status.values());assert status['native']['watchdogSeconds']==60 and status['checkMesh']['watchdogSeconds']==120
 stock=(F/f'runtime/logs/{kind}.checkMesh.stdout').read_text();assert '-allTopology -allGeometry' in stock and 'Enabling all geometry checks.' in stock and '\nEnd\n' in stock
 assert ('\nMesh OK.\n' in stock)==(kind=='RESIDUAL_ON');assert ('Failed 1 mesh checks.' in stock)==(kind=='RESIDUAL_OFF')
 ld=(F/f'runtime/logs/{kind}.native.stderr').read_text();init=[l.strip() for l in ld.splitlines() if 'calling init:' in l and 'libdcCartMesh.so' in l];assert len(init)==1 and plan['nativeRoot'] in init[0];r['loaderLines'][kind]=init
 r['status'][kind]={k:{f:s[f] for f in ['returncode','timedOut','seconds','watchdogSeconds','pid','startTicks']} for k,s in status.items()}
a=tr['RESIDUAL_OFF'];b=tr['RESIDUAL_ON'];assert a['proposals']==b['proposals'][:4] and a['fractions']==b['fractions'][:8];assert len(a['proposals'])==4 and len(b['proposals'])==5 and len(b['fractions'])==13
p5=b['proposals'][4];f5=[x for x in b['fractions'] if x['proposal']==4];assert p5['dimension']==3 and p5['requests']==256 and p5['iterations']<=32 and p5['restorationAttempts']==0 and p5['restorationRequests']==0
assert [(v['checkpoint'],v['called'],v['veto'],v['oldHardDW'],v['nextHardDW']) for v in f5]==[(0,15,4,1,0),(0,15,4,1,0),(0,15,4,1,0),(0,7,3,1,1),(1,127,0,1,0)]
r['trace']={'rawFirst4ProposalsExact':True,'rawFirst8FractionsExact':True,'fifthProposal':p5,'fifthFractions':f5,'interpretation':'dimension3 same original seed reranked; full current veto chain passes checkpoint1 fraction1. Labels are diagnostic; no sourceUID/native-written identity inferred.'}
ma,pa,fa,nia,bfa,bpa,sa=mesh('RESIDUAL_OFF');mb,pb,fb,nib,bfb,bpb,sb=mesh('RESIDUAL_ON');assert len(pa)==len(pb)==1704 and fa==fb and nia==nib==6847 and bpa==bpb
names=['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones'];topo={n:ident(ma/n)==ident(mb/n) for n in names};assert all(v for n,v in topo.items() if n!='points') and topo['points'] is False
base=P/'cases/TAIL_ON/Mesh/constant/polyMesh';assert all((ma/n).read_bytes()==(base/n).read_bytes() for n in names);baseSets={p.name:p.read_bytes() for p in (base/'sets').iterdir()};assert baseSets=={p.name:p.read_bytes() for p in (ma/'sets').iterdir()}
changed=[i for i,(x,y) in enumerate(zip(pa,pb)) if struct.pack('<3d',*x)!=struct.pack('<3d',*y)];assert changed==[687] and not set(changed)&set(bpa);assert sa=={'underdeterminedCells':[1547]} and sb=={}
def conserved(pts,bfaces):
 edge=collections.Counter();v=Q(0);mom=[Q(0)]*3
 for ring in bfaces:
  for x,y in zip(ring,ring[1:]+ring[:1]):edge[x,y]+=1;edge[y,x]-=1
  pp=[[Q(x) for x in pts[p]] for p in ring]
  for i in range(1,len(pp)-1):
   a,b,c=pp[0],pp[i],pp[i+1];d=a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]);v+=d
   for k in range(3):mom[k]+=d*(a[k]+b[k]+c[k])
 assert all(v==0 for v in edge.values());return {'sixVolume':str(v),'twentyFourFirstMoment':list(map(str,mom)),'orientedEdgeClosure':True}
ca=conserved(pa,bfa);cb=conserved(pb,bfb);assert ca==cb
r['writtenScience']={'topologyZonesBytesHeld':topo,'OFFBaseline8AndRawSetsExact':True,'boundaryFaces':667,'boundaryPointBits':660,'changedWrittenPoints':[{'writtenPointID':i,'before':pa[i],'after':pb[i]} for i in changed],'setsOFF':sa,'setsON':sb,'exactBoundaryConservation':ca,'identityScope':'Written point687/cell1547 only; no inverse to native/source UID established.'}
inputs=js('execution-tools/input-bindings.json');assert len(inputs)==12;r['inputProof']={}
for kind in ['RESIDUAL_OFF','RESIDUAL_ON']:
 before=js(f'runtime/logs/{kind}.initial-input-proof.json');assert before['count']==12 and set(before['files'])==set(inputs)
 for n,d in inputs.items():assert ident(Path(d['path']))=={'sha256':d['sha256'],'bytes':d['bytes']}==before['files'][n]
 for n,d in inputs.items():
  if n!='Mesh/constant/dcCartSourceRegions.json':assert ident(F/'cases'/kind/n)==before['files'][n]
 sourcePath=Path(inputs['Mesh/constant/dcCartSourceRegions.json']['path']);orig=json.loads(sourcePath.read_text());cur=json.loads((F/'cases'/kind/'Mesh/constant/dcCartSourceRegions.json').read_text())
 bd=(F/'cases'/kind/'Mesh/constant/polyMesh/boundary').read_text();blocks=re.findall(r'([^\s{}()]+)\s*\{([^{}]*)\}',bd);patch=[]
 for name,block in blocks:
  x=re.search(r'\bnFaces\s+(\d+)\s*;',block);y=re.search(r'\bstartFace\s+(\d+)\s*;',block)
  if x and y:patch.append({'patchIndex':len(patch),'patchName':name,'startFace':int(y[1]),'nFaces':int(x[1])})
 assert len(patch)==12
 def stable(meta):
  meta=json.loads(json.dumps(meta));assert meta['format']=='dcdtuni.native-source-regions/v1'
  for row in meta['regions']:
   for p in row['patchRanges']:p.pop('startFace');p.pop('nFaces')
  return meta
 assert stable(orig)==stable(cur)
 for row in cur['regions']:
  for p in row['patchRanges']:assert p==patch[p['patchIndex']]
 raw=(F/'cases'/kind/'model/source.cfdrun').read_bytes();d=(F/'cases'/kind/'Mesh/system/dcCartMeshDict').read_text();assert hashlib.sha256(raw).hexdigest() in d and 'globallyNoBulkSourcesAndNoMaterialDomains' in d
 r['inputProof'][kind]={'before12HashesExact':True,'afterOther11HashesExact':True,'onlySourceDerivedRangesChanged':True,'schemaIdentityRolesHeld':True,'completeActualPatchRangesBound':True}
assert js('runtime/environment-difference.json')=={'DCCART_PRIVATE_DW_RESIDUAL_RESELECT':[None,'1']};r['variantOnlyFlag']=True
r['experimentGuardBeforeAfterExact']=js('runtime/guards/run-before.json')==js('runtime/guards/run-after.json');assert r['experimentGuardBeforeAfterExact']
r['metadataDefects']=[{'path':'receipt.json / actualPair.actualNativeRun','value':js('receipt.json')['actualPair']['actualNativeRun'],'reason':'False is inherited build-pair metadata; contradicts actual native status/loader/checker raw evidence. Correct only in additive review, preserve frozen original.'},{'path':'execution-tools/run-plan.json / noNewProposalOrQualityCalls','value':plan['noNewProposalOrQualityCalls'],'reason':'Stale/inexact label: intended residual scheduling does admit one extra proposal and quality calls; variant observer alone does not imply no calls.'}]
r['elapsedSeconds']=time.monotonic()-start;r['CPU']=sorted(os.sched_getaffinity(0));(O/'observations.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'FULL':r['frozen'],'CPU':r['CPU'],'sourceDiff':diff,'freshBuild':r['freshBuild'],'changed':changed,'boundary':[667,660],'sets':[sa,sb],'conservation':ca,'prefix':[4,8],'elapsedSeconds':r['elapsedSeconds'],'metadataDefects':r['metadataDefects']}))
