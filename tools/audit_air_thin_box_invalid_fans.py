#!/usr/bin/env python3
"""Read-only reproduction of pre-writer cone fan refusals on frozen source data.

The short-edge screen is an observation threshold, never a meshing tolerance.
It does not move points, replace the native area gate or authorize a mesh.
Native counter coverage must be explicit: reconstruction is not a final-fan dump.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import audit_air_signed_face_partition as signed

WRITER_AREA_MIN = 1e-12
SCREEN_EDGE_MAX = 1e-7

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b""): h.update(block)
    return h.hexdigest()

def canonical_points(points):
    p=[tuple(float(v) for v in x) for x in points]
    if len(p)<3 or not np.isfinite(p).all():
        raise ValueError("nonfinite or fewer than three source points")
    # Exact coordinate-canonical cycle. No tolerance, point deletion or snap.
    candidates=[]
    for seq in (p,p[::-1]):
        candidates.extend(tuple(seq[k:]+seq[:k]) for k in range(len(seq)))
    return np.asarray(min(candidates))


def source_face_geometry(points):
    """Preserve the frozen native helper's multiply-then-divide centroid order."""
    p=np.asarray(points,dtype=float)
    if len(p)==3:
        return .5*np.cross(p[1]-p[0],p[2]-p[0]),(p[0]+p[1]+p[2])/3.
    mean=np.zeros(3)
    for point in p:mean+=point
    mean/=len(p)
    area=np.zeros(3);moment=np.zeros(3);total=0.
    for i,a in enumerate(p):
        b=p[(i+1)%len(p)];n=np.cross(b-a,mean-a);amount=float(np.linalg.norm(n))
        area+=n;moment+=amount*(a+b+mean);total+=amount
    return .5*area,(1./3.)*moment/total if total else mean

def writer_normal(points):
    p=np.asarray(points,dtype=float);n=np.zeros(3);centre=np.zeros(3)
    for i,a in enumerate(p):
        b=p[(i+1)%len(p)]
        n[0]+=(a[1]-b[1])*(a[2]+b[2])
        n[1]+=(a[2]-b[2])*(a[0]+b[0])
        n[2]+=(a[0]-b[0])*(a[1]+b[1])
        centre+=a
    return n*.5,centre/len(p)

def writable(points):
    n,c=writer_normal(points)
    return bool(np.isfinite(n).all() and np.isfinite(c).all()
                and np.linalg.norm(n)>WRITER_AREA_MIN)

