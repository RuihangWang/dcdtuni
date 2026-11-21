#!/usr/bin/env python3
"""Offline N-cone proposals; actual local geometry/Pareto remain the gates."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import time
import numpy as np
from scipy.optimize import linprog
import audit_air_cone_quality_kernel as k


def cross_matrix(v):
    x,y,z=v
    return np.array([[0,-z,y],[z,0,-x],[-y,x,0]],float)


def side_wedge_directions(edge,d,angle=70.):
    edge=np.asarray(edge,float);d=np.asarray(d,float)
    en=np.linalg.norm(edge);dn=np.linalg.norm(d)
    if not np.isfinite(en+dn) or en<=0 or dn<=0:
        raise ValueError("nonfinite/zero fixed edge or centre difference")
    e=edge/en;perp=d-e*np.dot(e,d);pn=np.linalg.norm(perp)
    if not np.isfinite(pn) or pn<=1e-14*dn:
        raise ValueError("no resolved perpendicular centre difference")
    u=perp/pn;v=np.cross(e,u)
    alpha=math.cos(math.radians(angle))*dn/pn
    if not np.isfinite(alpha) or alpha<=0 or alpha>1:
        raise ValueError("unreachable N wedge in this proposal model")
    beta=math.sqrt(max(0.,1-alpha*alpha))/alpha
    # At alpha=1 the last row still requires the positive longitudinal branch.
    return np.array([beta*u-v,beta*u+v,u]),{"alpha":alpha,"beta":beta}


def external_inner_directions(sf,angle=70.,facets=16):
    sf=np.asarray(sf,float);amount=np.linalg.norm(sf)
    if not np.isfinite(amount) or amount<=0 or facets<3:
        raise ValueError("finite nonzero fixed face and >=3 cone facets required")
    n=sf/amount;axis=np.eye(3)[int(np.argmin(abs(n)))]
    u=np.cross(n,axis);u/=np.linalg.norm(u);v=np.cross(n,u)
    coefficient=math.cos(math.pi/facets)*math.tan(math.radians(angle))
    return np.array([coefficient*n-math.cos(2*math.pi*j/facets)*u
                     -math.sin(2*math.pi*j/facets)*v for j in range(facets)]+[n])


def normalized_rows(rows):
    rows=np.asarray(rows,float)
    return rows/np.maximum(np.max(abs(rows),axis=1),1e-300)[:,None]


class ProposalModel:
    def __init__(self,doc,angle=70.,facets=16):
        self.doc=doc;self.angle=angle;self.facets=facets
        if not np.isfinite(angle) or not 0<=angle<90:
            raise ValueError("an acute candidate N cone is required")
        for f in doc["affectedFaces"]:
            raw=doc["faces"][str(f)];kind=(raw.get("patch") or {}).get("type","").lower()
            if raw["neighbour"] is None and any(t in kind for t in ("cyclic","processor","ami","mapped")):
                raise ValueError("unknown coupled neighbour geometry is unavailable")
        self.origin=np.asarray(doc["originalApex"],float)
        self.length=float(np.ptp(np.asarray(list(doc["points"].values())),axis=0).max())
        rows,strict,names,faces,models,unsupported,approx=k.model_stencil(doc,True)
        self.approximation=approx;self.unsupported=unsupported
        self.models=models;self.face_models=faces
        wr=[strict[i] for i,name in enumerate(names) if name.startswith("sourcePlane:")]
        for m in faces.values():
            # Candidate reserve only; the acceptance thresholds stay unchanged.
            wr.extend(k.quality_rows(m["dOwn"],m["dNeighbour"],m["vOwn"],m["vNeighbour"],
                                     k.rational(.05000001),k.rational(.01000001)).values())
        self.wr=np.array([[float(x) for x in row] for row in k.normalize_rows(wr,self.origin,self.length)])
        direct=k._load("audit_air_cone_candidate_independent.py").Stencil(doc,self.origin)
        children=set(doc["children"]);entries=[];nrows=[];labels=[]
        def cc(cell):
            return (.75*np.array([float(x) for x in models[cell]["centre"]])+.25*self.origin
                    if cell in models else np.array(doc["cells"][str(cell)]["writtenCentre"]))
        for f,m in faces.items():
            raw=doc["faces"][str(f)];ids=raw["pointIDs"];sf,_=direct.face(f)
            own,nei=raw["owner"],raw["neighbour"];d=cc(nei)-cc(own)
            S=np.zeros((3,3));T=.25*self.length*np.eye(3)*((nei in children)-(own in children))
            if doc["apexWrittenPoint"] in ids:
                j=ids.index(doc["apexWrittenPoint"])
                P=np.array(doc["points"][str(ids[j-1])])
                Q=np.array(doc["points"][str(ids[(j+1)%len(ids)])])
                S=.5*cross_matrix(P-Q)*self.length
                directions,meta=side_wedge_directions(Q-P,d,angle)
                if np.any(T):raise ValueError("both moving centres must cancel the common AP")
                for index,h in enumerate(directions):
                    nrows.append(np.r_[S.T@h,np.dot(sf,h)]);labels.append(f"{f}:sideN:{index}")
                kind="sameParentSide"
            else:
                directions=external_inner_directions(sf,angle,facets);meta={}
                for index,h in enumerate(directions):
                    nrows.append(np.r_[T.T@h,np.dot(d,h)]);labels.append(f"{f}:externalN:{index}")
                kind="fixedExternalBase"
            entries.append({"face":f,"kind":kind,"sf":sf,"S":S,"d":d,"T":T,"metadata":meta})
        self.entries=entries;self.nlabels=labels;self.nrows=normalized_rows(nrows)
        self.rows=np.r_[self.wr,self.nrows]

    def apex(self,xi):return self.origin+self.length*np.asarray(xi)

    def proxy_cosines(self,xi):
        values=[]
        for row in self.entries:
            sf=row["sf"]+row["S"]@xi;d=row["d"]+row["T"]@xi
            values.append(np.dot(sf,d)/(np.linalg.norm(sf)*np.linalg.norm(d)))
        return np.array(values)

    def lp_candidate(self):
        A,b=self.rows[:,:3],self.rows[:,3]
        result=linprog([0,0,0,-1],A_ub=np.c_[-A,np.ones(len(A))],b_ub=b,
                       bounds=[(None,None)]*4,method="highs")
        receipt={"success":bool(result.success),"status":int(result.status),"message":result.message,
                 "scope":"Floating sufficient proposal polytope; no impossibility certificate."}
        if result.success:
            receipt["minimumNormalizedSlack"]=float(result.x[3])
            receipt["minimumRowValue"]=float((A@result.x[:3]+b).min())
            return self.apex(result.x[:3]),receipt
        return None,receipt


def axis_box(doc):
    lo=np.full(3,-np.inf);hi=np.full(3,np.inf)
    for plane in doc["sourceKernel"]["originalPlanes"]:
        n=np.array(plane["normal"]);axis=int(np.argmax(abs(n)))
        if np.max(abs(np.delete(n,axis)))>1e-12:
            raise ValueError("portable clipping replay requires axis-aligned source kernel")
        if n[axis]>0:hi[axis]=min(hi[axis],plane["centre"][axis])
        else:lo[axis]=max(lo[axis],plane["centre"][axis])
    if not np.all(np.isfinite(lo+hi)) or np.any(lo>=hi):
        raise ValueError("bounded nonempty kernel box required")
    return lo,hi


def native_clip_proposals(model):
    """Replay existing native proposal-only convex clipping, in normalized box."""
    lo,hi=axis_box(model.doc);size=hi-lo
    rows=model.rows.copy()
    rows[:,3]+=rows[:,:3]@((lo-model.origin)/model.length)
    rows[:,:3]*=size[None,:]/model.length
    planes=[]
    for row in rows:
        norm=np.linalg.norm(row[:3]);scale=np.max(abs(row))
        if norm<=1e-12*max(scale,1e-300):
            if row[3]<-1e-12*max(scale,1e-300):return [],{"empty":True}
            continue
        planes.append(row/norm)
    l=3.00001e-6/size;h=1-l
    if np.any(l>=h):return [],{"empty":True}
    verts=[np.array([x,y,z]) for z in [l[2],h[2]] for y in [l[1],h[1]] for x in [l[0],h[0]]]
    faces=[[verts[i].copy() for i in row] for row in [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]]]
    def unique(points,q):
        if not any(np.dot(p-q,p-q)<=1e-24 for p in points):points.append(q)
    for row in planes:
        nxt=[];cap=[]
        for face in faces:
            cut=[]
            for index,a in enumerate(face):
                b=face[(index+1)%len(face)]
                da,db=np.dot(row[:3],a)+row[3],np.dot(row[:3],b)+row[3]
                if (da>=0)!=(db>=0):
                    q=a+da/(da-db)*(b-a);unique(cut,q);unique(cap,q)
                if db>=0:unique(cut,b)
            if len(cut)>=3:nxt.append(cut)
        if len(cap)>=3:
            centre=np.mean(cap,axis=0);a=row[:3]
            ref=np.eye(3)[int(np.argmin(abs(a)))];u=np.cross(a,ref);u/=np.linalg.norm(u);v=np.cross(a,u)
            cap.sort(key=lambda p:(math.atan2(np.dot(p-centre,v),np.dot(p-centre,u)),*p))
            nxt.append(cap)
        faces=nxt
        if not faces:return [],{"empty":True}
    allpoints=[]
    for face in faces:
        for point in face:unique(allpoints,point)
    if len(allpoints)<4:return [],{"empty":True}
    vertices=np.array(allpoints);mean=vertices.mean(axis=0)
    box=.5*(vertices.min(axis=0)+vertices.max(axis=0));near=(model.origin-lo)/size
    for sweep in range(256):
        worst=0.
        for row in planes:
            distance=np.dot(row[:3],near)+row[3]
            if distance<0:near-=distance*row[:3];worst=max(worst,-distance)
        if worst<1e-12:break
    result=[]
    for name,q in [("vertexMean",mean),("vertexBBoxMid",box),("nearCurrent",near),
                   ("near75Mean25",.75*near+.25*mean),("near50Mean50",.5*(near+mean)),
                   ("near25Mean75",.25*near+.75*mean)]:
        ap=lo+q*size
        if not any(np.linalg.norm(ap-old[1])<=1e-12 for old in result):result.append((name,ap))
    return result,{"empty":False,"polytopeVertices":len(vertices),"planes":len(planes),"projectionSweeps":sweep+1}


def evaluate(doc,old,apex):
    q=k.actual_stencil_quality(doc,apex);pareto=k.actual_pareto(old,q)
    volume=abs(q["sumVolume"]-old["sumVolume"])/old["sumVolume"]
    moment=float(np.linalg.norm(np.array(q["firstMoment"])-old["firstMoment"]))
    span=float(np.ptp(np.array(list(doc["points"].values())),axis=0).max())
    direct=k._load("audit_air_cone_candidate_independent.py").Stencil(doc,apex)
    child_gates=[];face_gates=[]
    for child in q["cells"]:
        c=child["cell"];areas,centres,cc,V=direct.cell(c)
        vertices=np.array([direct.points[v] for f in doc["cells"][str(c)]["faces"]
                           for v in doc["faces"][str(f)]["pointIDs"]])
        cell_span=max(float(np.linalg.norm(p-np.asarray(apex))) for p in vertices)
        normals=areas/np.linalg.norm(areas,axis=1)[:,None];worst=-np.inf
        for i in range(len(centres)):
            delta=centres-centres[i];length=np.linalg.norm(delta,axis=1)
            cosine=delta@normals[i]/np.maximum(length,1e-300)
            worst=max(worst,float(cosine[np.arange(len(centres))!=i].max()))
        child_gates.append({"cell":c,"maximumOtherFaceCentreCosine":worst,
                            "vertexHalfspaceGatePassed":bool(child["maximumVertexOutsideM"]<=1e-10*max(cell_span,1e-6)),
                            "strictFaceCentreGatePassed":bool(worst<-1.0001e-6),
                            "strictPyramidGatePassed":bool(child["minCentroidPyramidVolume"]>1e-15*V)})
    for face in q["faces"]:
        f=face["face"];sf,fc=direct.face(f)
        face_span=max(np.linalg.norm(p-fc) for p in direct.polygon(f))
        face_gates.append({"face":f,"polygonSupportPassed":bool(face["polygonMinimumSupportM"]>=-1e-10*max(face_span,1e-6))})
    geometry=(all(c["vertexHalfspaceGatePassed"] and c["strictFaceCentreGatePassed"] and c["strictPyramidGatePassed"] for c in child_gates)
              and all(f["polygonSupportPassed"] for f in face_gates)
              and not q["unsupportedCoupledFaces"] and q["minimumParentPlaneMarginM"]>3e-6
              and q["minimumPyramidVolume"]>0 and q["minimumChildVolume"]>0
              and q["maximumClosure"]<1e-12 and q["maximumNativePlanarityGateFraction"]<=1
              and q["allEdgesClosed"] and q["concavityPairTriggers"]==0
              and q["minimumCentreFanTetQuality"]>1e-15 and q["minimumSharedBasePointTetQuality"]>1e-15
              and q["minimumFaceAreaM2"]>1e-12 and q["maximumVertexOutsideM"]<1e-10*span
              and q["minimumContextFixedPointDistanceM"]>3e-6
              and volume<1e-10 and moment/(old["sumVolume"]*span)<1e-10)
    passed=all(v["noNewFailures"] and v["countDoesNotIncrease"] and v["worstFailedSeverityDoesNotIncrease"] for v in pareto.values())
    allpass=(q["minimumDeterminant"]>=.001 and q["minimumWeight"]>=.05 and q["minimumRatio"]>=.01
             and q["maximumSkewness"]<=4 and q["maximumNonOrthogonalityDegrees"]<=70)
    score=min(q["minimumDeterminant"]/.001,q["minimumWeight"]/.05,q["minimumRatio"]/.01,
              4/max(q["maximumSkewness"],1e-300),70/max(q["maximumNonOrthogonalityDegrees"],1e-300))
    return {"apex":list(map(float,apex)),"actual":q,"pareto":pareto,
            "childGeometryGateDetails":child_gates,"faceGeometryGateDetails":face_gates,
            "passesLocalGeometry":bool(geometry),"passesLocalFiveMetricPareto":bool(passed),
            "allLocalFiveMetricsPass":bool(allpass),"normalizedWorstScore":score,
            "relativeVolumeDifference":volume,"firstMomentDifference":moment}


def analyze(doc):
    started=time.monotonic();model=ProposalModel(doc,70-1e-5)
    old=k.actual_stencil_quality(doc,doc["originalApex"]);point,lp=model.lp_candidate()
    proposed=[] if point is None else [("linearMaxSlack",point)]
    clips,clip_receipt=native_clip_proposals(model);proposed+=clips
    candidates=[]
    for name,point in proposed:
        result=evaluate(doc,old,point);result["derivation"]=name;candidates.append(result)
    return {"schema":"dcdtuni.nonorth-cone-proposal.v1","rank":doc["rank"],"parent":doc["parent"],
            "scope":"Pure offline candidate generation and actual complete local stencil reevaluation; no native acceptance.",
            "candidateAngleDegrees":model.angle,"externalInnerPolygonFacets":model.facets,
            "children":len(doc["children"]),"affectedFaces":len(doc["affectedFaces"]),
            "wrRows":len(model.wr),"nonorthRows":len(model.nrows),"nonorthRowNames":model.nlabels,
            "normalizedRows":model.rows.tolist(),"normalizedOrigin":model.origin.tolist(),
            "normalizedLength":model.length,"approximation":model.approximation,"unsupported":model.unsupported,
            "originalActual":old,"linearSolver":lp,"portableNativeClipReplay":clip_receipt,
            "candidates":candidates,"seconds":time.monotonic()-started}



def classify_regressions(baseline,new):
    """Recompute every new written D label and bind the actual selected traces."""
    q=k._load("audit_air_cone_remaining_quality.py")
    records=[];identities={};set_rows=[];baseline_total=0;new_total=0
    for rank in range(8):
        op=baseline/f"processor{rank}/constant/polyMesh"
        npoly=new/f"processor{rank}/constant/polyMesh"
        old_set=op/"sets/underdeterminedCells";new_set=npoly/"sets/underdeterminedCells"
        before=set(map(int,q.g.cell_set(old_set)))
        after=set(map(int,q.g.cell_set(new_set)))
        core={name:{"baselineSHA256":k.sha(op/name),"newSHA256":k.sha(npoly/name)}
              for name in ["faces","owner","neighbour","boundary"]}
        topology={name:value["baselineSHA256"]==value["newSHA256"] for name,value in core.items()}
        if not all(topology.values()):raise ValueError("written set IDs require exact core topology in every rank")
        baseline_total+=len(before);new_total+=len(after)
        labels=sorted(after-before)
        set_rows.append({"rank":rank,"baselineCount":len(before),"newCount":len(after),"newlyBad":labels,
                         "baselineLabels":sorted(before),"newLabels":sorted(after),"nonPointsCoreIdentity":core,
                         "baselineSetIdentity":{"path":str(old_set),"sha256":k.sha(old_set) if old_set.exists() else None},
                         "newSetIdentity":{"path":str(new_set),"sha256":k.sha(new_set) if new_set.exists() else None}})
        if not labels:continue
        og=q.g.Geometry(op);ng=q.g.Geometry(npoly)
        parents=np.memmap(new/f"conforming-written-rank{rank}-cell-parent.i32",mode="r",dtype="<i4")
        native_faces=np.memmap(new/f"conforming-written-rank{rank}-face-native.i32",mode="r",dtype="<i4")
        source_apex=np.memmap(new/f"conforming-source-rank{rank}-apex.f64",mode="r",dtype="<f8").reshape(-1,3)
        def selected(mesh,parent):
            path=mesh/f"conforming-kernel-candidates-rank{rank}.jsonl";found=[]
            for line in path.open():
                if f'"parent":{parent},' in line:
                    row=json.loads(line)
                    if row["decision"]=="selected-off-experiment":found.append(row)
            if not found:raise ValueError("selected candidate trace is unavailable")
            return found[-1],k.sha(path)
        grouped={}
        for cell in labels:grouped.setdefault(int(parents[cell]),[]).append(cell)
        for parent,cells in grouped.items():
            old_trace,old_sha=selected(baseline,parent);new_trace,new_sha=selected(new,parent)
            children=list(map(int,np.flatnonzero(parents==parent)))
            common=set.intersection(*(set(map(int,ng.cell(c)[5])) for c in children))
            if len(common)!=1:raise ValueError("actual parent children must have one shared written apex")
            apex=next(iter(common))
            for cell in cells:
                bases=[int(f) for f in ng.cell(cell)[0] if apex not in set(map(int,ng.ids(f)))]
                if len(bases)!=1:raise ValueError("one fixed base per cone required")
                native_base=int(native_faces[bases[0]])
                j=new_trace["baseOrder"].index(native_base);jo=old_trace["baseOrder"].index(native_base)
                old_geom=q.cell_row(og,cell,True);new_geom=q.cell_row(ng,cell,True)
                row={"rank":rank,"writtenCell":cell,"sourceParent":parent,"writtenApexPoint":apex,
                     "writtenBaseFace":bases[0],"nativeBaseFace":native_base,
                     "baselineApex":og.points[apex].tolist(),"newApex":ng.points[apex].tolist(),
                     "baselineActualD":old_geom["cellDeterminant"],"newActualD":new_geom["cellDeterminant"],
                     "newComparisonOriginalProfileD":new_trace["old"]["metrics"][0][j],
                     "baselineSelectedProfileD":old_trace["next"]["metrics"][0][jo],
                     "newSelectedProfileD":new_trace["next"]["metrics"][0][j],
                     "newComparisonOriginalAP":new_trace["original"],"sourceSnapshotAP":source_apex[parent].tolist(),
                     "comparisonUsesSourceInitialAP":bool(np.array_equal(new_trace["original"],source_apex[parent])),
                     "exactNonPointsCoreTopology":topology,
                     "baselineCellGeometry":old_geom,"newCellGeometry":new_geom}
                records.append(row)
            identities[f"rank{rank}/parent{parent}"]={
                "baselineCandidateLogSHA":old_sha,"newCandidateLogSHA":new_sha,
                "writerParentMapSHA":k.sha(new/f"conforming-written-rank{rank}-cell-parent.i32"),
                "writerFaceNativeMapSHA":k.sha(new/f"conforming-written-rank{rank}-face-native.i32"),
                "sourceSnapshotApexSHA":k.sha(new/f"conforming-source-rank{rank}-apex.f64"),
                "baselinePointsSHA":k.sha(op/"points"),"newPointsSHA":k.sha(npoly/"points")}
    explained=bool(records) and all(v["comparisonUsesSourceInitialAP"] and v["newComparisonOriginalProfileD"]<.001
                                    and v["baselineActualD"]>=.001 and v["newActualD"]<.001 for v in records)
    return {"schema":"dcdtuni.written-det-baseline-regression.v1",
            "scope":"Complete eight-rank determinant-set differences, actual unchanged-topology written geometry and native candidate traces. No native execution or mesh modification.",
            "baselineMesh":str(baseline),"newMesh":str(new),"cells":records,"identities":identities,
            "determinantSetDifference":{"baselineCount":baseline_total,"newCount":new_total,"ranks":set_rows},
            "sourceInitialComparisonExplainsEveryNewD":explained,
            "cause":"If the verified initial-comparison flag is true, source-initial AP already had the D labels bad; comparing new N candidates to that initial AP can reintroduce labels repaired by the completed WR-only selected baseline. Compare additional N candidates to actual completed WR baseline, then retain full written no-new/full checker gates."}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",type=Path);parser.add_argument("--parent",type=int)
    parser.add_argument("--baseline-mesh",type=Path);parser.add_argument("--new-mesh",type=Path)
    parser.add_argument("--rank",type=int,default=None);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.baseline_mesh or args.new_mesh:
        if not args.baseline_mesh or not args.new_mesh:raise ValueError("both frozen meshes required")
        result=classify_regressions(args.baseline_mesh,args.new_mesh)
    else:
        if args.input is None or args.parent is None:raise ValueError("an input and parent are required")
        docs=json.loads(args.input.read_text())["stencils"]
        choices=[d for d in docs if d["parent"]==args.parent and (args.rank is None or d["rank"]==args.rank)]
        if len(choices)!=1:raise ValueError("exactly one parent/rank stencil required")
        result=analyze(choices[0]);result["sourceInput"]={"path":str(args.input),"sha256":k.sha(args.input)}
    result["toolSHA256"]=k.sha(Path(__file__));result["qualityKernelToolSHA256"]=k.sha(Path(k.__file__))
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    if "candidates" in result:
        summary={"parent":result["parent"],"children":result["children"],"affectedFaces":result["affectedFaces"],
                 "passesLocalGeometryAndFivePareto":[c["derivation"] for c in result["candidates"] if c["passesLocalGeometry"] and c["passesLocalFiveMetricPareto"]],
                 "allLocalFiveMetricsPass":[c["derivation"] for c in result["candidates"] if c["passesLocalGeometry"] and c["allLocalFiveMetricsPass"]],
                 "noNativeAcceptance":True,"seconds":result["seconds"]}
    else:
        summary={"actualNewDLabels":len(result["cells"]),"completeDetSetCounts":{key:value for key,value in result["determinantSetDifference"].items() if key!="ranks"},
                 "sourceInitialComparisonExplainsEveryNewD":result["sourceInitialComparisonExplainsEveryNewD"],"noNativeAcceptance":True}
    print(json.dumps(summary))



if __name__=="__main__":main()
