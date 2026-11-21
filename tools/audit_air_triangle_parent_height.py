#!/usr/bin/env python3
"""Actual tetrahedral fixed-base parent-height necessary bounds, read-only."""
from __future__ import annotations
import argparse,importlib.util,json
from collections import Counter
from pathlib import Path
import numpy as np

def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
P=load("parent_height",Path(__file__).with_name("audit_air_pyramid_remaining_bound.py"))
B=P.B;M=P.M

def triangle_parent_bound(points,selected_faces,height_squared):
    if selected_faces not in (3,4):
        raise ValueError("only installed selected-three-side/all-four triangle branches")
    if len(points)!=3:raise ValueError("triangle required")
    return P.bounded_height_formula(points,selected_faces==3,height_squared)

def audit(mesh):
    mesh=Path(mesh);rows=[];certificates={};identity={};counts=Counter()
    for rank in range(8):
        geo=M.g.Geometry(mesh/f"processor{rank}/constant/polyMesh");t=M.read_trace(mesh,rank)
        nativepath=mesh/f"conforming-written-rank{rank}-point-native.i32"
        native=np.memmap(nativepath,mode="r",dtype="<i4")
        if len(native)!=len(geo.points):raise ValueError("current point map size")
        bad=M.g.cell_set(geo.poly_dir/"sets/underdeterminedCells")
        live=np.flatnonzero(t["faceAlive"]);labs=np.concatenate((t["owner"][live],t["neighbour"][live]))
        facs=np.tile(live,2);ok=(labs>=0)&(labs<t["sourceManifest"]["cells"])
        order=np.argsort(labs[ok],kind="stable")
        t["sourceCellFaces"]=facs[ok][order]
        t["sourceCellOffsets"]=np.r_[0,np.cumsum(np.bincount(labs[ok],minlength=t["sourceManifest"]["cells"]))]
        child_order=np.argsort(t["cellParent"],kind="stable")
        child_off=np.r_[0,np.cumsum(np.bincount(t["cellParent"],minlength=t["sourceManifest"]["cells"]))]
        aps={}
        for c in map(int,bad):
            counts["actualDCells"]+=1;q=B.tetrahedron_qualification(geo,c)
            if q is None:counts["nonTetOrUnsupportedSelectedMask"]+=1;continue
            raw,selected,physical=q;counts[f"selected{len(selected)}Tetrahedra"]+=1
            bases=[int(f) for f in raw[0] if t["faceBase"][f]>=0]
            if len(selected)==3:bases=physical
            if len(bases)!=1:counts["ambiguousOriginalBase"]+=1;continue
            f=bases[0];base=int(t["faceBase"][f]);parent=int(t["cellParent"][c])
            if parent not in aps:
                children=child_order[child_off[parent]:child_off[parent+1]].tolist()
                try:aps[parent]=(B.shared_apex_from_children([geo.cell(cc)[5] for cc in children],native,t["sourceManifest"]["points"]),children)
                except ValueError as ex:aps[parent]=(None,str(ex))
            ap,children=aps[parent]
            if ap is None:counts["unprovenInsertedParentApex"]+=1;continue
            if ap not in raw[5] or ap in geo.ids(f):raise ValueError("apex/base incidence mismatch")
            coords=geo.points[geo.ids(f)].tolist()
            ids=t["vertices"][t["offsets"][base]:t["offsets"][base+1]]
            record={"rank":rank,"cell":c,"sourceParent":parent,"sourceBase":base,
                    "sourceBaseArity":len(ids),"sourceBaseOriginalCoordinates":t["points"][ids].tolist(),
                    "writtenBaseFace":f,"writtenBaseCoordinates":coords,
                    "selectedFaces":selected,"selectedFaceCount":len(selected),
                    "writtenApexPoint":ap,"writtenNativePoint":int(native[ap]),
                    "actualWrittenApex":geo.points[ap].tolist(),
                    "actualWrittenDeterminant":M.q.determinant([geo.face(ff)[0] for ff in selected])[0]}
            try:
                record["anyApexNecessaryBound"]=B.fixed_triangle_bound(coords,len(selected))
                h=P.source_parent_height_bound(t,parent,coords,geo.points[ap],len(selected)==3)
                cert_key=f"{rank}:{parent}:{f}"
                certificates[cert_key]=h;record["sourceParentHeightCertificate"]=cert_key
                counts["evaluatedTriangles"]+=1
                counts["freeAnyApexNecessaryFails001"]+=record["anyApexNecessaryBound"]["necessaryFails001"]
                if h["sourceOnlyHeightCapCertified"]:
                    hb=h["parentKernelNecessaryBound"];record["parentKernelNecessaryBound"]=hb
                    counts["heightCapsCertified"]+=1
                    counts["heightBelowFreeOptimum"]+=hb["maximumHeightBelowUnconstrainedOptimum"]
                    counts["parentBoundNecessaryFails001"]+=hb["parentBoundNecessaryFails001"]
                    counts["newExclusionsBeyondFreeBound"]+=hb["parentBoundNecessaryFails001"] and not record["anyApexNecessaryBound"]["necessaryFails001"]
                else:counts["heightCapsUnsupported"]+=1
            except ValueError as ex:
                record["unsupportedExactHeightBound"]=str(ex);counts["unsupportedExactHeightBound"]+=1
            rows.append(record)
        identity[str(rank)]={"trace":t["identities"],"pointMapSHA256":M.sha(nativepath),
                             "written":{name:M.sha(geo.poly_dir/name) for name in ("points","faces","owner","neighbour","boundary")},
                             "actualDBadSetSHA256":M.sha(geo.poly_dir/"sets/underdeterminedCells")}
        print(json.dumps({"rank":rank,"counts":dict(counts)}),flush=True)
    excluded=[row for row in rows if row.get("parentKernelNecessaryBound",{}).get("parentBoundNecessaryFails001")]
    return {"schema":"actual-triangle-parent-height-necessary-bound/v1","mesh":str(mesh),
            "counts":dict(counts),"identity":identity,"rows":rows,"sourceHeightCertificates":certificates,
            "certifiedExclusions":excluded,
            "scope":"Fixed actual triangle with the actual selected-three/four face mask and unique new common apex. Original source plane/closed-convex proof is required for finite height. Necessary bounds do not establish full-parent/whole-case feasibility; no native/API/browser/CPP or coordinates changed."}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--mesh",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();d=audit(a.mesh);a.output.write_text(json.dumps(d,indent=2)+"\n")
    print(json.dumps(d["counts"]),flush=True)
if __name__=="__main__":main()
