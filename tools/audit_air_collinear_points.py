#!/usr/bin/env python3
"""Read-only all-incident-face collinear redundancy on saved bad-tet faces."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_air_mesh_geometry import Geometry, cell_set, sha

def incident_check(points,index,patch=None):
    points=np.asarray(points,dtype=float)
    p=points[index];a=points[(index-1)%len(points)];b=points[(index+1)%len(points)]
    edge=b-a;den=float(edge@edge)
    tol=1e-10*max(1.,float(np.ptp(points,axis=0).max()))
    fraction=float((p-a)@edge/den) if den else None
    distance=float(np.linalg.norm(p-a-fraction*edge)) if den else None
    coupled=bool(patch and (patch["type"].startswith(("processor","cyclic","mapped")) or "AMI" in patch["type"]))
    eligible=len(points)>3 and den>0 and distance<=tol and 1e-10<fraction<1-1e-10
    return {"verticesBefore":len(points),"verticesAfter":len(points)-1,
            "betweenFraction":fraction,"distanceFromNeighbourLineM":distance,
            "auditToleranceM":tol,"geometricallyRedundantHere":bool(eligible),"coupledVeto":coupled,
            "previousPoint":a.tolist(),"nextPoint":b.tolist(),"patch":patch}

def rank_audit(task):
    mesh,rank=task
    os.sched_setaffinity(0,{24+rank})
    poly=Path(mesh)/f"processor{rank}/constant/polyMesh"
    bad=cell_set(poly/"sets/lowQualityTetFaces")
    hashes={p.name:sha(p) for p in poly.iterdir() if p.is_file()}
    if not len(bad):
        return {"rank":rank,"badFaces":0,"inputSha256":hashes,"points":[],"faces":[]}
    g=Geometry(poly)
    candidates=np.unique(np.concatenate([g.ids(int(f)) for f in bad]))
    positions=np.flatnonzero(np.isin(g.vertices,candidates))
    face_ids=np.searchsorted(g.offsets[1:],positions,side="right")
    incident=defaultdict(list)
    for pos,f in zip(positions,face_ids):
        incident[int(g.vertices[pos])].append((int(f),int(pos-g.offsets[f])))
    rows=[];eligible=set()
    for point in candidates:
        checks=[]
        for face,index in incident[int(point)]:
            ids=g.ids(face)
            check=incident_check(g.points[ids],index,g.patch(face))
            check.update(face=face,vertexIds=ids.tolist(),owner=int(g.owner[face]),
                         neighbour=int(g.neighbour[face]) if face<len(g.neighbour) else None)
            checks.append(check)
        all_collinear=all(x["geometricallyRedundantHere"] for x in checks)
        veto=any(x["coupledVeto"] for x in checks)
        candidate=all_collinear and not veto
        if candidate:eligible.add(int(point))
        rows.append({"point":int(point),"coordinates":g.points[point].tolist(),"incidentFaceCount":len(checks),
                     "allIncidentPolygonsCollinearBetween":all_collinear,"touchesCoupledPatch":veto,
                     "conservativeLocalCandidate":candidate,"incidentFaces":checks})
    faces=[]
    for f in bad:
        ids=g.ids(int(f))
        removable=[int(x) for x in ids if x in eligible]
        faces.append({"face":int(f),"patch":g.patch(int(f)),"vertexIds":ids.tolist(),
                      "points":g.points[ids].tolist(),"candidatePointIds":removable,
                      "remainingVertices":len(ids)-len(removable)})
    return {"rank":rank,"badFaces":len(bad),"inputSha256":hashes,"setSha256":sha(poly/"sets/lowQualityTetFaces"),
            "points":rows,"faces":faces}

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--mesh",type=Path,required=True)
    a.add_argument("--output",type=Path,required=True)
    a.add_argument("--workers",type=int,default=8)
    args=a.parse_args()
    mesh=args.mesh.resolve()
    ranks=sorted(int(p.name[9:]) for p in mesh.glob("processor[0-9]*") if p.name[9:].isdigit())
    if not ranks or ranks!=list(range(len(ranks))):raise ValueError("Requires contiguous MPI rank partitions")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows=list(pool.map(rank_audit,[(str(mesh),r) for r in ranks]))
    points=[p for r in rows for p in r["points"]];faces=[f for r in rows for f in r["faces"]]
    out={"format":"air-collinear-point-readonly/v1","mesh":str(mesh),
         "scope":"Necessary local geometric redundancy counts only. No points or faces are removed. Does not certify a conforming native repair or full checkMesh pass.",
         "coupledScope":"All processor/cyclic/mapped/AMI point incidents vetoed. Remote geometry is not inferred.",
         "criteria":"Point strictly between prev/next on a line within recorded tolerance in EVERY local incident polygon; >=3 remaining vertices in each; no coupled patch incident.",
         "simultaneousRemovalLimit":"Per-point eligibility does not prove safety of removing several adjacent points simultaneously. Neighbor IDs/patch identities and all diagnostic field/zone mappings still require preservation.",
         "sourceSha256":{str(p):sha(p) for p in [Path(__file__).resolve(),Path(__file__).resolve().with_name("audit_air_mesh_geometry.py")]},
         "totals":{"localBadFaces":len(faces),"uniqueLocalCandidateInputPoints":len(points),
                   "allIncidentCollinearPoints":sum(p["allIncidentPolygonsCollinearBetween"] for p in points),
                   "coupledVetoedPoints":sum(p["touchesCoupledPatch"] for p in points),
                   "conservativeLocalCandidatePoints":sum(p["conservativeLocalCandidate"] for p in points),
                   "badFacesWithCandidatePoint":sum(bool(f["candidatePointIds"]) for f in faces)},
         "ranks":rows}
    args.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__=="__main__":main()
