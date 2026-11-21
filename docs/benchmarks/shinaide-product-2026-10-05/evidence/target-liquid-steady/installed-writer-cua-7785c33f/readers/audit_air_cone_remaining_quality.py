#!/usr/bin/env python3
"""Read-only OF v2412 quality formulas on actual rejected written sets.

Parent ancestry is unavailable unless the native writer explicitly supplies it.
These rows diagnose geometry only and never change a mesh acceptance threshold.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,re
from collections import Counter
from fractions import Fraction
from pathlib import Path
import numpy as np
spec=importlib.util.spec_from_file_location("coupled",Path(__file__).with_name("audit_air_written_coupled_pairs.py"))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);g=m.g

def determinant(areas):
    a=np.asarray(areas,dtype=float).reshape(-1,3)
    if not len(a):return 0.,[0.,0.,0.],np.zeros((3,3))
    scale=np.linalg.norm(a,axis=1).mean()
    if not scale:return 0.,[0.,0.,0.],np.zeros((3,3))
    tensor=(a/scale).T@(a/scale)
    return float(abs(np.linalg.det(tensor))/8),np.linalg.eigvalsh(tensor).tolist(),tensor
def face_metrics(sf,fc,owncc,ownv,points,neicc=None,neiv=None):
    sf=np.asarray(sf);fc=np.asarray(fc);owncc=np.asarray(owncc);points=np.asarray(points)
    cpf=fc-owncc
    if neicc is None:
        normal=sf/np.linalg.norm(sf);d=normal*np.dot(normal,cpf);fraction=.4
    else:d=np.asarray(neicc)-owncc;fraction=.2
    sv=cpf-np.dot(sf,cpf)/(np.dot(sf,d)+1e-150)*d
    svhat=sv/(np.linalg.norm(sv)+1e-150)
    fd=max(fraction*np.linalg.norm(d)+1e-150,float(np.max(np.abs((points-fc)@svhat))))
    row={"skewness":float(np.linalg.norm(sv)/fd),"normalOwnerDistanceM":float(abs(np.dot(sf,cpf))/np.linalg.norm(sf))}
    if neicc is not None:
        do=abs(np.dot(sf,cpf));dn=abs(np.dot(sf,np.asarray(neicc)-fc))
        row.update(weight=float(min(do,dn)/(do+dn+1e-300)),volumeRatio=float(min(ownv,neiv)/(max(ownv,neiv)+1e-300)),
                   normalNeighbourDistanceM=float(dn/np.linalg.norm(sf)))
    return row
def boundary_mesh(geo):
    obj=m.Mesh.__new__(m.Mesh);obj.points=geo.points;obj.offsets=geo.offsets;obj.vertices=geo.vertices;obj.metadata={}
    for name,body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}",(geo.poly_dir/"boundary").read_text()):
        fields=dict(re.findall(r"(\w+)\s+([^;\s]+)\s*;",body))
        if all(k in fields for k in ["startFace","nFaces","type"]):
            obj.metadata[name]={"start":int(fields["startFace"]),"count":int(fields["nFaces"]),"type":fields["type"],"fields":fields}
    return obj
def fixed_triangle_determinant_upper_bound(points):
    """Exact binary-coordinate bound for 3 selected side faces and one physical base.

    Let A be triangle area, L its longest edge, h apex height, S the sum of
    three side-face areas. det(S1,S2,S3)^2=A^4 h^4/4, S>=A and S>=h L.
    The installed determinant is 729 A^4 h^4/(32 S^6). Maximizing this
    conservative bound over all h gives 729 A^2/(32 L^4).
    No assumption about apex tangential position or source ancestry is used.
    """
    p=[[Fraction.from_float(float(x)) for x in q] for q in points]
    if len(p)!=3:raise ValueError("triangular base required")
    u=[p[1][j]-p[0][j] for j in range(3)];v=[p[2][j]-p[0][j] for j in range(3)]
    cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
    area_squared=sum(x*x for x in cross)/4
    longest_squared=max(sum((p[i][j]-p[(i+1)%3][j])**2 for j in range(3)) for i in range(3))
    if not longest_squared:raise ValueError("zero triangle edge length")
    return Fraction(729,32)*area_squared/longest_squared**2

def base_apex_candidates(geo,c):
    fs,areas,fcs,cc,v,vid,lo,hi=geo.cell(c)
    arities=[len(geo.ids(int(f))) for f in fs]
    if len(fs)==5 and len(vid)==5 and sorted(arities)==[3,3,3,3,4]:
        selected=[int(f) for f,k in zip(fs,arities) if k==4];selection="uniqueQuadrilateralPyramidBase"
    elif len(fs)==4 and len(vid)==4 and arities==[3]*4:
        physical=[int(f) for f in fs if geo.patch(int(f)) and geo.patch(int(f))["type"] not in ["cyclic","processor","processorCyclic"]]
        selected=physical if len(physical)==1 else list(map(int,fs))
        selection="uniquePhysicalTriangleAsGeometricBase" if len(physical)==1 else "allTetrahedronBasePossibilitiesNoOriginalBaseIdentity"
    else:return {"selection":"unsupportedShape","candidates":[]}
    rows=[]
    for f in selected:
        ids=geo.ids(f);base=geo.points[ids];apices=set(vid.tolist())-set(ids.tolist())
        if len(apices)!=1:raise ValueError("base does not have one opposite vertex")
        apexid=next(iter(apices));apex=geo.points[apexid]
        area,fc,*_=geo.face(f);amount=float(np.linalg.norm(area));normal=area/amount
        edges=np.roll(base,-1,axis=0)-base;lengths=np.linalg.norm(edges,axis=1)
        signed=float(np.dot(apex-fc,normal));height=abs(signed);projection=apex-signed*normal
        support=np.einsum("ij,j->i",np.cross(edges,projection-base),normal)/lengths
        inradius=2*amount/float(lengths.sum())
        tangent=float(np.linalg.norm(projection-fc))
        angles=[]
        for i,point in enumerate(base):
            left=base[(i-1)%len(base)]-point;right=base[(i+1)%len(base)]-point
            angles.append(float(np.degrees(np.arctan2(np.linalg.norm(np.cross(left,right)),np.dot(left,right)))))
        rows.append({"baseFace":f,"basePatch":geo.patch(f),"basePointIDs":ids.tolist(),"apexPointID":int(apexid),
           "baseAreaM2":amount,"basePerimeterM":float(lengths.sum()),"baseMinimumEdgeM":float(lengths.min()),"baseMaximumEdgeM":float(lengths.max()),
           "baseAreaPerimeterRadiusM":inradius,"baseMaximumEdgeToRadius":float(lengths.max()/inradius),"baseMinimumInteriorAngleDegrees":min(angles),
           "apexCoordinates":apex.tolist(),"apexNormalHeightM":height,"apexNormalHeightToBaseRadius":height/inradius,
           "apexProjectedMinimumEdgeSupportM":float(support.min()),"apexProjectionInsideBase":bool(support.min()>=0),
           "apexTangentialDistanceFromBaseCentreM":tangent,"apexTangentialDistanceToBaseRadius":tangent/inradius})
    if selection=="uniquePhysicalTriangleAsGeometricBase":
        for row in rows:
            bound=fixed_triangle_determinant_upper_bound(geo.points[geo.ids(row["baseFace"])])
            row["allPossibleApexDeterminantUpperBound"]=float(bound)
            if bound<Fraction(1,1000):
                row["certifiedAnyApexStillFails001"]=True
                row["exactDeterminantUpperBoundFraction"]=str(bound)
                row["certifiedBaseCoordinates"]=geo.points[geo.ids(row["baseFace"])].tolist()
    return {"selection":selection,"originalParentIdentityAvailable":False,"candidates":rows}

def cell_row(geo,c,detail=False):
    fs,areas,fcs,cc,v,vid,lo,hi=geo.cell(c)
    selected=[];physical=[];coupled=[];internal=0
    for f,a in zip(fs,areas):
        p=geo.patch(int(f))
        if p is None:internal+=1;selected.append(a)
        elif p["type"] in ["cyclic","processor","processorCyclic"]:coupled.append(p["name"]);selected.append(a)
        else:physical.append(p["name"])
    det,eig,tensor=determinant(selected)
    arities=sorted(len(geo.ids(int(f))) for f in fs)
    row={"cell":int(c),"faces":len(fs),"points":len(vid),"faceArities":arities,"isTetrahedron":len(fs)==4 and len(vid)==4 and arities==[3]*4,
         "V":float(v),"CC":cc.tolist(),"bbox":[lo.tolist(),hi.tolist()],"internalFaces":internal,"coupledPatches":coupled,
         "physicalPatches":physical,"selectedFaces":len(selected),"cellDeterminant":det,"selectedAreaTensorEigenvalues":eig,"baseApexGeometry":base_apex_candidates(geo,c)}
    if detail:
        row["pointIDs"]=vid.tolist();row["pointCoordinates"]=geo.points[vid].tolist()
        row["facePolygons"]=[{"face":int(f),"pointIDs":geo.ids(int(f)).tolist(),"patch":geo.patch(int(f)),
           "outwardArea":a.tolist(),"FC":fc.tolist()} for f,a,fc in zip(fs,areas,fcs)]
    return row
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mesh",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);args=ap.parse_args()
    dirs={int(p.name[9:]):p/"constant/polyMesh" for p in args.mesh.glob("processor[0-9]*") if p.name[9:].isdigit()}
    if sorted(dirs)!=list(range(len(dirs))) or len(dirs)<2:raise ValueError("actual contiguous MPI mesh ranks required")
    geos={rank:g.Geometry(p) for rank,p in sorted(dirs.items())}
    metas={rank:boundary_mesh(geo) for rank,geo in geos.items()}
    detrows=[];facerows=[];samples=[];counts={}
    for rank,geo in geos.items():
        sets={name:g.cell_set(geo.poly_dir/"sets"/name) for name in ["underdeterminedCells","lowWeightFaces","lowVolRatioFaces","skewFaces"]}
        counts[str(rank)]={name:len(v) for name,v in sets.items()}
        local=[]
        for c in sets["underdeterminedCells"]:
            row={"rank":rank,**cell_row(geo,int(c))};local.append(row);detrows.append(row)
        for row in sorted(local,key=lambda r:r["cellDeterminant"])[:2]:
            samples.append({"kind":"minimumDeterminant","rank":rank,**cell_row(geo,row["cell"],True)})
        localface=[]
        for f in sorted(set().union(*[set(v.tolist()) for name,v in sets.items() if name!="underdeterminedCells"])):
            sf,fc,*_=geo.face(f);own=int(geo.owner[f]);oc=geo.cell(own)
            patch=geo.patch(f);nc=None;nr=None;nf=None;neighbour=None
            if f<len(geo.neighbour):nr=rank;neighbour=int(geo.neighbour[f]);nc=geo.cell(neighbour)
            elif patch and patch["type"] in ["cyclic","processor","processorCyclic"]:
                nr,remote=m.counterpart(rank,patch["name"],metas)
                source=metas[rank].metadata[patch["name"]];target=metas[nr].metadata[remote]
                nf=target["start"]+f-source["start"]
                check=m.pair_check(metas[rank].face(f),metas[nr].face(nf))
                if not check["sameStartReverse"]:raise ValueError("coupled counterpart geometry failed exact audit")
                neighbour=int(geos[nr].owner[nf]);nc=geos[nr].cell(neighbour)
            row={"rank":rank,"face":f,"patch":patch,"faceArity":len(geo.ids(f)),"SF":sf.tolist(),"FC":fc.tolist(),
                 "owner":own,"ownerCC":oc[3].tolist(),"ownerV":float(oc[4]),"ownerFaces":len(oc[0]),"ownerPoints":len(oc[5]),
                 "neighbour":neighbour,"neighbourRank":nr,"remoteFace":nf,"neighbourCC":nc[3].tolist() if nc else None,
                 "neighbourV":float(nc[4]) if nc else None,"neighbourFaces":len(nc[0]) if nc else None,"neighbourPoints":len(nc[5]) if nc else None,
                 "sets":[name for name,v in sets.items() if name!="underdeterminedCells" and f in v],
                 **face_metrics(sf,fc,oc[3],oc[4],geo.points[geo.ids(f)],nc[3] if nc else None,nc[4] if nc else None)}
            facerows.append(row);localface.append(row)
        chosen={r["face"] for r in localface if "skewFaces" in r["sets"]}
        for metric in ["weight","volumeRatio"]:
            qualifying=[r for r in localface if metric in r]
            chosen.update(r["face"] for r in sorted(qualifying,key=lambda r:r[metric])[:2])
        for row in localface:
            if row["face"] not in chosen:continue
            f=row["face"];full={**row,"pointIDs":geo.ids(f).tolist(),"pointCoordinates":geo.points[geo.ids(f)].tolist(),
                             "ownerCell":cell_row(geo,row["owner"],True)}
            if row["neighbour"] is not None:full["neighbourCell"]=cell_row(geos[row["neighbourRank"]],row["neighbour"],True)
            samples.append({"kind":"faceQualitySample",**full})
        print(json.dumps({"rank":rank,"counts":counts[str(rank)],"minDet":min((r["cellDeterminant"] for r in local),default=None),
          "minWeight":min((r["weight"] for r in localface if "weight" in r),default=None),
          "minRatio":min((r["volumeRatio"] for r in localface if "volumeRatio" in r),default=None)}),flush=True)
    summary={"counts":{name:sum(v[name] for v in counts.values()) for name in next(iter(counts.values()))},
       "determinantAllFlaggedBelow001":all(r["cellDeterminant"]<.001 for r in detrows),"minimumDeterminant":min(r["cellDeterminant"] for r in detrows),
       "determinantCellShapeHistogram":dict(Counter(str((r["faces"],r["points"],r["faceArities"])) for r in detrows)),
       "determinantSelectedFaceCountHistogram":dict(Counter(r["selectedFaces"] for r in detrows)),
       "determinantPhysicalPatchIncidences":dict(Counter(p for r in detrows for p in r["physicalPatches"])),
       "faceArityHistogram":dict(Counter(r["faceArity"] for r in facerows)),
       "minimumWeight":min(r["weight"] for r in facerows if "lowWeightFaces" in r["sets"]),
       "minimumVolumeRatio":min(r["volumeRatio"] for r in facerows if "lowVolRatioFaces" in r["sets"]),
       "maximumSkewness":max(r["skewness"] for r in facerows if "skewFaces" in r["sets"]),
       "allFlaggedWeightsBelow005":all(r["weight"]<.05 for r in facerows if "lowWeightFaces" in r["sets"]),
       "allFlaggedRatiosBelow001":all(r["volumeRatio"]<.01 for r in facerows if "lowVolRatioFaces" in r["sets"])}
    unique=[r for r in detrows if len(r["baseApexGeometry"]["candidates"])==1]
    pyramid=[r for r in unique if r["baseApexGeometry"]["selection"]=="uniqueQuadrilateralPyramidBase"]
    physicaltet=[r for r in unique if r["baseApexGeometry"]["selection"]=="uniquePhysicalTriangleAsGeometricBase"]
    quantiles={}
    for name,rs in [("quadrilateralPyramidBases",pyramid),("physicalTriangleTetrahedronBases",physicaltet)]:
        metrics={}
        for metric in ["apexNormalHeightM","apexNormalHeightToBaseRadius","baseMaximumEdgeToRadius","baseMinimumInteriorAngleDegrees","apexTangentialDistanceToBaseRadius"]:
            metrics[metric]=np.quantile([r["baseApexGeometry"]["candidates"][0][metric] for r in rs],[0,.1,.5,.9,1]).tolist() if rs else []
        quantiles[name]={"cells":len(rs),"quantileProbabilities":[0,.1,.5,.9,1],"metrics":metrics,
                       "projectedApexOutsideBase":sum(not r["baseApexGeometry"]["candidates"][0]["apexProjectionInsideBase"] for r in rs)}
    summary["baseApexGeometricQuantiles"]=quantiles
    summary["certifiedFixedPhysicalTriangleAnyApexFails001"]=[
       {"rank":r["rank"],"cell":r["cell"],**r["baseApexGeometry"]["candidates"][0]}
       for r in physicaltet if r["baseApexGeometry"]["candidates"][0].get("certifiedAnyApexStillFails001")]
    summary["baseSelectionHistogram"]=dict(Counter(r["baseApexGeometry"]["selection"] for r in detrows))
    source=Path("/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/src/OpenFOAM/lnInclude")
    out={"format":"written-cone-remaining-quality-diagnostic/v1","meshPath":str(args.mesh),
         "scope":"Only flagged actual written cells/faces, recomputing installed OpenFOAM v2412 formulas; no native execution or acceptance.",
         "ancestry":{"available":False,"reason":"Native pre-decomposition parent to compact written cell trace was not exported. Shared vertex/apex geometry cannot establish ancestry."},
         "sourceSha256":{str(p):g.sha(p) for p in [Path(__file__),Path(__file__).with_name("audit_air_written_coupled_pairs.py"),
           Path(__file__).with_name("audit_air_mesh_geometry.py"),source/"primitiveMeshTools.C",source/"polyMeshTools.C"]},
         "meshSha256":{str((p/name).relative_to(args.mesh)):g.sha(p/name) for p in dirs.values() for name in ["points","faces","owner","neighbour","boundary"]},
         "inputCfdrunSha256":{p.name:g.sha(p) for p in sorted((args.mesh.parent/"model").glob("*.cfdrun"))},
         "setSha256":{str(p.relative_to(args.mesh)):g.sha(p) for d in dirs.values() for p in (d/"sets").glob("*") if p.name in next(iter(counts.values()))},
         "summary":summary,"perRankCounts":counts,"determinantCells":detrows,"qualityFaces":facerows,"samples":samples,
         "limitations":["Physical boundary Sf are excluded by installed determinant definition; this is a connectivity stencil metric.",
             "No threshold, STL, native mesh, source or parent geometry was changed.","Written coupled geometry follows reciprocal actual boundary roles before remote CC/V are used.",
             "Skew face wall-pyramid sampling is geometric evidence, not a claim of native parent history."]}
    args.output.write_text(json.dumps(out,indent=2)+"\n");print(json.dumps(summary,indent=2),flush=True)
if __name__=="__main__":main()
