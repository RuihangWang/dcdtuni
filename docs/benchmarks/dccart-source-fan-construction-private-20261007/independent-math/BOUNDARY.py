from pathlib import Path
import json,signal,time,os
R=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/source-plane-independent-review-20261007')
ns={};exec((R/'REPLAY.py').read_text().split('# Independent Foam quoted-string decoder:')[0],ns)
Q,point,plane,rref,cross,dot=tuple(ns[n] for n in ['Q','point','plane','rref','cross','dot']);F=ns['F'];start=time.monotonic()
records=json.loads((F/'logs/ON.geometry.json').read_text());results=[]
def orient(a,b,c,u,v):return (b[u]-a[u])*(c[v]-a[v])-(b[v]-a[v])*(c[u]-a[u])
def classify(q,r,axis):
 u,v=(axis+1)%3,(axis+2)%3;edges=[];winding=0
 for i,a in enumerate(r):
  b=r[(i+1)%len(r)];o=orient(a,b,q,u,v)
  if o==0 and all(min(a[j],b[j])<=q[j]<=max(a[j],b[j]) for j in (u,v)):
   direction=tuple(b[j]-a[j] for j in range(3));lead=next(x for x in direction if x);direction=tuple(x/lead for x in direction)
   edges.append({'edge':i,'direction':direction,'atVertex':q==a or q==b})
  if a[v]<=q[v]<b[v] and o>0:winding+=1
  if b[v]<=q[v]<a[v] and o<0:winding-=1
 rank=len(rref([e['direction'] for e in edges],3)[1]) if edges else 0
 return {'projectedLocation':'boundary-corner' if rank>=2 else 'boundary-straight' if rank==1 else 'interior' if winding else 'outside','supportingBoundaryEdges':edges,'boundaryDirectionRank':rank}
for record in records:
 rep=next(e for e in record['events'] if e['kind']=='representative');q=point(rep['rawBits']);sources=[e for e in record['events'] if e['kind']=='source'];rows=[];constraints=[];bad=[];corner=False
 for src in sources:
  r=[point(x) for x in src['worldBits']];n,d,axis=plane(r);res=dot(n,q)-d;constraints.append(n);cl=classify(q,r,axis)
  if res or cl['projectedLocation']=='outside':bad.append(src['sourceFacet'])
  if cl['projectedLocation']=='boundary-corner':
   # Conservative source-outline policy: leave original immutable source corner fixed.
   corner=True;constraints.extend([(Q(1),Q(0),Q(0)),(Q(0),Q(1),Q(0)),(Q(0),Q(0),Q(1))])
  elif cl['projectedLocation']=='boundary-straight':
   dvec=cl['supportingBoundaryEdges'][0]['direction'];constraints.extend([cross(dvec,b) for b in [(Q(1),Q(0),Q(0)),(Q(0),Q(1),Q(0)),(Q(0),Q(0),Q(1))]])
  rows.append({'sourceFacet':src['sourceFacet'],'planeResidual':res,**cl})
 rank=len(rref(constraints,3)[1]);plane_rank=len(rref([plane([point(x) for x in src['worldBits']])[0] for src in sources],3)[1])
 results.append({'compactPoint':record['compactPoint'],'nativePoint':rep['native'],'planeOnlyDOFUpperBound':3-plane_rank,'sourceOutlinePolicy':'Original source corners pinned; straight immutable boundary edges permit only edge direction; all per-source identities retained','conservativeBoundaryCandidateDOF':3-rank,'baselineEligibleUnderTheseNecessaryLocationChecks':not bad,'inadmissibleSourceLocations':bad,'anyImmutableSourceCorner':corner,'sources':rows})
a={'scope':'Exact source-outline location review; necessary plane constraints plus conservative fixed-corner policy, not movement admission or full footprint proof','records':results,'seconds':time.monotonic()-start,'cpu':list(os.sched_getaffinity(0))};(R/'INDEPENDENT-source-outline-constraints.json').write_text(json.dumps(ns['enc'](a),indent=2)+'\n');signal.alarm(0)
for row in results:print(row['compactPoint'],row['nativePoint'],'planeDOF',row['planeOnlyDOFUpperBound'],'outlineConservativeDOF',row['conservativeBoundaryCandidateDOF'],'baseline',row['baselineEligibleUnderTheseNecessaryLocationChecks'],[(s['sourceFacet'],s['projectedLocation']) for s in row['sources']])
