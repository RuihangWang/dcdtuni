"""Acceptance check: does ``polyMesh/boundary`` account for every STL solid?

A multi-solid ASCII STL carries one ``solid <name>`` block per boundary region,
and the whole point of the pipeline is that each of those becomes a named
OpenFOAM patch that a boundary condition can be attached to. A solid that
silently fails to produce a patch is a lost BC — the region is still *there*
geometrically, but nothing can be prescribed on it, and the solve runs on a
quietly wrong problem.

The correspondence is not one-to-one by name, and the exceptions are NOT
guessed from naming conventions here — every one of them is read back out of
the dictionaries the runner itself generated:

``system/createBafflesDict``
    Zero-thickness baffles are meshed as internal faces in a faceZone and split
    by ``createBaffles`` into a cyclic ``<zone>_master`` / ``<zone>_slave``
    pair. So the solids feeding a zone contribute that PAIR, not one patch each
    (three ``Baffle_*`` solids merged into ``merged_wall_baffle`` yield exactly
    two patches). This is the "master/slave counted specially" rule.

``system/snappyHexMeshDict``
    ``refinementSurfaces`` is the runner's own declaration of which STL regions
    are meant to become named patches. A solid that appears only in ``geometry``
    — a rack through-zone, say — is a refinement volume, never a patch, and is
    reported separately rather than counted as a failure.

the STL geometry itself
    The U-slot system emits every internal slot boundary TWICE: the top face of
    the server below and the bottom face of the server above are the same plane
    under two names (``A10_A10_Slot19_top`` and ``A10_A10_Slot23_bottom`` are
    both the quad at y=1.1676). Solids occupying the same space form a group,
    and a group is not a failure in either of its two shapes:

    * one member got a patch — duplicate naming for a single surface, satisfied
      by whichever name won the faces;
    * NO member got a patch — the plane is buried between two stacked solid
      blocks, so there is no fluid on either side and no boundary face can
      exist. A boundary condition there would have nothing to act on.

    Alibaba-sige has 451 such groups, every one of them buried.

the mesh itself
    A surface can also end up with no fluid on either side without any
    coincident twin: the gap between the topmost server and the rack's inner
    top is sealed by the rack frame, so the flood fill never reaches it and
    every cell there is carved away. That is invisible in the STL — the only
    witness is whether any surviving CELL sits next to the surface. When a
    region is still unaccounted for, the mesh's cell centres are consulted, and
    a region with no cell within a couple of cell widths is reported as a
    sealed void rather than a loss.

So the pass condition is: every region the runner DECLARED as a patch exists in
``polyMesh/boundary`` (allowing for master/slave), and every patch in
``boundary`` traces back to one. A missing patch is a lost boundary condition —
the geometry is still there, but nothing can be prescribed on it, and the solve
runs on a quietly different problem.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

__all__ = ["MeshAcceptance", "check_patch_solid_correspondence", "is_editor_only_stl"]


#: Patches OpenFOAM/the pipeline may add that correspond to no STL solid.
#: `backgroundBoundary` is the leftover blockMesh box (normally zero faces);
#: `oldInternalFaces` appears when subsetMesh exposes interior faces.
SYNTHETIC_PATCHES = frozenset({"backgroundBoundary", "oldInternalFaces", "defaultFaces"})


# ---------------------------------------------------------------------------
# Editor-only export buckets — NEVER a mesh input (WP-W7)
# ---------------------------------------------------------------------------
#
# The frontend writes each server's left/right INTERFACE faces — the planes
# where a server touches a rack-frame cavity wall — into the bucket file
# ``merged_server_interface_walls.stl`` purely for editor export completeness.
# There is NO FLUID at these interfaces: the closed fluid partition is carried
# by the frame cavity walls plus the servers' fluid-side faces, so no mesh
# path may consume the file. It must never become a patch, a boundary
# condition, a refinement shell, or an acceptance solid.
#
# This predicate is the ONE place that contract is spelled out; the runner's
# STL discovery / patch-geometry measurement and this module's readers all
# filter through it. Per-side split forms (``merged_server_*_left.stl`` /
# ``merged_server_*_right.stl``) are excluded by the same rule. A legitimate
# server bucket file always ends in ``<N>U.stl`` or ``<N>U__<zone>.stl``
# (see ``_parse_bucket_from_filename`` in stlgen_runner), never in
# ``_left``/``_right``, so this cannot hit a real mesh input.
EDITOR_ONLY_STL_NAMES = frozenset({"merged_server_interface_walls.stl"})
_EDITOR_ONLY_STL_PREFIX = "merged_server_"
_EDITOR_ONLY_STL_SUFFIXES = ("_left.stl", "_right.stl")


def is_editor_only_stl(filename: "str | Path") -> bool:
    """True when a triSurface STL is an editor-only export artifact.

    Such files must be ignored by EVERY mesh path: runner STL discovery,
    patch-geometry measurement, snappy/dcCart surface declaration,
    createBaffles faceZones, field/BC materialisation, and the acceptance
    readers below.
    """
    name = Path(filename).name
    if name in EDITOR_ONLY_STL_NAMES:
        return True
    return name.startswith(_EDITOR_ONLY_STL_PREFIX) and name.endswith(
        _EDITOR_ONLY_STL_SUFFIXES
    )


def _mesh_input_stls(tri_surface_dir: Path) -> List[Path]:
    """Sorted mesh-input ``*.stl`` files under *tri_surface_dir*.

    Editor-only export buckets (see :func:`is_editor_only_stl`) are not mesh
    inputs, so every reader below measures only the geometry the mesh path
    could actually have consumed.
    """
    if not tri_surface_dir.is_dir():
        return []
    return [
        stl
        for stl in sorted(tri_surface_dir.glob("*.stl"))
        if not is_editor_only_stl(stl.name)
    ]


_RE_SOLID = re.compile(r"^\s*solid\s+(\S+)", re.MULTILINE)
_RE_SOLID_BLOCK = re.compile(r"solid\s+(\S+)(.*?)endsolid", re.DOTALL)
_RE_VERTEX = re.compile(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)")
_RE_PATCH = re.compile(r"^    ([A-Za-z_][\w.]*)\s*$", re.MULTILINE)


@dataclass
class MeshAcceptance:
    """Outcome of one case's patch/solid correspondence check."""

    case: str
    n_solids: int = 0
    n_patches: int = 0
    #: STL solids with neither a patch nor a documented reason to lack one
    missing_patches: List[str] = field(default_factory=list)
    #: patches that trace back to no solid and are not synthetic
    unexpected_patches: List[str] = field(default_factory=list)
    #: solids consumed by a faceZone baffle -> (solid, master/slave pair)
    baffle_solids: Dict[str, str] = field(default_factory=dict)
    #: STL solids the runner never declared as patches (refinement volumes)
    not_requested_solids: List[str] = field(default_factory=list)
    #: declared region -> the same-geometry region whose patch stands in for it
    duplicate_geometry: Dict[str, str] = field(default_factory=dict)
    #: declared regions sharing a plane where NO member got a patch: an
    #: interface buried between two solids, with no fluid on either side
    buried_interfaces: List[str] = field(default_factory=list)
    #: declared regions with no mesh face and nothing reaching their plane from
    #: either side — buried between flush solids, or a sealed pocket
    sealed_voids: List[str] = field(default_factory=list)
    #: declared regions a CELL STRADDLES: the grid never resolved the surface,
    #: so its boundary condition has nothing to attach to. A real defect.
    unresolved_surfaces: List[str] = field(default_factory=list)
    #: declared regions whose STL solid holds ZERO triangles. An export defect,
    #: not a mesher one: the boundary condition has no geometry at all.
    empty_solids: List[str] = field(default_factory=list)
    #: server fan faces the mesh kept less than FAN_FACE_MIN_RATIO of: the
    #: server box is open and air passes through it between the aisles
    open_server_faces: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return (
            not self.error
            and not self.missing_patches
            and not self.unresolved_surfaces
            and not self.empty_solids
            and not self.unexpected_patches
            and not self.open_server_faces
            and self.n_patches > 0
        )

    def summary(self) -> str:
        if self.error:
            return f"{self.case}: ERROR {self.error}"
        status = "OK" if self.ok else "FAIL"
        parts = [
            f"{self.case}: {status}",
            f"{self.n_solids} solids -> {self.n_patches} patches",
        ]
        if self.baffle_solids:
            parts.append(f"{len(self.baffle_solids)} baffle solids -> master/slave")
        if self.not_requested_solids:
            parts.append(f"{len(self.not_requested_solids)} not-requested")
        if self.duplicate_geometry:
            parts.append(f"{len(self.duplicate_geometry)} duplicate-geometry")
        if self.buried_interfaces:
            parts.append(f"{len(self.buried_interfaces)} buried-interface")
        if self.sealed_voids:
            parts.append(f"{len(self.sealed_voids)} buried/sealed")
        if self.unresolved_surfaces:
            parts.append(f"UNRESOLVED {len(self.unresolved_surfaces)}")
        if self.empty_solids:
            parts.append(
                f"EMPTY-STL {len(self.empty_solids)}: "
                + ", ".join(self.empty_solids[:4])
                + ("…" if len(self.empty_solids) > 4 else "")
            )
        if self.open_server_faces:
            parts.append(
                f"OPEN-SERVER {len(self.open_server_faces)}: "
                + ", ".join(self.open_server_faces[:4])
                + ("…" if len(self.open_server_faces) > 4 else "")
            )
        if self.missing_patches:
            parts.append(
                f"MISSING {len(self.missing_patches)}: "
                + ", ".join(self.missing_patches[:6])
                + ("…" if len(self.missing_patches) > 6 else "")
            )
        if self.unexpected_patches:
            parts.append(
                f"UNEXPECTED {len(self.unexpected_patches)}: "
                + ", ".join(self.unexpected_patches[:6])
                + ("…" if len(self.unexpected_patches) > 6 else "")
            )
        return " | ".join(parts)


