#!/usr/bin/env python3
"""Read-only original-node 2-to-3 cavity quality on a complete local stencil.

No native mesh, node, boundary role or solver is modified. Exact partition
preflight is separate from all quality and replacement-role checks.
"""
from __future__ import annotations
import argparse,collections,hashlib,importlib.util,json,itertools
from pathlib import Path
import numpy as np
from air_cone_trace_probe import read_trace,load_geometry
from audit_air_bistellar_cavity import exact_xyz,dot,cross,sub

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical_owner_cycle(owner,neighbour,cycle):
    """Orient an internal face toward the greater cell label, preserving point0."""
    if neighbour is not None and owner>neighbour:
        return neighbour,owner,cycle[:1]+cycle[:0:-1]
    return owner,neighbour,list(cycle)

def export_stencil(mesh,rank,search,geometry):
    g=geometry.Geometry(mesh/f"processor{rank}/constant/polyMesh");t=read_trace(mesh,rank);selected=search["selected"]
    if not selected:raise ValueError("An exact legal selected cavity is required")
    core=set(selected["cells"]);fs=set(int(f) for c in core for f in g.cell(c)[0]);outside=set()
    for f in fs:
        if f<len(g.neighbour):outside.update((int(g.owner[f]),int(g.neighbour[f])))
        elif g.patch(f) and g.patch(f)["type"] in {"cyclic","processor","processorCyclic"}:raise ValueError("Unknown coupled remote stencil")
    outside-=core;cells=core|outside;allfs=set(int(f) for c in cells for f in g.cell(c)[0]);pids=sorted({int(p) for f in allfs for p in g.ids(f)})
    rows=[]
    for f in sorted(allfs):
        b=int(t["faceBase"][f]);sf,fc,*_=g.face(f)
        rows.append({"face":f,"pointIDs":g.ids(f).tolist(),"owner":int(g.owner[f]),"neighbour":int(g.neighbour[f]) if f<len(g.neighbour) else None,"patch":g.patch(f),"sourceBase":b,"sourceZone":int(t["zone"][b]) if b>=0 else -1,"originalAreaVector":sf.tolist(),"originalOFcentre":fc.tolist()})
    return {"format":"bistellar-complete-cavity-stencil/v1","scope":"Original cavity and every direct exterior neighbour with its complete original topology. No mesh write.","rank":rank,"mesh":str(mesh),"selected":selected,
      "coreCells":sorted(core),"outsideCells":sorted(outside),"points":[{"point":p,"xyz":g.points[p].tolist(),"nativePoint":int(t["pointNative"][p])} for p in pids],"faces":rows,
      "cells":[{"cell":c,"sourceParent":int(t["cellParent"][c]),"faces":g.cell(c)[0].tolist(),"originalOFcentre":g.cell(c)[3].tolist(),"originalVolumeM3":g.cell(c)[4]} for c in sorted(cells)],
      "sourceSHA256":{p.name:sha(p) for p in mesh.glob(f"conforming-*-rank{rank}-manifest.json")},"writtenSHA256":{name:sha(g.poly_dir/name) for name in ["points","faces","owner","neighbour","boundary"]},"searchReceiptSHA256":None}

def outward_tet_faces(ids,points):
    if len(ids)!=4 or len(set(ids))!=4:raise ValueError("Four distinct tetra vertices required")
    result=[]
    for omitted in range(4):
        face=[p for j,p in enumerate(ids) if j!=omitted];a,b,c=map(exact_xyz,[points[p] for p in face]);op=exact_xyz(points[ids[omitted]])
        value=dot(cross(sub(b,a),sub(c,a)),sub(op,a))
        if value==0:raise ValueError("Degenerate tetrahedron")
        if value>0:face=face[:1]+face[:0:-1]
        result.append(face)
    return result

