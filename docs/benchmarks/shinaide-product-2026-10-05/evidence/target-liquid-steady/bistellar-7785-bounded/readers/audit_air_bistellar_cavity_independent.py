#!/usr/bin/env python3
"""Independent reconstruction of one rejected original-node 2-to-3 cavity.
No AIR assembly or metric implementation is imported. Original compact arrays,
exact rational geometry and existing installed-OF mathematical oracles are used.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import copy
from fractions import Fraction as F
import hashlib,json,re
from pathlib import Path
import numpy as np
import audit_air_cone_small_union_independent as u

def vec(point):return tuple(F.from_float(float(x)) for x in point)
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def dot(a,b):return sum((x*y for x,y in zip(a,b)),F(0))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def determinant(a,b,c):return dot(a,cross(b,c))

def exact_tet(ids,points):
    p=[vec(points[v]) for v in ids]
    signed=determinant(sub(p[1],p[0]),sub(p[2],p[0]),sub(p[3],p[0]))/6
    volume=abs(signed)
    centre=tuple(sum((q[k] for q in p),F(0))/4 for k in range(3))
    return volume,tuple(volume*x for x in centre)

def exact_intersection(selected,points):
    a,b,c=map(lambda x:vec(points[x]),selected["sharedVertexIds"])
    d,e=map(lambda x:vec(points[x]),selected["oppositeVertexIds"])
    columns=[sub(e,d),tuple(-v for v in sub(b,a)),tuple(-v for v in sub(c,a))]
    matrix=[[columns[j][k] for j in range(3)]+[sub(a,d)[k]] for k in range(3)]
    for col in range(3):
        pivot=next((r for r in range(col,3) if matrix[r][col]),None)
        if pivot is None:raise ValueError("singular cavity intersection")
        matrix[col],matrix[pivot]=matrix[pivot],matrix[col]
        value=matrix[col][col];matrix[col]=[v/value for v in matrix[col]]
        for row in range(3):
            if row==col:continue
            value=matrix[row][col];matrix[row]=[a-value*b for a,b in zip(matrix[row],matrix[col])]
    tau,beta,gamma=[r[3] for r in matrix]
    return {"tau":str(tau),"beta":str(beta),"gamma":str(gamma),"remaining":str(1-beta-gamma),
            "strictInterior":0<tau<1 and beta>0 and gamma>0 and 1-beta-gamma>0}

def outward_faces(ids,points):
    if len(ids)!=4 or len(set(ids))!=4:raise ValueError("four distinct original nodes")
    if exact_tet(ids,points)[0]==0:raise ValueError("degenerate new tet")
    faces=[]
    for omitted in range(4):
        face=[v for j,v in enumerate(ids) if j!=omitted]
        p=list(map(lambda v:vec(points[v]),face));op=vec(points[ids[omitted]])
        if dot(cross(sub(p[1],p[0]),sub(p[2],p[0])),sub(op,p[0]))>0:
            face=[face[0],*reversed(face[1:])]
        faces.append(face)
    return faces

def normalize(raw):
    points={str(p["point"]):p["xyz"] for p in raw["points"]}
    arbitrary=min(map(int,points))
    return {"points":points,"faces":{str(r["face"]):copy.deepcopy(r) for r in raw["faces"]},
       "cells":{str(r["cell"]):{"faces":r["faces"],"sourceParent":r["sourceParent"],
              "writtenCentre":r["originalOFcentre"],"writtenVolume":r["originalVolumeM3"]} for r in raw["cells"]},
       "children":raw["coreCells"],"outsideCells":raw["outsideCells"],
       "apexWrittenPoint":arbitrary,"originalApex":points[str(arbitrary)]}

def rebuild(raw):
    original=normalize(raw);candidate=copy.deepcopy(original)
    points={int(k):v for k,v in original["points"].items()};selected=raw["selected"]
    core=set(raw["coreCells"]);outer=selected["outerFaceIds"];shared=selected["sharedFace"]
    if original["faces"][str(shared)]["sourceZone"]!=-1:raise ValueError("protected old shared face")
    if any(original["faces"][str(f)]["patch"] is not None for f in outer):raise ValueError("only actual unprotected local internal shell")
    newlabels=[max(map(int,original["cells"]))+1+i for i in range(3)]
    newtet=selected["newTetPointIDs"]
    incident=defaultdict(list)
    for cell,tet in zip(newlabels,newtet):
        for ids in outward_faces(tet,points):incident[tuple(sorted(ids))].append((cell,ids))
    exterior={key:val for key,val in incident.items() if len(val)==1}
    interior={key:val for key,val in incident.items() if len(val)==2}
    oldkeys={tuple(sorted(original["faces"][str(f)]["pointIDs"])):f for f in outer}
    if set(exterior)!=set(oldkeys) or len(exterior)!=6 or len(interior)!=3 or len(incident)!=9:
        raise ValueError("exact original six-facet shell required")
    del candidate["faces"][str(shared)]
    for cell in core:del candidate["cells"][str(cell)]
    for cell in newlabels:candidate["cells"][str(cell)]={"faces":[],"sourceParent":None,"sourceParentsInherited":selected["sourceParents"]}
    face_lineage={};cell_lineage={cell:sorted(core) for cell in newlabels}
    for key,entries in sorted(exterior.items()):
        cell,ids=entries[0];face=oldkeys[key];row=candidate["faces"][str(face)]
        was_owner=row["owner"] in core
        expected=row["pointIDs"] if was_owner else [row["pointIDs"][0],*reversed(row["pointIDs"][1:])]
        if Counter(zip(ids,ids[1:]+ids[:1]))!=Counter(zip(expected,expected[1:]+expected[:1])):
            raise ValueError("original outer oriented cycle mismatch")
        if was_owner:row["owner"]=cell
        else:row["neighbour"]=cell
        candidate["cells"][str(cell)]["faces"].append(face);face_lineage[face]=[face]
    first=max(map(int,original["faces"]))+1
    for n,(key,entries) in enumerate(sorted(interior.items())):
        (owner,ids),(neighbour,other)=sorted(entries)
        if Counter(zip(ids,ids[1:]+ids[:1]))!=Counter((b,a) for a,b in zip(other,other[1:]+other[:1])):
            raise ValueError("new internal directed cycle mismatch")
        face=first+n
        candidate["faces"][str(face)]={"owner":owner,"neighbour":neighbour,"pointIDs":ids,"patch":None,"sourceBase":-1,"sourceZone":-1}
        for cell in [owner,neighbour]:candidate["cells"][str(cell)]["faces"].append(face)
        face_lineage[face]=[shared]
    for cell in newlabels:candidate["cells"][str(cell)]["faces"].sort()
    candidate["children"]=newlabels
    return original,candidate,cell_lineage,face_lineage

def summarize(q):
    return {"Dmin":min(r["D"] for r in q["cells"]),
     "Wmin":min(r["weight"] for r in q["faces"]),"Rmin":min(r["ratio"] for r in q["faces"]),
     "Smax":max(r["skew"] for r in q["faces"]),
     "Nmax":max(r["nonOrthogonalityDegrees"] for r in q["faces"]),
     "Dfail":sum(r["D"]<.001 for r in q["cells"]),
     "Wfail":sum(r["weight"]<.05 for r in q["faces"]),
     "Rfail":sum(r["ratio"]<.01 for r in q["faces"]),
     "Sfail":sum(r["skew"]>4 for r in q["faces"]),
     "Nfail":sum(r["nonOrthogonalityDegrees"]>70 for r in q["faces"])}

def analyze(raw,mesh=None):
    original,candidate,cell_lineage,face_lineage=rebuild(raw)
    old,oq=u.family_metrics(original);new,nq=u.family_metrics(candidate)
    points={int(k):v for k,v in original["points"].items()}
    intersection=exact_intersection(raw["selected"],points)
    cavity_ids=set(raw["selected"]["sharedVertexIds"]+raw["selected"]["oppositeVertexIds"])
    halfspaces=[]
    for f in raw["selected"]["outerFaceIds"]:
        row=original["faces"][str(f)];ids=row["pointIDs"]
        if row["owner"] not in original["children"]:ids=[ids[0],*reversed(ids[1:])]
        a,b,c=[vec(points[v]) for v in ids];normal=cross(sub(b,a),sub(c,a))
        vals=[dot(normal,sub(vec(points[v]),a)) for v in cavity_ids]
        halfspaces.append({"face":f,"maximumOutwardRawExact":str(max(vals)),"passes":max(vals)<=0})
    oldtets=[sorted({v for f in original["cells"][str(c)]["faces"] for v in original["faces"][str(f)]["pointIDs"]}) for c in original["children"]]
    exactold=[exact_tet(t,points) for t in oldtets]
    exactnew=[exact_tet(t,points) for t in raw["selected"]["newTetPointIDs"]]
    oldv=sum((q[0] for q in exactold),F(0));newv=sum((q[0] for q in exactnew),F(0))
    oldm=tuple(sum((q[1][k] for q in exactold),F(0)) for k in range(3))
    newm=tuple(sum((q[1][k] for q in exactnew),F(0)) for k in range(3))
    regressions=[]
    for metric,(threshold,upper) in u.LIMITS.items():
        collection="cells" if metric=="D" else "faces";label="cell" if metric=="D" else "face"
        before={r[label]:r[metric] for r in oq[collection] if metric in r}
        lineage=cell_lineage if metric=="D" else face_lineage
        bad=lambda v:v>threshold if upper else v<threshold
        for row in nq[collection]:
            if metric not in row:continue
            origins=lineage[row[label]]
            passing=[c for c in origins if not bad(before[c])]
            if passing and bad(row[metric]):
                regressions.append({"replacement":row[label],"metric":metric,"originalPassingRoles":passing,"value":row[metric]})
    outside=[]
    for c in original["outsideCells"]:
        a=old.cell(c);b=new.cell(c)
        outside.append({"cell":c,"centreBitExact":bool(np.array_equal(a[2].view(np.uint64),b[2].view(np.uint64))),"volumeExact":a[3]==b[3]})
    shell=[]
    for face in raw["selected"]["outerFaceIds"]:
        a=original["faces"][str(face)];b=candidate["faces"][str(face)]
        outsideold=a["neighbour"] if a["owner"] in original["children"] else a["owner"]
        outsidenew=b["neighbour"] if b["owner"] in candidate["children"] else b["owner"]
        shell.append({"face":face,"originalCyclePatchZoneBaseUnchanged":all(a[k]==b[k] for k in ("pointIDs","patch","sourceZone","sourceBase")),"outsideNeighbourUnchanged":outsideold==outsidenew})
    canon=u.verify_role_canonicalization(candidate)
    geometry=(intersection["strictInterior"] and oldv==newv and oldm==newm
       and all(row["originalCyclePatchZoneBaseUnchanged"] and row["outsideNeighbourUnchanged"] for row in shell)
       and all(row["centreBitExact"] and row["volumeExact"] for row in outside)
       and all(row["passes"] for row in halfspaces)
       and all(c["V"]>0 and c["faceCentreConcavityPairTriggers"]==0 and c["edgesExactlyTwo"] and c["edgesOppositeOrientation"]
               and c["relativeClosure"]<1e-10 and c["minCentroidPyramidVolume"]>0 for c in nq["cells"])
       and all(f["minimumCentreFanTetQuality"]>1e-30 and f["bestSharedBasePointMinimumTetQuality"]>1e-30 and np.linalg.norm(new.face(f["face"])[0])>1e-12 for f in nq["faces"]))
    source=None
    if mesh:
        source_raw={"mesh":str(mesh),"rank":raw["rank"],"points":original["points"],"faces":original["faces"],
          "cells":{str(r["cell"]):{"faces":r["faces"],"CC":r["originalOFcentre"],"V":r["originalVolumeM3"],"sourceParent":r["sourceParent"]} for r in raw["cells"]}}
        source=u.verify_written_source(source_raw)
        native_array=np.memmap(mesh/f"conforming-written-rank{raw['rank']}-point-native.i32",mode="r",dtype="<i4")
        if any(int(native_array[r["point"]])!=r["nativePoint"] for r in raw["points"]):raise ValueError("original point-native trace mismatch")
        source["pointNativeTraceVerified"]=len(raw["points"])
        source["traceSha256"][f"conforming-written-rank{raw['rank']}-point-native.i32"]=u.g.sha(mesh/f"conforming-written-rank{raw['rank']}-point-native.i32")
        source["writtenCellZonesEmpty"]=bool(re.search(r"\n0\(\)",(mesh/f"processor{raw['rank']}/constant/polyMesh/cellZones").read_text()))
        if not source["writtenCellZonesEmpty"]:raise ValueError("unsupported original cell zone membership")
    oldsummary=summarize(oq);newsummary=summarize(nq)
    return {"schema":"bistellar-cavity-independent/v1","scope":"One original-node offline 2-to-3 candidate. Geometry preserved but D/N rejection. No native or whole-mesh acceptance.",
        "sourceVerification":source,"pointsUnchanged":original["points"]==candidate["points"],
        "exactIntersection":intersection,"exactOriginalCavityVertexHalfspaces":halfspaces,"exactVolumeSumOld":str(oldv),"exactVolumeSumNew":str(newv),
        "exactFirstMomentOld":list(map(str,oldm)),"exactFirstMomentNew":list(map(str,newm)),
        "exactNewTetVolume":list(map(str,[r[0] for r in exactnew])),
        "originalOuterShell":shell,"completeOutsideCells":outside,
        "old":oldsummary,"new":newsummary,"oldCells":oq["cells"],"newCells":nq["cells"],
        "oldFaces":oq["faces"],"newFaces":nq["faces"],
        "cellLineage":cell_lineage,"faceLineage":face_lineage,"passingRoleRegressions":regressions,
        "canonicalOwnerNeighbourAudit":canon,"geometryPass":bool(geometry),
        "allFiveLocalQualityAndRoleInheritancePass":bool(geometry and not regressions and all(newsummary[k]==0 for k in ("Dfail","Wfail","Rfail","Sfail","Nfail")))}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--input",type=Path,required=True)
    p.add_argument("--mesh",type=Path);p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    raw=json.loads(args.input.read_text());out=analyze(raw,args.mesh)
    out["inputIdentity"]={"path":str(args.input),"sha256":hashlib.sha256(args.input.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,indent=2,allow_nan=False)+"\n")
    print(json.dumps({key:out[key] for key in ("old","new","geometryPass","allFiveLocalQualityAndRoleInheritancePass","passingRoleRegressions")},indent=2))
if __name__=="__main__":main()