def _read_stl_solids(tri_surface_dir: Path) -> Dict[str, List[str]]:
    """Map each mesh-input ``*.stl`` file to the solid names it declares."""
    out: Dict[str, List[str]] = {}
    for stl in _mesh_input_stls(tri_surface_dir):
        text = stl.read_text(encoding="utf-8", errors="replace")
        out[stl.name] = _RE_SOLID.findall(text)
    return out


def _solid_geometry_keys(tri_surface_dir: Path) -> Dict[str, str]:
    """Map each solid name to a hash of its geometry.

    Two solids with the same key occupy the same space, so at most one of them
    can own the mesh faces there. The key is the SET of distinct vertices,
    rounded to 1e-6 m and sorted — a set rather than a list because the same
    rectangle split along either diagonal gives different triangle vertex
    multisets but the same four corners.
    """
    keys: Dict[str, str] = {}
    for stl in _mesh_input_stls(tri_surface_dir):
        text = stl.read_text(encoding="utf-8", errors="replace")
        for match in _RE_SOLID_BLOCK.finditer(text):
            name, body = match.group(1), match.group(2)
            verts = sorted(
                {
                    (round(float(a), 6), round(float(b), 6), round(float(c), 6))
                    for a, b, c in _RE_VERTEX.findall(body)
                }
            )
            if not verts:
                continue
            keys[name] = hashlib.md5(repr(verts).encode("utf-8")).hexdigest()
    return keys


