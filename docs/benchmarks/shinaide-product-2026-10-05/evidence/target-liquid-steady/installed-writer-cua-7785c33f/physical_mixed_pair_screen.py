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

doc=json.loads((E/"classification.json").read_text());out=[];stats=Counter();tested=set();positive=None
for rank in range(8):
 rows=[r for r in doc["ranks"][rank]["Dcells"] if r["shape"]=="quad-pyramid" and r.get("basePatch") and r["basePatch"]["type"] not in ["cyclic","processor","processorCyclic"]]
 stats["badPhysicalQuadPyramidSeeds"]+=len(rows)
 if not rows:continue
 t=T.read_trace(M,rank);g=G.Geometry(M/f"processor{rank}/constant/polyMesh")
 for row in rows:
  c=row["cell"];parent=row["parent"]
  for face in g.cell(c)[0]:
   face=int(face)
   if face>=len(g.neighbour):continue
   own,nei=int(g.owner[face]),int(g.neighbour[face]);other=nei if own==c else own
   if t["cellParent"][other]!=parent:continue
   key=(rank,*sorted((c,other)))
   if key in tested:continue
   tested.add(key)
   raw=U.merged_boundary(g,[c,other],t)
   if any(r["patch"] and r["patch"]["type"] in ["cyclic","processor","processorCyclic"] for r in raw):
    stats["coupledCavityRejected"]+=1;continue
   joined,joins=exact_physical_join(g,raw,G)
   if not joins:stats["noExactSameRolePhysicalJoin"]+=1;continue
   stats["exactPhysicalJoinCandidates"]+=1
   cand=G.Geometry.from_arrays(g.points,[r["ids"] for r in joined]);cl=cand.classify(0,False)
   stats[cl["classification"]]+=1
   if cl["classification"]!="convex-no-face-plane-trigger":continue
   result=U.metrics(g,[c,other],t,G,Q,True)
   fs,sf,fc,cc,v,*_=cand.cell(0);angles=[]
   for x,a,b in zip(joined,sf,fc):
    if x["other"] is None:continue
    delta=g.cell(x["other"])[3]-cc
    angles.append(float(np.degrees(np.arccos(np.clip(np.dot(delta,a)/(np.linalg.norm(delta)*np.linalg.norm(a)+1e-150),-1,1)))))
   result.update(rank=rank,parent=parent,maximumNonorthogonalityDegrees=max(angles,default=0),joinedPhysicalFacets=joins,rawOutsideFacetCount=len(raw),shell=joined,completeGeometry=cand.classify(0,True),seed=row)
   result["passesFiveAndGeometry"]=bool(result["passesAvailableLocalGeometryAndQuality"] and result["maximumNonorthogonalityDegrees"]<=70)
   out.append(result)
   if result["passesFiveAndGeometry"]:
    positive=result;stats["localFivePositive"]+=1;break
  if positive:break
 if positive:break
result={"scope":"Targeted only actual D-failed physical quad-base pyramids and adjacent same-parent cell; exact same-sourceBase/patch/zone physical facet outline join, all original nodes retained. Stop at first local five-positive. No native/API/mesh/source edit; full lineage/writer proof remains separate.","mesh":str(M),"testedPairs":len(tested),"counters":dict(stats),"localGeometrySurvivors":out,"firstPositive":positive}
(E/"physical-mixed-pair-screen.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps({k:v for k,v in result.items() if k not in ["localGeometrySurvivors","firstPositive","mesh"]}),flush=True)
if positive:print(json.dumps({k:positive[k] for k in ["rank","parent","cells","externalFaces","determinant","minimumWeight","minimumVolumeRatio","maximumSkewness","maximumNonorthogonalityDegrees","joinedPhysicalFacets","rawOutsideFacetCount"]}),flush=True)
