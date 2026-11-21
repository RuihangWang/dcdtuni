#!/usr/bin/env python3
"""Read-only exact signed-outline oracle for saved native planar partition witnesses.

Float64 coordinates are interpreted as their exact binary rational values.
No native mesh, solver, threshold, source point, or candidate is modified.
"""
from __future__ import annotations
import argparse
from collections import Counter
from fractions import Fraction as F
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path

def rational(x):
    x=float(x)
    if not math.isfinite(x): raise ValueError("nonfinite coordinate")
    return F.from_float(x)

def sub(a,b): return tuple(x-y for x,y in zip(a,b))
def dot(a,b): return sum((x*y for x,y in zip(a,b)),F(0))
def cross3(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def cross2(a,b): return a[0]*b[1]-a[1]*b[0]
def orient(a,b,c): return cross2(sub(b,a),sub(c,a))
def edges(p): return zip(p,p[1:]+p[:1])
def sign(x): return 1 if x>0 else -1 if x<0 else 0
def on_segment(a,b,p):
    return orient(a,b,p)==0 and all(min(a[i],b[i])<=p[i]<=max(a[i],b[i]) for i in range(2))
def proper_cross(a,b,c,d):
    return orient(a,b,c)*orient(a,b,d)<0 and orient(c,d,a)*orient(c,d,b)<0
def intersects(a,b,c,d):
    return proper_cross(a,b,c,d) or any([on_segment(a,b,c),on_segment(a,b,d),on_segment(c,d,a),on_segment(c,d,b)])

def require_simple(p):
    if len(p)<3 or len(set(p))!=len(p): raise ValueError("duplicate/insufficient outline vertices")
    ee=list(edges(p))
    for i,(a,b) in enumerate(ee):
        prev=p[(i-1)%len(p)]
        # Retained collinear points may split a straight edge, but never reverse it.
        if orient(prev,a,b)==0 and dot(sub(a,prev),sub(b,a))<=0:
            raise ValueError("adjacent collinear backtrack")
        for j in range(i+1,len(ee)):
            if j==i+1 or (i==0 and j==len(ee)-1): continue
            if intersects(a,b,*ee[j]): raise ValueError("nonadjacent crossing/touch/overlap")

def area_moment(p):
    """Signed area and first moment directly from the original oriented outline."""
    aa=sum((cross2(a,b) for a,b in edges(p)),F(0))/2
    if aa==0: raise ValueError("zero polygon area")
    mx=sum(((a[0]+b[0])*cross2(a,b) for a,b in edges(p)),F(0))/6
    my=sum(((a[1]+b[1])*cross2(a,b) for a,b in edges(p)),F(0))/6
    return aa,(mx,my)

def closed_inside(p,polygon):
    if any(on_segment(a,b,p) for a,b in edges(polygon)): return True
    winding=0
    for a,b in edges(polygon):
        if a[1]<=p[1]<b[1] and orient(a,b,p)>0: winding+=1
        if b[1]<=p[1]<a[1] and orient(a,b,p)<0: winding-=1
    return winding!=0

def triangle_intersection_area(a,b):
    """Exact convex clipping; boundary-only intersections have zero area."""
    output=list(a)
    if orient(*b)<0: b=list(reversed(b))
    for u,v in edges(b):
        source=output;output=[]
        if not source: break
        prev=source[-1];dp=orient(u,v,prev)
        for cur in source:
            dc=orient(u,v,cur)
            if (dp<0)!=(dc<0):
                t=dp/(dp-dc)
                output.append(tuple(prev[k]+t*(cur[k]-prev[k]) for k in range(2)))
            if dc>=0: output.append(cur)
            prev=cur;dp=dc
    if len(output)<3: return F(0)
    return abs(sum((cross2(u,v) for u,v in edges(output)),F(0))/2)

def outline_oracle(points):
    xyz=[tuple(map(rational,p)) for p in points]
    if any(len(p)!=3 for p in xyz): raise ValueError("not xyz")
    if len(set(xyz))!=len(xyz): raise ValueError("duplicate original point")
    area=tuple(sum((v[k] for v in [cross3(a,b) for a,b in edges(xyz)]),F(0))/2 for k in range(3))
    drop=max(range(3),key=lambda k:abs(area[k]))
    if area[drop]==0: raise ValueError("zero vector area")
    if any(dot(area,sub(p,xyz[0]))!=0 for p in xyz):
        raise ValueError("not exactly planar; exact planar proof unavailable")
    keep=[k for k in range(3) if k!=drop]
    xy=[tuple(p[k] for k in keep) for p in xyz]
    require_simple(xy)
    aa,moment=area_moment(xy)
    centre=[F(0)]*3
    for k,m in zip(keep,moment): centre[k]=m/aa
    centre[drop]=xyz[0][drop]-sum((area[k]*(centre[k]-xyz[0][k]) for k in keep),F(0))/area[drop]
    return {"xyz":xyz,"xy":xy,"area":area,"projectedArea":aa,"moment":moment,
            "centre":tuple(centre),"keep":keep,"drop":drop}

def validate_coverage(oracle,pieces):
    xyz=oracle["xyz"]; outline=oracle["xy"]; winding=sign(oracle["projectedArea"])
    lookup={p:i for i,p in enumerate(xyz)}
    ids=[];triangles=[];sum_area=F(0);sum_m=[F(0),F(0)]
    for raw in pieces:
        q=[tuple(map(rational,p)) for p in raw]
        if any(p not in lookup for p in q): raise ValueError("partition adds/moves original point")
        pp=[tuple(p[k] for k in oracle["keep"]) for p in q]
        require_simple(pp)
        aa,mm=area_moment(pp)
        if sign(aa)!=winding: raise ValueError("piece reverses original winding")
        if any(winding*orient(a,b,p)<0 for a,b in edges(pp) for p in pp):
            raise ValueError("piece is not convex")
        ids.append([lookup[p] for p in q]);sum_area+=aa
        for k in range(2):sum_m[k]+=mm[k]
        for i in range(1,len(pp)-1):
            t=[pp[0],pp[i],pp[i+1]]
            if orient(*t)==0: continue # A convex polygon may retain collinear outline points.
            if sign(orient(*t))!=winding: raise ValueError("fan orientation mismatch")
            if not closed_inside(tuple(sum((p[k] for p in t),F(0))/3 for k in range(2)),outline):
                raise ValueError("triangle interior outside original outline")
            for a,b in edges(t):
                if not closed_inside(tuple((a[k]+b[k])/2 for k in range(2)),outline):
                    raise ValueError("triangle edge leaves original outline")
                if any(proper_cross(a,b,c,d) for c,d in edges(outline)):
                    raise ValueError("triangle crosses original outline")
            triangles.append(t)
    boundary=Counter((i,(i+1)%len(xyz)) for i in range(len(xyz)))
    all_edges=Counter((a,b) for p in ids for a,b in edges(p))
    remaining=all_edges.copy()
    for e,n in boundary.items():
        if remaining[e]!=n: raise ValueError("original boundary edge not exactly preserved")
        remaining[e]-=n
    if any(n!=remaining[(b,a)] or n!=1 for (a,b),n in remaining.items() if n):
        raise ValueError("new diagonal edges not paired once oppositely")
    if set(i for p in ids for i in p)!=set(range(len(xyz))):
        raise ValueError("original vertex omitted")
    overlaps=[triangle_intersection_area(a,b) for a,b in combinations(triangles,2)]
    if any(x>0 for x in overlaps): raise ValueError("positive interior overlap")
    if sum_area!=oracle["projectedArea"] or tuple(sum_m)!=oracle["moment"]:
        raise ValueError("signed area/first moments do not equal original outline")
    return {"pieces":len(pieces),"nonzeroVerificationTriangles":len(triangles),
            "exactSignedAreaAndFirstMomentsEqual":True,
            "allPiecesConvex":True,"allOriginalBoundaryEdgesOnce":True,
            "newDiagonalEdgesOnceInEachDirection":True,"allOriginalVerticesRetained":True,
            "maximumExactPairIntersectionAreaM2":float(max(overlaps,default=0)),
            "allTriangleInteriorsAndEdgesInsideOriginalOutline":True}

def as_float(v): return [float(x) for x in v]
def length(v):return math.sqrt(sum(x*x for x in v))
def canonical(points):
    q=[tuple(float(x).hex() for x in p) for p in points]
    return min(tuple(p[i:]+p[:i]) for p in [q,q[::-1]] for i in range(len(p)))

def analyse(row):
    oracle=outline_oracle(row["points"])
    coverage=validate_coverage(oracle,row["pieces"])
    centre=oracle["centre"];area=oracle["area"]
    old=tuple(map(rational,row["oldOFcentre"]))
    shift=sub(centre,old);normal=as_float(area);norm=length(normal)
    normal_shift=float(dot(area,shift))/norm
    total_shift=length(as_float(shift))
    span=max(max(p[k] for p in oracle["xyz"])-min(p[k] for p in oracle["xyz"]) for k in range(3))
    pyramids={}
    for name in ["ownerApex","neighbourApex"]:
        if name=="neighbourApex" and row["neighbour"]<0:continue
        apex=tuple(map(rational,row[name]))
        signed_v=dot(area,sub(centre,apex))/3
        old_v=dot(area,sub(old,apex))/3
        piece_v=sum((dot(outline_oracle(p)["area"],sub(outline_oracle(p)["centre"],apex))/3 for p in row["pieces"]),F(0))
        pyramids[name]={"signedOriginalVolumeM3":float(signed_v),"oldOFVolumeM3":float(old_v),
             "exactPartitionVolumeEqualsSignedOriginal":piece_v==signed_v,
             "oldOFMinusSignedVolumeM3":float(old_v-signed_v),
             "oldOFRelativeVolumeDifference":float(abs(old_v-signed_v)/abs(signed_v)) if signed_v else None,
             "apexOrientedAreaDotDistanceSign":sign(signed_v)}
    native_errors={name:length(sub(as_float(centre),row[name])) for name in ["pieceCentre","signedCentre"]}
    return {"face":row["face"],"owner":row["owner"],"neighbour":row["neighbour"],"patch":row["patch"],"zone":row["zone"],
        "vertexCount":len(row["points"]),"exactlyPlanarAndSimpleOriginalOutline":True,
        "normalAxis":oracle["drop"],"signedOutlineAreaVectorM2":as_float(area),
        "signedOutlineCentreM":as_float(centre),"oldOFcentreM":row["oldOFcentre"],
        "signedMinusOldOFCentreM":as_float(shift),
        "oldOFCentreShiftM":total_shift,"oldOFCentreShiftRelativeToMaxAxisSpan":total_shift/float(span),
        "normalCentreShiftM":normal_shift,
        "tangentialCentreShiftM":math.sqrt(max(0,total_shift*total_shift-normal_shift*normal_shift)),
        "candidateCentreDistanceToIndependentSignedOracleM":native_errors,
        "coverage":coverage,"pyramidVolumeComparison":pyramids}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--witness-dir",type=Path,required=True)
    ap.add_argument("--old-comparison",type=Path);ap.add_argument("--output",type=Path,required=True);args=ap.parse_args()
    paths=sorted(args.witness_dir.glob("conforming-cone-partition-rank*.json"))
    if not paths:raise ValueError("no witness inputs")
    old={}
    if args.old_comparison:
        for rank in json.loads(args.old_comparison.read_text())["ranks"]:
            for row in rank["faces"]:old[(rank["rank"],canonical(row["points"]))]=row["face"]
    reports=[]
    for p in paths:
        rank=int(p.stem.split("rank")[-1])
        for row in json.loads(p.read_text()):
            result=analyse(row);result["rank"]=rank
            result["sameRankExactCoordinateMatchToOldRemainingFace"]=old.get((rank,canonical(row["points"])))
            reports.append(result)
    all_v=[v for r in reports for v in r["pyramidVolumeComparison"].values()]
    out={"format":"native-planar-partition-signed-outline-audit/v1",
      "scope":"Read-only exact-rational audit of saved native candidate polygons; no mesh quality or product acceptance.",
      "inputSha256":{p.name:sha(p) for p in paths},
      "sourceSha256":{Path(__file__).name:sha(Path(__file__))},
      "method":"Original float64 coordinates are exact binary rationals. Strict simple outline, exact planarity, signed shoelace area/first moment. Candidate coverage separately proves convex pieces, original directed boundary, paired diagonals, positive triangle interiors, zero pair interior intersection, and exact signed area/first-moment equality.",
      "summary":{"faces":len(reports),"pieces":sum(r["coverage"]["pieces"] for r in reports),
        "physicalFaces":sum(r["patch"]>=0 for r in reports),
        "internalFaces":sum(r["neighbour"]>=0 for r in reports),
        "allExactlyPlanarAndSimple":all(r["exactlyPlanarAndSimpleOriginalOutline"] for r in reports),
        "allPartitionsExactlyCoverOriginalSignedOutline":all(r["coverage"]["exactSignedAreaAndFirstMomentsEqual"] for r in reports),
        "exactOldCoordinateMatches":sum(r["sameRankExactCoordinateMatchToOldRemainingFace"] is not None for r in reports),
        "maximumOldOFCentreTangentialShiftM":max(r["tangentialCentreShiftM"] for r in reports),
        "maximumOldOFCentreNormalShiftM":max(abs(r["normalCentreShiftM"]) for r in reports),
        "maximumNativePieceCentreDistanceToIndependentOracleM":max(r["candidateCentreDistanceToIndependentSignedOracleM"]["pieceCentre"] for r in reports),
        "maximumNativeSignedCentreDistanceToIndependentOracleM":max(r["candidateCentreDistanceToIndependentSignedOracleM"]["signedCentre"] for r in reports),
        "allPartitionPyramidVolumesExactlyEqualSignedOriginal":all(v["exactPartitionVolumeEqualsSignedOriginal"] for v in all_v),
        "maximumOldOFRelativePyramidVolumeDifference":max(v["oldOFRelativeVolumeDifference"] or 0 for v in all_v)},
      "limitations":["OF abs-weighted centroid of a concave face may differ tangentially from the true signed-outline centroid. This audit compares both to the independently derived original outline.",
        "Exact partition equality holds for geometry from these original planar outlines; a few-ulp native normal-coordinate difference is reported without changing any threshold.",
        "A corrected tangential face moment can change OpenFOAM reconstructed cell centres and therefore numerical coefficients; unchanged face normal and volume do not prove unchanged discretization.",
        "No native mutation, full checkMesh, processor/cyclic face ordering, deadline, field convergence, or physical case acceptance is demonstrated by this audit.",
        "No inference about unsaved surrounding cell faces or global mesh quality follows from these 21 base witnesses."],
      "faces":reports}
    if args.old_comparison:out["oldComparisonSha256"]=sha(args.old_comparison)
    args.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps(out["summary"],indent=2))
if __name__=="__main__": main()
