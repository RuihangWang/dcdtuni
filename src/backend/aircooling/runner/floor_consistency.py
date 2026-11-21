"""Floor-opening / floor-part consistency preflight.

A perforated (or wallBaffle) floor opening is exported as its own
zero-thickness sheet lying inside a hole cut out of the floor's remaining
part(s) (``<floor>_part1`` ...). Both come from ONE browser export. When the two
land on disk from different exports -- an older ``perforated_floor_<x>.stl``
kept beside a fresh ``wallBaffle_floor_<floor>_part1.stl`` after the opening's
footprint was edited -- the opening overhangs the hole and the strip of overlap
is coplanar with the floor part. The cut-cell mesher tags that strip to the
part's faceZone, so the opening's zone can never tile its own STL area and the
baffle coverage gate refuses the mesh after the full cut with a ratio (0.980)
that says nothing about the cause.

Base604, 2026-09-21: patch_2 from the June export (Z from 0.030697 m) meshed
against a fresh part1 whose hole starts at Z=0.05 m -- 19.3 mm x 9.6 m =
0.185 m2 of the opening's 9.30 m2, the same deficit at every refinement level
because it is geometry. Either set on its own tiles at exactly 1.0.

This module measures that overlap from the STL files themselves so the run can
refuse in a second, naming the pair and the fix (re-export the whole
triSurface from the same project). Pure geometry: the sheets are projected
onto their common plane and every part triangle is clipped against every
opening triangle (Sutherland-Hodgman); the summed clip area is the overlap.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from backend.aircooling.runner.mesh_acceptance import (
    _RE_SOLID_BLOCK,
    _RE_VERTEX,
    _mesh_input_stls,
)

logger = logging.getLogger(__name__)

Point3 = Tuple[float, float, float]
Tri3 = Tuple[Point3, Point3, Point3]
Point2 = Tuple[float, float]

#: a sheet is planar when its extent along one axis is below this (m)
PLANE_TOL = 1e-6
#: two sheets share a plane when their plane coordinates differ by less (m)
COPLANAR_TOL = 1e-4
#: overlaps below this fraction of the opening's area are export rounding
MIN_FRACTION = 1e-4
#: environment override: "off" turns the refusal into a warning
ENV_OVERRIDE = "DCDTUNI_FLOOR_OVERLAP_CHECK"


@dataclass(frozen=True)
class FloorOverlap:
    floor: str
    opening: str
    part: str
    overlap_m2: float
    opening_m2: float
    fraction: float
    plane_axis: int
    plane_coord: float


# ---------------------------------------------------------------------------
# STL reading
# ---------------------------------------------------------------------------

def solid_triangles(
    tri_surface_dir: Path,
    want: Optional[Callable[[str], bool]] = None,
) -> Dict[str, List[Tri3]]:
    """Map each solid name in the mesh-input STLs to its triangles.

    ``want(name)`` limits the (regex) vertex parse to the solids of interest;
    the names are always read. A name met in a second file keeps its first
    triangles: the split passes leave the extracted block in one file only,
    and counting a duplicate would double the overlap.
    """
    out: Dict[str, List[Tri3]] = {}
    for stl in _mesh_input_stls(tri_surface_dir):
        text = stl.read_text(encoding="utf-8", errors="replace")
        for match in _RE_SOLID_BLOCK.finditer(text):
            name = match.group(1)
            if name in out or (want is not None and not want(name)):
                continue
            verts: List[Point3] = [
                (float(a), float(b), float(c)) for a, b, c in _RE_VERTEX.findall(match.group(2))
            ]
            tris: List[Tri3] = [
                (verts[i], verts[i + 1], verts[i + 2]) for i in range(0, len(verts) - 2, 3)
            ]
            if tris:
                out[name] = tris
    return out


def sheet_plane(tris: Sequence[Tri3]) -> Optional[Tuple[int, float]]:
    """``(axis, coordinate)`` of the plane a zero-thickness sheet lies in, else None."""
    pts = [p for t in tris for p in t]
    if not pts:
        return None
    for axis in range(3):
        lo = min(p[axis] for p in pts)
        hi = max(p[axis] for p in pts)
        if hi - lo <= PLANE_TOL:
            return axis, 0.5 * (lo + hi)
    return None


def _tri_area3(t: Tri3) -> float:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def sheet_area(tris: Sequence[Tri3]) -> float:
    return sum(_tri_area3(t) for t in tris)


# ---------------------------------------------------------------------------
# 2-D clipping
# ---------------------------------------------------------------------------

def _signed_area(poly: Sequence[Point2]) -> float:
    n = len(poly)
    return 0.5 * sum(
        poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n)
    )


def _ccw(poly: Sequence[Point2]) -> List[Point2]:
    return list(poly) if _signed_area(poly) >= 0 else list(reversed(poly))


def _intersect(p: Point2, q: Point2, a: Point2, b: Point2) -> Point2:
    """Where segment p-q crosses the line through a-b."""
    dx1, dy1 = q[0] - p[0], q[1] - p[1]
    dx2, dy2 = b[0] - a[0], b[1] - a[1]
    den = dx1 * dy2 - dy1 * dx2
    if abs(den) < 1e-300:
        return q
    t = ((a[0] - p[0]) * dy2 - (a[1] - p[1]) * dx2) / den
    return (p[0] + t * dx1, p[1] + t * dy1)


def _clip(subject: Sequence[Point2], clip: Sequence[Point2]) -> List[Point2]:
    """Sutherland-Hodgman: ``subject`` clipped to the CCW convex ``clip``."""
    out = list(subject)
    n = len(clip)
    for i in range(n):
        if not out:
            break
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % n]

        def side(p: Point2) -> float:
            return (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)

        inp, out = out, []
        s = inp[-1]
        s_in = side(s) >= 0
        for e in inp:
            e_in = side(e) >= 0
            if e_in:
                if not s_in:
                    out.append(_intersect(s, e, (ax, ay), (bx, by)))
                out.append(e)
            elif s_in:
                out.append(_intersect(s, e, (ax, ay), (bx, by)))
            s, s_in = e, e_in
    return out


def overlap_area(a_tris: Sequence[Tri3], b_tris: Sequence[Tri3], axis: int) -> float:
    """Area shared by two sheets in the plane normal to ``axis`` (m2).

    Both are projected by dropping ``axis``; each triangle of ``b`` is clipped
    against each triangle of ``a`` and the clip areas are summed. Exact for
    sheets whose own triangles do not overlap each other, which is what an
    STL tessellation is.
    """
    keep = [i for i in range(3) if i != axis]

    def project(tri: Tri3) -> Optional[List[Point2]]:
        poly = [(p[keep[0]], p[keep[1]]) for p in tri]
        if abs(_signed_area(poly)) < 1e-14:
            return None
        return _ccw(poly)

    a_polys = [p for p in (project(t) for t in a_tris) if p]
    b_polys = [p for p in (project(t) for t in b_tris) if p]
    total = 0.0
    for pa in a_polys:
        for pb in b_polys:
            cut = _clip(pb, pa)
            if len(cut) >= 3:
                total += abs(_signed_area(cut))
    return total


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

def check_floor_openings(
    tri_surface_dir: Path,
    floors: Dict[str, dict],
    sanitize: Callable[[str], str],
    min_fraction: float = MIN_FRACTION,
) -> List[FloorOverlap]:
    """Every (opening, coplanar floor part) pair whose overlap exceeds ``min_fraction``.

    ``floors`` is the manifest's ``floors`` map: each floor's ``openings`` carry
    a ``solidName`` and a ``physics.type``; only ``perforated`` and
    ``wallBaffle`` openings become their own sheets. The floor's parts are the
    solids whose sanitized name starts with the floor's sanitized name and
    that are not openings -- the same rule the runner's split pass uses.
    """
    floors = floors or {}
    floor_sans = [sanitize(name) for name in floors]
    if not floor_sans:
        return []
    tris = solid_triangles(
        tri_surface_dir,
        want=lambda name: any(sanitize(name).startswith(fs) for fs in floor_sans),
    )
    by_san: Dict[str, Tuple[str, List[Tri3]]] = {sanitize(n): (n, t) for n, t in tris.items()}
    found: List[FloorOverlap] = []
    for floor_name, floor in floors.items():
        san_floor = sanitize(floor_name)
        openings = [op for op in (floor or {}).get("openings", []) if op.get("solidName")]
        op_sans = {sanitize(op["solidName"]) for op in openings}
        parts = [
            (name, part_tris)
            for san, (name, part_tris) in by_san.items()
            if san.startswith(san_floor) and san not in op_sans
        ]
        if not parts:
            continue
        for op in openings:
            if (op.get("physics") or {}).get("type", "wall") not in ("perforated", "wallBaffle"):
                continue
            entry = by_san.get(sanitize(op["solidName"]))
            if entry is None:
                continue
            op_name, op_tris = entry
            op_plane = sheet_plane(op_tris)
            op_area = sheet_area(op_tris)
            if op_plane is None or op_area <= 0:
                continue
            for part_name, part_tris in parts:
                part_plane = sheet_plane(part_tris)
                if (
                    part_plane is None
                    or part_plane[0] != op_plane[0]
                    or abs(part_plane[1] - op_plane[1]) > COPLANAR_TOL
                ):
                    continue
                area = overlap_area(op_tris, part_tris, op_plane[0])
                fraction = area / op_area
                if fraction >= min_fraction:
                    found.append(FloorOverlap(
                        floor=floor_name, opening=op_name, part=part_name,
                        overlap_m2=area, opening_m2=op_area, fraction=fraction,
                        plane_axis=op_plane[0], plane_coord=op_plane[1],
                    ))
    return found


def format_floor_overlaps(found: Sequence[FloorOverlap]) -> str:
    axis_name = "xyz"
    lines = [
        "Floor openings overhang their floor parts -- the perforated / wall-baffle "
        "floor opening STLs and the floor part STLs on disk do not come from the same export:"
    ]
    for ov in found:
        lines.append(
            f"  - floor '{ov.floor}': opening '{ov.opening}' overlaps part '{ov.part}' by "
            f"{ov.overlap_m2:.4g} m2 ({100 * ov.fraction:.2f}% of the opening's {ov.opening_m2:.4g} m2, "
            f"plane {axis_name[ov.plane_axis]}={ov.plane_coord:g})"
        )
    lines.append(
        "An opening must lie inside the hole of its floor part. Where it overhangs, the cut-cell "
        "mesher tags the strip to the floor part's faceZone and the baffle coverage gate refuses "
        "the mesh after the full cut (Base604: a June perforated_floor_*.stl beside a fresh "
        "wallBaffle_floor_*_part1.stl gave a 19 mm x 9.6 m strip and coverage 0.980). "
        "Re-export the whole triSurface from the same project (browser export), then mesh again. "
        f"Set {ENV_OVERRIDE}=off to mesh anyway."
    )
    return "\n".join(lines)
