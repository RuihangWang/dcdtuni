#!/usr/bin/env python3
"""Read source-STL coordinates against existing rejected face witnesses; never mutates a mesh."""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import numpy as np
from audit_air_mesh_geometry import Geometry,sha

def read_ascii_stl(path):
    rows=[];solid="";vertices=[];local=0
    for line in Path(path).read_text().splitlines():
        words=line.split()
        if not words:continue
        if words[0]=="solid":solid=" ".join(words[1:]);local=0
        elif words[0]=="vertex":vertices.append([float(v) for v in words[1:4]])
        elif words[0]=="endfacet":
            if len(vertices)!=3:raise ValueError("Invalid ASCII STL triangle")
            x=np.asarray(vertices);n=np.cross(x[1]-x[0],x[2]-x[0]);length=np.linalg.norm(n)
            if length<=0 or not np.isfinite(x).all():raise ValueError("Degenerate/nonfinite STL triangle")
            rows.append({"file":Path(path).name,"solid":solid,"solidTriangle":local,"points":x,"normal":n/length})
            vertices=[];local+=1
    if not rows:raise ValueError("No ASCII STL facets")
    return rows

def closest_point(q,t):
    a,b,c=t;ab=b-a;ac=c-a;ap=q-a;d1=ab@ap;d2=ac@ap
    if d1<=0 and d2<=0:return a
    bp=q-b;d3=ab@bp;d4=ac@bp
    if d3>=0 and d4<=d3:return b
    vc=d1*d4-d3*d2
    if vc<=0 and d1>=0 and d3<=0:return a+d1/(d1-d3)*ab
    cp=q-c;d5=ab@cp;d6=ac@cp
    if d6>=0 and d5<=d6:return c
    vb=d5*d2-d1*d6
    if vb<=0 and d2>=0 and d6<=0:return a+d2/(d2-d6)*ac
    va=d3*d6-d5*d4
    if va<=0 and d4-d3>=0 and d5-d6>=0:return b+(d4-d3)/((d4-d3)+(d5-d6))*(c-b)
    inv=1/(va+vb+vc);return a+vb*inv*ab+vc*inv*ac