def _read_boundary_patches(boundary_path: Path) -> List[str]:
    """Patch names from ``constant/polyMesh/boundary``.

    The file stays an ASCII dictionary even when the mesh itself is written in
    binary, so a plain regex over the four-space-indented entry keys is enough.
    """
    if not boundary_path.is_file():
        return []
    text = boundary_path.read_text(encoding="utf-8", errors="replace")
    start = text.find("(")
    return _RE_PATCH.findall(text[start:] if start >= 0 else text)


def _read_baffle_zones(create_baffles_dict: Path) -> Dict[str, List[str]]:
    """Map each baffle zoneName to the patch names createBaffles will produce."""
    zones: Dict[str, List[str]] = {}
    if not create_baffles_dict.is_file():
        return zones
    text = create_baffles_dict.read_text(encoding="utf-8", errors="replace")
    for zone in re.finditer(r"zoneName\s+(\S+?);", text):
        name = zone.group(1)
        # the master/slave names follow the zoneName within the same block
        tail = text[zone.end():]
        stop = tail.find("zoneName")
        block = tail if stop < 0 else tail[:stop]
        names = re.findall(r"\bname\s+(\S+?);", block)
        zones[name] = names or [f"{name}_master", f"{name}_slave"]
    return zones


def _brace_block(text: str, start: int) -> str:
    """Return the contents of the brace block that opens at/after *start*."""
    open_brace = text.find("{", start)
    if open_brace < 0:
        return ""
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1: i]
    return ""


