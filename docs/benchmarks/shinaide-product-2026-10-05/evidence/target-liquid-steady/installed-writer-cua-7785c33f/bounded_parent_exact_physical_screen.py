from pathlib import Path
import sys,json,importlib.util,hashlib,time
from collections import Counter
import numpy as np
A=Path(__file__).resolve().parents[2];E=Path(__file__).resolve().parent
sys.path[:0]=[str(A/"src"),str(A/"tools"),str(E/"readers")]
import air_cone_trace_probe as T
import air_cone_child_union_probe as U
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
G=load("bounded_geometry",E/"readers/audit_air_mesh_geometry.py")
Q=load("bounded_quality",E/"readers/audit_air_cone_remaining_quality.py")
M=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh")
rank=1; parent=30430
t=T.read_trace(M,rank);g=G.Geometry(M/f"processor{rank}/constant/polyMesh")
doc=json.loads((E/"classification.json").read_text())["ranks"][rank]
seeds=[x["cell"] for x in doc["Dcells"] if x["parent"]==parent]
children=set(np.flatnonzero(t["cellParent"]==parent).tolist())
adj={};start=time.monotonic()
for c in children:
 adj[c]=set()
 for f in g.cell(c)[0]:
  f=int(f)
  if f>=len(g.neighbour):continue
  o,n=int(g.owner[f]),int(g.neighbour[f]);other=n if o==c else o
  base=int(t["faceBase"][f])
  zone=int(t["zone"][base]) if base>=0 else -1
  if other in children and zone==-1:adj[c].add(other)
groups={tuple(sorted((c,o))) for c in seeds for o in adj[c]}
layer=set(groups)
for size in (3,4):
 nex=set()
 for group in layer:
  for c in group:
   for n in adj[c]:
    if n not in group:nex.add(tuple(sorted((*group,n))))
 groups.update(nex);layer=nex

from fractions import Fraction as F
def same_axis_plane(ids):
 p=g.points[ids]
 return any(all(float(x)==float(p[0,k]) for x in p[:,k]) for k in range(3))
def exact_convex(ids):
 p=g.points[ids];axis=next((k for k in range(3) if all(float(x)==float(p[0,k]) for x in p[:,k])),None)
 if axis is None:return False
 axes=[k for k in range(3) if k!=axis]
 q=[[F.from_float(float(x[k])) for k in axes] for x in p]
 signs=[]
 for i,a in enumerate(q):
  b=q[(i+1)%len(q)]
  cross=[(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]) for c in q]
  signs.extend(x for x in cross if x)
 return bool(signs) and (all(x>0 for x in signs) or all(x<0 for x in signs))
def exact_physical_join(geo,rows,geometry):
 rows=[dict(r) for r in rows];merged=0
 while True:
  changed=False
  for i in range(len(rows)):
   if changed:break
   for j in range(i+1,len(rows)):
    a,b=rows[i],rows[j]
    if a["other"] is not None or b["other"] is not None or not a["patch"] or not b["patch"]:continue
    if any(a[k]!=b[k] for k in ["patch","zone"]):continue
    ba=set(int(t["faceBase"][f]) for f in a["faces"]);bb=set(int(t["faceBase"][f]) for f in b["faces"])
    if len(ba)!=1 or ba!=bb or min(ba)<0:continue
    ea=list(zip(a["ids"],a["ids"][1:]+a["ids"][:1]));eb=list(zip(b["ids"],b["ids"][1:]+b["ids"][:1]))
    ids=U.loop_from_edges(ea+eb)
    if ids is None or set(ids)!=set(a["ids"]+b["ids"]) or not same_axis_plane(ids) or not exact_convex(ids):continue
    ga=geometry.Geometry.from_arrays(g.points,[a["ids"]]);gb=geometry.Geometry.from_arrays(g.points,[b["ids"]]);gg=geometry.Geometry.from_arrays(g.points,[ids])
    sa,ca,*_=ga.face(0);sb,cb,*_=gb.face(0);sg,cg,*_=gg.face(0)
    aa,ab,ag=map(np.linalg.norm,(sa,sb,sg));span=max(np.ptp(g.points[ids],axis=0).max(),1e-6)
    if min(aa,ab,ag)<=1e-12:continue
    if max(np.linalg.norm(sg-sa-sb)/(aa+ab),abs(ag-aa-ab)/(aa+ab),np.linalg.norm(ag*cg-aa*ca-ab*cb)/((aa+ab)*span))>1e-10:continue
    rows[i]={**a,"ids":ids,"faces":a["faces"]+b["faces"]};rows.pop(j);merged+=1;changed=True;break
  if not changed:break
 return rows,merged
