import pathlib,json,hashlib,re,time,sys,collections,numpy as np
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni');T=B/'.codex-tmp/goal-20261005/integration';A=B/'.codex-tmp/goal-20261005/air'
P=T/'.codex-tmp/finite-native-dw-product-20261005';O=P/'independent-semantic-review';D=T/'.codex-tmp/cases/Shinaide_PointRepairR_CUA';F=T/'.codex-tmp/actual-zone-and-coupled-screen-20261005'
E=T/'docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/finite-native-product-495546f2'
sys.path.insert(0,str(T/'src'))
from backend.aircooling.runner.polymesh_io import foam_header_info,_binary_list_at,_payload_start
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def mapped(path,vec=False):
 with path.open('rb') as f:head=f.read(4096)
 inf=foam_header_info(head);assert inf==dict(format='binary',label_bytes=4,scalar_bytes=8)
 n,offset=_binary_list_at(head,_payload_start(head))
 return np.memmap(path,dtype='<f8' if vec else '<i4',mode='r',offset=offset,shape=(n,3)if vec else(n,))
def mappedfaces(path):
 off=mapped(path)
 with path.open('rb') as f:
  head=f.read(4096);n,start=_binary_list_at(head,_payload_start(head));pos=start+n*4
  f.seek(pos);rest=f.read(512)
 idx=rest.index(b')');nv,vo=_binary_list_at(rest,idx+1)
 verts=np.memmap(path,dtype='<i4',mode='r',offset=pos+vo,shape=(nv,))
 assert len(off)>1 and off[0]==0 and off[-1]==len(verts)
 return off,verts
def patches(poly):
 out=[]
 for name,body in re.findall(r'(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}',(poly/'boundary').read_text()):
  start=re.search(r'startFace\s+(\d+)',body);count=re.search(r'nFaces\s+(\d+)',body);kind=re.search(r'type\s+(\w+)',body)
  if start and count and kind:out.append(dict(name=name,type=kind[1],start=int(start[1]),count=int(count[1]),body=body.strip()))
 return out
def coupled(k):return k in {'processor','processorCyclic','cyclic','cyclicAMI','cyclicACMI'}
def patch(f,ps):return next((p for p in ps if p['start']<=f<p['start']+p['count']),None)
def cfaces(cs,ow,ne):
 return np.union1d(np.flatnonzero(np.isin(ow,sorted(cs),kind='table')),np.flatnonzero(np.isin(ne,sorted(cs),kind='table')))
