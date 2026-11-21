#!/usr/bin/env python3
"""Independent assembly audit of air's exact analytic five-cell roof fixture."""
from __future__ import annotations
import argparse,importlib.util,json,math,sys
from pathlib import Path
from collections import Counter
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_air_mesh_geometry import Geometry,sha

def load_air(path):
    spec=importlib.util.spec_from_file_location("air_roof_original",path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def assemble(reports):
    coordinates={};incidences={}
    for c,report in enumerate(reports):
        for face in report["faces"]:
            ids=face["vertexIds"]
            for i,p in zip(ids,face["points"]):
                value=tuple(p)
                if i in coordinates and coordinates[i]!=value:raise ValueError("Shared point mismatch")
                coordinates[i]=value
            key=tuple(sorted(ids))
            incidences.setdefault(key,[]).append((c,ids))
    if sorted(coordinates)!=list(range(len(coordinates))):raise ValueError("Noncontiguous fixture vertices")
    internal=[];boundary=[]
    for items in incidences.values():
        if len(items)==1:boundary.append(items[0])
        elif len(items)==2:
            (c,a),(d,b)=items
            if Counter(zip(a,a[1:]+a[:1]))!=Counter((y,x) for x,y in zip(b,b[1:]+b[:1])):
                raise ValueError("Shared orientation mismatch")
            internal.append((c,d,a))
        else:raise ValueError("Face belongs to >2 cells")
    internal.sort(key=lambda row:(row[0],row[1],tuple(row[2])))
    boundary.sort(key=lambda row:(row[0],tuple(row[1])))
    faces=[row[2] for row in internal]+[row[1] for row in boundary]
    owner=np.array([row[0] for row in internal]+[row[0] for row in boundary],dtype=np.int64)
    neighbour=np.array([row[1] for row in internal],dtype=np.int64)
    g=Geometry.from_arrays([coordinates[i] for i in range(len(coordinates))],faces)
    g.owner=owner;g.neighbour=neighbour;g.n_cells=len(reports);g.n_faces=len(faces)
    labels=np.r_[owner,neighbour]
    face_ids=np.r_[np.arange(g.n_faces),np.arange(len(neighbour))]
    g.cell_faces=face_ids[np.argsort(labels,kind="stable")]
    g.cell_offsets=np.r_[0,np.cumsum(np.bincount(labels,minlength=g.n_cells))]
    return g

def quality(a,b,c,d):
    av=b-a;bv=c-a;cv=d-a
    volume=float(np.dot(np.cross(av,bv),cv))/6
    ba=np.cross(bv,av);ca=np.cross(cv,av)
    denom=float(cv@ba)
    if abs(denom)<1e-150:radius=1e15
    else:
        lam=float(cv@cv-av@cv);mu=float(bv@bv-av@bv)
        radius=float(np.linalg.norm(.5*(av+(lam*ba-mu*ca)/denom)))
    return volume/(8/(9*math.sqrt(3))*min(radius,1e15)**3+1e-150)

def face_fans(g,f):
    pts=g.points[g.ids(f)];fc=g.face(f)[1]
    own=g.cell(int(g.owner[f]))[3]
    nei=g.cell(int(g.neighbour[f]))[3] if f<len(g.neighbour) else None
    own_centroid=min(-quality(a,b,fc,own) for a,b in zip(pts,np.roll(pts,-1,axis=0)))
    nei_centroid=min(quality(a,b,fc,nei) for a,b in zip(pts,np.roll(pts,-1,axis=0))) if nei is not None else None
    candidates=[]
    for k,base in enumerate(pts):
        ownq=[];neiq=[]
        for i in range(1,len(pts)-1):
            a=pts[(k+i)%len(pts)];b=pts[(k+i+1)%len(pts)]
            ownq.append(quality(own,base,a,b))
            if nei is not None:neiq.append(quality(nei,base,b,a))
        candidates.append(min(ownq+neiq))
    return {"face":f,"owner":int(g.owner[f]),"neighbour":int(g.neighbour[f]) if nei is not None else None,
            "ownerCentroidFanMinimumQuality":own_centroid,"neighbourCentroidFanMinimumQuality":nei_centroid,
            "sharedOriginalVertexFanBestMinimumQuality":max(candidates)}

def evaluate(air,audit_module,epsilon):
    source=air.evaluate(audit_module,epsilon)
    g=assemble(source["cells"])
    return g,audit_assembled(g,epsilon)

def audit_assembled(g,epsilon):
    cells=[];interfaces=[]
    for c in range(g.n_cells):
        row=g.classify(c,True)
        # Directed edge count and orientation are checked independently.
        edges=Counter()
        for f in g.cell_faces[g.cell_offsets[c]:g.cell_offsets[c+1]]:
            ids=g.ids(int(f)).tolist()
            if g.owner[f]!=c:ids=ids[::-1]
            edges.update(zip(ids,ids[1:]+ids[:1]))
        row["oppositeEdgeCyclesClosed"]=all(n==1 and edges[(b,a)]==1 for (a,b),n in edges.items())
        fs,areas,centres,_,_,v,*_=g.cell(c);points=g.points[v]
        minimum_margin=math.inf;max_dot=-math.inf
        for i,f in enumerate(fs):
            n=areas[i]/np.linalg.norm(areas[i])
            ids=set(g.ids(int(f)).tolist())
            for p in v:
                if p not in ids:minimum_margin=min(minimum_margin,-float((g.points[p]-centres[i])@n))
            for j,other in enumerate(fs):
                if i==j:continue
                delta=centres[j]-centres[i]
                max_dot=max(max_dot,float(delta@n/np.linalg.norm(delta)))
        row["minimumNonincidentVertexInnerMarginM"]=minimum_margin
        row["maximumOtherFaceCentreOutwardUnitDot"]=max_dot
        cells.append(row)
    for f in range(len(g.neighbour)):
        a,fc,*_=g.face(f)
        o=int(g.owner[f]);n=int(g.neighbour[f])
        d0=abs(float(a@(fc-g.cell(o)[3])));d1=abs(float(a@(g.cell(n)[3]-fc)))
        vo=g.cell(o)[4];vn=g.cell(n)[4]
        interfaces.append({"face":f,"owner":o,"neighbour":n,"weight":min(d0,d1)/(d0+d1+1e-300),
                           "volumeRatio":min(vo,vn)/max(vo,vn)})
    fans=[face_fans(g,f) for f in range(g.n_faces)]
    summary={"epsilonMetres":epsilon,"points":len(g.points),"faces":g.n_faces,"internalFaces":len(g.neighbour),
             "cells":cells,"interfaces":interfaces,"tetFans":fans,
             "totalVolumeM3":sum(row["volumeM3"] for row in cells),
             "allOppositeEdgeCyclesClosed":all(row["oppositeEdgeCyclesClosed"] for row in cells),
             "allFacesPlanar":all(row["allFacePolygonsPlanar"] for row in cells),
             "allFacesConvex":all(row["allFacePolygonsConvex"] for row in cells),
             "allCellsStrictlyInsideFaceCentreCriterion":all(row["maximumOtherFaceCentreOutwardUnitDot"] < -1e-6 for row in cells),
             "allNonincidentVerticesStrictInside":all(row["minimumNonincidentVertexInnerMarginM"]>0 for row in cells),
             "minimumTetFanQuality":min(min(x["ownerCentroidFanMinimumQuality"],x["neighbourCentroidFanMinimumQuality"] if x["neighbourCentroidFanMinimumQuality"] is not None else math.inf,x["sharedOriginalVertexFanBestMinimumQuality"]) for x in fans),
             "minimumInternalWeight":min(x["weight"] for x in interfaces),"minimumInternalVolumeRatio":min(x["volumeRatio"] for x in interfaces)}
    return summary

def write_fixture(g,path):
    poly=path/"constant/polyMesh";poly.mkdir(parents=True,exist_ok=True)
    system=path/"system";system.mkdir(exist_ok=True)
    def header(name,kind):return f"FoamFile {{version 2.0;format ascii;class {kind};object {name};}}\n"
    (poly/"points").write_text(header("points","vectorField")+str(len(g.points))+"\n(\n"+"\n".join("("+" ".join(format(x,".17g") for x in p)+")" for p in g.points)+"\n)\n")
    (poly/"faces").write_text(header("faces","faceList")+str(g.n_faces)+"\n(\n"+"\n".join(str(len(g.ids(f)))+"("+" ".join(map(str,g.ids(f)))+")" for f in range(g.n_faces))+"\n)\n")
    for name,ids in [("owner",g.owner),("neighbour",g.neighbour)]:
        (poly/name).write_text(header(name,"labelList")+str(len(ids))+"\n(\n"+"\n".join(map(str,ids))+"\n)\n")
    (poly/"boundary").write_text(header("boundary","polyBoundaryMesh")+f"1\n(\nwalls {{type wall;nFaces {g.n_faces-len(g.neighbour)};startFace {len(g.neighbour)};}}\n)\n")
    (system/"controlDict").write_text(header("controlDict","dictionary")+"application checkMesh;startFrom startTime;startTime 0;stopAt endTime;endTime 1;deltaT 1;writeControl timeStep;writeInterval 1;")
    (system/"fvSchemes").write_text(header("fvSchemes","dictionary")+"ddtSchemes{default steadyState;} gradSchemes{default Gauss linear;} divSchemes{default none;} laplacianSchemes{default Gauss linear corrected;} interpolationSchemes{default linear;} snGradSchemes{default corrected;}")
    (system/"fvSolution").write_text(header("fvSolution","dictionary")+"solvers{}")
    (system/"decomposeParDict").write_text(header("decomposeParDict","dictionary")+"numberOfSubdomains 2;method manual;manualCoeffs{dataFile cellDecomp;}")
    labels=[0 if g.cell(c)[3][0]<1 else 1 for c in range(g.n_cells)]
    (path/"constant/cellDecomp").write_text(header("cellDecomp","labelList")+str(g.n_cells)+"\n("+" ".join(map(str,labels))+")\n")

def evaluate_buffer(air,audit_module,epsilon):
    # 3x3 exact translated packets, then 3x3 normal coarse left neighbors.
    # This makes the central original packet interior without changing it.
    original=air.evaluate(audit_module,epsilon)
    points=[];lookup={};reports=[]
    def add_cell(faces):
        items=[]
        for face in faces:
            ids=[];coords=[]
            for point in face:
                key=tuple(float(x) for x in point)
                if key not in lookup:lookup[key]=len(points);points.append(key)
                ids.append(lookup[key]);coords.append(list(key))
            items.append({"vertexIds":ids,"points":coords})
        reports.append({"faces":items})
    for dy in [-1,0,1]:
        for dz in [-1,0,1]:
            for cell in original["cells"]:
                add_cell([[np.array(point)+np.array([0,dy,dz]) for point in face["points"]] for face in cell["faces"]])
    box=[
      [(0,0,0),(0,0,1),(0,1,1),(0,1,0)],
      [(1,0,0),(1,1,0),(1,1,1),(1,0,1)],
      [(0,0,0),(1,0,0),(1,0,1),(0,0,1)],
      [(0,1,0),(0,1,1),(1,1,1),(1,1,0)],
      [(0,0,0),(0,1,0),(1,1,0),(1,0,0)],
      [(0,0,1),(1,0,1),(1,1,1),(0,1,1)]]
    for dy in [-1,0,1]:
        for dz in [-1,0,1]:
            add_cell([[np.array(point)+np.array([-1,dy,dz]) for point in face] for face in box])
    g=assemble(reports)
    row=audit_assembled(g,epsilon)
    determinants=[]
    for c in range(g.n_cells):
        areas=[g.face(int(f))[0] for f in g.cell_faces[g.cell_offsets[c]:g.cell_offsets[c+1]] if f<len(g.neighbour)]
        avg=sum(np.linalg.norm(a) for a in areas)/len(areas)
        tensor=sum(np.outer(a/avg,a/avg) for a in areas)
        determinants.append(float(abs(np.linalg.det(tensor))/8))
    row["internalAreaTensorDeterminants"]=determinants
    row["minimumInternalAreaTensorDeterminant"]=min(determinants)
    row["scope"]="54-cell buffered analytic diagnostic. Central five-cell coordinates unchanged; repeated exact roof packet enables three internal/coupled normal directions. Not a native user-case acceptance."
    return g,row

def main():
    p=argparse.ArgumentParser();p.add_argument("--air-tool",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--fixture",type=Path);p.add_argument("--buffer-fixture",type=Path)
    args=p.parse_args()
    audit=Path(__file__).with_name("audit_air_mesh_geometry.py");air=load_air(args.air_tool)
    eps=[-5e-4,0,1e-8,1e-7,1e-6,1.001e-6,2e-6,1e-5,5e-4,.01,.1,.49,.5,.51]
    summaries=[]
    for value in eps:
        g,row=evaluate(air,audit,value)
        summaries.append(row)
        if value==5e-4 and args.fixture:write_fixture(g,args.fixture)
    buffer_g,buffer_row=evaluate_buffer(air,audit,5e-4)
    if args.buffer_fixture:write_fixture(buffer_g,args.buffer_fixture)
    out={"format":"air-transition-roof-independent/v2","scope":"Exact original air analytic coordinates; independent five-cell global topology and full face incidence audit. Not a native product mesh pass.",
         "sourceSha256":{str(f):sha(f) for f in [Path(__file__),args.air_tool,audit]},
         "necessaryEpsilonBounds":"For h=1 and this exact normalized cubic packet: epsilon > 1e-6/sqrt(1-2e-12) for adjacent roof-centre criterion and epsilon < .5 to keep the interface centre behind the fine outer face. Necessary bounds are not a full-quality guarantee.",
         "roofFormula":"x=1+epsilon*(2*y*(1-y)+2*z*(1-z)); corners0, edge mids epsilon/2, centre epsilon.",
         "refuseUntilProved":["Any packet other than one axis-aligned cube h and exactly four uncut cubes h/2 spanning a regular 2x2 interface.",
            "Any additional vertex, clipped/nonconvex polygon, irregular split, different level ratio or multiple pressure/zone/baffle/coupled interpretations.",
            "Any moved point whose entire incident cell/face star is outside the five-cell fixture and has not been checked for shared equality, planarity and strict quality.",
            "Any protected physical/zone/coupled geometry or distinct physical patch outline that moves, or any processor/cyclic counterpart without exact shared transformed coordinates.",
            "Any overlapping transition proposal with different normals/amplitudes on a common point or cell without one globally consistent displacement and full revalidation.",
            "Any nonfinite epsilon or epsilon outside the necessary normalized bounds; passing those bounds is still insufficient for native acceptance."],
         "epsilonSweep":summaries,"bufferedDiagnostic":buffer_row}
    args.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__=="__main__":main()