def _read_declared_patch_regions(
    snappy_dict: Path,
    stl_solids: Dict[str, List[str]] | None = None,
) -> Set[str]:
    """Region names the runner declared as boundary patches.

    ``refinementSurfaces`` is the authority. A surface listed there with a
    ``regions`` block contributes one patch per region name. A surface WITHOUT
    one still contributes a patch per named solid in its STL — snappy names the
    patch after the STL region, not after the surface — which is how three
    ``merged_wallBlankingPanel_*U`` surfaces yield three
    ``Default_Rack_1_inlet_*`` patches. Only when the STL has no named solids
    does the surface name itself become the patch name. Anything present only
    in ``geometry`` (through-zones, refinement shells) is never a patch.
    """
    if not snappy_dict.is_file():
        return set()
    text = snappy_dict.read_text(encoding="utf-8", errors="replace")
    idx = text.find("refinementSurfaces")
    if idx < 0:
        return set()
    block = _brace_block(text, idx)

    declared: Set[str] = set()
    pos = 0
    while pos < len(block):
        m = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*$", re.MULTILINE).search(block, pos)
        if not m:
            break
        surface = m.group(1)
        body = _brace_block(block, m.end())
        if not body:
            break
        rid = body.find("regions")
        if rid >= 0:
            regions_block = _brace_block(body, rid)
            names = re.findall(r"^\s*([A-Za-z_][\w.]*)\s*$", regions_block, re.MULTILINE)
            if names:
                declared.update(names)
            else:
                declared.add(surface)
        else:
            solids_here = (stl_solids or {}).get(f"{surface}.stl") or []
            declared.update(solids_here or [surface])
        # advance past this surface's block
        pos = block.find(body, m.end()) + len(body) + 1

    return declared


def _empty_solid_names(tri_surface_dir: Path) -> Dict[str, str]:
    """Solids declared in an STL with ZERO triangles -> the file that holds them.

    `solid X` immediately followed by `endsolid X`. The pipeline still declares
    a patch for such a region, so the boundary condition it carries has no
    geometry to act on anywhere. Polygon_Room ships five of them in
    merged_server_5U.stl. This is an export defect, not a meshing one, and is
    named separately so it is not mistaken for one.
    """
    out: Dict[str, str] = {}
    for stl in _mesh_input_stls(tri_surface_dir):
        text = stl.read_text(encoding="utf-8", errors="replace")
        for match in _RE_SOLID_BLOCK.finditer(text):
            if not _RE_VERTEX.search(match.group(2)):
                out[match.group(1)] = stl.name
    return out


def _solid_boxes(tri_surface_dir: Path) -> Dict[str, "tuple"]:
    """Map each solid name to its ``(min_xyz, max_xyz)`` bounding box."""
    boxes: Dict[str, tuple] = {}
    for stl in _mesh_input_stls(tri_surface_dir):
        text = stl.read_text(encoding="utf-8", errors="replace")
        for match in _RE_SOLID_BLOCK.finditer(text):
            verts = _RE_VERTEX.findall(match.group(2))
            if not verts:
                continue
            xs = [float(v[0]) for v in verts]
            ys = [float(v[1]) for v in verts]
            zs = [float(v[2]) for v in verts]
            boxes[match.group(1)] = (
                (min(xs), min(ys), min(zs)),
                (max(xs), max(ys), max(zs)),
            )
    return boxes


