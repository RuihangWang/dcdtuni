from pathlib import Path
import os,time,json,hashlib,math,collections,resource
P=Path(__file__).resolve().parent;R=P/'actual-export-audit';p=R/'merged_liquid_transport.stl';os.sched_setaffinity(0,{34,35});start=time.monotonic()
points=[];pointmap={};edges=collections.defaultdict(lambda:[0,0]);facets=0;regions=collections.Counter();volume=0.;badarea=[];badnormal=[];tri=[];normal=None;solid=None
for line in p.open():
 words=line.split()
 if not words:continue
 if words[0]=='solid':assert solid is None;solid=words[1]
 elif words[0]=='endsolid':assert words[1]==solid;solid=None
 elif words[:2]==['facet','normal']:normal=tuple(map(float,words[2:]));tri=[]
 elif words[0]=='vertex':
  v=tuple(map(float,words[1:]));assert all(math.isfinite(x) for x in v);idx=pointmap.get(v)
  if idx is None:idx=len(points);points.append(v);pointmap[v]=idx
  tri.append(idx)
 elif words[0]=='endfacet':
  assert len(tri)==3 and solid;facets+=1;regions[solid]+=1;a,b,c=[points[i] for i in tri];ab=[b[i]-a[i] for i in range(3)];ac=[c[i]-a[i] for i in range(3)];cross=[ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]];area=math.sqrt(sum(x*x for x in cross))/2
  if not math.isfinite(area) or area<=1e-20:badarea.append({'facet':facets-1,'solid':solid,'area':area,'points':[a,b,c]})
  if normal is None or not all(math.isfinite(x) for x in normal) or sum(n*q for n,q in zip(normal,cross))<=0:badnormal.append({'facet':facets-1,'solid':solid,'normal':normal})
  volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
  for x,y in zip(tri,tri[1:]+tri[:1]):edges[min(x,y),max(x,y)][0 if x<y else 1]+=1
assert solid is None
badedges=[{'ids':[a,b],'counts':counts,'points':[points[a],points[b]]} for (a,b),counts in edges.items() if counts!=[1,1]]
obj={'sourcePath':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'facetCount':facets,'regionFacetCounts':regions,'pointCount':len(points),'edgeCount':len(edges),'badDirectedEdgeCount':len(badedges),'badAreaCount':len(badarea),'badNormalCount':len(badnormal),'badDirectedEdgesFirst16':badedges[:16],'badAreasFirst16':badarea[:16],'badNormalsFirst16':badnormal[:16],'volumeM3':volume,'bounds':[[min(v[i] for v in points),max(v[i] for v in points)] for i in range(3)],'exactLiteralVertexIdentity':True,'epsilonChanged':False,'nativeMeshExecuted':False,'elapsedSeconds':time.monotonic()-start,'peakRssKiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
obj['accepted']=len(regions)==2 and not(badedges or badarea or badnormal) and math.isfinite(volume) and volume>1e-12
(R/'INDEPENDENT-LITERAL-STL-AUDIT.json').write_text(json.dumps(obj,indent=2)+'\n');print(json.dumps(obj));raise SystemExit(0 if obj['accepted'] else 1)
