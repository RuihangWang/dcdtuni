#!/usr/bin/env python3
"""Freeze one complete actual stencil and its bounded, nonrecursive cut frontier."""
from pathlib import Path
import argparse,hashlib,json,sys,numpy as np
ROOT=Path(__file__).resolve()
while ROOT.parent!=ROOT and not (ROOT/".git").exists():ROOT=ROOT.parent
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_thin_source_migration as M
import audit_air_axis_prism_extrusion as P

def identity(path):
    return dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def extract(mesh,reader,rank,parent,stencil_out,frontier_out):
    mesh=P.repo_path(mesh);reader=P.repo_path(reader)
    for out in (stencil_out,frontier_out):
        if P.repo_path(out).exists():raise ValueError("preserve existing extraction; choose fresh repo outputs")
    t=M.source_index(M.load_trace_reader(reader)(mesh,rank))
    g=M.G.Geometry(mesh/f"processor{rank}/constant/polyMesh")
    roles=M.realized_patch_roles(g,t)
    def source_record(p):
        fs,polys,geo,raw,closed=M.geometry_parent(t,p)
        if not closed or raw[4]<=0:raise ValueError("positive directed closed original source required")
        ids=raw[5].tolist();index={v:i for i,v in enumerate(ids)}
        faces=[]
        for f,poly in zip(map(int,fs),polys):
            patch=int(t["patch"][f]);role=roles.get(patch)
            if patch>=0 and role is None:raise ValueError("unresolved original source physical role")
            faces.append(dict(face=f,vertices=[index[v] for v in poly],patch=patch,zone=int(t["zone"][f]),
                role=role,owner=int(t["owner"][f]),neighbour=int(t["neighbour"][f])))
        return dict(parent=p,points=t["points"][ids].tolist(),nativePointIDs=ids,faces=faces,
                    classification=M.prism(t,p),centre=raw[3].tolist(),volume=float(raw[4]))
    def written(c):
        raw=g.cell(c);faces=[]
        for f in map(int,raw[0]):
            ids=g.ids(f)
            if int(g.owner[f])!=c:ids=ids[::-1]
            other=int(g.neighbour[f]) if f<len(g.neighbour) and int(g.owner[f])==c else int(g.owner[f]) if int(g.owner[f])!=c else -1
            o=g.cell(other) if other>=0 else None
            faces.append(dict(face=f,coordinates=g.points[ids].tolist(),patch=g.patch(f),other=other,
                otherCentre=o[3].tolist() if o else None,otherVolume=float(o[4]) if o else None))
        return dict(cell=c,sourceParent=int(t["cellParent"][c]),faces=faces,centre=raw[3].tolist(),volume=float(raw[4]))
    children=list(map(int,np.flatnonzero(t["cellParent"]==parent)))
    if not children:raise ValueError("alive actual written children required")
    external=[];outset=set()
    for f in sorted({int(f) for c in children for f in g.cell(c)[0]}):
        a=int(g.owner[f]);b=int(g.neighbour[f]) if f<len(g.neighbour) else -1
        ina=a in children;inb=b in children
        if ina==inb:continue
        base=int(t["faceBase"][f])
        if base<0:raise ValueError("original exterior source-base mapping missing")
        ids=g.ids(f) if ina else g.ids(f)[::-1];other=b if ina else a
        if other>=0:outset.add(other)
        external.append(dict(face=f,coordinates=g.points[ids].tolist(),outside=other,
            sourceBase=base,sourcePatch=int(t["patch"][base]),sourceZone=int(t["zone"][base]),patch=g.patch(f)))
    paths=list(mesh.glob(f"conforming-source-rank{rank}-*"))+list(mesh.glob(f"conforming-written-rank{rank}-*"))
    paths += [g.poly_dir/n for n in ("points","faces","owner","neighbour","boundary","faceZones","cellZones")]
    ids={str(p):identity(p) for p in sorted(paths)}
    doc=dict(schema="frozen-actual-axis-prism-stencil/v1",mesh=str(mesh),rank=rank,parent=parent,
        source={k:v for k,v in source_record(parent).items() if k!="parent"},
        children=[written(c) for c in children],externalFaces=external,outside=[written(c) for c in sorted(outset)],
        scope="Actual complete written neighbor cells and source roles, no remote geometry inference or coalescing.",
        sourceFileIdentities=ids,readerIdentity=dict(path=str(reader),**identity(reader)))
    # This local analysis selects only the direct parents implicated in the
    # failed candidate. No exploration of the next frontier is performed.
    trial=P.analyze(doc);outside_bycell={o["cell"]:o for o in doc["outside"]}
    parents=sorted({parent}|{outside_bycell[c]["sourceParent"] for c in trial["outsideNewConcavityFailures"]})
    if len(parents)>5:raise ValueError("bounded frontier exceeds focal plus four parents")
    compact=[source_record(p) for p in parents]
    vertices=set(v for x in compact for v in x["nativePointIDs"])
    positions=np.flatnonzero(np.isin(t["vertices"],list(vertices)))
    starfaces=np.unique(np.searchsorted(t["offsets"],positions,side="right")-1)
    starfaces=starfaces[t["faceAlive"][starfaces]!=0]
    for x in compact:
        star=[];wanted=set(x["nativePointIDs"])
        for f in map(int,starfaces):
            v=t["vertices"][t["offsets"][f]:t["offsets"][f+1]].tolist()
            if not wanted.intersection(v):continue
            patch=int(t["patch"][f]);role=roles.get(patch)
            if patch>=0 and role is None:raise ValueError("unresolved whole vertex-star role")
            star.append(dict(face=f,nativePointIDs=v,coordinates=t["points"][v].tolist(),
                patch=patch,zone=int(t["zone"][f]),role=role,owner=int(t["owner"][f]),neighbour=int(t["neighbour"][f])))
        x["vertexStarFaces"]=star
    planes=trial["recipe"]["existingAxisPlanes"][1:-1]
    if len(planes)!=1:raise ValueError("this bounded witness requires one original interior seam")
    frontier=dict(schema="frozen-bounded-prism-frontier/v1",mesh=str(mesh),rank=rank,
        axis=trial["recipe"]["axis"],plane=planes[0],parents=compact,
        implicatedWrittenCells=trial["outsideNewConcavityFailures"],sourceFileIdentities=ids,
        readerIdentity=doc["readerIdentity"],
        scope="Full current source vertex stars, focal plus four direct source parents only; no frontier recursion or preGhost rank inference.")
    for path,obj in ((stencil_out,doc),(frontier_out,frontier)):
        path=P.repo_path(path);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(obj,indent=2,allow_nan=False)+"\n")
    print(json.dumps(dict(rank=rank,parent=parent,children=len(children),outsideCells=len(outset),
                         boundedParents=parents,rawFiles=len(ids),stencil=identity(stencil_out),frontier=identity(frontier_out)),indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mesh",required=True,type=Path);p.add_argument("--reader",required=True,type=Path)
    p.add_argument("--rank",required=True,type=int);p.add_argument("--parent",required=True,type=int)
    p.add_argument("--stencil-output",required=True,type=Path);p.add_argument("--frontier-output",required=True,type=Path)
    a=p.parse_args();extract(a.mesh,a.reader,a.rank,a.parent,a.stencil_output,a.frontier_output)
