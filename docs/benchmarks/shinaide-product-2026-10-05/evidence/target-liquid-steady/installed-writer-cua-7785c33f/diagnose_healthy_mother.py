from pathlib import Path
import sys,importlib.util,json,hashlib,math
from collections import Counter
import numpy as np
A=Path(__file__).resolve().parents[2];E=Path(__file__).resolve().parent
sys.path[:0]=[str(A/'src'),str(A/'tools'),str(E/'readers')]
import air_cone_trace_probe as T
import air_cone_child_union_probe as U
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
G=load('mothergeometry',E/'readers/audit_air_mesh_geometry.py')
Q=load('motherquality',E/'readers/audit_air_cone_remaining_quality.py')
M=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh')
rank=1;parent=30430
t=T.read_trace(M,rank);g=G.Geometry(M/'processor1/constant/polyMesh')
sourcefs=np.flatnonzero((t['faceAlive']!=0)&((t['owner']==parent)|(t['neighbour']==parent)))
polys=[]
for f in sourcefs:
 q=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist()
 polys.append(q if t['owner'][f]==parent else q[::-1])
sg=G.Geometry.from_arrays(t['points'],polys)
source=sg.classify(0,True)
source['sourceFaceIDs']=sourcefs.tolist()
for f in source['faces']:
 f['sourceFace']=int(sourcefs[f['face']])
 f['sourcePatchID']=int(t['patch'][f['sourceFace']]);f['sourceZone']=int(t['zone'][f['sourceFace']])
 f['sourceOwner']=int(t['owner'][f['sourceFace']]);f['sourceNeighbour']=int(t['neighbour'][f['sourceFace']])
for r in source['checkerTriggers']:
 r['sourceFace']=int(sourcefs[r['face']]);r['otherSourceFace']=int(sourcefs[r['otherFace']])
children=np.flatnonzero(t['cellParent']==parent).tolist()
rows=U.merged_boundary(g,children,t)
cg=G.Geometry.from_arrays(g.points,[r['ids'] for r in rows])
whole=cg.classify(0,True)
_,sf,fc,cc,v,*_=cg.cell(0)
selected=[];quality=[];role_regressions=[]
def nangle(sf,cc,nc):
 d=nc-cc
 return float(np.degrees(np.arccos(np.clip(np.dot(d,sf)/(np.linalg.norm(d)*np.linalg.norm(sf)+1e-150),-1,1))))
for i,(r,s,c) in enumerate(zip(rows,sf,fc)):
 f=r['faces'][0];patch=r['patch']
 coupled=patch and patch['type'] in ['cyclic','processor','processorCyclic']
 if patch is None or coupled:selected.append(s)
 own=int(g.owner[f]);nei=int(g.neighbour[f]) if f<len(g.neighbour) else None
 oldchild=own if own in children else nei
 oc=g.cell(oldchild)
 nc=g.cell(r['other']) if r['other'] is not None else None
 before=Q.face_metrics(s,c,oc[3],oc[4],g.points[r['ids']],nc[3] if nc else None,nc[4] if nc else None)
 after=Q.face_metrics(s,c,cc,v,g.points[r['ids']],nc[3] if nc else None,nc[4] if nc else None)
 if nc:
  before['nonorthogonalityDegrees']=nangle(s,oc[3],nc[3])
  after['nonorthogonalityDegrees']=nangle(s,cc,nc[3])
 row={'writtenFace':f,'nativeFace':int(t['faceNative'][f]),'sourceBase':int(t['faceBase'][f]),'oldChild':oldchild,'outsideCell':r['other'],'outsideParent':int(t['cellParent'][r['other']]) if r['other'] is not None else None,'patch':patch,'zone':r['zone'],'ids':r['ids'],'points':g.points[r['ids']].tolist(),'Sf':s.tolist(),'Cf':c.tolist(),'outsideCC':nc[3].tolist() if nc else None,'outsideVolume':nc[4] if nc else None,'before':before,'after':after}
 quality.append(row)
 for k,lim,low in [('weight',.05,True),('volumeRatio',.01,True),('skewness',4,False),('nonorthogonalityDegrees',70,False)]:
  if k in before and ((before[k]>=lim and after[k]<lim) if low else (before[k]<=lim and after[k]>lim)):
   role_regressions.append({'writtenFace':f,'metric':k,'before':before[k],'after':after[k]})
