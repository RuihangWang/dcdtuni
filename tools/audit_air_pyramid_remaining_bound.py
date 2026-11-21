#!/usr/bin/env python3
"""Read-only remaining quad-cone classification and fixed-planar-base any-apex bounds."""
from __future__ import annotations
from fractions import Fraction as F
from pathlib import Path
from collections import Counter
import argparse,importlib.util,json,math
import numpy as np

def load_bound():
    path=Path(__file__).with_name("audit_air_tetra_all_selected_bound.py")
    spec=importlib.util.spec_from_file_location("tetbound_pyramid",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
B=load_bound();M=B.M

def vec(x):return tuple(F.from_float(float(v)) for v in x)
def add(a,b):return tuple(x+y for x,y in zip(a,b))
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def mul(a,k):return tuple(x*k for x in a)
def dot(a,b):return sum((x*y for x,y in zip(a,b)),F(0))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def gram(rows):return [[sum((p[i]*p[j] for p in rows),F(0)) for j in range(3)] for i in range(3)]
def det(a):return a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])+a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])

def planar_base(points):
    p=[vec(v) for v in points]
    if len(p)<3 or len(set(p))!=len(p):raise ValueError("distinct polygon nodes required")
    sf=mul(tuple(sum((cross(sub(p[i],p[0]),sub(p[i+1],p[0]))[j] for i in range(1,len(p)-1)),F(0)) for j in range(3)),F(1,2))
    area2=dot(sf,sf)
    if not area2:raise ValueError("nonzero planar base required")
    if any(dot(sf,sub(v,p[0])) for v in p):raise ValueError("exact planar base required")
    for i in range(len(p)):
        edge=sub(p[(i+1)%len(p)],p[i])
        if dot(cross(edge,sub(p[(i+2)%len(p)],p[(i+1)%len(p)])),sf)<=0:raise ValueError("strictly convex no-collinear base required")
        if any(dot(cross(edge,sub(v,p[i])),sf)<0 for v in p):raise ValueError("convex base required")
    return p,sf,area2

def cone_shape(points):
    p,sf,area2=planar_base(points);apex=add(p[0],sf)
    sides=[mul(cross(sub(p[(i+1)%len(p)],p[i]),sub(apex,p[i])),F(1,2)) for i in range(len(p))]
    sideK=det(gram(sides))/area2**2
    allK=det(gram([mul(sf,-1)]+sides))/area2**2
    if sideK<=0 or allK<=0:raise ValueError("rank-three cone tensor required")
    return p,sf,area2,sideK,allK

