#!/usr/bin/env python3
"""Pure geometric candidates for axis-box source partitions; no native acceptance.

Only exact axis-planar rectangular source shells are supported. Original point
coordinates are preserved; inserted points and propagation have no product
authorization until a full conforming native mesh passes stock checkMesh.
"""
from __future__ import annotations
import argparse, itertools, json, sys
from collections import Counter
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_air_mesh_geometry as g
import audit_air_transition_roof as roof
import audit_air_cone_remaining_quality as q


def source_box(parent):
    points=np.concatenate([np.asarray(f["points"],float) for f in parent["faces"]])
    if not np.isfinite(points).all():raise ValueError("nonfinite source points")
    lo=points.min(axis=0);hi=points.max(axis=0)
    if not (hi>lo).all():raise ValueError("degenerate source box")
    plane_amounts=Counter();plane_rectangles={}
    for face in parent["faces"]:
        p=np.asarray(face["points"],float)
        fixed=[a for a in range(3) if np.all(p[:,a]==p[0,a])]
        if len(fixed)!=1:raise ValueError("source face must be exactly axis-planar")
        axis=fixed[0]
        if p[0,axis] not in (lo[axis],hi[axis]):raise ValueError("interior source facet plane unsupported")
        geom=g.Geometry.from_arrays(p,[list(range(len(p)))])
        sf,fc,_,_,_,support=geom.face(0)
        if support<0 or np.linalg.norm(sf)<=0:raise ValueError("source polygon must be convex")
        sign=1 if p[0,axis]==hi[axis] else -1
        if sf[axis]*sign<=0 or np.any(np.delete(sf,axis)!=0):raise ValueError("source outward orientation mismatch")
        tang=[a for a in range(3) if a!=axis]
        f_lo=p[:,tang].min(axis=0);f_hi=p[:,tang].max(axis=0)
        # Retained collinear boundary vertices are allowed. Area and centroid
        # must be those of its independent enclosing rectangle.
        expected=float(np.prod(f_hi-f_lo))
        if abs(np.linalg.norm(sf)-expected)>1e-10*expected:raise ValueError("nonrectangular source face")
        if np.linalg.norm(fc[tang]-(f_lo+f_hi)/2)>1e-10*np.linalg.norm(f_hi-f_lo):
            raise ValueError("nonrectangular source first moment")
        plane_amounts[(axis,sign)]+=expected
        plane_rectangles.setdefault((axis,sign),[]).append((f_lo,f_hi))
    for axis in range(3):
        area=float(np.prod(np.delete(hi-lo,axis)))
        for sign in (-1,1):
            if abs(plane_amounts[(axis,sign)]-area)>1e-10*area:
                raise ValueError("source box plane coverage incomplete")
            rectangles=plane_rectangles[(axis,sign)]
            levels=[sorted({float(v[j]) for rect in rectangles for v in rect}) for j in range(2)]
            for i,k in itertools.product(range(len(levels[0])-1),range(len(levels[1])-1)):
                mid=np.array([(levels[0][i]+levels[0][i+1])/2,(levels[1][k]+levels[1][k+1])/2])
                if sum(bool(np.all(mid>a) and np.all(mid<b)) for a,b in rectangles)!=1:
                    raise ValueError("source plane tiles overlap or leave a hole")
    # Closure and exact directed edges independently reject overlap/missing tiles.
    keys={};ps=[];polys=[]
    for f in parent["faces"]:
        ids=[]
        for p in f["points"]:
            key=tuple(p)
            if key not in keys:keys[key]=len(ps);ps.append(p)
            ids.append(keys[key])
        polys.append(ids)
    geom=g.Geometry.from_arrays(ps,polys)
    edge=Counter((a,b) for ids in polys for a,b in zip(ids,ids[1:]+ids[:1]))
    if not all(n==1 and edge[(b,a)]==1 for (a,b),n in edge.items()):
        raise ValueError("source shell not edge-closed")
    if geom.cell(0)[4]<=0:raise ValueError("source shell volume nonpositive")
    return lo,hi,geom


def choose_original_seam(parent,axis):
    lo,hi,_=source_box(parent)
    values=sorted(set(float(p[axis]) for f in parent["faces"] for p in f["points"]
                      if lo[axis]<p[axis]<hi[axis]))
    if not values:raise ValueError("no original interior seam coordinate")
    mid=(lo[axis]+hi[axis])/2
    return min(values,key=lambda x:(abs(x-mid),x))


