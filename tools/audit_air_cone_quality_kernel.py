#!/usr/bin/env python3
"""Read-only linear quality kernels for fixed planar cone bases and one shared apex.

These constraints describe W/R on the positive-pyramid branch only. They do not
authorize a mesh, change its topology, or imply determinant/tet/skew acceptance.
"""
from __future__ import annotations
import argparse
from fractions import Fraction as F
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import numpy as np
from scipy.optimize import linprog

VSMALL=F.from_float(1e-300)
WEIGHT=F.from_float(.05)
RATIO=F.from_float(.01)

def rational(x):
    return x if isinstance(x,F) else F.from_float(float(x))

def vector(x):
    return tuple(rational(v) for v in x)

def add(a,b): return tuple(x+y for x,y in zip(a,b))
def sub(a,b): return tuple(x-y for x,y in zip(a,b))
def mul(a,t): return tuple(x*t for x in a)
def dot(a,b): return sum((x*y for x,y in zip(a,b)),F(0))
def cross(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])

def affine(coeff,constant=0):
    return (*vector(coeff),rational(constant))

def value(row,apex):
    return dot(row[:3],vector(apex))+row[3]

def scale(row,k): return tuple(k*x for x in row)
def combine(a,b,ka=1,kb=1): return tuple(ka*x+kb*y for x,y in zip(a,b))

def planar_polygon(points):
    """Exact binary-coordinate planar, convex, simple polygon centroid and area."""
    p=[vector(v) for v in points]
    if len(p)<3 or len(set(p))!=len(p):raise ValueError("distinct polygon nodes required")
    origin=p[0];tri=[cross(sub(p[i],origin),sub(p[i+1],origin)) for i in range(1,len(p)-1)]
    sf=mul(tuple(sum((v[j] for v in tri),F(0)) for j in range(3)),F(1,2))
    if sf==(0,0,0):raise ValueError("zero polygon area")
    if any(dot(sf,sub(v,origin))!=0 for v in p):raise ValueError("exact planar model required")
    # Exact edge halfplanes reject concavity and nonadjacent touch/overlap.
    n=len(p)
    for i in range(n):
        edge=sub(p[(i+1)%n],p[i])
        support=[dot(cross(edge,sub(v,p[i])),sf) for v in p]
        if min(support)<0:raise ValueError("convex base required")
        for j,v in enumerate(p):
            if j in (i,(i+1)%n):continue
            if support[j]==0 and 0<=dot(sub(v,p[i]),edge)<=dot(edge,edge):
                raise ValueError("polygon self-touch or overlapping boundary")
    axis=max(range(3),key=lambda j:abs(sf[j]))
    amount=sum((v[axis] for v in tri),F(0))
    centre=tuple(sum((v[axis]*(origin[j]+p[i+1][j]+p[i+2][j])/3 for i,v in enumerate(tri)),F(0))/amount for j in range(3))
    return sf,centre

def side_distance(edge_start,edge_end,base_centre,owner):
    """Signed Sf dot face-to-cell distance; Sf=.5*(Q-P) cross (A-P)."""
    p,q,b=vector(edge_start),vector(edge_end),vector(base_centre)
    k=sub(p,b) if owner else sub(b,p)
    a=mul(cross(k,sub(q,p)),F(3,8))
    return affine(a,-dot(a,p))

def cone_volume(outward_base_area,base_centre):
    sf,bc=vector(outward_base_area),vector(base_centre)
    return affine(mul(sf,F(-1,3)),dot(sf,bc)/3)

def fixed_face_distance(sf,fc,base_centre,owner):
    sf,fc,bc=vector(sf),vector(fc),vector(base_centre)
    if owner:return affine(mul(sf,F(-1,4)),dot(sf,sub(fc,mul(bc,F(3,4)))))
    return affine(mul(sf,F(1,4)),dot(sf,sub(mul(bc,F(3,4)),fc)))

def quality_rows(do,dn,vo,vn,weight=WEIGHT,ratio=RATIO):
    """All rows are >=0; include the installed denominator's exact VSMALL."""
    return {
        "ownerPositivePyramid":do,
        "neighbourPositivePyramid":dn,
        "ownerPositiveVolume":vo,
        "neighbourPositiveVolume":vn,
        "weightOwner":combine(combine(do,dn,1-weight,-weight),affine((0,0,0),-weight*VSMALL)),
        "weightNeighbour":combine(combine(dn,do,1-weight,-weight),affine((0,0,0),-weight*VSMALL)),
        "ratioOwner":combine(combine(vo,vn,1,-ratio),affine((0,0,0),-ratio*VSMALL)),
        "ratioNeighbour":combine(combine(vn,vo,1,-ratio),affine((0,0,0),-ratio*VSMALL)),
    }

