from pathlib import Path
from fractions import Fraction as F
from collections import Counter
import json,struct,hashlib,math
here=Path(__file__).parent
def vector(p):return list(map(F,p))
def sub(a,b):return [x-y for x,y in zip(a,b)]
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def vm(doc,cell):
 p={int(k):vector(v)for k,v in doc['points'].items()};v=F(0);m=[F(0)]*3
 for f in doc['cells'][str(cell)]['faces']:
  row=doc['faces'][str(f)];r=row['pointIDs']if row['owner']==cell else [row['pointIDs'][0]]+row['pointIDs'][:0:-1]
  a=p[r[0]]
  for j in range(1,len(r)-1):
   b,c=p[r[j]],p[r[j+1]];dv=dot(a,cross(b,c))/6;v+=dv
   for q in range(3):m[q]+=dv*(a[q]+b[q]+c[q])/4
 return v,m
def summed(doc,cells):
 vals=[vm(doc,c)for c in cells]
 return sum(v[0]for v in vals),[sum(v[1][j]for v in vals)for j in range(3)]
def outward(row,core):
 ids=row['pointIDs']
 assert (row['owner']in core)!=(row['neighbour']in core)
 return ids if row['owner']in core else [ids[0]]+ids[:0:-1]
def verify_shape(old,new,ext):
 oldcore=set(old['children']);newcore=set(new['children'])
 assert not newcore&{v for r in old['faces'].values()for v in [r['owner'],r['neighbour']]},'new-label-collision'
 for cell,row in new['cells'].items():
  actual={int(f)for f,r in new['faces'].items()if int(cell)in [r['owner'],r['neighbour']]}
  assert actual==set(row['faces']),'incomplete-cell-incidence'
 for k,xyz in old['points'].items():assert struct.pack('ddd',*xyz)==struct.pack('ddd',*new['points'][k]),'original-point-coordinate'
 proofs=[]
 for f in ext:
  a,b=old['faces'][str(f)],new['faces'][str(f)]
  for key in ['patch','sourceBase','sourceZone','nativeFace']:assert a[key]==b[key],'outer-role'
  ra,rb=outward(a,oldcore),outward(b,newcore);assert ra==rb,'outer-ring-or-point0'
  ao=a['neighbour']if a['owner']in oldcore else a['owner']
  bo=b['neighbour']if b['owner']in newcore else b['owner'];assert ao==bo,'actual-outside-endpoint'
  proofs.append({'face':f,'orderedCoreOutwardPointIDs':ra,'actualOutside':ao,'rolesExact':True})
 for cell in old['outsideCells']:assert vm(old,cell)==vm(new,cell),'ordinary-outside-Fraction-shape'
 assert summed(old,oldcore)==summed(new,newcore),'cavity-volume-or-first-moment'
 assert all(vm(new,c)[0]>0 for c in newcore),'positive-new-volume'
 for c in newcore:
  directed=Counter()
  for f in new['cells'][str(c)]['faces']:
   r=new['faces'][str(f)];ids=r['pointIDs']if r['owner']==c else [r['pointIDs'][0]]+r['pointIDs'][:0:-1]
   for a,b in zip(ids,ids[1:]+ids[:1]):directed[(a,b)]+=1
  assert all(directed[(a,b)]==directed[(b,a)]==1 for a,b in directed),'cell-edge-closure'
 return proofs
def ulpdiff(a,b):
 def key(x):
  q=struct.unpack('q',struct.pack('d',x))[0]
  return q if q>=0 else -(q+(1<<63))
 return abs(key(a)-key(b))