def face_rectangle(lo,hi,axis,sign):
    tangent=[a for a in range(3) if a!=axis]
    pts=[]
    for u,v in [(0,0),(1,0),(1,1),(0,1)]:
        p=lo.copy();p[axis]=hi[axis] if sign>0 else lo[axis]
        p[tangent[0]]=hi[tangent[0]] if u else lo[tangent[0]]
        p[tangent[1]]=hi[tangent[1]] if v else lo[tangent[1]]
        pts.append(p)
    if np.cross(pts[1]-pts[0],pts[2]-pts[0])[axis]*sign<0:
        pts=[pts[0],*pts[:0:-1]]
    return [p.tolist() for p in pts]


def cells_from_boxes(parents,cuts):
    reports=[];labels=[];coordinates={}
    def register(p):
        key=tuple(p)
        if key not in coordinates:coordinates[key]=len(coordinates)
        return coordinates[key]
    for parent in parents:
        lo,hi,_=source_box(parent)
        values=[[float(lo[a]),*sorted(cuts.get(parent["parent"],{}).get(a,[])),float(hi[a])]
                for a in range(3)]
        if any(any(b<=a for a,b in zip(v,v[1:])) for v in values):raise ValueError("invalid cuts")
        for intervals in itertools.product(*(list(zip(v,v[1:])) for v in values)):
            bl=np.array([v[0] for v in intervals]);bh=np.array([v[1] for v in intervals])
            fs=[]
            for axis in range(3):
                for sign in (-1,1):
                    ps=face_rectangle(bl,bh,axis,sign)
                    fs.append({"vertexIds":[register(p) for p in ps],"points":ps})
            reports.append({"faces":fs})
            labels.append({"parent":parent["parent"],"lo":bl.tolist(),"hi":bh.tolist()})
    geom=roof.assemble(reports)
    return geom,labels


def source_face_for(parent,points):
    lo,hi,_=source_box(parent)
    p=np.asarray(points,float)
    fixed=[a for a in range(3) if np.all(p[:,a]==p[0,a])]
    if len(fixed)!=1:raise ValueError("candidate face not axis-planar")
    axis=fixed[0]
    if p[0,axis] not in (lo[axis],hi[axis]):return None
    matches=[]
    for f in parent["faces"]:
        a=np.asarray(f["points"],float)
        if not np.all(a[:,axis]==p[0,axis]):continue
        tang=[d for d in range(3) if d!=axis]
        if np.all(p[:,tang].min(axis=0)>=a[:,tang].min(axis=0)) and np.all(p[:,tang].max(axis=0)<=a[:,tang].max(axis=0)):
            matches.append(f)
    if len(matches)!=1:raise ValueError("candidate crosses source neighbour/patch seam")
    return matches[0]


