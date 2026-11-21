#!/usr/bin/env python3
"""Bounded read-only exact-shell audit of frozen thin-parent union proposals."""
import argparse,hashlib,importlib.util,json
from pathlib import Path
from collections import Counter
from itertools import combinations
import numpy as np
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def tet_values(a,b,c,d):
    ab=b-a;ac=c-a;ad=d-a
    volume=float(np.dot(np.cross(ab,ac),ad)/6)
    ba=np.cross(ac,ab);ca=np.cross(ad,ab)
    denom=float(np.dot(ad,ba))
    if abs(denom)<1e-150:radius=1e15
    else:radius=float(np.linalg.norm(.5*(ab+((np.dot(ad,ad)-np.dot(ab,ad))*ba-(np.dot(ac,ac)-np.dot(ab,ac))*ca)/denom)))
    quality=volume/(8/(9*np.sqrt(3))*min(radius,1e15)**3+1e-150)
    return {"signedVolumeM3":volume,"signedQuality":quality,"circumRadiusM":radius}

def actual_tet_and_pyramid_rows(candidate,rows,original):
    fs,areas,centres,cc,vol,*_=candidate.cell(0);result=[]
    for i,row in enumerate(rows):
        points=candidate.points[row["ids"]];fc=centres[i]
        edge_tets=[{**tet_values(a,b,fc,cc),"edge":j}
                   for j,(a,b) in enumerate(zip(points,np.roll(points,-1,axis=0)))]
        other=row["other"]
        outside_cc=None if other is None else original.cell(other)[3]
        fans=[]
        for anchor in range(len(points)):
            owner=[tet_values(cc,points[anchor],points[(anchor+j)%len(points)],points[(anchor+j+1)%len(points)])["signedQuality"] for j in range(1,len(points)-1)]
            neighbour=None if outside_cc is None else [-tet_values(outside_cc,points[anchor],points[(anchor+j)%len(points)],points[(anchor+j+1)%len(points)])["signedQuality"] for j in range(1,len(points)-1)]
            fans.append({"anchor":anchor,"ownerPositiveQualities":owner,"neighbourPositiveQualities":neighbour,
                         "jointMinQuality":min(owner+(neighbour or []))})
        result.append({"candidateFace":i,"sourceFaces":row["faces"],"outsideNeighbour":other,"patch":row["patch"],
            "actualCellCentre":cc.tolist(),"actualOutsideCentre":None if outside_cc is None else outside_cc.tolist(),
            "actualFaceCentre":fc.tolist(),"outwardAreaVector":areas[i].tolist(),
            "centroidPyramidVolumeM3":float(np.dot(areas[i],fc-cc)/3),
            "ownerEdgeFaceCentreTets":edge_tets,"ownerEdgeFaceCentreTetErrorAt1e30":any(t["signedQuality"]>-1e-30 for t in edge_tets),
            "baseAnchorFans":fans,"existsSharedBaseAt1e30":any(f["jointMinQuality"]>=1e-30 for f in fans)})
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument("--mesh",type=Path,required=True);p.add_argument("--repo",type=Path,required=True)
    p.add_argument("--outdir",type=Path,required=True);p.add_argument("--helper",type=Path,required=True);a=p.parse_args()
    P=load("bound",a.repo/"tools/audit_air_pyramid_remaining_bound.py")
    S=load("box",a.repo/"tools/audit_air_source_box_split.py")
    U=load("union",a.helper);G=P.M.g;Q=P.M.q
    geo=G.Geometry(a.mesh/"processor1/constant/polyMesh");t=P.M.read_trace(a.mesh,1)
    t["zone"]=np.memmap(a.mesh/"conforming-source-rank1-zone.i32",mode="r",dtype="<i4")
    before=json.loads((a.repo/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-quad-pyramid-independent/actual-parent-height-bound.json").read_text())
    expected=before["identity"]["1"]["written"]
    for name,h in expected.items():
        if P.M.sha(geo.poly_dir/name)!=h:raise ValueError("not the frozen original height-cap mesh")
    seed_parents=sorted({x["sourceParent"] for x in before["rows"] if x["rank"]==1 and x.get("sourceParentHeightBound",{}).get("parentKernelNecessaryBound",{}).get("parentBoundNecessaryFails001")})
    cache={}
    def children(parent):
        if parent not in cache:cache[parent]=set(map(int,np.flatnonzero(t["cellParent"]==parent)))
        return cache[parent]
    def neighbours(parent):
        ns=set()
        for c in children(parent):
            for f in geo.cell(c)[0]:
                if f>=len(geo.neighbour):continue
                o=int(geo.owner[f]);n=int(geo.neighbour[f]);other=n if o==c else o
                if other not in children(parent):ns.add(int(t["cellParent"][other]))
        return ns
    def exact_moments(rows):
        ids=sorted({v for row in rows for v in row["ids"]});index={v:i for i,v in enumerate(ids)}
        return S.signed_shell_moments(geo.points[ids],[[index[v] for v in row["ids"]] for row in rows])
    results=[];class_counts=Counter();total_triggers=0
    for parent in seed_parents:
        ns=neighbours(parent)
        groups={tuple(sorted([parent,n])) for n in ns}
        groups|={tuple(sorted([parent,x,y])) for x,y in combinations(sorted(ns),2)}
        for parents in sorted(groups):
            cells=set().union(*(children(q) for q in parents))
            fs=set(int(f) for c in cells for f in geo.cell(c)[0])
            removed=[f for f in fs if f<len(geo.neighbour) and geo.owner[f] in cells and geo.neighbour[f] in cells and t["cellParent"][geo.owner[f]]!=t["cellParent"][geo.neighbour[f]]]
            if not removed or any(geo.patch(f) is not None or (t["faceBase"][f]>=0 and t["zone"][t["faceBase"][f]]!=-1) for f in removed):continue
            raw=U.merged_boundary(geo,cells,t);rows,joins=U.coalesce(geo,raw,G)
            candidate=G.Geometry.from_arrays(geo.points,[r["ids"] for r in rows]);cls=candidate.classify(0,True)
            info=U.metrics(geo,cells,t,G,Q,True)
            oldV,oldM=exact_moments(raw);newV,newM=exact_moments(rows)
            ofM=sum((geo.cell(c)[3]*geo.cell(c)[4] for c in cells),np.zeros(3))
            tetrows=actual_tet_and_pyramid_rows(candidate,rows,geo)
            cp=candidate.cell(0);Merr=float(np.linalg.norm(cp[3]*cp[4]-ofM))/max(float(np.linalg.norm(ofM)),abs(cp[4]),1e-300)
            blockers=[]
            for trigger in cls["checkerTriggers"]:
                x=rows[trigger["face"]];y=rows[trigger["otherFace"]]
                blockers.append({**trigger,"faceSourceIDs":x["faces"],"otherFaceSourceIDs":y["faces"],
                    "facePatch":x["patch"],"otherFacePatch":y["patch"],"faceOutsideNeighbour":x["other"],"otherOutsideNeighbour":y["other"],
                    "faceZone":x["zone"],"otherZone":y["zone"],
                    "sameMetadataMayCoalesce":all(x[k]==y[k] for k in ["patch","other","zone"])})
            vertex_ids=sorted({v for row in raw for v in row["ids"]})
            node_index={v:i for i,v in enumerate(vertex_ids)}
            originalV=oldV
            record={"seedParent":parent,"parents":parents,"children":sorted(cells),"removedCrossParentFaces":removed,
                "interfacesAllInternalUnzoned":True,"singleMeshRegion":"air processor1/constant/polyMesh",
                "materialScope":"This is a single-region air solve mesh. No physical/cyclic/processor or zoned interface is removed; arbitrary cross-material union is not authorized.",
                "rawBoundaryFaces":raw,"candidateBoundaryFaces":rows,"originalPointLabels":vertex_ids,"originalPointCoordinates":geo.points[vertex_ids].tolist(),
                "exactShellVolumePreserved":oldV==newV,"exactShellFirstMomentPreserved":oldM==newM,
                "oldExactShellVolumeFraction":str(oldV),"candidateExactShellVolumeFraction":str(newV),
                "oldExactShellFirstMomentFractions":[str(x) for x in oldM],"candidateExactShellFirstMomentFractions":[str(x) for x in newM],
                "floatOFFirstMomentRelativeError":Merr,"geometry":cls,
                "reconstructedOFFaceCentreCriterionPass":sum(cls["checkerTriggerCounts"].values())==0,
                "centroidAllPyramidsPositive":cls["minimumCentroidPyramidVolumeM3"]>0,
                "outerFaceBlockers":blockers,"actualCentroidFacetetDiagnostics":tetrows,"facetetScope":"Actual candidate-owner edge/face-centre tet qualities and shared vertex-anchor existence against unchanged external CC. All outer-neighbour cell determinant recomputation and full native checker remain unperformed.",**info,
                "acceptance":False,"acceptanceScope":"Every proposal triggers the installed face-to-face-centre concavity formula; official v2412 application allGeometry counts true as a failed check. Positive centroid pyramids and facetets are evaluated separately, and whole-shell vertex convexity is only an extra sufficient diagnostic. No rewritten native mesh/full checker was run."}
            results.append(record);class_counts[cls["classification"]]+=1
        subset=[x for x in results if x["seedParent"]==parent]
        print(json.dumps({"seedParent":parent,"boundedProposals":len(subset),"passesOFFaceCentre":sum(x["reconstructedOFFaceCentreCriterionPass"] for x in subset)}),flush=True)
    d={"schema":"bounded-original-parent-union-shell-primary/v1","mesh":str(a.mesh),"sourceSeedsFromHeightCertificates":seed_parents,
       "helperSHA256":hashlib.sha256(a.helper.read_bytes()).hexdigest(),"identity":before["identity"]["1"],
       "summary":{"proposals":len(results),"classes":dict(class_counts),"passesOFFaceCentre":sum(x["reconstructedOFFaceCentreCriterionPass"] for x in results),
                  "allPositiveCentroidPyramids":sum(x["centroidAllPyramidsPositive"] for x in results),
                  "exactVolumePreserved":sum(x["exactShellVolumePreserved"] for x in results),"exactFirstMomentPreserved":sum(x["exactShellFirstMomentPreserved"] for x in results),"allCandidateOwnerEdgeFaceCentreTetsPass":sum(not any(f["ownerEdgeFaceCentreTetErrorAt1e30"] for f in x["actualCentroidFacetetDiagnostics"]) for x in results),"allSharedBaseAnchorsExist":sum(all(f["existsSharedBaseAt1e30"] for f in x["actualCentroidFacetetDiagnostics"]) for x in results)},
       "results":results}
    (a.outdir/"bounded-primary-shell-audit.json").write_text(json.dumps(d,indent=2)+"\n");print(json.dumps(d["summary"]),flush=True)
if __name__=="__main__":main()
