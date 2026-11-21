"""Task + design -> complete ``.cfdproj`` (the family template with the design's controls).

Controls are written where the editor keeps them: the transient tracks own the
time-variable fields (ACU supply temperature and airflow, CDU primary inlet
temperature and flow, pump speed, server power, cold-plate heat), so the
compiler writes the tracks' t = 0 values; the ACU return patch carries the same
flow as the supply. The hybrid heat budget's per-server IT power is scaled with
the task. A closed unit keeps its body and loses its supply and return patches
(no flow, no thermostat), with its tracks removed.

Every running unit gets one enabled temperature probe ``RETURN_PROBE_OFFSET_M``
in front of the centre of its return face. dcdtuni's steady convergence rule
(owner, 2026-10-20) is that the ACU return-air temperatures have plateaued; the
scripted API exports no return-patch monitors, and the base project's probes
are disabled, so without these probes every steady run of a design is judged
INCONCLUSIVE. The probes change no geometry, boundary condition or physics.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache

import math

from .families import BUILDER_VERSION, aabb, build_family, yaw_rad
from .spec import CATALOG, DSL_VERSION, FAMILIES, Design, Task, unit_ids, validate

COMPILER_VERSION = "dc-design-compiler/v2-2026-10-21"
RETURN_PROBE_OFFSET_M = 0.15     # probe distance in front of a running unit's return face


class IllegalDesign(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


@lru_cache(maxsize=None)
def _template_text(family: str, tile_count) -> str:
    return json.dumps(build_family(family, tile_count))


def _set_track(tracks: list, uid: str, param: str, target: str, value: float):
    hits = [t for t in tracks if t["modelUid"] == uid and t["param"] == param and t["target"] == target]
    if len(hits) != 1:
        raise ValueError(f"expected one track {uid} {param} {target!r}, found {len(hits)}")
    hits[0]["keyframes"] = [{"t": 0, "value": float(value)}]


_FACE_NORMAL = {"front": (0.0, 0.0, -1.0), "back": (0.0, 0.0, 1.0), "left": (-1.0, 0.0, 0.0),
                "right": (1.0, 0.0, 0.0), "top": (0.0, 1.0, 0.0), "bottom": (0.0, -1.0, 0.0)}


def return_probe_point(geometry: dict) -> tuple[float, float, float]:
    """A point RETURN_PROBE_OFFSET_M in front of the centre of the unit's return face (world coordinates)."""
    face = geometry["returnFaces"][0]["faceId"]
    nx, ny, nz = _FACE_NORMAL[face]
    t = yaw_rad(geometry["quaternion"])
    c, s = math.cos(t), math.sin(t)
    wx, wz = nx * c + nz * s, -nx * s + nz * c       # local direction -> world (families.aabb convention)
    b = aabb(geometry)
    lo, hi = (b[0], b[2], b[4]), (b[1], b[3], b[5])
    point = []
    for k, w in enumerate((wx, ny, wz)):
        w = 0.0 if abs(w) < 1e-9 else w
        point.append(hi[k] + RETURN_PROBE_OFFSET_M if w > 0 else lo[k] - RETURN_PROBE_OFFSET_M if w < 0
                     else 0.5 * (lo[k] + hi[k]))
    return tuple(point)


