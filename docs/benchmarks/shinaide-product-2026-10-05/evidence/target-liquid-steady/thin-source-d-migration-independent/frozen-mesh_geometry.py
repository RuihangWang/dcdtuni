#!/usr/bin/env python3
"""Read-only extended checkMesh geometry diagnosis on frozen MPI meshes.

Uses OpenFOAM's area-weighted polygon centre and signed pyramid cell centre.
The halfspace tolerance is an audit classification tolerance in metres, never
a replacement for the checkMesh gate. A convex-polyhedron classification also
requires planar convex face polygons, closure and positive volume.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from backend.aircooling.runner.polymesh_io import (
    read_all_faces, read_points, read_label_list, read_owner_note,
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def cell_set(path):
    if not path.exists():return np.empty(0,dtype=np.int64)
    # v2412 labelHashSet writes ASCII data even under a binary FoamFile header.
    raw=path.read_bytes()
    hit=re.search(rb"\n(\d+)\s*\(\s*([0-9\s]*)\)",raw)
    if not hit:raise ValueError(f"Unsupported diagnostic set payload: {path}")
    ids=np.fromstring(hit[2].decode(),sep=" ",dtype=np.int64)
    if len(ids)!=int(hit[1]):raise ValueError(f"Set size mismatch: {path}")
    return ids

class Geometry:
    def __init__(self,poly):
        self.poly_dir=poly
        self.points=read_points(poly/"points")
        self.offsets,self.vertices=read_all_faces(poly/"faces")
        self.owner=read_label_list(poly/"owner").astype(np.int64)
        self.neighbour=read_label_list(poly/"neighbour").astype(np.int64)
        note=read_owner_note(poly/"owner")
        self.n_cells=note.get("nCells") or int(max(self.owner.max(),self.neighbour.max())+1)
        self.n_faces=len(self.owner)
        labels=np.concatenate((self.owner,self.neighbour))
        face_ids=np.concatenate((np.arange(self.n_faces),np.arange(len(self.neighbour))))
        order=np.argsort(labels,kind="stable")
        self.cell_faces=face_ids[order]
        self.cell_offsets=np.zeros(self.n_cells+1,dtype=np.int64)
        np.cumsum(np.bincount(labels,minlength=self.n_cells),out=self.cell_offsets[1:])
        self.faces={}
        self.cells={}
        self.patches=[]
        text=(poly/"boundary").read_text()
        for name,body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}",text):
            start=re.search(r"startFace\s+(\d+)",body)
            count=re.search(r"nFaces\s+(\d+)",body)
            kind=re.search(r"type\s+(\w+)",body)
            if start and count and kind:
                self.patches.append((int(start[1]),int(count[1]),name,kind[1]))

    @classmethod
    def from_arrays(cls,points,faces):
        mesh=cls.__new__(cls)
        mesh.points=np.asarray(points,dtype=float)
        mesh.offsets=np.r_[0,np.cumsum([len(f) for f in faces])]
        mesh.vertices=np.asarray([v for f in faces for v in f],dtype=np.int64)
        mesh.n_cells=1;mesh.n_faces=len(faces)
        mesh.owner=np.zeros(len(faces),dtype=np.int64)
        mesh.neighbour=np.empty(0,dtype=np.int64)
        mesh.cell_offsets=np.array([0,len(faces)])
        mesh.cell_faces=np.arange(len(faces))
        mesh.faces={};mesh.cells={};mesh.patches=[]
        return mesh

    def ids(self,face):
        return self.vertices[self.offsets[face]:self.offsets[face+1]]

    def patch(self,face):
        for start,count,name,kind in self.patches:
            if start<=face<start+count:return {"name":name,"type":kind}
        return None

    def face(self,face):
        if face not in self.faces:
            points=self.points[self.ids(face)]
            mean=np.zeros(3)
            for point in points:mean+=point
            mean/=len(points)
            if len(points)==3:
                area=np.cross(points[1]-points[0],points[2]-points[0])*.5
                centre=mean
            else:
                area=np.zeros(3);accum=np.zeros(3);total=0.
                for i,point in enumerate(points):
                    nxt=points[(i+1)%len(points)]
                    tri=np.cross(nxt-point,mean-point)
                    amount=float(np.linalg.norm(tri))
                    area+=tri;total+=amount
                    accum+=amount*(point+nxt+mean)
                area*=.5
                centre=(1./3.)*accum/total if total else mean
            norm=float(np.linalg.norm(area))
            normal=area/norm if norm else np.zeros(3)
            plane=float(np.max(np.abs((points-centre)@normal)))
            turns=[float(np.dot(np.cross(points[(i+1)%len(points)]-point,
                points[(i+2)%len(points)]-points[(i+1)%len(points)]),normal))
                for i,point in enumerate(points)]
            edges=np.roll(points,-1,axis=0)-points
            lengths=np.linalg.norm(edges,axis=1)
            edge_max=float(lengths.max())
            # Every point must lie inside every directed edge support line.
            # Local turn signs alone can miss a self-intersecting polygon.
            support=np.einsum("evj,j->ev",np.cross(edges[:,None,:],points[None,:,:]-points[:,None,:]),normal)
            edge_support_min=float(np.min(support/np.maximum(lengths[:,None],1e-300)))
            self.faces[face]=(area,centre,plane,min(turns),edge_max,edge_support_min)
        return self.faces[face]

    def cell(self,cell):
        if cell not in self.cells:
            fs=self.cell_faces[self.cell_offsets[cell]:self.cell_offsets[cell+1]]
            areas=[];centres=[]
            for f in fs:
                area,fc,*_=self.face(int(f))
                areas.append(area if self.owner[f]==cell else -area)
                centres.append(fc)
            areas=np.asarray(areas);centres=np.asarray(centres)
            estimate=np.mean(centres,axis=0)
            pyramid3=np.einsum("ij,ij->i",areas,centres-estimate)
            volume=float(pyramid3.sum()/3)
            centre=((pyramid3[:,None]*(.75*centres+.25*estimate)).sum(axis=0)/pyramid3.sum()
                    if pyramid3.sum() else estimate)
            vertex_ids=np.unique(np.concatenate([self.ids(int(f)) for f in fs]))
            vertices=self.points[vertex_ids]
            lo=vertices.min(axis=0);hi=vertices.max(axis=0)
            self.cells[cell]=(fs,areas,centres,centre,volume,vertex_ids,lo,hi)
        return self.cells[cell]

    def classify(self,cell,detail=False):
        fs,areas,centres,centre,volume,vertex_ids,lo,hi=self.cell(cell)
        points=self.points[vertex_ids]
        tol=1e-10*max(1.,float((hi-lo).max()))
        mags=np.linalg.norm(areas,axis=1)
        normals=areas/np.maximum(mags[:,None],1e-300)
        halfspaces=np.einsum("vfi,fi->vf",points[:,None,:]-centres[None,:,:],normals)
        max_out=float(halfspaces.max())
        planar=all(self.face(int(f))[2]<=tol for f in fs)
        polygons=all(self.face(int(f))[5]>=-tol for f in fs)
        closure=float(np.linalg.norm(areas.sum(axis=0))/max(mags.sum(),1e-300))
        min_pyramid=float(np.min(np.einsum("ij,ij->i",areas,centres-centre)/3))
        triggers=[]
        coplanar=0;positive=0;near=0
        for i,f in enumerate(fs):
            delta=centres-centres[i]
            length=np.linalg.norm(delta,axis=1)
            dots=delta@normals[i]/np.maximum(length,1e-300)
            for j in np.flatnonzero(dots>-1e-6):
                if j==i:continue
                same_plane=(abs(float(np.dot(delta[j],normals[i])))<=tol
                            and float(np.dot(normals[j],normals[i]))>1-1e-10)
                if same_plane:coplanar+=1
                elif dots[j]>1e-6:positive+=1
                else:near+=1
                if detail:triggers.append({"face":int(f),"otherFace":int(fs[j]),"unitDot":float(dots[j]),"coplanarSameOutwardNormal":same_plane})
        convex=planar and polygons and max_out<=tol and closure<1e-10 and volume>0
        eligible=planar and polygons and closure<1e-10 and volume>0 and min_pyramid>1e-12*volume
        if convex and coplanar:classification="convex-with-coplanar-facets"
        elif convex and (positive or near):classification="convex-near-planar-angle-margin"
        elif convex:classification="convex-no-face-plane-trigger"
        elif not planar:classification="nonplanar-face-unproved"
        elif max_out>tol:classification="vertex-halfspace-violation"
        elif not polygons:classification="nonconvex-face-polygon"
        else:classification="degenerate-or-unproved"
        result={"cell":int(cell),"classification":classification,"allVertexMaximumOutsideMetres":max_out,
                "auditToleranceMetres":tol,"allFacePolygonsPlanar":planar,"allFacePolygonsConvex":polygons,
                "relativeClosure":closure,"volumeM3":volume,"minimumCentroidPyramidVolumeM3":min_pyramid,
                "pyramidCandidateEligible":eligible,"faceCount":len(fs),"vertexCount":len(points),
                "checkerTriggerCounts":{"coplanar":coplanar,"positiveOutside":positive,"nearPlanarMargin":near},
                "bboxMin":lo.tolist(),"bboxMax":hi.tolist(),"centre":centre.tolist()}
        if detail:
            edge_counts=Counter()
            for f in fs:
                ids=self.ids(int(f))
                edge_counts.update(tuple(sorted((int(ids[i]),int(ids[(i+1)%len(ids)])))) for i in range(len(ids)))
            result["boundaryEdgeMultiplicityCounts"]=dict(Counter(edge_counts.values()))
            result["boundaryEdgesPairExactly"]=all(n==2 for n in edge_counts.values())
            result["vertices"]=points.tolist();result["checkerTriggers"]=triggers
            result["faces"]=[{"face":int(f),"vertexIds":self.ids(int(f)).tolist(),"points":self.points[self.ids(int(f))].tolist(),
                             "patch":self.patch(int(f)),"areaM2":float(mags[i]),"centre":centres[i].tolist(),
                             "outwardAreaVector":areas[i].tolist(),
                             "neighbourCell":int(self.neighbour[f]) if f<len(self.neighbour) and self.owner[f]==cell else int(self.owner[f]) if self.owner[f]!=cell else None,
                             "planarityErrorM":self.face(int(f))[2],"minimumPolygonTurnM2":self.face(int(f))[3],"minimumPolygonEdgeHalfspaceM":self.face(int(f))[5]}
                            for i,f in enumerate(fs)]
            for f in fs:
                base=set(int(x) for x in self.ids(int(f)))
                apex=set(int(x) for x in vertex_ids)-base
                if len(base)>=4 and len(apex)==1 and all(
                    next(iter(apex)) in self.ids(int(g)) for g in fs if g!=f
                ):
                    result["pyramidShape"]={"baseFace":int(f),"baseVertices":len(base),"apex":self.points[next(iter(apex))].tolist()}
                    break
        return result

    def face_detail(self,face):
        owner=int(self.owner[face]);nei=int(self.neighbour[face]) if face<len(self.neighbour) else None
        a,fc,plane,turn,edge,support=self.face(face)
        item={"face":int(face),"patch":self.patch(face),"vertexCount":len(self.ids(face)),
              "points":self.points[self.ids(face)].tolist(),"areaM2":float(np.linalg.norm(a)),
              "centre":fc.tolist(),"planarityErrorM":plane,"minimumPolygonTurnM2":turn,"minimumPolygonEdgeHalfspaceM":support,
              "owner":self.classify(owner,True),"neighbour":self.classify(nei,True) if nei is not None else None,
              "nativeCellLevel":None,"mergeCoalesceProvenance":None,
              "provenanceLimit":"Native ancestry/levels are not written in this mesh; polygon vertex count alone does not prove coalescence."}
        if nei is not None:
            co=self.cell(owner)[3];cn=self.cell(nei)[3]
            do=abs(float(np.dot(a,fc-co)));dn=abs(float(np.dot(a,cn-fc)))
            vo=self.cell(owner)[4];vn=self.cell(nei)[4]
            item["internalNormalDistanceWeightEstimate"]=min(do,dn)/max(do+dn,1e-300)
            item["internalVolumeRatioEstimate"]=min(vo,vn)/max(vo,vn)
        else:item["neighbourLimit"]="Coupled remote centroid/volume is not inferred from this local partition."
        return item

def cone_probe(mesh,cell):
    """Probe geometric child quality only; no mesh mutation or conformity claim."""
    fs,areas,centres,centre,volume,*_=mesh.cell(cell)
    children=[]
    for i,f in enumerate(fs):
        base=mesh.points[mesh.ids(int(f))].copy()
        if np.dot(mesh.face(int(f))[0],areas[i])<0:base=base[::-1]
        n=len(base)
        faces=[tuple(range(n))]+[((j+1)%n,j,n) for j in range(n)]
        child=Geometry.from_arrays(np.vstack((base,centre)),faces)
        row=child.classify(0)
        children.append({"originalBaseFace":int(f),"baseVertices":n,
                         "classification":row["classification"],"volumeM3":row["volumeM3"],
                         "checkerTriggerCounts":row["checkerTriggerCounts"],
                         "allVertexMaximumOutsideMetres":row["allVertexMaximumOutsideMetres"]})
    return {"parentCell":int(cell),"parentVolumeM3":volume,
            "childVolumeSumM3":sum(x["volumeM3"] for x in children),
            "childrenFailingCheckerPlaneCriterion":sum(sum(x["checkerTriggerCounts"].values())>0 for x in children),
            "children":children,
            "scope":"One preserved-base pyramid per face with one triangular side per edge. Geometry-only probe; no conformity or complete checkMesh acceptance."}

def rank_audit(task):
    mesh,rank,detail_limit=task
    os.sched_setaffinity(0,{24+rank})
    poly=Path(mesh)/f"processor{rank}/constant/polyMesh"
    geom=Geometry(poly)
    sets={name:cell_set(poly/"sets"/name) for name in
          ["concaveCells","lowQualityTetFaces","lowWeightFaces","lowVolRatioFaces","concaveFaces","warpedFaces"]}
    counts=Counter();triggers=Counter();eligible=0;samples={}
    for i,cell in enumerate(sets["concaveCells"]):
        row=geom.classify(int(cell))
        counts[row["classification"]]+=1
        triggers.update(row["checkerTriggerCounts"])
        eligible+=int(row["pyramidCandidateEligible"])
        key=row["classification"]
        if key not in samples:samples[key]=geom.classify(int(cell),True)
        if i%2000==0:print(f"rank{rank}: {i}/{len(sets['concaveCells'])} concave-set cells",file=sys.stderr,flush=True)
    details={}
    for name in ["lowQualityTetFaces","lowWeightFaces","lowVolRatioFaces"]:
        details[name]=[geom.face_detail(int(f)) for f in sets[name][:detail_limit]]
    return {"rank":rank,"cells":geom.n_cells,"faces":geom.n_faces,
            "setCounts":{k:len(v) for k,v in sets.items()},
            "concaveSetClassification":dict(counts),"checkerTriggerCounts":dict(triggers),
            "eligibleCentroidPyramidCandidates":eligible,"classificationExamples":samples,
            "exampleConeProbes":{key:cone_probe(geom,row["cell"]) for key,row in samples.items()},
            "failureFaceDetails":details,
            "inputSha256":{x.name:sha(x) for x in poly.iterdir() if x.is_file()},
            "setSha256":{name:sha(poly/"sets"/name) for name in sets if (poly/"sets"/name).exists()}}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mesh",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--workers",type=int,default=8)
    parser.add_argument("--detail-limit",type=int,default=1000)
    args=parser.parse_args()
    mesh=args.mesh.resolve()
    ranks=sorted(int(x.name[9:]) for x in mesh.glob("processor[0-9]*") if x.name[9:].isdigit())
    if ranks!=list(range(len(ranks))) or not ranks:raise ValueError("Requires contiguous MPI mesh ranks")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows=list(pool.map(rank_audit,[(str(mesh),rank,args.detail_limit) for rank in ranks]))
    total=Counter();sets=Counter();eligible=0
    for row in rows:
        total.update(row["concaveSetClassification"]);sets.update(row["setCounts"])
        eligible+=row["eligibleCentroidPyramidCandidates"]
    result={"format":"air-mesh-geometry-readonly/v1","mesh":str(mesh),
            "scope":"Classifies saved checker sets; no mesh or solver mutation. Audit tolerance does not change strict acceptance.",
            "checkerCriterion":"primitiveMeshCheck.C: face-to-other-face-centre unit dot > -1e-6 includes coplanar subdivisions.",
            "coupledCountLimit":"Local face-set totals may differ from globally reported checker counts; duplicate coupled faces are not silently deduplicated.",
            "classificationTotals":dict(total),"localSetTotals":dict(sets),"eligibleCentroidPyramidCandidates":eligible,"ranks":rows}
    args.output.write_text(json.dumps(result,indent=2)+"\n")

if __name__=="__main__":main()
