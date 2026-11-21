import json,hashlib,itertools
from pathlib import Path
from fractions import Fraction as F
root=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp');base=root/'complete-two-point-star-template-20261006';out=root/'two-lobe-separator-independent-20261006'
a=json.loads((base/'complete-star-input.json').read_bytes());r=json.loads((out/'separator-topology-geometry-audit.json').read_bytes());P={int(p):tuple(F.from_float(x) for x in xyz) for p,xyz in a['points'].items()}
sub=lambda u,v:tuple(x-y for x,y in zip(u,v));dot=lambda u,v:sum((x*y for x,y in zip(u,v)),F(0));cross=lambda u,v:(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]);cross2=lambda u,v:u[0]*v[1]-u[1]*v[0]
def normal(xyz):
 for i in range(1,len(xyz)-1):
  n=cross(sub(xyz[i],xyz[0]),sub(xyz[i+1],xyz[0]))
  if dot(n,n):return n
 raise ValueError('zero-area')
def clean(poly):
 p=[]
 for q in poly:
  if q not in p:p.append(q)
 return p

def plane_section(poly,n,o):
 q=[]
 for u,v in zip(poly,poly[1:]+poly[:1]):
  su=dot(n,u)-o;sv=dot(n,v)-o
  if su==0:q.append(u)
  if su*sv<0:
   k=su/(su-sv);q.append(tuple(u[i]+k*(v[i]-u[i]) for i in range(3)))
 return clean(q)
def coplanar_clip(A,B,n):
 drop=max(range(3),key=lambda i:abs(n[i]));keep=[i for i in range(3) if i!=drop];proj=lambda p:(p[keep[0]],p[keep[1]])
 area=sum(cross2(proj(u),proj(v)) for u,v in zip(B,B[1:]+B[:1]))
 if area<0:B=list(reversed(B))
 result=A[:]
 for u,v in zip(B,B[1:]+B[:1]):
  if not result:break
  edge=sub(proj(v),proj(u));distance=lambda p:cross2(edge,sub(proj(p),proj(u)));tmp=[]
  for x,y in zip(result,result[1:]+result[:1]):
   sx=distance(x);sy=distance(y)
   if sx>=0:tmp.append(x)
   if sx*sy<0:
    k=sx/(sx-sy);tmp.append(tuple(x[i]+k*(y[i]-x[i]) for i in range(3)))
  result=clean(tmp)
 return result

def intersection(A,B):
 na=normal(A);nb=normal(B);oa=dot(na,A[0]);ob=dot(nb,B[0]);d=cross(na,nb)
 if dot(d,d)==0:
  if dot(na,B[0])!=oa:return []
  return coplanar_clip(A,B,na)
 sa=plane_section(A,nb,ob);sb=plane_section(B,na,oa)
 if not sa or not sb:return []
 aa=sorted((dot(d,p),p) for p in sa);bb=sorted((dot(d,p),p) for p in sb);lo=max(aa[0][0],bb[0][0]);hi=min(aa[-1][0],bb[-1][0])
 if lo>hi:return []
 if aa[-1][0]==aa[0][0]:return [aa[0][1]]
 x=aa[0][1];y=aa[-1][1];den=aa[-1][0]-aa[0][0]
 return clean([tuple(x[i]+((v-aa[0][0])/den)*(y[i]-x[i]) for i in range(3)) for v in [lo,hi]])
def allowed(q,shared):
 if not shared:return False
 if len(shared)==1:return q==shared[0]
 x,y=shared[0],shared[1];d=sub(y,x);v=sub(q,x)
 if dot(cross(d,v),cross(d,v))!=0:return False
 return 0<=dot(v,d)<=dot(d,d)

def check_faces(fs):
 issues=[];tested=0;intersections=0
 for (fa,ia),(fb,ib) in itertools.combinations(fs,2):
  tested+=1;A=[P[p] for p in ia];B=[P[p] for p in ib]
  if any(max(p[k] for p in A)<min(p[k] for p in B) or max(p[k] for p in B)<min(p[k] for p in A) for k in range(3)):continue
  pts=intersection(A,B)
  if not pts:continue
  intersections+=1;common=set(ia)&set(ib);shared=[P[p] for p in sorted(common)]
  if len(shared)>2 or not all(allowed(q,shared) for q in pts):issues.append(dict(faces=[fa,fb],sharedNodeIDs=sorted(common),intersectionVertices=[[str(x) for x in p] for p in pts]))
 return dict(faceCount=len(fs),testedPairs=tested,nonemptyIntersectionPairs=intersections,unintendedIntersections=issues,embeddedByExactConvexFacePairIntersections=not issues)
allids=sorted(r['classification']['left']+r['classification']['right']+r['classification']['on']);globalfs=[(f,a['faces'][str(f)]['pointIDs']) for f in allids];leftfs=[(f,a['faces'][str(f)]['pointIDs']) for f in r['subshells']['left']['originalOuterFaces']]+[('separator',r['separator']['originalOrderedRingLeft'])];rightfs=[(f,a['faces'][str(f)]['pointIDs']) for f in r['subshells']['right']['originalOuterFaces']]+[('separator',r['separator']['originalOrderedRingRight'])]
convex=[]
for f,ids in globalfs:
 xyz=[P[p] for p in ids];n=normal(xyz);turns=[dot(n,cross(sub(xyz[i],xyz[i-1]),sub(xyz[(i+1)%len(ids)],xyz[i]))) for i in range(len(ids))]
 convex.append(dict(face=f,exactPlanar=all(dot(n,sub(p,xyz[0]))==0 for p in xyz),strictlyConvex=all(x>0 for x in turns)))
assert all(x['exactPlanar'] and x['strictlyConvex'] for x in convex)
g=check_faces(globalfs);l=check_faces(leftfs);rr=check_faces(rightfs)
report=dict(scope='Exact fixed-surface face-pair intersections only; no AP, optimization, candidate construction or native execution.',inputSha256=hashlib.sha256((base/'complete-star-input.json').read_bytes()).hexdigest(),parentStaticReportSha256=hashlib.sha256((out/'separator-topology-geometry-audit.json').read_bytes()).hexdigest(),method='Binary coordinates lifted to Fraction. All original38 faces exact planar and strictly convex. Pairwise convex polygon intersections are computed by exact plane sections or coplanar clipping; every intersection must be limited to shared literal node or shared literal edge. Shared closing quad is tested against every original assigned face.',original38FaceGeometry=convex,globalShell=g,leftSubshell=l,rightSubshell=rr,coverageConclusion='When all three embeddedness checks pass, the closed connected orientable spherical subshells and exact boundary-chain equality certify a geometric separator. Their bounded interiors lie in opposite x halfspaces; they are disjoint off the shared quad, and their union is the original bounded domain by boundary/winding equality. This is independent of any AP.',candidateLimit='Actual cone cells are not present in this certificate. Each new AP must separately satisfy strict visibility of every base face in its lobe. Positive cone heights, conforming side interfaces, exact conservation, passing roles, all native quality/halo checks and final writer binding must still be verified.')
path=out/'fixed-surface-embeddedness-certificate.json';path.open('x').write(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(reportPath=str(path),globalShell=g,leftSubshell=l,rightSubshell=rr),indent=2))
