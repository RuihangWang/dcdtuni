#!/usr/bin/env python3
"""Match native pre-write base witnesses to written faces by exact cyclic coordinates."""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path
import numpy as np
from audit_air_mesh_geometry import Geometry, sha
from audit_air_conforming_cones import canonical_cycle

def coordinate_key(points):
    x=np.asarray(points,dtype=np.float64)
    if x.ndim!=2 or x.shape[1]!=3 or not np.isfinite(x).all():
        raise ValueError("Expected finite Nx3 coordinates")
    cycle=canonical_cycle(list(range(len(x))),x)
    return x[cycle].tobytes()

def split_guard(points):
    """Replay the native convex/collinear decisions using its original winding."""
    x=np.asarray(points,dtype=float)
    cycle=canonical_cycle(list(range(len(x))),x)
    area,centre,*_=Geometry.from_arrays(x,[cycle]).face(0)
    original=Geometry.from_arrays(x,[list(range(len(x)))]).face(0)[0]
    if np.dot(area,original)<0: area=-area
    normal=area/np.linalg.norm(area)
    span=float(np.linalg.norm(x-centre,axis=1).max())
    convex=True;collinear=False;min_support=float("inf")
    for j,q in enumerate(x):
        edge=x[(j+1)%len(x)]-q
        support=np.cross(edge,x-q)@normal/np.linalg.norm(edge)
        min_support=min(min_support,float(support.min()))
        if support.min() < -1e-12*span: convex=False
        da=x[(j-1)%len(x)]-q;db=edge
        if np.dot(da,db)<0 and np.linalg.norm(np.cross(da,db))<=1e-12*np.linalg.norm(da)*np.linalg.norm(db):
            collinear=True
    return {"projectedConvex":convex,"collinearVertex":collinear,
            "splitRequired":collinear or not convex,"minimumProjectedEdgeSupportM":min_support,
            "canonicalCentre":centre.tolist(),
            "planeDeviationM":float(np.max(np.abs((x-centre)@normal))),
            "planeToleranceM":1e-10*span}

def analyse(written,native_rows):
    index={};count=0
    for row in written["ranks"]:
        for face in row["badFaces"]:
            key=(row["rank"],coordinate_key(face["points"]))
            if key in index: raise ValueError("Ambiguous written coordinate-cycle identity")
            index[key]=face;count+=1
    used=set();matches=[]
    for row in native_rows:
        rank=row["rank"]
        for native in row["badBaseFaces"]:
            key=(rank,coordinate_key(native["points"]))
            if key not in index: raise ValueError("Native witness lacks an exact written coordinate-cycle match")
            if key in used: raise ValueError("Duplicate native witness coordinate identity")
            used.add(key);face=index[key]
            guard=split_guard(native["points"])
            if guard["splitRequired"]!=native["splitRequired"]:
                raise ValueError("Independent split decision differs from native")
            if native["reasonMask"]!=2 or face["reasons"]!=["nonplanar"]:
                raise ValueError("Unexpected base rejection reason")
            matches.append({"rank":rank,"nativeFace":native["face"],"writtenFace":face["writtenFace"],
                "nativeOwner":native["owner"],"nativeNeighbour":native["neighbour"],
                "writtenOwner":face["owner"],"writtenNeighbour":face["neighbour"],"writtenPatch":face["patch"],
                "coordinateCycleBitIdentical":True,"pointCount":len(native["points"]),
                "nativeReasonMask":native["reasonMask"],"nativePlaneDeviationM":native["maxOffPlane"],
                "writtenPlaneDeviationM":face["planeDeviationM"],
                "recomputedVsNativePlaneDeviationDifferenceM":abs(guard["planeDeviationM"]-native["maxOffPlane"]),
                "recomputedVsWrittenPlaneDeviationDifferenceM":abs(guard["planeDeviationM"]-face["planeDeviationM"]),
                "nativeSplitRequired":native["splitRequired"],"independentGuard":guard})
    if len(used)!=count: raise ValueError("Not every rejected written face has one native witness")
    return {"matchedFaces":len(matches),
        "writtenCellBaseIncidences":sum(2 if x["writtenNeighbour"] is not None else 1 for x in matches),
        "needsFanFaces":sum(x["nativeSplitRequired"] for x in matches),
        "needsFanCellBaseIncidences":sum((2 if x["writtenNeighbour"] is not None else 1) for x in matches if x["nativeSplitRequired"]),
        "unsplitWarpedFaces":sum(not x["nativeSplitRequired"] for x in matches),
        "allCoordinateCyclesBitIdentical":all(x["coordinateCycleBitIdentical"] for x in matches),
        "allSplitDecisionsMatch":all(x["nativeSplitRequired"]==x["independentGuard"]["splitRequired"] for x in matches),
        "maximumRecomputedVsNativePlaneDeviationDifferenceM":max(x["recomputedVsNativePlaneDeviationDifferenceM"] for x in matches),
        "matches":matches}

def main():
    p=argparse.ArgumentParser();p.add_argument("--written",type=Path,required=True)
    p.add_argument("--native-dir",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    paths=sorted(a.native_dir.glob("conforming-cone-preflight-rank*.json"))
    rows=[json.loads(path.read_text()) for path in paths]
    if sorted(row["rank"] for row in rows)!=list(range(8)):
        raise ValueError("Expected complete eight-rank native witness set")
    out={"format":"conforming-cone-exact-base-witness-audit/v1",
        "scope":"Read-only identity and rejected-base analysis; no native execution, point/mesh mutation or product-case acceptance.",
        "sourceSha256":sha(Path(__file__)),"writtenAuditSha256":sha(a.written),
        "geometryAuditSourceSha256":sha(Path(__file__).with_name("audit_air_mesh_geometry.py")),
        "canonicalAuditSourceSha256":sha(Path(__file__).with_name("audit_air_conforming_cones.py")),
        "nativeFilesSha256":{path.name:sha(path) for path in paths},
        "results":analyse(json.loads(a.written.read_text()),rows),
        "limits":["Native and written labels are independent and are mapped only through exact float64 coordinate cycles.",
                  "Keeping an unsplit warped source polygon does not establish its cone child's strict geometry quality.",
                  "Five split-required warped faces remain outside the planar fan proof; unsplit retention alone cannot authorize this mesh.",
                  "No coupled matching, transformed boundary support or full product checker acceptance is inferred from these non-coupled base witnesses."]}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({k:v for k,v in out["results"].items() if k!="matches"}))

if __name__=="__main__":main()
