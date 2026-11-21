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
out=[];classes=Counter();bySize=Counter();passed=[]
for group in sorted(groups,key=lambda x:(len(x),x)):
 rows=U.merged_boundary(g,group,t)
 if any(r["patch"] and r["patch"]["type"] in ("cyclic","processor","processorCyclic") for r in rows):
  classes["coupled-protected"]+=1;continue
 cand=G.Geometry.from_arrays(g.points,[r["ids"] for r in rows])
 cls=cand.classify(0,False);classes[cls["classification"]]+=1;bySize[len(group)]+=1
 if cls["classification"]!="convex-no-face-plane-trigger":continue
 row=U.metrics(g,group,t,G,Q,False)
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
result={"format":"actual-7785-single-parent-bounded-literal-union-screen/v1",
 "scope":"One actual healthy-axis-box mother, connected groups of2 through4 containing an actual D failure. Literal external facets only, no coalesce, no mesh/native mutation. A local five-metric/geometry screen is not complete lineage/stencil authorization.",
 "mesh":str(M),"rank":rank,"parent":parent,"seeds":seeds,"children":len(children),
 "groups":len(groups),"testedSizes":dict(bySize),"classes":dict(classes),
 "geometrySurvivors":len(out),"fiveSurvivors":len(passed),"elapsedSeconds":time.monotonic()-start,
 "trials":out,"toolSHA":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(E/"bounded-parent-literal-union-screen.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps({k:v for k,v in result.items() if k not in ("trials","mesh")},indent=2))
print("PASSES",json.dumps([{k:r[k] for k in ("cells","externalFaces","determinant","minimumWeight","minimumVolumeRatio","maximumSkewness","maximumNonorthogonalityDegrees","oldD")} for r in passed],indent=2))
