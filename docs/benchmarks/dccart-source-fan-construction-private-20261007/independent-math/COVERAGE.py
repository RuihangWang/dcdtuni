from pathlib import Path
import json,signal,time
R=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/source-plane-independent-review-20261007');ns={};exec((R/'REPLAY.py').read_text().split('# Independent Foam quoted-string decoder:')[0],ns)
point,Q,cross,dot,plane=tuple(ns[n] for n in ['point','Q','cross','dot','plane']);F=ns['F'];start=time.monotonic();records=json.loads((F/'logs/ON.geometry.json').read_text());checks=[]
def sixvol(r):return sum((dot(r[0],cross(r[i],r[i+1])) for i in range(1,len(r)-1)),Q(0))
def orient(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def polyarea(r):return sum((r[i][0]*r[(i+1)%len(r)][1]-r[i][1]*r[(i+1)%len(r)][0] for i in range(len(r))),Q(0))
def disjoint(a,b):
 for ring in (a,b):
  for i,x in enumerate(ring):
   y=ring[(i+1)%len(ring)];n=(y[1]-x[1],x[0]-y[0]);pa=[p[0]*n[0]+p[1]*n[1] for p in a];pb=[p[0]*n[0]+p[1]*n[1] for p in b]
   if max(pa)<=min(pb) or max(pb)<=min(pa):return True
 return False
replay=json.loads((R/'INDEPENDENT-exact-replay.json').read_text())
for record in records:
 for e in record['events']:
  if e['kind']!='group':continue
  src=[point(b) for b in e['outlineBits']];axis=e['axis'];u,v=(axis+1)%3,(axis+2)%3;pr=[(p[u],p[v]) for p in src];area=polyarea(pr);sgn=1 if area>0 else -1
  assert all(sgn*orient(pr[i],pr[(i+1)%len(pr)],pr[(i+2)%len(pr)])>=0 for i in range(len(pr)))
  tiles=[[point(b) for b in f['worldBits']] for f in e['facets']];projected=[[(p[u],p[v]) for p in tile] for tile in tiles]
  assert all(sgn*polyarea(tile)>0 for tile in projected)
  inside=all(sgn*orient(pr[i],pr[(i+1)%len(pr)],p)>=0 for tile in projected for p in tile for i in range(len(pr)))
  pairs=[(i,j,disjoint(projected[i],projected[j])) for i in range(len(tiles)) for j in range(i+1,len(tiles))];assert inside and all(p[2] for p in pairs)
  assert sum(map(polyarea,projected),Q(0))==area
  n,d,actual_axis=plane(src);assert actual_axis==axis;coplanar=all(dot(n,p)==d for tile in tiles for p in tile)
  delta=sum((sixvol(tile) for tile in tiles),Q(0))-sixvol(src);moment=next(x for x in replay['completeGroupsReplay'] if x['sourceFacet']==e['sourceFacet']);assert all(Q(moment['sixFirstDelta'][k][k])==delta for k in range(3))
  checks.append({'sourceFacet':e['sourceFacet'],'convexTarget':True,'allSixProjectedTrianglesPositive':True,'allProjectedTriangleVerticesInTarget':inside,'all15PairsProjectedInteriorDisjoint':True,'sumProjectedAreasExact':True,'allVerticesOnOriginal3DPlane':coplanar,'full3DAreaAndM1Equal':moment['wholeVectorAndTensorMomentsEqual'],'independentlyReplayedFullFootprintGeometry':coplanar and moment['wholeVectorAndTensorMomentsEqual'],'closedOrientedSixVolume':delta,'closedOrientedVolume':delta/6,'allThreeDiagonalMomentDeltasEqualSixVolume':True,'pairSATResults':pairs})
stl=F/'cases/ON/Mesh/constant/triSurface/merged_box.stl';raw=stl.read_bytes();assert raw.startswith(b'solid ') and raw.count(b'facet normal')==12
a={'scope':'Independent exact two-group convex projected coverage plus full3D moment/plane checks; no native execution or admission','groups':checks,'literalSTL':{'path':str(stl),'sha256':ns['sha'](stl),'asciiFacetCount':12},'seconds':time.monotonic()-start};(R/'INDEPENDENT-two-group-coverage-volume.json').write_text(json.dumps(ns['enc'](a),indent=2)+'\n');signal.alarm(0);print(json.dumps(ns['enc'](a),indent=2))