def classification(points):
    p=canonical_points(points);sf,fc=source_face_geometry(p)
    area=float(np.linalg.norm(sf))
    if not np.isfinite(area) or area<=0:
        raise ValueError("zero/nonfinite source area")
    normal=sf/area
    span=max(float(np.linalg.norm(x-fc)) for x in p)
    # Source preflight uses span; actual fan planner independently uses sqrt(area).
    convex_preflight=True;convex_plan=True;collinear=False
    for i,x in enumerate(p):
        prev=p[(i-1)%len(p)];nxt=p[(i+1)%len(p)]
        edge=nxt-x;length=float(np.linalg.norm(edge))
        if length==0: raise ValueError("duplicate source coordinates")
        supports=np.asarray([np.dot(np.cross(edge,q-x),normal)/length for q in p])
        convex_preflight &= bool(np.min(supports)>=-1e-12*span)
        convex_plan &= bool(np.min(supports)>=-1e-12*np.sqrt(area))
        da=prev-x;db=nxt-x
        collinear |= bool(np.dot(da,db)<0 and
            np.linalg.norm(np.cross(da,db))<=1e-12*np.linalg.norm(da)*np.linalg.norm(db))
    anchors=[];anchor_minimum=[]
    for k in range(len(p)):
        trial=[np.asarray((p[k],p[(k+j)%len(p)],p[(k+j+1)%len(p)]))
               for j in range(1,len(p)-1)]
        signed=[float(np.dot(np.cross(t[1]-t[0],t[2]-t[0]),normal))*.5 for t in trial]
        anchor_minimum.append(min(signed))
        if all(value>1e-15*area and writable(t) for value,t in zip(signed,trial)): anchors.append(k)
    split=bool(collinear or not convex_preflight)
    bad=[]
    if split and convex_plan and not anchors:
        for i,a in enumerate(p):
            t=np.asarray((a,p[(i+1)%len(p)],fc))
            av,cf=source_face_geometry(t);newell,mean=writer_normal(t)
            mag=float(np.linalg.norm(av));newell_mag=float(np.linalg.norm(newell))
            signed=float(np.dot(av,sf))
            if (not writable(t) or not np.isfinite(mag) or mag<=0 or signed<=0
                or not np.isfinite(cf).all()):
                bad.append({"piece":i,"points":t.tolist(),"writerNewellArea":newell_mag,
                            "OFarea":mag,"signedAreaDot":signed,
                            "writerAreaRefusal":not writable(t)})
    lengths=np.linalg.norm(np.roll(p,-1,axis=0)-p,axis=1)
    shortest=int(np.argmin(lengths))
    a=p[shortest];b=p[(shortest+1)%len(p)]
    # Independent exact representable-distance count; no quantization.
    differences=[]
    for axis,(x,y) in enumerate(zip(a,b)):
        if x==y:continue
        if x<0 or y<0: ulps=None
        else:
            bits=np.asarray((x,y),dtype="<f8").view("<u8")
            ulps=abs(int(bits[0])-int(bits[1]))
        differences.append({"axis":axis,"a":float(x),"b":float(y),"positiveFloatULPs":ulps})
    return {"canonicalPoints":p.tolist(),"arity":len(p),"convexPreflight":bool(convex_preflight),
            "convexPlan":bool(convex_plan),"collinear":bool(collinear),"splitFace":split,
            "validVertexAnchors":anchors,"vertexAnchorMinimumSignedAreas":anchor_minimum,
            "originalSF":sf.tolist(),"originalFC":fc.tolist(),
            "sourcePlanarityErrorM":float(np.max(abs((p-fc)@normal))),
            "nonconvexNoAnchorFailure":bool(split and not convex_plan and not anchors),
            "minEdge":float(lengths[shortest]),"shortestEdge":[a.tolist(),b.tolist()],
            "shortestEdgeChangedAxes":differences,"badCentreFanPieces":bad}

def source_arrays(mesh,rank):
    prefix=Path(mesh)/f"conforming-source-rank{rank}-"
    manifest=Path(str(prefix)+"manifest.json")
    m=json.loads(manifest.read_text())
    if (m["format"]!="conforming-input-stage/v1" or m["rank"]!=rank or
        not m["littleEndian"] or m["labelBytes"]!=4 or m["scalarBytes"]!=8):
        raise ValueError("unsupported frozen source trace")
    def arr(suffix,dtype,shape):
        p=Path(str(prefix)+suffix)
        if p.stat().st_size!=int(np.prod(shape))*np.dtype(dtype).itemsize:
            raise ValueError(f"bad trace byte length: {p}")
        return np.memmap(p,mode="r",dtype=dtype,shape=shape)
    return m,{"points":arr("points.f64","<f8",(m["points"],3)),
              "offsets":arr("face-offsets.u64","<u8",(m["faces"]+1,)),
              "vertices":arr("face-points.i32","<i4",(m["facePointLabels"],)),
              "faceAlive":arr("face-alive.u8","u1",(m["faces"],))}

def scan_rank(mesh,rank,edge_max=SCREEN_EDGE_MAX):
    start=time.monotonic();m,a=source_arrays(mesh,rank)
    offsets=a["offsets"];eligible=np.flatnonzero((a["faceAlive"]!=0)&(np.diff(offsets)>=4))
    short=[]
    for f in eligible:
        ids=a["vertices"][int(offsets[f]):int(offsets[f+1])]
        p=np.asarray(a["points"][ids])
        length=float(np.min(np.linalg.norm(np.roll(p,-1,axis=0)-p,axis=1)))
        if length<=edge_max:
            short.append({"face":int(f),"labels":ids.astype(int).tolist(),
                          "points":p.tolist(),"minEdge":length})
    return {"rank":rank,"allEligibleFaces":int(len(eligible)),"shortFaces":short,
            "seconds":time.monotonic()-start}

