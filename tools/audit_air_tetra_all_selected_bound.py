#!/usr/bin/env python3
"""Any-apex necessary bounds for the installed 3D tetrahedron determinant branches.

Read-only actual written mesh/trace audit. All four selected faces use 128 A²/P⁴;
one noncoupled physical base and three selected sides use 54 A²/P⁴. A bound above
the quality threshold is never a feasibility certificate.
"""
from __future__ import annotations
import argparse
from collections import Counter
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import numpy as np

def load_ancestry():
    path=Path(__file__).with_name("audit_air_refan_remaining_ancestry.py")
    spec=importlib.util.spec_from_file_location("ancestry_bound",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

M=load_ancestry()
COUPLED_TYPES=frozenset(("processor","cyclic","processorCyclic"))

def fixed_triangle_bound(points,selected_faces):
    """Exact binary-input necessary bound for the actual tetrahedron mask."""
    if selected_faces not in (3,4):
        raise ValueError("only installed 3-side or all-4 tetrahedron masks supported")
    physical=M.triangle_strong_bound(points)
    constant=54 if selected_faces==3 else 128
    bound=Fraction(physical["exactUpperBoundFraction"])*Fraction(constant,54)
    return {"selectedFaceCount":selected_faces,"boundConstant":constant,
            "bound":float(bound),"exactUpperBoundFraction":str(bound),
            "perimeterLowerBoundFraction":physical["perimeterLowerBoundFraction"],
            "perimeterSqrtPrecisionBits":physical["perimeterSqrtPrecisionBits"],
            "edgeLengthLowerFractions":physical["edgeLengthLowerFractions"],
            "edgeLengthSquaredExactFractions":physical["edgeLengthSquaredExactFractions"],
            "necessaryFails001":bound<Fraction.from_float(.001),
            "necessaryOnlyNotFeasibility":True}

def selected_cell_faces(geo,faces):
    result=[]
    for f in map(int,faces):
        patch=geo.patch(f) if f>=len(geo.neighbour) else None
        if f<len(geo.neighbour) or (patch and patch["type"] in COUPLED_TYPES):
            result.append(f)
        elif patch and patch["type"] not in ("wall","patch","empty","symmetry","symmetryPlane"):
            raise ValueError(f"unclassified installed coupled-mask patch type {patch['type']}")
    return result

def tetrahedron_qualification(geo,cell):
    raw=geo.cell(cell);faces=raw[0]
    if len(faces)!=4 or len(raw[5])!=4 or any(len(geo.ids(int(f)))!=3 for f in faces):
        return None
    selected=selected_cell_faces(geo,faces)
    if len(selected) not in (3,4):
        return None
    physical=[int(f) for f in faces if int(f) not in selected]
    if len(selected)==3 and len(physical)!=1:
        raise ValueError("selected-three mask must have exactly one physical face")
    return raw,selected,physical

def audit(mesh):
    mesh=Path(mesh)
    ranks=sorted(int(p.name[9:]) for p in mesh.glob("processor[0-9]*") if p.name[9:].isdigit())
    if len(ranks)<2 or ranks!=list(range(len(ranks))):
        raise ValueError("actual contiguous multi-rank mesh required")
    counts=Counter();rows=[];identity={}
    for rank in ranks:
        geo=M.g.Geometry(mesh/f"processor{rank}/constant/polyMesh")
        trace=M.read_trace(mesh,rank)
        for field in ("patch","zone"):
            path=mesh/f"conforming-source-rank{rank}-{field}.i32"
            if path.stat().st_size!=4*trace["sourceManifest"]["faces"]:
                raise ValueError("source patch/zone metadata size mismatch")
            trace[field]=np.memmap(path,dtype="<i4",mode="r")
            trace["identities"][path.name]=M.sha(path)
        bad=M.g.cell_set(geo.poly_dir/"sets/underdeterminedCells")
        identity[str(rank)]={"written":{name:M.sha(geo.poly_dir/name) for name in ("points","faces","owner","neighbour","boundary","sets/underdeterminedCells")},
                             "trace":trace["identities"]}
        counts["actualDCells"]+=len(bad)
        for c in map(int,bad):
            qualification=tetrahedron_qualification(geo,c)
            if qualification is None:
                counts["notQualifiedSelected3Or4Tetrahedron"]+=1;continue
            raw,selected,physical=qualification
            counts[f"selected{len(selected)}Tetrahedra"]+=1
            bases=[int(f) for f in raw[0] if int(trace["faceBase"][f])>=0]
            if len(selected)==3:
                # Only its noncoupled physical triangle is the fixed-base oracle.
                bases=physical
            if len(bases)!=1:
                counts["ambiguousOriginalBase"]+=1;continue
            f=bases[0];base=int(trace["faceBase"][f]);parent=int(trace["cellParent"][c])
            if not 0<=base<trace["sourceManifest"]["faces"]:
                raise ValueError("actual tetrahedron has invalid source base")
            ids=trace["vertices"][trace["offsets"][base]:trace["offsets"][base+1]]
            coords=geo.points[geo.ids(f)]
            cert=fixed_triangle_bound(coords,len(selected))
            actual=M.q.determinant([geo.face(ff)[0] for ff in selected])[0]
            row={"rank":rank,"cell":c,"sourceParent":parent,"sourceBase":base,
                 "sourceBaseArity":len(ids),"sourceBasePointIds":ids.tolist(),
                 "sourceBaseCoordinates":trace["points"][ids].tolist(),
                 "sourceBasePatch":int(trace["patch"][base]),"sourceBaseZone":int(trace["zone"][base]),
                 "sourceBaseOwner":int(trace["owner"][base]),"sourceBaseNeighbour":int(trace["neighbour"][base]),
                 "writtenBaseFace":f,"writtenBasePointIds":geo.ids(f).tolist(),"writtenBaseCoordinates":coords.tolist(),
                 "actualSelectedFaces":selected,"physicalFaces":physical,"actualWrittenDeterminant":actual,**cert}
            rows.append(row)
            if cert["necessaryFails001"]:
                counts[f"selected{len(selected)}CertifiedImpossible"]+=1
                counts[f"certifiedSourceArity{len(ids)}"]+=1
        print(json.dumps({"rank":rank,"actualDCells":len(bad),"cumulative":dict(counts)}),flush=True)
    exclusions=[row for row in rows if row["necessaryFails001"]]
    return {"schema":"actual-tetra-selected-mask-any-apex-bound-v1","meshPath":str(mesh),
            "counts":dict(counts),"rows":rows,"certifiedExclusions":exclusions,
            "certifiedOriginalSourceFaces":sorted({(r["rank"],r["sourceBase"],r["sourceBaseArity"]) for r in exclusions}),
            "identity":identity,
            "scope":["Actual 3D installed selected-mask qualification on written diagnostic tetrahedra only.",
                     "128 bound assumes all four tetrahedron faces selected; 54 assumes exactly three sides selected with one physical base.",
                     "Any-apex exclusion is about the fixed triangle tetrahedron, not arbitrary polyhedra or the whole target.",
                     "Original sourceface arity/provenance identifies possible preserving-outline refan; no native topology change performed.",
                     "No cell labels are reused from an older topology."],
            "nativeStartedByAudit":False,"noMeshOrThresholdMutation":True}

def audit_original_outline_refan(proof):
    """Repartition actual original outlines using the all-four necessary gate."""
    proof=Path(proof);doc=json.loads(proof.read_text());groups={}
    for row in doc["certifiedExclusions"]:
        if row["selectedFaceCount"]!=4:
            continue
        groups.setdefault((row["rank"],row["sourceBase"]),[]).append(row)
    rows=[]
    for (rank,base),bad in sorted(groups.items()):
        ref=bad[0];ids=ref["sourceBasePointIds"];points=dict(zip(ids,ref["sourceBaseCoordinates"]))
        enum=M.general_refan_polygon(ids,points,len(ids)-2,physical_base=False)
        eligible=[]
        for candidate in enum["candidates"]:
            if not candidate["eligibleNecessaryOnly"]:
                continue
            bounds=[fixed_triangle_bound([points[i] for i in piece],4)
                    for piece in candidate["bases"] if len(piece)==3]
            if any(bound["necessaryFails001"] for bound in bounds):
                continue
            eligible.append({"partition":candidate,"selected4TriangleNecessaryBounds":bounds})
        eligible.sort(key=lambda row:(row["partition"]["minimumAreaPerimeterShape"],
                      row["partition"]["minimumWithinBaseWeight"],row["partition"]["minimumWithinBaseRatio"],
                      -len(row["partition"]["bases"])),reverse=True)
        rows.append({"rank":rank,"sourceBase":base,"sourcePointLabels":ids,"sourceCoordinates":ref["sourceBaseCoordinates"],
                     "actualCertifiedWrittenCells":[row["cell"] for row in bad],
                     "sourceParents":[ref["sourceBaseOwner"],ref["sourceBaseNeighbour"]],
                     "generalTriangulation":enum,"strong128EligibleCandidates":eligible,
                     "selected":eligible[0] if eligible else None,
                     "noActualParentApexProfileOrWholeParentAcceptance":True})
    return {"schema":"all-selected128-original-outline-refan-candidates-v1",
            "inputBoundAuditSHA256":M.sha(proof),"originalSourceFaces":len(rows),
            "withNecessaryEligibleRefan":sum(row["selected"] is not None for row in rows),"bases":rows,
            "scope":"128 any-apex necessary filter plus within-base W/R and signed outline area/moment; nontriangle D and entire actual both-parent geometry still required"}

def shared_apex_from_children(point_sets,native_points,original_point_count):
    """Prove the unique new point common to every written child of one source parent."""
    if len(point_sets)<2:
        raise ValueError("more than one child is required to identify a common apex")
    common=set(map(int,point_sets[0]))
    for points in point_sets[1:]:
        common.intersection_update(map(int,points))
    if len(common)!=1:
        raise ValueError("child incidence does not determine one unique apex")
    apex=next(iter(common))
    if not 0<=apex<len(native_points) or int(native_points[apex])<original_point_count:
        raise ValueError("common point is not a newly inserted source-parent apex")
    return apex

def audit_written_apex(mesh,proof):
    mesh=Path(mesh);proof=Path(proof);prior=json.loads(proof.read_text())
    ranks=sorted({row["rank"] for row in prior["bases"]});rows=[];identity={}
    for rank in ranks:
        geo=M.g.Geometry(mesh/f"processor{rank}/constant/polyMesh");trace=M.read_trace(mesh,rank)
        path=mesh/f"conforming-written-rank{rank}-point-native.i32"
        if path.stat().st_size!=4*len(geo.points):
            raise ValueError("written point map size mismatch")
        native_points=np.memmap(path,dtype="<i4",mode="r")
        witnesses=json.loads((mesh/f"conforming-cone-partition-rank{rank}.json").read_text())
        identity[str(rank)]={"pointMapSHA256":M.sha(path),"trace":trace["identities"],
                            "written":{name:M.sha(geo.poly_dir/name) for name in ("points","faces","owner","neighbour","boundary")}}
        for old in [row for row in prior["bases"] if row["rank"]==rank]:
            base=old["sourceBase"];witness=next(row for row in witnesses if row["face"]==base)
            for parent in [witness["owner"]]+([witness["neighbour"]] if witness["neighbour"]>=0 else []):
                children=np.flatnonzero(trace["cellParent"]==parent).tolist()
                raw_children={c:geo.cell(c) for c in children}
                apex=shared_apex_from_children([raw_children[c][5] for c in children],
                                               native_points,trace["sourceManifest"]["points"])
                input_apex=witness["ownerApex"] if parent==witness["owner"] else witness["neighbourApex"]
                base_children=[]
                for c,raw in raw_children.items():
                    bases=[int(f) for f in raw[0] if int(trace["faceBase"][f])==base]
                    if not bases:
                        continue
                    if len(bases)!=1:
                        raise ValueError("ambiguous written cell original base")
                    selected=selected_cell_faces(geo,raw[0])
                    determinant=M.q.determinant([geo.face(f)[0] for f in selected])[0]
                    base_children.append({"cell":c,"baseFace":bases[0],"baseCoordinates":geo.points[geo.ids(bases[0])].tolist(),
                                          "selectedFaces":selected,"actualWrittenDeterminant":determinant,
                                          "actualWrittenCentre":raw[3].tolist(),"actualWrittenVolume":raw[4]})
                if not base_children:
                    raise ValueError("source face has no written child")
                rows.append({"rank":rank,"sourceParent":parent,"sourceBase":base,"allWrittenChildren":children,
                             "writtenApexPoint":apex,"writtenNativePoint":int(native_points[apex]),
                             "finalWrittenApex":geo.points[apex].tolist(),"witnessInputApex":input_apex,
                             "writtenApexExactlyMatchesInputWitness":bool(np.array_equal(geo.points[apex],input_apex)),
                             "baseChildren":base_children,
                             "minimumActualWrittenBaseChildDeterminant":min(row["actualWrittenDeterminant"] for row in base_children),
                             "proof":"unique newly inserted point common to ALL current written children, using current exact cell-parent and point-native maps"})
    return {"schema":"actual-written-common-apex-proof-v1","meshPath":str(mesh),"inputPureProofSHA256":M.sha(proof),
            "writtenParentCount":len(rows),"parents":rows,"identity":identity,
            "scope":"specific final written AP and actual new-topology child determinant proof; other original bases/full-parent quality omitted",
            "noOldCellLabelReuse":True,"nativeStartedByAudit":False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--refan-from",type=Path)
    parser.add_argument("--written-apex-from",type=Path)
    args=parser.parse_args()
    if args.written_apex_from:
        if not args.mesh or args.refan_from:
            parser.error("--written-apex-from requires --mesh and excludes --refan-from")
        result=audit_written_apex(args.mesh,args.written_apex_from)
    else:
        if bool(args.mesh)==bool(args.refan_from):
            parser.error("choose exactly one of --mesh or --refan-from")
        result=audit_original_outline_refan(args.refan_from) if args.refan_from else audit(args.mesh)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result.get("counts",{"originalSourceFaces":result.get("originalSourceFaces"),"withNecessaryEligibleRefan":result.get("withNecessaryEligibleRefan")})),flush=True)

if __name__=="__main__":main()