U.coalesce=exact_physical_join
out=[];classes=Counter();bySize=Counter();passed=[]
for group in sorted(groups,key=lambda x:(len(x),x)):
 rawrows=U.merged_boundary(g,group,t)
 rows,joins=exact_physical_join(g,rawrows,G)
 if any(r["patch"] and r["patch"]["type"] in ("cyclic","processor","processorCyclic") for r in rows):
  classes["coupled-protected"]+=1;continue
 cand=G.Geometry.from_arrays(g.points,[r["ids"] for r in rows])
 cls=cand.classify(0,False);classes[cls["classification"]]+=1;bySize[len(group)]+=1
 if cls["classification"]!="convex-no-face-plane-trigger":continue
 row=U.metrics(g,group,t,G,Q,True)
 row["joinedPhysicalFacets"]=joins
 row["preCoalesceOutsideFacets"]=len(rawrows)
 fs,sf,fc,cc,v,*_=cand.cell(0);angles=[]
 for r,s,c in zip(rows,sf,fc):
  if r["other"] is None:continue
  d=g.cell(r["other"])[3]-cc
  angles.append(float(np.degrees(np.arccos(np.clip(np.dot(d,s)/(np.linalg.norm(d)*np.linalg.norm(s)+1e-150),-1,1)))))
 row["maximumNonorthogonalityDegrees"]=max(angles,default=0)
 row["shell"]=rows;row["completeGeometry"]=cand.classify(0,True)
 row["passesFiveAndGeometry"]=bool(row["passesAvailableLocalGeometryAndQuality"] and row["maximumNonorthogonalityDegrees"]<=70)
 row["oldD"]=[Q.determinant([s for f,s in zip(g.cell(c)[0],g.cell(c)[1]) if f<len(g.neighbour) or (g.patch(int(f)) and g.patch(int(f))["type"] in ("cyclic","processor","processorCyclic"))])[0] for c in group]
 out.append(row)
 if row["passesFiveAndGeometry"]:passed.append(row)
result={"format":"actual-7785-single-parent-bounded-exact-physical-union-screen/v1",
 "scope":"One actual healthy-axis-box mother, connected groups of2 through4 containing an actual D failure. Only physical same sourceBase/patch/zone, exact axis support, all old nodes, exact convex edge support and area/M1 guarded coalescence; outside IDs never merged, no mesh/native mutation. A local five-metric/geometry screen is not complete lineage/stencil authorization.",
 "mesh":str(M),"rank":rank,"parent":parent,"seeds":seeds,"children":len(children),
 "groups":len(groups),"testedSizes":dict(bySize),"classes":dict(classes),
 "geometrySurvivors":len(out),"fiveSurvivors":len(passed),"elapsedSeconds":time.monotonic()-start,
 "trials":out,"toolSHA":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(E/"bounded-parent-exact-physical-union-screen.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps({k:v for k,v in result.items() if k not in ("trials","mesh")},indent=2))
print("PASSES",json.dumps([{k:r[k] for k in ("cells","externalFaces","determinant","minimumWeight","minimumVolumeRatio","maximumSkewness","maximumNonorthogonalityDegrees","oldD")} for r in passed],indent=2))
