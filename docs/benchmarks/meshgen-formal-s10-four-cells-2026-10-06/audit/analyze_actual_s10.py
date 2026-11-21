from pathlib import Path
from fractions import Fraction as F
from collections import defaultdict,Counter
import json,re,hashlib
O=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ident(p):return {"path":str(p),"bytes":p.stat().st_size,"sha256":sha(p)}
def out(n,d):
 p=O/n;assert not p.exists()
 p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n")
def parsev(s):return tuple(F(x) for x in s.split(","))
def add(a,b):return tuple(x+y for x,y in zip(a,b))
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def scale(a,k):return tuple(x*k for x in a)
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def area(points):
 s=(F(0),)*3
 for a,b in zip(points,points[1:]+points[:1]):s=add(s,cross(a,b))
 return scale(s,F(1,2))
def j(v):
 if isinstance(v,F):return {"fraction":str(v),"value":float(v)}
 if isinstance(v,dict):return {str(k):j(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [j(x) for x in v]
 return v
p=O/"root-browser/fullLog.txt";lines=p.read_text().splitlines()
head=re.compile(r"\[DC-S10K\] ci=(\d+) SigmaSf=(\S+) nfaces=(\d+) level=(\d+) cen=\(([^)]+)\) splitPiece=(\d+)")
face=re.compile(r"\s+f(\d+) nbr=(-?\d+) patch=(-?\d+) npts=(\d+) Sf=\(([^)]+)\) home=\(([^)]+)\):(.*)")
point=re.compile(r"(\d+)\(([^)]+)\)")
cells={};points={}
for i,line in enumerate(lines):
 m=head.search(line)
 if not m:continue
 ci=int(m[1]);n=int(m[3]);faces=[]
 for k in range(n):
  f=face.search(lines[i+1+k]);assert f,(ci,k)
  ids=[];coords=[]
  for a,b in point.findall(f[7]):
   label=int(a);v=parsev(b)
   if label in points:assert points[label]==v
   points[label]=v;ids.append(label);coords.append(v)
  assert len(ids)==int(f[4])
  av=area(coords);assert av==parsev(f[5]),(ci,k,av,f[5])
  faces.append({"index":int(f[1]),"neighbour":int(f[2]),"patch":int(f[3]),
   "ids":ids,"coordinates":coords,"printedSf":parsev(f[5]),"exactAreaVector":av,"line":i+2+k,"raw":lines[i+1+k]})
 cells[ci]={"ci":ci,"printedSigmaSf":F(m[2]),"level":int(m[4]),"printedCen":parsev(m[5]),"splitPiece":int(m[6]),
  "faces":faces,"headLine":i+1,"headRaw":line}
assert sorted(cells)==[457,463,469,475]
# Exact ASCII input STL triangle names/orientations; no product imports.
stls=[];solidTris=defaultdict(list)
for q in sorted((O/"case/Mesh/constant/triSurface").glob("*.stl")):
 text=q.read_text();current=None;verts=[]
 for line in text.splitlines():
  if line.strip().startswith("solid "):current=line.strip()[6:];verts=[]
  elif line.strip().startswith("vertex "):verts.append(tuple(F(x) for x in line.split()[1:4]))
  elif line.strip().startswith("endfacet"):
   assert current and len(verts)==3
   solidTris[current].append(verts);verts=[]
 stls.append(ident(q))
bottom=solidTris["Thermal_block_Bottom"];assert len(bottom)==2
bottomSf=tuple(sum(area(tris)[axis] for tris in bottom) for axis in range(3))
assert bottomSf==(F(0),-F(1,64),F(0))
ingest=json.loads((O/"case/Mesh/.meshgen_scratch/dc_report.json").read_text())
assert ingest["patchNames"][17]=="Thermal_block_Bottom"
# Match each observed patch17 polygon to actual bottom surface bounds; clip-free rectangular footprint.
bbox=[tuple(min(p[a] for tri in bottom for p in tri) for a in range(3)),
 tuple(max(p[a] for tri in bottom for p in tri) for a in range(3))]
assert bbox==[(F(1,4),F(1,2),F(1,4)),(F(3,8),F(1,2),F(3,8))]
def edgeResidual(faces):
 directed=Counter();pool=list(set(points.values()))
 for f in faces:
  poly=f["coordinates"]
  for a,b in zip(poly,poly[1:]+poly[:1]):
   delta=sub(b,a);den=dot(delta,delta);assert den>0
   cuts=[]
   for q in pool:
    off=sub(q,a)
    if cross(off,delta)==(0,0,0):
     t=dot(off,delta)/den
     if 0<=t<=1:cuts.append((t,q))
   cuts=sorted(set(cuts))
   for (_,aa),(_,bb) in zip(cuts,cuts[1:]):directed[(aa,bb)]+=1
 residual={};visited=set()
 for (a,b),cnt in directed.items():
  key=tuple(sorted((a,b)))
  if key in visited:continue
  visited.add(key);value=directed[(key[0],key[1])]-directed[(key[1],key[0])]
  if value:residual[str(key)]=value
 return residual
def moments(faces,ref):
 V=F(0);M=(F(0),)*3
 for f in faces:
  poly=f["coordinates"];p0=poly[0]
  for k in range(1,len(poly)-1):
   p1,p2=poly[k],poly[k+1]
   v=dot(sub(p0,ref),cross(sub(p1,ref),sub(p2,ref)))/6
   centroid=scale(add(add(ref,p0),add(p1,p2)),F(1,4))
   V+=v;M=add(M,scale(centroid,v))
 return {"V":V,"M1":M,"centroid":scale(M,1/V) if V else None}
results=[]
for ci,c in sorted(cells.items()):
 faces=c["faces"];cap=[f for f in faces if f["patch"]==17]
 assert len(cap)==1 and cap[0]["index"]==10
 cap=cap[0];assert area(cap["coordinates"])==(0,F(1,256),0)
 assert all(v[1]==F(1,2) and bbox[0][0]<=v[0]<=bbox[1][0] and bbox[0][2]<=v[2]<=bbox[1][2] for v in cap["coordinates"])
 residual=tuple(sum(f["exactAreaVector"][a] for f in faces) for a in range(3))
 assert residual==(0,F(1,256),0)
 rest=[f for f in faces if f["index"]!=10]
 without=tuple(sum(f["exactAreaVector"][a] for f in rest) for a in range(3))
 assert without==(0,0,0)
 rev=tuple(residual[a]-2*cap["exactAreaVector"][a] for a in range(3));assert rev==(0,-F(1,256),0)
 allp=[p for f in faces for p in f["coordinates"]]
 lo=tuple(min(p[a] for p in allp) for a in range(3));hi=tuple(max(p[a] for p in allp) for a in range(3))
 ref=scale(add(lo,hi),F(1,2))
 er=edgeResidual(faces);wr=edgeResidual(rest);assert len(er)==4 and not wr,(ci,er,wr)
 v0=moments(faces,ref);v1=moments(rest,ref);vOrigin=moments(faces,(F(0),)*3)
 v1Origin=moments(rest,(F(0),)*3)
 assert v1==v1Origin and v1["V"]>0
 assert v0["V"]==F(13,32768) and v1["V"]==F(15,32768)
 assert max(abs(float(a-b)) for a,b in zip(v0["centroid"],c["printedCen"]))<5e-10
 bottomFluid=[f for f in rest if f["patch"]==-1 and all(v[1]==F(1,2) for v in f["coordinates"])]
 topFluid=[f for f in rest if f["patch"]==-1 and all(v[1]==hi[1] for v in f["coordinates"])]
 assert sum(-f["exactAreaVector"][1] for f in bottomFluid)==F(5,1024)
 assert sum(f["exactAreaVector"][1] for f in topFluid)==F(5,1024)
 results.append({"ci":ci,"patch17SurfaceName":"Thermal_block_Bottom","nativeCompactIdOnly":True,
  "fullFaces":13,"capIndex":10,"capIDs":cap["ids"],"capCoords":cap["coordinates"],"capAreaVector":cap["exactAreaVector"],
  "allFaceSum":residual,"withoutOnlyCapFaceSum":without,"flipOnlyCapFaceSum":rev,
  "bottomComplementArea":F(5,1024),"topComplementArea":F(5,1024),
  "originalDirectedUncancelledSegments":er,"withoutCapDirectedUncancelledSegments":wr,
  "bbox":[lo,hi],"referenceBBoxCentre":ref,"originalMomentsAtBBoxReference":v0,
  "originalMomentsAtZeroReference":vOrigin,"withoutCapMoments":v1,
  "originalOpenVIsReferenceDependent":v0["V"]!=vOrigin["V"],
  "printedNativeOriginalVolumeMatchesReferenceCalculation":True,
  "noCapClosedReferenceInvariantVAndM1":True,
  "scope":"Diagnostic subtraction only; case/source/topology untouched. This does not establish correct counterpart ownership/global volume conservation or native acceptance."})
out("actual-cell-faces-points.json",j({"log":ident(p),"cells":cells,"pointMap":points,"coordinateScope":"Actual %.9g native diagnostic coordinates; dyadic finite values in these tables reconstruct exactly as printed. No exported binary polyMesh exists, so printed coordinates are not a binary payload identity proof."}))
out("fraction-closure-and-cap-ownership.json",j({"results":results,"STLFiles":stls,"actualThermalBottomTriangles":bottom,
 "STLBottomAreaVector":bottomSf,"STLBottomBBox":bbox,"actualNativePatch17Name":ingest["patchNames"][17],
 "mappingScope":"Actual ingest patchNames[17] bound here; downstream surf.patchNames construction checked separately in source review.",
 "strongConclusion":"Each observed cell contains a superfluous/wrong-owner coplanar bottom-footprint face. The other12 faces are a closed positive L-prism with no missing directed segment; reversing cap alone still fails closure. No duplicate complete geometric face is claimed.",
 "repairBoundary":"Suppress/assign covered tile by actual fluid-side ownership at its original emitter; do not add balancing cap or relax S10. Counterpart/phase1 origCi mapping still unknown."}))
print(json.dumps({"actualCells":sorted(cells),"uniquePrintedPoints":len(points),"allResidual":[0,0.00390625,0],
 "withoutFace10":"all four exactly closed, edge residual0","reverseFace10":"still−0.00390625Y",
 "originalReferenceVolume":"13/32768","closedDiagnosticVolume":"15/32768","nativeRoundedCentroidsReproduced":True}))
