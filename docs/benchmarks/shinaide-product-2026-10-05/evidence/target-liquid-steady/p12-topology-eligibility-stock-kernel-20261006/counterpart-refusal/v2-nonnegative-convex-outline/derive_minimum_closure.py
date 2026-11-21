from pathlib import Path
import sys,json,copy,itertools,hashlib
from collections import Counter
from fractions import Fraction as F
import numpy as np
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration'
sys.path.insert(0,str(I/'tools'))
import audit_air_cone_small_union_independent as U
here=Path(__file__).parent;raw=json.loads((here/'actual-halo-witness.json').read_text())
points={str(r['point']):r['coordinates'] for r in raw['points']}
faces={str(r['face']):{'pointIDs':r['pointIDs'],'owner':r['owner'],'neighbour':r['neighbour'] if r['neighbour']>=0 else None,
 'patch':r['patch'],'sourceBase':r['sourceBase'],'sourceZone':r['writtenZoneMembership'],'nativeFace':r['nativeFace']}for r in raw['faces']}
cells={str(r['cell']):{'faces':r['faces'],'sourceParent':r['sourceParent'],'writtenCentre':r['computedCentreM'],
 'writtenVolume':r['computedVolumeM3'],'nativeCell':r['nativeCell']}for r in raw['cells']}
seed=raw['pair']['cells'];apex=faces[str(raw['pair']['sharedFace'])]['pointIDs'][0]
doc={'children':seed[:],'outsideCells':[int(k) for k in cells if int(k) not in seed],
 'points':points,'faces':faces,'cells':cells,'apexWrittenPoint':apex,'originalApex':points[str(apex)]}
def det(a,b,c,d):
 p=[[F(x)for x in q]for q in [a,b,c,d]]
 u=[p[1][i]-p[0][i]for i in range(3)];v=[p[2][i]-p[0][i]for i in range(3)];w=[p[3][i]-p[0][i]for i in range(3)]
 return u[0]*(v[1]*w[2]-v[2]*w[1])-u[1]*(v[0]*w[2]-v[2]*w[0])+u[2]*(v[0]*w[1]-v[1]*w[0])
def exact_plane(ids):
 p=[points[str(v)]for v in ids]
 return all(det(*(p[i]for i in c))==0 for c in itertools.combinations(range(len(p)),4))
def outside(row,c):return row['neighbour']if row['owner']==c else row['owner']
# One automatic breadth-first closure only, at most three pair groups.
trial,_,outer=U.union_document(doc,seed);s=U.WrittenStencil(trial,trial['originalApex']);rep=min(seed);groups=[seed[:]];needs=[]
for f,k in itertools.combinations(outer,2):
 row=s.faces[f];rr=s.faces[k];ids=list(dict.fromkeys(row['pointIDs']+rr['pointIDs']))
 sf,fc=s.face(f);sk,kc=s.face(k);sf=sf if row['owner']==rep else -sf;sk=sk if rr['owner']==rep else -sk
 if not exact_plane(ids)or np.dot(sf,sk)<=0:continue
 a,b=outside(row,rep),outside(rr,rep)
 if a is None or b is None or a==b:continue
 pair=sorted([a,b]);shared=set(cells[str(a)]['faces'])&set(cells[str(b)]['faces'])
 if len(shared)!=1 or cells[str(a)]['sourceParent']!=cells[str(b)]['sourceParent']:
  needs.append({'unsupportedPair':pair,'reason':'not one shared same-origin face'});continue
 if any(set(pair)&set(g)for g in groups):continue
 groups.append(pair)
 if len(groups)>3:raise ValueError('closure exceeds three-pair cap')
core=sorted(set(v for g in groups for v in g));doc['children']=core;doc['outsideCells']=sorted(set(map(int,cells))-set(core))
after=copy.deepcopy(doc);removed=[]
for group in groups:
 after,r,o=U.union_document(after,group);removed.extend(r)
after,flipped=U.canonicalize_internal_roles(after)
lineage={int(f):[int(f)]for f in after['faces']}
# Exact projected signed-area + first-moment.
def moments(ids,axis):
 axes=[j for j in range(3)if j!=axis];p=[[F(x)for x in points[str(v)]]for v in ids]
 area=F(0);m=[F(0),F(0),F(0)]
 for a,b in zip(p,p[1:]+p[:1]):
  cross=a[axes[0]]*b[axes[1]]-b[axes[0]]*a[axes[1]];area+=cross
  for j in axes:m[j]+=(a[j]+b[j])*cross
 area/=2
 for j in axes:m[j]/=6
 # Plane reconstruct omitted coordinate using exact three-point normal.
 a,b,c=next((a,b,c)for a,b,c in itertools.combinations(p,3)if any(
 (b[(j+1)%3]-a[(j+1)%3])*(c[(j+2)%3]-a[(j+2)%3])-(b[(j+2)%3]-a[(j+2)%3])*(c[(j+1)%3]-a[(j+1)%3])!=0
 for j in range(3)))
 u=[b[i]-a[i]for i in range(3)];v=[c[i]-a[i]for i in range(3)]
 n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
 if n[axis]==0:raise ValueError('degenerate projected plane')
 plane=sum(n[j]*a[j]for j in range(3))
 m[axis]=(plane*area-sum(n[j]*m[j]for j in axes))/n[axis]
 return area,m
