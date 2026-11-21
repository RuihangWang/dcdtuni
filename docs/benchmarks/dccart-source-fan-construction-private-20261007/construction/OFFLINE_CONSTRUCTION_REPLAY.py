from pathlib import Path
import json,math,struct,copy,time
from fractions import Fraction as F
T=Path(__file__).resolve().parent
records=json.loads((T/'input/ON.geometry.json').read_text())
replay=json.loads((T/'input/actual-source-centre-replay.json').read_text())
def dp(h):return struct.unpack('>d',bytes.fromhex(h))[0]
def hx(x):return struct.pack('>d',x).hex()
def restoreProperty(poly,fc,av):
 before=list(fc);areaSqr=(av[0]*av[0]+av[1]*av[1])+av[2]*av[2]
 finite=len(poly)>=3 and math.isfinite(areaSqr) and areaSqr>0 and all(math.isfinite(x) for x in fc) and all(math.isfinite(x) for p in poly for x in p)
 axes=[]
 if finite:
  axes=[k for k in range(3) if all(hx(p[k])==hx(poly[0][k]) for p in poly)]
  if len(axes)==1:fc=list(fc);fc[axes[0]]=poly[0][axes[0]]
 return fc,axes if finite else []
cases=[]
def case(name,p,fc,av,expectedAxis=None,expectedBits=None):
 out,axes=restoreProperty(p,list(fc),av)
 if expectedAxis is None: assert list(map(hx,out))==list(map(hx,fc)),name
 else:
  assert axes==[expectedAxis] and hx(out[expectedAxis])==hx(p[0][expectedAxis]),name
  assert all(hx(out[k])==hx(fc[k]) for k in range(3) if k!=expectedAxis),name
 if expectedBits is not None:assert list(map(hx,out))==expectedBits,name
 cases.append(dict(name=name,pool='offline binary64 property model, not actual C++/normal authority execution',sourceVertices=[[hx(x) for x in q] for q in p],beforeCentreBits=[hx(x) for x in fc],sourceAreaVectorBits=[hx(x) for x in av],afterFanCentreBits=[hx(x) for x in out],fixedAxes=axes,expectedAxis=expectedAxis,pass_=True))
 return out
for sid in ('3579','3526'):
 r=replay[sid];p=[tuple(map(dp,x)) for x in r['source']['worldBits']];fc=list(map(dp,r['targetObservedBits']));av=list(map(dp,r['actualExpressionReplay']['areaVectorBits']));axis=0 if sid=='3579' else 2
 case('actual-source-'+sid,p,fc,av,axis)
actual=replay['3579'];p0=[tuple(map(dp,x)) for x in actual['source']['worldBits']];fc0=list(map(dp,actual['targetObservedBits']));av0=list(map(dp,actual['actualExpressionReplay']['areaVectorBits']))
p=copy.deepcopy(p0);p[1]=tuple(math.nextafter(x,math.inf) if k==0 else x for k,x in enumerate(p[1]));case('one-source-vertex-shifted-nonconstant',p,fc0,av0)
p=[tuple(x+0.2*y if k==0 else x for k,x in enumerate(q)) for q in p0 for y in [q[1]]];case('tilted-no-axis-support',p,fc0,av0)
case('finite-non-axis-triangle',[(0.,0.,0.),(1.,0.,1.),(0.,1.,1.)],[.3,.3,.6],[1.,1.,-1.])
p=copy.deepcopy(p0);p[0]=(float('nan'),p[0][1],p[0][2]);case('NaN-original-vertex',p,fc0,av0)
p=copy.deepcopy(p0);p[0]=(p[0][0],float('inf'),p[0][2]);case('Inf-original-vertex',p,fc0,av0)
case('nonfinite-original-centre',p0,[fc0[0],float('nan'),fc0[2]],av0)
case('nonfinite-area',p0,fc0,[float('inf'),0.,0.])
case('overflow-area-square',p0,fc0,[1e308,0.,0.])
case('degenerate-line',[(0.,0.,0.),(0.,1.,1.),(0.,2.,2.)],[.1,1.,1.],[0.,0.,0.])
case('degenerate-repeated-point',[(0.,0.,0.)]*3,[.1,.1,.1],[0.,0.,0.])
case('tiny-area-square-underflow',p0,fc0,[1e-200,0.,0.])
pz=[(0.,0.,0.),(-0.,1.,0.),(0.,1.,1.),(0.,0.,1.)];case('mixed-signed-zero-support-not-bit-literal',pz,[0.1,.5,.5],[1.,0.,0.])
case('uniform-positive-zero-support',[(0.,0.,0.),(0.,1.,0.),(0.,1.,1.),(0.,0.,1.)],[-0.,.5,.5],[1.,0.,0.],0)
case('uniform-negative-zero-support',[(-0.,0.,0.),(-0.,1.,0.),(-0.,1.,1.),(-0.,0.,1.)],[0.,.5,.5],[1.,0.,0.],0)
case('fewer-than-three-source-vertices',[(0.,0.,0.),(0.,1.,1.)],[.1,.5,.5],[1.,0.,0.])
def point(b):return tuple(F.from_float(dp(x)) for x in b)
def subtract(a,b):return tuple(x-y for x,y in zip(a,b))
def cross(a,b):return(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def moments(rings):
 area=[F(0)]*3;first=[[F(0)]*3 for _ in range(3)]
 for ring in rings:
  for i in range(1,len(ring)-1):
   tri=(ring[0],ring[i],ring[i+1]);n=cross(subtract(tri[1],tri[0]),subtract(tri[2],tri[0]))
   area=[a+b for a,b in zip(area,n)]
   for a in range(3):
    for b in range(3):first[a][b]+=n[a]*sum(x[b] for x in tri)
 return area,first
rec=next(r for r in records if r['compactPoint']==1609)
g=next(e for e in rec['events'] if e['kind']=='group' and e['sourceFacet']==3579)
outline=list(map(point,g['outlineBits']));oldrings=[list(map(point,f['worldBits'])) for f in g['facets']]
oldcentre=point(replay['3579']['targetObservedBits']);sourceplane=outline[0][0]
afterrings=[[(sourceplane,x[1],x[2]) if x==oldcentre else x for x in ring] for ring in oldrings]
targetA,targetM=moments([outline]);oldA,oldM=moments(oldrings);newA,newM=moments(afterrings)
assert oldA==targetA and newA==targetA
assert oldM!=targetM and newM==targetM
tensor=dict(scope='offline construction coordinate only; actual complete6 current triangles bound by source3579 group record; no current/raw case changed',area2OldTargetEqual=True,area2CandidateTargetEqual=True,sixFirstOldTargetEqual=False,sixFirstCandidateTargetEqual=True,oldMinusSourceSixFirst=[[str(a-b) for a,b in zip(x,y)] for x,y in zip(oldM,targetM)],candidateMinusSourceSixFirst=[[str(a-b) for a,b in zip(x,y)] for x,y in zip(newM,targetM)],tangentialCentroidBitsUnchanged=True,sourceOriginalOutlineBits=g['outlineBits'])
result=dict(caseCount=len(cases),passed=sum(x['pass_'] for x in cases),cases=cases,completeSource3579MomentReplay=tensor,actualCPPCompileOrNativeExecution=False,source3526Scope='Only2 actual incident facets observed; do not claim full current source3526 coverage/M1',constructionNotSourceAuthority=True,allSourceCoordinatesRolesHelperAndFcOriginalPreserved=True)
(T/'report/offline-dp-properties.json').write_text(json.dumps(result,indent=2)+'\n')
print('offline',len(cases),'pass',result['passed']);print(json.dumps(tensor,indent=2))
