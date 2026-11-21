from pathlib import Path
import json,hashlib,sys,collections,time
import numpy as np
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');A=B/'.codex-tmp/goal-20261005/air';I=A.parent/'integration'
here=Path(__file__).parent;start=time.monotonic()
P=I/'.codex-tmp/p12-D-eligibility-audit-20261005'
M=I/'.codex-tmp/finite-best-hard-checkpoint-native-20261005/full-case-raw-manifest.json'
m=json.loads(M.read_text());case=Path(m['case']);bindings={}
def checked(rel,preferred=None):
 p=preferred if preferred and preferred.is_file()else case/rel
 bb=p.read_bytes();sha=hashlib.sha256(bb).hexdigest();assert sha==m['files'][rel]['sha256'],(rel,sha)
 bindings[rel]={'path':str(p),'sha256':sha,'bytes':len(bb),'frozenP12Match':True};return p,bb
poly=here/'poly';poly.mkdir(exist_ok=True)
for name in ['faces','owner','neighbour','boundary','points']:
 rel='Mesh/processor0/constant/polyMesh/'+name;p,b=checked(rel,P/'selected'/rel)
 if not (poly/name).exists():(poly/name).symlink_to(p)
sys.path.insert(0,str(I/'tools'));import audit_air_mesh_geometry as G
g=G.Geometry(poly)
tables={}
for suffix in ['cell-parent.i32','cell-native.i32','face-base.i32','face-native.i32','point-native.i32']:
 rel='Mesh/conforming-written-rank0-'+suffix;p,bb=checked(rel,P/'selected'/rel);tables[suffix]=np.frombuffer(bb,dtype='<i4')
_,bb=checked('Mesh/conforming-source-rank0-zone.i32');sourcezone=np.frombuffer(bb,dtype='<i4')
seed=json.loads((A/'.codex-tmp/same-origin-packet-design-20261006/actual-halo-witness.json').read_text())
target=28144;edge=(26617,target)
prior=__import__('json').loads((A/'.codex-tmp/complete-interior-edge-vertex-star-20261006/complete-star-input.json').read_text())
assert hashlib.sha256((A/'.codex-tmp/complete-interior-edge-vertex-star-20261006/complete-star-input.json').read_bytes()).hexdigest()=='4640bdc915791838840b88ddf80ebd1da082bf989c45ed7ebe6bcc1d5155c476'
faceids=np.searchsorted(g.offsets,np.flatnonzero(g.vertices==target),side='right')-1
faceids=set(map(int,faceids));edgefaces=[]
for f in faceids:
 ids=list(map(int,g.ids(f)))
 if any({a,b}==set(edge)for a,b in zip(ids,ids[1:]+ids[:1])):edgefaces.append(f)
def endpoints(fs):
 cells={int(g.owner[f])for f in fs}
 cells|={int(g.neighbour[f])for f in fs if f<len(g.neighbour)};return cells
apexCore=endpoints(faceids);vertexcore=apexCore|set(prior['children']);edgecore=endpoints(edgefaces)
def neighbors(core):
 fs=set.union(*(set(map(int,g.cell(c)[0]))for c in core))
 return endpoints(fs),fs
first,corefaces=neighbors(vertexcore);second,firstfaces=neighbors(first)
complete,secondfaces=neighbors(second)
# second includes all metric-endpoint shapes for every first-halo face.
faces=sorted(secondfaces);cells=sorted(second);points=sorted(set(v for f in faces for v in map(int,g.ids(f))))
rows={}
for f in faces:
 base=int(tables['face-base.i32'][f]);rows[str(f)]={'pointIDs':list(map(int,g.ids(f))),'owner':int(g.owner[f]),
 'neighbour':int(g.neighbour[f])if f<len(g.neighbour)else None,'patch':g.patch(f),'sourceBase':base,
 'sourceZone':int(sourcezone[base])if base>=0 else -1,'nativeFace':int(tables['face-native.i32'][f])}
cs={str(c):{'faces':list(map(int,g.cell(c)[0])),'sourceParent':int(tables['cell-parent.i32'][c]),'nativeCell':int(tables['cell-native.i32'][c]),
 'writtenCentre':g.cell(c)[3].tolist(),'writtenVolume':g.cell(c)[4]}for c in cells}