def face_audit(face,rank,triangles,carve_tol):
    patch=face["writtenPatch"]
    if patch is None:
        return {"rank":rank,"nativeFace":face["nativeFace"],"writtenFace":face["writtenFace"],
                "sourceBinding":"unavailable: internal mesh face has no source patch"}
    mine=[t for t in triangles if t["solid"]==patch["name"]]
    if not mine:raise ValueError("Source patch is absent from retained ASCII STL")
    x=np.asarray(face["points"],dtype=float);vertices=[]
    for q in x:
        hits=[]
        for tri in mine:
            foot=closest_point(q,tri["points"]);distance=float(np.linalg.norm(q-foot))
            hits.append((distance,tri,foot))
        distance,tri,foot=min(hits,key=lambda h:h[0])
        vertices.append({"point":q.tolist(),"nearestSamePatchTriangle":tri["solidTriangle"],"sourceFile":tri["file"],
            "sourceTriangleNormal":tri["normal"].tolist(),"closestSourcePoint":foot.tolist(),
            "pointToRawSourceTriangleDistanceM":distance,
            "signedSourcePlaneDistanceM":float((q-tri["points"][0])@tri["normal"]),
            "withinConfiguredCarveONBand":distance<=carve_tol})
    # The cap routine uses a Newell normal and arithmetic vertex centroid.
    area=np.zeros(3)
    for j in range(len(x)):area+=np.cross(x[j],x[(j+1)%len(x)])/2
    centre=x.mean(axis=0);mag=float(np.linalg.norm(area))
    off=float(np.max(np.abs((x-centre)@(area/mag))))
    gate=max(.001*np.sqrt(mag),carve_tol)
    planes=[]
    for tri in mine:
        distances=(x-tri["points"][0])@tri["normal"]
        planes.append({"sourceFile":tri["file"],"solidTriangle":tri["solidTriangle"],"normal":tri["normal"].tolist(),
            "maximumVertexDistanceToInfinitePlaneM":float(np.max(np.abs(distances))),
            "signedVertexPlaneDistancesM":distances.tolist()})
    planes.sort(key=lambda d:d["maximumVertexDistanceToInfinitePlaneM"])
    return {"rank":rank,"nativeFace":face["nativeFace"],"writtenFace":face["writtenFace"],"patch":patch,
       "sourceTriangleCount":len(mine),"sourceBinding":"raw exported ASCII STL, before native surface canonicalization/weld",
       "vertices":vertices,"bestInfiniteSourcePlanes":planes[:2],
       "capRoutineReplay":{"NewellAreaM2":mag,"arithmeticCentre":centre.tolist(),"maximumPlaneDeviationM":off,
              "planarGateM":gate,"planarWithCarveONTolerance":off<=gate},
       "allVerticesWithinRawSTLPatchONBand":all(v["withinConfiguredCarveONBand"] for v in vertices)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--written",type=Path,required=True);p.add_argument("--mapped",type=Path,required=True)
    p.add_argument("--stl-dir",type=Path,required=True);p.add_argument("--dict",type=Path,required=True)
    p.add_argument("--native-log",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();w=json.loads(a.written.read_text());mapped=json.loads(a.mapped.read_text())["results"]["matches"]
    dictionary=a.dict.read_text()
    axes=[np.fromstring(re.search(r"\b"+ax+r"\s*\(([^)]+)\)",dictionary).group(1),sep=" ") for ax in "xyz"]
    spacings=sorted(np.concatenate([np.diff(v) for v in axes]).tolist())
    rawlog=a.native_log.read_text(errors="replace")
    # Native message prints the configured surface max level (actual original run used level 3).
    match=re.search(r"Surfaces:.*maxLevel\s+(\d+)",rawlog)
    if match is None:raise ValueError("Native maxLevel not recorded")
    level=int(match.group(1));finest=spacings[len(spacings)//2]/(1<<level)
    override=re.search(r"\bonToleranceCarveFraction\s+([\deE+.\-]+)\s*;",dictionary)
    fraction=float(override.group(1)) if override else .01
    carve_tol=finest*fraction
    stls=sorted(a.stl_dir.glob("*.stl"));triangles=[t for f in stls for t in read_ascii_stl(f)]
    bad={(row["rank"],f["writtenFace"]):f for row in w["ranks"] for f in row["badFaces"]}
    out=[]
    for face in mapped:
        item=dict(face);item["points"]=bad[(face["rank"],face["writtenFace"])]["points"]
        out.append(face_audit(item,face["rank"],triangles,carve_tol))
    result={"format":"rejected-cone-source-stl-audit/v1","scope":"Read-only raw-source geometry diagnosis. No native execution/source/point changes or product acceptance.",
        "sourceSha256":sha(Path(__file__)),"geometryHelperSha256":sha(Path(__file__).with_name("audit_air_mesh_geometry.py")),
        "inputFilesSha256":{"written":sha(a.written),"mapped":sha(a.mapped),"dict":sha(a.dict),"nativeLog":sha(a.native_log)},
        "STLfilesSha256":{f.name:sha(f) for f in stls},
        "configuredTolerance":{"medianBackgroundSpacingM":spacings[len(spacings)//2],"maxSurfaceLevel":level,
             "finestM":finest,"carveONFraction":fraction,"carveONM":carve_tol,
             "limitations":"Native actual tree deepestLevel can differ from configured maxLevel; this original run reports finest≈0.0316667 and level3. Raw STL remains before native surface snaps/welds."},
        "faces":out,"limits":["Nearest raw STL triangle and an infinite source plane are different quantities; a nearest infinite plane need not cover the point.",
             "Existing final coordinates alone cannot prove the first stage introducing deviation; native stage witnesses are required.",
             "No local projection is authorized: shared vertices and physical corners require globally compatible clipping/constraints.",
             "Source STL is parsed as decimal float64 and not substituted for the native processed surface coordinates."]}
    a.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"faces":len(out),"internalWithoutSTLBinding":sum("patch" not in f for f in out),"carveONM":carve_tol}))
if __name__=="__main__":main()