def analyze(doc,G,M,F):
    points={r["point"]:np.asarray(r["xyz"],float) for r in doc["points"]};
    if not points or not all(np.isfinite(p).all() for p in points.values()):raise ValueError("Finite actual coordinates required")
    faces={r["face"]:r for r in doc["faces"]};cells={r["cell"]:r for r in doc["cells"]};old=set(doc["coreCells"]);outer=set(doc["selected"]["outerFaceIds"])
    def geom(polygons):
        labels=sorted({p for f in polygons for p in f});idx={p:i for i,p in enumerate(labels)}
        return G.Geometry.from_arrays([points[p] for p in labels],[[idx[p] for p in f] for f in polygons])
    oldgeom={}
    for c,row in cells.items():
        polygons=[]
        for f in row["faces"]:
            r=faces[f];ids=r["pointIDs"];polygons.append(ids if r["owner"]==c else ids[:1]+ids[:0:-1])
        oldgeom[c]=geom(polygons)
    newlabels=[min(old),max(old),max(cells)+1]
    canonical_rows=[]

    newpolygons=[outward_tet_faces(ids,points) for ids in doc["selected"]["newTetPointIDs"]];newgeom=[geom(ps) for ps in newpolygons]
    incidence=collections.defaultdict(list)
    for i,ps in enumerate(newpolygons):
        for ids in ps:incidence[tuple(sorted(ids))].append((i,ids))
    outerkeys={tuple(sorted(faces[f]["pointIDs"])):f for f in outer}
    if len(outerkeys)!=6 or len(incidence)!=9:raise ValueError("Six exterior and three interior triangles required")
    exterior={k for k,v in incidence.items() if len(v)==1};interior={k for k,v in incidence.items() if len(v)==2}
    if exterior!=set(outerkeys) or len(interior)!=3:raise ValueError("Original six exterior triangles must be preserved exactly")
    def detrow(geo,polygons,patches,label,lineage):
        sf=geo.cell(0)[1];selected=[a for a,p in zip(sf,patches) if p is None or p["type"] in {"cyclic","processor","processorCyclic"}]
        edge=collections.Counter((a,b) for ids in polygons for a,b in zip(ids,ids[1:]+ids[:1]));close=all(n==1 and edge[(b,a)]==1 for (a,b),n in edge.items())
        return {"cell":label,"replacesOriginalCellRoles":lineage,"determinant":M.determinant(selected)[0],"selectedFaces":len(selected),"CC":geo.cell(0)[3].tolist(),"V":geo.cell(0)[4],"directedEdgesPairExactly":close,"geometry":geo.classify(0,True)}
    oldrows=[]
    for c in sorted(old):
        ps=[];patches=[]
        for f in cells[c]["faces"]:
            r=faces[f];ids=r["pointIDs"];ps.append(ids if r["owner"]==c else ids[:1]+ids[:0:-1]);patches.append(r["patch"])
        oldrows.append(detrow(oldgeom[c],ps,patches,c,[c]))
    newrows=[]
    for i,ps in enumerate(newpolygons):
        patches=[faces[outerkeys[tuple(sorted(ids))]]["patch"] if tuple(sorted(ids)) in outerkeys else None for ids in ps]
        newrows.append(detrow(newgeom[i],ps,patches,"new"+str(i),sorted(old)))
    def metrics(sf,fc,ids,owngeo,neigeo):
        own=owngeo.cell(0);nei=neigeo.cell(0) if neigeo else None
        row=M.face_metrics(sf,fc,own[3],own[4],[points[p] for p in ids],nei[3] if nei else None,nei[4] if nei else None)
        if nei:
            delta=nei[3]-own[3];co=np.dot(sf,delta)/(np.linalg.norm(sf)*np.linalg.norm(delta));row["nonOrthogonalityDegrees"]=float(np.degrees(np.arccos(np.clip(co,-1,1))))
        row["fanQuality"]=F.fan_quality([points[p] for p in ids],own[3],nei[3] if nei else None);row["areaM2"]=float(np.linalg.norm(sf))
        return row
    oldface=[]
    for f in sorted(outer|{doc["selected"]["sharedFace"]}):
        r=faces[f];fg=G.Geometry.from_arrays([points[p] for p in r["pointIDs"]],[list(range(3))]);sf,fc,*_=fg.face(0)
        oldface.append({"face":f,"replacesOriginalFaceRoles":[f],**metrics(sf,fc,r["pointIDs"],oldgeom[r["owner"]],oldgeom[r["neighbour"]] if r["neighbour"] is not None else None)})
    newface=[]
    for k,v in sorted(incidence.items()):
        i,ids=v[0];fg=G.Geometry.from_arrays([points[p] for p in ids],[list(range(3))]);sf,fc,*_=fg.face(0);j=v[1][0] if len(v)==2 else None
        if j is not None:
            expected=collections.Counter(zip(ids,ids[1:]+ids[:1]));other=v[1][1]
            if expected!=collections.Counter((b,a) for a,b in zip(other,other[1:]+other[:1])):raise ValueError("New shared triangle must reverse every directed edge exactly")
            othergeo=newgeom[j];f=None;lineage=[doc["selected"]["sharedFace"]]
        else:
            f=outerkeys[k];r=faces[f];outside=r["owner"] if r["owner"] not in old else r["neighbour"]
            othergeo=oldgeom[outside] if outside is not None else None;lineage=[f]
            outward=r["pointIDs"] if r["owner"] in old else r["pointIDs"][:1]+r["pointIDs"][:0:-1]
            if collections.Counter(zip(ids,ids[1:]+ids[:1]))!=collections.Counter(zip(outward,outward[1:]+outward[:1])):raise ValueError("Original exterior oriented triangle changed")
        ownlabel=newlabels[i];neilabel=newlabels[j] if j is not None else outside
        canonicalOwn,canonicalNei,canonicalCycle=canonical_owner_cycle(ownlabel,neilabel,ids)
        check=G.Geometry.from_arrays([points[p] for p in canonicalCycle],[list(range(3))]);ca,cc,*_=check.face(0)
        reversedSense=canonicalOwn!=ownlabel
        if not np.array_equal(cc,fc):raise ValueError("Canonical owner reversal must preserve triangle centre exactly")
        if np.linalg.norm(ca-(-sf if reversedSense else sf))>1e-14*np.linalg.norm(sf):raise ValueError("Canonical owner reversal lost area-vector direction")
        canonical_rows.append({"face":f if f is not None else "new-internal-"+"-".join(map(str,k)),"uncompactedTrialOwner":ownlabel,"uncompactedTrialNeighbour":neilabel,"canonicalOwner":canonicalOwn,"canonicalNeighbour":canonicalNei,"canonicalPointIDs":canonicalCycle,"point0Preserved":canonicalCycle[0]==ids[0],"ownerOrderMonotone":canonicalNei is None or canonicalOwn<canonicalNei,"normalReversedWhenRequired":reversedSense,"centreExactUnchanged":True})
        newface.append({"face":f if f is not None else "new-internal-"+"-".join(map(str,k)),"newOwner":i,"newNeighbour":j,"pointIDs":ids,"replacesOriginalFaceRoles":lineage,"sourcePatch":faces[f]["patch"] if f is not None else None,"sourceZone":faces[f]["sourceZone"] if f is not None else -1,**metrics(sf,fc,ids,newgeom[i],othergeo)})
    def summary(cr,fr):
        return {"Dmin":min(r["determinant"] for r in cr),"Wmin":min(r.get("weight",1.) for r in fr),"Rmin":min(r.get("volumeRatio",1.) for r in fr),"Smax":max(r["skewness"] for r in fr),"Nmax":max(r.get("nonOrthogonalityDegrees",0.) for r in fr),"Dfail":sum(r["determinant"]<.001 for r in cr),"Wfail":sum(r.get("weight",1.)<.05 for r in fr),"Rfail":sum(r.get("volumeRatio",1.)<.01 for r in fr),"Sfail":sum(r["skewness"]>4 for r in fr),"Nfail":sum(r.get("nonOrthogonalityDegrees",0.)>70 for r in fr)}
    oldsum=summary(oldrows,oldface);newsum=summary(newrows,newface)
    oldv=sum(r["V"] for r in oldrows);newv=sum(r["V"] for r in newrows);oldm=sum((r["V"]*np.asarray(r["CC"]) for r in oldrows),np.zeros(3));newm=sum((r["V"]*np.asarray(r["CC"]) for r in newrows),np.zeros(3))
    # Each new tetrahedron inherits every removed cell's original passing role.
    rolefail=[]
    for r in newrows:
        if any(q["determinant"]>=.001 for q in oldrows) and r["determinant"]<.001:rolefail.append({"cell":r["cell"],"metric":"D","reason":"removedPassingCellRoleMustRemainPassing"})
    of={r["face"]:r for r in oldface}
    for r in newface:
        for key,threshold,direction in [("weight",.05,1),("volumeRatio",.01,1),("skewness",4,-1),("nonOrthogonalityDegrees",70,-1)]:
            if key not in r:continue
            passing=any(direction*of[f].get(key,threshold)>=direction*threshold for f in r["replacesOriginalFaceRoles"])
            if passing and direction*r[key]<direction*threshold:rolefail.append({"face":r["face"],"metric":key,"reason":"originalPassingFaceRoleMustRemainPassing"})
    geometrypass=all(r["directedEdgesPairExactly"] and r["V"]>0 and sum(r["geometry"]["checkerTriggerCounts"].values())==0 and r["geometry"]["relativeClosure"]<1e-10 for r in newrows) and all(r["areaM2"]>1e-12 and r["fanQuality"]["minimumQuality"]>1e-30 for r in newface)
    fullpass=geometrypass and all(newsum[k]==0 for k in ["Dfail","Wfail","Rfail","Sfail","Nfail"]) and not rolefail
    return {"format":"bistellar-complete-local-quality/v1","scope":"Read-only local candidate on unchanged original nodes and six exterior facets. Actual checker and whole-mesh budget not run. No production acceptance.","selected":doc["selected"],"old":oldsum,"new":newsum,"localAllFiveAndGeometryPass":fullpass,"geometryPass":geometrypass,"passingRoleRegression":rolefail,"oldCells":oldrows,"newCells":newrows,"oldFaces":oldface,"newFaces":newface,"relativeVolumeDifference":abs(newv-oldv)/oldv,"firstMomentDifferenceM4":(newm-oldm).tolist(),"originalNodeCoordinatesUnchanged":True,"originalExteriorPointCyclesRolesAndOutsideNeighboursPreserved":True,"originalOutsideCellsCompleteCount":len(doc["outsideCells"]),"canonicalOwnerProof":canonical_rows,"outsideCCVMaximumReconstructionError":max(float(np.max(abs(oldgeom[c].cell(0)[3]-np.asarray(cells[c]["originalOFcentre"])))) for c in doc["outsideCells"])}