def exact_solve(matrix,rhs):
    """Rational elimination; free variables zero, with exact consistency check."""
    a=[[rational(v) for v in row]+[rational(b)] for row,b in zip(matrix,rhs)]
    n=len(matrix[0]);pivot=[];r=0
    for c in range(n):
        k=next((k for k in range(r,len(a)) if a[k][c]),None)
        if k is None:continue
        a[r],a[k]=a[k],a[r];div=a[r][c];a[r]=[v/div for v in a[r]]
        for k in range(len(a)):
            if k!=r and a[k][c]:
                div=a[k][c];a[k]=[v-div*w for v,w in zip(a[k],a[r])]
        pivot.append((r,c));r+=1
        if r==len(a):break
    if any(not any(row[:n]) and row[n] for row in a):raise ValueError("inconsistent exact system")
    x=[F(0)]*n
    for r,c in reversed(pivot):x[c]=a[r][n]-sum((a[r][j]*x[j] for j in range(c+1,n)),F(0))
    return x

def normalize_rows(rows,origin,length):
    origin=vector(origin);length=rational(length);out=[]
    for row in rows:
        local=(*mul(row[:3],length),value(row,origin))
        norm=max(abs(x) for x in local)
        if not norm:norm=F(1)
        out.append(tuple(x/norm for x in local))
    return out

def farkas_certificate(rows):
    """Find a sparse floating witness, then reconstruct/verify exact fractions."""
    a=np.array([[float(v) for v in row[:3]] for row in rows])
    b=np.array([float(row[3]) for row in rows])
    dual=linprog(b,A_eq=np.vstack([a.T,np.ones(len(rows))]),
                 b_eq=[0,0,0,1],bounds=[(0,None)]*len(rows),method="highs")
    if not dual.success or dual.fun>=-1e-10:return None
    support=np.flatnonzero(dual.x>1e-8).tolist()
    try:
        lam=exact_solve([[rows[i][j] for i in support] for j in range(3)]+[[F(1)]*len(support)],[0,0,0,1])
    except ValueError:return None
    if any(v<0 for v in lam):return None
    grad=[sum((l*rows[i][j] for l,i in zip(lam,support)),F(0)) for j in range(3)]
    constant=sum((l*rows[i][3] for l,i in zip(lam,support)),F(0))
    if any(grad) or constant>=0:return None
    return {"indices":support,"multipliers":[str(v) for v in lam],
            "gradientExact":[str(v) for v in grad],"constantExact":str(constant),
            "constantFloat":float(constant),
            "proof":"Nonnegative combination of all >=0 rows equals this negative constant."}

def solve_kernel(rows,origin,length,strict_rows=None):
    normalized=normalize_rows(rows,origin,length)
    a=np.array([[float(v) for v in row[:3]] for row in normalized])
    b=np.array([float(row[3]) for row in normalized])
    feasible=linprog([0,0,0],A_ub=-a,b_ub=b,bounds=[(None,None)]*3,method="highs")
    result={"status":int(feasible.status),"message":feasible.message}
    if feasible.status==2:
        cert=farkas_certificate(normalized)
        result.update(infeasibleCertified=cert is not None,certificate=cert)
        return result
    if not feasible.success:return result
    # Maximize a common normalized slack of strict geometry and quality rows.
    interior=normalize_rows(strict_rows if strict_rows is not None else rows,origin,length)
    aa=np.array([[float(v) for v in row[:3]] for row in interior]);bb=np.array([float(row[3]) for row in interior])
    opt=linprog([0,0,0,-1],A_ub=np.c_[-aa,np.ones(len(aa))],b_ub=bb,
                bounds=[(None,None)]*4,method="highs")
    result["closedKernelFeasible"]=True
    if opt.success:
        candidate=np.asarray(origin)+float(length)*opt.x[:3]
        result.update(candidate=candidate.tolist(),minimumNormalizedSlack=float(opt.x[3]),
                      candidateExactMinimumRowValue=min(float(value(row,candidate)) for row in rows))
    else:result["interiorMessage"]=opt.message
    return result

