#!/usr/bin/env python3
"""Read-only combinatorial hole and exact area audit on written illegal cells."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json
from collections import Counter
from fractions import Fraction as F
from pathlib import Path
import numpy as np
spec=importlib.util.spec_from_file_location("geometry",Path(__file__).with_name("audit_air_mesh_geometry.py"))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
spec2=importlib.util.spec_from_file_location("signed",Path(__file__).with_name("audit_air_signed_face_partition.py"))
s=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(s)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def audit_cell(mesh,c):
    fs=mesh.cell_faces[mesh.cell_offsets[c]:mesh.cell_offsets[c+1]]
    faces=[];directed=Counter()
    allpoints=set()
    for f0 in fs:
        f=int(f0);ids=mesh.ids(f).tolist()
        outward=ids if mesh.owner[f]==c else ids[::-1]
        directed.update(s.edges(outward));allpoints.update(ids)
        area=mesh.face(f)[0]
        faces.append({"face":f,"owner":int(mesh.owner[f]),
           "neighbour":int(mesh.neighbour[f]) if f<len(mesh.neighbour) else -1,
           "patch":mesh.patch(f),"pointIds":ids,"points":mesh.points[ids].tolist(),
           "areaM2":float(np.linalg.norm(area))})
    unmatched=[(b,a) for (a,b),n in directed.items() for _ in range(max(0,n-directed[(b,a)]))]
    next_ids={}
    for a,b in unmatched:next_ids.setdefault(a,[]).append(b)
    holes=[]
    if unmatched and all(len(v)==1 for v in next_ids.values()) and set(next_ids)==set(b for _,b in unmatched):
        remaining=set(next_ids)
        while remaining:
            first=min(remaining);loop=[];at=first
            while at in remaining:
                loop.append(at);remaining.remove(at);at=next_ids[at][0]
            if at!=first:raise ValueError("hole edges do not close")
            holes.append(loop)
    candidates=[]
    for loop in holes:
        p=mesh.points[loop]
        if len(loop)==3:
            q=[tuple(map(s.rational,x)) for x in p]
            area=tuple(v/2 for v in s.cross3(s.sub(q[1],q[0]),s.sub(q[2],q[0])))
        elif len(loop)>3:
            area=s.outline_oracle(p)["area"]
        else: area=(F(0),)*3
        area_exact_norm=float(sum(v*v for v in area))**.5
        lengths=np.linalg.norm(np.roll(p,-1,axis=0)-p,axis=1)
        newell_areas=[]
        for direction in [list(p),list(p)[::-1]]:
            for k in range(len(direction)):
                order=direction[k:]+direction[:k];n=np.zeros(3)
                for a,b in s.edges(order):
                    n[0]+=(a[1]-b[1])*(a[2]+b[2])
                    n[1]+=(a[2]-b[2])*(a[0]+b[0])
                    n[2]+=(a[0]-b[0])*(a[1]+b[1])
                newell_areas.append(float(np.linalg.norm(.5*n)))
        candidates.append({"pointIds":loop,"points":p.tolist(),"vertexCount":len(loop),
            "areaVectorFromExactWrittenCoordinatesM2":s.as_float(area),
            "exactAreaM2":area_exact_norm,"minimumEdgeLengthM":float(lengths.min()),
            "writerNewellAreaAcrossAllCyclesM2":newell_areas,
            "maximumWriterNewellAreaM2":max(newell_areas),
            "writerWouldDropAtArea1e-12":all(v<=1e-12 for v in newell_areas),
            "allHolePointIdsRemainInWrittenFaces":all(q in allpoints for q in loop)})
    return {"cell":c,"writtenFaceCount":len(fs),"writtenUniquePoints":len(allpoints),
       "allWrittenFacesTriangles":all(len(f["pointIds"])==3 for f in faces),
       "faces":faces,"unpairedOutwardEdges":unmatched,"closedHoleCycles":candidates,
       "openTetrahedronPattern":len(fs)==3 and len(allpoints)==4 and len(candidates)==1 and len(candidates[0]["pointIds"])==3,
       "minimumWrittenPointPairDistanceM":min(float(np.linalg.norm(mesh.points[a]-mesh.points[b])) for a in allpoints for b in allpoints if a<b)}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mesh",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);a=ap.parse_args()
    ranks=[];hashes={}
    for rank in range(8):
        p=a.mesh/f"processor{rank}/constant/polyMesh"
        illegal=g.cell_set(p/"sets/illegalCells");zipup=g.cell_set(p/"sets/zipUpCells")
        assert set(illegal)==set(zipup),(rank,illegal,zipup)
        for name in ["points","faces","owner","neighbour","boundary","sets/illegalCells","sets/zipUpCells"]:
            hashes[str((p/name).relative_to(a.mesh))]=sha(p/name)
        if len(illegal):
            m=g.Geometry(p)
            rows=[audit_cell(m,int(c)) for c in sorted(illegal)]
            del m
        else:rows=[]
        ranks.append({"rank":rank,"illegalCells":len(illegal),"cells":rows})
    rows=[c for r in ranks for c in r["cells"]];holes=[h for c in rows for h in c["closedHoleCycles"]]
    out={"format":"written-cone-illegal-hole-audit/v1",
      "scope":"Read-only written mesh diagnosis after full checker rejection; missing polygons are inferred from exact unpaired cell edge cycles, not native prewrite face provenance.",
      "meshPath":str(a.mesh),"meshSha256":hashes,
      "sourceSha256":{p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name("audit_air_mesh_geometry.py"),Path(__file__).with_name("audit_air_signed_face_partition.py")]},
      "summary":{"illegalCells":len(rows),"allIllegalMatchZipUpCells":True,
          "ranksWithIllegalCells":sum(r["illegalCells"]>0 for r in ranks),
          "allAreThreeFaceFourPointOpenTetrahedra":all(c["openTetrahedronPattern"] for c in rows),
          "inferredMissingTriangularFaces":len(holes),
          "allHolePointIdsStillWritten":all(h["allHolePointIdsRemainInWrittenFaces"] for h in holes),
          "maximumMissingTriangleAreaM2":max(h["exactAreaM2"] for h in holes),
          "maximumWriterNewellMissingTriangleAreaAcrossEveryCycleM2":max(h["maximumWriterNewellAreaM2"] for h in holes),
          "allMissingTrianglesBelowExistingWriterAreaDrop":all(h["writerWouldDropAtArea1e-12"] for h in holes),
          "minimumHoleEdgeLengthM":min(h["minimumEdgeLengthM"] for h in holes),
          "minimumAnyIllegalCellPointDistanceM":min(c["minimumWrittenPointPairDistanceM"] for c in rows)},
      "limits":["Topology and exact area refer to the final written coordinates, not an unrecorded prewriter mesh.",
        "The writer's existing <=1e-12 m2 drop condition would remove every inferred missing base. Definitive first-mutation tracing still requires native prewrite witness or local writer audit.",
        "All four vertex IDs remain in every affected open tetrahedron; current evidence does not support claiming that these missing faces were caused by point merging.",
        "This audit does not reproduce or explain rank6 globalEdgeTransform; no coupled correctness is implied.","No mesh, source, binary, threshold, solver or API was changed or run."],
      "ranks":ranks}
    a.output.write_text(json.dumps(out,indent=2)+"\n");print(json.dumps(out["summary"],indent=2))
if __name__=="__main__":main()