def fixed_polygon_cone_bound(points,physical_base,precision_bits=192):
    p,sf,area2,sideK,allK=cone_shape(points);scale=1<<precision_bits
    lengths2=[dot(sub(p[(i+1)%len(p)],p[i]),sub(p[(i+1)%len(p)],p[i])) for i in range(len(p))]
    lower=[F(math.isqrt(x.numerator*scale*scale//x.denominator),scale) for x in lengths2]
    perimeter=sum(lower,F(0));n=len(p)
    upper=F(8*n**6,27)*sideK/(area2*perimeter**4) if physical_base else F((n+1)**6,32)*allK/(area2*perimeter**4)
    return {"baseArity":n,"selectedFaceCount":n if physical_base else n+1,
            "physicalBaseExcluded":physical_base,"areaSquaredExactFraction":str(area2),
            "sideGramHeight4CoefficientExactFraction":str(sideK),
            "allGramHeight4CoefficientExactFraction":str(allK),
            "perimeterLowerExactFraction":str(perimeter),"perimeterPrecisionBits":precision_bits,
            "exactUpperBoundFraction":str(upper),"bound":float(upper),
            "anyApexNecessaryFails001":upper<F.from_float(.001),
            "necessaryOnlyNotFeasibility":True}

def raw_formula(points,apex,physical_base):
    p,sf,area2,sideK,allK=cone_shape(points);a=vec(apex)
    height2=dot(sf,sub(a,p[0]))**2/area2
    sides=[mul(cross(sub(p[(i+1)%len(p)],p[i]),sub(a,p[i])),F(1,2)) for i in range(len(p))]
    selected=sides if physical_base else [mul(sf,-1)]+sides
    side_area=sum(math.sqrt(float(dot(x,x))) for x in sides)
    total=side_area if physical_base else side_area+math.sqrt(float(area2))
    coefficient=sideK if physical_base else allK
    rawGram=det(gram(selected))
    assert rawGram==coefficient*height2**2
    predicted=len(selected)**6*float(rawGram)/(8*total**6)
    return {"heightSquaredExactFraction":str(height2),"rawGramDetExactFraction":str(rawGram),
            "projectionIndependentHeight4IdentityExact":True,"predictedDeterminant":predicted}

def audit(mesh):
    mesh=Path(mesh);rows=[];counts=Counter();identity={};parent_groups=set()
    ranks=sorted(int(p.name[9:]) for p in mesh.glob("processor[0-9]*") if p.name[9:].isdigit())
    if len(ranks)<2 or ranks!=list(range(len(ranks))):raise ValueError("actual contiguous MPI mesh required")
    for rank in ranks:
        geo=M.g.Geometry(mesh/f"processor{rank}/constant/polyMesh");trace=M.read_trace(mesh,rank)
        natpath=mesh/f"conforming-written-rank{rank}-point-native.i32"
        native=np.memmap(natpath,dtype="<i4",mode="r")
        if len(native)!=len(geo.points):raise ValueError("written native-point map size mismatch")
        bad=M.g.cell_set(geo.poly_dir/"sets/underdeterminedCells");counts["allActualDCells"]+=len(bad)
        order=np.argsort(trace["cellParent"],kind="stable");offs=np.r_[0,np.cumsum(np.bincount(trace["cellParent"],minlength=trace["sourceManifest"]["cells"]))]
        common_cache={}
        identity[str(rank)]={"written":{name:M.sha(geo.poly_dir/name) for name in ("points","faces","owner","neighbour","boundary","sets/underdeterminedCells")},
                             "trace":trace["identities"],"pointNativeSHA256":M.sha(natpath)}
        for c in map(int,bad):
            if B.tetrahedron_qualification(geo,c) is not None:continue
            raw=geo.cell(c);parent=int(trace["cellParent"][c]);parent_groups.add((rank,parent))
            counts["otherPolyCells"]+=1
            fs=raw[0];bases=[int(f) for f in fs if trace["faceBase"][f]>=0]
            arity=sorted(len(geo.ids(int(f))) for f in fs)
            if len(fs)!=5 or len(raw[5])!=5 or arity!=[3,3,3,3,4] or len(bases)!=1:
                raise ValueError("actual remaining polycell is outside the proven quad-pyramid classification")
            f=bases[0]
            if len(geo.ids(f))!=4:raise ValueError("unique original base is not quadrilateral")
            selected=B.selected_cell_faces(geo,fs);physical=f not in selected
            if len(selected)!=(4 if physical else 5):raise ValueError("actual selected mask not all sides with optional internal/coupled base")
            if parent not in common_cache:
                children=order[offs[parent]:offs[parent+1]].tolist()
                pointsets=[geo.cell(cc)[5] for cc in children]
                common_cache[parent]=(B.shared_apex_from_children(pointsets,native,trace["sourceManifest"]["points"]),children)
            ap,children=common_cache[parent]
            if ap not in raw[5] or ap in geo.ids(f):raise ValueError("actual shared apex incidence inconsistent")
            sf_rows=[geo.face(ff)[0] for ff in selected]
            actual=M.q.determinant(sf_rows)[0];base=int(trace["faceBase"][f])
            srcids=trace["vertices"][trace["offsets"][base]:trace["offsets"][base+1]]
            record={"rank":rank,"cell":c,"sourceParent":parent,"sourceBase":base,
                    "sourceBaseArity":len(srcids),"sourceBasePoints":trace["points"][srcids].tolist(),
                    "writtenFaces":fs.tolist(),"baseFace":f,"baseCoordinates":geo.points[geo.ids(f)].tolist(),
                    "selectedFaces":selected,"physicalBaseExcluded":physical,
                    "writtenApexPoint":ap,"writtenNativePoint":int(native[ap]),"actualWrittenApex":geo.points[ap].tolist(),
                    "allWrittenParentChildren":children,"actualWrittenDeterminant":actual,
                    "geometry":geo.classify(c),"notUnmodifiedOriginalCell":True}
            counts["physicalBaseQuadPyramids" if physical else "internalCoupledBaseQuadPyramids"]+=1
            try:
                bound=fixed_polygon_cone_bound(record["baseCoordinates"],physical)
                formula=raw_formula(record["baseCoordinates"],record["actualWrittenApex"],physical)
                record["fixedBaseAnyApexBound"]=bound;record["formulaAtActualWrittenApex"]=formula
                record["formulaVsActualAbsoluteError"]=abs(formula["predictedDeterminant"]-actual)
                counts["exactPlanarBoundQualified"]+=1
                if bound["anyApexNecessaryFails001"]:counts["certifiedAnyApexImpossible"]+=1
            except ValueError as err:
                record["unsupportedExactBoundReason"]=str(err);counts["exactBoundUnsupported"]+=1
            rows.append(record)
        print(json.dumps({"rank":rank,"counts":dict(counts)}),flush=True)
    counts["sourceParentGroups"]=len(parent_groups)
    return {"schema":"actual-remaining-quad-pyramid-bound-v1","meshPath":str(mesh),"counts":dict(counts),
            "rows":rows,"identity":identity,
            "scope":["Classification and exact necessary planar-base bounds only; not whole parent/case acceptance.",
                     "Every classified cell has the same unique new point shared by all current written children of its source parent.",
                     "An upper bound above .001 does not prove that any AP in the actual parent kernel is feasible.",
                     "No production C++/native/API/browser/threshold/mesh mutation by this audit."]}


def sqrt_lower_fraction(value,precision_bits=192):
    if value<0:raise ValueError("nonnegative radicand required")
    scale=1<<precision_bits
    return F(math.isqrt(value.numerator*scale*scale//value.denominator),scale)

def bounded_height_formula(points,physical_base,maximum_height_squared,precision_bits=192):
    """Necessary OF-D bound on 0 <= h <= hmax; no feasibility claim."""
    p,sf,area2,sideK,allK=cone_shape(points)
    h2=F(maximum_height_squared)
    if h2<0:raise ValueError("nonnegative maximum height required")
    lower_perimeter=F(fixed_polygon_cone_bound(points,physical_base,precision_bits)["perimeterLowerExactFraction"])
    optimum_h2=F(8 if physical_base else 32)*area2/lower_perimeter**2
    use_h2=min(h2,optimum_h2)
    n=len(points);coefficient=sideK if physical_base else allK
    s2=area2+use_h2*lower_perimeter**2/4
    if physical_base:
        upper=F(n**6,8)*coefficient*use_h2**2/s2**3
    else:
        total_lower=sqrt_lower_fraction(area2,precision_bits)+sqrt_lower_fraction(s2,precision_bits)
        upper=F((n+1)**6,8)*coefficient*use_h2**2/total_lower**6
    return {"maximumHeightSquaredExactFraction":str(h2),"unconstrainedOptimalHeightSquaredUsingLowerPerimeterExactFraction":str(optimum_h2),
            "maximumHeightBelowUnconstrainedOptimum":h2<optimum_h2,
            "evaluatedHeightSquaredExactFraction":str(use_h2),"exactUpperBoundFraction":str(upper),
            "bound":float(upper),"parentBoundNecessaryFails001":upper<F.from_float(.001),
            "necessaryOnlyNotFeasibility":True}

def source_parent_height_bound(trace,parent,points,actual_apex,physical_base):
    """Source-only hmax plus direct original-plane or convex-shell certificate."""
    p,sf,area2,*_=cone_shape(points)
    if dot(sf,sub(vec(actual_apex),p[0]))<0:sf=mul(sf,-1)
    fs=trace["sourceCellFaces"][trace["sourceCellOffsets"][parent]:trace["sourceCellOffsets"][parent+1]]
    shells=[];vertex_ids=set();edges={}
    for f in fs:
        ids=list(map(int,trace["vertices"][trace["offsets"][f]:trace["offsets"][f+1]]))
        if trace["owner"][f]!=parent:ids=ids[::-1]
        vertex_ids.update(ids);shells.append((int(f),ids))
        for a,b in zip(ids,ids[1:]+ids[:1]):
            key=tuple(sorted((a,b)));row=edges.setdefault(key,[0,0]);row[0]+=1;row[1]+=1 if a<b else -1
    vertices=[vec(trace["points"][v]) for v in sorted(vertex_ids)]
    signed=[dot(sf,sub(v,p[0])) for v in vertices]
    maximum=max(signed);h2=maximum**2/area2
    if maximum<=0:raise ValueError("source parent has no positive inward height")
    closure=all(v==[2,0] for v in edges.values())
    convex=True;all_planar=True;plane_witness=[]
    for f,ids in shells:
        vs=[vec(trace["points"][v]) for v in ids]
        normal=mul(tuple(sum((cross(sub(vs[i],vs[0]),sub(vs[i+1],vs[0]))[j] for i in range(1,len(vs)-1)),F(0)) for j in range(3)),F(1,2))
        if dot(normal,normal)==0 or any(dot(normal,sub(v,vs[0])) for v in vs):
            all_planar=False;convex=False;continue
        if any(dot(normal,sub(v,vs[0]))>0 for v in vertices):convex=False
        if cross(normal,sf)==(F(0),F(0),F(0)) and dot(normal,sf)>0 and dot(sf,sub(vs[0],p[0]))==maximum:
            plane_witness.append(f)
    # For a direct hmax plane, AP satisfying that original halfspace necessarily h<=hmax.
    # Alternatively, all source vertices satisfy every outward original face plane and the
    # closed shell certifies a convex source cell. No unproved general star/hull premise.
    certified=closure and all_planar and (bool(plane_witness) or convex)
    result={"sourceParent":parent,"sourceFaceLabels":[f for f,_ in shells],"sourceVertexLabels":sorted(vertex_ids),
            "sourceOriginalVertexCoordinates":[list(map(float,v)) for v in vertices],
            "sourceDirectedEdgesClosedExactly":closure,"allOriginalSourceFacesExactlyPlanar":all_planar,
            "allSourceVerticesWithinAllOutwardFacePlanes":convex,"directOriginalHeightCapFaceLabels":plane_witness,
            "sourceOnlyHeightCapCertified":certified,"maximumSourceVertexInwardHeightM":math.sqrt(float(h2)),
            "maximumHeightSquaredExactFraction":str(h2),
            "scope":"Only an original outward halfspace cap or a closed convex source shell certifies the parent bound; no new AP is used as an original vertex."}
    if certified:result["parentKernelNecessaryBound"]=bounded_height_formula(points,physical_base,h2)
    else:result["unsupportedHeightCertificateReason"]="No exact original cap plane/closed convex shell certificate"
    return result

def audit_parent_heights(document):
    result=dict(document);result["rows"]=[dict(row) for row in document["rows"]];summary=Counter()
    mesh=Path(document["meshPath"])
    for rank in sorted({row["rank"] for row in result["rows"]}):
        trace=M.read_trace(mesh,rank);live=np.flatnonzero(trace["faceAlive"])
        cellids=np.concatenate((trace["owner"][live],trace["neighbour"][live]))
        faceids=np.tile(live,2);ok=(cellids>=0)&(cellids<trace["sourceManifest"]["cells"])
        order=np.argsort(cellids[ok],kind="stable");cells=cellids[ok][order]
        trace["sourceCellFaces"]=faceids[ok][order]
        trace["sourceCellOffsets"]=np.r_[0,np.cumsum(np.bincount(cells,minlength=trace["sourceManifest"]["cells"]))]
        for row in result["rows"]:
            if row["rank"]!=rank or "fixedBaseAnyApexBound" not in row:continue
            bound=source_parent_height_bound(trace,row["sourceParent"],row["baseCoordinates"],row["actualWrittenApex"],row["physicalBaseExcluded"])
            row["sourceParentHeightBound"]=bound;summary["evaluatedExactPlanarBases"]+=1
            summary["heightCapCertified" if bound["sourceOnlyHeightCapCertified"] else "heightCapUnsupported"]+=1
            if bound["sourceOnlyHeightCapCertified"]:
                b=bound["parentKernelNecessaryBound"]
                summary["heightBelowFreeOptimum"]+=b["maximumHeightBelowUnconstrainedOptimum"]
                summary["parentBoundNecessaryFails001"]+=b["parentBoundNecessaryFails001"]
        print(json.dumps({"heightRank":rank,"counts":dict(summary)}),flush=True)
    result["parentHeightSummary"]=dict(summary)
    result["schema"]="actual-remaining-quad-pyramid-bound-with-parent-height-v1"
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh",type=Path);parser.add_argument("--parent-heights-from",type=Path);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.parent_heights_from:result=audit_parent_heights(json.loads(args.parent_heights_from.read_text()))
    elif args.mesh:result=audit(args.mesh)
    else:parser.error("--mesh or --parent-heights-from required")
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result["counts"]),flush=True)
if __name__=="__main__":main()
