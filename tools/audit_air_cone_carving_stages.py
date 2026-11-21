#!/usr/bin/env python3
"""Find exact coordinate multisets in precision-17 stage dumps (which omit cyclic ordering)."""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import numpy as np
from audit_air_mesh_geometry import sha

def point_multiset_key(points):
    x=np.asarray(points,dtype=np.float64)
    if x.ndim!=2 or x.shape[1]!=3 or not np.isfinite(x).all():raise ValueError("Invalid coordinate list")
    return tuple(sorted(tuple(q.tobytes()) for q in x))

def match_stages(target,paths):
    key=point_multiset_key(target);rows=[]
    for path in paths:
        matches=[];most=0;target_points=set(key)
        for line_no,line in enumerate(path.read_text().splitlines(),1):
            if " face " not in line:continue
            points=[[float(v) for v in token.split()] for token in re.findall(r"\(([^)]+)\)",line)]
            if not points:continue
            candidate=point_multiset_key(points)
            most=max(most,len(target_points.intersection(candidate)))
            if candidate==key:
                matches.append({"line":line_no,"points":points,"nativeCellBinding":line.split("|",1)[1].strip(' "')})
        rows.append({"file":path.name,"sha256":sha(path),"exactCoordinateMultisetOccurrences":len(matches),
                     "maximumSharedTargetPointCount":most,"matches":matches})
    first=next((row["file"] for row in rows if row["matches"]),None)
    return {"firstExactStage":first,"rows":rows,
            "persistingEveryRecordedLaterStage":first is not None and all(row["matches"] for row in rows if row["file"]>=first)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--native-witness",type=Path,required=True)
    p.add_argument("--stages",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();raw=json.loads(a.native_witness.read_text())
    paths=sorted(a.stages.glob("*.3.txt"));ids=[143243,143244,143247]
    found={face["face"]:face for face in raw["badBaseFaces"]}
    out={"format":"conforming-cone-carving-first-stage-audit/v1","scope":"Read-only precision-17 raw dump coordinate identity; dump lists sorted face points and does not retain cyclic order.",
        "sourceSha256":sha(Path(__file__)),"nativeWitnessSha256":sha(a.native_witness),
        "stageBox":[[4.035,.03,5.722],[4.058,.045,5.746]],
        "targets":{str(n):match_stages(found[n]["points"],paths) for n in ids},
        "limits":["Only lower-y pentagon 143243 is covered by this stage box. Upper-y targets 143244/143247 have no observed first stage.",
                  "The 01carving checkpoint combines feature pre-splits, shell clipping and cap processing; it does not identify a particular internal branch.",
                  "Native cell geometry keys can change after stage rekeying; exact point multiset binds each observed polygon instead.",
                  "No mesher/checker/solver execution or geometry/source mutation is performed."]}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({k:{"firstExactStage":v["firstExactStage"],"persisting":v["persistingEveryRecordedLaterStage"]} for k,v in out["targets"].items()}))
if __name__=="__main__":main()
