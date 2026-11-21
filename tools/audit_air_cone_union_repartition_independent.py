#!/usr/bin/env python3
"""Independent pure-math union/repartition proof; never writes a native mesh."""
from __future__ import annotations
import argparse,copy,hashlib,importlib.util,json,sys
from pathlib import Path
import numpy as np
from audit_air_cone_candidate_independent import Stencil,face_geometry,evaluate

def load(name,path):
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def compact(geo,tr,parent,original):
    children=np.flatnonzero(tr["cellParent"]==parent).tolist()
    af=sorted({int(f) for c in children for f in geo.cell(c)[0]})
    outside=sorted({int(geo.owner[f]) if int(geo.owner[f]) not in children else int(geo.neighbour[f])
                    for f in af if f<len(geo.neighbour)
                    and (int(geo.owner[f]) not in children or int(geo.neighbour[f]) not in children)})
    allfaces=sorted(set(af)|{int(f) for c in outside for f in geo.cell(c)[0]})
    cells={str(c):{"faces":list(map(int,geo.cell(c)[0])),"writtenVolume":geo.cell(c)[4],
                   "writtenCentre":geo.cell(c)[3].tolist()} for c in children+outside}
    points=sorted({int(v) for f in allfaces for v in geo.ids(f)})
    common={int(v) for f in geo.cell(children[0])[0] for v in geo.ids(f)}
    for c in children:
        common&={int(v) for f in geo.cell(c)[0] for v in geo.ids(f)}
    assert len(common)==1
    apex=common.pop()
    incidence=np.unique(np.searchsorted(geo.offsets,np.flatnonzero(geo.vertices==apex),side="right")-1)
    incidentCells={int(geo.owner[f]) for f in incidence}|{int(geo.neighbour[f]) for f in incidence if f<len(geo.neighbour)}
    assert incidentCells==set(children)
    mapped=[v for v in points if int(tr["pointNative"][v])<len(tr["points"]) and v!=apex]
    assert all(np.array_equal(geo.points[v].view(np.uint64),tr["points"][int(tr["pointNative"][v])].view(np.uint64)) for v in mapped)
    faces={str(f):{"pointIDs":geo.ids(f).tolist(),"owner":int(geo.owner[f]),
                    "neighbour":int(geo.neighbour[f]) if f<len(geo.neighbour) else None,
                    "patch":geo.patch(f),"baseSourceFace":int(tr["faceBase"][f])} for f in allfaces}
    boundary=[f for f in af if faces[str(f)]["neighbour"] is None
              or not {faces[str(f)]["owner"],faces[str(f)]["neighbour"]}.issubset(children)]
    return {"children":children,"outsideCells":outside,"affectedFaces":af,"originalParentBoundaryFaces":boundary,
            "parentOriginal":original,"apexWrittenPoint":apex,"originalApex":geo.points[apex].tolist(),
            "points":{str(v):geo.points[v].tolist() for v in points},"faces":faces,"cells":cells,
            "allApexIncidentCellsExactChildren":True,"actualApexIncidentCells":sorted(incidentCells),
            "fixedOriginalPointMappings":{str(v):int(tr["pointNative"][v]) for v in mapped},
            "fixedOriginalCoordinatesBitExact":True,
            "apexNativePoint":int(tr["pointNative"][apex])}

def rebuild(doc):
    owned=set(doc["children"]);outside=set(doc["outsideCells"])
    for c in owned|outside:doc["cells"][str(c)]["faces"]=[]
    for k,row in doc["faces"].items():
        for c in [row["owner"],row["neighbour"]]:
            if c in owned|outside:doc["cells"][str(c)]["faces"].append(int(k))
    doc["affectedFaces"]=sorted(int(k) for k,row in doc["faces"].items()
        if row["owner"] in owned or row["neighbour"] in owned)
    doc["originalParentBoundaryFaces"]=[f for f in doc["affectedFaces"]
        if doc["faces"][str(f)]["neighbour"] is None
        or not {doc["faces"][str(f)]["owner"],doc["faces"][str(f)]["neighbour"]}.issubset(owned)]
    return doc