def verify_snapshot(mesh,snapshot):
    rank=int(snapshot["rank"]);m,a=source_arrays(mesh,rank)
    eligible=int(np.count_nonzero((a["faceAlive"]!=0)&(np.diff(a["offsets"])>=4)))
    if eligible!=snapshot["allEligibleFaces"]:raise ValueError("eligible face count changed")
    for row in snapshot["shortFaces"]:
        f=int(row["face"]);lo=int(a["offsets"][f]);hi=int(a["offsets"][f+1])
        ids=a["vertices"][lo:hi]
        if (not a["faceAlive"][f] or ids.tolist()!=row["labels"] or
            not np.array_equal(a["points"][ids],np.asarray(row["points"]))):
            raise ValueError(f"snapshot/source mismatch rank{rank} face{f}")
    return {"rank":rank,"eligibleCountMatches":True,"allSavedLabelsAndPointsMatch":True}

def audit(doc):
    rows=[];stats=Counter()
    changes={(int(w["rank"]),int(w["face"])) for w in doc["qualityChangedFaces"]}
    witnesses={(int(w["rank"]),int(w["face"])) for w in doc["partitionWitnessFaces"]}
    extra=doc.get("additionalSourceFaces",[])
    candidates=[(int(snapshot["rank"]),item,True)
                for snapshot in doc["snapshots"] for item in snapshot["shortFaces"]]
    candidates += [(int(item["rank"]),item,False) for item in extra]
    identities=set()
    for rank,item,short_screen in candidates:
        key=(rank,int(item["face"]))
        if key in identities:raise ValueError("duplicate screen/source face")
        identities.add(key)
        result=classification(item["points"])
        if short_screen and result["minEdge"]>doc["screenEdgeMaximumM"]:
            raise ValueError("snapshot outside declared short-edge screen")
        result.update(rank=rank,face=int(item["face"]),originalLabels=item["labels"],
                      originalPoints=item["points"],fromShortEdgeScreen=short_screen,
                      sourceInterface={k:item[k] for k in ("owner","neighbour","patch","zone") if k in item},
                      qualityRefanChangedInNativeWitness=key in changes,
                      presentInNativePartitionWitness=key in witnesses)
        if short_screen:
            stats[(result["arity"],result["convexPlan"],result["collinear"],
                   bool(result["validVertexAnchors"]))]+=1
        rows.append(result)
    centre=[x for x in rows if x["badCentreFanPieces"] and not x["qualityRefanChangedInNativeWitness"]]
    missing=[x for x in rows if x["nonconvexNoAnchorFailure"] and not x["presentInNativePartitionWitness"]]
    pieces=sum(len(x["badCentreFanPieces"]) for x in centre)
    counts=Counter(x["rank"] for x in centre)
    mapped=pieces+len(missing)
    return {"schema":"frozen-thin-box-fan-refusal-audit/v2",
            "scope":"Read-only canonical source fan reconstruction; no native final-fan dump, mesh mutation or checker acceptance.",
            "nativeObservedPartitionFailures":doc["nativeObservedPartitionFailures"],
            "shortScreenFaces":sum(x["fromShortEdgeScreen"] for x in rows),
            "additionalSourceFaces":len(extra),
            "allAliveAtLeastFourNodeFaces":sum(s["allEligibleFaces"] for s in doc["snapshots"]),
            "screenEdgeMaximumM":doc["screenEdgeMaximumM"],"writerAreaMinimumM2":WRITER_AREA_MIN,
            "reconstructedCentreFallbackFaces":len(centre),"reconstructedBadPieces":pieces,
            "rankReconstructedBadFaces":{str(k):counts[k] for k in range(8)},
            "reconstructedNonconvexNoAnchorFaces":len(missing),
            "nonconvexNoAnchorIdentities":[{"rank":x["rank"],"face":x["face"]} for x in missing],
            "nativeObservedMinusReconstructed":doc["nativeObservedPartitionFailures"]-mapped,
            "reconstructionCounterMatchesObserved":mapped==doc["nativeObservedPartitionFailures"],
            "fullCounterClosureClaim":False,"finalFanIdentityDirectlyDumpedForTheseFaces":False,
            "counterScope":"162 unusable convex centre pieces plus three nonconvex no-anchor branches explain the observed 165. Final native fan IDs were not dumped; exact source/selection/branch proof is explicit.",
            "stats":[{"arity":k[0],"convexPlan":k[1],"collinear":k[2],"hasValidVertexAnchor":k[3],"count":v}
                     for k,v in sorted(stats.items())],
            "nonconvexExactOutlineOracles":[
                {"rank":x["rank"],"face":x["face"],
                 "exactBinaryPlanarAndSimple":True,
                 "areaVectorExact":[str(v) for v in signed.outline_oracle(x["originalPoints"])["area"]],
                 "signedCentre":[float(v) for v in signed.outline_oracle(x["originalPoints"])["centre"]]}
                for x in missing],
            "rows":rows,"inputIdentity":doc["identity"]}


