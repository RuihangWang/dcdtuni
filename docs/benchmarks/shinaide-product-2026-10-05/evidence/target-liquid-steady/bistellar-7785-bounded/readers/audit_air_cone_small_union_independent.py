#!/usr/bin/env python3
"""Pure local cone-child union audit with original role lineage.

No points/facets are edited in the native case. A replaced cell inherits every
original passing role; numeric-label reuse cannot hide a regression.
"""
from __future__ import annotations
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
import audit_air_mesh_geometry as g
import audit_air_cone_candidate_independent as i

LIMITS={"D":(.001,False),"weight":(.05,False),"ratio":(.01,False),
        "skew":(4.,True),"nonOrthogonalityDegrees":(70.,True)}

class WrittenStencil(i.Stencil):
    def face(self,f):
        if f not in self.faceCache:
            xyz=self.polygon(f)
            geo=g.Geometry.from_arrays(xyz,[list(range(len(xyz)))])
            self.faceCache[f]=geo.face(0)[:2]
        return self.faceCache[f]

def labelled_input(raw):
    family=raw["familyChildren"]
    common=set.intersection(*({v for f in raw["cells"][str(c)]["faces"]
                              for v in raw["faces"][str(f)]["pointIDs"]} for c in family))
    if len(common)!=1:raise ValueError("one traced common AP required")
    apex=common.pop()
    return {"children":list(family),"outsideCells":list(raw["outsideCells"]),
        "points":copy.deepcopy(raw["points"]),"faces":copy.deepcopy(raw["faces"]),
        "cells":{key:{"faces":list(row["faces"]),"writtenCentre":row["CC"],"writtenVolume":row["V"],
                 "sourceParent":row["sourceParent"]} for key,row in raw["cells"].items()},
        "apexWrittenPoint":apex,"originalApex":raw["points"][str(apex)]}

def union_document(doc,group):
    group=set(group);rep=min(group)
    if not 2<=len(group)<=4 or not group<=set(doc["children"]):raise ValueError("two to four same-parent children")
    if len({doc["cells"][str(c)]["sourceParent"] for c in group})!=1:raise ValueError("same source parent required")
    out=copy.deepcopy(doc);removed=[];outer=[]
    for label,row in list(out["faces"].items()):
        own,nei=row["owner"],row["neighbour"]
        if own in group and nei in group:
            removed.append(int(label));del out["faces"][label];continue
        if own in group or nei in group:
            if row["patch"] and any(v in row["patch"]["type"].lower() for v in ("processor","cyclic","ami","mapped")):
                raise ValueError("affected coupled exterior state unavailable")
            outer.append(int(label))
            if own in group:row["owner"]=rep
            if nei in group:row["neighbour"]=rep
    connected={rep}
    while True:
        previous=len(connected)
        for f in removed:
            row=doc["faces"][str(f)];a,b=row["owner"],row["neighbour"]
            if a in connected:connected.add(b)
            if b in connected:connected.add(a)
        if len(connected)==previous:break
    if connected!=group:raise ValueError("face-connected union required")
    # Only the replacement cell's face list changes. All outside/remaining
    # faces retain their original arithmetic traversal order and geometry.
    out["cells"][str(rep)]["faces"]=sorted(outer)
    for c in group-{rep}:del out["cells"][str(c)]
    out["children"]=[c for c in out["children"] if c not in group or c==rep]
    return out,sorted(removed),sorted(outer)