def _regions_without_a_mesh_face(
    mesh_dir: Path, candidates: List[str]
) -> "tuple":
    """Split *candidates* into (no_face, remaining) using the MESH.

    The exact question is not "is there fluid nearby" but "is there a mesh FACE
    on this surface". Without one there is nothing for a boundary condition to
    attach to, whatever the reason — and with one, the region's absence from
    `boundary` means the face went somewhere else, which is a real defect.

    A distance heuristic got this wrong: `ACU_A1_Left` on CMI-Guichou has cells
    0.075 m away and looked "adjacent", but they are on the far side of a rack
    frame whose right wall is flush against the ACU. The plane has solid on both
    sides. Counting faces on the plane answers it directly: there are none.

    But "no face" alone is too lenient: it lumps a correctly-buried surface in
    with one the grid simply failed to resolve, and the second is a real defect
    with a real fix. They are told apart by whether a cell STRADDLES the plane —
    i.e. whether there are faces on BOTH sides of it inside the footprint but
    none on it:

    * a CELL occupies the gap the plane falls in -> that cell spans the surface,
      so the grid never resolved it. Merely having faces somewhere either side is
      not enough: on Base2-9 the face planes around `A1_A1_DH_S6500_U45_top`
      (y = 2.752174) run ... 2.70435 | 2.8 ... with ~0.012 m local spacing, an
      eight-cell gap in which every cell has been CARVED AWAY. Adding a grid
      line there changed nothing (+113% cells, zero patches recovered) because
      no face can exist in carved space.
    * faces only on one side, or none at all -> nothing reaches the plane; it is
      buried or sealed. (`ACU_A1_Left`: faces stop at x=1.75 because rack frame
      A3 is flush against the ACU at x=1.8. Solid on both sides.)

    Returns ``(buried, unresolved, remaining)``. On any read failure everything
    stays in ``remaining``, so an unreadable mesh never silently excuses a region.
    """
    if not candidates:
        return [], [], []
    try:
        import numpy as np

        from backend.aircooling.runner.polymesh_io import (
            cell_centres,
            read_all_faces,
            read_points,
        )
    except Exception:
        return [], [], list(candidates)

    poly = mesh_dir / "constant" / "polyMesh"
    try:
        points = read_points(poly / "points")
        offsets, verts = read_all_faces(poly / "faces")
    except Exception:
        return [], [], list(candidates)

    n_faces = len(offsets) - 1
    if n_faces <= 0:
        return [], [], list(candidates)

    # face centroids
    face_of_vertex = np.repeat(np.arange(n_faces), np.diff(offsets))
    sums = np.zeros((n_faces, 3), dtype=np.float64)
    np.add.at(sums, face_of_vertex, points[verts])
    centres = sums / np.diff(offsets).astype(np.float64)[:, None]

    cells = cell_centres(poly)
    if cells is None or not len(cells):
        return [], [], list(candidates)

    boxes = _solid_boxes(mesh_dir / "constant" / "triSurface")
    order = np.argsort(centres[:, 0])
    sorted_x = centres[order, 0]
    cell_order = np.argsort(cells[:, 0])
    cell_x = cells[cell_order, 0]

    buried: List[str] = []
    unresolved: List[str] = []
    remaining: List[str] = []
    for name in candidates:
        box = boxes.get(name)
        if box is None:
            remaining.append(name)
            continue
        lo = list(box[0])
        hi = list(box[1])
        extents = [hi[i] - lo[i] for i in range(3)]
        normal = min(range(3), key=lambda i: extents[i])
        reach = max(4.0 * max(extents), 0.5)

        # narrow by x first so only a slab of faces is tested per region
        x_lo = lo[0] - 1e-4
        x_hi = hi[0] + 1e-4
        # widen the x window when x IS the normal, so both sides are visible
        if normal == 0:
            x_lo = lo[0] - reach
            x_hi = hi[0] + reach
        first = int(np.searchsorted(sorted_x, x_lo, side="left"))
        last = int(np.searchsorted(sorted_x, x_hi, side="right"))
        if first >= last:
            buried.append(name)
            continue
        slab = centres[order[first:last]]

        in_footprint = np.ones(len(slab), dtype=bool)
        for i in range(3):
            if i == normal:
                continue
            in_footprint &= (slab[:, i] >= lo[i] - 1e-9) & (slab[:, i] <= hi[i] + 1e-9)

        offset = slab[:, normal] - lo[normal]
        on_plane = in_footprint & (np.abs(offset) < 1e-4)
        if bool(on_plane.any()):
            remaining.append(name)
            continue

        near = in_footprint & (np.abs(offset) < reach)
        above = offset[near & (offset > 1e-4)]
        below = offset[near & (offset < -1e-4)]
        if not len(above) or not len(below):
            buried.append(name)
            continue

        # The plane sits between the nearest face plane either side. Only a
        # genuine straddle has a CELL in that gap — a carved band has none.
        gap_lo = lo[normal] + float(below.max())
        gap_hi = lo[normal] + float(above.min())
        c_first = int(np.searchsorted(cell_x, lo[0] - reach, side="left"))
        c_last = int(np.searchsorted(cell_x, hi[0] + reach, side="right"))
        occupied = False
        if c_first < c_last:
            block = cells[cell_order[c_first:c_last]]
            inside = (block[:, normal] > gap_lo + 1e-9) & (block[:, normal] < gap_hi - 1e-9)
            for i in range(3):
                if i == normal:
                    continue
                inside &= (block[:, i] >= lo[i] - 1e-9) & (block[:, i] <= hi[i] + 1e-9)
            occupied = bool(inside.any())
        if occupied:
            unresolved.append(name)
        else:
            buried.append(name)
    return buried, unresolved, remaining