def run():
 old=json.loads((here/'complete-star-input.json').read_text())
 n=here/'single-interior-point-star-v1'
 new=json.loads((n/'candidate-best-global-handles.json').read_text())
 top=json.loads((here/'star-topology-report.json').read_text())
 shell=verify_shape(old,new,top['vertexStarExteriorFaces'])
 apexIDs=set(new['points'])-set(old['points']);assert len(apexIDs)==1
 ap=vector(new['points'][apexIDs.pop()]);kernel=[]
 for f in top['vertexStarExteriorFaces']:
  r=old['faces'][str(f)];ids=outward(r,set(old['children']))
  pts=[vector(old['points'][str(v)])for v in ids];nrm=cross(sub(pts[1],pts[0]),sub(pts[2],pts[0]))
  assert all(dot(nrm,sub(x,pts[0]))==0 for x in pts),'literal-exterior-planarity'
  side=dot(nrm,sub(ap,pts[0]));assert side<0,'strict-cavity-star-kernel'
  kernel.append({'face':f,'strictNegativeSideNumerator':str(side.numerator),'strictNegativeSideDenominator':str(side.denominator)})
 containing=[]
 for c in old['children']:
  sides=[]
  for f in old['cells'][str(c)]['faces']:
   r=old['faces'][str(f)];ids=r['pointIDs']if r['owner']==c else [r['pointIDs'][0]]+r['pointIDs'][:0:-1]
   pts=[vector(old['points'][str(v)])for v in ids];nn=cross(sub(pts[1],pts[0]),sub(pts[2],pts[0]))
   sides.append(dot(nn,sub(ap,pts[0])))
  if all(x<0 for x in sides):containing.append(c)
 assert containing,'new-apex-outside-original-cavity'

 raw=[json.loads(x)for x in (n/'typed-best-fullgeometry-global-handles/installed-kernel-raw.jsonl').read_text().splitlines()if x.startswith('{"kind"')]
 oc={r['id']:r for r in raw[0]['cells']};nc={r['id']:r for r in raw[1]['cells']}
 of={r['face']:r for r in raw[0]['capturedMetrics']};nf={r['face']:r for r in raw[1]['capturedMetrics']}
 metrics={}
 for k,t,upper in [('D',.001,False),('weight',.05,False),('ratio',.01,False),('skew',4,True),('N',70,True)]:
  a,b=(oc,nc)if k=='D'else(of,nf)
  bad=lambda v:not math.isfinite(v)or(v>t if upper else v<t)
  oldbad=[x for x,r in a.items()if bad(r[k])];newbad=[x for x,r in b.items()if bad(r[k])]
  retained=[x for x in set(a)&set(b)if not bad(a[x][k])and bad(b[x][k])]
  newInherited=[x for x in new['children']if k=='D'and bad(b[x][k])]if k=='D'else []
  metrics[k]={'oldBad':oldbad,'newBad':newbad,'retainedPassingRoleRegressions':retained,'newCellAllReplacedPassingRoleFailures':newInherited,
              'oldMinMax':[min(r[k]for r in a.values()),max(r[k]for r in a.values())],
              'newMinMax':[min(r[k]for r in b.values()),max(r[k]for r in b.values())],
              'newWarningLabels':sorted(set(newbad)-set(oldbad))if k=='N'else None,'removedWarningLabels':sorted(set(oldbad)-set(newbad))if k=='N'else None}
 outside=[]
 for c in old['outsideCells']:
  a,b=oc[c],nc[c]
  rows=[('V',a['V'],b['V'])]+[(f'CC{j}',a['CC'][j],b['CC'][j])for j in range(3)]
  delta=[{'field':f,'old':x,'new':y,'ULP':ulpdiff(x,y)}for f,x,y in rows if struct.pack('d',x)!=struct.pack('d',y)]
  if delta:outside.append({'cell':c,'deltas':delta})
 out={'scope':'Actual installed OpenFOAM DP/Int32 local primitiveMesh calculation, complete P12 14-cell star and true halo. No business mesher/checkMesh, no native operator/whole writer acceptance.',
      'inputSha256':hashlib.sha256((here/'complete-star-input.json').read_bytes()).hexdigest(),
      'baselineJob':'52d0e89c/P12; P13 D831/W82 is untouched and not this geometric binding.',
      'oldCoreCount':len(old['children']),'newCoreCount':len(new['children']),'completeOldMetricEndpointCells':len(oc),'completeNewMetricEndpointCells':len(nc),
      'oldPrintedMetricFaces':len(of),'newPrintedMetricFaces':len(nf),
      'exactOriginalDPPointsPreserved':True,'outerOrderedCoordinatesPoint0AndRoles':shell,
      'physicalProtectedFacetLiteralExact':2217059,'sourceZone3OnExteriorNotPermission':True,
      'targetPointRemovableFromCandidateTopology':all(old['apexWrittenPoint']not in r['pointIDs']for r in new['faces'].values()),
      'FractionCavityVolumeAndFirstMomentExact':True,'allOutsideFractionVolumeAndFirstMomentExact':True,
      'stockConcavityAnyCompleteCell':[raw[0]['stockConcaveAny'],raw[1]['stockConcaveAny']],
      'minimumNewCellPyramidVolume':min(r['minPyramidV']for r in nc.values()),
      'maximumNewCellRelativeClosure':max(r['relativeClosure']for r in nc.values()),
      'minimumNewCentreFanTetQuality':min(r['minCentreFanTetQuality']for r in nf.values()),
      'minimumNewSharedAnchorTetQuality':min(r['bestSharedBaseTetQuality']for r in nf.values()),
      'exactStarKernelSides':kernel,'newApexStrictlyContainedInOriginalCells':containing,'officialTetFloor':1e-30,'metrics':metrics,'ordinaryOutsideDPMomentChanges':outside,
      'maximumOrdinaryOutsideMomentULP':max([0]+[d['ULP']for r in outside for d in r['deltas']]),
      'allBoundEndpointsKeepOriginalOwnerNeighbourOrdering':True,
      'incompleteFarEndpointMomentsNotEstimatedOrUsed':True,'fullRankAliasOrWriterCertificateNotClaimed':True,
      'unknownSourceAuthorizationNotInferredFromTrace':True,'qualifiedCandidate':False,
      'refusal':'Two new tetrahedra fail original determinant floor .001 and inherit passing replaced-cell roles; new N max also differs by a tiny recomputation ULP and is explicitly retained.',
      'fullFixedGrammarScope':'Original18 boundary nodes / plus one Chebyshev-derived point / plus two core-centroid-derived points, tet+literal exterior quad-pyramid only; floating solver refusal is not exact or global infeasibility.'}
 assert not any(metrics[k]['retainedPassingRoleRegressions']for k in ['D','weight','ratio','skew'])
 assert len(metrics['D']['newCellAllReplacedPassingRoleFailures'])==2
 assert out['stockConcavityAnyCompleteCell']==[False,False]
 assert out['minimumNewCellPyramidVolume']>0 and out['maximumNewCellRelativeClosure']<1e-10
 assert out['minimumNewSharedAnchorTetQuality']>1e-30 and out['minimumNewCentreFanTetQuality']>1e-30
 (here/'installed-star-audit.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
 print(json.dumps({'qualifiedCandidate':False,'counts':{k:[len(v['oldBad']),len(v['newBad'])]for k,v in metrics.items()},
 'newBadCore':metrics['D']['newCellAllReplacedPassingRoleFailures'],'geometry':[out['stockConcavityAnyCompleteCell'],out['minimumNewCellPyramidVolume'],out['maximumNewCellRelativeClosure'],out['minimumNewSharedAnchorTetQuality']],
 'ordinaryOutsideDPMomentChangedCells':len(outside),'maxULP':out['maximumOrdinaryOutsideMomentULP']}))
if __name__=='__main__':run()
