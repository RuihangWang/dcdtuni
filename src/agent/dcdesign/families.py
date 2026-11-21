"""Family templates: the base hall with each family's cooling units, supply path and containment.

The base project (``case/Shinaide_Liquid/Shinaide_liquid.cfdproj``) is the
in-row, open-aisle hall: two facing rows on a raised floor (racks, four in-row
units, CDU and UPS), a cold aisle between the row fronts, and each row's rear
zone running to a long wall. Its liquid network (CDU, supply and return rings
under the floor, rack manifolds) and hybrid heat budget are kept unchanged in
every family.

* In-row families keep the four base in-row units. CAC enables the base
  cold-aisle roof and end doors; HAC encloses each row's rear zone (roof at rack
  height from the rack rears to the wall, plus end panels).
* Perimeter families replace the in-row units by solid fillers of the same
  footprint and place two downflow units against each long wall (bottom supply
  through a floor opening into the plenum, back return). Perforated tiles in the
  cold aisle feed the room. HAC encloses the rear zones, which then contain the
  perimeter units; CAC encloses the cold aisle.

* Mesh: the cooling units' surfaces are refined to ``UNIT_SURFACE_LEVEL`` (3),
  the octree level of the cells around them, instead of the base project's 1.
  dcCartMesh moves an axis-aligned surface onto a grid plane (its first-layer
  floor) only within a band set by the surface's OWN level spacing. At level 1
  a perimeter unit's -x face, 1.84 mm off a level-3 plane, gets only the
  1.2 mm classic weld and carves a 1.84 mm cell strip that, where it meets the
  raised floor and the unit's floor opening, fails the final checkMesh (small
  determinants, low interpolation weights). In-row units stand inside the rows,
  so the base project never exposed this; the level is set in every family so
  that all families share one mesh setting.

Geometry follows the editor conventions: a model's ``location`` is the pivot
corner, its ``quaternion`` a yaw about +y, faces ``front`` / ``back`` are the
local z = 0 / z = size.z sides; a floor's cutouts and patches use floor-local
x and z. Every object resting on the raised floor has a footprint cutout named
after it, as the editor maintains them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .spec import CATALOG, DSL_VERSION, FAMILIES, N_UNITS, TILE_COUNTS, unit_ids

BUILDER_VERSION = "dc-design-families/v2-2026-10-21"
UNIT_SURFACE_LEVEL = 3      # meshConfig.acuRefinementLevel (min, max) of every family; see the module notes
REPO = Path(__file__).resolve().parents[3]
BASE_PROJECT = REPO / "case" / "Shinaide_Liquid" / "Shinaide_liquid.cfdproj"
_EPS = 1e-6


# ---------------------------------------------------------------- geometry helpers

def yaw_rad(q: dict) -> float:
    if abs(q.get("x", 0.0)) > 1e-9 or abs(q.get("z", 0.0)) > 1e-9:
        raise ValueError("only yaw rotations about +y are supported")
    return 2.0 * math.atan2(q.get("y", 0.0), q.get("w", 1.0))


def _yaw_quaternion(theta: float) -> dict:
    return {"x": 0.0, "y": math.sin(theta / 2), "z": 0.0, "w": math.cos(theta / 2)}


def aabb(geometry: dict) -> tuple[float, float, float, float, float, float]:
    """World (xmin, xmax, ymin, ymax, zmin, zmax) of a box model rotated about +y."""
    loc, size = geometry["location"], geometry["size"]
    t = yaw_rad(geometry["quaternion"])
    c, s = math.cos(t), math.sin(t)
    xs, zs = [], []
    for lx in (0.0, size["x"]):
        for lz in (0.0, size["z"]):
            xs.append(loc["x"] + lx * c + lz * s)
            zs.append(loc["z"] - lx * s + lz * c)
    return min(xs), max(xs), loc["y"], loc["y"] + size["y"], min(zs), max(zs)


@dataclass(frozen=True)
class Row:
    name: str
    zmin: float
    zmax: float
    front: str        # "+z" or "-z": the side facing the cold aisle

    @property
    def rear_z(self) -> float:
        return self.zmin if self.front == "+z" else self.zmax


@dataclass(frozen=True)
class Hall:
    room_x: tuple[float, float]
    room_z: tuple[float, float]
    floor_y: float
    ceiling_y: float
    row_x: tuple[float, float]
    rack_top_y: float
    rows: tuple[Row, Row]        # (low-z row, high-z row)

    @property
    def cold_aisle_z(self) -> tuple[float, float]:
        return self.rows[0].zmax, self.rows[1].zmin

    def rear_zone_z(self, row: Row) -> tuple[float, float]:
        return (self.room_z[0], row.zmin) if row is self.rows[0] else (row.zmax, self.room_z[1])

    def to_json(self) -> dict:
        return {"room_x_m": self.room_x, "room_z_m": self.room_z, "floor_y_m": self.floor_y,
                "ceiling_y_m": self.ceiling_y, "row_x_m": self.row_x, "rack_top_y_m": self.rack_top_y,
                "rows": [{"name": r.name, "z_m": (r.zmin, r.zmax), "front": r.front} for r in self.rows],
                "cold_aisle_z_m": self.cold_aisle_z,
                "rear_zones_z_m": [self.rear_zone_z(r) for r in self.rows]}


def _floor(project: dict, name: str) -> dict:
    hits = [f for f in project["constructions"]["floors"].values() if f["meta"].get("name") == name]
    if len(hits) != 1:
        raise ValueError(f"expected one floor named {name!r}")
    return hits[0]


def hall_layout(project: dict) -> Hall:
    """Rows, aisle and zones of the base hall, derived from its objects and checked."""
    c = project["constructions"]
    floor, ceiling = _floor(project, "Raised_Floor"), _floor(project, "Room_Ceiling")
    room = _floor(project, "Room_Floor")["geometry"]
    room_x = (room["location"]["x"], room["location"]["x"] + room["size"]["x"])
    room_z = (room["location"]["z"], room["location"]["z"] + room["size"]["z"])
    boxes = [aabb(o["geometry"]) for kind in ("racks", "acus", "cdus", "boxes") for o in c[kind].values()]
    zs = sorted({(round(b[4], 6), round(b[5], 6)) for b in boxes})
    if len(zs) != 2:
        raise ValueError("the base hall must have exactly two rows")
    (z0a, z0b), (z1a, z1b) = zs
    if not z0b < z1a:
        raise ValueError("rows overlap")
    xs = [(b[0], b[1]) for b in boxes]
    row_x = (min(x[0] for x in xs), max(x[1] for x in xs))
    for zr in zs:
        span = [x for b, x in zip(boxes, xs) if (round(b[4], 6), round(b[5], 6)) == zr]
        if abs(min(s[0] for s in span) - row_x[0]) > _EPS or abs(max(s[1] for s in span) - row_x[1]) > _EPS:
            raise ValueError("both rows must span the same x range")
    top = max(b[3] for b in boxes)
    fy = floor["geometry"]["location"]["y"]
    if any(abs(b[2] - fy) > _EPS for b in boxes):
        raise ValueError("every row object must stand on the raised floor")
    return Hall(room_x, room_z, fy, ceiling["geometry"]["location"]["y"], row_x, top,
                (Row("low", z0a, z0b, "+z"), Row("high", z1a, z1b, "-z")))


# ---------------------------------------------------------------- object builders

def _uid(name: str) -> str:
    return "dcd_" + name


def _rename(obj: dict, name: str) -> dict:
    obj = copy.deepcopy(obj)
    obj["uid"] = _uid(name)
    obj["meta"]["name"] = name
    obj["meta"]["enabled"] = True
    return obj


def _footprint_cutout(floor: dict, box: tuple, name: str) -> dict:
    fx, fz = floor["geometry"]["location"]["x"], floor["geometry"]["location"]["z"]
    x0, x1, z0, z1 = box[0] - fx, box[1] - fx, box[4] - fz, box[5] - fz
    ring = [[x0, z0], [x1, z0], [x1, z1], [x0, z1], [x0, z0]]
    return {"faceId": "top", "polygon": [[ring]], "sourceName": name}


def _baffle(template: dict, name: str, location: tuple, size_xy: tuple) -> dict:
    b = _rename(template, name)
    b["geometry"]["location"] = dict(zip("xyz", map(float, location)))
    b["geometry"]["size"] = {"x": float(size_xy[0]), "y": float(size_xy[1]), "z": 0.0}
    b["geometry"]["cutouts"] = []
    for face in b["geometry"]["faces"].values():
        face["patches"] = []
    b["cooling"] = {"type": "wall"}
    return b


def _end_panel(template, name, x, z0, z1, y0, y1):
    """Vertical panel in the plane x = const spanning z0..z1 (the base end-door orientation)."""
    return _baffle(template, name, (x, y0, z0), (z1 - z0, y1 - y0))


def _roof(template, name, y, x0, x1, z0, z1):
    """Horizontal panel at height y spanning x0..x1, z0..z1 (the base roof orientation)."""
    return _baffle(template, name, (x0, y, z0), (z1 - z0, x1 - x0))


def _perimeter_unit(template: dict, name: str, hall: Hall, x_center: float, wall: str) -> dict:
    body = CATALOG["perimeter_body_m"]
    w, d, h = body["width"], body["depth"], body["height"]
    gap = CATALOG["perimeter_wall_gap_m"]
    a = _rename(template, name)
    g = a["geometry"]
    if wall == "low":     # back face (local +z) toward +z, into the room
        loc, theta = (x_center - w / 2, hall.floor_y, hall.room_z[0] + gap), 0.0
    else:                 # rotated half a turn: back face toward -z
        loc, theta = (x_center + w / 2, hall.floor_y, hall.room_z[1] - gap), math.pi
    g["location"] = dict(zip("xyz", loc))
    g["size"] = {"x": w, "y": h, "z": d}
    g["quaternion"] = _yaw_quaternion(theta)
    g["cutouts"] = []
    for face in g["faces"].values():
        face["patches"] = []
    supply = {"id": f"{name}_supply", "type": "rect", "name": f"{name}_supply", "color": "#e06c75",
              "x": 0.0, "y": 0.0, "w": w, "h": d, "physics": {"role": "supply"}}
    ret = {"id": f"{name}_return", "type": "rect", "name": f"{name}_return", "color": "#61afef",
           "x": 0.05, "y": 0.1, "w": w - 0.1, "h": h - 0.2, "physics": {"role": "return"}}
    g["faces"]["bottom"]["patches"] = [supply]
    g["faces"]["back"]["patches"] = [ret]
    g["supplyFaces"] = [{"faceId": "bottom", "patchId": supply["id"]}]
    g["returnFaces"] = [{"faceId": "back", "patchId": ret["id"]}]
    return a


def _tile_patches(hall: Hall, floor: dict, count: int) -> tuple[list, list]:
    """``count`` perforated tiles in two lines along the cold aisle, centred on the rows."""
    t = CATALOG["tile_m"]
    fx, fz = floor["geometry"]["location"]["x"], floor["geometry"]["location"]["z"]
    za, zb = hall.cold_aisle_z
    lines = int(round((zb - za) / t))
    if lines < 2:
        raise ValueError("the cold aisle must hold two tile lines")
    z_lines = [za + (zb - za - 2 * t) / 2 + i * t for i in range(2)]
    per_line = count // 2
    slots = int(math.floor((hall.row_x[1] - hall.row_x[0]) / t + 1e-9))
    if per_line > slots:
        raise ValueError("too many tiles for the aisle")
    x_start = hall.row_x[0] + (hall.row_x[1] - hall.row_x[0] - per_line * t) / 2
    patches, openings = [], []
    for li, z in enumerate(z_lines):
        for i in range(per_line):
            pid = f"tile_{li}_{i}"
            patches.append({"id": pid, "name": pid, "type": "rect", "color": "#5599cc", "physics": {},
                            "x": x_start + i * t - fx, "y": z - fz, "w": t, "h": t})
            openings.append({"patchId": pid, "type": "perforated",
                             "perforation": {"shape": "circle", "holeDiameterMm": CATALOG["tile_hole_mm"],
                                             "openAreaPct": CATALOG["tile_open_area_pct"]}})
    return patches, openings


# ---------------------------------------------------------------- family template

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=None)
def _base_text(path: str) -> str:
    return Path(path).read_text()


def build_family(family: str, tile_count: int | None = None, *, base_path: Path = BASE_PROJECT) -> dict:
    """The family's template project (controls left at the base values)."""
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}")
    kind, containment = FAMILIES[family]["units"], FAMILIES[family]["containment"]
    if (kind == "perimeter") != (tile_count is not None) or (tile_count is not None and tile_count not in TILE_COUNTS):
        raise ValueError("perimeter families need tile_count in TILE_COUNTS; in-row families take none")
    base = json.loads(_base_text(str(base_path)))
    p = copy.deepcopy(base)
    c = p["constructions"]
    hall = hall_layout(base)
    floor = _floor(p, "Raised_Floor")
    cut_by_name = {cu["sourceName"]: cu for cu in floor["geometry"].get("cutouts", [])}
    base_acus = sorted(c["acus"].values(), key=lambda a: a["meta"]["name"])
    if len(base_acus) != N_UNITS:
        raise ValueError(f"the base hall must have {N_UNITS} in-row units")
    door_t = next(b for b in c["baffles"].values() if b["meta"]["name"] == "con_f")
    roof_t = next(b for b in c["baffles"].values() if b["meta"]["name"] == "con_b_copy")
    names = unit_ids(family)

    if kind == "inrow":
        new_acus = {}
        for name, a in zip(names, base_acus):
            old = a["meta"]["name"]
            obj = _rename(a, name)
            new_acus[obj["uid"]] = obj
            cut = cut_by_name.pop(old, None)
            if cut is None:
                raise ValueError(f"missing footprint cutout of {old}")
            cut_by_name[name] = {**cut, "sourceName": name}
        c["acus"] = new_acus
    else:
        filler_t = next(iter(c["boxes"].values()))
        for i, a in enumerate(base_acus):
            old = a["meta"]["name"]
            f = _rename(filler_t, f"row_filler_{i + 1:02d}")
            for key in ("location", "size", "quaternion"):
                f["geometry"][key] = copy.deepcopy(a["geometry"][key])
            c["boxes"][f["uid"]] = f
            cut = cut_by_name.pop(old)
            cut_by_name[f["meta"]["name"]] = {**cut, "sourceName": f["meta"]["name"]}
        L = hall.row_x[1] - hall.row_x[0]
        centers = (hall.row_x[0] + 0.25 * L, hall.row_x[0] + 0.75 * L)
        placements = [(x, "low") for x in centers] + [(x, "high") for x in centers]
        c["acus"] = {}
        for name, (x, wall) in zip(names, placements):
            u = _perimeter_unit(base_acus[0], name, hall, x, wall)
            c["acus"][u["uid"]] = u
            cut_by_name[name] = _footprint_cutout(floor, aabb(u["geometry"]), name)
        patches, openings = _tile_patches(hall, floor, tile_count)
        floor["geometry"]["faces"]["top"]["patches"] = patches
        floor["geometry"]["openings"] = openings

    # The transient tracks own the time-variable ACU fields (supply temperature and
    # airflow of the supply patch); the replaced units' tracks go, the new units get
    # tracks with the replaced unit's values (the compiler sets the design values).
    old_uids = [a["uid"] for a in base_acus]
    tracks = p.get("transient", {}).get("tracks", [])
    old_values = {(t["modelUid"], t["param"]): t["keyframes"][0]["value"] for t in tracks if t["modelUid"] in old_uids}
    kept = [t for t in tracks if t["modelUid"] not in old_uids]
    for old, unit in zip(old_uids, sorted(c["acus"].values(), key=lambda a: a["meta"]["name"])):
        patch = unit["geometry"]["supplyFaces"][0]["patchId"]
        for param in ("acu.supplyTemp", "acu.airflow"):
            kept.append({"modelUid": unit["uid"], "param": param, "target": patch,
                         "keyframes": [{"t": 0, "value": old_values[(old, param)]}]})
    if "transient" in p:
        p["transient"]["tracks"] = kept

    c["baffles"] = {}
    x0, x1 = hall.row_x
    if containment == "cold":
        za, zb = hall.cold_aisle_z
        for b in (_end_panel(door_t, "cac_end_low_x", x0, za, zb, hall.floor_y, hall.rack_top_y),
                  _end_panel(door_t, "cac_end_high_x", x1, za, zb, hall.floor_y, hall.rack_top_y),
                  _roof(roof_t, "cac_roof", hall.rack_top_y, x0, x1, za, zb)):
            c["baffles"][b["uid"]] = b
    elif containment == "hot":
        for row in hall.rows:
            za, zb = hall.rear_zone_z(row)
            for b in (_end_panel(door_t, f"hac_{row.name}_end_low_x", x0, za, zb, hall.floor_y, hall.rack_top_y),
                      _end_panel(door_t, f"hac_{row.name}_end_high_x", x1, za, zb, hall.floor_y, hall.rack_top_y),
                      _roof(roof_t, f"hac_{row.name}_roof", hall.rack_top_y, x0, x1, za, zb)):
                c["baffles"][b["uid"]] = b
    floor["geometry"]["cutouts"] = list(cut_by_name.values())
    p["runDefaults"]["meshConfig"]["acuRefinementLevel"] = [UNIT_SURFACE_LEVEL, UNIT_SURFACE_LEVEL]

    p.setdefault("meta", {})["dcdesign"] = {
        "dsl": DSL_VERSION, "builder": BUILDER_VERSION, "family": family, "tile_count": tile_count,
        "units": list(names), "base_project": str(Path(base_path).resolve().relative_to(REPO)),
        "base_project_sha256": _sha256(Path(base_path)), "hall": hall.to_json(), "catalog": CATALOG,
    }
    return p
