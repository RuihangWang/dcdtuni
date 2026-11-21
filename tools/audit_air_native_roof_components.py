#!/usr/bin/env python3
"""Read-only independent audit of native pre-write roof component JSONL."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from collections import Counter
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_air_mesh_geometry import Geometry,sha

def proposals(row):
    points={p["point"]:p for p in row["points"]}
    by_point={};formula=[]
    for p in row["packets"]:
        assert len(p["pointProposals"])==9,"incomplete packet grid"
        errors=[];plane_errors=[]
        for q in p["pointProposals"]:
            if "bboxMin" in p:
                lo=np.asarray(p["bboxMin"]);hi=np.asarray(p["bboxMax"]);point=np.asarray(q["original"])
                axis=p["axis"];u=(axis+1)%3;v=(axis+2)%3
                i=int(round(2*(point[u]-lo[u])/(hi[u]-lo[u])))
                j=int(round(2*(point[v]-lo[v])/(hi[v]-lo[v])))
                assert i in range(3) and j in range(3),"grid point outside normalized3x3"
                fraction=(.5 if i==1 else 0)+(.5 if j==1 else 0)
                expected=np.zeros(3);expected[axis]=p["sign"]*(5e-4*p["normalSpan"])*fraction
                errors.append(float(np.max(np.abs(expected-np.asarray(q["delta"])))))
                plane_errors.append(abs(float(point[axis]-p["plane"])))
            if q["point"] in points:
                assert q["original"]==points[q["point"]]["original"],"packet coordinate differs from point record"
            by_point.setdefault(q["point"],[]).append(dict(q,cell=p["cell"],axis=p["axis"],sign=p["sign"],plane=p["plane"]))
        if errors:formula.append({"cell":p["cell"],"maximumFormulaDeltaErrorM":max(errors),"maximumOriginalPlaneErrorM":max(plane_errors)})
    shared=[];conflicts=[]
    for node,items in sorted(by_point.items()):
        first=items[0];delta=np.array(first["delta"])
        different=False
        for q in items[1:]:
            value=np.asarray(q["delta"])
            both_nonzero=np.dot(delta,delta)>0 and np.dot(value,value)>0
            different |= not np.array_equal(delta,value) or (both_nonzero and any(first[k]!=q[k] for k in ["axis","sign","plane"]))
        if len(items)>1:
            shared.append({"point":node,"proposals":items,"exactCompatible":bool(not different),"protectedMask":points.get(node,{}).get("protectedMask")})
        if different:conflicts.append(node)
    moving={node:np.asarray(items[0]["delta"]) for node,items in by_point.items() if np.dot(items[0]["delta"],items[0]["delta"])>0}
    protected=[node for node in moving if node in points and points[node]["protectedMask"]!=0]
    missing=[node for node in by_point if node not in points]
    return {"byPoint":by_point,"sharedPoints":shared,"visibleExactConflictPointIds":conflicts,
      "visibleMovingPointIds":list(moving),"visibleMovingProtectedPointIds":protected,
      "missingProposalPointRecordIds":missing,"packetFormulaReconstruction":formula,
      "allPointProposalPacketsExported":len(row["packets"])==row["totalPackets"]}

def geometry(row,moving):
    points={p["point"]:np.asarray(p["original"],float) for p in row["points"]}
    faces={f["face"]:f for f in row["faces"]};cells={c["cell"]:c for c in row["cells"]}
    results=[]
    for c,cell in sorted(cells.items()):
        fs=[faces[f] for f in cell["faces"]]
        ids=sorted({p for f in fs for p in f["points"]})
        lookup={p:i for i,p in enumerate(ids)}
        coords=[points[p]+moving.get(p,np.zeros(3)) for p in ids]
        polygons=[]
        for f in fs:
            polygon=f["points"] if f["owner"]==c else f["points"][::-1]
            assert f["owner"]==c or f["neighbour"]==c,"cell incidence mismatch"
            polygons.append([lookup[p] for p in polygon])
        g=Geometry.from_arrays(coords,polygons)
        stat=g.classify(0);span=max(float(np.linalg.norm(g.cell(0)[7]-g.cell(0)[6])),1e-6)
        stat.update(nativeCell=c,belongsToFullStar=cell["belongsToFullStar"],planarityToleranceM=1e-10*span)
        bad=[]
        for j,f in enumerate(fs):
            if g.face(j)[2]>1e-10*span:
                bad.append({"nativeFace":f["face"],"planarityErrorM":g.face(j)[2],"points":g.points[g.ids(j)].tolist(),
                            "coupled":f["coupled"],"physical":f["physical"],"owner":f["owner"],"neighbour":f["neighbour"]})
        stat["nonplanarFaces"]=bad;results.append(stat)
    return {"exportedCellCount":len(results),"cellsWithNonplanarFaceAtNativeRelativeTolerance":sum(bool(c["nonplanarFaces"]) for c in results),
       "uniqueNonplanarNativeFaceIds":sorted({f["nativeFace"] for c in results for f in c["nonplanarFaces"]}),
       "maximumPlanarityDeviationM":max((f["planarityErrorM"] for c in results for f in c["nonplanarFaces"]),default=0),
       "cellsWithStrictFaceCentreTrigger":sum(sum(c["checkerTriggerCounts"].values())>0 for c in results),
       "cells":results}

def audit(row):
    prop=proposals(row);faces={f["face"]:f for f in row["faces"]}
    star=[c for c in row["cells"] if c["belongsToFullStar"]]
    coupled=sorted({f for c in star for f in c["faces"] if faces[f]["coupled"]})
    missing_neighbours=sorted({n for f in row["faces"] for n in [f["owner"],f["neighbour"]] if n>=0 and n not in {c["cell"] for c in row["cells"]}})
    out={"rank":row["rank"],"vetoReason":row["vetoReason"],"truncated":row["truncated"],
       "totalPackets":row["totalPackets"],"totalStarCells":row["totalStarCells"],"exportedPackets":len(row["packets"]),
       "exportedFullStarCells":len(star),"proposals":{k:v for k,v in prop.items() if k!="byPoint"},
       "nativeDeclaredExactConflictPointCount":row["exactConflictPoints"],
       "visibleCoupledStarFaceIds":coupled,
       "nativeRejectionWitnessVisible":bool((row["vetoReason"]==0 and prop["visibleExactConflictPointIds"]) or (row["vetoReason"]==2 and coupled)),
       "outsideExportedCellNeighbourIds":missing_neighbours,
       "scope":"Native pre-write IDs, not final polyMesh IDs. Complete packet list can establish proposal compatibility; limited star cannot establish whole-component geometry. Coupled remote centres remain unavailable."}
    complete=not row["truncated"] and len(star)==row["totalStarCells"] and prop["allPointProposalPacketsExported"]
    if complete and not prop["visibleExactConflictPointIds"]:
        moves={node:np.asarray(prop["byPoint"][node][0]["delta"]) for node in prop["visibleMovingPointIds"]}
        out["completeExportedStarOriginalGeometry"]=geometry(row,{})
        out["completeExportedStarPureTrial"]=geometry(row,moves)
        out["pureTrialLimit"]="Offline NumPy trial only, despite native coupled-star rejection. Coupled remote centre/weights and outside-export one-ring quality not inferred."
    else:out["geometryNotEvaluated"]="Partial component or conflicting proposals: applying a partial/arbitrary displacement could create spurious geometry."
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument("--input-dir",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    records=[];inputs={}
    for path in sorted(a.input_dir.glob("transition-roof-components-rank*.jsonl")):
        inputs[path.name]=sha(path)
        for i,line in enumerate(path.read_text().splitlines()):
            if not line.strip():continue
            row=audit(json.loads(line));row.update(inputFile=path.name,inputLine=i+1);records.append(row)
    out={"format":"native-roof-component-independent-audit/v1",
      "scope":"Only exported native proposal witnesses, with complete/partial distinctions; no native mesh modification, solver/checker execution or product acceptance.",
      "inputSha256":inputs,"sourceSha256":sha(Path(__file__)),
      "exportedRecordCount":len(records),"recordVetoReasons":dict(Counter(r["vetoReason"] for r in records)),
      "completeExportedStarTrialRecords":sum("completeExportedStarPureTrial" in r for r in records),
      "records":records}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__=="__main__":main()