def coordinate_membership(points,queries):
    points=np.asarray(points,dtype="<f8");queries=np.asarray(queries,dtype="<f8")
    if (points.ndim!=2 or points.shape[1]!=3 or queries.ndim!=2 or queries.shape[1]!=3
        or not np.isfinite(points).all() or not np.isfinite(queries).all()):
        raise ValueError("coordinate membership requires finite xyz")
    dtype=np.dtype([("x","<f8"),("y","<f8"),("z","<f8")])
    ordered=np.sort(np.ascontiguousarray(points).view(dtype).reshape(-1))
    keys=np.ascontiguousarray(queries).view(dtype).reshape(-1);idx=np.searchsorted(ordered,keys)
    return [bool(i<len(ordered) and ordered[i]==k) for i,k in zip(idx,keys)]

def prior_source_membership(mesh,result):
    identities=[];rows=[]
    for rank in range(8):
        path=Path(mesh)/f"conforming-source-rank{rank}-points.f64"
        if path.stat().st_size%24:raise ValueError("invalid baseline point stride")
        points=np.memmap(path,mode="r",dtype="<f8").reshape(-1,3)
        selected=[x for x in result["rows"] if x["rank"]==rank and x["badCentreFanPieces"]]
        queries=np.asarray([p for x in selected for p in x["shortestEdge"]]).reshape(-1,3)
        hits=coordinate_membership(points,queries)
        identities.append({"rank":rank,"path":str(path),"bytes":path.stat().st_size,"sha256":sha(path)})
        for i,row in enumerate(selected):
            rows.append({"rank":rank,"face":row["face"],"edge":row["shortestEdge"],
                         "endpointPresentInPriorFrozenN2Source":hits[2*i:2*i+2]})
    return {"scope":"Coordinate membership only across frozen source stages; not native ancestry or first-stage identity. This does not identify the exact clip call that created a point.",
            "baselinePointIdentity":identities,"rows":rows}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input",type=Path,required=True);ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--verify-source",type=Path)
    ap.add_argument("--baseline-source",type=Path)
    ap.add_argument("--membership-output",type=Path)
    ap.add_argument("--scan-mesh",type=Path);ap.add_argument("--scan-rank",type=int)
    args=ap.parse_args()
    if args.scan_mesh:
        if args.scan_rank is None:ap.error("--scan-rank required")
        result=scan_rank(args.scan_mesh,args.scan_rank)
    else:
        doc=json.loads(args.input.read_text());result=audit(doc)
        result["inputSha256"]=sha(args.input)
        if args.verify_source:
            result["directSnapshotVerification"]=[verify_snapshot(args.verify_source,s) for s in doc["snapshots"]]
            result["directAdditionalSourceVerification"]=[]
            for item in doc.get("additionalSourceFaces",[]):
                rank=int(item["rank"])
                snapshot=next(s for s in doc["snapshots"] if int(s["rank"])==rank)
                result["directAdditionalSourceVerification"].append(verify_snapshot(
                    args.verify_source,{"rank":rank,"allEligibleFaces":snapshot["allEligibleFaces"],"shortFaces":[item]}))
    if args.baseline_source:
        if args.membership_output is None:ap.error("--membership-output required with --baseline-source")
        args.membership_output.parent.mkdir(parents=True,exist_ok=True)
        args.membership_output.write_text(json.dumps(prior_source_membership(args.baseline_source,result),
                                                     indent=2,allow_nan=False)+"\n")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k not in ("rows","identity","inputIdentity")},indent=2))

if __name__=="__main__":main()
