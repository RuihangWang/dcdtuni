#!/usr/bin/env python3
"""Read-only source-parent grouping and exact physical-triangle necessary bounds."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import importlib.util
import json
from math import isqrt
from pathlib import Path
import numpy as np

def load(name):
    p = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

q = load("audit_air_cone_remaining_quality.py")
fan = load("audit_air_canonical_fan_merge.py")
g = q.g

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()

def triangle_strong_bound(points, precision_bits=192):
    """54*A^2/P^4 with a rigorous rational lower bound for P.

    Side areas obey S >= sqrt(A^2 + h^2 P^2/4), even when the
    apex projection is outside. Maximizing x^2/(1+x)^3 gives 4/27.
    This branch requires exactly three selected internal side faces and
    a fixed noncoupled physical triangular base. It is not a mesh verdict.
    """
    if len(points) != 3:
        raise ValueError("fixed triangular base required")
    p = [[Fraction.from_float(float(x)) for x in row] for row in points]
    u = [p[1][j]-p[0][j] for j in range(3)]
    v = [p[2][j]-p[0][j] for j in range(3)]
    cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
    a2 = sum(x*x for x in cross)/4
    lengths2 = [sum((p[i][j]-p[(i+1)%3][j])**2 for j in range(3)) for i in range(3)]
    scale = 1 << precision_bits
    lower = [Fraction(isqrt((x.numerator*scale*scale)//x.denominator), scale) for x in lengths2]
    perimeter_lower = sum(lower)
    if a2 <= 0 or not perimeter_lower:
        raise ValueError("nondegenerate triangle required")
    bound = 54*a2/perimeter_lower**4
    old = Fraction(729,32)*a2/max(lengths2)**2
    return {"bound": float(bound), "exactUpperBoundFraction": str(bound),
            "perimeterLowerBoundFraction": str(perimeter_lower),
            "perimeterSqrtPrecisionBits": precision_bits,
            "edgeLengthLowerFractions": [str(x) for x in lower],
            "edgeLengthSquaredExactFractions": [str(x) for x in lengths2],
            "oldLongestEdgeBound": float(old), "oldExactUpperBoundFraction": str(old),
            "strongNecessaryFails001": bound < Fraction(1,1000),
            "oldNecessaryFails001": old < Fraction(1,1000)}

def face_relation(owner_parent, neighbour_parent, owner_base, neighbour_base):
    if neighbour_parent is None:
        return "boundaryOrCoupledNotLocallyPaired"
    if owner_parent != neighbour_parent:
        return "crossParentInterface"
    if owner_base >= 0 and owner_base == neighbour_base:
        return "sameParentSameSourceBaseAPInvariant"
    if owner_base >= 0 and neighbour_base >= 0:
        return "sameParentDifferentSourceBase"
    return "sameParentBaseIdentityAmbiguous"

def read_trace(mesh, rank):
    source = mesh/f"conforming-source-rank{rank}-"
    written = mesh/f"conforming-written-rank{rank}-"
    sm = json.loads(Path(str(source)+"manifest.json").read_text())
    wm = json.loads(Path(str(written)+"manifest.json").read_text())
    if (sm.get("format") != "conforming-input-stage/v1" or
        wm.get("format") != "conforming-written-stage/v1" or
        sm.get("rank") != rank or wm.get("rank") != rank or
        not sm.get("littleEndian") or sm.get("labelBytes") != 4 or sm.get("scalarBytes") != 8 or
        sm.get("pointStrideBytes") != 24 or wm.get("labelBytes") != 4):
        raise ValueError("unsupported trace identity or storage")
    identities = {Path(str(source)+"manifest.json").name:sha(Path(str(source)+"manifest.json")),
                  Path(str(written)+"manifest.json").name:sha(Path(str(written)+"manifest.json"))}
    def arr(prefix, suffix, dtype, shape):
        path = Path(str(prefix)+suffix)
        if path.stat().st_size != int(np.prod(shape))*np.dtype(dtype).itemsize:
            raise ValueError(f"trace size mismatch: {path.name}")
        identities[path.name] = sha(path)
        return np.memmap(path, mode="r", dtype=dtype, shape=shape)
    return {"sourceManifest": sm, "writtenManifest": wm, "identities": identities,
            "points": arr(source,"points.f64","<f8",(sm["points"],3)),
            "offsets": arr(source,"face-offsets.u64","<u8",(sm["faces"]+1,)),
            "vertices": arr(source,"face-points.i32","<i4",(sm["facePointLabels"],)),
            "owner": arr(source,"owner.i32","<i4",(sm["faces"],)),
            "neighbour": arr(source,"neighbour.i32","<i4",(sm["faces"],)),
            "faceAlive": arr(source,"face-alive.u8","u1",(sm["faces"],)),
            "cellAlive": arr(source,"cell-alive.u8","u1",(sm["cells"],)),
            "apex": arr(source,"apex.f64","<f8",(sm["cells"],3)),
            "cellParent": arr(written,"cell-parent.i32","<i4",(wm["cells"],)),
            "faceBase": arr(written,"face-base.i32","<i4",(wm["faces"],))}

def cell_base_mapping(geo, trace):
    bases = trace["faceBase"]
    valid = np.flatnonzero(bases >= 0)
    minimum = np.full(geo.n_cells, np.iinfo(np.int32).max, dtype=np.int32)
    maximum = np.full(geo.n_cells, -1, dtype=np.int32)
    first = np.full(geo.n_cells, geo.n_faces, dtype=np.int64)
    for cell_ids, fids in ((geo.owner[valid],valid),
        (geo.neighbour[valid[valid < len(geo.neighbour)]],valid[valid < len(geo.neighbour)])):
        np.minimum.at(minimum,cell_ids,bases[fids])
        np.maximum.at(maximum,cell_ids,bases[fids])
        np.minimum.at(first,cell_ids,fids)
    unique = (minimum == maximum) & (maximum >= 0)
    return np.where(unique,minimum,-1),first

def source_parent_kernel(trace, parent):
    if "sourceCellOffsets" in trace:
        ids=trace["sourceCellFaces"][trace["sourceCellOffsets"][parent]:trace["sourceCellOffsets"][parent+1]]
    else:
        ids = np.flatnonzero((trace["faceAlive"] != 0) &
                            ((trace["owner"] == parent) | (trace["neighbour"] == parent)))
    normal_rows = []
    distance_rows = []
    face_rows = []
    axis_only = True
    for f in ids:
        vertices = trace["vertices"][trace["offsets"][f]:trace["offsets"][f+1]]
        p = np.asarray(trace["points"][vertices])
        if trace["owner"][f] != parent:
            p = p[::-1]
        sf, fc = fan.face_geometry(p)
        normal = sf/np.linalg.norm(sf)
        normal_rows.append(normal);distance_rows.append(float(np.dot(normal,fc)))
        dominant=int(np.argmax(abs(normal)))
        scale=max(float(np.linalg.norm(point-fc)) for point in p)
        axis_valid=(np.max(abs(np.delete(normal,dominant)))<=1e-12 and
                    np.max(abs(p[:,dominant]-fc[dominant]))<=1e-10*max(scale,1e-6))
        axis_only=axis_only and axis_valid
        face_rows.append({"sourceFace":int(f),"normal":normal.tolist(),"centre":fc.tolist(),
                          "axisConditionMatchesNative":bool(axis_valid)})
    normals = np.asarray(normal_rows);distances = np.asarray(distance_rows)
    original_apex = np.asarray(trace["apex"][parent])
    lower = np.full(3,-np.inf);upper = np.full(3,np.inf)
    axis_count = 0
    for index, (n, d) in enumerate(zip(normals,distances)):
        axis = int(np.argmax(abs(n)))
        if np.max(abs(np.delete(n,axis))) > 1e-12:
            continue
        axis_count += 1
        bound = face_rows[index]["centre"][axis]
        if n[axis] > 0:
            upper[axis] = min(upper[axis],bound)
        else:
            lower[axis] = max(lower[axis],bound)
    bounded = bool(np.isfinite(lower).all() and np.isfinite(upper).all() and (upper>lower).all())
    centre = (lower+upper)/2 if bounded else None
    return {"inputParent":int(parent),"sourceFaceCount":len(ids),
            "sourceApex":original_apex.tolist(),
            "originalApexMinimumPlaneMarginM":float(np.min(distances-normals@original_apex)),
            "axisAlignedSourceFaces":axis_count,"allSourceFacesAxisAligned":bool(axis_only and axis_count==len(ids)),
            "axisKernelLower":lower.tolist() if np.isfinite(lower).all() else None,
            "axisKernelUpper":upper.tolist() if np.isfinite(upper).all() else None,
            "axisBoxCentre":centre.tolist() if centre is not None else None,
            "boxCentreMinimumAllOriginalPlaneMarginM":float(np.min(distances-normals@centre)) if centre is not None else None,
            "boxCentreIsCandidateOnly":True,
            "sourceApexEqualsNativeAxisCentre":bool(centre is not None and axis_only and
              np.linalg.norm(centre-original_apex)<=1e-12*max(float(np.linalg.norm(upper-lower)),1e-6)),
            "originalPlanes":face_rows}

def signed_planar_reference(outline, points):
    loop=fan.canonical_cycle(outline,points)
    xyz=np.asarray([points[i] for i in loop])
    sf,of_centre=fan.face_geometry(xyz);area=float(np.linalg.norm(sf))
    if area<=fan.AREA_EPS or not np.isfinite(xyz).all():raise ValueError("invalid original polygon area")
    normal=sf/area
    if np.max(abs((xyz-xyz[0])@normal))>fan.PLANAR_EPS:raise ValueError("nonplanar source polygon")
    # Reject nonadjacent crossings, touches, and collinear overlap.
    def side(a,b,c):return float(np.dot(np.cross(b-a,c-a),normal))
    def on(a,b,c):
        e=b-a;length2=float(np.dot(e,e))
        return abs(side(a,b,c))<=fan.TURN_EPS and -1e-12<=float(np.dot(c-a,e))/length2<=1+1e-12
    n=len(xyz)
    for i in range(n):
        for j in range(i+1,n):
            if j==(i+1)%n or i==(j+1)%n:continue
            a,b=xyz[i],xyz[(i+1)%n];c,d=xyz[j],xyz[(j+1)%n]
            if (side(a,b,c)*side(a,b,d)<0 and side(c,d,a)*side(c,d,b)<0) or on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b):
                raise ValueError("source polygon is not simple")
    origin=xyz[0];total=0.;moment=np.zeros(3)
    for j in range(1,n-1):
        amount=.5*float(np.dot(np.cross(xyz[j]-origin,xyz[j+1]-origin),normal))
        total+=amount;moment+=amount*(origin+xyz[j]+xyz[j+1])/3
    if abs(total-area)>1e-10*area:raise ValueError("signed outline area disagrees")
    return loop,normal,area,moment/total,of_centre

def strong_refan_polygon(outline,points,maximum_group_size=3):
    if not 3<=len(outline)<=8:raise ValueError("diagnostic enumeration requires 3..8 nodes")
    loop,normal,area,centre,ofcentre=signed_planar_reference(outline,points)
    candidates=[];attempts=0;positive=0;rejected=Counter()
    for k in range(len(loop)):
        rotated=loop[k:]+loop[:k]
        triangles=[[rotated[0],rotated[j],rotated[j+1]] for j in range(1,len(loop)-1)]
        if any(fan.polygon_profile(t,points,normal) is None for t in triangles):continue
        positive+=1
        for groups in fan.compositions(len(triangles)):
            if max(groups)>maximum_group_size:continue
            attempts+=1;bases=[];index=0
            for count in groups:
                bases.append([rotated[0]]+rotated[index+1:index+count+2]);index+=count
            profiles=[fan.polygon_profile(b,points,normal) for b in bases]
            if any(p is None for p in profiles):
                rejected["nonStrictConvexPiece"]+=1;continue
            if fan.boundary_edges(bases)!=fan.boundary_edges([loop]) or set(sum(bases,[]))!=set(loop):
                raise AssertionError("changed original outline nodes")
            area_error=abs(sum(p["area"] for p in profiles)-area)
            moment=sum((p["area"]*np.asarray(p["centre"]) for p in profiles),np.zeros(3))
            moment_error=float(np.linalg.norm(moment-area*centre))
            if area_error>1e-10*area or moment_error>1e-10*max(area,float(np.linalg.norm(area*centre))):
                raise AssertionError("did not preserve signed area and first moment")
            bounds=[{"piece":i,**triangle_strong_bound([points[k] for k in base])} for i,base in enumerate(bases) if len(base)==3]
            interfaces=fan.interfaces_profile(bases,profiles,points)
            w=min((x["weight"] for x in interfaces),default=1.);ratio=min((x["volumeRatio"] for x in interfaces),default=1.)
            eligible=not any(b["strongNecessaryFails001"] for b in bounds) and w>=.05 and ratio>=.01
            candidates.append({"anchor":rotated[0],"anchorCoordinates":list(points[rotated[0]]),
                               "groups":groups,"bases":bases,"profiles":profiles,"interfaces":interfaces,
                               "strongPhysicalTriBounds":bounds,"minimumWithinBaseWeight":w,
                               "minimumWithinBaseRatio":ratio,
                               "minimumAreaPerimeterShape":min(p["areaPerimeterShape"] for p in profiles),
                               "areaError":area_error,"signedMomentError":moment_error,
                               "eligibleNecessaryOnly":eligible})
    eligible=[c for c in candidates if c["eligibleNecessaryOnly"]]
    eligible.sort(key=lambda c:(-c["minimumAreaPerimeterShape"],-c["minimumWithinBaseWeight"],
                                -c["minimumWithinBaseRatio"],len(c["bases"]),tuple(c["anchorCoordinates"]),tuple(c["groups"])))
    return {"originalOutline":loop,"originalCoordinates":[list(points[i]) for i in loop],
            "reference":{"area":area,"signedAreaCentre":centre.tolist(),"originalOFAreaCentre":ofcentre.tolist(),
                         "signedVsOFCentreDifferenceM":float(np.linalg.norm(centre-ofcentre))},
            "maximumContiguousFanGroup":maximum_group_size,"positiveAnchors":positive,
            "partitionAttempts":attempts,"rejected":dict(rejected),
            "strictGeometryCandidates":len(candidates),"necessaryEligibleCount":len(eligible),
            "candidates":candidates,"selected":eligible[0] if eligible else None,
            "wholeParentAcceptance":False}

def general_refan_polygon(outline,points,maximum_triangles_per_piece=3,physical_base=True):
    """All valid ear triangulations plus connected strictly convex unions, n<=8."""
    if not 3<=len(outline)<=8:raise ValueError("small-polygon topology enumeration requires 3..8 nodes")
    loop,normal,area,centre,ofcentre=signed_planar_reference(outline,points)
    xyz={k:np.asarray(points[k]) for k in loop};memo={}
    def triangle(loop3):
        profile=fan.polygon_profile(list(loop3),points,normal)
        return profile is not None
    def ears(active):
        active=tuple(active)
        if active in memo:return memo[active]
        if len(active)==3:
            ans={frozenset((tuple(sorted(active)),))} if triangle(active) else set()
        else:
            ans=set()
            for i,v in enumerate(active):
                tri=(active[i-1],v,active[(i+1)%len(active)])
                if not triangle(tri):continue
                coords=[xyz[k] for k in tri]
                def inside(q):
                    return all(float(np.dot(np.cross(coords[(j+1)%3]-coords[j],xyz[q]-coords[j]),normal))>=-fan.TURN_EPS for j in range(3))
                if any(inside(q) for q in active if q not in tri):continue
                remaining=active[:i]+active[i+1:]
                for rest in ears(remaining):
                    ans.add(rest|frozenset((tuple(sorted(tri)),)))
        memo[active]=ans
        return ans
    triangulations=ears(tuple(loop));partitions={};attempts=0
    def oriented(poly):
        poly=fan.canonical_cycle(list(poly),points)
        sf,_=fan.face_geometry(np.asarray([points[k] for k in poly]))
        return poly if float(np.dot(sf,normal))>0 else [poly[0]]+poly[:0:-1]
    bound_cache={}
    for tri_set in triangulations:
        tris=[oriented(t) for t in sorted(tri_set)]
        use=defaultdict(list)
        for i,tri in enumerate(tris):
            for a,b in zip(tri,tri[1:]+tri[:1]):use[tuple(sorted((a,b)))].append(i)
        shared=[indices for indices in use.values() if len(indices)==2]
        if len(shared)!=len(tris)-1:raise AssertionError("triangulation dual is not a tree")
        for mask in range(1<<len(shared)):
            parents=list(range(len(tris)))
            def find(a):
                while parents[a]!=a:a=parents[a]
                return a
            for j,(a,b) in enumerate(shared):
                if mask&(1<<j):parents[find(a)]=find(b)
            components=defaultdict(list)
            for i in range(len(tris)):components[find(i)].append(i)
            if any(len(c)>maximum_triangles_per_piece for c in components.values()):continue
            attempts+=1;bases=[];valid=True
            for component in components.values():
                try:base=oriented(fan.outline_from_partition([tris[i] for i in component],points))
                except ValueError:valid=False;break
                if fan.polygon_profile(base,points,normal) is None:valid=False;break
                bases.append(base)
            if not valid:continue
            key=tuple(sorted(tuple(tuple(points[k]) for k in base) for base in bases))
            if key in partitions:continue
            bases.sort(key=lambda base:tuple(tuple(points[k]) for k in base))
            if fan.boundary_edges(bases)!=fan.boundary_edges([loop]) or set(sum(bases,[]))!=set(loop):
                raise AssertionError("general triangulation changed original boundary")
            profiles=[fan.polygon_profile(b,points,normal) for b in bases]
            ar_error=abs(sum(x["area"] for x in profiles)-area)
            mo_error=float(np.linalg.norm(sum((x["area"]*np.asarray(x["centre"]) for x in profiles),np.zeros(3))-area*centre))
            if ar_error>1e-10*area or mo_error>1e-10*max(area,float(np.linalg.norm(area*centre))):
                raise AssertionError("general union changed signed area or moment")
            bounds=[]
            for i,base in enumerate(bases):
                if not physical_base or len(base)!=3:continue
                tkey=tuple(sorted(base))
                if tkey not in bound_cache:bound_cache[tkey]=triangle_strong_bound([points[k] for k in base])
                bounds.append({"piece":i,**bound_cache[tkey]})
            interfaces=fan.interfaces_profile(bases,profiles,points)
            w=min((x["weight"] for x in interfaces),default=1.)
            ratio=min((x["volumeRatio"] for x in interfaces),default=1.)
            eligible=not any(b["strongNecessaryFails001"] for b in bounds) and w>=.05 and ratio>=.01
            partitions[key]={"bases":bases,"profiles":profiles,"interfaces":interfaces,
                             "strongPhysicalTriBounds":bounds,"minimumWithinBaseWeight":w,
                             "minimumWithinBaseRatio":ratio,
                             "minimumAreaPerimeterShape":min(x["areaPerimeterShape"] for x in profiles),
                             "areaError":ar_error,"signedMomentError":mo_error,"eligibleNecessaryOnly":eligible,
                             "supportingTriangulation":tris}
    candidates=list(partitions.values())
    eligible=[x for x in candidates if x["eligibleNecessaryOnly"]]
    def coords_key(row):return tuple(tuple(tuple(points[k]) for k in base) for base in row["bases"])
    candidates.sort(key=coords_key)
    eligible.sort(key=lambda x:(-x["minimumAreaPerimeterShape"],-x["minimumWithinBaseWeight"],
                                -x["minimumWithinBaseRatio"],len(x["bases"]),coords_key(x)))
    return {"originalOutline":loop,"originalCoordinates":[list(points[i]) for i in loop],
            "reference":{"area":area,"signedAreaCentre":centre.tolist(),"originalOFAreaCentre":ofcentre.tolist()},
            "validTriangulations":len(triangulations),"connectedPartitionAttempts":attempts,
            "maximumTrianglesPerPiece":maximum_triangles_per_piece,
            "physicalTriangleBranchApplicable":physical_base,
            "strictGeometryCandidates":len(candidates),"necessaryEligibleCount":len(eligible),
            "candidates":candidates,"selected":eligible[0] if eligible else None,
            "wholeParentAcceptance":False}

def local_cone_profile(bases,points,apex,physical_base=True):
    profiles=[];cell_rows=[]
    for base in bases:
        coords=np.asarray([points[k] for k in base]);sf,fc=fan.face_geometry(coords)
        if np.dot(sf,np.asarray(apex)-fc)>0:
            base=[base[0]]+base[:0:-1];coords=np.asarray([points[k] for k in base])
        pp=np.vstack((coords,np.asarray(apex)));ap=len(coords)
        faces=[list(range(ap))]+[[(j+1)%ap,j,ap] for j in range(ap)]
        geo=g.Geometry.from_arrays(pp,faces);cc=geo.cell(0)
        determinant=q.determinant(cc[1][1:] if physical_base else cc[1])[0]
        boundary=q.face_metrics(cc[1][0],cc[2][0],cc[3],cc[4],coords) if physical_base else {"skewness":None}
        cell_rows.append({"base":base,"determinant":determinant,"volume":cc[4],"centre":cc[3].tolist(),
                          "boundarySkew":boundary["skewness"],"geometricGuards":geo.classify(0)})
        profiles.append((geo,cc))
    edges=defaultdict(list)
    for i,base in enumerate(bases):
        for a,b in zip(base,base[1:]+base[:1]):edges[tuple(sorted((a,b)))].append(i)
    face_rows=[]
    for (a,b),cells in edges.items():
        if len(cells)!=2:continue
        i,j=cells;coords=np.asarray([points[a],points[b],apex]);sf,fc=fan.face_geometry(coords)
        row=q.face_metrics(sf,fc,profiles[i][1][3],profiles[i][1][4],coords,profiles[j][1][3],profiles[j][1][4])
        row["edge"]=[a,b];row["pieces"]=[i,j];face_rows.append(row)
    reference_outline=fan.outline_from_partition(bases,points)
    _,normal,area,centre,_=signed_planar_reference(reference_outline,points)
    height=abs(float(np.dot(normal,np.asarray(apex)-centre)))
    expected_volume=area*height/3.
    expected_centre=(3*centre+np.asarray(apex))/4.
    actual_volume=sum(row["volume"] for row in cell_rows)
    actual_moment=sum((row["volume"]*np.asarray(row["centre"]) for row in cell_rows),np.zeros(3))
    expected_moment=expected_volume*expected_centre
    volume_error=abs(actual_volume-expected_volume)
    moment_error=float(np.linalg.norm(actual_moment-expected_moment))
    return {"sourceApex":list(apex),"cells":cell_rows,"withinBaseFaces":face_rows,
            "originalBaseConeConservation":{"referenceOutline":reference_outline,
              "referenceArea":area,"referenceSignedCentre":centre.tolist(),
              "expectedVolume":expected_volume,"actualVolume":actual_volume,
              "absoluteVolumeError":volume_error,
              "expectedFirstMoment":expected_moment.tolist(),"actualFirstMoment":actual_moment.tolist(),
              "absoluteFirstMomentError":moment_error,
              "relativeVolumeError":volume_error/max(expected_volume,np.finfo(float).tiny),
              "relativeFirstMomentError":moment_error/max(expected_volume*max(np.linalg.norm(expected_centre),1.),np.finfo(float).tiny)},
            "minimumLocalDeterminant":min(x["determinant"] for x in cell_rows),
            "minimumWithinBaseWeight":min((x["weight"] for x in face_rows),default=1.),
            "minimumWithinBaseRatio":min((x["volumeRatio"] for x in face_rows),default=1.),
            "maximumLocalSkew":max([x["boundarySkew"] for x in cell_rows if x["boundarySkew"] is not None]+[x["skewness"] for x in face_rows],default=None),
            "allLocalConeHalfspacesAndClosure":all(x["geometricGuards"]["classification"]=="convex-no-face-plane-trigger" for x in cell_rows),
            "omitsOtherOriginalBaseInterfacesAndWholeParent":True,
            "physicalBaseExcludedFromDeterminant":physical_base,
            "actualAcrossInternalBaseSkewAndWeightsOmitted":not physical_base}

def across_internal_refan_base(bases,points,owner_profile,neighbour_profile):
    """Actual two-source-AP cone centres across the unchanged internal base pieces."""
    if len(owner_profile["cells"])!=len(bases) or len(neighbour_profile["cells"])!=len(bases):
        raise ValueError("profiles must use the same common canonical partition")
    rows=[]
    for i,base in enumerate(bases):
        coords=np.asarray([points[k] for k in base]);sf,fc=fan.face_geometry(coords)
        owner=owner_profile["cells"][i];neighbour=neighbour_profile["cells"][i]
        row=q.face_metrics(sf,fc,np.asarray(owner["centre"]),owner["volume"],coords,
                          np.asarray(neighbour["centre"]),neighbour["volume"])
        d=np.asarray(neighbour["centre"])-np.asarray(owner["centre"])
        cosine=abs(float(np.dot(sf,d)))/(np.linalg.norm(sf)*np.linalg.norm(d))
        row["nonOrthogonality"]=float(np.degrees(np.arccos(np.clip(cosine,-1.,1.))))
        row["base"]=base;rows.append(row)
    return {"faces":rows,"minimumWeight":min(row["weight"] for row in rows),
            "minimumVolumeRatio":min(row["volumeRatio"] for row in rows),
            "maximumSkewness":max(row["skewness"] for row in rows),
            "maximumNonOrthogonality":max(row["nonOrthogonality"] for row in rows),
            "noWholeParentOrOtherSourceBaseAcceptance":True}

def audit_strong_refan(mesh,proof_path):
    original=json.loads(Path(proof_path).read_text())
    rows=[r for r in original["qualifiedPhysicalTetrahedra"] if r["strongNecessaryFails001"]]
    traces={rank:read_trace(Path(mesh),rank) for rank in sorted({r["rank"] for r in rows})}
    bases=[]
    for rank,base in sorted({(r["rank"],r["sourceBase"]) for r in rows}):
        t=traces[rank];ids=t["vertices"][t["offsets"][base]:t["offsets"][base+1]].tolist()
        points={int(i):t["points"][i].tolist() for i in ids}
        row={"rank":rank,"sourceBase":base,"sourcePointLabels":ids,
             "sourceCoordinates":[points[i] for i in ids],
             "triggeringActualTetrahedra":[r for r in rows if r["rank"]==rank and r["sourceBase"]==base]}
        try:
            row["refan"]=strong_refan_polygon(ids,points)
            if row["refan"]["selected"] is None:
                row["generalTriangulation"]=general_refan_polygon(ids,points)
            candidate=(row.get("generalTriangulation") or row["refan"])["selected"]
            if candidate is not None:
                parent=row["triggeringActualTetrahedra"][0]["sourceParent"]
                row["localActualSourceApexProfile"]=local_cone_profile(candidate["bases"],points,t["apex"][parent].tolist())
        except ValueError as error:row["unsupportedReason"]=str(error)
        bases.append(row)
    return {"schema":"strong-necessary-trigger-refan-candidates-v1","meshPath":str(mesh),
            "inputProofSha256":sha(proof_path),"originalBaseCount":len(bases),
            "bases":bases,"noNativeExecution":True,"noWholeParentAcceptance":True,
            "limitations":["Each triangle bound is necessary only; nontriangular D and all-child/outside quality remain required.",
                           "Signed polygon area/moment is the geometric oracle for simple concave original outlines; the old absolute OF centre is retained for audit.",
                           "Every original node and exterior directed edge is preserved; only common canonical internal fan partition changes.",
                           "Candidate enumeration uses group<=3 and <=8 original nodes; unsupported layouts are explicit."]}

def audit_invariant_same_base_refan(mesh,proof_path):
    original=json.loads(Path(proof_path).read_text())
    rows=[r for r in original["flaggedFaces"] if r["relation"]=="sameParentSameSourceBaseAPInvariant"]
    ranks=sorted({r["rank"] for r in rows})
    traces={rank:read_trace(Path(mesh),rank) for rank in ranks}
    bases=[]
    for rank,base in sorted({(r["rank"],r["ownerSourceBase"]) for r in rows}):
        t=traces[rank];ids=t["vertices"][t["offsets"][base]:t["offsets"][base+1]].tolist()
        points={int(i):t["points"][i].tolist() for i in ids}
        owners=[int(t["owner"][base])]
        if t["neighbour"][base]>=0:owners.append(int(t["neighbour"][base]))
        physical=len(owners)==1
        result=general_refan_polygon(ids,points,physical_base=physical)
        record={"rank":rank,"sourceBase":base,"sourcePointLabels":ids,
                "sourceCoordinates":[points[i] for i in ids],"originalSourceParents":owners,
                "actualFlaggedFaces":[r for r in rows if r["rank"]==rank and r["ownerSourceBase"]==base],
                "generalTriangulation":result,"physicalTriangleBoundNotUsedForInternalBase":not physical}
        if result["selected"]:
            record["sourceApexLocalProfiles"]=[{"parent":parent,**local_cone_profile(
                result["selected"]["bases"],points,t["apex"][parent].tolist(),physical_base=physical)} for parent in owners]
            if not physical:
                record["acrossOriginalInternalBaseProfile"]=across_internal_refan_base(
                    result["selected"]["bases"],points,*record["sourceApexLocalProfiles"])
        bases.append(record)
    return {"schema":"same-original-base-invariant-refan-v1","meshPath":str(mesh),
            "inputProofSha256":sha(proof_path),"bases":bases,
            "originalSourceBaseCount":len(bases),"flaggedFaceCount":len(rows),
            "noWholeParentAcceptance":True,
            "limitations":["Base D mask includes the internal/coupled base; physical-triangle any-apex exclusion is not applied here.",
                           "Common canonical partition is required on both original source parents.",
                           "Source AP profiles do not certify all external neighbours or across-base opposite-parent metrics."]}

def cheap_parent_buckets(mesh, derive_missing_kernel=False):
    mesh=Path(mesh);rows=[];bucket_groups=Counter();bucket_cells=Counter();identity={}
    for rank in range(8):
        trace=read_trace(mesh,rank)
        candidates={}
        cp=mesh/f"conforming-kernel-candidates-rank{rank}.jsonl"
        for line in cp.read_text().splitlines():
            item=json.loads(line)
            if item["parent"] in candidates:raise ValueError("duplicate candidate parent")
            candidates[item["parent"]]={k:item[k] for k in ("decision","original","candidate")}
        identity[str(rank)]={"sets":sha(mesh/f"processor{rank}/constant/polyMesh/sets/underdeterminedCells"),
                             "candidateJSONL":sha(cp),"parentTrace":trace["identities"][f"conforming-written-rank{rank}-cell-parent.i32"]}
        ids=g.cell_set(mesh/f"processor{rank}/constant/polyMesh/sets/underdeterminedCells")
        counts=Counter(map(int,trace["cellParent"][ids]))
        child_counts=np.bincount(trace["cellParent"],minlength=trace["sourceManifest"]["cells"])
        if derive_missing_kernel:
            alive=np.flatnonzero(trace["faceAlive"])
            internal=alive[trace["neighbour"][alive]>=0]
            labels=np.concatenate((trace["owner"][alive],trace["neighbour"][internal]))
            face_ids=np.concatenate((alive,internal))
            order=np.argsort(labels,kind="stable")
            trace["sourceCellFaces"]=face_ids[order]
            trace["sourceCellOffsets"]=np.r_[0,np.cumsum(np.bincount(labels,minlength=trace["sourceManifest"]["cells"]))]
        for parent,count in counts.items():
            if not 0<=parent<trace["sourceManifest"]["cells"] or not trace["cellAlive"][parent]:
                raise ValueError("invalid flagged source parent")
            candidate=candidates.get(parent);bucket=candidate["decision"] if candidate else "no-candidate-log"
            kernel=None
            if derive_missing_kernel and candidate is None:
                kernel=source_parent_kernel(trace,parent)
                if not kernel["allSourceFacesAxisAligned"]:bucket="no-log-non-axis-source"
                elif kernel["sourceApexEqualsNativeAxisCentre"]:bucket="no-log-already-axis-centre"
                elif kernel["axisBoxCentre"] is None or kernel["boxCentreMinimumAllOriginalPlaneMarginM"]<=3e-6:
                    bucket="no-log-axis-kernel-not-strict"
                else:bucket="no-log-axis-changed-other-early-skip"
            rows.append({"rank":rank,"sourceParent":parent,"badDChildren":count,
                         "totalWrittenChildren":int(child_counts[parent]),"candidateDecision":candidate,
                         "bucket":bucket,"kernel":kernel})
            bucket_groups[bucket]+=1;bucket_cells[bucket]+=count
        print(json.dumps({"rank":rank,"Dchildren":len(ids),"DparentGroups":len(counts)}),flush=True)
    return {"schema":"actual-refan-cheap-parent-buckets-v1","meshPath":str(mesh),
            "counts":{"badDChildren":sum(bucket_cells.values()),"sourceParentGroups":sum(bucket_groups.values()),
                      "groupsByBucket":dict(bucket_groups),"DchildrenByBucket":dict(bucket_cells)},
            "parentGroups":rows,"identity":identity,
            "limitations":["Missing native JSONL rows are not silently assumed to be unchanged.",
                           "Kernel classification uses actual source faces and native axis/unchanged tolerances, with no candidate acceptance.",
                           "Changed early-skip may include coupled or point-proximity rejection; this tool does not assign a fabricated native decision."]}

def audit(mesh, maximum_kernel_groups=12):
    mesh = Path(mesh)
    ranks = sorted(int(p.name[9:]) for p in mesh.glob("processor[0-9]*") if p.name[9:].isdigit())
    if ranks != list(range(len(ranks))) or len(ranks)<2:
        raise ValueError("contiguous actual MPI mesh ranks required")
    groups = defaultdict(lambda: {"cells": [], "weightFaces": [], "ratioFaces": []})
    faces = []
    physical = []
    identities = {}
    rank_counts = {}
    relations = {"lowWeightFaces": Counter(), "lowVolRatioFaces": Counter()}
    kernels = {}
    for rank in ranks:
        geo = g.Geometry(mesh/f"processor{rank}/constant/polyMesh")
        trace = read_trace(mesh,rank)
        if trace["writtenManifest"]["cells"] != geo.n_cells or trace["writtenManifest"]["faces"] != geo.n_faces:
            raise ValueError("written mesh and trace sizes disagree")
        identities[str(rank)] = {"trace":trace["identities"],"writtenPolyMesh":{
            name:sha(geo.poly_dir/name) for name in ("points","faces","owner","neighbour","boundary")}}
        sets = {name:g.cell_set(geo.poly_dir/"sets"/name) for name in
                ("underdeterminedCells","lowWeightFaces","lowVolRatioFaces","skewFaces")}
        rank_counts[str(rank)] = {name:len(ids) for name,ids in sets.items()}
        base, first_base = cell_base_mapping(geo,trace)
        for cell in sets["underdeterminedCells"]:
            cell = int(cell);parent = int(trace["cellParent"][cell])
            if not 0 <= parent < trace["sourceManifest"]["cells"] or not trace["cellAlive"][parent]:
                raise ValueError("flagged written cell has invalid source parent")
            groups[(rank,parent)]["cells"].append(cell)
            fs = geo.cell_faces[geo.cell_offsets[cell]:geo.cell_offsets[cell+1]]
            if len(fs)!=4 or any(len(geo.ids(int(f)))!=3 for f in fs):
                continue
            physical_faces = [int(f) for f in fs if geo.patch(int(f)) and
                              geo.patch(int(f))["type"] not in ("processor","cyclic","processorCyclic")]
            selected_faces = [int(f) for f in fs if f<len(geo.neighbour) or
                              (geo.patch(int(f)) and geo.patch(int(f))["type"] in ("processor","cyclic","processorCyclic"))]
            if len(physical_faces)!=1 or len(selected_faces)!=3:
                continue
            f=physical_faces[0]
            if len(np.unique(np.concatenate([geo.ids(int(ff)) for ff in fs])))!=4:
                raise ValueError("qualified tetrahedron has unexpected vertices")
            row = {"rank":rank,"cell":cell,"sourceParent":parent,"physicalFace":f,
                   "sourceBase":int(trace["faceBase"][f]),"physicalPatch":geo.patch(f),
                   "basePointIds":geo.ids(f).tolist(),"baseCoordinates":geo.points[geo.ids(f)].tolist(),
                   "installedBranchSelectedFaces":selected_faces,
                   **triangle_strong_bound(geo.points[geo.ids(f)])}
            sf_rows = [geo.face(ff)[0] for ff in selected_faces]
            row["actualSelected3Determinant"] = q.determinant(sf_rows)[0]
            physical.append(row)
        for f in sorted(set(sets["lowWeightFaces"].tolist())|set(sets["lowVolRatioFaces"].tolist())):
            owner=int(geo.owner[f]);op=int(trace["cellParent"][owner]);ob=int(base[owner])
            neighbour=int(geo.neighbour[f]) if f<len(geo.neighbour) else None
            npart=int(trace["cellParent"][neighbour]) if neighbour is not None else None
            nb=int(base[neighbour]) if neighbour is not None else -1
            relation=face_relation(op,npart,ob,nb)
            selected_sets=[name for name in relations if f in sets[name]]
            row={"rank":rank,"face":int(f),"owner":owner,"neighbour":neighbour,
                 "ownerParent":op,"neighbourParent":npart,"ownerSourceBase":ob,
                 "neighbourSourceBase":nb,"sourceFaceBase":int(trace["faceBase"][f]),
                 "relation":relation,"sets":selected_sets}
            if neighbour is not None:
                sf,fc,*_=geo.face(f);oc=geo.cell(owner);nc=geo.cell(neighbour)
                row.update(q.face_metrics(sf,fc,oc[3],oc[4],geo.points[geo.ids(f)],nc[3],nc[4]))
            for name in selected_sets:
                relations[name][relation]+=1
                key="weightFaces" if name=="lowWeightFaces" else "ratioFaces"
                groups[(rank,op)][key].append(int(f))
                if npart is not None and npart!=op:
                    groups[(rank,npart)][key].append(int(f))
            faces.append(row)
        local_groups = [(p,v) for (rr,p),v in groups.items() if rr==rank]
        local_groups.sort(key=lambda x: (-len(x[1]["cells"]),-len(x[1]["weightFaces"]),x[0]))
        for parent,value in local_groups[:maximum_kernel_groups]:
            kernels[f"{rank}:{parent}"] = source_parent_kernel(trace,parent)
        print(json.dumps({"rank":rank,"counts":rank_counts[str(rank)],"topParent":
              local_groups[0][0] if local_groups else None}),flush=True)
    grouped = [{"rank":rank,"sourceParent":parent,
                "determinantCells":v["cells"],"lowWeightFaces":sorted(set(v["weightFaces"])),
                "lowRatioFaces":sorted(set(v["ratioFaces"])),
                "counts":{"determinant":len(v["cells"]),"weight":len(set(v["weightFaces"])),"ratio":len(set(v["ratioFaces"]))}}
               for (rank,parent),v in groups.items()]
    grouped.sort(key=lambda x: (-sum(x["counts"].values()),x["rank"],x["sourceParent"]))
    return {"schema":"actual-refan-parent-grouping-v1","meshPath":str(mesh),
            "scope":"actual written diagnostic sets and native trace; no mesh mutation or acceptance",
            "summary":{"counts":{name:sum(v[name] for v in rank_counts.values()) for name in next(iter(rank_counts.values()))},
                       "sourceParentGroups":len(grouped),"faceRelations":{k:dict(v) for k,v in relations.items()},
                       "qualifiedPhysicalTetrahedra":len(physical),
                       "oldCertifiedAnyApexImpossible":sum(r["oldNecessaryFails001"] for r in physical),
                       "strongCertifiedAnyApexImpossible":sum(r["strongNecessaryFails001"] for r in physical),
                       "newStrongNecessaryExclusions":[{"rank":r["rank"],"cell":r["cell"],"parent":r["sourceParent"],"base":r["sourceBase"],"bound":r["bound"]}
                                                      for r in physical if r["strongNecessaryFails001"] and not r["oldNecessaryFails001"]]},
            "rankCounts":rank_counts,"parentGroups":grouped,"flaggedFaces":faces,
            "qualifiedPhysicalTetrahedra":physical,"topParentKernelGeometry":kernels,
            "identity":identities,
            "limitations":["Coupled flagged faces are classified without remote metrics; AIR owns exact coupled pairing audit.",
                           "Same-parent same-source-base W/ratio is AP-invariant only for actual coplanar common-apex cones; this relation is a provenance candidate, not an automatic proof for arbitrary cell shapes.",
                           "The strong triangular bound requires the actual three-selected-side/one-noncoupled-physical-base branch checked here.",
                           "Axis boxes are kernel candidates, not all-child or outside-neighbour acceptance.",
                           "No threshold, physical field, mesh point, topology, or product status was changed."]}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mesh",type=Path,required=True);ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--kernel-groups-per-rank",type=int,default=12)
    ap.add_argument("--cheap-only",action="store_true")
    ap.add_argument("--derive-missing-kernel",action="store_true")
    ap.add_argument("--refan-exclusions-from",type=Path)
    ap.add_argument("--same-base-refan-from",type=Path)
    args=ap.parse_args()
    result=(audit_invariant_same_base_refan(args.mesh,args.same_base_refan_from) if args.same_base_refan_from else
            audit_strong_refan(args.mesh,args.refan_exclusions_from) if args.refan_exclusions_from else
            cheap_parent_buckets(args.mesh,args.derive_missing_kernel) if args.cheap_only else audit(args.mesh,args.kernel_groups_per_rank))
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result.get("summary",result.get("counts",{"originalBaseCount":result.get("originalBaseCount",result.get("originalSourceBaseCount"))}))),flush=True)

if __name__=="__main__":
    main()
