from pathlib import Path
import json,struct,math,hashlib,collections
from fractions import Fraction as F
T=Path(__file__).resolve().parent; R=T/'report'
records=json.loads((T/'logs/ON.geometry.json').read_text())
def dp(h):return struct.unpack('>d',bytes.fromhex(h))[0]
def p(b):return tuple(F.from_float(dp(x)) for x in b)
def out(x):return str(x) if isinstance(x,F) else x
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def cross(a,b):return(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def normal(ring):
 for i in range(1,len(ring)-1):
  n=cross(sub(ring[i],ring[0]),sub(ring[i+1],ring[0]))
  if any(n):return n
 return (F(0),)*3
def rank(rows):
 a=[list(x) for x in rows if any(x)];j=0
 for k in range(3):
  pivot=next((z for z in range(j,len(a)) if a[z][k]),None)
  if pivot is None:continue
  a[j],a[pivot]=a[pivot],a[j];q=a[j][k];a[j]=[x/q for x in a[j]]
  for z in range(len(a)):
   if z!=j:
    q=a[z][k];a[z]=[x-q*y for x,y in zip(a[z],a[j])]
  j+=1
 return j
def planar(ring):
 n=normal(ring);return any(n) and all(dot(n,sub(x,ring[0]))==0 for x in ring)
def axes(ring):return [k for k in range(3) if all(x[k]==ring[0][k] for x in ring)]
def projectedMoment(ring,ax):
 a=(ax+1)%3;b=(ax+2)%3;A=F(0);M=[F(0)]*2
 for i,x in enumerate(ring):
  y=ring[(i+1)%len(ring)];c=x[a]*y[b]-x[b]*y[a];A+=c
  M[0]+=(x[a]+y[a])*c;M[1]+=(x[b]+y[b])*c
 return dict(signedArea2=str(A),firstMoment6UV=[str(x) for x in M])
def turn(ring,ax):
 a=(ax+1)%3;b=(ax+2)%3;r=[]
 for i,x in enumerate(ring):
  y=ring[(i+1)%len(ring)];z=ring[(i+2)%len(ring)]
  q=(y[a]-x[a])*(z[b]-y[b])-(y[b]-x[b])*(z[a]-y[a])
  r.append(0 if not q else (1 if q>0 else -1))
 return r
summaries=[]; allsources={}; coords={};bindingissues=[]
for rec in records:
 assert rec['observationComplete'] is True
 ev=rec['events'];rep=next(e for e in ev if e['kind']=='representative')
 card=next(e for e in ev if e['kind']=='cardinality')
 src=[e for e in ev if e['kind']=='source'];incident=[e for e in ev if e['kind']=='incident']
 binds=[e for e in ev if e['kind']=='source-binding']
 assert len(src)==card['baseCount'] and {e['sourceFacet'] for e in src}==set(card['bases'])
 assert rep['native']==rep['alias'] and rep['compact']==rec['compactPoint'] and rep['memberCount']==1
 current=p(rep['rawBits']); planes=[];sourceRows=[]
 for s in src:
  ring=list(map(p,s['worldBits']));n=normal(ring);ax=axes(ring)
  detail=dict(sourceFacet=s['sourceFacet'],nativeOwner=s['nativeOwner'],patch=s['patch'],zone=s['zone'],originParents=[x['parent'] for x in s['origins']],origins=s['origins'],sourceRing=s['sourceRing'],sourceOutlineArity=len(ring),sourceWorldBits=s['worldBits'],exactPlanar=planar(ring),exactNormal=[str(x) for x in n],constantAxes=ax,selectedPointOnExactPlane=dot(n,sub(current,ring[0]))==0,axisTurnSigns=turn(ring,ax[0]) if len(ax)==1 else None)
  if len(ax)==1:detail['projectedMoments']=projectedMoment(ring,ax[0])
  sourceRows.append(detail);planes.append(n)
  if s['sourceFacet'] in allsources: assert allsources[s['sourceFacet']]==detail or allsources[s['sourceFacet']]['sourceWorldBits']==detail['sourceWorldBits']
  else:allsources[s['sourceFacet']]=detail
 for b in binds:
  assert b['selectedPointCoveredByExistingBind'] is True
  face=next(e for e in incident if e['nativeFace']==b['nativeFace'])
  selected=[x for x in face['ring'] if x['alias']==rep['native']]
  assert selected and all(x['bits']==rep['rawBits'] for x in selected)
 for face in incident:
  for x in face['ring']:
   key=x['native']
   if key in coords:assert coords[key]==x['bits']
   else:coords[key]=x['bits']
 groups=[e for e in ev if e['kind']=='group'];groupsInfo=[]
 for g in groups:
  ring=list(map(p,g['outlineBits']));ax=g['axis'];outlineMoment=projectedMoment(ring,ax)
  facetInfo=[];off=[];allA=F(0);allM=[F(0)]*2
  for f in g['facets']:
   fr=list(map(p,f['worldBits']));fm=projectedMoment(fr,ax);allA+=F(fm['signedArea2'])
   allM=[x+F(y) for x,y in zip(allM,fm['firstMoment6UV'])]
   for j,x in enumerate(fr):
    if x[ax]!=ring[0][ax]:
     diff=x[ax]-ring[0][ax];bits=f['worldBits'][j][ax];target=g['outlineBits'][0][ax]
     off.append(dict(nativeFace=f['nativeFace'],vertexOrdinal=j,axis=ax,pointBits=f['worldBits'][j],expectedAxisBits=target,deltaExact=str(diff),deltaFloat=float(diff),ulpOffset=int(bits,16)-int(target,16)))
   facetInfo.append(dict(nativeFace=f['nativeFace'],rawOwner=f['rawOwner'],rawNeighbour=f['rawNeighbour'],patch=f['patch'],zone=f['zone'],alive=f['alive'],exactPlanar=planar(fr),allVerticesInSourceAxisPlane=all(x[ax]==ring[0][ax] for x in fr),projectedMoments=fm))
  groupsInfo.append(dict(sourceFacet=g['sourceFacet'],axis=ax,sourceOutlineArity=len(ring),sourceOutlineExactPlanar=planar(ring),sourceOutlineTurnSigns=turn(ring,ax),currentFacetCount=len(g['facets']),facetInfo=facetInfo,axisPlaneViolations=off,projectedOutline=outlineMoment,projectedTileSum=dict(signedArea2=str(allA),firstMoment6UV=[str(x) for x in allM]),projectedAreaAndM1Equal=allA==F(outlineMoment['signedArea2']) and allM==list(map(F,outlineMoment['firstMoment6UV'])),projectedEqualityIsNot3DSurfaceCoverageProof=bool(off)))
 first=[e for e in ev if e['kind']=='footprint-failure'];tail=[e for e in ev if e['kind']=='coverage-tail']
 summaries.append(dict(compactPoint=rec['compactPoint'],representative=rep,refusal=rec['existingRefusal'],baseCount=card['baseCount'],bases=card['bases'],coreCount=len(card['core']),core=card['core'],incidentFaceCount=len(incident),physicalIncidentFaceCount=sum(not e['internal'] for e in incident),allExistingSuccessfulSourceBindingsPointCovered=True,sourcePlanesRank=rank(planes),allSourceAxesPlanesContainSelectedPoint=all(s['selectedPointOnExactPlane'] for s in sourceRows),sources=sourceRows,footprintGroups=groupsInfo,firstExecutedFootprintFailure=first[0] if first else None,coverageTail=tail[0]['result'] if tail else 'not-reached-shape-refusal',recordComplete=True,observedCoordinates=rec['coordinateCount']))
result=dict(scope='PRIVATE source/geometry observation only; no candidate, no motion, no strict check rerun',baseCardinalityHistogram=dict(sorted(collections.Counter(x['baseCount'] for x in summaries).items())),sourcePlaneRankHistogram=dict(sorted(collections.Counter(x['sourcePlanesRank'] for x in summaries).items())),recordCount=len(summaries),allRecordsComplete=True,distinctNativeSourceFacetCount=len(allsources),distinctObservedNativePoints=len(coords),binding='Original successful S.bind DP/ordered-ring proof covers selected native representative; observer reads raw bits only after those calls; compact indices are EARLIEST not final writer indices',nativeSourceFacetNotSTLTriangle=True,summaries=summaries)
(R/'exact-source-geometry.json').write_text(json.dumps(result,indent=2)+'\n')
(R/'observed-native-point-bits.json').write_text(json.dumps(coords,indent=2)+'\n')
(R/'distinct-source-outlines.json').write_text(json.dumps(allsources,indent=2)+'\n')
print('histogram',result['baseCardinalityHistogram'],'ranks',result['sourcePlaneRankHistogram'],'sources',len(allsources),'points',len(coords))
for x in summaries:
 print(x['compactPoint'],'native',x['representative']['native'],'bases',x['baseCount'],'rank',x['sourcePlanesRank'],'allSourcePointPlanes',x['allSourceAxesPlanesContainSelectedPoint'],'first',x['firstExecutedFootprintFailure'],'tail',x['coverageTail'])
 for g in x['footprintGroups']:print(' group',g['sourceFacet'],'facets',g['currentFacetCount'],'axisOff',len(g['axisPlaneViolations']),'projectedAreaM1exact',g['projectedAreaAndM1Equal'],'off',g['axisPlaneViolations'][:1])