actual=json.loads((F/'actual-role-census.json').read_text());recovered=json.loads((F/'recovered-points-and-graph.json').read_text())['recoveredPoints']
assert len(recovered)==33
assert json.loads((P/'independent-review/topology-identity-before-label-comparison.json').read_text())['matched56of56']
cfg=json.loads((D/'model/Shinaide_liquid.cfdrun').read_text())
porous={v['patchName']:dict(sourceKey=k,kind='porousBinding',type=v['type'],objectPath=v.get('objectPath'),surfaceUid=v.get('surfaceUid'),parameters=v.get('parameters')) for k,v in cfg['porousBindings'].items()}
boundary={v['patchName']:dict(sourceKey=k,kind='boundaryBinding',role=v.get('role'),bcType=v.get('bcType'),objectPath=v.get('objectPath'),surfaceUid=v.get('surfaceUid')) for k,v in cfg['boundaryBindings'].items()}
started=time.monotonic();out=[];bindings={};bytesarrays=0
for rank in sorted({x['rank']for x in recovered}):
 poly=D/f'Mesh/processor{rank}/constant/polyMesh'
 ow=mapped(poly/'owner');ne=mapped(poly/'neighbour');off,verts=mappedfaces(poly/'faces');ps=patches(poly)
 targets=[x for x in recovered if x['rank']==rank];pids=[x['point']for x in targets]
 hits=np.flatnonzero(np.isin(verts,pids,kind='table'));faceids=np.searchsorted(off[1:],hits,side='right');pf=collections.defaultdict(set)
 for pt,face in zip(verts[hits],faceids):pf[int(pt)].add(int(face))
 frozen={x['point']:x for x in actual['ranks'][rank]['pointRows']}
 original=json.loads((A/f'.codex-tmp/shared-point-components-7785-20261005/rank{rank}-mobility-bound.json').read_text());origrow={x['point']:x for x in original['pointRows']}
 bytesarrays+=ow.nbytes+ne.nbytes+off.nbytes+verts.nbytes
 for name in ['cellZones','faceZones','pointZones','boundary']:bindings[str(poly/name)]=sha(poly/name)
 for rec in targets:
  pt=rec['point'];row=frozen[pt];inc=sorted(pf[pt])
  assert inc==origrow[pt]['incidentFaces']
  assert all(f<len(ne) for f in inc),'moving point touches a boundary facet'
  core=set(map(int,ow[inc]))|{int(ne[f])for f in inc};assert sorted(core)==row['coreCells']
  cf=cfaces(core,ow,ne);outer=set(map(int,ow[cf]))|{int(ne[f])for f in cf if f<len(ne)};outside=outer-core
  assert sorted(outside)==row['outsideCells']
  fullcf=cfaces(core|outside,ow,ne)
  coreB=[];outB=[]
  for f in fullcf:
   f=int(f);b=patch(f,ps)
   if b:
    match=porous.get(b['name']) or boundary.get(b['name'])
    entry=dict(face=f,owner=int(ow[f]),patch=b['name'],type=b['type'],sourceRole=match,coupled=coupled(b['type']),containsMovingPoint=pt in verts[off[f]:off[f+1]])
    (coreB if int(ow[f]) in core else outB).append(entry)
  assert not any(x['containsMovingPoint']for x in coreB+outB)
  assert not any(x['coupled']for x in coreB)
  out.append(dict(rank=rank,point=pt,nativePoint=rec['nativePoint'],oldReasons=rec['oldReasons'],incidentFaces=inc,coreCells=sorted(core),outsideCells=sorted(outside),allCoreFaces=list(map(int,cf)),allCompleteCoreOutsideFaces=list(map(int,fullcf)),coreBoundaryFacets=coreB,outsideBoundaryFacets=outB,movingPointTouchesAnyBoundary=False,coreCoupledOwnerFalse=True,allPhysicalBoundaryCoordinatesUnmodifiedByThisPoint=True,actualZones='empty point/cell/face membership already frozen census and same56 topology',coreExternalPhysicalPatchNames=sorted({x['patch']for x in coreB if not x['coupled']}),outsideCoupledPatchNames=sorted({x['patch']for x in outB if x['coupled']})))
unique={}
for row in out:
 for b in row['coreBoundaryFacets']+row['outsideBoundaryFacets']:
  unique[b['patch']]=dict(type=b['type'],sourceRole=b['sourceRole'],coupled=b['coupled'])
res=dict(scope='Complete sparse current ce178f4a topology reconstruction of 33 recovered metadata points; no proposal/metrics/point movement. All moving point incident faces and complete core/outside membership independently equal frozen census. Current vs495 topology exact was checked first.',input=dict(cfdrunPath=str(D/'model/Shinaide_liquid.cfdrun'),cfdrunSHA256=sha(D/'model/Shinaide_liquid.cfdrun'),priorCensusSHA256=sha(F/'actual-role-census.json'),topologyAuditSHA256=sha(P/'independent-review/topology-identity-before-label-comparison.json')),pointCount=len(out),rows=out,uniqueBoundaryRoleMappings=unique,unknownNoncoupledPatchNames=sorted(k for k,v in unique.items()if not v['coupled']and v['sourceRole']is None),reboundSmallInputs=bindings,largeReadScope='Memorymap selected four ranks owner/neighbour/face offsets+vertices; never instantiate full Geometry or read all point coordinates/full2GB mesh. Owner/neighbour and vertex arrays only are scanned to independently close point-star membership.',mappedArrayBytes=bytesarrays,elapsedSeconds=time.monotonic()-started)
(O/'recovered33-complete-current-star-and-source-role.json').write_text(json.dumps(res,indent=2)+'\n')
print(json.dumps(dict(points=len(out),uniqueMappings=unique,unknown=res['unknownNoncoupledPatchNames'],mappedArrayBytes=bytesarrays,elapsed=res['elapsedSeconds']),indent=2))
