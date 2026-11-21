#!/usr/bin/env python3
"""Read-only before/after boundary outlines, patch identity and coupled order."""
import argparse, hashlib, json, re, struct, sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from backend.aircooling.runner.polymesh_io import read_all_faces,read_points,read_label_list,read_owner_note

def outline(points):
    points=[tuple(float(x) for x in p) for p in points]
    changed=True
    while changed and len(points)>3:
        changed=False
        for i,p in enumerate(points):
            a=np.asarray(points[i-1])-p;b=np.asarray(points[(i+1)%len(points)])-p
            scale=np.linalg.norm(a)*np.linalg.norm(b)
            if scale>0 and np.dot(a,b)<0 and np.linalg.norm(np.cross(a,b))<=1e-12*scale:
                points.pop(i);changed=True;break
    # Preserve orientation; only cyclic starting vertex is representation.
    rotations=[points[i:]+points[:i] for i in range(len(points))]
    best=min(rotations)
    return b"".join(struct.pack("ddd",*p) for p in best)

def rank(poly):
    points=read_points(poly/"points");offsets,vertices=read_all_faces(poly/"faces")
    owner=read_label_list(poly/"owner");neighbour=read_label_list(poly/"neighbour")
    note=read_owner_note(poly/"owner")
    text=(poly/"boundary").read_text();patches={}
    for name,body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}",text):
        start=re.search(r"startFace\s+(\d+)",body);count=re.search(r"nFaces\s+(\d+)",body);kind=re.search(r"type\s+(\w+)",body)
        if not(start and count and kind):continue
        start=int(start[1]);count=int(count[1]);kind=kind[1]
        shapes=[];full=[];area=0.
        for f in range(start,start+count):
            xyz=points[vertices[offsets[f]:offsets[f+1]]]
            shapes.append(hashlib.sha256(outline(xyz)).hexdigest())
            loops=[list(range(i,len(xyz)))+list(range(i)) for i in range(len(xyz))]
            best=min((list(map(tuple,xyz[l])),l) for l in loops)[1]
            full.append(hashlib.sha256(xyz[best].astype("<f8").tobytes()).hexdigest())
            area+=float(np.linalg.norm(np.cross(xyz,np.roll(xyz,-1,axis=0)).sum(axis=0)*.5))
        coupled=kind.startswith(("processor","cyclic","mapped")) or "AMI" in kind
        patches[name]={"type":kind,"faceCount":count,"areaM2":area,
            "outlineMultisetSha256":hashlib.sha256("".join(sorted(shapes)).encode()).hexdigest(),
            "coupledExactVertexOrderSha256":hashlib.sha256("".join(full).encode()).hexdigest() if coupled else None}
    return {"cells":note["nCells"],"faces":len(owner),"internalFaces":len(neighbour),"patches":patches}

def main():
    parser=argparse.ArgumentParser();parser.add_argument("before",type=Path);parser.add_argument("after",type=Path);parser.add_argument("output",type=Path);args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    for path in [args.before,args.after,args.output]:
        if not path.resolve().is_relative_to(repo):raise ValueError("Audit paths must remain inside this worktree")
    ranks=sorted(args.before.glob("processor*/constant/polyMesh"));reports=[]
    before_layout={p.relative_to(args.before) for p in ranks}
    after_layout={p.relative_to(args.after) for p in args.after.glob("processor*/constant/polyMesh")}
    if before_layout != after_layout:raise ValueError("Before/after processor layout differs")
    for before in ranks:
        after=args.after/before.relative_to(args.before)
        a=rank(before);b=rank(after)
        keys_equal=a["patches"].keys()==b["patches"].keys()
        good=a["cells"]==b["cells"] and a["faces"]==b["faces"] and a["internalFaces"]==b["internalFaces"] and keys_equal
        differences=[]
        for name,x in a["patches"].items():
            y=b["patches"].get(name)
            ok=y is not None and all(x[k]==y[k] for k in ["type","faceCount","outlineMultisetSha256","coupledExactVertexOrderSha256"])
            relative=abs(x["areaM2"]-y["areaM2"])/max(x["areaM2"],1e-300) if y else None
            ok=ok and relative<=1e-10
            good=good and ok
            if not ok:differences.append({"patch":name,"before":x,"after":y,"relativeAreaError":relative})
        reports.append({"processor":before.parts[-3],"preserved":good,"differences":differences,"before":a,"after":b})
    result={"before":str(args.before),"after":str(args.after),"allPreserved":bool(reports) and all(r["preserved"] for r in reports),"ranks":reports,
        "scope":"Boundary polygon outlines and patch labels; coupled vertex order exact. Not a checkMesh acceptance."}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"allPreserved":result["allPreserved"],"rankCount":len(reports),"differences":sum(len(x["differences"]) for x in reports)}))
if __name__=="__main__":main()