def coalesce_physical_faces(doc,labels,rep):
    """Cancel exact common directed edges; retain all original outline points."""
    labels=list(labels)
    if len(labels)!=2:raise ValueError("exactly two physical triangles supported")
    rows=[doc["faces"][str(f)] for f in labels]
    if any(r["owner"]!=rep or r["neighbour"] is not None or r["patch"] is None for r in rows):
        raise ValueError("same physical owner role required")
    for key in ("patch","sourceZone","sourceBase"):
        if any(r[key]!=rows[0][key] for r in rows):raise ValueError("same physical patch, zone and source base required")
    if any(len(r["pointIDs"])!=3 for r in rows):raise ValueError("two original triangles required")
    directed=Counter()
    for row in rows:
        ids=row["pointIDs"]
        for a,b in zip(ids,ids[1:]+ids[:1]):directed[(a,b)]+=1
    common=[]
    for a,b in list(directed):
        if a<b and directed[(a,b)] and directed[(b,a)]:
            if directed[(a,b)]!=1 or directed[(b,a)]!=1:raise ValueError("non-simple shared edge")
            common.append((a,b));del directed[(a,b)];del directed[(b,a)]
    if len(common)!=1 or any(v!=1 for v in directed.values()):raise ValueError("one shared exact edge required")
    successor={}
    for a,b in directed:
        if a in successor:raise ValueError("branching polygon boundary")
        successor[a]=b
    start=min(successor,key=lambda v:(tuple(doc["points"][str(v)]),v))
    loop=[start]
    while successor[loop[-1]]!=start:
        nxt=successor[loop[-1]]
        if nxt in loop:raise ValueError("multiple cycles")
        loop.append(nxt)
    if len(loop)!=len(successor) or set(loop)!={v for row in rows for v in row["pointIDs"]}:
        raise ValueError("all original outline points must remain")
    out=copy.deepcopy(doc);keep=min(labels);drop=max(labels)
    out["faces"][str(keep)]["pointIDs"]=loop
    del out["faces"][str(drop)]
    out["cells"][str(rep)]["faces"]=[f for f in out["cells"][str(rep)]["faces"] if f!=drop]
    before=WrittenStencil(doc,doc["originalApex"]);after=WrittenStencil(out,out["originalApex"])
    source=[before.face(f) for f in labels];sf,fc=after.face(keep)
    vector=sum((v[0] for v in source),np.zeros(3))
    amount=sum(np.linalg.norm(v[0]) for v in source)
    moment=sum((np.linalg.norm(v[0])*v[1] for v in source),np.zeros(3))
    xyz=after.polygon(keep);normal=sf/np.linalg.norm(sf)
    span=max(np.linalg.norm(v-fc) for v in xyz);plane=float(abs((xyz-fc)@normal).max())
    face=after.face_detail(keep)
    audit={"originalFaces":labels,"replacementFace":keep,"removedCommonEdges":[list(x) for x in common],
        "originalOutlinePointsAllRetained":True,"polygon":loop,"physicalRole":rows[0]["patch"],
        "sourceZone":rows[0]["sourceZone"],"sourceBase":rows[0]["sourceBase"],
        "vectorAreaRelativeError":float(np.linalg.norm(sf-vector)/amount),
        "unsignedAreaRelativeError":float(abs(np.linalg.norm(sf)-amount)/amount),
        "areaWeightedCentroidDifferenceM":float(np.linalg.norm(fc-moment/amount)),
        "planeErrorM":plane,"nativePlaneSpanLimitM":float(1e-10*span),
        "minimumEdgeSupportM":face["polygonMinimumSupportM"]}
    audit["geometryPreserved"]=bool(audit["vectorAreaRelativeError"]<=1e-10 and audit["unsignedAreaRelativeError"]<=1e-10
        and audit["areaWeightedCentroidDifferenceM"]<=1e-10*span and plane<=1e-10*span
        and face["polygonMinimumSupportM"]>=-1e-10*span)
    if not audit["geometryPreserved"]:raise ValueError("joined outline geometry guard failed")
    return out,{keep:labels},audit

def canonicalize_internal_roles(doc):
    """Retain common point zero while orienting every owner below neighbour."""
    out=copy.deepcopy(doc);flipped=[]
    for key,row in out["faces"].items():
        if row["neighbour"] is not None and row["owner"]>row["neighbour"]:
            row["owner"],row["neighbour"]=row["neighbour"],row["owner"]
            ids=row["pointIDs"];row["pointIDs"]=[ids[0],*reversed(ids[1:])]
            flipped.append(int(key))
    return out,flipped

