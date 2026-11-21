"""Read-only source-STL support probe for final OpenFOAM dcCart boundary faces.

This is a diagnostic, not a mesh repair or a solver acceptance replacement.
It reads one processor's binary v2412 polyMesh and the prepared ASCII STL
files. A face sample is supported when its orthogonal projection lies within
its declared source solid's triangles, within the explicit tolerances.
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sys


@dataclass(frozen=True)
class SupportDistance:
    plane_offset_m: float
    in_plane_gap_m: float


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _point_segment_distance(point, a, b):
    ab = _subtract(b, a)
    length2 = _dot(ab, ab)
    if length2 == 0:
        return math.sqrt(_dot(_subtract(point, a), _subtract(point, a)))
    t = max(0.0, min(1.0, _dot(_subtract(point, a), ab) / length2))
    residual = _subtract(point, tuple(a[i] + t * ab[i] for i in range(3)))
    return math.sqrt(_dot(residual, residual))


def projected_support(point, triangle) -> SupportDistance:
    """Distance of a point to a source triangle's plane and projected support."""
    a, b, c = triangle
    ab, ac = _subtract(b, a), _subtract(c, a)
    normal = _cross(ab, ac)
    normal_length = math.sqrt(_dot(normal, normal))
    if normal_length <= 1e-15:
        raise ValueError("source STL triangle has zero area")
    normal = tuple(v / normal_length for v in normal)
    signed_offset = _dot(_subtract(point, a), normal)
    projected = tuple(point[i] - signed_offset * normal[i] for i in range(3))
    ap = _subtract(projected, a)
    aa, bb, cc = _dot(ab, ab), _dot(ab, ac), _dot(ac, ac)
    denom = aa * cc - bb * bb
    if denom <= 0:
        raise ValueError("source STL triangle is numerically degenerate")
    q, r = _dot(ap, ab), _dot(ap, ac)
    u = (cc * q - bb * r) / denom
    v = (aa * r - bb * q) / denom
    if u >= -1e-12 and v >= -1e-12 and u + v <= 1 + 1e-12:
        gap = 0.0
    else:
        gap = min(
            _point_segment_distance(projected, a, b),
            _point_segment_distance(projected, b, c),
            _point_segment_distance(projected, c, a),
        )
    return SupportDistance(abs(signed_offset), gap)


def _read_foam_array(path: Path, typecode: str, width: int = 1):
    raw = path.read_bytes()
    if sys.byteorder != "little" or b"format      binary" not in raw or b"label=32;scalar=64" not in raw:
        raise ValueError(f"unsupported OpenFOAM binary format: {path}")
    match = re.search(rb"\n(\d+)\n\(", raw)
    if not match:
        raise ValueError(f"missing OpenFOAM list header: {path}")
    count = int(match.group(1)) * width
    values = array(typecode)
    end = match.end() + count * values.itemsize
    values.frombytes(raw[match.end():end])
    if len(values) != count:
        raise ValueError(f"truncated OpenFOAM list: {path}")
    return values


def _read_faces(path: Path):
    raw = path.read_bytes()
    if b"class       faceCompactList" not in raw:
        raise ValueError(f"expected binary faceCompactList: {path}")
    match = re.search(rb"\n(\d+)\n\(", raw)
    if not match:
        raise ValueError(f"missing face offset list: {path}")
    count = int(match.group(1))
    offsets = array("i")
    offset_end = match.end() + 4 * count
    offsets.frombytes(raw[match.end():offset_end])
    labels_match = re.match(rb"\)\n(\d+)\n\(", raw[offset_end:])
    if not labels_match:
        raise ValueError(f"missing face point list: {path}")
    label_count = int(labels_match.group(1))
    start = offset_end + labels_match.end()
    labels = array("i")
    labels.frombytes(raw[start:start + 4 * label_count])
    if len(offsets) != count or len(labels) != label_count or offsets[-1] != label_count:
        raise ValueError(f"invalid faceCompactList lengths: {path}")
    return offsets, labels


def _read_boundary_ranges(path: Path):
    text = path.read_text()
    ranges = []
    for match in re.finditer(r"\n\s*([^\s{}]+)\n\s*\{([^}]*)\}", text):
        count = re.search(r"nFaces\s+(\d+);", match.group(2))
        start = re.search(r"startFace\s+(\d+);", match.group(2))
        if count and start and int(count.group(1)):
            first = int(start.group(1))
            ranges.append((first, first + int(count.group(1)), match.group(1)))
    return ranges


def _read_ascii_stl_solid(path: Path, solid_name: str):
    triangles = []
    current = None
    vertices = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "solid":
            current = " ".join(parts[1:])
        elif parts[0] == "vertex":
            if len(parts) != 4:
                raise ValueError(f"invalid STL vertex: {path}")
            vertices.append(tuple(map(float, parts[1:])))
        elif parts[0] == "endfacet":
            if len(vertices) != 3:
                raise ValueError(f"invalid STL facet: {path}")
            if current == solid_name:
                triangles.append(tuple(vertices))
            vertices = []
        elif parts[0] == "endsolid":
            current = None
    if not triangles:
        raise ValueError(f"STL solid absent or empty: {path}:{solid_name}")
    return triangles


