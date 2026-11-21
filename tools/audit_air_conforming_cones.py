#!/usr/bin/env python3
"""Independent cone/fan geometry and canonical-order audit; never writes a mesh."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from collections import defaultdict
import numpy as np
from audit_air_mesh_geometry import Geometry, sha

def canonical_cycle(ids, points):
    rows = []
    for cycle in (list(ids), list(reversed(ids))):
        for k in range(len(cycle)):
            seq = cycle[k:] + cycle[:k]
            rows.append((tuple(tuple(points[i]) for i in seq), seq))
    return min(rows, key=lambda v:v[0])[1]

def edge_keys(cycle):
    return [tuple(sorted((cycle[j], cycle[(j+1)%len(cycle)]))) for j in range(len(cycle))]

def cell_geometry(cell):
    points = {}
    for f in cell["faces"]:
        for node, coord in zip(f["vertexIds"], f["points"]):
            assert node not in points or points[node] == coord
            points[node] = coord
    ids = sorted(points); lookup = {n:i for i,n in enumerate(ids)}
    polygons = []
    for f in cell["faces"]:
        seq = list(f["vertexIds"])
        fg = Geometry.from_arrays(f["points"], [list(range(len(seq)))])
        if np.dot(fg.face(0)[0], f["outwardAreaVector"]) < 0:
            seq.reverse()
        polygons.append([lookup[n] for n in seq])
    return Geometry.from_arrays([points[n] for n in ids], polygons)

def point_map(cells):
    result = {}
    for cell in cells:
        for face in cell["faces"]:
            for node, coord in zip(face["vertexIds"], face["points"]):
                assert node not in result or np.asarray(result[node]).tobytes() == np.asarray(coord).tobytes()
                result[node] = coord
    return result

def analyse(data):
    before = {c["cell"]:c for c in data["before"]["cells"]}
    after = {c["cell"]:c for c in data["after"]["cells"]}
    old = point_map(before.values()); new = point_map(after.values())
    unchanged = all(n in new and np.asarray(x).tobytes() == np.asarray(new[n]).tobytes() for n,x in old.items())
    parents = data["construction"]["outputParentCell"]
    marked = set(data["construction"]["markedCells"])
    groups = defaultdict(list)
    child_geometry = {}
    for node, child in after.items():
        g = cell_geometry(child); child_geometry[node] = g
        groups[parents[node]].append(node)
    parent_results = []
    for parent in sorted(marked):
        g = cell_geometry(before[parent])
        record = next(a for a in data["construction"]["apices"] if a["parent"] == parent)
        apex = np.asarray(record["coordinates"])
        fs, areas, centres, _, volume, vertices, *_ = g.cell(0)
        margins = np.einsum("ij,ij->i", areas, centres-apex)/np.linalg.norm(areas,axis=1)
        normal = areas/np.linalg.norm(areas,axis=1)[:,None]
        outside = np.max(np.einsum("vfi,fi->vf",g.points[vertices,None,:]-centres[None,:,:],normal))
        child_volume = sum(child_geometry[n].cell(0)[4] for n in groups[parent])
        parent_results.append({"parent":parent, "childCount":len(groups[parent]),
            "minimumApexKernelMarginM":float(margins.min()),
            "apexEqualsRecordedOriginalOFCentre":bool(np.array_equal(apex,np.asarray(before[parent]["centre"]))),
            "maximumOriginalVertexOutsideBaseHalfspaceM":float(outside),
            "originalVolumeM3":volume, "childVolumeM3":child_volume,
            "relativeVolumeError":abs(child_volume-volume)/volume})
    fans = []; old_faces = {}
    for cell in before.values():
        for f in cell["faces"]:
            if f["face"] not in old_faces or f["neighbourCell"] is None:
                old_faces[f["face"]] = f
    # Source fan owner is explicit; retrieve outward owner face rather than an arbitrary neighbour copy.
    for source in data["construction"]["splitSourceFaces"]:
        owner = before[source["owner"]]
        original = next(f for f in owner["faces"] if f["face"] == source["face"])
        sf = np.asarray(original["outwardAreaVector"]); total = np.zeros(3); moment = np.zeros(3); amount = 0
        edge_set = []
        minimum_triangle_area = float("inf")
        for ids in source["pieces"]:
            assert len(ids) == 3 and source["centrePoint"] in ids
            coords = np.asarray([new[n] for n in ids])
            area = .5*np.cross(coords[1]-coords[0],coords[2]-coords[0])
            assert np.dot(area,sf) > 0
            mag = np.linalg.norm(area); total += area; amount += mag; moment += mag*coords.mean(axis=0)
            minimum_triangle_area = min(minimum_triangle_area,float(mag))
            edge_set.append(tuple(sorted(n for n in ids if n != source["centrePoint"])))
        raw_ids = original["vertexIds"]; expected = set(edge_keys(raw_ids))
        coords = np.asarray(source["canonicalCoordinates"])
        cycle = canonical_cycle(list(range(len(coords))),coords)
        canonical = tuple(tuple(coords[n]) for n in cycle)
        centre = Geometry.from_arrays(coords,[cycle]).face(0)[1]
        variants = []
        for sequence in (list(range(len(coords))),list(reversed(range(len(coords))))):
            for j in range(len(coords)):
                selected = canonical_cycle(sequence[j:]+sequence[:j],coords)
                variants.append(tuple(tuple(coords[n]) for n in selected) == canonical)
        fans.append({"sourceFace":source["face"],"triangleCount":len(edge_set),
                     "eachOriginalBoundaryEdgeUsedExactlyOnce":set(edge_set)==expected and len(edge_set)==len(expected),
                     "minimumTriangleAreaM2":minimum_triangle_area,
                     "relativeAreaVectorError":float(np.linalg.norm(total-sf)/np.linalg.norm(sf)),
                     "relativeUnsignedAreaError":abs(float(amount-np.linalg.norm(sf)))/np.linalg.norm(sf),
                     "maximumCentreDifferenceM":float(np.max(np.abs(moment/amount-centre))),
                     "addedCentreMatchesCanonicalOFCentreBits":np.asarray(new[source["centrePoint"]]).tobytes()==centre.tobytes(),
                     "allCycleRotationReversalVariantsSame":all(variants)})
    # Independently match boundary triangles to the original convex planar parent polygons.
    boundary_area = defaultdict(float); old_boundary = []
    for parent,cell in before.items():
        for face in cell["faces"]:
            if face["neighbourCell"] is None:
                old_boundary.append((parent,face))
    count = 0
    for node,cell in after.items():
        for face in cell["faces"]:
            if face["neighbourCell"] is not None: continue
            count += 1
            q = np.asarray(face["points"]); candidates = []
            for parent,original in old_boundary:
                if parent != parents[node]: continue
                poly = np.asarray(original["points"]); n = np.asarray(original["outwardAreaVector"]); n /= np.linalg.norm(n)
                if np.max(np.abs((q-poly[0])@n)) > 1e-12: continue
                inside = True
                for j,a in enumerate(poly):
                    edge = poly[(j+1)%len(poly)]-a
                    # Original raw winding can be neighbour-oriented; choose orientation from outward area.
                    raw = Geometry.from_arrays(poly,[list(range(len(poly)))]).face(0)[0]
                    sign = 1 if np.dot(raw,n)>0 else -1
                    if min(sign*np.dot(np.cross(edge,p-a),n)/np.linalg.norm(edge) for p in q) < -1e-12:
                        inside = False; break
                if inside: candidates.append(original)
            assert len(candidates) == 1, "Boundary piece not uniquely contained in original parent polygon"
            boundary_area[candidates[0]["face"]] += np.linalg.norm(face["outwardAreaVector"])
    boundary_errors = [abs(boundary_area[f["face"]]-np.linalg.norm(f["outwardAreaVector"]))/np.linalg.norm(f["outwardAreaVector"]) for _,f in old_boundary]
    # Actual pentagon exhibits ordinal hazards; sorted unordered edge keys eliminate outward reversal dependence.
    coords = np.asarray(data["construction"]["splitSourceFaces"][0]["canonicalCoordinates"])
    cycle = canonical_cycle(list(range(len(coords))),coords)
    reverse = list(reversed(cycle))
    rotation = np.array([[0,-1,0],[1,0,0],[0,0,1]])
    rotated_cycle = canonical_cycle(list(range(len(coords))),coords@rotation.T)
    return {"originalPoints":len(old),"outputPoints":len(new),"originalCoordinatesBitIdentical":unchanged,
            "markedParents":len(marked),"unmarkedParents":len(before)-len(marked),
            "outputCells":len(after),"parentKernelAndVolumes":parent_results,
            "fanBases":fans,"allFanBoundaryEdgesCoveredOnce":all(f["eachOriginalBoundaryEdgeUsedExactlyOnce"] for f in fans),
            "allCanonicalCycleVariantsIdentical":all(f["allCycleRotationReversalVariantsSame"] for f in fans),
            "allAddedFaceCentreBitsReconstructed":all(f["addedCentreMatchesCanonicalOFCentreBits"] for f in fans),
            "minimumChildVolumeM3":min(g.cell(0)[4] for g in child_geometry.values()),
            "sumChildVolumeM3":sum(g.cell(0)[4] for g in child_geometry.values()),
            "originalBoundaryFaces":len(old_boundary),"outputBoundaryFaces":count,
            "originalBoundaryAreaM2":sum(np.linalg.norm(f["outwardAreaVector"]) for _,f in old_boundary),
            "outputBoundaryAreaM2":sum(boundary_area.values()),
            "maximumParentBoundaryPolygonAreaRelativeError":max(boundary_errors),
            "canonicalCounterexample":{"actualSourceFace":data["construction"]["splitSourceFaces"][0]["face"],
               "originalCoordinates":coords.tolist(),"positiveOutwardFanEdgeOrdinal":edge_keys(cycle),
               "negativeOutwardFanEdgeOrdinal":edge_keys(reverse),"localOrdinalPairingValid":edge_keys(cycle)==edge_keys(reverse),
               "commonUnorderedEdgeKeySetEqual":sorted(edge_keys(cycle))==sorted(edge_keys(reverse)),
               "rotation90CanonicalCycle":rotated_cycle,"originalCanonicalCycle":cycle,
               "rotation90KeepsCanonicalStart":cycle==rotated_cycle},
            "limits":["Kernel and partition proof requires closed non-self-intersecting cell surface, planar convex base polygons and positive-volume children.",
                      "Single walls patch and no zones in fixture; label/zone preservation of real cases remains a native gate.",
                      "Existing 2-rank check follows global construction then stock decomposePar; it does not verify independent per-rank fan generation.",
                      "Rotational cyclic needs a common transformed frame or exact vertex correspondence; AMI/mapped/transformed unsupported pairs must fail closed.",
                      "No native mesh, binary, checker, solver, point or physical outline is mutated by this audit. No product-case acceptance."]}


def self_touching_fan_counterexample():
    # All coordinates are integers. Proper-crossing-only ignores p2 on edge p5->p0.
    p = np.array([[0,0,0],[1,0,0],[-1,1,0],[-1,-1,0],[2,0,0],[-2,2,0]],float)
    area, centre, *_ = Geometry.from_arrays(p,[list(range(6))]).face(0)
    normal = area/np.linalg.norm(area)
    proper = []
    for i in range(6):
        for j in range(i+1,6):
            ii=(i+1)%6; jj=(j+1)%6
            if ii==j or jj==i: continue
            a,b=p[i],p[ii]; c,d=p[j],p[jj]
            s1=np.dot(np.cross(b-a,c-a),normal); s2=np.dot(np.cross(b-a,d-a),normal)
            t1=np.dot(np.cross(d-c,a-c),normal); t2=np.dot(np.cross(d-c,b-c),normal)
            if s1*s2 < 0 and t1*t2 < 0: proper.append((i,j))
    triangles=[]; moment=np.zeros(3); amount=0
    for j in range(1,5):
        value=float(.5*np.dot(np.cross(p[j]-p[0],p[j+1]-p[0]),normal))
        triangles.append(value); amount+=value; moment+=value*(p[0]+p[j]+p[j+1])/3
    return {"scope":"Base-only counterexample to proper-crossing-only/simple-positive-anchor-fan sufficiency. No claim that a complete native cell passes every other gate.",
            "points":p.tolist(),"properCrossings":proper,"anchorZeroTriangleAreasM2":triangles,
            "OFareaM2":float(np.linalg.norm(area)), "fanAreaM2":amount,
            "OFcentre":centre.tolist(),"fanCentre":(moment/amount).tolist(),
            "maximumCentreDifferenceM":float(np.max(np.abs(moment/amount-centre))),
            "selfTouch":{"point":2,"nonadjacentEdge":[5,0],
               "exactCollinearityCross":np.cross(p[2]-p[5],p[0]-p[5]).tolist(),
               "strictlyBetweenEndpointDot":float(np.dot(p[2]-p[5],p[2]-p[0]))},
            "overlapProof":"Triangle [p0,p1,p2] is contained in [p0,p4,p5]: p1=.5*p4 and p2=.5*p5; it has positive area .5m2 and is counted twice."}


def main():
    p = argparse.ArgumentParser(); p.add_argument("--input",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    p.add_argument("--native-receipt",type=Path);p.add_argument("--fixture",type=Path)
    a=p.parse_args(); data=json.loads(a.input.read_text())
    out={"format":"conforming-cone-independent-audit/v1","inputSha256":sha(a.input),
       "sourceSha256":sha(Path(__file__)),"geometryAuditSourceSha256":sha(Path(__file__).with_name("audit_air_mesh_geometry.py")),
       "results":analyse(data),"properCrossingOnlyCounterexample":self_touching_fan_counterexample()}
    if a.native_receipt is not None:
        if a.fixture is None: raise ValueError("Fixture path required for native receipt hash verification")
        receipt=json.loads(a.native_receipt.read_text())
        files={name:{"actualSha256":sha(a.fixture/name),"expectedSha256":expected,"matches":sha(a.fixture/name)==expected}
               for name,expected in receipt["fixtureFiles"].items()}
        out["nativeReceiptFileHashVerification"]={"receiptSha256":sha(a.native_receipt),"files":files,"allMatch":all(x["matches"] for x in files.values()),
          "scope":"Read existing files only. Full checkMesh was performed by the original diagnostic; this audit performs zero native executions."}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
if __name__ == "__main__":main()