def compile_design(task: Task, design: Design) -> dict:
    issues = validate(task, design)
    if issues:
        raise IllegalDesign(issues)
    p = json.loads(_template_text(design.family, design.tile_count))
    c = p["constructions"]
    tracks = p["transient"]["tracks"]
    names = unit_ids(design.family)
    by_name = {a["meta"]["name"]: a for a in c["acus"].values()}
    rated = CATALOG["acu_rated_flow_m3s"]

    for i, name in enumerate(names):
        a = by_name[name]
        g = a["geometry"]
        sref, rref = g["supplyFaces"][0], g["returnFaces"][0]
        supply = next(x for x in g["faces"][sref["faceId"]]["patches"] if x["id"] == sref["patchId"])
        ret = next(x for x in g["faces"][rref["faceId"]]["patches"] if x["id"] == rref["patchId"])
        if i in design.closed_units:
            g["faces"][sref["faceId"]]["patches"].remove(supply)
            g["faces"][rref["faceId"]]["patches"].remove(ret)
            g["supplyFaces"], g["returnFaces"] = [], []
            tracks[:] = [t for t in tracks if t["modelUid"] != a["uid"]]
            continue
        flow = design.acu_flow_fraction[i] * rated
        t_s = design.acu_supply_c[i]
        supply.setdefault("physics", {}).update(
            role="supply", setPointTemperature=t_s, coolingCapacity=CATALOG["acu_capacity_kw"],
            coolantTemperature=CATALOG["acu_coolant_c"])
        supply["physics"].pop("velocity", None)
        ret.setdefault("physics", {}).update(role="return", volumeFlowRate=flow)
        ret["physics"].pop("velocity", None)
        ret["physics"].pop("temperature", None)
        _set_track(tracks, a["uid"], "acu.supplyTemp", supply["id"], t_s)
        _set_track(tracks, a["uid"], "acu.airflow", supply["id"], flow)
        x, y, z = return_probe_point(g)
        pid = f"dcd_return_{name}"
        p.setdefault("instrumentation", {}).setdefault("probes", {})[pid] = {
            "uid": pid, "geometry": {"location": {"x": x, "y": y, "z": z}},
            "meta": {"name": f"return_{name}", "enabled": True}}

    cdu = next(iter(c["cdus"].values()))
    _set_track(tracks, cdu["uid"], "cdu.primaryInletT", "", design.cdu_primary_inlet_c)
    _set_track(tracks, cdu["uid"], "cdu.primaryFlow", "", design.cdu_primary_flow_kg_s)
    _set_track(tracks, cdu["uid"], "pump.rpm", "", design.cdu_pump_ratio)

    if task.it_scale != 1.0:
        for t in tracks:
            if t["param"] in ("server.power", "cp.heat"):
                for kf in t["keyframes"]:
                    kf["value"] = kf["value"] * task.it_scale
        for rec in p["runDefaults"]["hybridHeatBudget"]["servers"].values():
            rec["total_it_power_W"] = rec["total_it_power_W"] * task.it_scale

    meta = p["meta"]["dcdesign"]
    meta.update(compiler=COMPILER_VERSION, task={"it_scale": task.it_scale, "thermal_class": task.thermal_class,
                                                 "allowed_families": list(task.allowed_families)},
                design=design.to_json(), unit_uids={n: by_name[n]["uid"] for n in names},
                closed_units=[names[i] for i in design.closed_units])
    meta["design_sha256"] = hashlib.sha256(json.dumps(meta["design"], sort_keys=True).encode()).hexdigest()
    return p


def reference_design(family: str, tile_count: int | None = None) -> Design:
    """The base hall's operating point carried to a family (used for the G-D0/G-D1 reference designs)."""
    if FAMILIES[family]["units"] == "perimeter" and tile_count is None:
        tile_count = 16
    base = json.loads(_template_text(family, tile_count))
    tracks = base["transient"]["tracks"]
    names = unit_ids(family)
    uid = {a["meta"]["name"]: a["uid"] for a in base["constructions"]["acus"].values()}
    value = {(t["modelUid"], t["param"]): t["keyframes"][0]["value"] for t in tracks}
    ts = tuple(float(value[(uid[n], "acu.supplyTemp")]) for n in names)
    ff = tuple(min(1.0, float(value[(uid[n], "acu.airflow")]) / CATALOG["acu_rated_flow_m3s"]) for n in names)
    cdu = next(iter(base["constructions"]["cdus"].values()))["uid"]
    return Design(family=family, acu_supply_c=ts, acu_flow_fraction=ff,
                  cdu_primary_inlet_c=float(value[(cdu, "cdu.primaryInletT")]),
                  cdu_primary_flow_kg_s=float(value[(cdu, "cdu.primaryFlow")]),
                  cdu_pump_ratio=float(value[(cdu, "pump.rpm")]), tile_count=tile_count)