selected_source=[a for f,a in zip(sourcefs,sg.cell(0)[1]) if t['patch'][f]<0]
source['selectedDeterminant']=Q.determinant(selected_source)[0]
whole['selectedDeterminant']=Q.determinant(selected)[0]
whole['sumOriginalChildVolume']=sum(g.cell(c)[4] for c in children)
whole['relativeVolumeErrorToChildren']=abs(v-whole['sumOriginalChildVolume'])/v
whole['sourceCC']=sg.cell(0)[3].tolist()
whole['CCDifferenceFromSourceM']=float(np.linalg.norm(cc-sg.cell(0)[3]))
whole['sourceVolume']=sg.cell(0)[4]
whole['relativeVolumeErrorToSource']=abs(v-sg.cell(0)[4])/v
whole['momentDifferenceFromSourceM4']=(v*cc-sg.cell(0)[4]*sg.cell(0)[3]).tolist()
whole['children']=children
whole['externalFaceRows']=quality
whole['originalPassingRoleRegressions']=role_regressions
summary={'rank':rank,'parent':parent,'writtenChildren':len(children),'sourceFaces':len(sourcefs),'literalWholeShellFaces':len(rows),'sourceStockFaceCentreTriggers':source['checkerTriggerCounts'],'wholeStockFaceCentreTriggers':whole['checkerTriggerCounts'],'sourceDeterminant':source['selectedDeterminant'],'wholeDeterminant':whole['selectedDeterminant'],'wholeBadFaceCounts':{},'passingRoleRegressions':len(role_regressions),'coupledExteriorFaces':sum(bool(r['patch'] and r['patch']['type'] in ['cyclic','processor','processorCyclic']) for r in rows)}
for k,lim,low in [('weight',.05,True),('volumeRatio',.01,True),('skewness',4,False),('nonorthogonalityDegrees',70,False)]:
 q=[r for r in quality if k in r['after']]
 summary['wholeBadFaceCounts'][k]=sum(r['after'][k]<lim if low else r['after'][k]>lim for r in q)
 worst=min(q,key=lambda r:r['after'][k]) if low else max(q,key=lambda r:r['after'][k])
 summary['worst_'+k]={'writtenFace':worst['writtenFace'],'outsideCell':worst['outsideCell'],'outsideParent':worst['outsideParent'],'before':worst['before'][k],'after':worst['after'][k]}
# A closed conformal cube with one xlo plane split into two facets.
pts=np.array([[0,0,0],[0,1,0],[0,1,1],[0,0,1],[1,0,0],[1,1,0],[1,1,1],[1,0,1],[0,.5,0],[0,.5,1]],float)
# Outward cycles, boundary midpoints retained also in zlo/zhi.
polys=[[0,3,9,8],[8,9,2,1],[4,5,6,7],[0,4,7,3],[1,2,6,5],[0,8,1,5,4],[3,7,6,2,9]]
fixture=G.Geometry.from_arrays(pts,polys).classify(0,True)
# Bind exact installed source and dispatch excerpt, not a native checker run.
official=Path('/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/src/OpenFOAM/lnInclude/primitiveMeshCheck.C')
text=official.read_text();i=text.index('bool Foam::primitiveMesh::checkConcaveCells');j=text.index('bool Foam::primitiveMesh::checkUpperTriangular',i)
(E/'stock-checkConcaveCells-exact.txt').write_text(text[i:j])
out={'format':'actual-7785-healthy-mother-vs-literal-restoration/v1','scope':'Read-only installed-formula diagnosis on one actual source mother and a hypothetical literal union of all current children. No pre-cone stock checkMesh job was run. Exterior polygons, coordinates, outside IDs and outside geometry remain literal; mathematical convexity does not override stock face-centre predicate. No mesh mutation.','mesh':str(M),'officialSource':{'path':str(official),'SHA256':hashlib.sha256(official.read_bytes()).hexdigest(),'excerptSHA256':hashlib.sha256((E/'stock-checkConcaveCells-exact.txt').read_bytes()).hexdigest()},'toolSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'summary':summary,'sourceMother':source,'hypotheticalLiteralRestoration':whole,'subdividedCubeFixture':fixture}
(E/'healthy-mother-stock-diagnosis.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(summary,indent=2))
print('FIXTURE',json.dumps({k:fixture[k] for k in ['classification','relativeClosure','volumeM3','boundaryEdgesPairExactly','checkerTriggerCounts']}))