def audit(parents,cuts):
    geom,labels=cells_from_boxes(parents,cuts)
    byparent={p["parent"]:p for p in parents}
    selected=[];cellrows=[];faceprov={};boundarytotals={}
    for c,label in enumerate(labels):
        parent=byparent[label["parent"]];fs,areas,fcs,cc,v,*_=geom.cell(c)
        selection=[]
        for f,sf in zip(fs,areas):
            ids=geom.ids(int(f));ps=geom.points[ids]
            original=source_face_for(parent,ps)
            if original is None or original["patch"]<0:selection.append(sf)
            if original is not None:
                faceprov.setdefault(str(int(f)),[]).append({"cell":c,"sourceParent":parent["parent"],
                    "sourceFace":original["sourceFace"],"outsideParent":original["outsideParent"],
                    "sourcePatch":original["patch"],"sourceZone":original["zone"]})
                key=(parent["parent"],original["sourceFace"])
                bucket=boundarytotals.setdefault(key,{"Sf":np.zeros(3),"area":0.,"moment":np.zeros(3),"pieces":[]})
                amount=float(np.linalg.norm(sf));bucket["Sf"]+=sf;bucket["area"]+=amount
                bucket["moment"]+=amount*geom.face(int(f))[1];bucket["pieces"].append(int(f))
        D=q.determinant(selection)[0]
        row=geom.classify(c,True)
        row.update(label,determinant=D,selectedFaceCount=len(selection),volumeM3=v,centre=cc.tolist())
        cellrows.append(row)
    conservation=[]
    for parent in parents:
        _,_,original=source_box(parent)
        fs,areas,fcs,cc,v,*_=original.cell(0)
        children=[c for c,x in enumerate(labels) if x["parent"]==parent["parent"]]
        cv=sum(geom.cell(c)[4] for c in children)
        cm=sum((geom.cell(c)[4]*geom.cell(c)[3] for c in children),np.zeros(3))
        perface=[]
        for f in parent["faces"]:
            bucket=boundarytotals[(parent["parent"],f["sourceFace"])]
            geo=g.Geometry.from_arrays(f["points"],[list(range(len(f["points"])))])
            sf,fc,*_=geo.face(0);amount=float(np.linalg.norm(sf))
            perface.append({"sourceFace":f["sourceFace"],"pieces":bucket["pieces"],
                "relativeAreaError":abs(bucket["area"]-amount)/amount,
                "relativeVectorAreaError":float(np.linalg.norm(bucket["Sf"]-sf)/amount),
                "centroidDifferenceM":float(np.linalg.norm(bucket["moment"]/bucket["area"]-fc))})
        physical=[f["patch"]>=0 for f in parent["faces"]]
        originalD=q.determinant([a for a,b in zip(areas,physical) if not b])[0]
        conservation.append({"parent":parent["parent"],"originalSourceFaceCount":len(fs),
            "originalSelectedDeterminant":originalD,"originalClassification":original.classify(0,True),
            "originalVolumeM3":v,"childrenVolumeM3":cv,"relativeVolumeError":abs(cv-v)/v,
            "originalFirstMoment":(v*cc).tolist(),"childrenFirstMoment":cm.tolist(),
            "relativeFirstMomentError":float(np.linalg.norm(cm-v*cc)/(v*max(np.linalg.norm(cc),1e-300))),
            "boundaryFaces":perface})
    interior=[]
    for f in range(len(geom.neighbour)):
        own=int(geom.owner[f]);nei=int(geom.neighbour[f])
        sf,fc,*_=geom.face(f)
        row=q.face_metrics(sf,fc,geom.cell(own)[3],geom.cell(own)[4],
                           geom.points[geom.ids(f)],geom.cell(nei)[3],geom.cell(nei)[4])
        d=geom.cell(nei)[3]-geom.cell(own)[3]
        row.update(face=f,owner=own,neighbour=nei,
            nonorthogonalityDegrees=float(np.degrees(np.arccos(np.clip(sf@d/(np.linalg.norm(sf)*np.linalg.norm(d)),-1,1)))))
        interior.append(row)
    fans=[roof.face_fans(geom,f) for f in range(geom.n_faces)]
    oldcoords={tuple(p) for parent in parents for f in parent["faces"] for p in f["points"]}
    newcoords={tuple(p) for p in geom.points}
    return {"schema":"dcdtuni.axis-box-fragment-math.v1","points":geom.points.tolist(),
        "facePolygons":[geom.ids(f).tolist() for f in range(geom.n_faces)],
        "owner":geom.owner.tolist(),"neighbour":geom.neighbour.tolist(),
        "cells":cellrows,"interfaces":interior,"tetFans":fans,"sourceFaceProvenance":faceprov,
        "parentConservation":conservation,"allOriginalCoordinatesRemainUsed":oldcoords<=newcoords,
        "insertedPoints":[list(x) for x in sorted(newcoords-oldcoords)],
        "summary":{"cells":len(labels),"points":len(geom.points),"internalFaces":len(interior),
          "minDeterminant":min(c["determinant"] for c in cellrows),
          "minInternalWeight":min((f["weight"] for f in interior),default=None),
          "minInternalRatio":min((f["volumeRatio"] for f in interior),default=None),
          "maxInternalSkewness":max((f["skewness"] for f in interior),default=None),
          "maxInternalNonorthDegrees":max((f["nonorthogonalityDegrees"] for f in interior),default=None),
          "minTetQuality":min(min(f["ownerCentroidFanMinimumQuality"],
              f["neighbourCentroidFanMinimumQuality"] if f["neighbourCentroidFanMinimumQuality"] is not None else np.inf,
              f["sharedOriginalVertexFanBestMinimumQuality"]) for f in fans),
          "strictOtherFaceCentreFailures":sum(sum(c["checkerTriggerCounts"].values()) for c in cellrows),
          "maxParentRelativeVolumeError":max(c["relativeVolumeError"] for c in conservation),
          "maxParentRelativeFirstMomentError":max(c["relativeFirstMomentError"] for c in conservation)},
        "scope":"Only these fragments and their mutually conforming internal interfaces. No proof of conformity/quality with outside source parents, no full native checkMesh acceptance."}



