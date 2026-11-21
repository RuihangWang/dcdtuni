#!/usr/bin/env python3
"""Offline necessary convex determinant cuts for fixed tetrahedral cone bases.

All fractions come from original binary coordinates. LP supplies a candidate or
a sparse witness only; infeasibility is reported as certified solely after exact
nonnegative combination verification. Other cells and the native mesh are not
authorized by these necessary cuts.
"""
from __future__ import annotations
import argparse
from fractions import Fraction as F
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import audit_air_cone_quality_kernel as k
import audit_air_cone_nonorth_kernel as n
import audit_air_cone_whole_parent_optimizer as o

def integer_root(value,degree):
    if value<0 or degree<1:raise ValueError("nonnegative integer and positive degree")
    if not value:return 0
    if degree==2:return math.isqrt(value)
    x=1<<((value.bit_length()+degree-1)//degree)
    while True:
        y=((degree-1)*x+value//x**(degree-1))//degree
        if y>=x:break
        x=y
    while (x+1)**degree<=value:x+=1
    while x**degree>value:x-=1
    return x

def root_bounds(value,degree,bits=96):
    value=k.rational(value)
    if value<0:raise ValueError("nonnegative root required")
    scale=1<<bits
    i=integer_root(value.numerator*scale**degree//value.denominator,degree)
    lo=F(i,scale);hi=lo if lo**degree==value else F(i+1,scale)
    assert lo**degree<=value<=hi**degree
    return lo,hi

def support_vector(vector):
    vector=k.vector(vector);norm2=k.dot(vector,vector)
    if norm2<=0:raise ValueError("nonzero norm support required")
    _,upper=root_bounds(norm2,2)
    unit=k.mul(vector,1/upper)
    assert k.dot(unit,unit)<=1
    return unit

def dot_affine(vector,rows):
    return tuple(sum((vector[i]*rows[i][j] for i in range(3)),F(0)) for j in range(4))

def point_affine(point):
    return tuple(k.affine(tuple(F(i==j) for j in range(3)),0) for i in range(3))

def area_affine(points,apex_position):
    fixed=[k.vector(p) for p in points]
    def evaluate(point):
        p=list(fixed);p[apex_position]=k.vector(point);origin=p[0]
        return k.mul(tuple(sum((k.cross(k.sub(p[i],origin),k.sub(p[i+1],origin))[j]
             for i in range(1,len(p)-1)),F(0)) for j in range(3)),F(1,2))
    zero=evaluate((0,0,0));basis=[evaluate(tuple(F(i==j) for j in range(3))) for i in range(3)]
    return tuple(k.affine(tuple(basis[j][i]-zero[i] for j in range(3)),zero[i]) for i in range(3))

def affine_vector_value(rows,point):
    return tuple(k.value(row,point) for row in rows)

def qualified_tets(doc,old):
    entries=[];bycell={row["cell"]:row for row in old["cells"]}
    for cell in doc["children"]:
        if bycell[cell]["D"]>=.001:continue
        faces=doc["cells"][str(cell)]["faces"]
        verts={v for f in faces for v in doc["faces"][str(f)]["pointIDs"]}
        base=[f for f in faces if doc["apexWrittenPoint"] not in doc["faces"][str(f)]["pointIDs"]]
        if len(faces)!=4 or len(verts)!=4 or len(base)!=1:continue
        selected=[f for f in faces if doc["faces"][str(f)]["patch"] is None or doc["faces"][str(f)]["patch"]["type"] in o.COUPLED]
        if len(selected) not in (3,4) or (len(selected)==3 and base[0] in selected):continue
        points=[k.vector(doc["points"][str(v)]) for v in doc["faces"][str(base[0])]["pointIDs"]]
        if len(points)!=3:continue
        b=k.cross(k.sub(points[1],points[0]),k.sub(points[2],points[0]))
        h=k.affine(b,-k.dot(b,points[0]))
        if k.value(h,doc["originalApex"])<0:h=k.scale(h,-1)
        if k.value(h,doc["originalApex"])<=0:raise ValueError("positive original tetrahedral height")
        sides=[]
        for f in faces:
            if f==base[0]:continue
            ids=doc["faces"][str(f)]["pointIDs"]
            sides.append(area_affine([doc["points"][str(v)] for v in ids],ids.index(doc["apexWrittenPoint"])))
        entries.append({"cell":cell,"baseFace":base[0],"selectedCount":len(selected),"selectedFaces":selected,
            "areaSquared":k.dot(b,b)/4,"height":h,"sides":sides,"originalD":bycell[cell]["D"]})
    return entries

def determinant_formula(entry,point):
    height=float(k.value(entry["height"],point))
    side_areas=[np.linalg.norm(list(map(float,affine_vector_value(side,point)))) for side in entry["sides"]]
    area=math.sqrt(float(entry["areaSquared"]))
    count=entry["selectedCount"];total=sum(side_areas)+(area if count==4 else 0)
    denominator=512 if count==3 else 128
    return count**6*height**4/(denominator*total**6)

def convex_value(entry,point,threshold=.001):
    height=float(k.value(entry["height"],point))
    if height<=0:return math.inf
    count=entry["selectedCount"]
    total=sum(np.linalg.norm(list(map(float,affine_vector_value(side,point)))) for side in entry["sides"])
    if count==4:total+=math.sqrt(float(entry["areaSquared"]))
    c6=F(count**6,512 if count==3 else 128)/k.rational(threshold)
    return total-float(c6)**(1/6)*height**(2/3)

def determinant_cut(entry,point,threshold=.001):
    """Exact necessary >=0 support cut, valid for every positive signed height."""
    height=k.value(entry["height"],point)
    if height<=0:raise ValueError("positive tangent height required")
    count=entry["selectedCount"];c6=F(count**6,512 if count==3 else 128)/k.rational(threshold)
    # C*H^(2/3) <= (2C/(3H0^(1/3)))*H + C*H0^(2/3)/3.
    a6=F(2,3)**6*c6/height**2;b6=F(1,3)**6*c6*height**4
    _,a_upper=root_bounds(a6,6);_,b_upper=root_bounds(b6,6)
    supports=[];area_row=(F(0),)*4
    for side in entry["sides"]:
        unit=support_vector(affine_vector_value(side,point))
        area_row=k.combine(area_row,dot_affine(unit,side))
        supports.append([str(v) for v in unit])
    area_lower=root_bounds(entry["areaSquared"],2)[0] if count==4 else F(0)
    area_row=k.combine(area_row,k.affine((0,0,0),area_lower))
    row=k.combine(k.scale(entry["height"],a_upper),area_row,1,-1)
    row=k.combine(row,k.affine((0,0,0),b_upper))
    metadata={"kind":"tetDeterminantNecessary","cell":entry["cell"],"baseFace":entry["baseFace"],
        "selectedCount":count,"query":list(map(float,point)),"thresholdExact":str(k.rational(threshold)),
        "heightAtQueryExact":str(height),"coefficientSixthPowerExact":str(c6),
        "aSixthPowerExact":str(a6),"bSixthPowerExact":str(b6),"aUpper":str(a_upper),"bUpper":str(b_upper),
        "areaSquaredExact":str(entry["areaSquared"]),"baseAreaLower":str(area_lower),
        "unitNormSupportsExact":supports,
        "proof":"Norm support lower bounds and a rational upper concave-power tangent: D>=threshold implies this >=0 row on H>0."}
    return row,metadata

def atan_bounds(x,terms=50):
    x=k.rational(x)
    partial=sum(((-1)**i*x**(2*i+1)/F(2*i+1) for i in range(terms)),F(0))
    following=(-1)**terms*x**(2*terms+1)/F(2*terms+1)
    return min(partial,partial+following),max(partial,partial+following)

def cosine70_lower():
    # Machin: pi=16 atan(1/5)-4 atan(1/239), both alternating series.
    lo5,hi5=atan_bounds(F(1,5));lo239,hi239=atan_bounds(F(1,239))
    pi_upper=16*hi5-4*lo239
    x=F(7,18)*pi_upper
    terms=24
    lower=sum(((-1)**i*x**(2*i)/F(math.factorial(2*i)) for i in range(terms)),F(0))
    assert 0<lower<1
    scale=1<<96
    return F(lower.numerator*scale//lower.denominator,scale)

def nonorth_cuts(doc,face_models,models,point):
    """Exact necessary, not sufficient, cosine support cuts on retained faces."""
    points={int(v):k.vector(x) for v,x in doc["points"].items()};child=set(doc["children"])
    cosine=cosine70_lower();rows=[]
    def centre(cell):
        if cell in child:
            b=models[cell]["centre"]
            return tuple(k.affine(tuple(F(i==j,4) for j in range(3)),F(3,4)*b[i]) for i in range(3))
        return tuple(k.affine((0,0,0),v) for v in k.vector(doc["cells"][str(cell)]["writtenCentre"]))
    for face in face_models:
        raw=doc["faces"][str(face)];own=raw["owner"];nei=raw["neighbour"]
        oc,nc=centre(own),centre(nei);delta=tuple(k.combine(nc[i],oc[i],1,-1) for i in range(3))
        ids=raw["pointIDs"]
        if doc["apexWrittenPoint"] in ids:
            if any(any(row[j] for j in range(3)) for row in delta):raise ValueError("side centre difference must be constant")
            sf=area_affine([points[v] for v in ids],ids.index(doc["apexWrittenPoint"]))
            d=tuple(row[3] for row in delta);norm_lower=root_bounds(k.dot(d,d),2)[0]
            unit=support_vector(affine_vector_value(sf,point))
            row=k.combine(dot_affine(d,sf),dot_affine(unit,sf),1,-cosine*norm_lower)
            kind="sameParentSide";norm_kind="constantCentreDistance"
        else:
            sf,_=k.planar_polygon([points[v] for v in ids])
            norm_lower=root_bounds(k.dot(sf,sf),2)[0]
            unit=support_vector(affine_vector_value(delta,point))
            row=k.combine(dot_affine(sf,delta),dot_affine(unit,delta),1,-cosine*norm_lower)
            kind="fixedExternalBase";norm_kind="constantFaceArea"
        rows.append((row,{"kind":"nonorthNecessary","face":face,"relation":kind,"query":list(map(float,point)),
             "cosineLowerExact":str(cosine),"constantNormLowerExact":str(norm_lower),"normKind":norm_kind,
             "unitNormSupportExact":[str(v) for v in unit],
             "proof":"Sf.dot(d)>=cos(70)*|Sf|*|d| implies this support row; rational cosine/norm lower bounds, never an inner cone exclusion."}))
    return rows

def analyze(doc,maximum_rounds=12):
    o.FastStencil(doc) # Refuse unknown coupled exterior state.
    old=k.actual_stencil_quality(doc,doc["originalApex"])
    entries=qualified_tets(doc,old)
    rows,_,names,face_models,models,omitted,approximation=k.model_stencil(doc,approximate=False)
    if approximation:raise ValueError("exact necessary model required")
    metadata=[{"kind":"exactOriginalKernelOrWR","name":name} for name in names]
    for entry in entries:
        rows.append(entry["height"]);metadata.append({"kind":"positiveTetHeightRelaxation","cell":entry["cell"]})
    origin=np.asarray(doc["originalApex"]);coords=np.array([v for plane in doc["sourceKernel"]["originalPlanes"] for v in plane["coordinates"]])
    length=float((coords.max(axis=0)-coords.min(axis=0)).max())
    seeds=[origin]
    try:lo,hi=n.axis_box(doc);seeds.append((lo+hi)/2)
    except ValueError:pass
    history=[];candidates=[];certificate=None;normalized=None;cutseen=set()
    for round_id in range(maximum_rounds):
        for point in seeds:
            for entry in entries:
                if k.value(entry["height"],point)<=0:continue
                row,meta=determinant_cut(entry,point)
                if row not in cutseen:cutseen.add(row);rows.append(row);metadata.append(meta)
            for row,meta in nonorth_cuts(doc,face_models,models,point):
                if row not in cutseen:cutseen.add(row);rows.append(row);metadata.append(meta)
        normalized=k.normalize_rows(rows,origin,length)
        result=k.solve_kernel(rows,origin,length)
        history.append({"round":round_id,"rowCount":len(rows),"LP":result})
        if result.get("infeasibleCertified"):
            cert=result["certificate"];indices=cert["indices"]
            certificate={**cert,"rowConvention":">=0","normalizedOrigin":origin.tolist(),"normalizedScale":length,
                "supportRows":[{"originalIndex":i,"rowExact":[str(x) for x in normalized[i]],"derivation":metadata[i]} for i in indices],
                "scope":"Exact necessary convex-tet/kernel/W/R/N row combination for fixed original geometry on positive height. No full mesh or alternate topology infeasibility."}
            break
        if not result.get("candidate"):break
        point=np.asarray(result["candidate"])
        try:
            candidate=n.evaluate(doc,old,point);candidate["derivation"]="necessaryConvexOuterLP:round"+str(round_id);candidates.append(candidate)
        except (ValueError,FloatingPointError,ZeroDivisionError) as exc:
            candidates.append({"apex":point.tolist(),"unsupported":str(exc)})
        gaps=[convex_value(entry,point) for entry in entries]
        if all(v<=0 for v in gaps):break
        if any(np.array_equal(point,seed) for seed in seeds):break
        seeds=[point]
    return {"schema":"tetrahedral-convex-necessary-apex-kernel/v1","rank":doc["rank"],"parent":doc["parent"],
        "scope":"Pure offline necessary outer approximation on fixed triangular bases; all other cell geometry/native gates remain required.",
        "formulaDefinition":"A is fixed triangle area and h positive perpendicular height. D3=A^4*h^4/(32*meanSideArea^6); D4=A^4*h^4/(8*meanAllArea^6).",
        "qualifiedActualBadTets":len(entries),"modelledExactWRFaces":len(face_models),"modelledExactChildren":len(models),
        "omittedExactNecessaryModels":omitted,"history":history,"exactSeparationCertificate":certificate,
        "candidates":candidates,"allLocalFiveMetricPassingCandidateCount":sum(bool(x.get("allLocalFiveMetricsPass")) and bool(x.get("passesLocalGeometry")) and bool(x.get("passesLocalFiveMetricPareto")) for x in candidates),
        "entries":[{"cell":e["cell"],"baseFace":e["baseFace"],"selectedCount":e["selectedCount"],
          "areaSquaredExact":str(e["areaSquared"]),"heightAffineExact":[str(v) for v in e["height"]],
          "originalD":e["originalD"],"formulaOriginalD":determinant_formula(e,origin)} for e in entries],
        "allNecessaryRows":[{"rowExact":[str(v) for v in row],"derivation":meta} for row,meta in zip(rows,metadata)]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--maximum-rounds",type=int,default=12)
    args=parser.parse_args();doc=json.loads(args.input.read_text());out=analyze(doc,args.maximum_rounds)
    out["inputIdentity"]={"path":str(args.input),"bytes":args.input.stat().st_size,"sha256":hashlib.sha256(args.input.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,indent=2,allow_nan=False)+"\n")
    print(json.dumps({key:value for key,value in out.items() if key not in ("candidates","allNecessaryRows","history")},indent=2))

if __name__=="__main__":main()
