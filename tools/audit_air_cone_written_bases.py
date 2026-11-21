#!/usr/bin/env python3
"""Read-only written-mesh replay of native base guards; labels are not native IDs."""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from audit_air_mesh_geometry import Geometry,sha
from audit_air_conforming_cones import canonical_cycle

def scan(payload):
    mesh,rank,cpu=payload
    os.sched_setaffinity(0,{cpu})
    path=Path(mesh)/f"processor{rank}/constant/polyMesh"
    g=Geometry(path);bad=[]
    for f in range(g.n_faces):
        ids=g.ids(f);x=g.points[ids]
        cycle=canonical_cycle(list(range(len(x))),x)
        canonical=Geometry.from_arrays(x,[cycle])
        can_area,fc,*_=canonical.face(0)
        original_area,original_fc,*_=g.face(f)
        if np.dot(can_area,original_area)<0:can_area=-can_area
        ar=float(np.linalg.norm(can_area))
        span=max(float(np.linalg.norm(x-fc,axis=1).max()),1e-300)
        plane=float(np.max(np.abs((x-fc)@(can_area/ar))))
        duplicate=len(set(tuple(q) for q in x))!=len(x)
        area_error=float(np.linalg.norm(original_area-can_area)/ar)
        centre_error=float(np.linalg.norm(original_fc-fc)/span)
        patch=g.patch(f)
        unknown=patch is not None and patch["type"] not in ["processor","processorCyclic","cyclic"] and (
            patch["type"].startswith(("cyclic","mapped")) or "AMI" in patch["type"])
        reasons=[]
        if plane>1e-10*span:reasons.append("nonplanar")
        if duplicate:reasons.append("duplicate-coordinate")
        if area_error>1e-10:reasons.append("canonical-area-change")
        if centre_error>1e-10:reasons.append("canonical-centre-change")
        if unknown:reasons.append("unsupported-coupled-type")
        if reasons:
            bad.append({"rank":rank,"writtenFace":f,"owner":int(g.owner[f]),
                "neighbour":int(g.neighbour[f]) if f<len(g.neighbour) else None,
                "patch":patch,"reasons":reasons,"vertexIds":ids.tolist(),"points":x.tolist(),
                "planeDeviationM":plane,"planeToleranceM":1e-10*span,"canonicalSpanM":span,
                "canonicalAreaRelativeError":area_error,"canonicalCentreRelativeError":centre_error,
                "writtenFaceCentre":original_fc.tolist(),"canonicalFaceCentre":fc.tolist()})
    return {"rank":rank,"cells":g.n_cells,"faces":g.n_faces,"badFaces":bad,
            "meshCoreSha256":{name:sha(path/name) for name in ["points","faces","owner","neighbour","boundary","faceZones"]}}

def main():
    p=argparse.ArgumentParser();p.add_argument("--mesh",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--ranks",type=int,default=8);p.add_argument("--cpu-start",type=int,default=24);p.add_argument("--workers",type=int,default=8)
    a=p.parse_args()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        rows=list(pool.map(scan,[(str(a.mesh),i,a.cpu_start+i%a.workers) for i in range(a.ranks)]))
    out={"format":"written-tri-native-base-guard-diagnostic/v1",
        "scope":"Read actual old written mesh only. Written polyMesh labels differ from native pre-write IDs; counts cannot be asserted equal without raw native witnesses. No native execution/mesh/source mutation.",
        "sourceSha256":sha(Path(__file__)),"geometryAuditSourceSha256":sha(Path(__file__).with_name("audit_air_mesh_geometry.py")),
        "canonicalAuditSourceSha256":sha(Path(__file__).with_name("audit_air_conforming_cones.py")),
        "meshPath":str(a.mesh),"ranks":rows,
        "totalBadWrittenFaces":sum(len(x["badFaces"]) for x in rows),
        "totalBadWrittenBaseIncidences":sum(2 if f["neighbour"] is not None else 1 for x in rows for f in x["badFaces"])}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({k:out[k] for k in ["totalBadWrittenFaces","totalBadWrittenBaseIncidences"]}))
if __name__=="__main__":main()
