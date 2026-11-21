#!/usr/bin/env python3
"""Read-only serial roof witness audit; partial stars never imply full acceptance."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
from audit_air_native_roof_components import audit, proposals, geometry, sha
from audit_air_mesh_geometry import Geometry

def local_trial(row):
    """A full displacement field permits a rejection witness on complete local cells."""
    prop = proposals(row)
    result = {
        "allProposalPacketsExported": prop["allPointProposalPacketsExported"],
        "allStarCellsExported": sum(c["belongsToFullStar"] for c in row["cells"]) == row["totalStarCells"],
        "wholeComponentQualityAccepted": False,
        "scope": "A local failure is sufficient to reject this displacement. Partial cells, missing one-ring neighbours and uncomputed quality gates cannot establish whole-component acceptance.",
    }
    point_deltas_complete = bool(row["points"]) and all(
        p.get("deltaFromCompleteComponent") is True and "finalProposalDelta" in p
        for p in row["points"]
    )
    result["allExportedPointsHaveCompleteComponentDelta"] = point_deltas_complete
    if not prop["allPointProposalPacketsExported"] and not point_deltas_complete:
        result["notEvaluated"] = "Missing packets can move visible cell points; applying incomplete deltas could create a false geometry witness."
        return result
    if row.get("exactConflictPoints", 0) > 0 or prop["visibleExactConflictPointIds"] or prop["missingProposalPointRecordIds"]:
        result["notEvaluated"] = "Conflicting displacement constraints (including source-reported conflicts outside the sample) or missing point records."
        return result
    if point_deltas_complete:
        moves = {}
        for p in row["points"]:
            delta = np.asarray(p["finalProposalDelta"], dtype=float)
            if delta.shape != (3,) or not np.all(np.isfinite(delta)):
                raise ValueError("Invalid complete-component point delta")
            if p["point"] in prop["byPoint"]:
                expected = np.asarray(prop["byPoint"][p["point"]][0]["delta"])
                if not np.array_equal(delta, expected):
                    raise ValueError("Complete-component delta disagrees with visible proposal")
            if np.dot(delta, delta) > 0:
                moves[p["point"]] = delta
        result["displacementSource"] = "Native complete-component final delta for every exported point; not inferred from omitted packet proposals."
    else:
        moves = {n: np.asarray(prop["byPoint"][n][0]["delta"]) for n in prop["visibleMovingPointIds"]}
        result["displacementSource"] = "All packet proposals independently reconstructed."
    result["exportedOriginalGeometry"] = geometry(row, {})
    trial = geometry(row, moves)
    result["exportedLocalTrial"] = trial
    original = {p["point"]: p["original"] for p in row["points"]}
    faces = {f["face"]: f for f in row["faces"]}
    witnesses = []
    seen = set()
    for c in trial["cells"]:
        for f in c["nonplanarFaces"]:
            if f["nativeFace"] in seen:
                continue
            seen.add(f["nativeFace"])
            data = dict(f, nativeCell=c["nativeCell"], planarityToleranceM=c["planarityToleranceM"])
            data["nativePointIds"] = faces[f["nativeFace"]]["points"]
            data["originalPoints"] = [original[n] for n in data["nativePointIds"]]
            data["exactDeltaM"] = [moves.get(n, np.zeros(3)).tolist() for n in data["nativePointIds"]]
            data["points"] = [(np.asarray(original[n]) + moves.get(n, np.zeros(3))).tolist() for n in data["nativePointIds"]]
            if len(data["points"]) == 4:
                x = np.asarray(data["points"])
                data["signedQuadScalarTripleM3"] = float(np.dot(x[1]-x[0], np.cross(x[2]-x[0], x[3]-x[0])))
            witnesses.append(data)
    result["independentLocalPlanarityRejectionWitnesses"] = witnesses
    result["localPlanarityRejectionEstablished"] = bool(witnesses)
    return result


def native_witness(row):
    """Reconstruct the exact native trigger from its complete exported topology."""
    witness = row.get("witness")
    if witness is None:
        return {"notEvaluated": "No native trigger IDs in this initial export."}
    points = {p["point"]: p for p in row["points"]}
    if row["vetoReason"] == 0:
        values = row.get("conflictWitnessProposals", [])
        node = witness["point"]
        if node not in points or len(values) < 2 or any(q["point"] != node for q in values):
            return {"notEvaluated": "Incomplete exact conflict witness."}
        first = values[0]
        conflict = any(
            q["delta"] != first["delta"] or
            (np.dot(q["delta"], q["delta"]) > 0 and np.dot(first["delta"], first["delta"]) > 0 and
             any(q[k] != first[k] for k in ["axis", "sign", "plane"]))
            for q in values[1:]
        )
        return {"nativeTrigger": witness, "originalPoint": points[node]["original"],
                "protectedMask": points[node]["protectedMask"], "allRecordedConflictProposals": values,
                "exactConflictReproduced": conflict,
                "scope": "This is one sufficient point conflict, not all global conflicts."}
    if not all(p.get("deltaFromCompleteComponent") is True and "finalProposalDelta" in p for p in row["points"]):
        return {"notEvaluated": "No complete-component final delta for every exported point."}
    cells = {c["cell"]: c for c in row["cells"]}
    faces = {f["face"]: f for f in row["faces"]}
    cell = cells.get(witness["cell"])
    if cell is None:
        return {"notEvaluated": "Native trigger cell topology missing."}
    fs = [faces[f] for f in cell["faces"]]
    ids = sorted({n for f in fs for n in f["points"]})
    lookup = {n:i for i,n in enumerate(ids)}
    polygons = [[lookup[n] for n in (f["points"] if f["owner"] == cell["cell"] else f["points"][::-1])] for f in fs]
    values = {}
    for state in ["original", "trial"]:
        coords = [np.asarray(points[n]["original"]) +
                  (np.asarray(points[n]["finalProposalDelta"]) if state == "trial" else np.zeros(3)) for n in ids]
        g = Geometry.from_arrays(coords, polygons)
        fidx = cell["faces"].index(witness["face"])
        area, centre, plane, *_ = g.face(fidx)
        normal = area / np.linalg.norm(area)
        cell_span = max(float(np.linalg.norm(g.cell(0)[7]-g.cell(0)[6])), 1e-6)
        data = {"faceCentre": centre.tolist(), "outwardUnitNormal": normal.tolist(),
                "facePlanarityErrorM": plane, "nativePlanarityToleranceM": 1e-10*cell_span,
                "cellClassification": g.classify(0), "cellVolumeM3": g.cell(0)[4]}
        if witness["point"] >= 0:
            node = witness["point"]
            data["triggerPointNormalOffsetM"] = float(np.dot(np.asarray(coords[lookup[node]])-centre, normal))
        if witness["otherFace"] >= 0:
            hidx = cell["faces"].index(witness["otherFace"])
            other_area, other_centre, *_ = g.face(hidx)
            delta = other_centre-centre
            cosine = float(np.dot(delta, normal)/np.linalg.norm(delta))
            data.update(otherFaceCentre=other_centre.tolist(),
                        otherOutwardUnitNormal=(other_area/np.linalg.norm(other_area)).tolist(),
                        faceToOtherCentreCosine=cosine,
                        nativeStrictCentreGateRejected=cosine >= -1.0001e-6)
        values[state] = data
    if row["vetoReason"] == 3:
        reproduced = abs(values["trial"].get("triggerPointNormalOffsetM", 0)) > values["trial"]["nativePlanarityToleranceM"]
    elif row["vetoReason"] == 6:
        reproduced = values["trial"].get("nativeStrictCentreGateRejected", False)
    else:
        reproduced = False
    source_face_ids = [witness["face"]] + ([witness["otherFace"]] if witness["otherFace"] >= 0 else [])
    face_points = sorted({n for f in source_face_ids for n in faces[f]["points"]})
    return {"nativeTrigger": witness, "triggerCellBelongsToFullStar": cell["belongsToFullStar"],
            "triggerFaceIds": source_face_ids,
            "triggerPointCoordinatesAndCompleteComponentDeltas": [points[n] for n in face_points],
            "originalAndTrial": values, "exactNativeGateFailureReproduced": reproduced,
            "scope": "One exact native rejection witness from full per-point deltas and complete trigger-cell faces. Other component cells and later gates are not assumed checked."}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    records = []
    for i, line in enumerate(a.input.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        records.append({"inputLine": i, "nativeRecordAudit": audit(row), "completeProposalLocalTrial": local_trial(row), "exactNativeTriggerAudit": native_witness(row)})
    a.output.write_text(json.dumps({
        "format": "native-roof-serial-independent-audit/v1",
        "scope": "Only original pre-write cutMesh witness IDs/coordinates. No native C++/mesh/binary mutation or solver/checker execution. Serial native meshing is an API diagnostic, not a serial solver run or product acceptance.",
        "inputSha256": sha(a.input), "sourceSha256": sha(Path(__file__)),
        "sharedAuditSourceSha256": sha(Path(__file__).with_name("audit_air_native_roof_components.py")),
        "records": records,
    }, indent=2) + "\n")

if __name__ == "__main__":
    main()