def main():
    a=argparse.ArgumentParser();a.add_argument("--mesh",type=Path);a.add_argument("--rank",type=int,default=1);a.add_argument("--search",type=Path);a.add_argument("--stencil",type=Path,required=True);a.add_argument("--geometry-tool",type=Path,required=True);a.add_argument("--output",type=Path,required=True);args=a.parse_args()
    G=load_geometry(args.geometry_tool);M=module(args.geometry_tool.with_name("audit_air_cone_remaining_quality.py"),"remaining");F=module(args.geometry_tool.with_name("audit_air_face_partition.py"),"fan")
    if args.search:
        doc=export_stencil(args.mesh,args.rank,json.loads(args.search.read_text()),G);doc["searchReceiptSHA256"]=sha(args.search);args.stencil.parent.mkdir(parents=True,exist_ok=True);args.stencil.write_text(json.dumps(doc,indent=2)+"\n")
    else:doc=json.loads(args.stencil.read_text())
    result=analyze(doc,G,M,F);result["inputSHA256"]={str(p):sha(p) for p in [args.stencil,Path(__file__),args.geometry_tool,args.geometry_tool.with_name("audit_air_cone_remaining_quality.py"),args.geometry_tool.with_name("audit_air_face_partition.py")]};args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+"\n");print(json.dumps({k:result[k] for k in ["old","new","geometryPass","localAllFiveAndGeometryPass","passingRoleRegression","relativeVolumeDifference","firstMomentDifferenceM4"]}))
if __name__=="__main__":main()