def clip_polygon(points,lo,hi,normal_axis):
    p=[np.asarray(x,float) for x in points]
    for axis in range(3):
        if axis==normal_axis:continue
        for bound,lower in [(lo[axis],True),(hi[axis],False)]:
            out=[]
            if not p:return []
            for a,b in zip(p,p[1:]+p[:1]):
                ina=a[axis]>=bound if lower else a[axis]<=bound
                inb=b[axis]>=bound if lower else b[axis]<=bound
                if ina:out.append(a)
                if ina!=inb:
                    t=(bound-a[axis])/(b[axis]-a[axis])
                    point=a+t*(b-a);point[axis]=bound
                    out.append(point)
            p=out
    unique=[]
    for point in p:
        if not unique or not np.array_equal(point,unique[-1]):unique.append(point)
    if len(unique)>1 and np.array_equal(unique[0],unique[-1]):unique.pop()
    return unique if len(unique)>=3 else []


def retained_neighbour_diagnostic(snapshot,result):
    """Intersect old actual faces with fragments; outside cells stay frozen.

    This intentionally exposes missing propagation. Distinct outside labels
    prevent unconditional coalescence, so conceptual six-face boxes are not
    automatically an admissible mesh with the old outside topology.
    """
    selected={c["parent"] for c in result["cells"]}
    pieces=[];coverage=[];box_D=[];outside_cuts=Counter();coplanar_triggers=0;different_neighbour_triggers=0
    for c,cell in enumerate(result["cells"]):
        lo=np.array(cell["lo"]);hi=np.array(cell["hi"]);cc=np.array(cell["centre"]);v=cell["volumeM3"]
        selected_sf=[];plane_counts=Counter();plane_roles={}
        # Mutually conforming candidate internal faces already belong to this
        # selected domain and require no old external record.
        for f,own in enumerate(result["owner"]):
            nei=result["neighbour"][f] if f<len(result["neighbour"]) else None
            if nei is not None and c in (own,nei):
                points=np.array([result["points"][i] for i in result["facePolygons"][f]])
                sf=g.Geometry.from_arrays(points,[list(range(len(points)))]).face(0)[0]
                selected_sf.append(sf if own==c else -sf)
        for record in snapshot["actualWrittenParentBoundary"]:
            if record["sourceParent"]!=cell["parent"] or record["outsideParent"] in selected:continue
            points=np.asarray(record["points"])
            fixed=[a for a in range(3) if np.all(points[:,a]==points[0,a])]
            if len(fixed)!=1:raise ValueError("actual boundary is not exactly axis-planar")
            axis=fixed[0];plane=points[0,axis]
            if plane not in (lo[axis],hi[axis]):continue
            clipped=clip_polygon(points,lo,hi,axis)
            if not clipped:continue
            geo=g.Geometry.from_arrays(clipped,[list(range(len(clipped)))])
            sf,fc,*_=geo.face(0);amount=float(np.linalg.norm(sf))
            if not amount:continue
            if record["outsideWrittenCell"] is not None:selected_sf.append(sf)
            key=(axis,float(plane));plane_counts[key]+=1
            role=("cell",record["outsideWrittenCell"]) if record["outsideWrittenCell"] is not None else ("physical",str(record["patch"]))
            plane_roles.setdefault(key,set()).add(role)
            row={"box":c,"parent":cell["parent"],"originalWrittenFace":record["writtenFace"],
                 "sourceFace":record["sourceFace"],"outsideParent":record["outsideParent"],
                 "outsideWrittenCell":record["outsideWrittenCell"],"points":[x.tolist() for x in clipped],
                 "Sf":sf.tolist(),"centre":fc.tolist(),"areaM2":amount,
                 "patch":record["patch"],"centroidPyramidVolumeM3":float(sf@(fc-cc)/3)}
            outside=record["outsideCentre"];ov=record["outsideVolume"]
            row["quality"]=q.face_metrics(sf,fc,cc,v,np.asarray(clipped),
                                          np.asarray(outside) if outside is not None else None,ov)
            if outside is not None:
                d=np.asarray(outside)-cc
                row["quality"]["nonorthogonalityDegrees"]=float(np.degrees(np.arccos(np.clip(
                    sf@d/(np.linalg.norm(sf)*np.linalg.norm(d)),-1,1))))
                outside_cuts[record["outsideWrittenCell"],record["writtenFace"]]+=1
            pieces.append(row)
        coplanar_triggers+=sum(n*(n-1) for n in plane_counts.values())
        different_neighbour_triggers+=sum(n*(n-1) for key,n in plane_counts.items() if len(plane_roles[key])>1)
        box_D.append({"box":c,"selectedFacetCountWithOldNeighbourLabels":len(selected_sf),
                      "determinantWithOldNeighbourFacets":q.determinant(selected_sf)[0],
                      "conceptualSixFaceDeterminant":cell["determinant"],
                      "planePieceCounts":[{"axis":a,"plane":p,"pieces":n} for (a,p),n in plane_counts.items()]})
    old_records=[r for r in snapshot["actualWrittenParentBoundary"]
                 if r["sourceParent"] in selected and r["outsideParent"] not in selected]
    for r in old_records:
        children=[p for p in pieces if p["parent"]==r["sourceParent"] and p["originalWrittenFace"]==r["writtenFace"]]
        area=float(np.linalg.norm(r["Sf"]));covered=sum(p["areaM2"] for p in children)
        coverage.append({"parent":r["sourceParent"],"originalWrittenFace":r["writtenFace"],
                         "pieces":len(children),"relativeAreaCoverageError":abs(covered-area)/area})
    sameplane_witnesses=[]
    for i,first in enumerate(pieces):
        for j,other in enumerate(pieces):
            if i==j or first["box"]!=other["box"]:continue
            n=np.asarray(first["Sf"])/max(np.linalg.norm(first["Sf"]),1e-300)
            d=np.asarray(other["centre"])-np.asarray(first["centre"])
            if not np.linalg.norm(d):continue
            dot=float(d@n/max(np.linalg.norm(d),1e-300))
            if dot<=-1e-6:continue
            same_role=(first["outsideWrittenCell"]==other["outsideWrittenCell"] and
                       first["patch"]==other["patch"])
            sameplane_witnesses.append({"box":first["box"],
                "firstOriginalWrittenFace":first["originalWrittenFace"],"otherOriginalWrittenFace":other["originalWrittenFace"],
                "firstOutsideCell":first["outsideWrittenCell"],"otherOutsideCell":other["outsideWrittenCell"],
                "firstFragmentPoints":first["points"],"otherFragmentPoints":other["points"],
                "firstOutwardSf":first["Sf"],"firstFaceCentre":first["centre"],"otherFaceCentre":other["centre"],
                "actualUnitDot":dot,"installedPlanarCosAngle":1e-6,
                "exactInstalledPrimitivePredicateTrue":dot>-1e-6,"differentOutsideRole":not same_role,
                "cellCentreDoesNotEnterThisPredicate":True})
    outside_diagnostics=[]
    for cell in snapshot["actualOutsideCompleteCells"]:
        replacements={f["writtenFace"]:[p for p in pieces if p["outsideWrittenCell"]==cell["cell"] and p["originalWrittenFace"]==f["writtenFace"]]
                      for f in cell["faces"]}
        if not any(replacements.values()):continue
        selected_sf=[];splits=[]
        for f in cell["faces"]:
            is_selected=f["patch"] is None or f["patch"]["type"] in ["cyclic","processor","processorCyclic"]
            ps=replacements[f["writtenFace"]]
            if is_selected:selected_sf.extend([-np.asarray(p["Sf"]) for p in ps] if ps else [np.asarray(f["Sf"])])
            if len(ps)>1:splits.append({"face":f["writtenFace"],"pieces":len(ps)})
        newD=q.determinant(selected_sf)[0]
        outside_diagnostics.append({"outsideCell":cell["cell"],"oldD":cell["oldDeterminant"],
            "facetSplitD":newD,"passingToFailingD":cell["oldDeterminant"]>=.001 and newD<.001,
            "wouldCreateCoplanarFacets":splits,
            "scope":"Area-vector and selected-face count effect only; original CC/V kept. Edge/node conformity and repeated planar outside facets are unresolved, so not a mesh acceptance."})
    internal=[p["quality"] for p in pieces if p["outsideWrittenCell"] is not None]
    return {"scope":"All original frozen outside CC/V and clipped old labelled faces; coalescing distinct outside cells or conforming propagation is not performed. Counts expose an invalid direct splice, not a complete new mesh.",
        "pieces":pieces,"coverage":coverage,"boxFacetedDeterminants":box_D,
        "coplanarBoxOtherFaceCentreTriggerPairs":coplanar_triggers,
        "coplanarBoxTriggerPairsWithDistinctOutsideRoles":different_neighbour_triggers,
        "outsideDeterminantDiagnostics":outside_diagnostics,
        "installedCheckConcaveCellsWitnesses":sameplane_witnesses,
        "oldOutsideFacesThatWouldBeSplit":[{"cell":c,"writtenFace":f,"newPieces":n} for (c,f),n in outside_cuts.items() if n>1],
        "minimumWeight":min(p["weight"] for p in internal),
        "minimumRatio":min(p["volumeRatio"] for p in internal),
        "maximumSkewness":max(p["skewness"] for p in internal),
        "maximumNonorthogonalityDegrees":max(p["nonorthogonalityDegrees"] for p in internal),
        "maximumRelativeExteriorAreaCoverageError":max(p["relativeAreaCoverageError"] for p in coverage),
        "cannotAuthorizeDirectSplice":True}

