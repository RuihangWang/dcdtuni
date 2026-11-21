#!/usr/bin/env python3
"""Pure geometry-derived triangle ideal apex proposals, evaluated on frozen stencils."""
from __future__ import annotations
import argparse,importlib.util,json,math
from pathlib import Path
from collections import Counter
import numpy as np

def load(name):
    p=Path(__file__).with_name(name)
    s=importlib.util.spec_from_file_location(name.removesuffix(".py"),p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
B=load("audit_air_tetra_all_selected_bound.py")
Q=load("audit_air_cone_quality_kernel.py")
M=B.M

def ideal_triangle_apex(points,current_apex,selected_faces):
    """Global fixed-triangle D optimum; does not certify parent feasibility."""
    p=np.asarray(points,dtype=float);old=np.asarray(current_apex,dtype=float)
    if p.shape!=(3,3) or not np.isfinite(p).all() or not np.isfinite(old).all():
        raise ValueError("finite triangle and apex required")
    if selected_faces not in (3,4):raise ValueError("three side faces or all four selected required")
    sf=.5*np.cross(p[1]-p[0],p[2]-p[0]);A=float(np.linalg.norm(sf))
    if A<=0:raise ValueError("nondegenerate triangle required")
    lengths=np.array([np.linalg.norm(p[2]-p[1]),np.linalg.norm(p[0]-p[2]),np.linalg.norm(p[1]-p[0])])
    P=float(lengths.sum());incenter=(lengths[:,None]*p).sum(axis=0)/P
    normal=sf/A;oldheight=float(np.dot(normal,old-p[0]))
    if oldheight==0:raise ValueError("apex must choose a nonzero inward side")
    if oldheight<0:normal=-normal
    radius=2*A/P;height=math.sqrt(2 if selected_faces==3 else 8)*radius
    return {"triangleCoordinates":p.tolist(),"selectedFaces":selected_faces,"incenter":incenter.tolist(),
            "inradiusM":radius,"idealHeightM":height,"idealApex":(incenter+height*normal).tolist(),
            "normalTowardCurrentApex":normal.tolist(),"scope":"Only fixed-triangle OF determinant is optimized; no other child/kernel metric is assumed."}

def clipped_kernel_segment(start,target,planes,margin=3e-6):
    start=np.asarray(start,float);target=np.asarray(target,float);delta=target-start
    if margin<=0 or not np.isfinite(start).all() or not np.isfinite(target).all():raise ValueError("finite segment and positive margin required")
    max_t=1.;limiting=[]
    for plane in planes:
        normal=np.asarray(plane["normal"],float);centre=np.asarray(plane["centre"],float)
        if not np.isfinite(normal).all() or not np.isfinite(centre).all():raise ValueError("finite original plane required")
        length=float(np.linalg.norm(normal))
        if abs(length-1)>1e-10:raise ValueError("unit original plane normal required")
        before=float(np.dot(normal,centre-start));slope=float(np.dot(normal,delta))
        if before<=margin:raise ValueError("starting apex does not pass original strict kernel margin")
        if slope>0:
            bound=(before-margin)/slope
            if bound<max_t:max_t=max(0.,bound);limiting=[plane["sourceFace"]]
            elif bound==max_t:limiting.append(plane["sourceFace"])
    # Keep a small inward rounding margin; do not clamp coordinates after evaluation.
    clipped_t=max_t*(1-1e-10) if max_t<1 else 1.
    point=start+clipped_t*delta
    minimum=min(float(np.dot(np.asarray(p["normal"]),np.asarray(p["centre"])-point)) for p in planes)
    if minimum<margin*(1-1e-9):raise ValueError("numerical segment clip failed margin")
    return {"maximumSegmentFraction":clipped_t,"clippedTarget":point.tolist(),
            "limitingOriginalFaces":limiting,"clippedMinimumOriginalPlaneMarginM":minimum}

def geometry_guard(old,new,global_point_clearance):
    V=abs(new["sumVolume"]-old["sumVolume"])/old["sumVolume"]
    Mdiff=float(np.linalg.norm(np.asarray(new["firstMoment"])-old["firstMoment"]))/max(float(np.linalg.norm(old["firstMoment"])),old["sumVolume"],1e-300)
    return {"originalKernelMargin":new["minimumParentPlaneMarginM"]>3e-6,
      "allChildVolumesPositive":new["minimumChildVolume"]>0,
      "allCentroidPyramidsPositive":new["minimumPyramidVolume"]>0,
      "directedClosure":new["allEdgesClosed"] and new["maximumClosure"]<1e-10,
      "planarFaces":new["maximumNativePlanarityGateFraction"]<=1,
      "convexCellsAndFaces":new["maximumVertexOutsideM"]<1e-10 and new["minimumPolygonSupportM"]>-1e-10 and new["concavityPairTriggers"]==0,
      "positiveFaceAreas":new["minimumFaceAreaM2"]>0,
      "tetFansPass":new["minimumSharedBasePointTetQuality"]>=1e-15,
      "originalVolumePreserved":V<=1e-10,"firstMomentPreserved":Mdiff<=1e-10,
      "globalWrittenPointClearance":global_point_clearance>3e-6,
      "noUnsupportedCoupledFaces":not new["unsupportedCoupledFaces"],
      "relativeVolumeError":V,"relativeFirstMomentError":Mdiff,"globalWrittenPointClearanceM":global_point_clearance}

def proposal_analysis(doc,representative,all_written_points):
    old=Q.actual_stencil_quality(doc,doc["originalApex"])
    ideal=ideal_triangle_apex(representative["baseCoordinates"],doc["originalApex"],representative["selectedFaces"])
    clip=clipped_kernel_segment(doc["originalApex"],ideal["idealApex"],doc["sourceKernel"]["originalPlanes"])
    rows=[];start=np.asarray(doc["originalApex"]);target=np.asarray(clip["clippedTarget"])
    fixed=np.delete(all_written_points,doc["apexWrittenPoint"],axis=0)
    for t in [.25,.5,.75,1.]:
        point=start+t*(target-start)
        new=Q.actual_stencil_quality(doc,point);pareto=Q.actual_pareto(old,new)
        clearance=float(np.linalg.norm(fixed-point,axis=1).min())
        guards=geometry_guard(old,new,clearance)
        guards={k:bool(v) if isinstance(v,(bool,np.bool_)) else v for k,v in guards.items()}
        booleans=[v for v in guards.values() if isinstance(v,bool)]
        accepted=all(booleans) and all(p["noNewFailures"] and p["countDoesNotIncrease"] and p["worstFailedSeverityDoesNotIncrease"] for p in pareto.values())
        improved=any(len(p["candidateBad"])<len(p["oldBad"]) or p["candidateWorstFailedSeverity"]<p["oldWorstFailedSeverity"] for p in pareto.values())
        rows.append({"blend":t,"point":point.tolist(),"actual":new,"pareto":pareto,"geometryGuard":guards,
                     "wholeParentNoNewParetoAndGeometry":accepted,"strictImprovement":accepted and improved})
    return {"rank":doc["rank"],"parent":doc["parent"],"representative":representative,"ideal":ideal,"clip":clip,
            "baseline":old,"trials":rows,
            "scope":"Unchanged topology, original boundary coordinates and outside-cell coordinates. Actual all-child/incident-face metrics only; no full checkMesh/native acceptance."}

def representative_candidates(mesh,ranks,parents_per_rank=2,selected_face_count=None):
    selections={};identities={}
    excluded={652847,652920,652956,9836,9777,9792}
    for rank in ranks:
        geo=M.g.Geometry(mesh/f"processor{rank}/constant/polyMesh");trace=M.read_trace(mesh,rank)
        bad=M.g.cell_set(geo.poly_dir/"sets/underdeterminedCells")
        rows=[]
        for c in map(int,bad):
            qual=B.tetrahedron_qualification(geo,c)
            if qual is None:continue
            raw,selected,physical=qual;parent=int(trace["cellParent"][c])
            if selected_face_count is not None and len(selected)!=selected_face_count:continue
            if parent in excluded:continue
            bases=[int(f) for f in raw[0] if trace["faceBase"][f]>=0]
            if len(bases)!=1:continue
            f=bases[0]
            if len(physical)==1 and f!=physical[0]:continue
            bound=B.fixed_triangle_bound(geo.points[geo.ids(f)],len(selected))
            if bound["bound"]<.002:continue
            D=M.q.determinant([geo.face(ff)[0] for ff in selected])[0]
            rows.append({"rank":rank,"cell":c,"sourceParent":parent,"baseFace":f,"sourceBase":int(trace["faceBase"][f]),
                         "baseCoordinates":geo.points[geo.ids(f)].tolist(),"selectedFaces":len(selected),"actualWrittenD":D,"anyApexNecessaryBound":bound})
        chosen=[];seen=set()
        # Deterministic most-underresolved representative; no coordinate grid.
        for row in sorted(rows,key=lambda row:(row["actualWrittenD"],row["cell"])):
            if row["sourceParent"] in seen:continue
            try:
                doc=Q.extract_stencils(mesh,rank,[row["sourceParent"]])[0]
                if Q.actual_stencil_quality(doc,doc["originalApex"])["unsupportedCoupledFaces"]:continue
            except ValueError:continue
            chosen.append((row,doc));seen.add(row["sourceParent"])
            if len(chosen)>=parents_per_rank:break
        selections[rank]=chosen
        identities[str(rank)]={"underdeterminedSetSHA256":M.sha(geo.poly_dir/"sets/underdeterminedCells"),"trace":trace["identities"]}
        print(json.dumps({"rank":rank,"qualifiedCandidates":len(rows),"selectedParents":[a["sourceParent"] for a,b in chosen]}),flush=True)
    return selections,identities

def _json_numpy(value):
    if isinstance(value,np.generic):return value.item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--mesh",type=Path,required=True);p.add_argument("--ranks",default="1,3,4")
    p.add_argument("--parents-per-rank",type=int,default=2);p.add_argument("--selected-face-count",type=int,choices=[3,4]);p.add_argument("--output",type=Path,required=True);p.add_argument("--fixtures",type=Path,required=True)
    a=p.parse_args();selections,ids=representative_candidates(a.mesh,list(map(int,a.ranks.split(","))),a.parents_per_rank,a.selected_face_count)
    fixtures=[];results=[]
    for rank,chosen in selections.items():
        points=M.g.Geometry(a.mesh/f"processor{rank}/constant/polyMesh").points
        for rep,doc in chosen:
            fixtures.append({"representative":rep,"stencil":doc})
            try:row=proposal_analysis(doc,rep,points)
            except ValueError as err:row={"rank":rank,"parent":rep["sourceParent"],"unsupported":str(err)}
            results.append(row)
            print(json.dumps({"rank":rank,"parent":rep["sourceParent"],"acceptedImprovementBlends":[x["blend"] for x in row.get("trials",[]) if x["strictImprovement"]],"unsupported":row.get("unsupported")}),flush=True)
    a.fixtures.write_text(json.dumps(fixtures,indent=2)+"\n")
    a.output.write_text(json.dumps({"schema":"triangle-ideal-parent-proposal/v1","mesh":str(a.mesh),"identity":ids,"results":results,
        "scope":"Pure read-only mathematical proposal. No production CPP/native/API/browser/mesh changes; full checker remains mandatory."},indent=2,default=_json_numpy)+"\n")
if __name__=="__main__":main()
