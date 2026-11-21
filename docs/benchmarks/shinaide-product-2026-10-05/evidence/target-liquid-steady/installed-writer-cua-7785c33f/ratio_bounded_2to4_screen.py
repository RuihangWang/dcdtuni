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

doc=json.loads((E/"classification.json").read_text());results=[]
for rank in [1,3]:
 t=T.read_trace(M,rank);g=G.Geometry(M/f"processor{rank}/constant/polyMesh")
 f=next(f for f in doc["ranks"][rank]["failedFaces"] if "R" in f["failedFlags"])
 parent=f["owner"]["parent"];pair=(f["owner"]["cell"],f["neighbour"]["cell"])
 children=set(np.flatnonzero(t["cellParent"]==parent).tolist());adj={}
 for c in children:
  adj[c]=set()
  for face in g.cell(c)[0]:
   face=int(face)
   if face>=len(g.neighbour):continue
   o,n=int(g.owner[face]),int(g.neighbour[face]);other=n if o==c else o
   base=int(t["faceBase"][face]);zone=int(t["zone"][base]) if base>=0 else -1
   if other in children and zone==-1:adj[c].add(other)
 groups={tuple(sorted(pair))};layer=set(groups)
 for size in (3,4):
  nex=set()
  for group in layer:
   for c in group:
    for n in adj[c]:
     if n not in group:nex.add(tuple(sorted((*group,n))))
  groups.update(nex);layer=nex
 classes=Counter();survivors=[];start=time.monotonic()
 for group in sorted(groups,key=lambda x:(len(x),x)):
  raw=U.merged_boundary(g,group,t);rows,joins=exact_physical_join(g,raw,G)
  if any(r["patch"] and r["patch"]["type"] in ["cyclic","processor","processorCyclic"] for r in rows):
   classes["coupled-protected"]+=1;continue
  cand=G.Geometry.from_arrays(g.points,[r["ids"] for r in rows]);cl=cand.classify(0,False);classes[cl["classification"]]+=1
  if cl["classification"]!="convex-no-face-plane-trigger":continue
  r=U.metrics(g,group,t,G,Q,True);angles=[]
  fs,sf,fc,cc,v,*_=cand.cell(0)
  for x,a,b in zip(rows,sf,fc):
   if x["other"] is None:continue
   delta=g.cell(x["other"])[3]-cc
   angles.append(float(np.degrees(np.arccos(np.clip(np.dot(delta,a)/(np.linalg.norm(delta)*np.linalg.norm(a)+1e-150),-1,1)))))
  r.update(maximumNonorthogonalityDegrees=max(angles,default=0),joinedPhysicalFacets=joins,completeGeometry=cand.classify(0,True))
  r["passesFiveAndGeometry"]=bool(r["passesAvailableLocalGeometryAndQuality"] and r["maximumNonorthogonalityDegrees"]<=70)
  survivors.append(r)
 out={"rank":rank,"parent":parent,"actualRatioFace":f["face"],"seedPair":pair,"groups":len(groups),"classes":dict(classes),"geometrySurvivors":survivors,"localFiveSurvivors":sum(x["passesFiveAndGeometry"] for x in survivors),"elapsedSeconds":time.monotonic()-start}
 results.append(out);print(json.dumps({k:v for k,v in out.items() if k!="geometrySurvivors"}),flush=True)
(E/"ratio-bounded-2to4-union-screen.json").write_text(json.dumps({"scope":"Only two actual failed R faces, mandatory adjacent seed pair plus at most two same-parent neighbors; exact physical coalescence only; no native/API/mesh write. Local screen is not full lineage/coupled-writer authorization.","mesh":str(M),"results":results},indent=2)+"\n")