def plan(snapshot,seed):
    parents=snapshot["parents"];seedparent=next(p for p in parents if p["parent"]==seed)
    lo,hi,_=source_box(seedparent);long=int(np.argmax(hi-lo));thin=int(np.argmin(hi-lo))
    seam=choose_original_seam(seedparent,long)
    cuts={seed:{long:[seam]}}
    one=audit([seedparent],cuts)
    # Neighbour selection is by actual shared face and maximum original normal
    # thickness; never by a case-specific coordinate or parent ID.
    neighbours=[]
    for p in parents:
        if p["parent"]==seed:continue
        shared=[f for f in seedparent["faces"] if f["outsideParent"]==p["parent"]]
        if not shared:continue
        if any(not all(point[thin]==f["points"][0][thin] for point in f["points"]) for f in shared):continue
        peer_shared=[f for f in p["faces"] if f["outsideParent"]==seed]
        if {f["sourceFace"] for f in shared}!={f["sourceFace"] for f in peer_shared}:
            raise ValueError("shared original source facet identity mismatch")
        for face in shared:
            other=next(f for f in peer_shared if f["sourceFace"]==face["sourceFace"])
            if {tuple(x) for x in face["points"]}!={tuple(x) for x in other["points"]}:
                raise ValueError("shared original source coordinates mismatch")
        pl,ph,_=source_box(p)
        if pl[long]<seam<ph[long]:neighbours.append((ph[thin]-pl[thin],p))
    if not neighbours:raise ValueError("no eligible exact box peer")
    peer=max(neighbours,key=lambda row:(row[0],-row[1]["parent"]))[1]
    pl,ph,_=source_box(peer)
    cuts[peer["parent"]]={long:[seam],thin:[float((pl[thin]+ph[thin])/2)]}
    joint=audit([seedparent,peer],cuts)
    one["retainedNeighbourDiagnostic"]=retained_neighbour_diagnostic(snapshot,one)
    joint["retainedNeighbourDiagnostic"]=retained_neighbour_diagnostic(snapshot,joint)
    return {"schema":"dcdtuni.axis-box-partition-proposal.v1","seedParent":seed,
       "derivedLongAxis":long,"derivedThinAxis":thin,"originalSeam":seam,
       "peerParent":peer["parent"],"cuts":cuts,"singleParent":one,"jointParents":joint,
       "scope":"Pure exact-axis-box fragments. Original source points remain used. External conforming propagation, outside neighbour quality/Pareto and product stock checker are unresolved; no case acceptance."}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--seed",type=int,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    snapshot=json.loads(args.input.read_text())
    result=plan(snapshot,args.seed)
    result["sourceInput"]={"path":str(args.input),"sha256":g.sha(args.input)}
    result["toolSHA256"]=g.sha(Path(__file__))
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:result[k] for k in ["seedParent","derivedLongAxis","derivedThinAxis","originalSeam","peerParent"]}))
    print(json.dumps(result["singleParent"]["summary"]))
    print(json.dumps(result["jointParents"]["summary"]))


if __name__=="__main__":main()
