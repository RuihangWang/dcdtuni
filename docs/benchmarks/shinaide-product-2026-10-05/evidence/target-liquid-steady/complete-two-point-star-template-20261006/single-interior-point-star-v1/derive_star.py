from pathlib import Path
from fractions import Fraction as F
from math import isqrt
import json,copy,sys,collections,hashlib,time
import numpy as np
from scipy.optimize import linprog
I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
sys.path.insert(0,str(I/'tools'))
import audit_air_cone_small_union_independent as U
here=Path(__file__).parent
original=json.loads((here.parent/'complete-star-input.json').read_text())
top=json.loads((here.parent/'star-topology-report.json').read_text())
oldcore=set(original['children']);P={int(k):np.array(v)for k,v in original['points'].items()}
outside=U.WrittenStencil(original,original['originalApex'])
outer=[]
planes=[]
for f in top['vertexStarExteriorFaces']:
 r=original['faces'][str(f)];ids=r['pointIDs'] if r['owner']in oldcore else [r['pointIDs'][0]]+r['pointIDs'][:0:-1]
 xyz=np.array([P[v]for v in ids]);sf,fc=outside.face(f)
 if r['owner']not in oldcore:sf=-sf
 n=sf/np.linalg.norm(sf);b=n@fc
 planes.append([*n,b])
 outer.append({'face':f,'ids':ids,'outside':r['neighbour']if r['owner']in oldcore else r['owner'],
               'patch':r['patch'],'sourceZone':r['sourceZone'],'sourceBase':r['sourceBase']})
planes=np.array(planes);nodes=sorted(set(v for r in outer for v in r['ids']))
xyz=np.array([P[v]for v in nodes]);lo=xyz.min(0);span=xyz.max(0)-lo
lp=linprog([0,0,0,-1],A_ub=np.c_[planes[:,:3],np.ones(len(planes))],b_ub=planes[:,3],bounds=[(None,None)]*3+[(0,None)],method='highs')
eligible={'scope':'One complete actual P12 32-cell union of two complete interior vertex-stars, all literal 38 exterior facets/roles retained.',
          'outerFacets':outer,'boundaryNodes':nodes,'planes':planes.tolist(),'inputSha256':hashlib.sha256((here.parent/'complete-star-input.json').read_bytes()).hexdigest(),
          'nonConvexHullMaximumVertexOutsideM':float(max((xyz@planes[:,:3].T-planes[:,3]).flat)),
          'positiveKernelLP':{'success':bool(lp.success),'message':lp.message,'point':lp.x[:3].tolist()if lp.success else None,'radiusM':float(lp.x[3])if lp.success else None}}
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def diff(a,b):return [a-b for a,b in zip(a,b)]
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def sqrtlo(r,bits=192):return F(isqrt((r.numerator<<(2*bits))//r.denominator),1<<bits)
bounds=[]
for r in outer:
 if len(r['ids'])!=3:continue
 pts=[list(map(F,P[v]))for v in r['ids']];n=cross(diff(pts[1],pts[0]),diff(pts[2],pts[0]));A2=dot(n,n)/4
 per=sum(sqrtlo(dot(diff(pts[(j+1)%3],pts[j]),diff(pts[(j+1)%3],pts[j])))for j in range(3));B=128*A2/per**4
 bounds.append({'face':r['face'],'bound':{'numerator':str(B.numerator),'denominator':str(B.denominator)},'approx':float(B),'below001':B<F(1,1000)})
eligible['fixedBoundaryTriangleAnyApexAll4Bound']=bounds
def construct(ap):
 out=copy.deepcopy(original)
 newp=591824;out['points'][str(newp)]=list(map(float,ap))
 nextc=833839
 core=[nextc+j for j in range(len(outer))]
 for c in oldcore:del out['cells'][str(c)]
 for f,r in list(out['faces'].items()):
  if r['owner']in oldcore and r['neighbour']in oldcore:del out['faces'][f]
 edges={}
 for c,row in zip(core,outer):
  f=row['face'];r=out['faces'][str(f)]
  if r['owner']in oldcore:r['owner']=c
  else:r['neighbour']=c
  out['cells'][str(c)]={'faces':[f],'sourceParent':-1,'originCells':sorted(oldcore)}
  ids=row['ids']
  for a,b in zip(ids,ids[1:]+ids[:1]):edges.setdefault(tuple(sorted((a,b))),[]).append((c,[b,a,newp]))
 nf=2251326;newfaces=[]
 for edge,rs in sorted(edges.items()):
  assert len(rs)==2,(edge,rs)
  (a,ring),(b,rr)=rs
  assert set(ring)==set(rr)
  if a>b:a,b=b,a;ring=[ring[0]]+ring[:0:-1]
  out['faces'][str(nf)]={'pointIDs':ring,'owner':a,'neighbour':b,'patch':None,'sourceBase':-1,'sourceZone':-1,'nativeFace':-1}
  for c in (a,b):out['cells'][str(c)]['faces'].append(nf)
  newfaces.append(nf);nf+=1
 out['children']=core;out['apexWrittenPoint']=newp;out['originalApex']=list(map(float,ap));out['firstHaloMetricFaces']=sorted((set(original['firstHaloMetricFaces'])&set(map(int,out['faces'])))|set(newfaces));out,_=U.canonicalize_internal_roles(out)
 for c,r in out['cells'].items():
  assert {int(f)for f,fr in out['faces'].items()if int(c)in [fr['owner'],fr['neighbour']]}==set(r['faces'])
 assert not set(core)&{v for r in original['faces'].values()for v in [r['owner'],r['neighbour']]}
 return out,newp,newfaces
def profile(ap,details=False):
 out,p,newfaces=construct(ap);s=U.WrittenStencil(out,out['originalApex'])
 cs=[s.cell_detail(c)for c in out['children']]
 fs={f:s.face_detail(f)for f in newfaces+original['firstHaloMetricFaces']if str(f)in out['faces']}
 D=[r['D']for r in cs];W=[r.get('weight',1)for r in fs.values()];R=[r.get('ratio',1)for r in fs.values()];S=[r['skew']for r in fs.values()];N=[r.get('nonOrthogonalityDegrees',0)for r in fs.values()]
 summary={'point':list(ap),'Dmin':min(D),'Wmin':min(W),'Rmin':min(R),'Smax':max(S),'Nmax':max(N),
          'counts':dict(D=sum(v<.001 for v in D),W=sum(v<.05 for v in W),R=sum(v<.01 for v in R),S=sum(v>4 for v in S),N=sum(v>70 for v in N)),
          'newStockFaceCentreTriggers':sum(r['faceCentreConcavityPairTriggers']for r in cs),
          'minCentroidPyramidVolume':min(r['minCentroidPyramidVolume']for r in cs)}
 return out,summary,{'cells':cs,'faceMetrics':fs,'newFaces':newfaces,'newPoint':p}if details else {}
if __name__=='__main__':
 (here/'eligibility.json').write_text(json.dumps(eligible,indent=2,allow_nan=False)+'\n')
 print(json.dumps(eligible)[:6000])
 if lp.success and lp.x[3]>0:
  out,q,r=profile(lp.x[:3],True)
  (here/'candidate-chebyshev.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
  (here/'chebyshev-profile.json').write_text(json.dumps({'summary':q,**r},indent=2,allow_nan=False)+'\n')
  print(json.dumps(q))