def merged_doc(doc,group,rows):
    out=copy.deepcopy(doc);rep=min(group);group=set(group)
    for f in list(out["faces"]):
        row=out["faces"][f]
        if row["owner"] in group or row["neighbour"] in group:del out["faces"][f]
    for row in rows:
        base=[doc["faces"][str(f)]["baseSourceFace"] for f in row["faces"]]
        assert len(set(base))==1
        f=min(row["faces"])
        out["faces"][str(f)]={"pointIDs":row["ids"],"owner":rep,"neighbour":row["other"],
                             "patch":row["patch"],"baseSourceFace":base[0]}
    out["auditReplacementCells"]=[rep]
    out["children"]=[c for c in out["children"] if c not in group or c==rep]
    for c in group-{rep}:del out["cells"][str(c)]
    return rebuild(out)

def repartition_doc(doc,group,bases):
    """Retain every union external side polygon; replace only the physical fan and interior."""
    out=copy.deepcopy(doc);group=set(group);reps=sorted(group)[:len(bases)];apex=doc["apexWrittenPoint"]
    points={int(k):np.array(v) for k,v in doc["points"].items()}
    physical=[];external=[]
    for k,row in doc["faces"].items():
        own,nei=row["owner"],row["neighbour"]
        if own not in group and nei not in group:continue
        if own in group and nei in group:continue
        ids=row["pointIDs"] if own in group else row["pointIDs"][::-1]
        other=nei if own in group else own
        if row["patch"] is not None:physical.append((int(k),row,ids))
        else:external.append((int(k),row,ids,other))
    assert physical and all(r[1]["patch"]==physical[0][1]["patch"] for r in physical)
    source={r[1]["baseSourceFace"] for r in physical};assert len(source)==1
    originalArea=sum((face_geometry(np.array([points[v] for v in ids]))[0] for _,_,ids in physical),np.zeros(3))
    oriented=[]
    for base in bases:
        sf,_=face_geometry(np.array([points[v] for v in base]))
        oriented.append(base if np.dot(sf,originalArea)>0 else base[::-1])
    edgeOwner={}
    for rep,base in zip(reps,oriented):
        for x,y in zip(base,base[1:]+base[:1]):
            edgeOwner.setdefault(tuple(sorted((x,y))),[]).append((rep,x,y))
    for k in list(out["faces"]):
        row=out["faces"][k]
        if row["owner"] in group or row["neighbour"] in group:del out["faces"][k]
    for f,row,ids,other in external:
        edge=tuple(sorted(v for v in ids if v!=apex))
        assert len(edge)==2 and len(edgeOwner[edge])==1
        rep=edgeOwner[edge][0][0]
        out["faces"][str(f)]={**row,"pointIDs":ids,"owner":rep,"neighbour":other}
    physIDs=[r[0] for r in physical]
    for index,(rep,base) in enumerate(zip(reps,oriented)):
        out["faces"][str(physIDs[index])]={"pointIDs":base,"owner":rep,"neighbour":None,
                    "patch":physical[0][1]["patch"],"baseSourceFace":next(iter(source))}
    interiors=[(e,owners) for e,owners in edgeOwner.items() if len(owners)==2]
    assert len(interiors)==len(bases)-1
    free=-1
    for edge,owners in interiors:
        own,x,y=owners[0];nei=owners[1][0]
        while str(free) in out["faces"]:free-=1
        out["faces"][str(free)]={"pointIDs":[y,x,apex],"owner":own,"neighbour":nei,"patch":None,"baseSourceFace":-1};free-=1
    out["children"]=[c for c in out["children"] if c not in group or c in reps]
    for c in group-set(reps):del out["cells"][str(c)]
    out["repartition"]={"originalCells":sorted(group),"replacementCells":reps,"physicalBases":oriented,
                        "retainedExternalSideFaces":[r[0] for r in external],"sourcePhysicalBase":next(iter(source))}
    return rebuild(out)

