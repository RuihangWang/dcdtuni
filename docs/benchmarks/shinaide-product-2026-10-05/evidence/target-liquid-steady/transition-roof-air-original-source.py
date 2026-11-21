#!/usr/bin/env python3
"""Analytic, conservative transition-shaping probe; not a product mesh pass.

One coarse cube shares its face with four half-size cubes. Their shared
rectangular facets remain planar under an additive quadratic displacement.
No boundary vertices or topology are changed in a product by this probe.
"""
import argparse, hashlib, importlib.util, json
from pathlib import Path
import numpy as np

def evaluate(audit_module, epsilon):
    spec=importlib.util.spec_from_file_location("roof_geometry_audit",audit_module)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    points=[];lookup={}
    def vertex(x,y,z):
        # All five cells use exactly the same shared position.
        if x==1.:x+=epsilon*(2*y*(1-y)+2*z*(1-z))
        key=(x,y,z)
        if key not in lookup:lookup[key]=len(points);points.append(key)
        return lookup[key]
    def polygon(coords):return [vertex(*p) for p in coords]
    def box(x0,x1,y0,y1,z0,z1):
        return [polygon(p) for p in [
            [(x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0)],
            [(x1,y0,z0),(x1,y1,z0),(x1,y1,z1),(x1,y0,z1)],
            [(x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1)],
            [(x0,y1,z0),(x0,y1,z1),(x1,y1,z1),(x1,y1,z0)],
            [(x0,y0,z0),(x0,y1,z0),(x1,y1,z0),(x1,y0,z0)],
            [(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]]]
    coarse=box(0.,1.,0.,1.,0.,1.)
    # Insert the shared boundary mid-edge points into the four coarse sides.
    coarse[2]=polygon([(0,0,0),(1,0,0),(1,0,.5),(1,0,1),(0,0,1)])
    coarse[3]=polygon([(0,1,0),(0,1,1),(1,1,1),(1,1,.5),(1,1,0)])
    coarse[4]=polygon([(0,0,0),(0,1,0),(1,1,0),(1,.5,0),(1,0,0)])
    coarse[5]=polygon([(0,0,1),(1,0,1),(1,.5,1),(1,1,1),(0,1,1)])
    coarse.pop(1)
    fine=[];pairs=[]
    for y in (0.,.5):
        for z in (0.,.5):
            cell=box(1.,1.5,y,y+.5,z,z+.5)
            coarse.append(list(reversed(cell[0])));fine.append(cell)
            pairs.append((len(coarse)-1,0))
    meshes=[module.Geometry.from_arrays(points,faces) for faces in [coarse]+fine]
    reports=[]
    for mesh in meshes:
        report=mesh.classify(0,detail=True)
        cc=mesh.cell(0)[3]
        minimum_centroid_fan=1e100;best_vertex_fan=[]
        for f in range(mesh.n_faces if hasattr(mesh,"n_faces") else len(mesh.owner)):
            ids=mesh.ids(f);xyz=mesh.points[ids];fc=mesh.face(f)[1]
            fan=[]
            for q,a in enumerate(xyz):
                b=xyz[(q+1)%len(xyz)]
                fan.append(float(np.dot(np.cross(b-a,fc-a),cc-a))/-6.)
            minimum_centroid_fan=min(minimum_centroid_fan,min(fan))
            anchors=[]
            for q,anchor in enumerate(xyz):
                vols=[]
                for t in range(1,len(xyz)-1):
                    a=xyz[(q+t)%len(xyz)];b=xyz[(q+t+1)%len(xyz)]
                    vols.append(float(np.dot(np.cross(a-anchor,b-anchor),cc-anchor))/-6.)
                anchors.append(min(vols))
            best_vertex_fan.append(max(anchors))
        report["minimumPositiveCentroidFanTetVolume"]=minimum_centroid_fan
        report["minimumBestOriginalVertexFanTetVolume"]=min(best_vertex_fan)
        reports.append(report)
    # Coarse-to-fine interface metrics, using the installed checker formulas.
    interface=[]
    for k,(cf,ff) in enumerate(pairs):
        a,fc,*_=meshes[0].face(cf);nc=meshes[k+1].cell(0)[3];oc=meshes[0].cell(0)[3]
        area=np.linalg.norm(a);d0=float(np.dot(a,fc-oc))/area;d1=float(np.dot(a,nc-fc))/area
        vol0=meshes[0].cell(0)[4];vol1=meshes[k+1].cell(0)[4]
        interface.append({"weight":min(d0,d1)/(d0+d1),"volumeRatio":min(vol0,vol1)/max(vol0,vol1),
            "sameSharedVertices":set(coarse[cf])==set(fine[k][ff])})
    total=sum(p["volumeM3"] for p in reports)
    return {"epsilonMetres":epsilon,"auditSourceSha256":hashlib.sha256(Path(audit_module).read_bytes()).hexdigest(),
        "cells":reports,"interfaces":interface,"totalVolumeM3":total,"expectedVolumeM3":1.5,
        "relativeVolumeError":abs(total-1.5)/1.5,"scope":"analytic five-cell fixture; no actual checkMesh or native product acceptance"}

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--audit-module",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    original=evaluate(args.audit_module,0.);shaped=evaluate(args.audit_module,5e-4)
    assert original["cells"][0]["checkerTriggerCounts"]["coplanar"]>0
    assert all(c["classification"]=="convex-no-face-plane-trigger" for c in shaped["cells"])
    assert all(c["minimumPositiveCentroidFanTetVolume"]>0 and c["minimumBestOriginalVertexFanTetVolume"]>0 for c in shaped["cells"])
    assert shaped["relativeVolumeError"]<1e-12
    assert all(f["sameSharedVertices"] and f["weight"]>.05 and f["volumeRatio"]>.01 for f in shaped["interfaces"])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"original":original,"shaped":shaped,"probeAssertionsPassed":True},indent=2)+"\n")
    print("Analytic roof probe assertions passed; no product quality claim")
if __name__=="__main__":main()
