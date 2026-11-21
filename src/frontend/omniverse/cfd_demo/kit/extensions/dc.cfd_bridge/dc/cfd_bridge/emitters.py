"""Turn a Placement into the quads the STLGen frontend would draw for it.

Ported from `src/frontend/web/STL_generate.html`. The frontend's geometry is entirely
textured planar rectangles — `spawnPlanarRect` / `spawnFaceRect` — and the SAME quad list
feeds both the Three.js render and the multi-solid STL export that OpenFOAM meshes. So
reproducing the quad list here reproduces both the look and the solved geometry.

Everything is emitted in the placement's LOCAL frame, the box [0,W] x [0,H] x [0,D] with
the origin at the min corner. Local (not world) is what makes the prototype cache work:
four identical racks differ only by their Xform, so they share one cached USD.

Face basis, copied verbatim from `getFaceLayouts` (STL_generate.html:7129). Outward
normal = right x up, and getting a sign wrong here inverts a face without changing its
silhouette, so do not "simplify" these:

    front (Z=0): origin (W,0,0)  right (-1,0,0)  up (0,1,0)
    back  (Z=D): origin (0,0,D)  right ( 1,0,0)  up (0,1,0)
    left  (X=0): origin (0,0,0)  right (0,0, 1)  up (0,1,0)
    right (X=W): origin (W,0,D)  right (0,0,-1)  up (0,1,0)
    top   (Y=H): origin (0,H,D)  right ( 1,0,0)  up (0,0,-1)
    bottom(Y=0): origin (0,0,0)  right ( 1,0,0)  up (0,0, 1)

Line numbers are volatile — re-grep the named function before trusting one.

Materials. Every quad names a `materials.json` objectKind — the SAME kind the editor
gives that face — and the colour the editor hands the material (None when the editor
passes only a renderer default). usdgen turns (kind, colour, patch overlay, opacity)
into a material through dc.cfd_bridge.material_library, whose colour rules decide
whether that colour shows; this module keeps no material numbers of its own.
UVs are in metres in the face's own frame, as the editor's ShapeGeometry carries
them; the kind's UV mode (physical / stretch / tile-per-u) is applied in usdgen.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per-face material kind of a rack shell (renderRackModel's `rackFaceKind`).
RACK_FACE_KIND = {
    "left": "rack-side", "right": "rack-side", "top": "rack-top",
    "bottom": "rack-bottom", "front": "rack-front", "back": "rack-front",
}

# Generic box faces: the same cabinet material on all six sides.
_BOX_FACE_KIND = {f: "box" for f in ("front", "back", "left", "right", "top", "bottom")}
# The face whose surface a planar model's sheet is (a face texture is read there).
_PLANAR_FACE = {"walls": "front", "baffles": "front", "floors": "top"}
# The editor's patch-overlay tint on a server face (SERVER_PATCH_TINT).
SERVER_PATCH_TINT = "#ffcc33"
FACE_IMAGE = "face-image"


def floor_body_style(p, all_placements=None) -> str:
    """Port of _floorBodyStyle: the floor's body KIND.

    A floor that is not a room boundary is a raised floor (HPL tiles); the upper of two
    boundary floors is the room ceiling (mineral-fibre tiles); anything else is the slab.
    Getting this wrong is not subtle — Base_leakage's raised floor is the largest object
    in frame.
    """
    if not getattr(p, "is_room_boundary", False):
        return "raised-floor"
    boundary = [q for q in (all_placements or [])
                if q.cls == "floors" and getattr(q, "is_room_boundary", False)]
    if len(boundary) > 1:
        ys = [q.origin[1] for q in boundary]
        if max(ys) - min(ys) > 1e-3 and p.origin[1] >= (max(ys) + min(ys)) / 2.0:
            return "ceiling"
    return "floor"


def picture_source(record) -> str | None:
    """A face's or patch's picture, as the editor resolves it (patch_textures.sourceFor):
    an embedded data URL, a served URL, or a preset (served from the texture pool)."""
    if not isinstance(record, dict):
        return None
    for key in ("textureDataUrl", "textureUrl"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    preset = record.get("texturePreset")
    if isinstance(preset, str) and preset:
        from . import material_library
        for entry in material_library.manifest()["patchPresets"]:
            if entry["id"] == preset:
                return "/textures/" + entry["image"]["file"]
    return None


def face_basis(W: float, H: float, D: float) -> dict:
    return {
        "front":  ((W, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), W, H),
        "back":   ((0.0, 0.0, D), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), W, H),
        "left":   ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), D, H),
        "right":  ((W, 0.0, D), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0), D, H),
        "top":    ((0.0, H, D), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), W, D),
        "bottom": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), W, D),
    }


@dataclass
class Quad:
    """One planar rectangle, in the placement's local frame.

    `pts` is 4 corners in winding order (origin, +right, +right+up, +up), which puts the
    outward normal along right x up.
    """
    name: str          # becomes the USD prim name; mirrors the STL solid name
    material: str      # a materials.json objectKind, or 'face-image' with `picture`
    pts: tuple
    color: str | int | None = None   # what the editor hands the material; None = a default
    opacity: float = 1.0
    double_sided: bool = False
    keep_user_color: bool = False    # a patch overlay: its colour tints any tintable kind
    uv: tuple | None = None          # metres per point, in the face frame; None = from pts
    uv_mode: str | None = None       # 'stretch' | 'tile-per-u' | 'physical'; None = the kind's
    u_count: int = 1                 # rack units a tile-per-u face spans
    tile_m: float | None = None      # a 'tile-physical' override of the kind's tile size
    picture: str | None = None       # face-image source (data URL, served URL)
    fallback: str | None = None      # the kind to draw if the picture cannot be bound


def _uv_rect(w, h, flip=False):
    uv = ((0.0, 0.0), (w, 0.0), (w, h), (0.0, h))
    return (uv[0], uv[3], uv[2], uv[1]) if flip else uv


def _rect(origin, right, up, w, h):
    ox, oy, oz = origin
    rx, ry, rz = right
    ux, uy, uz = up
    p0 = (ox, oy, oz)
    p1 = (ox + rx * w, oy + ry * w, oz + rz * w)
    p2 = (p1[0] + ux * h, p1[1] + uy * h, p1[2] + uz * h)
    p3 = (ox + ux * h, oy + uy * h, oz + uz * h)
    return (p0, p1, p2, p3)


def face_opacity(p, face_id: str) -> float:
    """Per-face opacity, falling back to the model's.

    Mirrors the frontend: `faces[faceId]?.opacity ?? model.meta.display.opacity`. A face
    may be individually faded without the whole object being, so reading only the model
    value renders such a face solid.
    """
    fv = (getattr(p, "faces", None) or {}).get(face_id)
    if isinstance(fv, dict) and fv.get("opacity") is not None:
        return float(fv["opacity"])
    return float(p.opacity)


def _face_quad(name, material, basis, face, opacity, double_sided=False,
               x=0.0, y=0.0, w=None, h=None, color=None, **extra):
    """A rectangle placed on one face of the local box, in that face's own 2D frame.

    Its UVs are local to the rectangle (spawnPlanarRect's ShapeGeometry), metres.
    """
    origin, right, up, fw, fh = basis[face]
    w = fw if w is None else w
    h = fh if h is None else h
    ox = tuple(origin[i] + right[i] * x + up[i] * y for i in range(3))
    return Quad(name=name, material=material, pts=_rect(ox, right, up, w, h), color=color,
                opacity=opacity, double_sided=double_sided, uv=_uv_rect(w, h), **extra)


def _with_picture(quad, record, host_kind):
    """Draw `quad` as the record's picture (stretched once over it), if it has one."""
    src = picture_source(record)
    if src:
        quad.material, quad.picture, quad.fallback = FACE_IMAGE, src, host_kind
        quad.color, quad.keep_user_color, quad.uv_mode = "#ffffff", False, "stretch"
    return quad


# ── planar objects: walls, baffles, floors ──────────────────────────────────────────
# renderWallModel / renderBaffleModel / renderFloorModel are 4-7 lines each: one
# rectangle. They are ALWAYS double-sided — a wall or baffle is a zero-thickness
# boundary, so back-face culling would make it vanish from one side.

def _planar_sheet(p, material):
    """The sheet quad of a planar model, with its face picture if it has one."""
    W, H, D = p.size
    # A planar object has one zero extent; the other two span the panel. Walls and
    # baffles are W x H (D == 0); floors and ceilings are W x D (H == 0).
    if abs(H) < 1e-9 and abs(D) >= 1e-9:
        pts = _rect((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), W, D)
        uv = _uv_rect(W, D)
    else:
        pts = _rect((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), W, H)
        uv = _uv_rect(W, H)
    q = Quad(name=p.name, material=material, pts=pts, opacity=p.opacity,
             double_sided=True, uv=uv)
    return _with_picture(q, (p.faces or {}).get(_PLANAR_FACE.get(p.cls, "front")), material)


def emit_planar(p, material: str) -> list:
    return [_planar_sheet(p, material)] + emit_patches(p, face_basis(*p.size), p.faces)


def emit_polygon_floor(p, material: str = "floor") -> list:
    """A non-rectangular floor. Fan-triangulated, emitted as one Quad per triangle.

    48 of the 53 floors across the tracked cases carry a `polygon`, so this is the
    common case, not an exception. The polygon is [[x, z], ...] in the local frame and
    the panel lies at local y = 0.
    """
    poly = [(float(a), float(b)) for a, b in (p.polygon or [])]
    if len(poly) < 3:
        return emit_planar(p, material)
    out = []
    D = p.size[2]
    x0, z0 = poly[0]
    for i in range(1, len(poly) - 1):
        x1, z1 = poly[i]
        x2, z2 = poly[i + 1]
        # A Quad carries 4 points; a triangle repeats the last one. The USD author
        # collapses a degenerate corner back to a 3-vertex face. UVs are the 'top' face
        # frame the editor's shape lives in (right +x, up -z from z = D), so every
        # triangle of one floor tiles on the same grid.
        pts = ((x0, 0.0, z0), (x1, 0.0, z1), (x2, 0.0, z2), (x2, 0.0, z2))
        out.append(Quad(name=f"{p.name}_t{i}", material=material, pts=pts,
                        opacity=p.opacity, double_sided=True,
                        uv=tuple((x, D - z) for x, _, z in pts)))
    # Vents and leakage openings sit ON the panel.
    return out + emit_patches(p, face_basis(*p.size), p.faces)


# ── boxes and ACUs ──────────────────────────────────────────────────────────────────

def _shell(p, basis, kind):
    """Six faces of the local box in one kind, each with its face picture if any."""
    out = []
    for f in ("front", "back", "left", "right", "top", "bottom"):
        q = _face_quad(f"{p.name}_{f}", kind, basis, f, face_opacity(p, f))
        out.append(_with_picture(q, (p.faces or {}).get(f), kind))
    return out


def emit_box(p, kind_map=None) -> list:
    """Six faces of the local box (renderBoxModel) plus its patches."""
    W, H, D = p.size
    basis = face_basis(W, H, D)
    if kind_map:
        return [_face_quad(f"{p.name}_{f}", kind_map.get(f, "box"), basis, f, face_opacity(p, f))
                for f in ("front", "back", "left", "right", "top", "bottom")]
    return _shell(p, basis, "box") + emit_patches(p, basis, p.faces if isinstance(p.faces, dict) else {})


def _patch_style(p, patch):
    """(kind, colour, keep_user_color) of a patch, as the editor draws it.

    ACU islands by `physics.role` (_buildSingleBoxFace), in the patch's own colour; a
    patch on a raised floor is a perforated vent tile and on a baffle a grille, both in
    their material's colour (renderPlanarModelWithPatches / the baffle recipe); any
    other host shows its own material in the patch colour. A patch with no physics block
    is still a real opening — the raised-floor leakage tile in Base_leakage carries only
    a rect and a downward vector — so it is never skipped.
    """
    if p.cls == "floors":
        return "floor-vent", None, False
    if p.cls == "baffles":
        return "baffle-grille", "#ffffff", False
    color = patch.get("color")
    if p.cls == "acus":
        role = ((patch.get("physics") or {}).get("role") or "")
        return {"supply": "acu-supply", "return": "acu-return"}.get(role, "box"), color, True
    if p.cls == "walls":
        return "wall", color, True
    if p.cls == "racks":
        return "rack-front", color, True
    return "box", color, True


def emit_patches(p, basis: dict, faces: dict | None = None) -> list:
    """Face patches — ACU supply/return grilles, perforated floor tiles, leakage openings.

    Each rectangle sits 1 mm proud of its host face along the outward normal so it wins
    the depth test instead of z-fighting with the face it decorates. Its UVs are the
    host face's coordinates, as the editor's island shapes carry them.
    """
    out = []
    for fid, fv in (faces or {}).items():
        if fid not in basis or not isinstance(fv, dict):
            continue
        origin, right, up, fw, fh = basis[fid]
        n = (right[1] * up[2] - right[2] * up[1],
             right[2] * up[0] - right[0] * up[2],
             right[0] * up[1] - right[1] * up[0])
        for patch in (fv.get("patches") or []):
            if not isinstance(patch, dict):
                continue
            kind, color, keep = _patch_style(p, patch)
            px, py = float(patch.get("x", 0.0)), float(patch.get("y", 0.0))
            pw = float(patch.get("w", fw))
            ph = float(patch.get("h", fh))
            ox = tuple(origin[i] + right[i] * px + up[i] * py + n[i] * 0.001
                       for i in range(3))
            q = Quad(name=f"{p.name}_{fid}_{patch.get('id', 'p')}", material=kind,
                     pts=_rect(ox, right, up, pw, ph), color=color, keep_user_color=keep,
                     opacity=p.opacity, double_sided=True,
                     uv=((px, py), (px + pw, py), (px + pw, py + ph), (px, py + ph)))
            out.append(_with_picture(q, patch, kind))
    return out


def emit_acu(p, faces: dict | None = None) -> list:
    """ACU cabinet ('acu-body' sheet metal) plus its supply/return grilles.

    The grilles take the patch's own colour on their louvre / perforated material, the
    signal the editor gives for supply versus return.
    """
    W, H, D = p.size
    basis = face_basis(W, H, D)
    return _shell(p, basis, "acu-body") + emit_patches(p, basis, faces)


# ── racks ───────────────────────────────────────────────────────────────────────────
# Ported from renderRackModel (:9337) + buildRackFrameGeometry (:9109) and the four
# slot predicates at :5556-:6790.

def slot_bounds(p, i: int) -> tuple:
    """(x, y, w, h) of U-slot `i` on the front/back face (getRackSlotBounds, :6755)."""
    sw = p.size[0] * p.slot_width_ratio
    return ((p.size[0] - sw) / 2.0, p.margin_bottom + i * p.slot_height, sw, p.slot_height)


def equipment_for_slot(p, i: int):
    for s in p.servers:
        if s.slot_position <= i < s.slot_position + s.slot_occupation:
            return s
    return None


def slot_status(p, side: str, i: int) -> str:
    """'equipment' | 'void' | 'wall' (getRackSlotStatus, :6775).

    NOTE the sense of deletedFaces: True means the slot face was DELETED, i.e. an OPEN
    hole, not a blank panel. Reading it the other way round renders a sealed rack for
    Base_leakage, whose racks are open everywhere except the server.
    """
    if equipment_for_slot(p, i) is not None:
        return "equipment"
    df = (p.deleted_faces or {}).get(side) or []
    if i < len(df) and bool(df[i]):
        return "void"
    return "wall"


def merged_empty_ranges(p, side: str) -> list:
    """Contiguous runs of blank-panel slots (getRackMergedEmptySlotRanges, :5556).

    Returns (startUnit, endUnit, isWallBaffle). Merging matters for more than tidiness:
    one quad per run instead of per slot is the difference between ~10 and ~42 prims per
    rack face.

    A run SPLITS where the "half-dissolved" state changes — a slot walled on this side
    but void on the other acts as a baffle, not a rack wall, and the editor tints it
    differently. Merging across that boundary paints a whole column in the baffle colour.
    """
    opp = "back" if side == "front" else "front"
    ranges, start, baffle = [], -1, False
    for i in range(p.slot):
        if slot_status(p, side, i) != "wall":
            if start >= 0:
                ranges.append((start, i - 1, baffle))
                start = -1
            continue
        odf = (p.deleted_faces or {}).get(opp) or []
        opp_void = (equipment_for_slot(p, i) is None
                    and i < len(odf) and bool(odf[i]))
        if start < 0:
            start, baffle = i, opp_void
        elif opp_void != baffle:
            ranges.append((start, i - 1, baffle))
            start, baffle = i, opp_void
    if start >= 0:
        ranges.append((start, p.slot - 1, baffle))
    return ranges


def _quad3(name, material, origin, right, up, w, h, color, opacity,
           double_sided=False, flip=False, **extra):
    """A rectangle placed anywhere in the local frame by an explicit basis.

    The frontend's rack internals are NOT face-relative — `buildRackFrameGeometry` calls
    `spawnPlanarRect` with raw local vectors for the cavity walls, so they need the same
    primitive here rather than the face-relative `_face_quad`.

    `flip` reverses the winding, mirroring spawnPlanarRect's `flipWinding`: the server
    top/bottom faces choose right/up to keep patch UVs stable, which leaves the cross
    product pointing INTO the equipment.
    """
    if w <= 1e-6 or h <= 1e-6:
        return None
    pts = _rect(origin, right, up, w, h)
    if flip:
        pts = (pts[0], pts[3], pts[2], pts[1])
    extra.setdefault("uv", _uv_rect(w, h, flip))
    return Quad(name=name, material=material, pts=pts, color=color, opacity=opacity,
                double_sided=double_sided, **extra)


# A patch whose kind is textured keeps its material's colour; any other gets the tint.
_IMG_PATCH = frozenset(("server-outlet-mesh", "server-front"))
# A rack shelf (partition) has no kind of its own in the editor's render: a plain
# satin surface in its colour, which is the manifest's 'default'.
PARTITION_KIND = "default"
PARTITION_COLOR = "#88909b"


def slot_depth_range(p, server=None) -> tuple:
    """(z0, z1) of a slot's usable depth (getSlotDepthRange).

    The rack's inlet/outlet offsets recess the slot column from both ends; a server may
    stack its own extra offsets on top. This is the whole of issue 3 — reading these as
    zero puts every server face flush with the rack shell.
    """
    z0 = p.inlet_offset + (server.extra_inlet_offset if server else 0.0)
    z1 = p.size[2] - p.outlet_offset - (server.extra_outlet_offset if server else 0.0)
    return (z0, max(z0, z1))


def emit_rack_frame(p) -> list:
    """Outer shell, margin strips, side rails, and the inner cavity walls.

    Port of `buildRackFrameGeometry` (:9109). Every piece is double-sided: a rack is a
    hollow box, and with the front slots open the camera sees the inside of the far wall.
    """
    W, H, D = p.size
    basis = face_basis(W, H, D)
    n, op = p.name, p.opacity
    slot_w = W * p.slot_width_ratio
    slot_x = (W - slot_w) / 2.0
    slot_h = p.slot * p.slot_height
    mb, mt = p.margin_bottom, p.margin_top
    z0, z1 = slot_depth_range(p)
    out = []

    # The frame kinds take the manifest's colour whatever the editor hands them (its
    # frame/side/top/bottom/inner greys are renderer defaults), so no colour travels.
    for f in ("left", "right", "top", "bottom"):
        out.append(_face_quad(f"{n}_frame_outer_{f}", RACK_FACE_KIND[f], basis, f,
                              face_opacity(p, f), double_sided=True))

    for side in ("front", "back"):
        fop = face_opacity(p, side)
        kind = RACK_FACE_KIND[side]
        if mb > 1e-6:
            out.append(_face_quad(f"{n}_frame_{side}_margin_bottom", kind, basis, side,
                                  fop, True, x=0.0, y=0.0, w=W, h=mb))
        # marginTop is authored, not derived: H - (mb + slot*slotHeight) agrees on every
        # tracked case but is the editor's invariant, not its input.
        if mt > 1e-6:
            out.append(_face_quad(f"{n}_frame_{side}_margin_top", kind, basis, side,
                                  fop, True, x=0.0, y=H - mt, w=W, h=mt))
        # Side rails flanking the slot column, front and back: 19-inch mounting rails,
        # one rail section per rack unit.
        rails = dict(uv_mode="tile-per-u", u_count=max(1, int(p.slot or 1)))
        if slot_x > 1e-6 and slot_h > 1e-6:
            out.append(_face_quad(f"{n}_frame_{side}_strip_left", "rack-rail",
                                  basis, side, fop, True,
                                  x=0.0, y=mb, w=slot_x, h=slot_h, **rails))
        rw = W - slot_x - slot_w
        if rw > 1e-6 and slot_h > 1e-6:
            out.append(_face_quad(f"{n}_frame_{side}_strip_right", "rack-rail",
                                  basis, side, fop, True,
                                  x=slot_x + slot_w, y=mb, w=rw, h=slot_h, **rails))

    # Inner cavity walls — the left/right faces of the slot column, at the recessed depth.
    if slot_h > 1e-6 and (z1 - z0) > 1e-6:
        out.append(_quad3(f"{n}_frame_inner_left", "rack-side", (slot_x, mb, z0),
                          (0, 0, 1), (0, 1, 0), z1 - z0, slot_h, None, op, True))
        out.append(_quad3(f"{n}_frame_inner_right", "rack-side",
                          (slot_x + slot_w, mb, z0), (0, 1, 0), (0, 0, 1),
                          slot_h, z1 - z0, None, op, True))
    # Inner floor / ceiling close the cavity where the margins leave it open. These span
    # the FULL rack depth, not the recessed range.
    if slot_w > 1e-6:
        if mb > 1e-6:
            out.append(_quad3(f"{n}_frame_inner_floor", "rack-bottom", (slot_x, mb, 0.0),
                              (1, 0, 0), (0, 0, 1), slot_w, D, None, op, True))
        if mt > 1e-6:
            out.append(_quad3(f"{n}_frame_inner_ceiling", "rack-top", (slot_x, mb + slot_h, D),
                              (1, 0, 0), (0, 0, -1), slot_w, D, None, op, True))
    # Depth-offset cavity walls: the recess itself, between the shell face and the
    # recessed slot face. Without these the rack has an open-sided notch at each end.
    if p.inlet_offset > 1e-6 and slot_h > 1e-6:
        out.append(_quad3(f"{n}_frame_inlet_cavity_left", "rack-side", (slot_x, mb, 0.0),
                          (0, 0, 1), (0, 1, 0), p.inlet_offset, slot_h, None, op, True))
        out.append(_quad3(f"{n}_frame_inlet_cavity_right", "rack-side",
                          (slot_x + slot_w, mb, 0.0), (0, 1, 0), (0, 0, 1),
                          slot_h, p.inlet_offset, None, op, True))
    if p.outlet_offset > 1e-6 and slot_h > 1e-6:
        out.append(_quad3(f"{n}_frame_outlet_cavity_left", "rack-side", (slot_x, mb, z1),
                          (0, 0, 1), (0, 1, 0), p.outlet_offset, slot_h, None, op, True))
        out.append(_quad3(f"{n}_frame_outlet_cavity_right", "rack-side",
                          (slot_x + slot_w, mb, z1), (0, 1, 0), (0, 0, 1),
                          slot_h, p.outlet_offset, None, op, True))
    return [q for q in out if q is not None]


def server_face_rect(box: dict, face: str):
    """(origin, right, up, w, h) for one server face — getServerFaceRect (:8723) verbatim.

    Basis convention is the usual outward = right x up, EXCEPT top/bottom, whose vectors
    are chosen to keep patch UVs stable and therefore cross INTO the equipment; the
    caller flips their winding.
    """
    x0, x1, y0, y1, z0, z1 = (box["x0"], box["x1"], box["y0"], box["y1"],
                              box["z0"], box["z1"])
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    return {
        "inlet":  ((x1, y0, z0), (-1, 0, 0), (0, 1, 0), dx, dy),
        "outlet": ((x0, y0, z1), (1, 0, 0), (0, 1, 0), dx, dy),
        "bottom": ((x0, y0, z0), (1, 0, 0), (0, 0, 1), dx, dz),
        "top":    ((x0, y1, z1), (1, 0, 0), (0, 0, -1), dx, dz),
        "left":   ((x0, y0, z0), (0, 0, 1), (0, 1, 0), dz, dy),
        "right":  ((x1, y0, z1), (0, 0, -1), (0, 1, 0), dz, dy),
    }.get(face)


def emit_server(p, s, deleted: dict) -> list:
    """One server's six faces — port of buildServerGeometry / _buildSingleServerFace.

    All six, all double-sided (the editor's WP-W6): a transparent server shows its far
    end and its sides. The inlet is the bezel ('server-front', one bezel per U), the
    outlet the rear panel ('server-rear-socket'), the rest painted chassis. The inlet
    and outlet take the server's face colour when one is authored; a non-fan unit's lid
    takes its inlet colour; the colour rules ignore the editor's default colours.
    """
    z0, z1 = slot_depth_range(p, s)
    i0 = max(0, s.slot_position)
    i1 = min(p.slot - 1, s.slot_position + s.slot_occupation - 1)
    if i1 < i0:
        return []
    xa, ya, wa, _ = slot_bounds(p, i0)
    _, yb, _, hb = slot_bounds(p, i1)
    box = {"x0": xa, "x1": xa + wa, "y0": ya, "y1": yb + hb, "z0": z0, "z1": z1}
    span = max(1, int(s.slot_occupation or 1))
    faces = s.faces or {}
    out = []
    for face in ("inlet", "outlet", "top", "bottom", "left", "right"):
        if face == "inlet" and deleted.get("front"):
            continue
        if face == "outlet" and deleted.get("back"):
            continue
        fr = server_face_rect(box, face)
        if fr is None:
            continue
        origin, right, up, w, h = fr
        fdef = faces.get(face) or {}
        if face in ("inlet", "outlet"):
            color = fdef.get("color")
        elif face in ("top", "bottom") and s.kind != "fanServer":
            # A non-fan unit is a solid box, so its lid takes the body colour rather than
            # the channel grey a fan-server's through-duct wall gets.
            color = (faces.get("inlet") or {}).get("color")
        else:
            color = None
        kind = {"inlet": "server-front", "outlet": "server-rear-socket"}.get(face, "server-chassis")
        per_u = dict(uv_mode="tile-per-u", u_count=span) if face in ("inlet", "outlet") else {}
        # top/bottom/left/right vectors cross INTO the equipment; flip the winding.
        flip = face not in ("inlet", "outlet")
        out.append(_quad3(f"{p.name}_{s.uid}_{face}", kind, origin, right, up, w, h,
                          color, p.opacity, True, flip, **per_u))
        # Patches sit ON the face, 1 mm proud along the outward normal. Their rects are
        # FRACTIONS of the face, not metres; their UVs are the face's.
        nrm = (right[1] * up[2] - right[2] * up[1],
               right[2] * up[0] - right[0] * up[2],
               right[0] * up[1] - right[1] * up[0])
        if flip:
            nrm = tuple(-c for c in nrm)
        pm = "server-outlet-mesh" if face == "outlet" else kind
        patch_uv = (dict(uv_mode="physical", tile_m=0.1) if face == "outlet"
                    else dict(uv_mode="tile-per-u", u_count=span))
        textured = pm in _IMG_PATCH
        for i, patch in enumerate(fdef.get("patches") or []):
            if patch.get("type") == "circle":
                px = float(patch.get("cx", 0.5)) - float(patch.get("r", 0.0))
                py = float(patch.get("cy", 0.5)) - float(patch.get("r", 0.0))
                pw = ph = 2.0 * float(patch.get("r", 0.0))
            else:
                px, py = float(patch.get("x", 0.0)), float(patch.get("y", 0.0))
                pw, ph = float(patch.get("w", 1.0)), float(patch.get("h", 1.0))
            po = tuple(origin[k] + right[k] * px * w + up[k] * py * h + nrm[k] * 0.001
                       for k in range(3))
            u0, v0, u1, v1 = px * w, py * h, (px + pw) * w, (py + ph) * h
            uv = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            if flip:
                uv = (uv[0], uv[3], uv[2], uv[1])
            out.append(_quad3(f"{p.name}_{s.uid}_{face}_p{i}", pm, po, right, up,
                              pw * w, ph * h, None if textured else SERVER_PATCH_TINT,
                              p.opacity, True, flip, keep_user_color=not textured,
                              uv=uv, **patch_uv))
    return [q for q in out if q is not None]


def emit_rack(p) -> list:
    """Frame + blanking panels + servers + partitions. Port of `renderRackModel`.

    The editor's U-division guide lines are view helpers (isSlotBoundaryGuide): the
    render snapshot leaves them out, and the blanking-panel texture carries one seam
    per U, so neither Kit path draws them.
    """
    W, H, D = p.size
    basis = face_basis(W, H, D)
    n, op = p.name, p.opacity
    slot_w = W * p.slot_width_ratio
    slot_x = (W - slot_w) / 2.0
    z0, z1 = slot_depth_range(p)
    out = emit_rack_frame(p)

    for side in ("front", "back"):
        blind_off = p.inlet_offset if side == "front" else p.outlet_offset
        blind_z = z0 if side == "front" else z1
        for a, b, is_baffle in merged_empty_ranges(p, side):
            xa, ya, wa, _ = slot_bounds(p, a)
            _, yb, _, hb = slot_bounds(p, b)
            mh = (yb + hb) - ya
            # A range whose opposite slots are open acts as a baffle, not a rack wall,
            # and has its own blanking-panel material so its CFD treatment stays visible.
            kind = "blank-panel-baffle" if is_baffle else "blank-panel"
            if blind_off > 1e-6:
                # Recessed: the panel moves inward to sit flush with the equipment
                # faces, wound INTO the cavity as the editor winds it (its material is
                # double-sided in the manifest).
                right = (1, 0, 0) if side == "front" else (-1, 0, 0)
                ox = xa if side == "front" else xa + wa
                out.append(_quad3(f"{n}_{side}_blank_{a}_{b}", kind,
                                  (ox, ya, blind_z), right, (0, 1, 0), wa, mh, None, op))
            else:
                out.append(_face_quad(f"{n}_{side}_blank_{a}_{b}", kind, basis,
                                      side, op, x=xa, y=ya, w=wa, h=mh))

    for s in p.servers:
        i0, i1 = s.slot_position, s.slot_position + s.slot_occupation
        deleted = {
            side: any(bool(v) for v in
                      ((p.deleted_faces or {}).get(side) or [])[i0:i1])
            for side in ("front", "back")
        }
        out.extend(emit_server(p, s, deleted))

    # Horizontal shelves between slots. Every tracked case deletes all of them, so this
    # emits nothing today — but the data is authored per rack and a project that keeps
    # one would otherwise silently lose it.
    for part in p.partitions:
        if part.deleted:
            continue
        pz0, pz1 = slot_depth_range(p, equipment_for_slot(p, part.index))
        az0, az1 = slot_depth_range(p, equipment_for_slot(p, part.index + 1))
        y0, y1 = max(pz0, az0), min(pz1, az1)
        if y1 - y0 < 1e-6:
            continue
        out.append(_quad3(f"{n}_partition_{part.index}", PARTITION_KIND,
                          (slot_x, p.margin_bottom + (part.index + 1) * p.slot_height, y0),
                          (1, 0, 0), (0, 0, 1), slot_w, y1 - y0,
                          PARTITION_COLOR, op, True))
    return [q for q in out if q is not None]


# ── dispatch ────────────────────────────────────────────────────────────────────────

def emit(p, all_placements=None) -> list:
    """Placement -> quads. `all_placements` lets a floor tell ceiling from floor."""
    if p.cls == "racks":
        return emit_rack(p)
    if p.cls == "acus":
        return emit_acu(p, p.faces if isinstance(p.faces, dict) else {})
    if p.cls == "boxes":
        return emit_box(p)
    if p.cls == "floors":
        kind = floor_body_style(p, all_placements)
        return emit_polygon_floor(p, kind) if p.polygon else emit_planar(p, kind)
    if p.cls == "baffles":
        perforated = getattr(p, "cooling_type", "") == "perforated"
        return emit_planar(p, "baffle-perforated" if perforated else "baffle")
    if p.cls == "walls":
        return emit_planar(p, "wall")
    return emit_planar(p, "box")