def _load(name):
    path=Path(__file__).with_name(name)
    spec=importlib.util.spec_from_file_location(name.removesuffix(".py"),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()

def extract_stencils(mesh,rank,parents):
    q=_load("audit_air_cone_remaining_quality.py")
    ancestry=_load("audit_air_refan_remaining_ancestry.py")
    geo=q.g.Geometry(mesh/f"processor{rank}/constant/polyMesh")
    trace=ancestry.read_trace(mesh,rank);documents=[]
    for parent in parents:
        children=list(map(int,np.flatnonzero(trace["cellParent"]==parent)))
        if len(children)<2:raise ValueError("parent is not decomposed into multiple cones")
        common=set.intersection(*(set(map(int,geo.cell(c)[5])) for c in children))
        if len(common)!=1:raise ValueError("unique shared apex required")
        apex=next(iter(common))
        # Frozen native written map proves this is a new point, never STL motion.
        wp=mesh/f"conforming-written-rank{rank}-point-native.i32"
        wm=trace["writtenManifest"]
        point_native=np.memmap(wp,mode="r",dtype="<i4",shape=(wm["points"],))
        if point_native[apex]<trace["sourceManifest"]["points"]:raise ValueError("common point is a fixed source point")
        faces={int(f) for c in children for f in geo.cell(c)[0]}
        apex_faces=set(map(int,np.searchsorted(geo.offsets,np.flatnonzero(geo.vertices==apex),side="right")-1))
        if not apex_faces<=faces:raise ValueError("shared apex has incidences outside its traced parent")
        if any(f>=len(geo.neighbour) or int(geo.owner[f]) not in children or int(geo.neighbour[f]) not in children for f in apex_faces):
            raise ValueError("shared apex touches an external or boundary face")
        outside=({int(geo.owner[f]) for f in faces}|{int(geo.neighbour[f]) for f in faces if f<len(geo.neighbour)})-set(children)
        context=faces|{int(f) for c in outside for f in geo.cell(c)[0]}
        points={int(v) for f in context for v in geo.ids(f)}
        kernel=ancestry.source_parent_kernel(trace,parent)
        for plane in kernel["originalPlanes"]:
            f=plane["sourceFace"]
            ids=trace["vertices"][trace["offsets"][f]:trace["offsets"][f+1]]
            if trace["owner"][f]!=parent:ids=ids[::-1]
            plane["coordinates"]=trace["points"][ids].tolist()
        doc={"schema":"actual-cone-quality-kernel-stencil/v1","rank":rank,"parent":parent,
             "mesh":str(mesh),"children":children,"outsideCells":sorted(outside),"apexWrittenPoint":apex,
             "apexNativePoint":int(point_native[apex]),"sourcePointCount":trace["sourceManifest"]["points"],
             "apexIncidenceProof":{"allFacesWithinTracedChildren":True,"allFacesInternal":True,"apexFaceCount":len(apex_faces)},
             "originalApex":geo.points[apex].tolist(),"affectedFaces":sorted(faces),"sourceKernel":kernel,
             "points":{str(v):geo.points[v].tolist() for v in sorted(points)},
             "faces":{str(f):{"pointIDs":geo.ids(f).tolist(),"owner":int(geo.owner[f]),
                        "neighbour":int(geo.neighbour[f]) if f<len(geo.neighbour) else None,
                        "patch":geo.patch(f),"sourceBase":int(trace["faceBase"][f])} for f in sorted(context)},
             "cells":{str(c):{"faces":geo.cell(c)[0].tolist(),"writtenCentre":geo.cell(c)[3].tolist(),
                        "writtenVolume":geo.cell(c)[4],"sourceParent":int(trace["cellParent"][c])} for c in sorted(set(children)|outside)},
             "meshIdentity":{p.name:sha(p) for p in geo.poly_dir.iterdir() if p.is_file()},
             "traceIdentity":dict(trace["identities"],**{wp.name:sha(wp)})}
        documents.append(doc)
    return documents

def model_stencil(doc,approximate=False):
    points={int(k):vector(v) for k,v in doc["points"].items()}
    faces={int(k):v for k,v in doc["faces"].items()}
    children=set(doc["children"]);apexid=doc["apexWrittenPoint"]
    models={};base_ids={};unsupported=[];approximation=[]
    direct=_load("audit_air_cone_candidate_independent.py").Stencil(doc,doc["originalApex"])
    for c in sorted(children):
        fs=doc["cells"][str(c)]["faces"]
        bases=[f for f in fs if apexid not in faces[f]["pointIDs"]]
        if len(bases)!=1:raise ValueError("each child must have exactly one fixed polygonal base")
        f=bases[0];ids=faces[f]["pointIDs"]
        p=[points[v] for v in ids]
        try:sf,bc=planar_polygon(p)
        except ValueError as e:
            if not approximate or str(e)!="exact planar model required":
                unsupported.append({"cell":c,"base":f,"reason":str(e)});continue
            area,centre=direct.face(f)
            xyz=np.asarray([list(map(float,v)) for v in p])
            error=float(abs((xyz-centre)@(area/np.linalg.norm(area))).max())
            span=max(float(np.linalg.norm(v-centre)) for v in xyz)
            if error>1e-10*span:
                unsupported.append({"cell":c,"base":f,"reason":"base exceeds unchanged native planarity gate"});continue
            sf,bc=vector(area),vector(centre)
            approximation.append({"cell":c,"base":f,"planeErrorM":error,"nativePlanarityLimitM":1e-10*span})
        if faces[f]["owner"]!=c:sf=mul(sf,-1)
        models[c]={"base":f,"centre":bc,"volume":cone_volume(sf,bc)}
        base_ids[c]=f
        predicted_cc=mul(add(mul(bc,3),vector(doc["originalApex"])),F(1,4))
        actual=vector(doc["cells"][str(c)]["writtenCentre"])
        err=max(abs(float(x-y)) for x,y in zip(predicted_cc,actual))
        predicted_v=float(value(models[c]["volume"],doc["originalApex"]))
        volume=doc["cells"][str(c)]["writtenVolume"]
        if err>1e-10 or abs(predicted_v-volume)>1e-9*abs(volume):
            raise ValueError("actual child CC/V does not match the fixed-base cone model")
    rows=[];names=[];strict=[];face_models={}
    for p in doc["sourceKernel"]["originalPlanes"]:
        # Source normal is the installed face-area direction. For infeasibility
        # the closed source kernel is relaxed; stricter 3um padding is only used
        # for candidate search, never in a claimed exact impossibility proof.
        xyz=[vector(v) for v in p["coordinates"]]
        sf=mul(tuple(sum((cross(sub(xyz[i],xyz[0]),sub(xyz[i+1],xyz[0]))[j] for i in range(1,len(xyz)-1)),F(0)) for j in range(3)),F(1,2))
        if sf==(0,0,0):raise ValueError("zero source-plane area")
        if any(dot(sf,sub(v,xyz[0])) for v in xyz):
            if not approximate:
                unsupported.append({"sourceFace":p["sourceFace"],"reason":"nonexact planar source kernel omitted"});continue
            xyz_float=np.array(p["coordinates"])
            area,centre=_load("audit_air_cone_candidate_independent.py").face_geometry(xyz_float)
            error=float(abs((xyz_float-centre)@(area/np.linalg.norm(area))).max())
            span=max(float(np.linalg.norm(v-centre)) for v in xyz_float)
            if error>1e-10*span:raise ValueError("source face exceeds unchanged native planarity gate")
            sf,fc=vector(area),vector(centre)
            approximation.append({"sourceFace":p["sourceFace"],"reason":"stored original floating plane used only for candidate generation"})
        else:fc=xyz[0]
        row=affine(mul(sf,-1),dot(sf,fc))
        magnitude=rational(math.sqrt(float(dot(sf,sf))))
        while magnitude*magnitude<dot(sf,sf):magnitude=rational(math.nextafter(float(magnitude),math.inf))
        rows.append(row);strict.append(combine(row,affine((0,0,0),-F.from_float(3e-6)*magnitude)));names.append("sourcePlane:"+str(p["sourceFace"]))
    for f in doc["affectedFaces"]:
        row=faces[f];own=row["owner"];nei=row["neighbour"]
        if nei is None:
            if row["patch"] and row["patch"]["type"] in {"cyclic","processor","processorCyclic"}:
                unsupported.append({"face":f,"reason":"coupled neighbour CC/V unavailable"})
            continue
        ids=row["pointIDs"];xyz=[points[v] for v in ids]
        if (own in children and own not in models) or (nei in children and nei not in models):
            unsupported.append({"face":f,"reason":"moving child lacks exact planar base model"});continue
        if apexid in ids:
            if own not in children or nei not in children:raise ValueError("apex side incidence outside actual children")
            index=ids.index(apexid);fixed=[points[ids[(index+i)%len(ids)]] for i in range(1,len(ids))]
            p,q=fixed[0],fixed[-1]
            if any(cross(sub(v,p),sub(q,p))!=(0,0,0) for v in fixed):raise ValueError("side polygon fixed nodes must be one straight edge")
            do=side_distance(p,q,models[own]["centre"],True)
            dn=side_distance(p,q,models[nei]["centre"],False)
            kind="sameParentSide"
        else:
            try:sf,fc=planar_polygon(xyz)
            except ValueError as e:
                if not approximate or str(e)!="exact planar model required":
                    unsupported.append({"face":f,"reason":str(e)});continue
                sf,fc=map(vector,direct.face(f))
                approximation.append({"face":f,"reason":"nonexact planar fixed interface used only for candidate generation"})
            do=(fixed_face_distance(sf,fc,models[own]["centre"],True) if own in children
                else affine((0,0,0),dot(sf,sub(fc,vector(doc["cells"][str(own)]["writtenCentre"])))))
            dn=(fixed_face_distance(sf,fc,models[nei]["centre"],False) if nei in children
                else affine((0,0,0),dot(sf,sub(vector(doc["cells"][str(nei)]["writtenCentre"]),fc))))
            kind="fixedExternalBase"
        vo=models[own]["volume"] if own in children else affine((0,0,0),doc["cells"][str(own)]["writtenVolume"])
        vn=models[nei]["volume"] if nei in children else affine((0,0,0),doc["cells"][str(nei)]["writtenVolume"])
        if value(do,doc["originalApex"])<=0 or value(dn,doc["originalApex"])<=0:
            raise ValueError("original face is outside positive-pyramid branch")
        fr=quality_rows(do,dn,vo,vn)
        for name,coeff in fr.items():rows.append(coeff);strict.append(coeff);names.append(str(f)+":"+name)
        face_models[f]={"kind":kind,"owner":own,"neighbour":nei,"dOwn":do,"dNeighbour":dn,"vOwn":vo,"vNeighbour":vn}
    return rows,strict,names,face_models,models,unsupported,approximation

def evaluate_linear(face_models,apex):
    result=[]
    for f,row in face_models.items():
        do,dn,vo,vn=[float(value(row[k],apex)) for k in ["dOwn","dNeighbour","vOwn","vNeighbour"]]
        result.append({"face":f,"kind":row["kind"],"dOwn":do,"dNeighbour":dn,"vOwn":vo,"vNeighbour":vn,
            "positivePyramid":do>0 and dn>0,"positiveVolume":vo>0 and vn>0,
            "weight":min(do,dn)/(do+dn+1e-300),"ratio":min(vo,vn)/(max(vo,vn)+1e-300)})
    return result

def actual_stencil_quality(doc,apex):
    direct=_load("audit_air_cone_candidate_independent.py").Stencil(doc,apex)
    children=[direct.cell_detail(c) for c in doc["children"]]
    coupled=[];faces=[]
    for f in doc["affectedFaces"]:
        raw=doc["faces"][str(f)]
        if raw["neighbour"] is None and raw["patch"] and raw["patch"]["type"] in {"cyclic","processor","processorCyclic"}:
            coupled.append(f);continue
        faces.append(direct.face_detail(f))
    kernel=doc["sourceKernel"]["originalPlanes"]
    margins=[float(np.dot(np.asarray(p["normal"]),np.asarray(p["centre"])-np.asarray(apex))) for p in kernel]
    spans={f["face"]:max(np.linalg.norm(p-direct.face(f["face"])[1]) for p in direct.polygon(f["face"])) for f in faces}
    minimum_area=min(float(np.linalg.norm(direct.face(f["face"])[0])) for f in faces)
    fixed_points=np.array([v for i,v in direct.points.items() if i!=doc["apexWrittenPoint"]])
    V=sum(c["V"] for c in children)
    M=sum((c["V"]*np.asarray(c["CC"]) for c in children),np.zeros(3))
    return {"minimumDeterminant":min(c["D"] for c in children),
        "minimumWeight":min(f["weight"] for f in faces if "weight" in f),
        "minimumRatio":min(f["ratio"] for f in faces if "ratio" in f),
        "maximumSkewness":max(f["skew"] for f in faces),
        "maximumNonOrthogonalityDegrees":max(f["nonOrthogonalityDegrees"] for f in faces if "nonOrthogonalityDegrees" in f),
        "minimumParentPlaneMarginM":min(margins),"sumVolume":V,"firstMoment":M.tolist(),
        "minimumChildVolume":min(c["V"] for c in children),
        "minimumPyramidVolume":min(c["minCentroidPyramidVolume"] for c in children),
        "maximumClosure":max(c["relativeClosure"] for c in children),
        "minimumFaceAreaM2":minimum_area,"minimumContextFixedPointDistanceM":float(np.linalg.norm(fixed_points-np.asarray(apex),axis=1).min()),
        "maximumFacePlanarityErrorM":max(f["planarityErrorM"] for f in faces),
        "maximumNativePlanarityGateFraction":max(f["planarityErrorM"]/(1e-10*spans[f["face"]]) for f in faces),
        "minimumPolygonSupportM":min(f["polygonMinimumSupportM"] for f in faces),
        "maximumVertexOutsideM":max(c["maximumVertexOutsideM"] for c in children),
        "concavityPairTriggers":sum(c["faceCentreConcavityPairTriggers"] for c in children),
        "allEdgesClosed":all(c["edgesExactlyTwo"] and c["edgesOppositeOrientation"] for c in children),
        "minimumCentreFanTetQuality":min(f["minimumCentreFanTetQuality"] for f in faces),
        "minimumSharedBasePointTetQuality":min(f["bestSharedBasePointMinimumTetQuality"] for f in faces),
        "unsupportedCoupledFaces":coupled,
        "cells":children,"faces":faces}

def actual_pareto(old,new):
    result={}
    for label,collection,idname,metric,threshold,greater in [
        ("determinant","cells","cell","D",.001,False),
        ("weight","faces","face","weight",.05,False),
        ("ratio","faces","face","ratio",.01,False),
        ("skew","faces","face","skew",4,True),
        ("nonOrthogonality","faces","face","nonOrthogonalityDegrees",70,True)]:
        def bad(q):
            return {row[idname]:row[metric] for row in q[collection] if metric in row and
                (row[metric]>threshold if greater else row[metric]<threshold)}
        before,after=bad(old),bad(new)
        def severity(v):return v/threshold if greater else threshold/v
        bo=max((severity(v) for v in before.values()),default=1.)
        ao=max((severity(v) for v in after.values()),default=1.)
        result[label]={"oldBad":sorted(before),"candidateBad":sorted(after),
            "newlyBad":sorted(set(after)-set(before)),"oldWorstFailedSeverity":bo,
            "candidateWorstFailedSeverity":ao,"noNewFailures":not (set(after)-set(before)),
            "countDoesNotIncrease":len(after)<=len(before),
            "worstFailedSeverityDoesNotIncrease":ao<=bo}
    return result

def analyze(doc):
    coords=np.array(list(doc["points"].values()));length=float(np.max(np.ptp(coords,axis=0)))
    erows,estrict,en,efaces,emodels,eunsupported,_=model_stencil(doc,False)
    exact=solve_kernel(erows,doc["originalApex"],length,estrict)
    if exact.get("certificate"):
        cert=exact["certificate"];cert["constraintNames"]=[en[i] for i in cert["indices"]]
        normalized=normalize_rows(erows,doc["originalApex"],length)
        cert["normalizedConstraintRows"]=[[str(v) for v in normalized[i]] for i in cert["indices"]]
    rows,strict,names,faces,models,unsupported,approximation=model_stencil(doc,True)
    solved=solve_kernel(rows,doc["originalApex"],length,strict)
    # Approximate-system infeasibility must never be labeled an exact exclusion.
    if approximation:
        solved.pop("certificate",None);solved.pop("infeasibleCertified",None)
        solved["scope"]="Near-planar affine proposal model; not an impossibility proof or acceptance."
    original=evaluate_linear(faces,doc["originalApex"])
    actual=actual_stencil_quality(doc,doc["originalApex"])
    actual_by_face={f["face"]:f for f in actual["faces"]}
    errors=[abs(f["weight"]-actual_by_face[f["face"]]["weight"]) for f in original]
    ratio_errors=[abs(f["ratio"]-actual_by_face[f["face"]]["ratio"]) for f in original]
    result={"schema":"cone-quality-kernel-diagnostic/v1","rank":doc["rank"],"parent":doc["parent"],
        "scope":"Frozen topology; one new internal apex; exact necessary subset certificates and explicitly near-planar affine candidate generation; actual OF floating-stencil reevaluation. No native acceptance.",
        "children":len(doc["children"]),"localFaceModels":len(faces),"unsupportedFaces":unsupported,
        "approximateGeometryRecords":approximation,
        "exactNecessarySubset":{"modelledChildren":len(emodels),"localFaceModels":len(efaces),"omitted":eunsupported,"solution":exact},
        "original":{"apex":doc["originalApex"],"minimumWeight":min(row["weight"] for row in original),
                    "minimumRatio":min(row["ratio"] for row in original),
                    "maximumAffineVsActualWeightError":max(errors),"maximumAffineVsActualRatioError":max(ratio_errors)},
        "linearConstraints":[{"name":name,"coefficients":[str(x) for x in row]} for name,row in zip(names,rows)],
        "normalizedCoordinates":{"origin":doc["originalApex"],"length":length},
        "solution":solved,"originalActual":actual}
    if solved.get("candidate"):
        candidate=evaluate_linear(faces,solved["candidate"]);reevaluated=actual_stencil_quality(doc,solved["candidate"])
        result["candidate"]={"apex":solved["candidate"],"minimumWeight":min(r["weight"] for r in candidate),
            "minimumRatio":min(r["ratio"] for r in candidate),
            "allLocalPositivePyramids":all(r["positivePyramid"] for r in candidate),
            "allLocalPositiveVolumes":all(r["positiveVolume"] for r in candidate)}
        reevaluated["relativeVolumeDifference"]=abs(reevaluated["sumVolume"]-actual["sumVolume"])/actual["sumVolume"]
        reevaluated["firstMomentDifference"]=float(np.linalg.norm(np.asarray(reevaluated["firstMoment"])-actual["firstMoment"]))
        result["candidateActual"]=reevaluated
        result["candidateActualPareto"]=actual_pareto(actual,reevaluated)
        real_faces={f["face"]:f for f in reevaluated["faces"]}
        result["candidate"]["maximumAffineVsActualWeightError"]=max(abs(f["weight"]-real_faces[f["face"]]["weight"]) for f in candidate)
        result["candidate"]["maximumAffineVsActualRatioError"]=max(abs(f["ratio"]-real_faces[f["face"]]["ratio"]) for f in candidate)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path);p.add_argument("--mesh",type=Path)
    p.add_argument("--rank",type=int);p.add_argument("--parents",type=int,nargs="+")
    p.add_argument("--output",type=Path,required=True);p.add_argument("--fixture",type=Path)
    args=p.parse_args()
    if args.input:
        documents=json.loads(args.input.read_text())["stencils"]
    elif args.mesh is not None and args.rank is not None and args.parents:
        documents=extract_stencils(args.mesh,args.rank,args.parents)
        if args.fixture:args.fixture.write_text(json.dumps({"schema":"cone-quality-kernel-input/v1","stencils":documents},indent=2)+"\n")
    else:p.error("provide --input or --mesh/--rank/--parents")
    output=[]
    for doc in documents:
        try:output.append(analyze(doc))
        except ValueError as e:output.append({"rank":doc["rank"],"parent":doc["parent"],"unsupported":str(e)})
    args.output.write_text(json.dumps({"schema":"cone-quality-kernel-batch/v1","toolSHA256":sha(Path(__file__)),"results":output},indent=2)+"\n")
    print(json.dumps([{**{k:row[k] for k in ["rank","parent","children","solution","unsupported"] if k in row},
        "original":{k:row["original"][k] for k in ["minimumWeight","minimumRatio"]} if "original" in row else None,
        "candidate":{k:row["candidate"][k] for k in ["apex","minimumWeight","minimumRatio"]} if "candidate" in row else None} for row in output],default=str))
if __name__=="__main__":main()