def fast(doc,apex):
    m=Stencil(doc,apex)
    ds=[m.cell_detail(c) for c in doc["children"]]
    fs=[m.face_detail(f) for f in doc["affectedFaces"]]
    D=min(v["D"] for v in ds);W=min(v.get("weight",1) for v in fs);R=min(v.get("ratio",1) for v in fs);S=max(v["skew"] for v in fs)
    return {"apex":list(map(float,apex)),"minimumD":D,"minimumWeight":W,"minimumRatio":R,"maximumSkew":S,
            "score":min(D/.001,W/.05,R/.01,4/S),"determinantBad":[v["cell"] for v in ds if v["D"]<.001],
            "weightBad":[v["face"] for v in fs if v.get("weight",1)<.05],
            "ratioBad":[v["face"] for v in fs if v.get("ratio",1)<.01],
            "coplanarOrConcavityTriggers":sum(v["faceCentreConcavityPairTriggers"] for v in ds),
            "maximumVertexOutsideM":max(v["maximumVertexOutsideM"] for v in ds)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--air-root",type=Path,required=True);p.add_argument("--mesh",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    geometry=load("union_independent_geo",root/"tools/audit_air_mesh_geometry.py")
    union=load("union_independent_trace_union",args.air_root/"tools/air_cone_child_union_probe.py")
    trace=load("union_independent_trace",args.air_root/"tools/air_cone_trace_probe.py")
    geo=geometry.Geometry(args.mesh/"processor1/constant/polyMesh");tr=trace.read_trace(args.mesh,1)
    parents=json.loads((args.air_root/"docs/benchmarks/air-product-presets-2026-10-05/strict-stage-conforming-cones-trace-baseline/exact-parent-samples-v2.json").read_text())["parents"]
    cases=[(30503,[526531,526532]),(46851,[604333,604334]),(25147,[507879,507880]),(25148,[507897,507898])]
    args.output.mkdir(parents=True,exist_ok=True)
    summary=[]
    for parent,group in cases:
        original=next(v for v in parents if v["rank"]==1 and v["inputParent"]==parent)
        doc=compact(geo,tr,parent,original)
        rows,_=union.coalesce(geo,union.merged_boundary(geo,group,tr),geometry)
        joined=merged_doc(doc,group,rows)
        variants={"minimalUnion":joined}
        if parent==25147:
            variants["refanFiveToFour"]=repartition_doc(doc,[507878,507879,507880,507881,507882],
                [[19876,19877,43902],[19876,43902,43917],[19876,43917,43942,575910],[19876,575910,21167]])
            variants["refanThreeToTwo"]=repartition_doc(doc,[507879,507880,507881],
                [[43902,19876,575910,43917],[43902,19877,19876]])
        if parent==25148:
            variants["refanFiveToFour"]=repartition_doc(doc,[507896,507897,507898,507899,507900],
                [[19745,19742,43922],[19745,43922,43921],[19745,43921,575876,19985],[19745,19985,19983]])
            variants["refanThreeToTwo"]=repartition_doc(doc,[507897,507898,507899],
                [[43922,19745,19985,43921],[43922,19742,19745]])
        for name,variant in variants.items():
            path=args.output/f"parent{parent}-{name}-context.json"
            path.write_text(json.dumps(variant,indent=2)+"\n")
            candidates={"written":doc["originalApex"]}
            axis=trace.axis_kernel_centre(original["originalFaces"])
            if axis:candidates["axisKernelCentre"]=axis["point"]
            cheb=original["chebyshevKernelCandidate"]
            if cheb.get("success"):candidates["chebyshev"]=cheb["point"]
            results={key:fast(variant,point) for key,point in candidates.items()}
            summary.append({"parent":parent,"variant":name,"children":len(variant["children"]),"outside":len(variant["outsideCells"]),"context":path.name,"results":results})
            print(json.dumps(summary[-1]),flush=True)
    (args.output/"candidate-baseline.json").write_text(json.dumps(summary,indent=2)+"\n")
if __name__=="__main__":main()
