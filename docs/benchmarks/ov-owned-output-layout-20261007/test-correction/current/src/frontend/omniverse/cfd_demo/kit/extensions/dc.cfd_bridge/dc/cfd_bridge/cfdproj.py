"""Read a .cfdproj (the STLGen editor's save format) into a placement list.

This is the OV demo's second geometry source, alongside the GTC scene JSON. It is a
PURE module: no `pxr`, no Kit imports, so it can be unit-tested with plain pytest.

Frame. A .cfdproj is authored **Y-up** — `size.y` is the height, `geometry.height` is the
room height — which is already the stage's up-axis, so unlike the GTC JSON path there is
no RotateX(-90) to undo. `location` is the **minimum corner** of the object's local AABB,
NOT its centre, and the quaternion rotates about that corner. Verified on Base_leakage:
Raised_Floor sits at y=1.0 and the racks span y=1.0..2.958 (standing ON it), with
Baffle_1 at y=2.958 (resting on the racks). Reading location as a centre puts every
object half its own height into the floor.

Scale. `geometry.scale` is carried and applied BEFORE the rotation, and it may be
negative — a mirror. See to_world.

Rotation. The full quaternion is carried through, NOT decomposed to a yaw angle. That
was the first design here and it was wrong: Base_leakage's `Baffle_1` is a -90 deg
rotation about **X** — a flat panel laid horizontally as a containment lid on top of the
racks — so a yaw-only model would have stood it upright, a wrong scene that still looks
plausible. Racks and ACUs do only yaw; baffles and other thin panels do not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


# Object classes we place. Ordered so the coarse shells build before the detail that
# sits on them, which keeps the USD tree readable in the OV stage window.
CLASSES = ("floors", "walls", "baffles", "boxes", "acus", "racks")



@dataclass
class Server:
    """One piece of equipment inside a rack, addressed by U-slot."""
    uid: str
    kind: str                 # 'fanServer' | ... (cfdproj `type`)
    slot_position: int
    slot_occupation: int
    name: str = ""            # meta.name, what the editor's tree shows
    # Per-server depth, STACKED on the rack's own inlet/outlet offset. A server may sit
    # further back than its neighbours; getRackServerBox adds these to the rack values.
    extra_inlet_offset: float = 0.0
    extra_outlet_offset: float = 0.0
    # Per-face colour + patches, keyed 'inlet'|'outlet'|'top'|'bottom'|'left'|'right'.
    # NOTE these patches are NORMALISED (0..1 fractions of the face), unlike the metre
    # rects on a model's own `geometry.faces`. _buildServerFaceWithPatches multiplies by
    # fr.w / fr.h; reading them as metres puts a 0.25-wide patch 25 cm off the face.
    faces: dict = field(default_factory=dict)


@dataclass
class Partition:
    """A horizontal shelf between two U-slots (rack constructions.partitions).

    `index` is the slot BELOW it, so the plate sits at y = marginBottom +
    (index + 1) * slotHeight. Every partition in every tracked case is currently
    `deleted`, i.e. the editor draws none of them — but the field is authored, so the
    reader carries it rather than pretending racks have no shelves.
    """
    index: int
    name: str = ""
    deleted: bool = False


@dataclass
class Placement:
    """One object, in world space, with everything an emitter needs."""
    cls: str                  # 'racks' | 'acus' | ...
    uid: str
    name: str
    origin: tuple             # world position of the local (0,0,0) corner
    size: tuple               # (W, H, D) in local axes, BEFORE rotation
    quat: tuple               # (x, y, z, w), rotation applied AT `origin`
    scale: tuple = (1.0, 1.0, 1.0)   # applied BEFORE the rotation; may be NEGATIVE
    enabled: bool = True
    opacity: float = 1.0
    polygon: list | None = None
    tags: list = field(default_factory=list)
    faces: dict = field(default_factory=dict)   # per-face patches (ACU supply/return)
    is_room_boundary: bool = False              # room shell vs a raised/vented floor
    # rack-only
    slot: int = 0
    slot_height: float = 0.0
    slot_width_ratio: float = 1.0
    margin_bottom: float = 0.0
    margin_top: float = 0.0
    inlet_offset: float = 0.0
    outlet_offset: float = 0.0
    deleted_faces: dict = field(default_factory=dict)
    servers: list = field(default_factory=list)
    partitions: list = field(default_factory=list)


@dataclass
class Scene:
    name: str
    room_plane: list          # [[x, z], ...] footprint polygon
    room_height: float
    placements: list


def rotate(q: tuple, v: tuple) -> tuple:
    """Rotate local vector `v` by quaternion `q` = (x, y, z, w).

    v' = v + 2 * qv x (qv x v + w * v), the standard branch-free form. The editor's
    quaternions are unit, so no normalisation.
    """
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (vx + qw * tx + qy * tz - qz * ty,
            vy + qw * ty + qz * tx - qx * tz,
            vz + qw * tz + qx * ty - qy * tx)


def to_world(p: "Placement", v: tuple) -> tuple:
    """Local point (in [0,W]x[0,H]x[0,D]) -> world: scale, then rotate, then translate.

    The scale is NOT decoration and is not always positive. Base_leakage's Rack_3 and
    Rack_4 carry scale (1, 1, -1) — a MIRROR — while their quaternion is merely the
    negative of Rack_1's, which is the same rotation. Dropping the scale placed those two
    racks 1 m the wrong way, closing the cold aisle from 1.2 m to 0.2 m and destroying
    the containment the case exists to model. Nothing in the rotation could reveal it:
    q and -q are identical, so the two rows looked correctly parallel while one of them
    sat in the wrong place.
    """
    sv = (v[0] * p.scale[0], v[1] * p.scale[1], v[2] * p.scale[2])
    r = rotate(p.quat, sv)
    return (p.origin[0] + r[0], p.origin[1] + r[1], p.origin[2] + r[2])


def _scale(sc: dict) -> tuple:
    return (float(sc.get("x", 1.0) or 1.0), float(sc.get("y", 1.0) or 1.0),
            float(sc.get("z", 1.0) or 1.0))


def _quat(q: dict) -> tuple:
    return (float(q.get("x", 0.0)), float(q.get("y", 0.0)),
            float(q.get("z", 0.0)), float(q.get("w", 1.0)))


def _servers_of(raw: dict) -> list:
    srv = ((raw.get("constructions") or {}).get("servers") or {})
    out = []
    for uid, s in srv.items():
        if not isinstance(s, dict):
            continue
        g = s.get("geometry") or {}
        out.append(Server(
            uid=uid,
            kind=str(s.get("type") or ""),
            slot_position=int(g.get("slotPosition") or 0),
            slot_occupation=int(g.get("slotOccupation") or 0),
            name=str((s.get("meta") or {}).get("name") or uid),
            extra_inlet_offset=float(s.get("extraInletOffset") or 0.0),
            extra_outlet_offset=float(s.get("extraOutletOffset") or 0.0),
            faces=(s.get("faces") if isinstance(s.get("faces"), dict) else {}),
        ))
    out.sort(key=lambda s: s.slot_position)
    return out


def _partitions_of(raw: dict) -> list:
    out = []
    for pt in ((raw.get("constructions") or {}).get("partitions") or []):
        if not isinstance(pt, dict) or pt.get("index") is None:
            continue
        out.append(Partition(index=int(pt["index"]), name=str(pt.get("name") or ""),
                             deleted=bool(pt.get("deleted"))))
    return out


def _placement(cls: str, uid: str, raw: dict) -> Placement:
    g = raw.get("geometry") or {}
    loc, size = g.get("location") or {}, g.get("size") or {}
    meta = raw.get("meta") or {}
    disp = meta.get("display") or {}
    name = str(meta.get("name") or uid)
    p = Placement(
        cls=cls,
        uid=uid,
        name=name,
        origin=(float(loc.get("x", 0.0)), float(loc.get("y", 0.0)), float(loc.get("z", 0.0))),
        size=(float(size.get("x", 0.0)), float(size.get("y", 0.0)), float(size.get("z", 0.0))),
        quat=_quat(g.get("quaternion") or {}),
        scale=_scale(g.get("scale") or {}),
        enabled=bool(meta.get("enabled", True)),
        # An explicit 0 here means "fully transparent", which is a real editor state, so
        # `or 1.0` would be wrong — only a MISSING value falls back to opaque.
        opacity=float(disp.get("opacity") if disp.get("opacity") is not None else 1.0),
        polygon=raw.get("polygon") or None,
        tags=list(meta.get("tags") or []),
        faces=(g.get("faces") if isinstance(g.get("faces"), dict) else {}),
        is_room_boundary=bool(raw.get("isRoomBoundary")),
    )
    if cls == "racks":
        p.slot = int(g.get("slot") or 0)
        p.slot_height = float(g.get("slotHeight") or 0.0)
        p.slot_width_ratio = float(g.get("slotWidthRatio") or 1.0)
        p.margin_bottom = float(raw.get("safeMarginBottom") or 0.0)
        p.margin_top = float(raw.get("safeMarginTop") or 0.0)
        p.inlet_offset = float(raw.get("inletOffset") or 0.0)
        p.outlet_offset = float(raw.get("outletOffset") or 0.0)
        p.deleted_faces = raw.get("deletedFaces") or {}
        p.servers = _servers_of(raw)
        p.partitions = _partitions_of(raw)
    return p


def read_cfdproj(path: str) -> Scene:
    with open(path) as f:
        doc = json.load(f)
    geom = doc.get("geometry") or {}
    cons = doc.get("constructions") or {}
    placements = []
    for cls in CLASSES:
        objs = cons.get(cls)
        if not isinstance(objs, dict):
            continue
        for uid, raw in objs.items():
            if isinstance(raw, dict):
                placements.append(_placement(cls, uid, raw))
    return Scene(
        name=str((doc.get("meta") or {}).get("projectName") or doc.get("name") or "scene"),
        room_plane=list(geom.get("plane") or []),
        room_height=float(geom.get("height") or 0.0),
        placements=placements,
    )


def room_bounds(scene: Scene) -> tuple:
    """(xmin, xmax, zmin, zmax) of the room footprint."""
    if not scene.room_plane:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [float(p[0]) for p in scene.room_plane]
    zs = [float(p[1]) for p in scene.room_plane]
    return (min(xs), max(xs), min(zs), max(zs))