def probe(poly_mesh: Path, tri_surface: Path, spec: dict):
    """Inspect the explicitly mapped boundary faces of the requested cells."""
    point_values = _read_foam_array(poly_mesh / "points", "d", 3)
    points = [tuple(point_values[i:i + 3]) for i in range(0, len(point_values), 3)]
    offsets, face_points = _read_faces(poly_mesh / "faces")
    owners = _read_foam_array(poly_mesh / "owner", "i")
    neighbours = _read_foam_array(poly_mesh / "neighbour", "i")
    if len(owners) + 1 != len(offsets) or len(neighbours) > len(owners):
        raise ValueError("polyMesh owner/neighbour/face counts disagree")
    ranges = _read_boundary_ranges(poly_mesh / "boundary")
    mappings = {item["patch"]: item for item in spec["sourcePatches"]}
    if len(mappings) != len(spec["sourcePatches"]):
        raise ValueError("duplicate patch mapping")
    requested = set(spec["cells"])
    if not requested or any(not isinstance(c, int) or c < 0 for c in requested):
        raise ValueError("cells must be nonempty nonnegative integer IDs")
    cell_faces = {c: [] for c in requested}
    for fid, owner in enumerate(owners):
        if owner in requested:
            cell_faces[owner].append(fid)
    for fid, neighbour in enumerate(neighbours):
        if neighbour in requested:
            cell_faces[neighbour].append(fid)
    if any(not face_ids for face_ids in cell_faces.values()):
        raise ValueError("one or more requested cells are absent from this polyMesh")
    source_triangles = {}
    source_hashes = {}
    for mapping in mappings.values():
        path = tri_surface / mapping["stl"]
        key = (mapping["stl"], mapping["solid"])
        if key not in source_triangles:
            source_triangles[key] = _read_ascii_stl_solid(path, mapping["solid"])
            source_hashes[mapping["stl"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    tolerance = float(spec["inPlaneToleranceM"])
    max_plane = float(spec["maxPlaneOffsetM"])
    if tolerance < 0 or max_plane < 0 or not math.isfinite(tolerance + max_plane):
        raise ValueError("invalid support tolerance")
    samples = []
    for cell in sorted(requested):
        for fid in sorted(cell_faces[cell]):
            if fid < len(neighbours):
                continue
            patch = next((name for start, end, name in ranges if start <= fid < end), None)
            if patch not in mappings:
                continue
            mapping = mappings[patch]
            vertices = [points[face_points[i]] for i in range(offsets[fid], offsets[fid + 1])]
            if len(vertices) < 3:
                raise ValueError(f"boundary face {fid} has fewer than three vertices")
            centroid = tuple(sum(vertex[i] for vertex in vertices) / len(vertices) for i in range(3))
            key = (mapping["stl"], mapping["solid"])
            best = min((projected_support(centroid, tri) for tri in source_triangles[key]),
                       key=lambda d: (d.in_plane_gap_m, d.plane_offset_m))
            reasons = []
            if best.in_plane_gap_m > tolerance:
                reasons.append("outside_source_triangle_support")
            if best.plane_offset_m > max_plane:
                reasons.append("beyond_plane_offset_budget")
            samples.append({
                "cell": cell, "face": fid, "patch": patch,
                "sourceObjectUid": mapping["sourceObjectUid"],
                "sourceStl": mapping["stl"], "sourceSolid": mapping["solid"],
                "centroidM": centroid, "planeOffsetM": best.plane_offset_m,
                "inPlaneGapM": best.in_plane_gap_m, "reasons": reasons,
            })
    if len(samples) != int(spec["expectedMappedFaces"]):
        raise ValueError(f"expected {spec['expectedMappedFaces']} mapped faces, found {len(samples)}")
    return {
        "polyMesh": str(poly_mesh), "triSurface": str(tri_surface),
        "cells": sorted(requested), "inPlaneToleranceM": tolerance,
        "maxPlaneOffsetM": max_plane, "stlSha256": source_hashes,
        "mappedFaces": len(samples),
        "unsupportedFaces": sum(bool(sample["reasons"]) for sample in samples),
        "samples": samples,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poly-mesh", type=Path, required=True)
    parser.add_argument("--tri-surface", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = probe(args.poly_mesh, args.tri_surface, json.loads(args.spec.read_text()))
    except (KeyError, OSError, ValueError, IndexError) as exc:
        parser.error(str(exc))
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    else:
        sys.stdout.write(payload)
    return 2 if report["unsupportedFaces"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