def check_patch_solid_correspondence(mesh_dir: Path, case_name: str = "") -> MeshAcceptance:
    """Check one meshed case. *mesh_dir* is the dir holding constant/ and system/."""
    mesh_dir = Path(mesh_dir)
    result = MeshAcceptance(case=case_name or mesh_dir.parent.name)

    boundary = mesh_dir / "constant" / "polyMesh" / "boundary"
    if not boundary.is_file():
        result.error = f"no polyMesh/boundary under {mesh_dir}"
        return result

    per_file = _read_stl_solids(mesh_dir / "constant" / "triSurface")
    solids = {name for names in per_file.values() for name in names}
    patches = _read_boundary_patches(boundary)
    result.n_solids = len(solids)
    result.n_patches = len(patches)

    if not solids:
        result.error = f"no STL solids found under {mesh_dir}/constant/triSurface"
        return result

    baffle_zones = _read_baffle_zones(mesh_dir / "system" / "createBafflesDict")
    declared = _read_declared_patch_regions(
        mesh_dir / "system" / "snappyHexMeshDict", per_file
    )
    if not declared:
        result.error = "snappyHexMeshDict declares no refinementSurfaces regions"
        return result

    patch_set = set(patches)

    # A faceZone baffle turns its declared region(s) into a cyclic master/slave
    # pair, so those regions must NOT be looked for under their own names.
    baffle_patch_names: Set[str] = set()
    consumed_by_baffle: Set[str] = set()
    for zone, names in baffle_zones.items():
        baffle_patch_names.update(names)
        pair = "/".join(names)
        for candidate in (zone, *per_file.get(f"{zone}.stl", [])):
            if candidate in declared or candidate in solids:
                consumed_by_baffle.add(candidate)
                result.baffle_solids[candidate] = pair
        # The declared region feeding a zone may be named after it either way
        # round: `merged_wall_baffle_<name>` (zone prefixes the region) or
        # `perforated_floor_<zone>` / `wallBaffle_floor_<zone>` (the generator
        # prefixes the zone). Both resolve to the same master/slave pair.
        for region in declared:
            if (
                region == zone
                or region.startswith(f"{zone}_")
                or region.endswith(f"_{zone}")
            ):
                consumed_by_baffle.add(region)
                result.baffle_solids.setdefault(region, pair)

    result.not_requested_solids = sorted(solids - declared)

    # MISSING: declared as a patch, but the mesher produced neither it nor a
    # master/slave pair for it. This is the lost-boundary-condition case.
    expected = declared - consumed_by_baffle
    apparently_missing = sorted(expected - patch_set)

    # ...unless another region occupying the SAME space did get a patch. Only
    # hash the geometry when something is missing: it re-reads every STL, which
    # is wasted work on a clean case.
    if apparently_missing:
        geom_keys = _solid_geometry_keys(mesh_dir / "constant" / "triSurface")
        by_key: Dict[str, List[str]] = {}
        for name, key in geom_keys.items():
            by_key.setdefault(key, []).append(name)
        still_missing = []
        for name in apparently_missing:
            key = geom_keys.get(name)
            twin = None
            if key:
                twin = next(
                    (o for o in by_key.get(key, []) if o != name and o in patch_set),
                    None,
                )
            group = by_key.get(key, []) if key else []
            if twin is not None:
                result.duplicate_geometry[name] = twin
            elif len(group) > 1:
                # shares its plane with another declared region and NEITHER got
                # a patch: solid on both sides, so no fluid face can exist there
                result.buried_interfaces.append(name)
            else:
                still_missing.append(name)
        apparently_missing = still_missing

    # A region whose STL solid has no triangles at all cannot be meshed by
    # anything; separate it before asking the mesh questions about it.
    if apparently_missing:
        empties = _empty_solid_names(mesh_dir / "constant" / "triSurface")
        result.empty_solids = sorted(n for n in apparently_missing if n in empties)
        apparently_missing = [n for n in apparently_missing if n not in empties]

    # Last resort: ask the mesh whether a face exists on the region at all.
    if apparently_missing:
        buried, unresolved, apparently_missing = _regions_without_a_mesh_face(
            mesh_dir, apparently_missing
        )
        result.sealed_voids = sorted(buried)
        result.unresolved_surfaces = sorted(unresolved)

    result.missing_patches = apparently_missing

    # UNEXPECTED: a patch nobody declared.
    unexpected = []
    for p in patches:
        if p in declared or p in baffle_patch_names or p in SYNTHETIC_PATCHES:
            continue
        base = re.sub(r"_(master|slave)$", "", p)
        if base in declared or base in baffle_zones:
            continue
        if any(base == z or base.startswith(f"{z}_") or base.endswith(f"_{z}")
               for z in baffle_zones):
            continue
        unexpected.append(p)
    result.unexpected_patches = sorted(set(unexpected))
    result.open_server_faces = [name for name, _, _ in fan_face_shortfalls(mesh_dir)]

    return result


