#!/usr/bin/env python3
"""Offline whole-parent AP candidate optimization on a frozen complete stencil.

Geometry-derived seeds and an epigraph objective maximize the worst actual
selected-SF determinant. No fixed source point is moved. Search success is never
native acceptance: every retained proposal is rechecked by the complete existing
geometry, conservation and five-metric Pareto evaluator.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy.optimize import minimize,linprog
import audit_air_cone_quality_kernel as k
import audit_air_cone_nonorth_kernel as n

LIMITS={"D":.001,"weight":.05,"ratio":.01,"skew":4.,"nonorth":70.}
COUPLED={"cyclic","processor","processorCyclic"}

class FastStencil:
    """Same floating geometry equations, omitting costly final tet/edge reports."""
    def __init__(self,doc):
        self.doc=doc;self.children=doc["children"];self.faces=doc["affectedFaces"]
        self.childset=set(self.children);self.ci={c:i for i,c in enumerate(self.children)}
        self.fi={f:i for i,f in enumerate(self.faces)}
        self.point={int(v):np.asarray(p) for v,p in doc["points"].items()}
        self.apid=doc["apexWrittenPoint"]
        self.geometry_module=k._load("audit_air_cone_candidate_independent.py")
        self.direct=self.geometry_module.Stencil(doc,doc["originalApex"])
        self.polygons=[];self.ap_positions=[];self.old_area=[];self.old_centre=[]
        self.triangles=[];self.nontri=[]
        for f in self.faces:
            row=doc["faces"][str(f)];patch=row["patch"]
            if row["neighbour"] is None and patch and any(x in patch["type"].lower() for x in ("processor","cyclic","ami","mapped")):
                raise ValueError("coupled outside state is unavailable")
            ids=row["pointIDs"];p=np.asarray([self.point[v] for v in ids])
            self.polygons.append(p);self.ap_positions.append(ids.index(self.apid) if self.apid in ids else -1)
            sf,fc=self.direct.face(f);self.old_area.append(sf);self.old_centre.append(fc)
            if self.apid in ids:
                (self.triangles if len(ids)==3 else self.nontri).append(self.fi[f])
        self.old_area=np.asarray(self.old_area);self.old_centre=np.asarray(self.old_centre)
        self.fixed_outside={c:self.direct.cell(c) for c in doc["outsideCells"]}
        self.child_faces=[];self.child_sign=[];self.selected=[]
        for c in self.children:
            fs=doc["cells"][str(c)]["faces"]
            self.child_faces.append(np.asarray([self.fi[f] for f in fs]))
            self.child_sign.append(np.asarray([1. if doc["faces"][str(f)]["owner"]==c else -1. for f in fs]))
            self.selected.append(np.asarray([i for i,f in enumerate(fs) if doc["faces"][str(f)]["patch"] is None
                 or doc["faces"][str(f)]["patch"]["type"] in COUPLED],int))
        self.normals=np.asarray([p["normal"] for p in doc["sourceKernel"]["originalPlanes"]])
        self.plane_centres=np.asarray([p["centre"] for p in doc["sourceKernel"]["originalPlanes"]])

    def evaluate(self,apex):
        apex=np.asarray(apex,dtype=float);areas=self.old_area.copy();centres=self.old_centre.copy()
        polygons=list(self.polygons)
        if self.triangles:
            xyz=np.asarray([self.polygons[i].copy() for i in self.triangles])
            for j,i in enumerate(self.triangles):xyz[j,self.ap_positions[i]]=apex
            areas[self.triangles]=.5*np.cross(xyz[:,1]-xyz[:,0],xyz[:,2]-xyz[:,0])
            centres[self.triangles]=xyz.sum(axis=1)/3.
            for j,i in enumerate(self.triangles):polygons[i]=xyz[j]
        for i in self.nontri:
            xyz=self.polygons[i].copy();xyz[self.ap_positions[i]]=apex
            geometry=self.geometry_module.face_geometry(xyz)
            areas[i],centres[i]=geometry;polygons[i]=xyz
        cc=[];volume=[];det=[]
        for ids,sign,selected in zip(self.child_faces,self.child_sign,self.selected):
            a=areas[ids]*sign[:,None];fc=centres[ids];estimate=fc.mean(axis=0)
            pv=np.einsum("ij,ij->i",a,fc-estimate);total=float(pv.sum())
            c=(pv[:,None]*(.75*fc+.25*estimate)).sum(axis=0)/total
            V=total/3.;sf=a[selected];avg=np.linalg.norm(sf,axis=1).mean()
            D=float(abs(np.linalg.det((sf/avg).T@(sf/avg)))/8)
            cc.append(c);volume.append(V);det.append(D)
        cc=np.asarray(cc);volume=np.asarray(volume)
        weight=[];ratio=[];skew=[];angles=[];face_ids=[];internal_ids=[]
        for i,f in enumerate(self.faces):
            row=self.doc["faces"][str(f)];o=row["owner"];nei=row["neighbour"]
            oc=cc[self.ci[o]] if o in self.ci else self.fixed_outside[o][2]
            ov=volume[self.ci[o]] if o in self.ci else self.fixed_outside[o][3]
            sf=areas[i];fc=centres[i];normal=sf/np.linalg.norm(sf)
            if nei is None:d=normal*np.dot(normal,fc-oc);factor=.4
            else:
                nc=cc[self.ci[nei]] if nei in self.ci else self.fixed_outside[nei][2]
                nv=volume[self.ci[nei]] if nei in self.ci else self.fixed_outside[nei][3]
                d=nc-oc;factor=.2
                do=abs(float(np.dot(sf,fc-oc)));dn=abs(float(np.dot(sf,nc-fc)))
                weight.append(min(do,dn)/(do+dn+1e-300));ratio.append(min(ov,nv)/(max(ov,nv)+1e-300))
                angles.append(float(np.degrees(np.arccos(np.clip(np.dot(normal,d)/np.linalg.norm(d),-1,1)))))
                internal_ids.append(f)
            cpf=fc-oc;sv=cpf-np.dot(sf,cpf)/(np.dot(sf,d)+1e-150)*d
            svhat=sv/(np.linalg.norm(sv)+1e-150)
            fd=max(factor*np.linalg.norm(d)+1e-150,float(abs((polygons[i]-fc)@svhat).max()))
            skew.append(float(np.linalg.norm(sv)/fd));face_ids.append(f)
        return {"D":np.asarray(det),"CC":cc,"V":volume,"weight":np.asarray(weight),
                "ratio":np.asarray(ratio),"skew":np.asarray(skew),"nonorth":np.asarray(angles),
                "faceIDs":face_ids,"internalFaceIDs":internal_ids,
                "kernelMargins":np.einsum("ij,ij->i",self.plane_centres-apex,self.normals)}

def preservation_audit(doc,apex):
    """Recompute fixed parent boundary, complete outside cells and parent moments.

    This is local original-stencil preservation, not global point clearance or a
    coupled-patch validation. Unsupported coupled affected faces are refused by
    FastStencil before this audit is reached.
    """
    geometry=k._load("audit_air_cone_candidate_independent.py")
    old=geometry.Stencil(doc,doc["originalApex"]);new=geometry.Stencil(doc,apex)
    children=set(doc["children"]);fixed=[];area_errors=[];centre_errors=[]
    groups={}
    for f in doc["affectedFaces"]:
        row=doc["faces"][str(f)]
        if doc["apexWrittenPoint"] in row["pointIDs"]:continue
        old_sf,old_fc=old.face(f);new_sf,new_fc=new.face(f)
        fixed.append(f)
        area_errors.append(float(np.linalg.norm(new_sf-old_sf)))
        centre_errors.append(float(np.linalg.norm(new_fc-old_fc)))
        sign=1 if row["owner"] in children else -1
        base=row["sourceBase"]
        if base>=0:
            group=groups.setdefault(base,{"faces":[],"oldArea":np.zeros(3),"newArea":np.zeros(3),
                "oldAreaAmount":0.,"newAreaAmount":0.,"oldAreaFirstMoment":np.zeros(3),"newAreaFirstMoment":np.zeros(3)})
            group["faces"].append(f)
            for key,sf,fc in [("old",old_sf,old_fc),("new",new_sf,new_fc)]:
                amount=float(np.linalg.norm(sf))
                group[key+"Area"]+=sign*sf;group[key+"AreaAmount"]+=amount
                group[key+"AreaFirstMoment"]+=amount*fc
    outside=[]
    for c in doc["outsideCells"]:
        _,_,old_cc,old_v=old.cell(c);_,_,new_cc,new_v=new.cell(c)
        outside.append({"cell":c,"centreExactlyUnchanged":bool(np.array_equal(old_cc,new_cc)),
                        "volumeExactlyUnchanged":bool(old_v==new_v),
                        "centreDifferenceM":float(np.linalg.norm(new_cc-old_cc)),
                        "volumeDifferenceM3":float(abs(new_v-old_v))})
    parent=[]
    for stencil in (old,new):
        values=[stencil.cell(c) for c in doc["children"]]
        volumes=np.asarray([v[3] for v in values]);centres=np.asarray([v[2] for v in values])
        parent.append((float(volumes.sum()),(volumes[:,None]*centres).sum(axis=0)))
    group_rows=[]
    for source_face,g in sorted(groups.items()):
        group_rows.append({"sourceFace":source_face,"writtenPieces":g["faces"],
            "areaVectorDifferenceM2":float(np.linalg.norm(g["newArea"]-g["oldArea"])),
            "areaAmountDifferenceM2":float(abs(g["newAreaAmount"]-g["oldAreaAmount"])),
            "areaFirstMomentDifferenceM3":float(np.linalg.norm(g["newAreaFirstMoment"]-g["oldAreaFirstMoment"]))})
    return {"scope":"One new internal AP only. Frozen original-parent boundary and complete same-rank outside cells; no coupled remote state or full native quality authorization.",
        "fixedPointCoordinatesExactlyUnchanged":bool(all(np.array_equal(old.points[int(v)],new.points[int(v)])
             for v in doc["points"] if int(v)!=doc["apexWrittenPoint"])),
        "apexIncidenceProof":doc["apexIncidenceProof"],
        "fixedBoundaryFaces":len(fixed),"maximumFixedFaceAreaDifferenceM2":max(area_errors,default=0.),
        "maximumFixedFaceCentreDifferenceM":max(centre_errors,default=0.),
        "sourceBoundaryPieceGroups":group_rows,"outsideCells":outside,
        "allOutsideCentresAndVolumesExactlyUnchanged":all(v["centreExactlyUnchanged"] and v["volumeExactlyUnchanged"] for v in outside),
        "originalSumChildVolumeM3":parent[0][0],"candidateSumChildVolumeM3":parent[1][0],
        "relativeParentVolumeDifference":abs(parent[1][0]-parent[0][0])/abs(parent[0][0]),
        "parentFirstMomentDifferenceM4":float(np.linalg.norm(parent[1][1]-parent[0][1]))}

def seed_candidates(doc):
    coords=np.asarray([v for p in doc["sourceKernel"]["originalPlanes"] for v in p["coordinates"]])
    lo=coords.min(axis=0);hi=coords.max(axis=0)
    candidates=[("actualWrittenAP",np.asarray(doc["originalApex"])),
                ("originalSourceCentroid",np.asarray(doc["sourceKernel"]["sourceApex"])),
                ]
    try:
        klo,khi=n.axis_box(doc)
        candidates.append(("originalPlaneKernelBoxCentre",(klo+khi)/2.))
    except ValueError:
        normals=np.asarray([p["normal"] for p in doc["sourceKernel"]["originalPlanes"]])
        offsets=np.asarray([np.dot(p["normal"],p["centre"]) for p in doc["sourceKernel"]["originalPlanes"]])
        solved=linprog([0,0,0,-1],A_ub=np.c_[normals,np.ones(len(normals))],b_ub=offsets,
                       bounds=[tuple(x) for x in zip(lo,hi)]+[(None,None)],method="highs")
        if solved.success:candidates.append(("originalPlaneChebyshevCentre",solved.x[:3]))
    try:
        model=n.ProposalModel(doc,70-1e-5);lp_point,lp=model.lp_candidate()
        if lp_point is not None:candidates.append(("WRNmaximumSlack",np.asarray(lp_point)))
    except ValueError as exc:
        lp={"success":False,"unsupported":str(exc),"scope":"Candidate seed construction only, not an exclusion certificate."}
    unique=[];seen=set()
    for name,point in candidates:
        key=tuple(point)
        if key not in seen:seen.add(key);unique.append((name,point))
    return lo,hi,unique,lp

def floors(old):
    out={}
    for metric in ("D","weight","ratio"):
        values=old[metric];threshold=LIMITS[metric]
        out[metric]=np.where(values>=threshold,threshold,min(threshold,float(values.min())))
    for metric in ("skew","nonorth"):
        values=old[metric];threshold=LIMITS[metric]
        out[metric]=np.where(values<=threshold,threshold,max(threshold,float(values.max())))
    return out

def optimize(doc,maxiter=150):
    started=time.monotonic();fast=FastStencil(doc);origin=np.asarray(doc["originalApex"])
    old_fast=fast.evaluate(origin);old=k.actual_stencil_quality(doc,origin)
    lo,hi,seeds,lp=seed_candidates(doc);span=hi-lo
    if np.any(span<=0) or not np.isfinite(span).all():raise ValueError("source bounds must have positive finite span")
    floor=floors(old_fast)
    absolute_wrn=bool(lp.get("success") and lp.get("minimumRowValue",-1)>=0)
    if absolute_wrn:
        for metric in ("weight","ratio","nonorth"):floor[metric]=np.full_like(floor[metric],LIMITS[metric])
    seen={};count=0
    def evaluate(u):
        nonlocal count
        key=tuple(np.asarray(u)[:3])
        if key not in seen:
            seen[key]=fast.evaluate(lo+span*np.asarray(u)[:3]);count+=1
        return seen[key]
    def constraints(x):
        q=evaluate(x);items=[(q["D"]/.001-x[3]),
          (q["D"]-floor["D"])/.001,
          (q["weight"]-floor["weight"])/.05,
          (q["ratio"]-floor["ratio"])/.01,
          (floor["skew"]-q["skew"])/4.,
          (floor["nonorth"]-q["nonorth"])/70.,
          (q["kernelMargins"]-3e-6)/float(span.max()),
          q["V"]/old_fast["V"]]
        out=np.concatenate(items)
        return np.nan_to_num(out,nan=-1e6,posinf=-1e6,neginf=-1e6)
    seeds_out=[];rejected_seeds=[];solvers=[];points=[]
    old_t=float(old_fast["D"].min()/.001)
    for name,point in seeds:
        u=(point-lo)/span
        margin=float(np.einsum("ij,ij->i",fast.plane_centres-point,fast.normals).min())
        if margin<=3e-6:
            rejected_seeds.append({"derivation":name,"apex":point.tolist(),"minimumOriginalPlaneMarginM":margin})
            continue
        seeds_out.append({"derivation":name,"apex":point.tolist(),"normalized":u.tolist()})
        q=fast.evaluate(point);t=max(0.,min(float(q["D"].min()/.001),old_t))*.999
        result=minimize(lambda x:-x[3],np.r_[u,t],method="SLSQP",bounds=[(0,1)]*3+[(0,10)],
                        constraints={"type":"ineq","fun":constraints},
                        options={"maxiter":maxiter,"ftol":1e-10,"disp":False})
        point=lo+span*result.x[:3];res=constraints(result.x)
        solvers.append({"seed":name,"success":bool(result.success),"status":int(result.status),
                        "message":str(result.message),"iterations":int(result.nit),
                        "epigraphValue":float(result.x[3]),"minimumConstraint":float(res.min()),
                        "apex":point.tolist(),"candidateOnly":True})
        points.append((name,point))
    blended=list(points)
    for name,point in points:
        for fraction in (.25,.5,.75):
            blended.append((name+":blendFromActual:"+str(fraction),origin+fraction*(point-origin)))
    results=[]
    for name,point in blended:
        try:
            row=n.evaluate(doc,old,point);row["derivation"]="boundedWholeParentSLSQP:"+name
            results.append(row)
        except (ValueError,ZeroDivisionError,FloatingPointError) as exc:
            results.append({"apex":point.tolist(),"derivation":name,"unsupported":str(exc)})
    valid=[x for x in results if x.get("passesLocalGeometry") and x.get("passesLocalFiveMetricPareto")
           and x["actual"]["minimumDeterminant"]>old["minimumDeterminant"]*(1+1e-8)]
    valid.sort(key=lambda x:(-x["actual"]["minimumDeterminant"],-x["normalizedWorstScore"],tuple(x["apex"])))
    return {"schema":"geometry-seeded-whole-parent-apex-optimization/v1","rank":doc["rank"],"parent":doc["parent"],
            "scope":"Pure bounded offline search for one new internal AP. Full original children/exterior stencil retained; no native/checker/physics acceptance.",
            "children":len(doc["children"]),"affectedFaces":len(doc["affectedFaces"]),"outsideCompleteCells":len(doc["outsideCells"]),
            "normalizedSourceBounds":{"lo":lo.tolist(),"hi":hi.tolist(),"scale":span.tolist()},
            "searchMethod":"SLSQP four variables: normalized AP and worst determinant epigraph. Actual D/W/R/skew/N preserve every old passing role and every original worst failed severity; all original planes margin>3um.",
            "maxIterationsPerSeed":maxiter,"seeds":seeds_out,"rejectedOutsideStrictKernelSeeds":rejected_seeds,
            "postSolverBlendFractionsFromOriginal":[.25,.5,.75],
            "minimumRelativeDeterminantImprovement":1e-8,
            "absoluteWRNProposalLP":lp,
            "searchConstraintMode":"absolute W/R/N" if absolute_wrn else "original per-role five-metric Pareto floors",
            "originalActual":old,"originalFast":{key:value.tolist() if isinstance(value,np.ndarray) else value for key,value in old_fast.items()},
            "solvers":solvers,"actualCandidateEvaluations":results,"bestGeometryAndParetoCandidate":valid[0] if valid else None,
            "bestCandidatePreservationAudit":preservation_audit(doc,valid[0]["apex"]) if valid else None,
            "hasWholeLocalFiveMetricPassingCandidate":any(x.get("allLocalFiveMetricsPass") for x in valid),
            "fastUniqueEvaluations":count,"seconds":time.monotonic()-started}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--maxiter",type=int,default=150)
    args=p.parse_args();doc=json.loads(args.input.read_text());out=optimize(doc,args.maxiter)
    out["inputIdentity"]={"path":str(args.input),"bytes":args.input.stat().st_size,"sha256":hashlib.sha256(args.input.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in out.items() if k not in ("originalActual","originalFast","actualCandidateEvaluations","bestGeometryAndParetoCandidate")},indent=2))

if __name__=="__main__":main()
