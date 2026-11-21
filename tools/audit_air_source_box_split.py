#!/usr/bin/env python3
"""Read-only source-box/seam classification of current non-tetrahedral D failures."""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
from fractions import Fraction as F
from collections import Counter
import numpy as np

def load(name):
    p=Path(__file__).with_name(name);s=importlib.util.spec_from_file_location(name.removesuffix(".py"),p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
P=load("audit_air_pyramid_remaining_bound.py");M=P.M;B=P.B

def signed_shell_moments(points,loops):
    points=[P.vec(p) for p in points];V=F(0);moment=[F(0)]*3
    for loop in loops:
        a=points[loop[0]]
        for i in range(1,len(loop)-1):
            b=points[loop[i]];c=points[loop[i+1]]
            v=P.dot(a,P.cross(b,c))/6;V+=v
            for j in range(3):moment[j]+=v*(a[j]+b[j]+c[j])/4
    return V,tuple(moment)

def source_box(points,loops):
    pts=[P.vec(p) for p in points]
    if not pts or not loops:raise ValueError("source points and oriented original faces required")
    lo=tuple(min(p[i] for p in pts) for i in range(3));hi=tuple(max(p[i] for p in pts) for i in range(3))
    span=tuple(b-a for a,b in zip(lo,hi))
    if min(span)<=0:raise ValueError("nonzero 3D source box required")
    edges=Counter();planeArea={};exact_axis=True;all_convex=True;planes=[]
    for loop in loops:
        vs=[pts[i] for i in loop]
        for u,v in zip(loop,loop[1:]+loop[:1]):edges[(u,v)]+=1
        sf=P.mul(tuple(sum((P.cross(P.sub(vs[i],vs[0]),P.sub(vs[i+1],vs[0]))[j] for i in range(1,len(vs)-1)),F(0)) for j in range(3)),F(1,2))
        nonzero=[i for i,x in enumerate(sf) if x]
        if len(nonzero)!=1:
            exact_axis=False;planes.append({"unsupported":"not exact axis normal"});continue
        a=nonzero[0];c=vs[0][a]
        if any(v[a]!=c for v in vs) or c not in (lo[a],hi[a]) or (sf[a]>0)!=(c==hi[a]):
            exact_axis=False
        for k in range(len(vs)):
            e=P.sub(vs[(k+1)%len(vs)],vs[k])
            if any(P.dot(P.cross(e,P.sub(v,vs[k])),sf)<0 for v in vs):all_convex=False
        planeArea[(a,c)]=planeArea.get((a,c),F(0))+abs(sf[a])
        planes.append({"axis":a,"coordinate":str(c),"areaVector":[str(x) for x in sf]})
    closure=all(n==1 and edges[(v,u)]==1 for (u,v),n in edges.items())
    coverage=all(planeArea.get((a,c),F(0))==span[(a+1)%3]*span[(a+2)%3] for a in range(3) for c in [lo[a],hi[a]])
    V,moment=signed_shell_moments(points,loops);boxV=span[0]*span[1]*span[2]
    boxM=tuple(boxV*(lo[i]+hi[i])/2 for i in range(3))
    qualified=exact_axis and all_convex and closure and coverage and V==boxV and moment==boxM
    return {"exactAxisBoundingFacePlanes":exact_axis,"originalFacePolygonsWeaklyConvex":all_convex,
            "directedEdgesClosedExactly":closure,"sixPlaneAreaCoverageExact":coverage,
            "sourceVolumeEqualsBoxExact":V==boxV,"sourceFirstMomentEqualsBoxExact":moment==boxM,
            "exactSourceBoxQualified":qualified,"bboxLower":[str(v) for v in lo],"bboxUpper":[str(v) for v in hi],
            "spans":[float(x) for x in span],"aspectRatio":float(max(span)/min(span)),
            "sourceVolumeExactFraction":str(V),"boxVolumeExactFraction":str(boxV),"sourceFirstMomentExactFractions":[str(x) for x in moment],
            "sourceFacePlanes":planes}

def seam_plan(points,loops,box,allow_uncertified_box=False):
    if not box["exactSourceBoxQualified"] and not allow_uncertified_box:return {"unsupported":"source box exact certificate refused"}
    pts=[P.vec(p) for p in points];lo=tuple(F(x) for x in box["bboxLower"]);hi=tuple(F(x) for x in box["bboxUpper"])
    span=tuple(b-a for a,b in zip(lo,hi));axis=max(range(3),key=lambda i:(span[i],-i))
    interior=sorted({p[axis] for p in pts if lo[axis]<p[axis]<hi[axis]})
    midpoint=(lo[axis]+hi[axis])/2
    position=min(interior,key=lambda x:(abs(x-midpoint),x)) if interior else midpoint
    fraction=(position-lo[axis])/span[axis];threshold=max(F.from_float(3e-6),F(1,50)*span[axis])
    near=[str(x) for x in sorted({p[axis] for p in pts if p[axis]!=position and abs(p[axis]-position)<threshold})]
    cut_edges=[];new_points=[]
    for loop in loops:
        for a,b in zip(loop,loop[1:]+loop[:1]):
            if (pts[a][axis]-position)*(pts[b][axis]-position)<0:
                t=(position-pts[a][axis])/(pts[b][axis]-pts[a][axis])
                point=P.add(pts[a],P.mul(P.sub(pts[b],pts[a]),t))
                cut_edges.append((a,b))
                if point not in pts:new_points.append(point)
    wholeV=F(box["boxVolumeExactFraction"]);leftV=wholeV*fraction;rightV=wholeV-leftV
    leftHi=list(hi);leftHi[axis]=position;rightLo=list(lo);rightLo[axis]=position
    leftM=tuple(leftV*(lo[i]+leftHi[i])/2 for i in range(3));rightM=tuple(rightV*(rightLo[i]+hi[i])/2 for i in range(3))
    origM=tuple(F(x) for x in box["sourceFirstMomentExactFractions"])
    return {"splitAxis":axis,"originalInteriorCoordinates":[str(x) for x in interior],"usesOriginalSeamCoordinate":bool(interior),
            "planeExactFraction":str(position),"planeCoordinateM":float(position),"lowerShareExactFraction":str(fraction),
            "minSharePass":min(fraction,1-fraction)>=F(1,50),"cleanPlaneNearbyDistinctCoordinates":near,
            "cleanPlaneNecessaryPass":not near,"newIntersectionPointsRelativeToOriginalParent":[list(map(float,p)) for p in sorted(set(new_points))],
            "leftVolumeExactFraction":str(leftV),"rightVolumeExactFraction":str(rightV),
            "leftFirstMomentExactFractions":[str(x) for x in leftM],"rightFirstMomentExactFractions":[str(x) for x in rightM],
            "subBoxVolumeSumExact":leftV+rightV==wholeV,"subBoxFirstMomentSumExact":P.add(leftM,rightM)==origM,
            "subBoxVolumeEqualsOriginalSourceExact":leftV+rightV==F(box["sourceVolumeExactFraction"]),
            "originalSourceGeometryPartitionCertified":box["exactSourceBoxQualified"],
            "scope":"Two rectangular subboxes are a geometry-only partition. Face-role transport, vertex-star MPI/zone exclusions, neighbour conformity and full OF metrics must still be proven."}


def native_tolerance_box_diagnostics(points,loops,exact):
    xyz=np.asarray(points,float);lo=xyz.min(0);hi=xyz.max(0);span=float((hi-lo).max())
    max_axis=0.;max_plane=0.;max_out=0.;min_support=0.
    for loop in loops:
        p=xyz[loop];sf,fc=M.fan.face_geometry(p);normal=sf/np.linalg.norm(sf);axis=int(np.argmax(abs(normal)))
        max_axis=max(max_axis,float(np.max(abs(np.delete(normal,axis)))))
        max_plane=max(max_plane,min(abs(float(fc[axis]-lo[axis])),abs(float(fc[axis]-hi[axis]))))
        max_out=max(max_out,float(((xyz-fc)@normal).max()))
        edges=np.roll(p,-1,axis=0)-p;lengths=np.linalg.norm(edges,axis=1)
        support=np.einsum("evj,j->ev",np.cross(edges[:,None,:],p[None,:,:]-p[:,None,:]),normal)
        min_support=min(min_support,float((support/np.maximum(lengths[:,None],1e-300)).min()))
    sourceV=F(exact["sourceVolumeExactFraction"]);boxV=F(exact["boxVolumeExactFraction"])
    Verr=abs(sourceV-boxV)/boxV
    eligible=exact["directedEdgesClosedExactly"] and max_axis<=1e-12 and max_plane<=1e-12*span and max_out<=1e-12*span and min_support>=-1e-12*span and Verr<=F.from_float(1e-10)
    return {"maximumOffAxisNormalComponent":max_axis,"maximumFaceCentreToBBoxPlaneM":max_plane,
            "maximumVertexOutsideOriginalFacePlaneM":max_out,"minimumOriginalFaceEdgeSupportM":min_support,
            "relativeSourceVsBoxVolumeExactFraction":str(Verr),"relativeSourceVsBoxVolume":float(Verr),
            "matchesReportedNativeFloatingBoxNecessaryTolerances":eligible,
            "exactSourceBoxProof":exact["exactSourceBoxQualified"],
            "scope":"Native tolerance diagnostics only; neither coordinates nor exact flags are rounded or changed."}

def classify(mesh):
    mesh=Path(mesh);records=[];totals=Counter();identities={}
    ranks=sorted(int(p.name[9:]) for p in mesh.glob("processor[0-9]*") if p.name[9:].isdigit())
    for rank in ranks:
        geo=M.g.Geometry(mesh/f"processor{rank}/constant/polyMesh");t=M.read_trace(mesh,rank)
        zonepath=mesh/f"conforming-source-rank{rank}-zone.i32";zone=np.memmap(zonepath,dtype="<i4",mode="r")
        native=np.memmap(mesh/f"conforming-written-rank{rank}-point-native.i32",dtype="<i4",mode="r")
        bad=M.g.cell_set(geo.poly_dir/"sets/underdeterminedCells");parentcells={}
        for c in map(int,bad):
            totals["actualDCells"]+=1
            if B.tetrahedron_qualification(geo,c) is None:
                totals["nonTetDCells"]+=1;parentcells.setdefault(int(t["cellParent"][c]),[]).append(c)
                raw=geo.cell(c)
                totals["nonTetQuadPyramids"]+=len(raw[0])==5 and len(raw[5])==5 and sorted(len(geo.ids(int(f))) for f in raw[0])==[3,3,3,3,4]
        live=np.flatnonzero(t["faceAlive"]);lab=np.concatenate((t["owner"][live],t["neighbour"][live]));fac=np.tile(live,2)
        keep=(lab>=0)&(lab<t["sourceManifest"]["cells"]);order=np.argsort(lab[keep],kind="stable")
        source_faces=fac[keep][order];source_offsets=np.r_[0,np.cumsum(np.bincount(lab[keep],minlength=t["sourceManifest"]["cells"]))]
        coupled_native=set()
        for start,count,name,kind in geo.patches:
            if kind in {"cyclic","processor","processorCyclic"}:
                coupled_native.update(map(int,native[geo.vertices[geo.offsets[start]:geo.offsets[start+count]]]))
        zoned_faces=np.flatnonzero((zone>=0)&(t["faceAlive"]!=0));zone_vertices=set()
        for f in zoned_faces:zone_vertices.update(map(int,t["vertices"][t["offsets"][f]:t["offsets"][f+1]]))
        for parent,cells in sorted(parentcells.items()):
            fs=source_faces[source_offsets[parent]:source_offsets[parent+1]].tolist()
            original_loops=[];vertices=set()
            for f in fs:
                vs=t["vertices"][t["offsets"][f]:t["offsets"][f+1]].tolist()
                if t["owner"][f]!=parent:vs=vs[::-1]
                original_loops.append(vs);vertices.update(vs)
            vertices=sorted(vertices);index={v:i for i,v in enumerate(vertices)}
            loops=[[index[v] for v in loop] for loop in original_loops];points=t["points"][vertices].tolist()
            box=source_box(points,loops);plan=seam_plan(points,loops,box);near=native_tolerance_box_diagnostics(points,loops,box)
            nearplan=seam_plan(points,loops,box,allow_uncertified_box=True) if near["matchesReportedNativeFloatingBoxNecessaryTolerances"] else {"unsupported":"Native tolerance diagnostic refused"}
            coupled=sorted(set(vertices)&coupled_native);zones=sorted(set(vertices)&zone_vertices)
            children=np.flatnonzero(t["cellParent"]==parent)
            boundary={int(f) for c in children for f in geo.cell(int(c))[0] if f>=len(geo.neighbour)}
            physical=sorted({geo.patch(f)["name"] for f in boundary if geo.patch(f) and geo.patch(f)["type"] not in {"cyclic","processor","processorCyclic"}})
            near_candidate=near["matchesReportedNativeFloatingBoxNecessaryTolerances"] and box["aspectRatio"]>4 and not coupled and not zones and nearplan.get("minSharePass",False) and nearplan.get("cleanPlaneNecessaryPass",False)
            candidate=box["exactSourceBoxQualified"] and box["aspectRatio"]>4 and not coupled and not zones and plan.get("minSharePass",False) and plan.get("cleanPlaneNecessaryPass",False)
            row={"rank":rank,"sourceParent":parent,"actualBadWrittenCells":cells,"sourceFaceLabels":fs,"sourceVertexLabels":vertices,
                "sourceOriginalCoordinates":points,"sourceOriginalOrientedFaceLoops":loops,"sourceFaceZoneIDs":[int(zone[f]) for f in fs],
                "physicalPatchNames":physical,"postGhostCoupledVertexStarOriginalLabels":coupled,"sourceZonedVertexStarOriginalLabels":zones,
                "exactBox":box,"nativeToleranceDiagnostics":near,"nativeToleranceSeamNecessaryPlan":nearplan,"nativeToleranceOnlyThinBoxCandidate":near_candidate,"longAxisSplitGeometryPlan":plan,"geometryOnlyThinBoxCandidate":candidate,
                "preGhostCellRankStarUnavailableInFrozenTrace":True}
            records.append(row);totals["sourceParentGroups"]+=1
            totals["exactSourceBoxes"]+=box["exactSourceBoxQualified"]
            totals["reportedNativeToleranceBoxes"]+=near["matchesReportedNativeFloatingBoxNecessaryTolerances"]
            totals["exactThinBoxesAspectOver4"]+=box["exactSourceBoxQualified"] and box["aspectRatio"]>4
            totals["geometryOnlyThinBoxCandidates"]+=candidate
            totals["candidateBadCells"]+=len(cells) if candidate else 0
            totals["nativeToleranceOnlyThinBoxCandidates"]+=near_candidate
            totals["nativeToleranceCandidateBadCells"]+=len(cells) if near_candidate else 0
        identities[str(rank)]={"underdeterminedSetSHA256":M.sha(geo.poly_dir/"sets/underdeterminedCells"),"trace":t["identities"],
                              "sourceZoneSHA256":M.sha(zonepath),"pointNativeSHA256":M.sha(mesh/f"conforming-written-rank{rank}-point-native.i32")}
        print(json.dumps({"rank":rank,"totals":dict(totals)}),flush=True)
    return {"schema":"actual-source-box-split-classification/v1","mesh":str(mesh),"totals":dict(totals),"identity":identities,"parents":records,
        "scope":"Exact source geometry/seam necessary conditions only. Postghost coupled vertex-star is checked; preghost rank-star is not exported. No production, native, API, browser or full mesh acceptance."}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--mesh",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();d=classify(a.mesh);a.output.write_text(json.dumps(d,indent=2)+"\n");print(json.dumps(d["totals"]),flush=True)
if __name__=="__main__":main()