def verify_role_canonicalization(doc):
    candidate,flipped=canonicalize_internal_roles(doc)
    a,aq=family_metrics(doc);b,bq=family_metrics(candidate)
    areaerr=max([0.,*(float(np.linalg.norm(a.face(f)[0]+b.face(f)[0])) for f in flipped)])
    centreerr=max([0.,*(float(np.linalg.norm(a.face(f)[1]-b.face(f)[1])) for f in flipped)])
    celldiffs=[]
    for cell in a.cells:
        ac=a.cell(cell);bc=b.cell(cell)
        celldiffs.append({"cell":cell,"centreDifferenceM":float(np.linalg.norm(ac[2]-bc[2])),
                          "relativeVolumeDifference":abs(ac[3]-bc[3])/abs(ac[3])})
    diffs={}
    for metric in LIMITS:
        collection="cells" if metric=="D" else "faces";label="cell" if metric=="D" else "face"
        old={r[label]:r[metric] for r in aq[collection] if metric in r}
        new={r[label]:r[metric] for r in bq[collection] if metric in r}
        diffs[metric]=max([0.,*(abs(new[k]-old[k]) for k in old)])
    original_physical=all(a.faces[f]==b.faces[f] for f in a.faces if a.faces[f]["neighbour"] is None)
    return {"facesFlipped":flipped,"allOwnerLabelsBelowNeighbour":all(r["neighbour"] is None or r["owner"]<r["neighbour"] for r in candidate["faces"].values()),
        "pointZeroRetained":all(a.faces[f]["pointIDs"][0]==b.faces[f]["pointIDs"][0] for f in flipped),
        "maximumAreaVectorOppositeErrorM2":areaerr,"maximumFaceCentreDifferenceM":centreerr,
        "maximumCellCentreDifferenceM":max(v["centreDifferenceM"] for v in celldiffs),
        "maximumCellRelativeVolumeDifference":max(v["relativeVolumeDifference"] for v in celldiffs),
        "unchangedPhysicalExteriorRolesAndPolygons":original_physical,
        "maximumFamilyQualityDifference":diffs,"completeCellDifferences":celldiffs}

def oriented_edges(stencil,cell):
    directed=Counter()
    for face in stencil.cells[cell]["faces"]:
        row=stencil.faces[face];ids=row["pointIDs"] if row["owner"]==cell else list(reversed(row["pointIDs"]))
        for a,b in zip(ids,ids[1:]+ids[:1]):directed[(a,b)]+=1
    undirected=Counter()
    for (a,b),count in directed.items():undirected[tuple(sorted((a,b)))]+=count
    return {"allEdgesExactlyTwo":all(v==2 for v in undirected.values()),
            "allEdgesOppositelyDirected":all(directed[(a,b)]==directed[(b,a)] for a,b in directed),
            "undirectedMultiplicityCounts":dict(Counter(undirected.values()))}

