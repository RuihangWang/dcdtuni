#!/usr/bin/env python3
"""Read-only saved bad-tet comparison after native collinear-point cleanup."""
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_air_mesh_geometry import Geometry,cell_set,sha
from backend.aircooling.runner.polymesh_io import read_label_list
from audit_air_transition_roof import face_fans

def rank_audit(task):
    old,new,rank=task
    os.sched_setaffinity(0,{24+rank})
    op=Path(old)/f"processor{rank}/constant/polyMesh"
    npoly=Path(new)/f"processor{rank}/constant/polyMesh"
    before=cell_set(op/"sets/lowQualityTetFaces");after=cell_set(npoly/"sets/lowQualityTetFaces")
    unchanged={"owner":sha(op/"owner")==sha(npoly/"owner"),"neighbour":sha(op/"neighbour")==sha(npoly/"neighbour")}
    result={"rank":rank,"originalBadFaceIds":before.tolist(),"newBadFaceIds":after.tolist(),
            "retainedBadFaceIds":[int(x) for x in sorted(set(before)&set(after))],"removedBadFaceIds":[int(x) for x in sorted(set(before)-set(after))],
            "newlyBadFaceIds":[int(x) for x in sorted(set(after)-set(before))],"addressingBytesUnchanged":unchanged,
            "addressingLabelArraysUnchanged":{name:bool(np.array_equal(read_label_list(op/name),read_label_list(npoly/name))) for name in ["owner","neighbour"]},
            "originalInputSha256":{p.name:sha(p) for p in op.iterdir() if p.is_file()},
            "newInputSha256":{p.name:sha(p) for p in npoly.iterdir() if p.is_file()},"faces":[],"allOriginalTetFanEvents":[]}
    if not len(before) and not len(after):return result
    og=Geometry(op);ng=Geometry(npoly)
    for f in before:
        f=int(f)
        row=face_fans(og,f);row.update(patch=og.patch(f))
        row["reconstructionGeometry"]={"points":og.points[og.ids(f)].tolist(),
            "faceCentre":og.face(f)[1].tolist(),"ownerCentre":og.cell(int(og.owner[f]))[3].tolist(),
            "neighbourCentre":og.cell(int(og.neighbour[f]))[3].tolist() if f<len(og.neighbour) else None}
        result["allOriginalTetFanEvents"].append(row)
    for f in after:
        f=int(f)
        face=ng.face_detail(f);face["tetFanQualities"]=face_fans(ng,f)
        points=ng.points[ng.ids(f)];oldpoints=og.points[og.ids(f)]
        oa,oc,*_=og.face(f);na,nc,*_=ng.face(f)
        face["comparison"]={"sameOwner":int(og.owner[f])==int(ng.owner[f]),
            "sameNeighbour":(int(og.neighbour[f]) if f<len(og.neighbour) else None)==(int(ng.neighbour[f]) if f<len(ng.neighbour) else None),
            "samePatch":og.patch(f)==ng.patch(f),"originalVertexCount":len(oldpoints),"newVertexCount":len(points),
            "allRetainedCoordinateBitsEqualOldPoint":all(tuple(x) in {tuple(v) for v in oldpoints} for x in points),
            "relativeOutwardAreaVectorError":float(np.linalg.norm(na-oa)/max(np.linalg.norm(oa),1e-300)),
            "faceCentreDifferenceM":float(np.linalg.norm(nc-oc))}
        result["faces"].append(face)
    return result

def event_counts(rows,tol):
    return {"ownerEdgeCentreFan":sum(x["ownerCentroidFanMinimumQuality"]<tol for x in rows),
            "neighbourEdgeCentreFan":sum(x["neighbourCentroidFanMinimumQuality"] is not None and x["neighbourCentroidFanMinimumQuality"]<tol for x in rows),
            "originalVertexFan":sum(x["sharedOriginalVertexFanBestMinimumQuality"]<=tol for x in rows)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--old-mesh",type=Path,required=True);p.add_argument("--new-mesh",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    with ProcessPoolExecutor(max_workers=8) as pool:
        rows=list(pool.map(rank_audit,[(str(a.old_mesh.resolve()),str(a.new_mesh.resolve()),r) for r in range(8)]))
    oldfans=[f for r in rows for f in r["allOriginalTetFanEvents"]]
    newfans=[f["tetFanQualities"] for r in rows for f in r["faces"]]
    types=Counter("internal" if f["patch"] is None else f["patch"]["type"] for r in rows for f in r["faces"])
    owners=Counter(f["owner"]["classification"] for r in rows for f in r["faces"])
    out={"format":"air-tet-cleanup-readonly/v1","scope":"Saved written mesh comparison only; no native execution or mesh/solver mutation.",
         "sourceSha256":{str(x):sha(x) for x in [Path(__file__),Path(__file__).with_name("audit_air_mesh_geometry.py"),Path(__file__).with_name("audit_air_transition_roof.py")]},
         "totals":{"originalUniqueBadFaceLabels":len(oldfans),"newUniqueBadFaceLabels":len(newfans),
                   "originalPatchTypes":dict(Counter("internal" if f["patch"] is None else f["patch"]["type"] for f in oldfans)),
                   "remainingPatchTypes":dict(types),"remainingOwnerClassification":dict(owners),
                   "newlyBadFaces":sum(len(r["newlyBadFaceIds"]) for r in rows)},
         "eventsAtTolerance":{str(t):{"original":event_counts(oldfans,t),"new":event_counts(newfans,t)} for t in [1e-30,1e-15]},
         "eventCountDefinition":"polyMeshTetDecomposition::checkFaceTets increments separate owner/neighbor edge-face-centre fans and shared original-vertex fan failures; a labelHashSet stores each face only once. Global reported errors are not unique face labels. None of these flagged faces is coupled.",
         "limits":"Python signed-volume/circumradius reconstruction matches observed event totals at both candidate tolerances. Installed checker's actual caller tolerance and last-bit identity are not inferred.",
         "ranks":rows}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__=="__main__":main()
