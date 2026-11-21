#!/usr/bin/env python3
"""Read-only actual cone-parent kernel candidates, all children and external neighbours."""
from __future__ import annotations
import argparse,copy,importlib.util,itertools,json
from pathlib import Path
import numpy as np
spec=importlib.util.spec_from_file_location("remaining",Path(__file__).with_name("audit_air_cone_remaining_quality.py"))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
spec=importlib.util.spec_from_file_location("partition",Path(__file__).with_name("audit_air_face_partition.py"))
part=importlib.util.module_from_spec(spec);spec.loader.exec_module(part)
def evaluate(geo,children,faceids,apexid,point,external_cache,detail=False):
    trial=copy.copy(geo);trial.points=geo.points.copy();trial.points[apexid]=point;trial.cells=dict(external_cache);trial.faces={}
    rows=[];facerows=[]
    for c in sorted(children):
        fs,areas,fcs,cc,volume,vid,lo,hi=trial.cell(c)
        sf=[a for f,a in zip(fs,areas) if trial.patch(int(f)) is None or trial.patch(int(f))["type"] in ["cyclic","processor","processorCyclic"]]
        rows.append({"cell":c,"V":volume,"CC":cc.tolist(),"determinant":m.determinant(sf)[0]})
    for f in sorted(faceids):
        sf,fc,*_=trial.face(f);oc=trial.cell(int(trial.owner[f]));nc=trial.cell(int(trial.neighbour[f])) if f<len(trial.neighbour) else None
        patch=trial.patch(f)
        if patch and patch["type"] in ["cyclic","processor","processorCyclic"]:raise ValueError("this parent probe requires all cross-parent neighbours locally available")
        facerows.append({"face":f,"owner":int(trial.owner[f]),"neighbour":int(trial.neighbour[f]) if nc else None,
          "outsideParent":(int(trial.owner[f]) not in children or (nc is not None and int(trial.neighbour[f]) not in children)),
          "patch":patch,**m.face_metrics(sf,fc,oc[3],oc[4],trial.points[trial.ids(f)],nc[3] if nc else None,nc[4] if nc else None)})
    for row in facerows:
        f=row["face"]
        if f<len(trial.neighbour):
            delta=trial.cell(int(trial.neighbour[f]))[3]-trial.cell(int(trial.owner[f]))[3];sf=trial.face(f)[0]
            cosine=np.dot(sf,delta)/(np.linalg.norm(sf)*np.linalg.norm(delta))
            row["nonOrthogonalityDegrees"]=float(np.degrees(np.arccos(np.clip(cosine,-1,1))))
    det=min(row["determinant"] for row in rows);weight=min(row["weight"] for row in facerows if "weight" in row)
    ratio=min(row["volumeRatio"] for row in facerows if "volumeRatio" in row);skew=max(row["skewness"] for row in facerows)
    result={"point":list(map(float,point)),"cells":len(children),"incidentFaces":len(faceids),
      "maximumInternalNonOrthogonalityDegrees":max(r["nonOrthogonalityDegrees"] for r in facerows if "nonOrthogonalityDegrees" in r),
      "minimumDeterminant":det,"minimumWeight":weight,"minimumVolumeRatio":ratio,"maximumSkewness":skew,
      "normalizedMinimumQuality":min(det/.001,weight/.05,ratio/.01,4/skew),
      "cellVolumeSum":sum(row["V"] for row in rows),"firstMomentSum":sum((np.array(row["CC"])*row["V"] for row in rows),np.zeros(3)).tolist(),
      "failedDeterminants":[r for r in rows if r["determinant"]<.001],
      "failedWeights":[r for r in facerows if r.get("weight",1)<.05],
      "failedVolumeRatios":[r for r in facerows if r.get("volumeRatio",1)<.01],
      "failedSkewness":[r for r in facerows if r["skewness"]>4]}
    if detail:
        checks=[];tetrows=[];baseunchanged=True;edgespaired=True;allcoordsunchanged=True
        for c in sorted(children):
            row=trial.classify(c,True)
            fs,areas,*_=trial.cell(c);edge={}
            for f,a in zip(fs,areas):
                ids=trial.ids(int(f)).tolist()
                if trial.owner[f]!=c:ids=ids[:1]+ids[:0:-1]
                for x,y in zip(ids,ids[1:]+ids[:1]):
                    key=tuple(sorted((x,y)));counts=edge.setdefault(key,[0,0]);counts[0]+=1;counts[1]+=1 if x<y else -1
            row["directedEdgesCloseExactly"]=all(v==[2,0] for v in edge.values())
            edgespaired=edgespaired and row["directedEdgesCloseExactly"];checks.append(row)
        for f in sorted(faceids):
            sf,fc,plane,*_=trial.face(f);ids=trial.ids(f);points=trial.points[ids]
            oc=trial.cell(int(trial.owner[f]));nc=trial.cell(int(trial.neighbour[f])) if f<len(trial.neighbour) else None
            fq=part.fan_quality(points,oc[3],nc[3] if nc else None)
            fq.update({"face":f,"planeErrorM":plane,"nativeRelativePlanarityLimitM":1e-10*float(np.max(np.linalg.norm(points-fc,axis=1)))})
            tetrows.append(fq)
            if apexid not in ids:baseunchanged=baseunchanged and np.array_equal(points,geo.points[geo.ids(f)])
        no_apex=np.arange(len(geo.points))!=apexid
        allcoordsunchanged=np.array_equal(trial.points[no_apex],geo.points[no_apex])
        distance=np.linalg.norm(trial.points[no_apex]-np.asarray(point),axis=1).min()
        result["geometry"]={"allNonApexWrittenCoordinatesUnchanged":allcoordsunchanged,"allOriginalBaseFaceCoordinatesUnchanged":baseunchanged,
          "minimumNewApexToOtherWrittenPointDistanceM":float(distance),"allDirectedCellEdgesCloseExactly":edgespaired,
          "allCellVolumesPositive":all(r["volumeM3"]>0 for r in checks),
          "maximumRelativeCellClosure":max(r["relativeClosure"] for r in checks),
          "maximumVertexOutsideFacePlaneM":max(r["allVertexMaximumOutsideMetres"] for r in checks),
          "faceCentrePlaneCheckerTriggers":sum(sum(r["checkerTriggerCounts"].values()) for r in checks),
          "allFacePolygonsPlanarConvex":all(r["allFacePolygonsPlanar"] and r["allFacePolygonsConvex"] for r in checks),
          "allFacesPassNativeRelativePlanarityCriterion":all(r["planeErrorM"]<=r["nativeRelativePlanarityLimitM"] for r in tetrows),
          "minimumWrittenPolygonAreaM2":min(float(np.linalg.norm(trial.face(f)[0])) for f in faceids),
          "minimumFaceTetFanQuality":min(r["minimumQuality"] for r in tetrows),
          "minimumCellCentroidPyramidVolumeM3":min(r["minimumCentroidPyramidVolumeM3"] for r in checks)}
        result.update({"children":rows,"faces":facerows,"geometricCells":checks,"faceTetFans":tetrows})
    return result
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mesh",type=Path,required=True);ap.add_argument("--trace-tool",type=Path,required=True)
    ap.add_argument("--parent-samples",type=Path,required=True);ap.add_argument("--rank",type=int,default=3);ap.add_argument("--parent",type=int,default=9885)
    ap.add_argument("--output",type=Path,required=True);args=ap.parse_args()
    if args.rank!=3 or args.parent!=9885:raise ValueError("this frozen grid is explicitly scoped to actual rank3 parent9885")
    sample_document=json.loads(args.parent_samples.read_text())
    if sample_document["toolSHA256"]!=m.g.sha(args.trace_tool):raise ValueError("trace helper changed since parent samples")
    if sample_document["geometryToolSHA256"]!=m.g.sha(Path(__file__).with_name("audit_air_mesh_geometry.py")):raise ValueError("parent geometry oracle source identity mismatch")
    spec=importlib.util.spec_from_file_location("trace",args.trace_tool);trace=importlib.util.module_from_spec(spec);spec.loader.exec_module(trace)
    t=trace.read_trace(args.mesh,args.rank);geo=m.g.Geometry(args.mesh/f"processor{args.rank}/constant/polyMesh")
    children=set(map(int,np.flatnonzero(t["cellParent"]==args.parent)))
    if not children:raise ValueError("no actual written children map to requested input-stage parent")
    common=set.intersection(*(set(geo.cell(c)[5].tolist()) for c in children))
    if len(common)!=1:raise ValueError("exactly one shared parent apex expected")
    apexid=next(iter(common));nativeid=int(t["pointNative"][apexid])
    if nativeid<t["sourceManifest"]["points"]:raise ValueError("shared apex is an original source point; unauthorized motion")
    parent=next(r for r in json.loads(args.parent_samples.read_text())["parents"] if r["rank"]==args.rank and r["inputParent"]==args.parent)
    if not np.array_equal(geo.points[apexid],np.asarray(parent["inputApex"])):raise ValueError("apex source and actual written coordinates differ")
    faceids={int(f) for c in children for f in geo.cell(c)[0]}
    neighbours={int(geo.owner[f]) for f in faceids}|{int(geo.neighbour[f]) for f in faceids if f<len(geo.neighbour)}
    outside=neighbours-children;external_cache={c:geo.cell(c) for c in outside}
    apexfaces=set(np.searchsorted(geo.offsets,np.flatnonzero(geo.vertices==apexid),side="right")-1)
    if not apexfaces<=faceids:raise ValueError("apex has incidences outside its actual mapped children")
    if any(f>=len(geo.neighbour) or int(geo.owner[f]) not in children or int(geo.neighbour[f]) not in children for f in apexfaces):raise ValueError("new apex affects physical/coupled/cross-parent geometry")
    normals=np.array([r["outwardArea"] for r in parent["originalFaces"]]);normals/=np.linalg.norm(normals,axis=1)[:,None]
    distances=np.einsum("ij,ij->i",normals,np.array([r["OFcentre"] for r in parent["originalFaces"]]))
    candidates=[]
    # A local deterministic mathematical search; it creates no native mesh.
    for x,y,z in itertools.product(np.linspace(3.771,3.819,5),np.linspace(2.705,2.742,5),np.linspace(1.50,1.595,6)):
        point=np.array([x,y,z]);margin=distances-normals@point
        if margin.min()<=3e-6:continue
        row=evaluate(geo,children,faceids,apexid,point,external_cache)
        row["minimumOriginalParentPlaneMarginM"]=float(margin.min());candidates.append(row)
    chosen=max(candidates,key=lambda row:row["normalizedMinimumQuality"])
    lower=np.full(3,-np.inf);upper=np.full(3,np.inf);off_axis=[]
    for n,distance in zip(normals,distances):
        axis=int(np.argmax(abs(n)));off_axis.append(float(np.max(abs(np.delete(n,axis)))))
        if off_axis[-1]>1e-12:raise ValueError("axis-kernel candidate derivation unsupported for this non-axis parent")
        if n[axis]>0:upper[axis]=min(upper[axis],distance/n[axis])
        else:lower[axis]=max(lower[axis],distance/n[axis])
    if not np.isfinite(lower).all() or not np.isfinite(upper).all() or not (lower<upper).all():raise ValueError("bounded axis kernel required")
    kernel_center=(lower+upper)*.5
    bbox_mix=kernel_center.copy();bbox_mix[2]=.5*(parent["bbox"][0][2]+parent["bbox"][1][2])
    named={"original":parent["inputApex"],"chebyshev":parent["chebyshevKernelCandidate"]["point"],"gridBest":chosen["point"],
           "axisKernelCenter":kernel_center.tolist(),"kernelXYParentBBoxZ":bbox_mix.tolist()}
    results={}
    for name,point in named.items():
        value=evaluate(geo,children,faceids,apexid,point,external_cache,True)
        margin=distances-normals@point;value["parentPlaneMarginsM"]=margin.tolist()
        value["minimumOriginalParentPlaneMarginM"]=float(margin.min())
        if not np.isfinite(margin).all() or margin.min()<=3e-6:raise ValueError("candidate violates actual original parent kernel")
        source_moment=np.asarray(parent["inputApex"])*parent["inputVolume"]
        value["maximumFirstMomentDifferenceFromInputParentM4"]=float(np.max(np.abs(np.array(value["firstMomentSum"])-source_moment)))
        value["minimumCandidateToSourceOriginalPointDistanceM"]=float(np.min(np.linalg.norm(np.asarray(t["points"])-np.asarray(point),axis=1)))
        value["relativeParentVolumeError"]=abs(value["cellVolumeSum"]-parent["inputVolume"])/parent["inputVolume"]
        results[name]=value
    for value in results.values():
        value["maximumFirstMomentDifferenceFromOriginalM4"]=float(np.max(np.abs(np.array(value["firstMomentSum"])-np.array(results["original"]["firstMomentSum"]))))
    mapping=[{"writtenCell":c,"nativeCell":int(t["cellNative"][c]),"inputStageParent":int(t["cellParent"][c])} for c in sorted(children)]
    out={"format":"actual-parent-kernel-math-diagnostic/v1","scope":"No mesh file is modified or generated. All children and all locally available cross-parent neighbour CC/V are evaluated. This is not a native full-checkMesh acceptance.",
         "mesh":str(args.mesh),"rank":args.rank,"inputStageParent":args.parent,"writtenApexPoint":apexid,"nativeApexPoint":nativeid,
         "originalSourcePointCount":t["sourceManifest"]["points"],"childrenMap":mapping,"crossParentOutsideNeighbourCells":sorted(outside),
         "derivedAxisKernelBounds":{"lower":lower.tolist(),"upper":upper.tolist(),"candidateCentre":kernel_center.tolist(),
           "maximumOffAxisNormalComponent":max(off_axis),"recognitionTolerance":1e-12,"candidateOnlyActual17PlaneGuardStillRequired":True},
         "outsideUnchangedLowDeterminantCells":[{"cell":c,"determinant":m.cell_row(geo,c)["cellDeterminant"]} for c in sorted(outside) if m.cell_row(geo,c)["cellDeterminant"]<.001],
         "actualInputParent":parent,"externalNeighbourOriginalGeometry":[geo.classify(c,True) for c in sorted(outside)],"search":{"candidateCount":len(candidates),"dimensionlessScore":"min(D/.001,weight/.05,volumeRatio/.01,4/skew)","candidates":candidates},
         "results":results,"inputSha256":{str(p):m.g.sha(p) for p in [args.trace_tool,args.parent_samples,Path(__file__),Path(__file__).with_name("audit_air_cone_remaining_quality.py"),
             Path(__file__).with_name("audit_air_mesh_geometry.py"),Path(__file__).with_name("audit_air_written_coupled_pairs.py"),Path(__file__).with_name("audit_air_face_partition.py"),Path(__file__).with_name("audit_air_transition_roof.py")]},
         "traceSha256":{p.name:m.g.sha(p) for p in args.mesh.glob(f"conforming-*-rank{args.rank}-*") if p.is_file()},
         "meshSha256":{p.name:m.g.sha(p) for p in geo.poly_dir.iterdir() if p.is_file()},
         "limitations":["Input-stage parent ancestry is explicitly the alive cell just before the optional decomposition, not STL or octree ancestry.",
             "No coupled external face occurs in this selected parent; this tool fails for coupled external faces instead of guessing remote values.",
             "The deterministic coordinate grid is specific to this frozen parent9885; it is not a production mesher algorithm or proof of optimum.",
             "Full mesh checker, global conformity, all-parent quality, point canonicalization and total deadline remain product acceptance gates."]}
    args.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({name:{k:value[k] for k in ["minimumDeterminant","minimumWeight","minimumVolumeRatio","maximumSkewness","normalizedMinimumQuality","relativeParentVolumeError","maximumFirstMomentDifferenceFromOriginalM4","maximumFirstMomentDifferenceFromInputParentM4","geometry"]} for name,value in results.items()},indent=2))
if __name__=="__main__":main()