def lineage_pareto(old,new,group,rep,face_lineage=None):
    face_lineage=face_lineage or {}
    result={}
    for metric,(threshold,upper) in LIMITS.items():
        collection="cells" if metric=="D" else "faces";label="cell" if metric=="D" else "face"
        before={r[label]:r[metric] for r in old[collection] if metric in r}
        after={r[label]:r[metric] for r in new[collection] if metric in r}
        bad=lambda value:(not np.isfinite(value)) or (value>threshold if upper else value<threshold)
        failures=[]
        for ident,value in after.items():
            origins=(list(group) if metric=="D" and ident==rep else face_lineage.get(ident,[ident]))
            passing=[x for x in origins if x in before and not bad(before[x])]
            if passing and bad(value):failures.append({"replacement":ident,"originalPassingRoles":passing,"value":value})
        oldbad={c:v for c,v in before.items() if bad(v)};newbad={c:v for c,v in after.items() if bad(v)}
        severity=lambda v:float("inf") if not np.isfinite(v) or (not upper and v<=0) else (v/threshold if upper else threshold/v)
        oldworst=max([1.,*(severity(v) for v in oldbad.values())])
        newworst=max([1.,*(severity(v) for v in newbad.values())])
        result[metric]={"originalPassingRoleFailures":failures,"oldBadLabels":sorted(oldbad),
            "candidateBadLabels":sorted(newbad),"badCountDoesNotIncrease":len(newbad)<=len(oldbad),
            "oldWorstFailedSeverity":oldworst,"candidateWorstFailedSeverity":newworst,
            "worstFailedSeverityDoesNotIncrease":newworst<=oldworst,
            "pass":not failures and len(newbad)<=len(oldbad) and newworst<=oldworst}
    return result

def family_metrics(doc):
    m=WrittenStencil(doc,doc["originalApex"]);family=set(doc["children"])
    af=sorted(f for f,r in m.faces.items() if r["owner"] in family or r["neighbour"] in family)
    cells=[m.cell_detail(c) for c in doc["children"]];faces=[];unknown=[]
    for face in af:
        raw=m.faces[face]
        if raw["neighbour"] is None and raw["patch"] and any(v in raw["patch"]["type"].lower() for v in ("processor","cyclic","ami","mapped")):
            unknown.append(face);continue
        faces.append(m.face_detail(face))
    return m,{"cells":cells,"faces":faces,"unaffectedUnknownCoupledFaces":unknown}

def verify_written_source(raw):
    mesh=Path(raw["mesh"]);rank=raw["rank"];poly=mesh/f"processor{rank}/constant/polyMesh"
    geo=g.Geometry(poly);hashes={name:g.sha(poly/name) for name in ("points","faces","owner","neighbour","boundary","faceZones","cellZones","pointZones")}
    for label,coords in raw["points"].items():
        actual=geo.points[int(label)]
        if not np.array_equal(actual.view(np.uint64),np.asarray(coords,dtype=float).view(np.uint64)):raise ValueError("written coordinate mismatch")
    bases=np.memmap(mesh/f"conforming-written-rank{rank}-face-base.i32",mode="r",dtype="<i4")
    parents=np.memmap(mesh/f"conforming-written-rank{rank}-cell-parent.i32",mode="r",dtype="<i4")
    zones=np.memmap(mesh/f"conforming-source-rank{rank}-zone.i32",mode="r",dtype="<i4")
    for label,row in raw["faces"].items():
        face=int(label)
        if not np.array_equal(geo.ids(face),row["pointIDs"]):raise ValueError("written polygon mismatch")
        nei=int(geo.neighbour[face]) if face<len(geo.neighbour) else None
        if int(geo.owner[face])!=row["owner"] or nei!=row["neighbour"] or geo.patch(face)!=row["patch"]:raise ValueError("written face role mismatch")
        if int(bases[face])!=row["sourceBase"]:raise ValueError("source base trace mismatch")
        if (int(zones[bases[face]]) if bases[face]>=0 else -1)!=row["sourceZone"]:raise ValueError("source zone trace mismatch")
    for label,row in raw["cells"].items():
        cell=int(label);data=geo.cell(cell)
        if list(map(int,data[0]))!=row["faces"] or int(parents[cell])!=row["sourceParent"]:raise ValueError("actual complete cell/parent mismatch")
        if not np.array_equal(data[3].view(np.uint64),np.asarray(row["CC"]).view(np.uint64)) or data[4]!=row["V"]:raise ValueError("actual written cell moment mismatch")
    trace={name:g.sha(mesh/name) for name in [f"conforming-written-rank{rank}-manifest.json",f"conforming-source-rank{rank}-manifest.json",f"conforming-written-rank{rank}-cell-parent.i32",f"conforming-written-rank{rank}-face-base.i32",f"conforming-source-rank{rank}-zone.i32"]}
    return {"mesh":str(mesh),"rank":rank,"polyMeshSha256":hashes,"traceSha256":trace,
       "pointCoordinatesBitExact":len(raw["points"]),"actualFaceRolesAndPolygonsVerified":len(raw["faces"]),"completeWrittenCellsAndMomentsVerified":len(raw["cells"])}

