#!/usr/bin/env python3
"""Read-only identity, comparable-set and boundary-point audit of MPI meshes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend.aircooling.runner.polymesh_io import read_all_faces, read_points, read_label_list

CORE = ("points", "faces", "owner", "neighbour", "boundary", "pointZones", "faceZones", "cellZones")
SETS = ("underdeterminedCells", "lowWeightFaces", "lowVolRatioFaces", "skewFaces", "nonOrthoFaces")
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def labels(path):
    if not path.exists():
        return set()
    raw = path.read_bytes()
    hit = re.search(rb"\n(\d+)\s*\(\s*([0-9\s]*)\)", raw)
    if not hit:
        raise ValueError(f"unsupported set payload: {path}")
    values = list(map(int, hit[2].split()))
    if len(values) != int(hit[1]):
        raise ValueError(f"set size mismatch: {path}")
    return set(values)

def audit(before, after, proof):
    processors = sorted(before.glob("processor[0-9]*"), key=lambda p: int(p.name[9:]))
    if [p.name for p in processors] != sorted([p.name for p in after.glob("processor[0-9]*")], key=lambda n: int(n[9:])):
        raise ValueError("processor layouts differ")
    exact = []
    changed = []
    for proc in processors:
        for name in CORE:
            rel = Path(proc.name) / "constant/polyMesh" / name
            (exact if sha(before / rel) == sha(after / rel) else changed).append(str(rel))
    nonpoints = [p for p in changed if Path(p).name != "points"]
    topology = {"before": str(before), "after": str(after), "exact": exact, "changed": changed,
                "allNonpointFilesByteExact": not nonpoints}
    if nonpoints:
        return {"topology": topology, "sets": None, "boundary": None,
                "scope": "connectivity changed: native-label differences and coupled-proof inheritance refused"}
    sets = {name: {"old": 0, "new": 0, "newFailures": 0, "ranks": {}} for name in SETS}
    boundary = {"ranks": [], "allBoundaryCoordinatesExact": True}
    for proc in processors:
        rank = int(proc.name[9:])
        a = proc / "constant/polyMesh"
        b = after / proc.name / "constant/polyMesh"
        for name in SETS:
            old, new = labels(a / "sets" / name), labels(b / "sets" / name)
            row = sets[name]
            row["old"] += len(old)
            row["new"] += len(new)
            row["newFailures"] += len(new - old)
            row["ranks"][str(rank)] = {"old": len(old), "new": len(new), "newLabels": sorted(new - old),
                                       "removed": len(old - new)}
        original, current = read_points(a / "points"), read_points(b / "points")
        if original.shape != current.shape:
            raise ValueError("point counts differ with purported identical topology")
        offsets, vertices = read_all_faces(a / "faces")
        n_internal = len(read_label_list(a / "neighbour"))
        used = np.unique(vertices[offsets[n_internal]:])
        changed_points = np.flatnonzero(np.any(original != current, axis=1))
        changed_boundary = used[np.any(original[used] != current[used], axis=1)]
        finite = bool(np.all(np.isfinite(original)) and np.all(np.isfinite(current)))
        exact_boundary = finite and len(changed_boundary) == 0
        boundary["allBoundaryCoordinatesExact"] &= exact_boundary
        boundary["ranks"].append({"rank": rank, "changedPointCount": len(changed_points),
                                  "boundaryUsePointCount": len(used),
                                  "changedBoundaryPointIds": changed_boundary.tolist(),
                                  "allBoundaryCoordinatesExact": exact_boundary, "allPointsFinite": finite})
    boundary["canInheritCoupledProof"] = bool(proof and boundary["allBoundaryCoordinatesExact"])
    if proof:
        prior = json.loads(proof.read_text())
        summary = prior.get("summary", prior)
        boundary["baselineCoupledProof"] = str(proof)
        boundary["baselineCoupledProofSha256"] = sha(proof)
        if boundary["canInheritCoupledProof"]:
            boundary["inheritedCoupledSummary"] = summary
    return {"topology": topology, "sets": sets, "boundary": boundary,
            "scope": "native labels comparable after every nonpoint core file proved byte-identical; inheritance additionally requires finite exact boundary-use coordinates"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--coupled-proof", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.before, args.after, args.coupled_proof)
    result["toolSha256"] = sha(Path(__file__))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"allNonpointFilesByteExact": result["topology"]["allNonpointFilesByteExact"],
                      "newFailedLabels": None if result["sets"] is None else {k: v["newFailures"] for k, v in result["sets"].items()},
                      "coupledInheritance": None if result["boundary"] is None else result["boundary"]["canInheritCoupledProof"]}))
if __name__ == "__main__":
    main()