outer=[f for f in corefaces if (int(g.owner[f])in vertexcore)^((int(g.neighbour[f])in vertexcore)if f<len(g.neighbour)else False)]
internal=sorted(set(corefaces)-set(outer));boundarytarget=[f for f in outer if target in rows[str(f)]['pointIDs']]
patchfaces=[f for f in faceids if f>=len(g.neighbour)]
coreCoupled=[{'face':f,'patch':g.patch(f)}for f in corefaces if (g.patch(f)and any(k in g.patch(f)['type'].lower()for k in ['cyclic','processor','ami','mapped']))]
corezones=[{'face':f,'sourceBase':rows[str(f)]['sourceBase'],'sourceZone':rows[str(f)]['sourceZone']}for f in corefaces if rows[str(f)]['sourceZone']>=0]
summary={'scope':'One actual P12 union of complete interior-point stars 28142 and 28144; source roles are witness only.',
 'frozenManifestPath':str(M),'frozenManifestSha256':hashlib.sha256(M.read_bytes()).hexdigest(),
 'targetPoints':[prior['apexWrittenPoint'],target],'apexStarOnlyCells':sorted(apexCore),'priorStarCells':prior['children'],'targetPoint':target,'targetNativePoint':int(tables['point-native.i32'][target]),'targetCoordinates':g.points[target].tolist(),
 'edge':list(edge),'incidentEdgeFaces':sorted(edgefaces),'completeEdgeStarCells':sorted(edgecore),
 'completeVertexIncidentFaces':sorted(faceids),'completeVertexStarCells':sorted(vertexcore),
 'vertexStarExteriorFaces':sorted(outer),'vertexStarInteriorFaces':internal,
 'targetOccursOnExteriorFaces':boundarytarget,'eitherTargetOccursOnExteriorFaces':[f for f in outer if any(p in rows[str(f)]['pointIDs']for p in [prior['apexWrittenPoint'],target])],'targetIsStrictlyCavityInterior':not boundarytarget,
 'targetBoundaryIncidentFaces':[{'face':f,'patch':g.patch(f)}for f in sorted(patchfaces)],
 'cavityCoupledFaces':coreCoupled,'sourceFacetZoneTouches':corezones,
 'counts':{'vertexStarCells':len(vertexcore),'edgeStarCells':len(edgecore),'exteriorFaces':len(outer),'coreFaces':len(corefaces),
 'firstHaloCells':len(first-vertexcore),'secondHaloCells':len(second-first),'completeStoredCells':len(cells),'storedFaces':len(faces),'storedPoints':len(points)},
 'originalSixCellsIncluded':set([19189,538794,535534,535535,538806,538807])<=vertexcore,
 'pointRemovalOrNativeAuthorizationNotClaimed':True,'limitingPriorOuterTrianglesNowInternal':{str(f):f in internal for f in [62157,1577554]},'literalProtectedPhysical2217059External':2217059 in outer,'inputBindings':bindings,'seconds':time.monotonic()-start}
doc={'job':'52d0e89c','rank':0,'children':sorted(vertexcore),'outsideCells':sorted(second-vertexcore),
 'points':{str(p):g.points[p].tolist()for p in points},'faces':rows,'cells':cs,
 'nativePointWitness':{str(p):int(tables['point-native.i32'][p])for p in points},
 'apexWrittenPoint':target,'originalApex':g.points[target].tolist(),
 'coreFaces':sorted(corefaces),'firstHaloCells':sorted(first-vertexcore),'secondHaloCells':sorted(second-first),
 'firstHaloMetricFaces':sorted(firstfaces),'completeMetricEndpointShapes':True}
(here/'complete-star-input.json').write_text(json.dumps(doc,indent=2,allow_nan=False)+'\n')
(here/'star-topology-report.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
print(json.dumps({k:summary[k]for k in ['targetCoordinates','completeEdgeStarCells','completeVertexStarCells','targetIsStrictlyCavityInterior','targetBoundaryIncidentFaces','cavityCoupledFaces','sourceFacetZoneTouches','counts','seconds']}))