def analyze(raw,group,verify_mesh=False,join_faces=None):
    doc=labelled_input(raw);candidate,removed,outer=union_document(doc,group);rep=min(group)
    face_lineage={};join_audit=None
    if join_faces:
        candidate,face_lineage,join_audit=coalesce_physical_faces(candidate,join_faces,rep)
        outer=[f for f in outer if str(f) in candidate["faces"]]
    old,oq=family_metrics(doc);new,nq=family_metrics(candidate)
    detail=new.cell_detail(rep);edges=oriented_edges(new,rep)
    before=[old.cell(c) for c in group];old_v=sum(v[3] for v in before)
    old_m=sum((v[3]*v[2] for v in before),np.zeros(3))
    _,_,cc,v=new.cell(rep);volerr=abs(v-old_v)/old_v;moment=float(np.linalg.norm(v*cc-old_m))
    group_xyz=np.array([old.points[v] for c in group for f in old.cells[c]["faces"] for v in old.faces[f]["pointIDs"]])
    group_span=float(np.linalg.norm(np.ptp(group_xyz,axis=0)))
    relative_moment=moment/(old_v*group_span)
    unchanged=[]
    for c in map(int,doc["cells"]):
        if c in group:continue
        _,_,a,av=old.cell(c);_,_,b,bv=new.cell(c)
        unchanged.append({"cell":c,"centreBitExact":bool(np.array_equal(a.view(np.uint64),b.view(np.uint64))),"volumeExactlyUnchanged":av==bv})
    actual_faces=[r for r in nq["faces"] if r["face"] in outer]
    centre_tet=min(r["minimumCentreFanTetQuality"] for r in actual_faces)
    anchor_tet=min(r["bestSharedBasePointMinimumTetQuality"] for r in actual_faces)
    planarity=[];allfixed=True
    for f in outer:
        a,b=old.faces[f],new.faces[f]
        if (f not in face_lineage and a["pointIDs"]!=b["pointIDs"]) or a["patch"]!=b["patch"] or a["sourceZone"]!=b["sourceZone"] or a["sourceBase"]!=b["sourceBase"]:allfixed=False
        old_other=a["neighbour"] if a["owner"] in group else a["owner"]
        new_other=b["neighbour"] if b["owner"]==rep else b["owner"]
        if old_other!=new_other:allfixed=False
        xyz=new.polygon(f);sf,fc=new.face(f);normal=sf/np.linalg.norm(sf)
        span=max(float(np.linalg.norm(p-fc)) for p in xyz);err=float(abs((xyz-fc)@normal).max())
        planarity.append({"face":f,"spanM":span,"errorM":err,"nativeSpanLimitM":1e-10*span,"passes":err<=1e-10*span})
    metrics={"minimumD":detail["D"],"minimumWeight":min(r["weight"] for r in actual_faces if "weight" in r),
        "minimumRatio":min(r["ratio"] for r in actual_faces if "ratio" in r),
        "maximumSkew":max(r["skew"] for r in actual_faces),
        "maximumNonOrth":max(r["nonOrthogonalityDegrees"] for r in actual_faces if "nonOrthogonalityDegrees" in r)}
    absolute=metrics["minimumD"]>=.001 and metrics["minimumWeight"]>=.05 and metrics["minimumRatio"]>=.01 and metrics["maximumSkew"]<=4 and metrics["maximumNonOrth"]<=70
    pareto=lineage_pareto(oq,nq,group,rep,face_lineage)
    unchanged_exact=all(r["centreBitExact"] and r["volumeExactlyUnchanged"] for r in unchanged)
    geometry=unchanged_exact and allfixed and edges["allEdgesExactlyTwo"] and edges["allEdgesOppositelyDirected"] and detail["faceCentreConcavityPairTriggers"]==0 and detail["maximumVertexOutsideM"]<=1e-10*group_span and detail["relativeClosure"]<1e-10 and detail["minCentroidPyramidVolume"]>0 and all(p["passes"] for p in planarity) and centre_tet>1e-15 and anchor_tet>1e-15 and volerr<1e-10 and relative_moment<1e-10
    return {"schema":"same-parent-small-union-independent/v1","scope":"Pure same-parent candidate: one merged cell and every affected interface, full existing family role lineage and complete surrounding cells. Optional same-physical-role exact edge union. Not native mesh acceptance.",
        "joinedPhysicalOutline":join_audit,"replacementFaceOriginalRoleLineage":face_lineage,
        "rank":raw["rank"],"sourceParent":raw["sourceParent"],"group":list(group),"representative":rep,
        "removedInternalFaces":removed,"fixedOuterFaces":outer,"pointsNeverMoved":candidate["points"]==doc["points"],
        "fixedOuterPolygonsPatchZoneAndOutsideNeighbourPreserved":allfixed,
        "oldGroupCells":[old.cell_detail(c) for c in group],"mergedCell":detail,"directedEdges":edges,
        "facePlanarity":planarity,"affectedFaces":actual_faces,"minimumCentreFanTetQuality":centre_tet,
        "minimumSharedOriginalAnchorTetQuality":anchor_tet,"sumOriginalVolumeM3":old_v,"mergedVolumeM3":v,
        "relativeVolumeDifference":volerr,"firstMomentDifferenceM4":moment,
        "groupSpanM":group_span,"firstMomentErrorNormalizedByVolumeAndSpan":relative_moment,"unchangedCompleteCells":unchanged,
        "allOtherCompleteCellCentresAndVolumesUnchanged":all(r["centreBitExact"] and r["volumeExactlyUnchanged"] for r in unchanged),
        "metrics":metrics,"passesCandidateGeometry":bool(geometry),"passesAllFiveAbsoluteLocalQuality":bool(absolute),
        "fullFamilyLineagePareto":pareto,"passesFullFamilyFiveMetricLineagePareto":all(r["pass"] for r in pareto.values()),
        "unaffectedUnknownCoupledFaces":oq["unaffectedUnknownCoupledFaces"],
        "ownerNeighbourLabelCanonicalizationRequiredOnFaces":[f for f in outer if new.faces[f]["neighbour"] is not None and new.faces[f]["owner"]>new.faces[f]["neighbour"]],
        "canonicalOwnerNeighbourAudit":verify_role_canonicalization(candidate),
        "sourceVerification":verify_written_source(raw) if verify_mesh else None}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--input",type=Path,required=True)
    p.add_argument("--group",type=int,nargs="+",required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--verify-mesh",action="store_true");p.add_argument("--join-physical-faces",type=int,nargs=2)
    args=p.parse_args();raw=json.loads(args.input.read_text());out=analyze(raw,args.group,args.verify_mesh,args.join_physical_faces)
    out["inputIdentity"]={"path":str(args.input),"bytes":args.input.stat().st_size,"sha256":hashlib.sha256(args.input.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,indent=2,allow_nan=False)+"\n")
    print(json.dumps({key:out[key] for key in ("group","metrics","passesCandidateGeometry",
        "passesAllFiveAbsoluteLocalQuality","passesFullFamilyFiveMetricLineagePareto",
        "relativeVolumeDifference","firstMomentDifferenceM4",
        "allOtherCompleteCellCentresAndVolumesUnchanged","inputIdentity")},indent=2))

if __name__=="__main__":main()
