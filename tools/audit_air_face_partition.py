#!/usr/bin/env python3
"""Read-only deterministic partitions of saved planar concave mesh faces.

No mesh file, point, patch, native executable or solver is changed.  The
prototype recomputes affected cells using the installed OpenFOAM geometry
definitions reproduced by audit_air_mesh_geometry, and explicitly records
the coplanar-facet checker consequence.
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from collections import Counter
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_air_mesh_geometry import Geometry, sha
from audit_air_transition_roof import quality

def cross2(a,b,c):
    u=b-a;v=c-a
    return float(u[0]*v[1]-u[1]*v[0])

def projected(points):
    pts=np.asarray(points,dtype=float)
    if pts.ndim!=2 or pts.shape[1]!=3 or len(pts)<3 or not np.isfinite(pts).all():
        raise ValueError("Expected finite 3D polygon with at least three points")
    if len({tuple(p) for p in pts})!=len(pts):
        raise ValueError("Coincident/repeated polygon point")
    local=pts-pts[0]
    area=sum((np.cross(p,q) for p,q in zip(local,np.roll(local,-1,axis=0))),np.zeros(3))*.5
    length=float(np.linalg.norm(area));span=float(np.ptp(pts,axis=0).max())
    if span==0 or length<=1e-14*span**2:
        raise ValueError("Degenerate area")
    normal=area/length
    if np.max(np.abs((pts-pts[0])@normal))>1e-12*span:
        raise ValueError("Nonplanar polygon")
    drop=int(np.argmax(np.abs(normal)))
    xy=np.delete(pts,drop,axis=1)
    # Translation makes all area comparisons local, avoiding world-offset loss.
    xy=xy-xy[0]
    signed=sum(float(a[0]*b[1]-a[1]*b[0]) for a,b in zip(xy,np.roll(xy,-1,axis=0)))
    return pts,xy,1 if signed>0 else -1,1e-14*span**2

def on_segment(a,b,p,tol):
    return abs(cross2(a,b,p))<=tol and float((p-a)@(p-b))<=tol

def intersects(a,b,c,d,tol):
    ab_c=cross2(a,b,c);ab_d=cross2(a,b,d)
    cd_a=cross2(c,d,a);cd_b=cross2(c,d,b)
    if ((ab_c>tol and ab_d < -tol) or (ab_c < -tol and ab_d>tol)) and (
        (cd_a>tol and cd_b < -tol) or (cd_a < -tol and cd_b>tol)):
        return True
    return any((on_segment(a,b,p,tol) for p in (c,d))) or any(
        (on_segment(c,d,p,tol) for p in (a,b)))

def require_simple(xy,tol):
    n=len(xy)
    for i in range(n):
        for j in range(i+1,n):
            if j==i+1 or (i==0 and j==n-1):
                continue
            if intersects(xy[i],xy[(i+1)%n],xy[j],xy[(j+1)%n],tol):
                raise ValueError("Self-crossing/touching/non-simple polygon")

def canonical(polygon,points):
    row=[tuple(points[i]) for i in polygon]
    variants=[tuple(row[k:]+row[:k]) for k in range(len(row))]
    rev=row[::-1]
    variants += [tuple(rev[k:]+rev[:k]) for k in range(len(rev))]
    return min(variants)

def ear_partition(points):
    pts,xy,sign,tol=projected(points)
    require_simple(xy,tol)
    alive=list(range(len(pts)));pieces=[]
    while len(alive)>3:
        candidates=[]
        for k,b in enumerate(alive):
            a=alive[k-1];c=alive[(k+1)%len(alive)]
            if sign*cross2(xy[a],xy[b],xy[c])<=tol:
                continue
            # Including the boundary forbids skipping another retained vertex.
            if any(all(sign*cross2(xy[u],xy[v],xy[p])>=-tol
                       for u,v in [(a,b),(b,c),(c,a)])
                   for p in alive if p not in (a,b,c)):
                continue
            candidates.append((tuple(pts[b]),k,(a,b,c)))
        if not candidates:
            raise ValueError("No nondegenerate deterministic ear")
        _,k,triangle=min(candidates)
        pieces.append(triangle);del alive[k]
    if sign*cross2(xy[alive[0]],xy[alive[1]],xy[alive[2]])<=tol:
        raise ValueError("Degenerate final ear")
    pieces.append(tuple(alive))
    validate_partition(points,pieces)
    return sorted(pieces,key=lambda p:canonical(p,pts))

def fan_quality(points,own,nei=None):
    pts=np.asarray(points,float)
    g=Geometry.from_arrays(pts,[list(range(len(pts)))])
    area,fc,*_=g.face(0)
    ownq=min(-quality(a,b,fc,own) for a,b in zip(pts,np.roll(pts,-1,axis=0)))
    neiq=min(quality(a,b,fc,nei) for a,b in zip(pts,np.roll(pts,-1,axis=0))) if nei is not None else None
    candidates=[]
    for k,p in enumerate(pts):
        qs=[]
        for i in range(1,len(pts)-1):
            a=pts[(k+i)%len(pts)];b=pts[(k+i+1)%len(pts)]
            qs.append(quality(own,p,a,b))
            if nei is not None:qs.append(quality(nei,p,b,a))
        candidates.append(min(qs))
    out={"ownerCentroidFanMinimumQuality":ownq,
         "neighbourCentroidFanMinimumQuality":neiq,
         "sharedOriginalVertexFanBestMinimumQuality":max(candidates),
         "ownerPyramidVolumeM3":float(area@(fc-own))/3,
         "neighbourPyramidVolumeM3":float(area@(nei-fc))/3 if nei is not None else None}
    out["minimumQuality"]=min(ownq,neiq if neiq is not None else math.inf,max(candidates))
    return out

def convex(points):
    _,xy,sign,tol=projected(points)
    require_simple(xy,tol)
    return all(sign*cross2(a,b,p)>=-tol for a,b in zip(xy,np.roll(xy,-1,axis=0)) for p in xy)

def joined(a,b):
    edges=Counter(zip(a,a[1:]+a[:1]))+Counter(zip(b,b[1:]+b[:1]))
    removed=[]
    for u,v in list(edges):
        if edges[(v,u)]:
            removed.append((u,v))
    if len(removed)!=2: # conservative prototype: one shared diagonal only
        return None
    for e in removed:del edges[e]
    successors={}
    for (u,v),count in edges.items():
        if count!=1 or u in successors:return None
        successors[u]=v
    if len(set(successors.values()))!=len(successors):return None
    start=min(successors);cycle=[start]
    while successors[cycle[-1]]!=start:
        nxt=successors[cycle[-1]]
        if nxt in cycle:return None
        cycle.append(nxt)
    return tuple(cycle) if len(cycle)==len(successors) else None

def convex_partition(points,own,nei=None):
    pts=np.asarray(points,float);pieces=ear_partition(pts)
    while True:
        candidates=[]
        for i,a in enumerate(pieces):
            for j in range(i+1,len(pieces)):
                union=joined(a,pieces[j])
                if union is None:continue
                coords=pts[list(union)]
                if convex(coords) and fan_quality(coords,own,nei)["minimumQuality"]>1e-15:
                    candidates.append((canonical(union,pts),i,j,union))
        if not candidates:break
        _,i,j,union=min(candidates)
        pieces=[p for k,p in enumerate(pieces) if k not in (i,j)]+[union]
    pieces=sorted(pieces,key=lambda p:canonical(p,pts))
    validate_partition(pts,pieces)
    return pieces

def validate_partition(points,pieces):
    pts,_,_,_=projected(points)
    edge=Counter((int(a),int(b)) for p in pieces for a,b in zip(p,p[1:]+p[:1]))
    outer=Counter(zip(range(len(pts)),list(range(1,len(pts)))+[0]))
    # Exactly one oriented original cycle, all diagonals paired oppositely.
    for e,n in edge.items():
        if n!=1 or n-edge[(e[1],e[0])]!=outer[e]-outer[(e[1],e[0])]:
            raise ValueError("Partition edge cycle does not preserve original outline")
    if any(edge[e]!=1 for e in outer):
        raise ValueError("Original outline edge missing")
    original=Geometry.from_arrays(pts,[list(range(len(pts)))]).face(0)[0]
    areas=[Geometry.from_arrays(pts,[p]).face(0)[0] for p in pieces]
    error=float(np.linalg.norm(sum(areas,np.zeros(3))-original)/np.linalg.norm(original))
    if error>1e-12:raise ValueError("Partition area vector changed")
    return {"allOriginalBoundaryEdgesPreserved":True,
            "allNewDiagonalEdgesPairedOppositely":True,
            "allOriginalVerticesReferenced":set(range(len(pts)))=={i for p in pieces for i in p},
            "relativeAreaVectorError":error}

def rebuilt_cell(report,partitions):
    coords={i:tuple(p) for f in report["faces"] for i,p in zip(f["vertexIds"],f["points"])}
    labels=sorted(coords);mapping={v:i for i,v in enumerate(labels)}
    faces=[];sources=[]
    for f in report["faces"]:
        ids=f["vertexIds"]
        raw=np.asarray(f["points"])
        area=Geometry.from_arrays(raw,[list(range(len(raw)))]).face(0)[0]
        reverse=float(area@np.asarray(f["outwardAreaVector"]))<0
        polygons=partitions.get(f["face"],[ids])
        for p in polygons:
            oriented=list(p)[::-1] if reverse else list(p)
            faces.append([mapping[i] for i in oriented]);sources.append(f["face"])
    mesh=Geometry.from_arrays([coords[i] for i in labels],faces)
    detail=mesh.classify(0)
    edges=Counter((int(a),int(b)) for p in faces for a,b in zip(p,p[1:]+p[:1]))
    detail["allDirectedCellEdgesPairedOppositely"]=all(n==1 and edges[(b,a)]==1 for (a,b),n in edges.items())
    detail["originalCell"]=report["cell"]
    detail["faceSourceIds"]=sources
    return mesh,detail

def mode_audit(rank,mode):
    entries=rank["faces"];parts={};pieces_by_face={};reports={}
    for f in entries:
        owner=f["owner"];nei=f["neighbour"]
        own=np.asarray(owner["centre"]);nc=np.asarray(nei["centre"]) if nei else None
        pts=np.asarray(f["points"])
        pieces=ear_partition(pts) if mode=="triangles" else convex_partition(pts,own,nc)
        if nei is not None:
            reverse=ear_partition(pts[::-1]) if mode=="triangles" else convex_partition(pts[::-1],nc,own)
        # For a boundary reversed polygon use an apex reflected through its plane.
        else:
            area=Geometry.from_arrays(pts,[list(range(len(pts)))]).face(0)[0]
            normal=area/np.linalg.norm(area);reflected=own-2*float((own-pts[0])@normal)*normal
            reverse=ear_partition(pts[::-1]) if mode=="triangles" else convex_partition(pts[::-1],reflected)
        keys=[canonical(p,pts) for p in pieces]
        reverse_keys=[canonical(p,pts[::-1]) for p in reverse]
        original_ids=owner["faces"][[x["face"] for x in owner["faces"]].index(f["face"])]["vertexIds"]
        parts[f["face"]]=[tuple(original_ids[i] for i in p) for p in pieces]
        pieces_by_face[f["face"]]={"rank":rank["rank"],"face":f["face"],"patch":f["patch"],
             "originalVertexCount":len(pts),"pieceCount":len(pieces),"piecePointIndices":[list(p) for p in pieces],
             "pieceVertexIds":[list(p) for p in parts[f["face"]]],
             "reversalSameGeometricPieceSets":sorted(keys)==sorted(reverse_keys),
             "outlineValidation":validate_partition(pts,pieces),
             "allPiecesConvex":all(convex(pts[list(p)]) for p in pieces),
             "originalCellCentreFans":[fan_quality(pts[list(p)],own,nc) for p in pieces]}
        reports[owner["cell"]]=owner
        if nei:reports[nei["cell"]]=nei
    cells={};rows=[]
    for c,report in sorted(reports.items()):
        mesh,row=rebuilt_cell(report,parts);old=report
        cells[c]=np.asarray(row["centre"])
        row["originalGeometry"]={k:old[k] for k in ["volumeM3","centre","checkerTriggerCounts","minimumCentroidPyramidVolumeM3","classification"]}
        row["relativeVolumeDifference"]=abs(row["volumeM3"]-old["volumeM3"])/abs(old["volumeM3"])
        row["cellCentreShiftM"]=float(np.linalg.norm(cells[c]-np.asarray(old["centre"])))
        # Also inspect ALL cell faces after recomputation, not only new children.
        row["allBoundaryOwnerFanMinimumQuality"]=min(fan_quality(mesh.points[mesh.ids(f)],cells[c])["minimumQuality"] for f in range(mesh.n_faces))
        rows.append(row)
    for f in entries:
        row=pieces_by_face[f["face"]];pts=np.asarray(f["points"])
        own=cells[f["owner"]["cell"]];nei=cells[f["neighbour"]["cell"]] if f["neighbour"] else None
        row["recomputedCellCentreFans"]=[fan_quality(pts[list(p)],own,nei) for p in row["piecePointIndices"]]
        original=Geometry.from_arrays(pts,[list(range(len(pts)))]).face(0)
        areas=[];moment=np.zeros(3);total=0.
        for p in row["piecePointIndices"]:
            a,fc,*_=Geometry.from_arrays(pts,[p]).face(0)
            size=float(np.linalg.norm(a));areas.append(a);total+=size;moment+=size*fc
        row["partitionAreaWeightedCentreShiftM"]=float(np.linalg.norm(moment/total-original[1]))
    return {"rank":rank["rank"],"mode":mode,"faces":list(pieces_by_face.values()),"affectedCells":rows}

def summarise(rows):
    faces=[f for r in rows for f in r["faces"]];cells=[c for r in rows for c in r["affectedCells"]]
    fans=[x for f in faces for x in f["recomputedCellCentreFans"]]
    oldfans=[x for f in faces for x in f["originalCellCentreFans"]]
    return {"originalFaces":len(faces),"partitionFaces":sum(f["pieceCount"] for f in faces),
      "affectedCells":len(cells),"allOutlinesAndVerticesPreserved":all(all(f["outlineValidation"][k] for k in ["allOriginalBoundaryEdgesPreserved","allNewDiagonalEdgesPairedOppositely","allOriginalVerticesReferenced"]) for f in faces),
      "allReversalInvariant":all(f["reversalSameGeometricPieceSets"] for f in faces),
      "allPiecesConvex":all(f["allPiecesConvex"] for f in faces),
      "allAffectedCellsDirectedEdgesClosed":all(c["allDirectedCellEdgesPairedOppositely"] for c in cells),
      "minimumTetQualityUsingOriginalCentres":min(f["minimumQuality"] for f in oldfans),
      "minimumTetQualityUsingRecomputedCentres":min(f["minimumQuality"] for f in fans),
      "failedChildFanEventsAt1e-15":{"owner":sum(f["ownerCentroidFanMinimumQuality"]<1e-15 for f in fans),"neighbour":sum(f["neighbourCentroidFanMinimumQuality"] is not None and f["neighbourCentroidFanMinimumQuality"]<1e-15 for f in fans),"originalVertex":sum(f["sharedOriginalVertexFanBestMinimumQuality"]<=1e-15 for f in fans)},
      "minimumChildOwnerPyramidM3":min(f["ownerPyramidVolumeM3"] for f in fans),
      "minimumChildNeighbourPyramidM3":min((f["neighbourPyramidVolumeM3"] for f in fans if f["neighbourPyramidVolumeM3"] is not None),default=None),
      "maximumRelativeAffectedCellVolumeChange":max(c["relativeVolumeDifference"] for c in cells),
      "maximumAffectedCellCentreShiftM":max(c["cellCentreShiftM"] for c in cells),
      "maximumPartitionAreaWeightedCentreShiftM":max(f["partitionAreaWeightedCentreShiftM"] for f in faces),
      "maximumRelativeAreaVectorError":max(f["outlineValidation"]["relativeAreaVectorError"] for f in faces),
      "affectedCellsWithAnyStrictFaceCentreTriggerBefore":sum(sum(c["originalGeometry"]["checkerTriggerCounts"].values())>0 for c in cells),
      "affectedCellsWithAnyStrictFaceCentreTriggerAfter":sum(sum(c["checkerTriggerCounts"].values())>0 for c in cells),
      "affectedCellCheckerTriggerPairsBefore":dict(sum((Counter(c["originalGeometry"]["checkerTriggerCounts"]) for c in cells),Counter())),
      "affectedCellCheckerTriggerPairsAfter":dict(sum((Counter(c["checkerTriggerCounts"]) for c in cells),Counter())),
      "affectedCellClassificationAfter":dict(Counter(c["classification"] for c in cells)),
      "minimumAllAffectedCellFaceFanQuality":min(c["allBoundaryOwnerFanMinimumQuality"] for c in cells),
      "minimumAllAffectedCellPyramidVolumeM3":min(c["minimumCentroidPyramidVolumeM3"] for c in cells)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--comparison",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    source=json.loads(a.comparison.read_text())
    modes={}
    for mode in ["triangles","convex-greedy"]:
        rows=[mode_audit(r,mode) for r in source["ranks"]]
        modes[mode]={"summary":summarise(rows),"ranks":rows}
    out={"format":"air-concave-face-partition-readonly/v1",
       "scope":"Pure in-memory prototype on all 21 saved remaining bad faces; not native mesh/checkMesh or product acceptance.",
       "inputSha256":sha(a.comparison),
       "sourceSha256":{str(x):sha(x) for x in [Path(__file__),Path(__file__).with_name("audit_air_mesh_geometry.py"),Path(__file__).with_name("audit_air_transition_roof.py")]},
       "method":"Planar simple polygon, strictly positive ears ordered by geometric tip coordinates; original points/cycle retained. Greedy merging removes a single internal diagonal only if every vertex lies in every edge half-plane and old-centre original-point/centre tet fans stay positive. Reversed ordering independently reproduced. Simultaneous affected-cell geometry recomputation.",
       "limitations":["Comparison of fan quality and cell geometry uses NumPy double reconstruction, not native exact-bit parity.",
          "Original point coordinates and polygon outline are unchanged, but subdivision changes OpenFOAM's abs-weighted concave face centroid and thus affected cell centres.",
          "Coplanar sibling faces necessarily fail installed strict cellConcavity criterion; fixing isolated tet fans is insufficient for full geometry acceptance.",
          "No coupled flagged face was partitioned; no distributed coupled-polygon algorithm is claimed.",
          "Only affected-cell faces rechecked; full-mesh determinant/weights/skewness were not evaluated."],
       "modes":modes}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__=="__main__":main()