def join(f,k):
 a,b=after['faces'][str(f)],after['faces'][str(k)]
 for key in ['owner','neighbour','patch','sourceZone','sourceBase']:
  if a[key]!=b[key]:return None
 ids=list(dict.fromkeys(a['pointIDs']+b['pointIDs']))
 if not exact_plane(ids):return None
 edge=Counter()
 for r in [a,b]:
  for x,y in zip(r['pointIDs'],r['pointIDs'][1:]+r['pointIDs'][:1]):edge[x,y]+=1
 common=[(x,y)for x,y in edge if x<y and edge[x,y]==1 and edge[y,x]==1]
 if len(common)!=1:return None
 x,y=common[0];del edge[x,y];del edge[y,x]
 if any(n!=1 for n in edge.values()):return None
 successor={}
 for x,y in edge:
  if x in successor:return None
  successor[x]=y
 start=min(successor,key=lambda v:tuple(points[str(v)]));loop=[start]
 while successor[loop[-1]]!=start:
  if successor[loop[-1]]in loop:return None
  loop.append(successor[loop[-1]])
 if len(loop)!=len(successor)or set(loop)!=set(ids):return None
 p=np.array([points[str(x)]for x in loop]);normal=U.WrittenStencil(after,after['originalApex']).face(f)[0]
 axis=int(np.argmax(abs(normal)))
 if np.linalg.norm(normal)<=0:return None
 # Strict convex turns via exact dominant projection; no collinear vertex deletion.
 axes=[j for j in range(3)if j!=axis];fp=[[F(x)for x in points[str(v)]]for v in loop];turn=[]
 for i in range(len(fp)):
  a0,b0,c0=fp[i],fp[(i+1)%len(fp)],fp[(i+2)%len(fp)]
  turn.append((b0[axes[0]]-a0[axes[0]])*(c0[axes[1]]-b0[axes[1]])-(b0[axes[1]]-a0[axes[1]])*(c0[axes[0]]-b0[axes[0]]))
 if not((all(v>=0 for v in turn)and any(v>0 for v in turn))or(all(v<=0 for v in turn)and any(v<0 for v in turn))):return None
 sign=1 if any(v>0 for v in turn)else -1
 for i,a0 in enumerate(fp):
  b0=fp[(i+1)%len(fp)]
  for c0 in fp:
   support=(b0[axes[0]]-a0[axes[0]])*(c0[axes[1]]-a0[axes[1]])-(b0[axes[1]]-a0[axes[1]])*(c0[axes[0]]-a0[axes[0]])
   if sign*support<0:return None
 before=[moments(a['pointIDs'],axis),moments(b['pointIDs'],axis)];now=moments(loop,axis)
 if now[0]!=before[0][0]+before[1][0]or any(now[1][j]!=before[0][1][j]+before[1][1][j]for j in range(3)):
  raise ValueError('exact area/M1 mismatch')
 if before[0][0]*before[1][0]<=0:return None
 return loop
joined=[];changed=True
while changed:
 changed=False
 for f,k in itertools.combinations(sorted(map(int,after['faces'])),2):
  if not(any(after['faces'][str(f)]['owner']in after['children']or after['faces'][str(f)]['neighbour']in after['children']for _ in [0])):continue
  loop=join(f,k)
  if loop is None:continue
  a,b=after['faces'][str(f)],after['faces'][str(k)]
  oldids=lineage[f]+lineage[k];lineage[f]=oldids;del lineage[k]
  after['faces'][str(f)]['pointIDs']=loop;del after['faces'][str(k)]
  for c in [a['owner'],a['neighbour']]:
   if c is not None:after['cells'][str(c)]['faces']=[x for x in after['cells'][str(c)]['faces']if x!=k]
  joined.append({'keptFace':f,'removedFace':k,'originalFacets':oldids,'points':loop,'owner':a['owner'],'neighbour':a['neighbour'],'sourceBase':a['sourceBase'],
                 'exactPlane':True,'allOriginalOutlinePointsRetained':True,'collinearNodesRetainedUnderExactNonnegativeConvexSupport':True,'exactSignedAreaAnd3DFirstMomentPreserved':True})
  changed=True;break
# Full geometry of all stored cells; no geometry gate discarded.
st=U.WrittenStencil(after,after['originalApex']);details=[st.cell_detail(c)for c in after['children']]
oldst=U.WrittenStencil(doc,doc['originalApex'])
oldV=sum(oldst.cell(c)[3]for c in core);newV=sum(st.cell(c)[3]for c in after['children'])
oldM=sum((oldst.cell(c)[3]*oldst.cell(c)[2]for c in core),np.zeros(3));newM=sum((st.cell(c)[3]*st.cell(c)[2]for c in after['children']),np.zeros(3))
report={'scope':'One automatic three-pair counterpart closure. Pure actual-array topology/geometry; not installed/native acceptance.',
 'seed':seed,'groups':groups,'unsupportedClosure':needs,'removedPairFaces':removed,'faceCoalesces':joined,'canonicalFlips':flipped,
 'coreSourceOrigins':{str(c):doc['cells'][str(c)]['sourceParent']for c in core},
 'newCells':details,'coreVolumeRelativeError':abs(newV-oldV)/oldV,'coreFirstMomentDelta':(newM-oldM).tolist(),
 'boundaryFacesWithDifferentRemainingOutsideIDsNeverCoalesced':True,'pointCoordinatesAllUnchanged':True,
 'stockConcavityTriggers':sum(r['faceCentreConcavityPairTriggers']for r in details),
 'nativeSourceAuthorizationNotInferredFromTrace':True}
for name,obj in [('before.json',doc),('candidate.json',after),('closure-report.json',report),('face-lineage.json',lineage)]:
 (here/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
print(json.dumps({'groups':groups,'joins':joined,'D':[r['D']for r in details],
 'concavity':[r['faceCentreConcavityPairTriggers']for r in details],
 'halfspace':[r['maximumVertexOutsideM']for r in details],'pyramid':[r['minCentroidPyramidVolume']for r in details],
 'closure':[r['relativeClosure']for r in details],'volumeRel':report['coreVolumeRelativeError']},indent=2))
