#!/usr/bin/env python3
"""Pure geometry enumeration of canonical vertex fans and contiguous convex merges.

This is a diagnostic candidate generator. It writes no mesh and makes no claim
about full OpenFOAM mesh acceptance. The determinant bound excludes impossible
physical triangular cones; passing that bound is only a necessary condition.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import numpy as np

AREA_EPS = 1e-12
TURN_EPS = 1e-14
PLANAR_EPS = 1e-10
DET_MIN = Fraction(1, 1000)
WEIGHT_MIN = .05
RATIO_MIN = .01


def face_geometry(points):
    p = np.asarray(points, dtype=float)
    mean = np.zeros(3)
    for q in p:
        mean += q
    mean /= len(p)
    if len(p) == 3:
        return .5 * np.cross(p[1] - p[0], p[2] - p[0]), mean
    area = np.zeros(3)
    weighted = np.zeros(3)
    total = 0.
    for i, q in enumerate(p):
        nxt = p[(i + 1) % len(p)]
        v = np.cross(nxt - q, mean - q)
        a = float(np.linalg.norm(v))
        area += v
        weighted += a * (q + nxt + mean)
        total += a
    return .5 * area, weighted / (3 * total) if total else mean


def canonical_cycle(loop, points):
    """Use exact coordinate tuples, never rank-local labels, for common order."""
    loop = tuple(loop)
    coords = [tuple(float(x) for x in points[i]) for i in loop]
    if len(set(loop)) != len(loop) or len(set(coords)) != len(loop):
        raise ValueError("outline has repeated labels or coincident coordinates")
    variants = []
    for seq in (loop, loop[::-1]):
        for k in range(len(seq)):
            candidate = seq[k:] + seq[:k]
            variants.append((tuple(tuple(float(x) for x in points[i]) for i in candidate), candidate))
    return list(min(variants, key=lambda x: x[0])[1])


def boundary_edges(loops):
    counts = Counter()
    for loop in loops:
        for a, b in zip(loop, loop[1:] + loop[:1]):
            if counts[(b, a)]:
                counts[(b, a)] -= 1
            else:
                counts[(a, b)] += 1
    return Counter({k: v for k, v in counts.items() if v})


def outline_from_partition(loops, points):
    edges = boundary_edges(loops)
    if not edges or any(v != 1 for v in edges.values()):
        raise ValueError("partition boundary is not a simple directed cycle")
    outgoing = {}
    for a, b in edges:
        if a in outgoing:
            raise ValueError("partition boundary branches")
        outgoing[a] = b
    start = next(iter(outgoing))
    loop = [start]
    nxt = outgoing[start]
    while nxt != start:
        if nxt in loop or nxt not in outgoing:
            raise ValueError("partition boundary is not closed")
        loop.append(nxt)
        nxt = outgoing[nxt]
    if len(loop) != len(edges):
        raise ValueError("partition boundary has disconnected cycles")
    return canonical_cycle(loop, points)


def triangle_bound(points):
    """Exact binary-coordinate 729*A^2/(32*Lmax^4) any-apex upper bound."""
    p = [[Fraction.from_float(float(x)) for x in q] for q in points]
    u = [p[1][j] - p[0][j] for j in range(3)]
    v = [p[2][j] - p[0][j] for j in range(3)]
    c = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
    a2 = sum(x*x for x in c) / 4
    l2 = max(sum((p[i][j] - p[(i+1) % 3][j])**2 for j in range(3)) for i in range(3))
    if not l2:
        raise ValueError("triangle has zero edge")
    return Fraction(729, 32) * a2 / l2**2


def compositions(n):
    """Every contiguous partition of n fan triangles, not only pairs."""
    if n == 0:
        yield []
    else:
        for first in range(1, n + 1):
            for rest in compositions(n - first):
                yield [first] + rest


def polygon_profile(loop, points, normal):
    p = np.asarray([points[i] for i in loop])
    sf, fc = face_geometry(p)
    area = float(np.linalg.norm(sf))
    if not np.isfinite(p).all() or area <= AREA_EPS or float(np.dot(sf, normal)) <= AREA_EPS:
        return None
    if np.max(np.abs((p - p[0]) @ normal)) > PLANAR_EPS:
        return None
    distances = []
    perimeter = 0.
    for i, a in enumerate(p):
        b = p[(i + 1) % len(p)]
        c = p[(i + 2) % len(p)]
        edge = b - a
        length = float(np.linalg.norm(edge))
        # Strict turns exclude collinear intermediate vertices of a merged piece.
        if length == 0 or float(np.dot(np.cross(edge, c - b), normal)) <= TURN_EPS:
            return None
        if any(float(np.dot(np.cross(edge, q - a), normal)) < -TURN_EPS for q in p):
            return None
        distances.append(float(np.linalg.norm(np.cross(edge, fc - a))) / length)
        perimeter += length
    return {"area": area, "centre": fc.tolist(), "perimeter": perimeter,
            "inradiusAtAreaCentre": min(distances), "shapeScore": min(distances)/perimeter,
            "areaPerimeterShape": 2*area/perimeter**2}


def interfaces_profile(bases, profiles, points):
    incidences = defaultdict(list)
    for k, loop in enumerate(bases):
        for a, b in zip(loop, loop[1:] + loop[:1]):
            incidences[tuple(sorted((a, b)))].append((k, a, b))
    rows = []
    for edge, inc in incidences.items():
        if len(inc) == 1:
            continue
        if len(inc) != 2 or inc[0][1:] != inc[1][1:][::-1]:
            raise ValueError("internal partition edge is not oppositely paired")
        i, j = inc[0][0], inc[1][0]
        a, b = (np.asarray(points[k]) for k in edge)
        e = b - a
        length = float(np.linalg.norm(e))
        d = [float(np.linalg.norm(np.cross(e, np.asarray(profiles[k]["centre"]) - a))) / length
             for k in (i, j)]
        areas = [profiles[k]["area"] for k in (i, j)]
        rows.append({"edge": list(edge), "pieces": [i, j],
                     "areaCentreDistancesToEdge": d,
                     "weight": min(d)/sum(d),
                     "volumeRatio": min(areas)/max(areas)})
    return rows


def enumerate_partitions(outline, points):
    outline = canonical_cycle(outline, points)
    if not 3 <= len(outline) <= 8:
        raise ValueError("candidate enumeration supports 3..8 original vertices")
    xyz = np.asarray([points[i] for i in outline])
    sf, fc = face_geometry(xyz)
    area = float(np.linalg.norm(sf))
    if not np.isfinite(xyz).all() or area <= AREA_EPS:
        raise ValueError("nonfinite or zero-area original polygon")
    normal = sf / area
    if np.max(np.abs((xyz - xyz[0]) @ normal)) > PLANAR_EPS:
        raise ValueError("original polygon is not planar")
    # The outline may contain collinear nodes, but must be a convex simple cycle.
    for i, a in enumerate(xyz):
        b = xyz[(i + 1) % len(xyz)]
        if any(float(np.dot(np.cross(b-a, q-a), normal)) < -TURN_EPS for q in xyz):
            raise ValueError("original polygon is not convex")
    original_edges = boundary_edges([outline])
    candidates = []
    rejects = Counter()
    fan_attempts = 0
    partition_attempts = 0
    for shift in range(len(outline)):
        rotated = outline[shift:] + outline[:shift]
        fan = [[rotated[0], rotated[k], rotated[k + 1]] for k in range(1, len(rotated) - 1)]
        fan_attempts += 1
        if any(polygon_profile(t, points, normal) is None for t in fan):
            rejects["degenerateFanAnchor"] += 1
            continue
        for groups in compositions(len(fan)):
            partition_attempts += 1
            bases = []
            cursor = 0
            for count in groups:
                bases.append([rotated[0]] + rotated[cursor + 1:cursor + count + 2])
                cursor += count
            profiles = [polygon_profile(b, points, normal) for b in bases]
            if any(p is None for p in profiles):
                rejects["nonStrictConvexMergedPiece"] += 1
                continue
            if boundary_edges(bases) != original_edges or set(sum(bases, [])) != set(outline):
                raise AssertionError("enumeration changed original outline or nodes")
            area_error = abs(sum(p["area"] for p in profiles) - area)
            moment = sum((p["area"] * np.asarray(p["centre"]) for p in profiles), np.zeros(3))
            moment_error = float(np.linalg.norm(moment - area * fc))
            if area_error > 1e-10 * area or moment_error > 1e-10 * max(area, float(np.linalg.norm(area*fc))):
                raise AssertionError("enumeration changed original area or first moment")
            bounds = []
            for piece, base in enumerate(bases):
                if len(base) == 3:
                    bound = triangle_bound([points[k] for k in base])
                    bounds.append({"piece": piece, "bound": float(bound),
                                   "exactFraction": str(bound),
                                   "atLeastDeterminantThreshold": bound >= DET_MIN})
            interfaces = interfaces_profile(bases, profiles, points)
            wmin = min((x["weight"] for x in interfaces), default=1.)
            rmin = min((x["volumeRatio"] for x in interfaces), default=1.)
            det_necessary = all(x["atLeastDeterminantThreshold"] for x in bounds)
            row = {"anchor": rotated[0], "anchorCoordinates": list(points[rotated[0]]),
                   "fanTriangles": fan, "groupSizes": groups, "bases": bases,
                   "pieceProfiles": profiles, "interfaces": interfaces,
                   "minimumWithinBaseWeight": wmin, "minimumWithinBaseVolumeRatio": rmin,
                   "minimumShapeScore": min(p["shapeScore"] for p in profiles),
                   "minimumAreaPerimeterShape": min(p["areaPerimeterShape"] for p in profiles),
                   "triangleAnyApexUpperBounds": bounds,
                   "triangleNecessaryCondition": det_necessary,
                   "withinBaseQualityEligible": det_necessary and wmin >= WEIGHT_MIN and rmin >= RATIO_MIN,
                   "preservedOriginalOutlineAndNodes": True,
                   "areaError": area_error, "firstMomentError": moment_error}
            candidates.append(row)
    candidates.sort(key=lambda c: (
        tuple(c["anchorCoordinates"]), tuple(c["groupSizes"])))
    det_candidates = [c for c in candidates if c["triangleNecessaryCondition"]]
    eligible = [c for c in det_candidates if c["withinBaseQualityEligible"]]
    eligible.sort(key=lambda c: (-c["minimumAreaPerimeterShape"], -c["minimumWithinBaseWeight"],
                                 -c["minimumWithinBaseVolumeRatio"],
                                 len(c["bases"]), tuple(c["anchorCoordinates"]), tuple(c["groupSizes"])))
    return {"schema": "canonical-fan-enumeration-v1",
            "scope": "planar convex original polygon; same-base internal interfaces only",
            "selectionRule": "max(min(2A/P^2), minWithinBaseWeight, minWithinBaseRatio, -pieceCount), then exact canonical-coordinate traversal",
            "enumerationRule": "every positive original-vertex fan and every contiguous fan partition; no apex input",
            "thresholds": {"areaEpsM2": AREA_EPS, "turnEpsM2": TURN_EPS,
                           "planarEpsM": PLANAR_EPS, "triangleBoundMinimum": str(DET_MIN),
                           "withinBaseWeightMinimum": WEIGHT_MIN, "withinBaseRatioMinimum": RATIO_MIN},
            "outline": outline, "outlineCoordinates": xyz.tolist(),
            "originalArea": area, "originalAreaCentre": fc.tolist(),
            "fanAnchorAttempts": fan_attempts, "contiguousPartitionAttempts": partition_attempts,
            "rejected": dict(rejects), "strictGeometryCandidateCount": len(candidates),
            "triangleNecessaryCandidateCount": len(det_candidates),
            "withinBaseQualityEligibleCount": len(eligible),
            "candidates": candidates, "triangleNecessaryCandidates": det_candidates,
            "eligibleCandidates": eligible,
            "selected": eligible[0] if eligible else None,
            "noFullMeshAcceptanceClaim": True,
            "limitations": [
                "Any-apex triangular determinant bound is a necessary condition only.",
                "Nontriangular determinants, cross-original-base interfaces, whole-parent and external-neighbour metrics remain unverified by this tool.",
                "No coupled/periodic patch transform or pairing is inferred from polygon shape.",
                "All candidates require actual volume/moment/closure/kernel/full-checker validation before native acceptance."
            ]}


def audit_context(path):
    path = Path(path)
    raw = path.read_bytes()
    doc = json.loads(raw)
    points = {int(k): v for k, v in doc["points"].items()}
    old_bases = doc["repartition"]["physicalBases"]
    outline = outline_from_partition(old_bases, points)
    result = enumerate_partitions(outline, points)
    target = {tuple(canonical_cycle(b, points)) for b in old_bases}
    selected = result["selected"]
    result["identity"] = {"inputSha256": hashlib.sha256(raw).hexdigest(),
                          "inputPath": str(path), "parent": doc["parentOriginal"]["inputParent"],
                          "rank": doc["parentOriginal"]["rank"],
                          "sourcePhysicalBase": doc["repartition"]["sourcePhysicalBase"],
                          "coordinateValuesReadWithoutModification": True}
    result["selectedMatchesPriorConstructivePartition"] = bool(selected) and target == {
        tuple(canonical_cycle(b, points)) for b in selected["bases"]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_context(args.context)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