#: A server fan face (``*_inlet_patch``, ``*_outlet_p<n>``) whose mesh area is
#: below this fraction of its STL area is reported as an open server box.
FAN_FACE_MIN_RATIO = 0.8
_RE_FAN_FACE = re.compile(r"_(inlet_patch|outlet_p\d+)$")
_RE_BOUNDARY_ENTRY = re.compile(
    r"\n\s+(\S+)\s*\n\s*\{[^{}]*?\bnFaces\s+(\d+);\s*\n\s*startFace\s+(\d+);", re.S
)


def _fan_face_stl_areas(tri_surface_dir: Path) -> Dict[str, float]:
    import numpy as np

    areas: Dict[str, float] = {}
    for stl in _mesh_input_stls(tri_surface_dir):
        text = stl.read_text(encoding="utf-8", errors="replace")
        for match in _RE_SOLID_BLOCK.finditer(text):
            name = match.group(1)
            if not _RE_FAN_FACE.search(name):
                continue
            v = np.array(_RE_VERTEX.findall(match.group(2)), dtype=float)
            if len(v) < 3:
                continue
            t = v[: len(v) // 3 * 3].reshape(-1, 3, 3)
            a = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum()
            areas[name] = areas.get(name, 0.0) + float(a)
    return areas


def fan_face_shortfalls(mesh_dir: Path, min_ratio: float = FAN_FACE_MIN_RATIO):
    """Server fan faces the mesh lost: ``[(patch, stl_area, mesh_area)]``.

    A server is a closed box whose front and back are its fan faces. When the
    mesher does not close that box, part of a fan face is left as internal
    faces and the box becomes a duct between the hot and the cold aisle: the
    patch still exists, so the name check above passes, but its area is short.
    CMI-Guichou's row J kept 54-72% of its NF5280M5 fan faces and leaked about
    11 m3/s of exhaust into the cold aisle. Quantisation on a face of at least
    one 1U slot stays above 0.9; 0.8 leaves margin. Reads every
    ``processor*/constant/polyMesh`` (or the serial one); an unreadable mesh
    returns ``[]``.
    """
    import numpy as np
    from backend.aircooling.runner.polymesh_io import read_all_faces, read_points

    mesh_dir = Path(mesh_dir)
    stl = _fan_face_stl_areas(mesh_dir / "constant" / "triSurface")
    if not stl:
        return []
    polys = sorted(mesh_dir.glob("processor*/constant/polyMesh")) or [mesh_dir / "constant" / "polyMesh"]
    got: Dict[str, float] = {}
    try:
        for poly in polys:
            text = (poly / "boundary").read_text(encoding="utf-8", errors="replace")
            ranges = [
                (name, int(n), int(s))
                for name, n, s in _RE_BOUNDARY_ENTRY.findall(text)
                if name in stl and int(n) > 0
            ]
            if not ranges:
                continue
            pts = read_points(poly / "points")
            off, verts = read_all_faces(poly / "faces")
            for name, n, s in ranges:
                lo, hi = off[s], off[s + n]
                idx = verts[lo:hi]
                starts = off[s:s + n] - lo
                nxt = np.arange(1, len(idx) + 1)
                nxt[np.r_[starts[1:], len(idx)] - 1] = starts
                cross = np.cross(pts[idx], pts[idx[nxt]])
                a = 0.5 * np.linalg.norm(np.add.reduceat(cross, starts, axis=0), axis=1)
                got[name] = got.get(name, 0.0) + float(a.sum())
    except (OSError, ValueError, IndexError):
        return []
    return sorted(
        (name, area, got.get(name, 0.0))
        for name, area in stl.items()
        if area > 0 and got.get(name, 0.0) < min_ratio * area
    )
