#!/usr/bin/env python3
"""Affine rectangular extension of the exact buffered roof; diagnostic only."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_air_transition_roof as roof
from audit_air_mesh_geometry import Geometry,sha

def affine(base,widths,axis=0,sign=1):
    widths=np.asarray(widths,float)
    if widths.shape!=(3,) or not np.isfinite(widths).all() or np.min(widths)<=1e-6:
        raise ValueError("finite positive rectangular extents >1micrometre required")
    if int(axis) not in range(3) or sign not in (-1,1):
        raise ValueError("invalid normal axis/sign")
    # Two sign flips form a proper rotation; winding remains outward.
    pts=base.points*np.array([sign,sign,1])
    pts=np.roll(pts,int(axis),axis=1)*widths
    g=Geometry.from_arrays(pts,[base.ids(f).tolist() for f in range(base.n_faces)])
    for name in ["owner","neighbour","cell_faces","cell_offsets"]:
        setattr(g,name,getattr(base,name).copy())
    g.n_cells=base.n_cells;g.n_faces=base.n_faces
    return g

def audit(base,widths,axis=0,sign=1):
    g=affine(base,widths,axis,sign)
    row=roof.audit_assembled(g,5e-4*widths[axis])
    determinants=[]
    for c in range(g.n_cells):
        areas=[g.face(int(f))[0] for f in g.cell_faces[g.cell_offsets[c]:g.cell_offsets[c+1]] if f<len(g.neighbour)]
        avg=sum(np.linalg.norm(a) for a in areas)/len(areas)
        tensor=sum(np.outer(a/avg,a/avg) for a in areas)
        determinants.append(float(abs(np.linalg.det(tensor))/8))
    row.update(widthsMetres=list(widths),normalAxis=axis,normalSign=sign,
       normalDisplacementMetres=5e-4*widths[axis],aspectConditionNumber=float(max(widths)/min(widths)),
       internalAreaTensorDeterminants=determinants,minimumInternalAreaTensorDeterminant=min(determinants),
       expectedTotalVolumeM3=22.5*float(np.prod(widths)))
    row["relativeTotalVolumeError"]=abs(row["totalVolumeM3"]-row["expectedTotalVolumeM3"])/row["expectedTotalVolumeM3"]
    row["fullPureMetricGatePassed"]=all(row[k] for k in [
       "allOppositeEdgeCyclesClosed","allFacesPlanar","allFacesConvex",
       "allCellsStrictlyInsideFaceCentreCriterion","allNonincidentVerticesStrictInside"]) and (
       row["minimumTetFanQuality"]>1e-15 and row["minimumInternalAreaTensorDeterminant"]>.001 and
       row["minimumInternalWeight"]>.05 and row["minimumInternalVolumeRatio"]>.01)
    return g,row

def write_fixture(g,base,path):
    roof.write_fixture(g,path)
    # Preserve the original 18coarse/36fine split under all rotations/scales.
    labels=[0 if base.cell(c)[3][0]<1 else 1 for c in range(base.n_cells)]
    header="FoamFile {version 2.0;format ascii;class labelList;object cellDecomp;}\n"
    (path/"constant/cellDecomp").write_text(header+str(len(labels))+"\n("+" ".join(map(str,labels))+")\n")

def main():
    p=argparse.ArgumentParser();p.add_argument("--tri-preflight",type=Path,required=True)
    p.add_argument("--liquid-preflight",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--fixture",type=Path);a=p.parse_args()
    airpath=Path(__file__).with_name("air_transition_roof_probe.py")
    air=roof.load_air(airpath);base,unit=roof.evaluate_buffer(air,Path(__file__).with_name("audit_air_mesh_geometry.py"),5e-4)
    samples={}
    tri=json.loads(a.tri_preflight.read_text());liquid=json.loads(a.liquid_preflight.read_text())
    samples["Tri-rank0-cell11"]=next(s["bboxWidths"] for s in tri["samples"] if s["cell"]==11)
    samples["Liquid-rank0-cell29"]=next(s["bboxWidths"] for s in liquid["samples"] if s["cell"]==29)
    actual=[]
    for name,widths in samples.items():
        for axis in range(3):
            for sign in [-1,1]:
                g,row=audit(base,widths,axis,sign);row["sourceSample"]=name;actual.append(row)
                if a.fixture and name=="Liquid-rank0-cell29" and axis==0 and sign==-1:
                    write_fixture(g,base,a.fixture)
    # Boundary samples support the proposed narrow kappa<=1.1 candidate gate.
    boundary=[]
    for widths in [[1.1,1,1],[1,1.1,1],[1,1,1.1],[1.1,1.1,1],[1.1,1,1.1],[1,1.1,1.1]]:
        _,row=audit(base,widths);boundary.append({k:v for k,v in row.items() if k not in ["cells","tetFans","interfaces"]})
    margin=-max(c["maximumOtherFaceCentreOutwardUnitDot"] for c in unit["cells"])
    out={"format":"air-rectangular-transition-roof-diagnostic/v1",
       "scope":"Affine extension of exact54cell buffered analytic roof. Actual candidate bbox dimensions are read from raw native binary preflights; candidate fine-neighbour/full-star eligibility is not asserted. Not a user-case mesh acceptance.",
       "sourceSha256":{str(p):sha(p) for p in [Path(__file__),Path(roof.__file__),airpath]},
       "preflightSha256":{str(p):sha(p) for p in [a.tri_preflight,a.liquid_preflight]},
       "actualWidthsMetres":samples,"actualSampleAffineAudits":actual,"aspectBoundarySamples":boundary,
       "affineProof":{"area":"Sf'=det(A) A^-T Sf; area closure and planarity preserved.",
         "centres":"All packet polygons are planar convex, so their area/centroid fan weights share one face scale: fc'=A fc. Signed pyramid volume and cell centre likewise give V'=det(A)V,cc'=A cc.",
         "convexity":"n' proportional A^-T n, so all vertex halfspace signs are preserved.",
         "strictFaceCentreDotBound":"For singular value ratio kappa, |dot(normalizedAd,normalizedA^-Tn)| >= |dot(normalizedd,n)|/kappa; signs unchanged.",
         "unitMinimumStrictFaceCentreInwardUnitDot":margin,"kappa1p1MinimumGuaranteedInwardDot":margin/1.1,
         "determinantLowerBound":"det(T')/det(T) >= kappa^-6 when normalized by mean internal/coupled face area.",
         "unitMinimumInternalAreaTensorDeterminant":unit["minimumInternalAreaTensorDeterminant"],
         "kappa1p1MinimumGuaranteedAreaDeterminant":unit["minimumInternalAreaTensorDeterminant"]/1.1**6,
         "weightsAndVolumeRatios":"Both face-normal distance integrals gain det(A); each volume gains det(A). Internal weight and adjacent volume ratio invariant.",
         "tet":"Every signed tet volume gains det(A)>0. Positive orientation is preserved; circumradius-normalized quality must be evaluated and final native checker still required."},
       "proposedRecognitionScope":"Finite extents>1micrometre, max/min<=1.1, exactly one regular rectangular coarse box +four half-extents neighbours and same normalized3x3 grid, eta=.0005 of normal extent. Independent protected-point/full-star/overlap and complete quality gates remain mandatory. Aspect scope is deliberately narrow, not unlimited rectangular support.",
       "limits":["Representative preflights do not prove any production packet qualifies; all physical/coupled/zone/full-star guards remain.",
         "This54cell fixture has a legal six-direction interior buffer. Moving a real vertex changes all incident faces and requires whole-star checks.",
         "Full checker diagnostic binds only saved fixture; no mesh acceptance receipt for usercases may be created."]}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__=="__main__":main()
