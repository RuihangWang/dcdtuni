"""Mirror of js/cfdproj_migrate.js — v2/legacy .cfdproj -> v3 in memory.
Single source of truth on the Python side for both the batch CLI
(migrate_cfdproj_v2_to_v3.py) and the load_cfdproj() compat shim."""
from __future__ import annotations
import copy
import json
import pathlib
import re

SCHEMA_VERSION = 3
_HANDLED = {
    "schemaVersion", "projectName", "exportFileName", "inputUnit", "bounds", "roomPolygon",
    "models", "sensorPoints", "cfdRoom", "dctwinConfig", "displayMode", "view",
    "snapFace", "snapEdge", "snapGrid", "treeCollapsed", "selection", "groups",
    "previewVisibility", "previewRegionVisibility", "previewRegionReverse",
    "rackDetailTarget", "rackDetailReturnMode", "rackDetailShowContext",
    "_selectedCategories", "_anchorCategory", "_lastClickedCategory", "liquidCooling",
}


def migrate_v2_to_v3(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return raw
    if int(raw.get("schemaVersion") or 0) >= SCHEMA_VERSION:
        return raw

    cfd_room = dict(raw.get("cfdRoom") or {})
    room_height = cfd_room.pop("height", None)
    room_temp = cfd_room.pop("roomTemperature", None)

    dct = dict(raw.get("dctwinConfig") or {})
    case_path = dct.pop("casePath", "") or ""

    models = raw.get("models")
    construction = []
    if isinstance(models, list):
        for m in models:
            # mirror JS `{ ...m, fromModel: ... }`: a non-dict (e.g. null) spreads to {}
            mm = copy.deepcopy(m) if isinstance(m, dict) else {}
            mm.setdefault("fromModel", None)
            construction.append(mm)

    v3 = {
        "schemaVersion": SCHEMA_VERSION,
        "meta": {
            "projectName": raw.get("projectName") if isinstance(raw.get("projectName"), str) else "",
            "exportFileName": raw.get("exportFileName") if isinstance(raw.get("exportFileName"), str) else "default",
            "inputUnit": raw.get("inputUnit") or "mm",
            "description": "",
            "room": {
                "bounds": raw.get("bounds") or {},
                "polygon": raw.get("roomPolygon") if raw.get("roomPolygon") is not None else None,
                "height": room_height if room_height is not None else 3.6,
                "temperature": room_temp if room_temp is not None else 22,
            },
            "case": {"path": case_path},
        },
        "model": {"racks": {}, "acus": {}},
        "construction": construction,
        "probeData": raw.get("sensorPoints") if isinstance(raw.get("sensorPoints"), list) else [],
        "sensorData": [],
        "simulation": cfd_room,
        "liquidCooling": raw.get("liquidCooling") or {},
        "ui": {
            "displayMode": raw.get("displayMode") or "air",
            "view": raw.get("view") or {},
            "snapFace": raw.get("snapFace") is not False,
            "snapEdge": raw.get("snapEdge") is not False,
            "snapGrid": bool(raw.get("snapGrid")),
            "treeCollapsed": raw.get("treeCollapsed") or {},
            "selection": raw.get("selection") or {},
            "groups": raw.get("groups") if isinstance(raw.get("groups"), list) else [],
            "previewVisibility": raw.get("previewVisibility") or {},
            "previewRegionVisibility": raw.get("previewRegionVisibility") or {},
            "previewRegionReverse": raw.get("previewRegionReverse") or {},
            "rackDetail": {
                "target": raw.get("rackDetailTarget"),
                "returnMode": raw.get("rackDetailReturnMode") or "3d",
                "showContext": bool(raw.get("rackDetailShowContext")),
            },
            "_selectedCategories": raw.get("_selectedCategories") if isinstance(raw.get("_selectedCategories"), list) else [],
            "_anchorCategory": raw.get("_anchorCategory"),
            "_lastClickedCategory": raw.get("_lastClickedCategory"),
            "session": dct,
        },
    }
    for k, val in raw.items():
        if k not in _HANDLED and k not in v3:
            v3[k] = val
    return v3


def load_cfdproj(path) -> dict:
    """Load a .cfdproj from disk, auto-migrating v2/legacy -> v3 in memory.
    Use this everywhere Python reads a .cfdproj structurally."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return migrate_v2_to_v3(data)


# =============================================================================
# v3 -> v4 migration — Python mirror of the v3->v4 section of
# src/frontend/shared/js/cfdproj_migrate.js (SOURCE OF TRUTH; ported verbatim).
# A parity test (test_cfdproj_migrate_v4_parity) asserts this module and the JS
# migrator are VALUE-identical (value-parity, decided 2026-06-12): canonical JSON
# after number-representation normalization. Key insertion order does not matter,
# and JS/Python serializers may differ on number text (e.g. sci-notation); the
# whole-number-float collapse in _strip_undef keeps the common case byte-clean.
# -----------------------------------------------------------------------------
# Schema constants below are copied VERBATIM from
# src/frontend/shared/js/cfdproj_schema.js (the SCHEMA source of truth):
#   SCHEMA_VERSION, BUCKETS, BUCKET_BY_KIND, MESH_KEYS, SIM_KEYS.
# =============================================================================

CFDPROJ_V4_SCHEMA_VERSION = 4

# Canonical serialization order of constructions buckets (cfdproj_schema.js BUCKETS).
_V4_BUCKETS = [
    "boxes", "customMeshes", "surfaces", "walls", "partitionWalls", "baffles",
    "floors", "racks", "rows", "servers", "acus", "cdus", "pipeSegments",
    "pipeJunctions", "pipes", "sensors", "liquidFlowNetworks", "heatGains",
]

# v3 construction[].kind -> V4 bucket (cfdproj_schema.js BUCKET_BY_KIND).
_V4_BUCKET_BY_KIND = {
    "box": "boxes", "custom": "customMeshes", "wall": "walls",
    "partitionWall": "partitionWalls", "baffle": "baffles", "floor": "floors",
    "rack": "racks", "acu": "acus", "cdu": "cdus", "pipe_segment": "pipeSegments",
    "pipe_junction": "pipeJunctions",
    "pipe": "pipes",
    "heat_source": "boxes", "crh": "boxes", "cold_plate": "boxes",
}

# v3 state.simulation key split (cfdproj_schema.js MESH_KEYS / SIM_KEYS).
_V4_MESH_KEYS = [
    "meshBaseSize", "meshDetectBaseSize", "refinementLevel", "rackRegionLevel",
    "wallBafflesRefinementLevel", "floorRefinementLevel", "floorFeatureLevel",
    "acuRefinementLevel", "boxRefinementLevel", "defaultFeatureLevel",
    "acuFeatureLevel", "boxFeatureLevel", "baffleFeatureLevel",
    "parallelMesh", "meshProcess", "maxGlobalCells",
    "blockMeshBounds", "blockMeshEditActive", "blockMeshMoveMode",
    "refinementZones", "locationInMesh", "locInMeshVisible",
    "meshMode", "meshEngine", "firstCellSize", "meshTimeLimitSeconds",
    "weldTolerance", "firstLayerMinFraction", "firstLayerMax", "firstLayerSealGap", "rackCellMinSize",
]
_V4_SIM_KEYS = [
    "solverType", "solverPreset", "endTime", "writeInterval", "endTimeSeconds",
    "deltaT", "maxDeltaT", "maxCo", "adjustTimeStep", "writeIntervalSeconds",
    "nOuterCorrectors", "nCorrectors", "ddtScheme", "crankNicolsonCoeff",
    "solveProcess", "parallelSolve", "gravityX", "gravityY", "gravityZ",
    "residualControl", "schemes", "relaxationFactors",
    "nNonOrthogonalCorrectors", "nonOrthoCorrection", "doublePrecisionWrite",
    "writePrecision", "startFrom",
]


# -- JS-undefined semantics ---------------------------------------------------
# JS object literals set keys to `undefined` when a destructured-but-absent value
# is assigned; `JSON.stringify` then drops those keys (real `null` is kept). We
# model JS `undefined` with a sentinel and strip it at the end of migration, so
# Python `None` faithfully stands in only for real JSON `null`.
class _Undef:
    __slots__ = ()

    def __repr__(self):  # pragma: no cover - debug aid only
        return "<undef>"


_UNDEF = _Undef()


def _get(d, key):
    """JS member access `obj.key`: missing -> undefined sentinel (not None)."""
    return d.get(key, _UNDEF) if isinstance(d, dict) else _UNDEF


def _is_undef(v):
    return v is _UNDEF


def _not_null(v):
    """JS `v != null` -> True unless v is null(None) or undefined."""
    return v is not None and v is not _UNDEF


def _js_truthy(v):
    """Mirror JS `if (v)`: falsy only for undefined/null/false/0/NaN/''.
    Crucially, empty {} and [] are TRUTHY in JS (Python treats them as falsy),
    so the old 'present-and-nonempty' idiom dropped empty containers that JS
    keeps. For every non-container value this matches that idiom exactly."""
    if v is _UNDEF or v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0:
        return False
    if isinstance(v, str) and v == "":
        return False
    return True


def _strip_undef(value):
    """Recursively drop dict keys whose value is the undefined sentinel and any
    undefined entries from lists, mirroring JSON.stringify of a JS structure.
    (JSON.stringify renders array `undefined` as `null`; none of the v3->v4
    transformers place undefined into arrays, so list entries are recursed only.)"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if v is _UNDEF:
                continue
            out[k] = _strip_undef(v)
        return out
    if isinstance(value, list):
        return [_strip_undef(v) for v in value]
    # Collapse integer-valued floats to int so the Python writer matches JS,
    # which has no int/float distinction (JSON.stringify(7.0) -> "7"). This keeps
    # the value-parity contract byte-clean for the common whole-number case;
    # genuinely fractional values are untouched. bool is not a float, so True/
    # False pass through; inf/nan report is_integer() == False, so they are safe.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# -- shared helpers (mirror cfdproj_migrate.js) -------------------------------

def _v4_empty_models():
    return {
        "curveModels": {},
        "geometryModels": {"boxes": {}, "racks": {}, "servers": {}, "acus": {}, "cdus": {},
                           "coldPlates": {}, "chips": {}, "pipes": {}, "surfaces": {}},
        "coolingModels": {"servers": {}, "cdus": {}, "coldPlates": {}, "heatExchangers": {},
                          "fans": {}, "pumps": {}, "pipes": {}, "valves": {}},
        "powerModels": {},
        "controlModels": {},
        "materialModels": {"fluids": {}},
    }


def _v4_empty_constructions():
    c = {"raisedFloor": None, "falseCeiling": None}
    for b in _V4_BUCKETS:
        c[b] = {}
    return c


def _v4_empty_document():
    return {
        "schemaVersion": CFDPROJ_V4_SCHEMA_VERSION,
        "uid": "",
        "name": "",
        "meta": {"projectName": "", "exportFileName": "", "description": "", "inputUnit": "m", "casePath": ""},
        "geometry": {"height": 3, "plane": None, "tileSize": 0.6, "temperature": 22, "bounds": None},
        "models": _v4_empty_models(),
        "constructions": _v4_empty_constructions(),
        "instrumentation": {"probes": {}, "measurements": {}},
        "runDefaults": {"meshConfig": {}, "simulationConfig": {},
                        "liquid": {"enabled": False, "solver": {}, "inheritResults": False}},
    }


def _v4_sanitize_uid(s):
    # JS: String(s || 'untitled').replace(/[^A-Za-z0-9_]/g, '_')
    base = str(s) if s else "untitled"
    return re.sub(r"[^A-Za-z0-9_]", "_", base)


def _v4_key_by_name(arr):
    out = {}
    for item in (arr if isinstance(arr, list) else []):
        if isinstance(item, dict) and item.get("name") is not None:
            out[item["name"]] = item
    return out


def _v4_split_simulation(sim):
    mesh_config, simulation_config = {}, {}
    for k, v in (sim if isinstance(sim, dict) else {}).items():
        (mesh_config if k in _V4_MESH_KEYS else simulation_config)[k] = v
    return mesh_config, simulation_config


def _v4_faces_list(faces):
    # Real cfdproj stores construction `faces` as an object keyed by face id;
    # some fixtures use an array. Normalize to a flat list of face entries.
    if isinstance(faces, list):
        return faces
    if isinstance(faces, dict):
        return list(faces.values())
    return []


def _v4_distribute_patch_physics(faces, patch_physics):
    left = dict(patch_physics) if isinstance(patch_physics, dict) else {}
    for face in _v4_faces_list(faces):
        patches = face.get("patches") if isinstance(face, dict) else None
        for patch in (patches if isinstance(patches, list) else []):
            if not isinstance(patch, dict):
                continue
            pid = patch.get("id")
            if pid is not None and pid in left:
                phys = left.pop(pid)
                phys = phys if isinstance(phys, dict) else {}
                perforated = phys.get("perforated", _UNDEF)
                rest = {k: v for k, v in phys.items() if k != "perforated"}
                patch["physics"] = rest
                if _js_truthy(perforated):
                    patch["perforation"] = perforated
    return left


def _v4_common(m):
    consumed = {"id", "kind", "name", "enabled", "opacity", "showAnchorMarker",
                "anchor", "base", "transform", "faces", "cutouts", "cfd"}
    mid = _get(m, "id")
    name = _get(m, "name")
    enabled = _get(m, "enabled")
    opacity = _get(m, "opacity")
    show_anchor = _get(m, "showAnchorMarker")
    anchor = _get(m, "anchor")
    base = _get(m, "base")
    transform = _get(m, "transform")
    faces = _get(m, "faces")
    cutouts = _get(m, "cutouts")
    cfd = _get(m, "cfd")
    rest = {k: v for k, v in m.items() if k not in consumed}

    # JS: `anchor ? {x:anchor.x,...} : {x:0,y:0,z:0}` and `if (base) size = {...}`.
    # JS treats {} as truthy, and a missing member reads as undefined (stripped by
    # JSON.stringify). Mirror both: relax to isinstance(dict) (empty {} included)
    # and default missing keys to _UNDEF (stripped) rather than None (kept as null).
    geometry = {"location": ({"x": anchor.get("x", _UNDEF), "y": anchor.get("y", _UNDEF),
                              "z": anchor.get("z", _UNDEF)}
                             if isinstance(anchor, dict)
                             else {"x": 0, "y": 0, "z": 0})}
    if isinstance(base, dict):
        geometry["size"] = {"x": base.get("W", _UNDEF), "y": base.get("H", _UNDEF),
                            "z": base.get("D", _UNDEF)}
    if isinstance(transform, dict) and transform and transform.get("quaternion") is not None:
        geometry["quaternion"] = transform.get("quaternion")
    if isinstance(transform, dict) and transform and transform.get("scale") is not None:
        geometry["scale"] = transform.get("scale")
    if faces is not _UNDEF:
        geometry["faces"] = faces
    if cutouts is not _UNDEF:
        geometry["cutouts"] = cutouts

    meta = {
        "name": name if name is not None and name is not _UNDEF else mid,
        "enabled": enabled is not False,
        "description": "", "tags": [],
        "display": {"opacity": opacity, "showAnchorMarker": show_anchor},
    }
    out = {"uid": mid, "geometry": geometry, "cooling": {}, "meta": meta}
    return out, (cfd if isinstance(cfd, dict) and cfd else ({} if cfd is _UNDEF or cfd is None else cfd)), rest


def _split(d, *keys):
    """Mirror JS `const {a, b, ...rest} = d`. Returns (a_val, b_val, ..., rest)
    where each extracted value uses the undefined sentinel if absent, and rest is
    a copy without the named keys."""
    src = d if isinstance(d, dict) else {}
    vals = [src.get(k, _UNDEF) for k in keys]
    rest = {k: v for k, v in src.items() if k not in keys}
    return (*vals, rest)


def _v4_box_like(m, legacy_kind=None):
    out, cfd, rest = _v4_common(m)
    if legacy_kind:
        out["meta"]["legacyKind"] = legacy_kind
    heat_source, crh, cold_plate, rest2 = _split(rest, "heatSource", "crh", "coldPlate")
    if _js_truthy(heat_source):
        out["cooling"]["boundaryMode"] = ("fixedTemperature"
                                          if heat_source.get("heatMode") == "temperature" else "power")
        if _not_null(heat_source.get("totalPower")):
            out["cooling"]["totalPowerW"] = heat_source["totalPower"]
        if _not_null(heat_source.get("fixedTemperature")):
            out["cooling"]["fixedTemperatureC"] = heat_source["fixedTemperature"]
        hs_rest = {k: v for k, v in heat_source.items()
                   if k not in ("heatMode", "totalPower", "fixedTemperature")}
        out["cooling"].update(hs_rest)
    if _js_truthy(crh):
        out["crh"] = crh
    if _js_truthy(cold_plate):
        out["coldPlate"] = cold_plate
    role, patch_physics, cfd_rest = _split(cfd, "role", "patchPhysics")
    leftover = _v4_distribute_patch_physics(out["geometry"].get("faces"),
                                            patch_physics if patch_physics is not _UNDEF else None)
    if leftover:
        out["cooling"]["unboundPatchPhysics"] = leftover
    out["cooling"].update(cfd_rest)
    out.update(rest2)
    return out


def _v4_normalize_default_bc(default_bc):
    # Real v3 floors store defaultBC as an object {type:'wall'}; some fixtures use
    # the bare string. Normalize to the string boundary type either way.
    if isinstance(default_bc, dict) and not isinstance(default_bc, list):
        return default_bc.get("type") or "wall"
    if default_bc is _UNDEF or default_bc is None:
        return "wall"
    return default_bc or "wall"


def _v4_floor(m):
    out, cfd, rest = _v4_common(m)
    (role, room_boundary_type, default_bc, default_bc_perforated, default_bc_seed,
     default_bc_seeds, openings, patch_physics, cfd_rest) = _split(
        cfd, "role", "roomBoundaryType", "defaultBC", "defaultBCPerforated",
        "defaultBCSeedConfig", "defaultBCSeedConfigs", "openings", "patchPhysics")
    out["cooling"]["roomBoundaryLocation"] = (room_boundary_type
                                              if (_js_truthy(room_boundary_type))
                                              else "none")
    out["cooling"]["defaultBoundaryType"] = _v4_normalize_default_bc(default_bc)
    if _js_truthy(default_bc_perforated):
        out["cooling"]["defaultPerforation"] = default_bc_perforated
    if _js_truthy(default_bc_seed):
        out["cooling"]["defaultSeedConfig"] = default_bc_seed
    if _js_truthy(default_bc_seeds):
        out["cooling"]["defaultSeedConfigs"] = default_bc_seeds
    op_list = openings if isinstance(openings, list) else []
    new_openings = []
    for op in op_list:
        op = op if isinstance(op, dict) else {}
        perforated = op.get("perforated", _UNDEF)
        op_rest = {k: v for k, v in op.items() if k != "perforated"}
        if _js_truthy(perforated):
            op_rest["perforation"] = perforated
            new_openings.append(op_rest)
        else:
            new_openings.append(op_rest)
    out["geometry"]["openings"] = new_openings
    leftover = _v4_distribute_patch_physics(out["geometry"].get("faces"),
                                            patch_physics if patch_physics is not _UNDEF else None)
    if leftover:
        out["cooling"]["unboundPatchPhysics"] = leftover
    out["cooling"].update(cfd_rest)
    out.update(rest)
    return out


def _v4_planar(m):  # wall, baffle, partitionWall
    out, cfd, rest = _v4_common(m)
    (role, baffle_type, perforated, patch_physics, seed_config,
     openings, holes, leakage, cfd_rest) = _split(
        cfd, "role", "baffleType", "perforated", "patchPhysics", "seedConfig",
        "openings", "holes", "leakage")
    if _js_truthy(baffle_type):
        out["cooling"]["type"] = baffle_type
    if _js_truthy(perforated):
        out["cooling"]["perforation"] = perforated
    if _js_truthy(seed_config):
        out["cooling"]["seedConfig"] = seed_config
    if _js_truthy(leakage):
        out["cooling"]["leakage"] = leakage
    if _js_truthy(openings):
        out["geometry"]["openings"] = openings
    if _js_truthy(holes):
        out["geometry"]["openings"] = holes
    leftover = _v4_distribute_patch_physics(out["geometry"].get("faces"),
                                            patch_physics if patch_physics is not _UNDEF else None)
    if leftover:
        out["cooling"]["unboundPatchPhysics"] = leftover
    out["cooling"].update(cfd_rest)
    out.update(rest)
    return out


def _v4_rack(m):
    out, cfd, rest = _v4_common(m)
    rack, rest2 = _split(rest, "rack")
    r = rack if isinstance(rack, dict) and rack else {}
    (unit_count, unit_height, slot_width_ratio, cooling_type, liquid_manifold,
     equipments, partitions, inlet_door, outlet_door, blanking_panel_type, rack_rest) = _split(
        r, "unitCount", "unitHeight", "slotWidthRatio", "coolingType", "liquidManifold",
        "equipments", "partitions", "inletDoor", "outletDoor", "blankingPanelType")
    if _not_null(unit_count):
        out["geometry"]["slot"] = unit_count
    if _not_null(unit_height):
        out["geometry"]["slotHeight"] = unit_height
    if _not_null(slot_width_ratio):
        out["geometry"]["slotWidthRatio"] = slot_width_ratio
    faces = out["geometry"].get("faces")
    out["geometry"]["faces"] = faces if isinstance(faces, list) else []
    for uid, door in (("inletDoor", inlet_door), ("outletDoor", outlet_door)):
        # JS `if (!door) continue;` — skip only when door is falsy. An empty {}
        # is TRUTHY in JS, so use _js_truthy (not `not door`, which drops {}).
        if not _js_truthy(door):
            continue
        door = door if isinstance(door, dict) else {}
        perforated = door.get("perforated", _UNDEF)
        door_rest = {k: v for k, v in door.items() if k != "perforated"}
        entry = {"uid": uid, "role": "door"}
        entry.update(door_rest)
        if _js_truthy(perforated):
            entry["perforation"] = perforated
        out["geometry"]["faces"].append(entry)
    role, leakage, physics_list, patch_physics, cfd_rest = _split(
        cfd, "role", "leakage", "servers", "patchPhysics")
    out["cooling"]["type"] = cooling_type if (_js_truthy(cooling_type)) else "air"
    if _js_truthy(liquid_manifold):
        out["cooling"]["liquidManifold"] = liquid_manifold
    if blanking_panel_type is not _UNDEF:
        out["cooling"]["blankingPanelType"] = blanking_panel_type
    lk = leakage if isinstance(leakage, dict) else None
    out["cooling"]["leakageEnabled"] = bool(lk and lk.get("enabled"))
    out["cooling"]["defaultLeakageSigma"] = (lk["sigma"] if (lk and lk.get("sigma") is not None) else 0.05)
    if _js_truthy(leakage):
        lk2 = leakage if isinstance(leakage, dict) else {}
        lk_rest = {k: v for k, v in lk2.items() if k not in ("enabled", "sigma")}
        if lk_rest:
            out["cooling"]["leakageExtra"] = lk_rest
    phys_by_id = {}
    for p in (physics_list if isinstance(physics_list, list) else []):
        if isinstance(p, dict) and p.get("equipmentId") is not None:
            # Key by str() to mirror JS object keys (which coerce a numeric
            # equipmentId to a string); the lookup below uses str(eid). Keeping
            # the raw int here would miss for numeric ids and drop server physics.
            phys_by_id[str(p["equipmentId"])] = p
    servers = {}
    eq_list = equipments if isinstance(equipments, list) else []
    for idx, eq in enumerate(eq_list):
        eq = eq if isinstance(eq, dict) else {}
        eid, name, etype, ekind, start_unit, span_units, eq_liquid, eq_rest = _split(
            eq, "id", "name", "type", "kind", "startUnit", "spanUnits", "liquidCooling")
        uid = str(eid) if (eid is not None and eid is not _UNDEF) else ("srv_" + str(idx))
        phys = phys_by_id.get(uid, {})
        phys = phys if isinstance(phys, dict) else {}
        phys_rest = {k: v for k, v in phys.items() if k != "equipmentId"}
        cold_plates = {}
        pairs = (eq_liquid.get("cpCuPairs") if isinstance(eq_liquid, dict) else None)
        pairs = pairs if isinstance(pairs, list) else []
        for pidx, pair in enumerate(pairs):
            pair = pair if isinstance(pair, dict) else {}
            if pair.get("id") is not None:
                cp_uid = str(pair["id"])
            elif pair.get("name") is not None:
                cp_uid = str(pair["name"])
            else:
                cp_uid = "cp_" + str(pidx)
            entry = {"uid": cp_uid}
            entry.update(pair)
            cold_plates[cp_uid] = entry
        srv = {
            "uid": uid,
            "type": (etype if (_js_truthy(etype))
                     else (ekind if (_js_truthy(ekind)) else "fanServer")),
            "geometry": {"slotPosition": start_unit, "slotOccupation": span_units},
            "cooling": phys_rest,
            "constructions": {"chips": {}, "coldPlates": cold_plates},
            "meta": {"name": (name if (name is not None and name is not _UNDEF) else uid), "enabled": True},
        }
        if _js_truthy(eq_liquid):
            srv["liquidCooling"] = eq_liquid
        srv.update(eq_rest)
        servers[uid] = srv
    out["constructions"] = {"servers": servers,
                            "partitions": partitions if isinstance(partitions, list) else [],
                            "rdhx": None}
    leftover = _v4_distribute_patch_physics(out["geometry"].get("faces"),
                                            patch_physics if patch_physics is not _UNDEF else None)
    if leftover:
        out["cooling"]["unboundPatchPhysics"] = leftover
    out["cooling"].update(cfd_rest)
    out.update(rack_rest)
    out.update(rest2)
    return out


def _v4_acu(m):
    out, cfd, rest = _v4_common(m)
    role, patch_physics, cfd_rest = _split(cfd, "role", "patchPhysics")
    pp = patch_physics if isinstance(patch_physics, dict) else {}
    supply_faces, return_faces = [], []
    for face in _v4_faces_list(out["geometry"].get("faces")):
        face = face if isinstance(face, dict) else {}
        patches = face.get("patches")
        for patch in (patches if isinstance(patches, list) else []):
            patch = patch if isinstance(patch, dict) else {}
            phys = pp.get(patch.get("id"))
            if not phys:
                continue
            if isinstance(phys, dict) and phys.get("role") == "supply":
                supply_faces.append({"faceId": face.get("id"), "patchId": patch.get("id")})
            if isinstance(phys, dict) and phys.get("role") == "return":
                return_faces.append({"faceId": face.get("id"), "patchId": patch.get("id")})
    leftover = _v4_distribute_patch_physics(out["geometry"].get("faces"),
                                            patch_physics if patch_physics is not _UNDEF else None)
    if leftover:
        out["cooling"]["unboundPatchPhysics"] = leftover
    out["geometry"]["supplyFaces"] = supply_faces
    out["geometry"]["returnFaces"] = return_faces
    out["constructions"] = {"heatExchanger": None, "fans": {}}
    out["cooling"].update(cfd_rest)
    out.update(rest)
    return out


def _v4_cdu(m):
    out, cfd, rest = _v4_common(m)
    liquid_cooling, rest2 = _split(rest, "liquidCooling")
    lc = liquid_cooling if isinstance(liquid_cooling, dict) and liquid_cooling else {}
    (supply_temperature, supply_pressure, total_flow_rate, served_racks,
     advanced_mode, advanced, lc_rest) = _split(
        lc, "supplyTemperature", "supplyPressure", "totalFlowRate", "servedRacks",
        "advancedMode", "advanced")
    if _not_null(supply_temperature):
        out["cooling"]["supplyTemperatureC"] = supply_temperature
    if _not_null(supply_pressure):
        out["cooling"]["supplyPressureKpa"] = supply_pressure
    if _not_null(total_flow_rate):
        out["cooling"]["totalFlowRate"] = total_flow_rate
    if advanced_mode is not _UNDEF:
        out["cooling"]["advancedMode"] = advanced_mode
    out["cooling"].update(lc_rest)
    adv = advanced if isinstance(advanced, dict) and advanced else {}
    out["constructions"] = {
        "heatExchanger": adv.get("heatExchanger") if adv.get("heatExchanger") is not None else None,
        "pump": adv.get("pump") if adv.get("pump") is not None else None,
        "valve": adv.get("valve") if adv.get("valve") is not None else None,
    }
    out["meta"]["racks"] = served_racks if isinstance(served_racks, list) else []
    role, cfd_rest = _split(cfd, "role")
    out["cooling"].update(cfd_rest)
    out.update(rest2)
    return out


def _v4_pipe(m):
    out, cfd, rest = _v4_common(m)
    pipe, rest2 = _split(rest, "pipe")
    p = pipe if isinstance(pipe, dict) and pipe else {}
    (spec, diameter, waypoints, color, valves, start_port_id, end_port_id,
     blind_ends, junctions, pipe_rest) = _split(
        p, "spec", "diameter", "waypoints", "color", "valves",
        "startPortId", "endPortId", "blindEnds", "junctions")
    if _js_truthy(waypoints):
        out["geometry"]["waypoints"] = waypoints
    if _not_null(diameter):
        out["geometry"]["diameterM"] = diameter
    if _not_null(spec):
        out["cooling"]["spec"] = spec
    out["cooling"]["loopRole"] = color if (_js_truthy(color)) else "unconnected"
    if _js_truthy(valves):
        out["cooling"]["valves"] = valves
    out["cooling"]["connection"] = {
        "startPortId": start_port_id if _not_null(start_port_id) else None,
        "endPortId": end_port_id if _not_null(end_port_id) else None,
        "blindEnds": blind_ends if (_js_truthy(blind_ends)) else {"start": False, "end": False},
        "junctions": junctions if (_js_truthy(junctions)) else [],
    }
    out["cooling"].update(pipe_rest)
    role, cfd_rest = _split(cfd, "role")
    out["cooling"].update(cfd_rest)
    out.update(rest2)
    return out


# Air-side pipe / duct (kind 'pipe', bucket 'pipes'): a swept solid along
# `geometry.waypoints` (LOCAL to geometry.location). Round pipes carry
# diameterM, rectangular ones widthM/heightM; `joins` pairs snapped ends.
def _v4_air_pipe(m):
    out, cfd, rest = _v4_common(m)
    pipe, rest2 = _split(rest, "pipe")
    p = pipe if isinstance(pipe, dict) and pipe else {}
    shape, diameter_m, width_m, height_m, waypoints, joins, pipe_rest = _split(
        p, "shape", "diameterM", "widthM", "heightM", "waypoints", "joins")
    out["geometry"]["waypoints"] = waypoints if isinstance(waypoints, list) else []
    out["geometry"]["shape"] = "rect" if shape == "rect" else "round"
    out["geometry"]["diameterM"] = diameter_m if _not_null(diameter_m) else 0.15
    out["geometry"]["widthM"] = width_m if _not_null(width_m) else 0.2
    out["geometry"]["heightM"] = height_m if _not_null(height_m) else 0.2
    out["geometry"]["joins"] = joins if isinstance(joins, list) else []
    out["cooling"].update(pipe_rest)
    role, cfd_rest = _split(cfd, "role")
    out["cooling"].update(cfd_rest)
    out.update(rest2)
    return out


def _v4_junction(m):
    out, cfd, rest = _v4_common(m)
    junction, rest2 = _split(rest, "junction")
    if _js_truthy(junction):
        jn = junction if isinstance(junction, dict) else {}
        jtype, loss_coeff, connected_pipes, j_rest = _split(jn, "type", "lossCoeff", "connectedPipes")
        if _not_null(jtype):
            out["cooling"]["type"] = jtype
        if _not_null(loss_coeff):
            out["cooling"]["lossCoeff"] = loss_coeff
        if _js_truthy(connected_pipes):
            out["connectedPipes"] = connected_pipes
        out["cooling"].update(j_rest)
    role, cfd_rest = _split(cfd, "role")
    out["cooling"].update(cfd_rest)
    out.update(rest2)
    return out


def _v4_custom(m):
    out, cfd, rest = _v4_common(m)
    custom_stl, custom_offset, solid_regions, rest2 = _split(
        rest, "customSTLBase64", "customOffset", "solidRegions")
    out["customMesh"] = {
        "stlBase64": custom_stl,
        "offset": custom_offset if (_js_truthy(custom_offset)) else {"x": 0, "y": 0, "z": 0},
        "solidRegions": solid_regions if (_js_truthy(solid_regions)) else [],
    }
    role, patch_physics, cfd_rest = _split(cfd, "role", "patchPhysics")
    leftover = _v4_distribute_patch_physics(out["geometry"].get("faces"),
                                            patch_physics if patch_physics is not _UNDEF else None)
    if leftover:
        out["cooling"]["unboundPatchPhysics"] = leftover
    out["cooling"].update(cfd_rest)
    out.update(rest2)
    return out


def migrate_cfdproj_v3_to_v4(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return raw
    if int(raw.get("schemaVersion") or 0) >= 4:
        return raw

    raw = copy.deepcopy(raw)
    meta3 = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    room = meta3.get("room") if isinstance(meta3.get("room"), dict) else {}
    lc = raw.get("liquidCooling") if isinstance(raw.get("liquidCooling"), dict) else {}
    specs = lc.get("specifications") if isinstance(lc.get("specifications"), dict) else {}

    doc = _v4_empty_document()
    doc["uid"] = raw.get("uid") or ("proj_" + _v4_sanitize_uid(meta3.get("projectName")))
    doc["name"] = meta3.get("projectName") or ""
    case = meta3.get("case") if isinstance(meta3.get("case"), dict) else {}
    doc["meta"] = {
        "projectName": meta3.get("projectName") or "",
        "exportFileName": meta3.get("exportFileName") or "",
        "description": meta3.get("description") or "",
        "inputUnit": meta3.get("inputUnit") or "m",
        "casePath": (case.get("path") if isinstance(case, dict) else "") or "",
    }
    polygon = room.get("polygon") if isinstance(room.get("polygon"), dict) else None
    doc["geometry"] = {
        "height": room["height"] if room.get("height") is not None else 3,
        "plane": (polygon.get("vertices") if polygon else None),
        "tileSize": 0.6,
        "temperature": room["temperature"] if room.get("temperature") is not None else 22,
        "bounds": room["bounds"] if room.get("bounds") is not None else None,
    }
    if polygon and polygon.get("height") is not None and polygon.get("height") != room.get("height"):
        doc["geometry"]["planeHeight"] = polygon.get("height")

    model3 = raw.get("model") if isinstance(raw.get("model"), dict) else {}
    doc["models"]["geometryModels"]["racks"] = model3.get("racks") or {}
    doc["models"]["geometryModels"]["acus"] = model3.get("acus") or {}
    doc["models"]["coolingModels"]["coldPlates"] = _v4_key_by_name(specs.get("coldPlateSpec"))
    doc["models"]["coolingModels"]["pipes"] = _v4_key_by_name(specs.get("pipeSpec"))
    doc["models"]["coolingModels"]["pumps"] = _v4_key_by_name(specs.get("pumpSpec"))
    doc["models"]["coolingModels"]["heatExchangers"] = _v4_key_by_name(specs.get("hxSpec"))
    doc["models"]["coolingModels"]["valves"] = _v4_key_by_name(specs.get("valveSpec"))
    fluid = lc.get("fluid") if isinstance(lc.get("fluid"), dict) else None
    if fluid and fluid.get("type"):
        doc["models"]["materialModels"]["fluids"][fluid["type"]] = fluid

    construction = raw.get("construction") if isinstance(raw.get("construction"), list) else []
    for m in construction:
        if not isinstance(m, dict):
            continue
        kind = m.get("kind")
        bucket = _V4_BUCKET_BY_KIND.get(kind) or "boxes"
        legacy = kind if (_V4_BUCKET_BY_KIND.get(kind) == "boxes" and kind != "box") else None
        if kind == "rack":
            obj = _v4_rack(m)
        elif kind == "acu":
            obj = _v4_acu(m)
        elif kind == "cdu":
            obj = _v4_cdu(m)
        elif kind == "pipe_segment":
            obj = _v4_pipe(m)
        elif kind == "pipe_junction":
            obj = _v4_junction(m)
        elif kind == "pipe":
            obj = _v4_air_pipe(m)
        elif kind == "floor":
            obj = _v4_floor(m)
        elif kind in ("wall", "baffle", "partitionWall"):
            obj = _v4_planar(m)
        elif kind == "custom":
            obj = _v4_custom(m)
        else:
            obj = _v4_box_like(m, legacy)
        doc["constructions"][bucket][obj["uid"]] = obj

    for p in (raw.get("probeData") if isinstance(raw.get("probeData"), list) else []):
        if not isinstance(p, dict):
            continue
        pid, pname, px, py, pz, penabled, p_rest = _split(p, "id", "name", "x", "y", "z", "enabled")
        # Skip id-less probes (mirrors the sensorData guard below and the JS
        # `if (p.id == null) continue`). A missing id would otherwise become an
        # unserializable _UNDEF dict key here / the literal 'undefined' key in JS.
        if pid is _UNDEF or pid is None:
            continue
        entry = {
            "uid": pid,
            "geometry": {"location": {"x": px, "y": py, "z": pz}},
            "meta": {"name": pname, "enabled": penabled is not False},
        }
        entry.update(p_rest)
        doc["instrumentation"]["probes"][pid] = entry
    for s in (raw.get("sensorData") if isinstance(raw.get("sensorData"), list) else []):
        if isinstance(s, dict) and s.get("id") is not None:
            doc["instrumentation"]["measurements"][s["id"]] = s

    mesh_config, simulation_config = _v4_split_simulation(raw.get("simulation"))
    doc["runDefaults"]["meshConfig"] = mesh_config
    doc["runDefaults"]["simulationConfig"] = simulation_config
    doc["runDefaults"]["liquid"] = {
        "enabled": lc.get("enabled") is True,
        "solver": lc.get("solver") or {},
        "inheritResults": lc.get("inheritResults") is True,
    }
    # Emit a liquidFlowNetwork only when liquid cooling is actually enabled, so
    # air cases stay free of phantom networks from a stale piping default.
    piping = lc.get("piping") if isinstance(lc.get("piping"), dict) else None
    if (lc.get("enabled") is True and piping and
            ("autoGenerated" in piping or piping.get("mainPipeSpec") or piping.get("branchPipeSpec"))):
        chains, piping_rest = _split(piping, "chains")
        entry = {"uid": "net_main", "geometry": {}, "meta": {"name": "main", "enabled": True}}
        entry.update(piping_rest)
        doc["constructions"]["liquidFlowNetworks"]["net_main"] = entry

    known = {"schemaVersion", "meta", "model", "construction", "probeData",
             "sensorData", "simulation", "liquidCooling", "ui", "uid"}
    for k, v in raw.items():
        if k not in known:
            doc[k] = v

    return _strip_undef(doc)


def migrate_cfdproj_to_v4(raw: dict) -> dict:
    """Chain the existing v2->v3 migrator (migrate_v2_to_v3) then v3->v4."""
    return migrate_cfdproj_v3_to_v4(migrate_v2_to_v3(raw))


def load_cfdproj_v4(path) -> dict:
    """Load a .cfdproj from disk and migrate all the way to v4 in memory."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return migrate_cfdproj_to_v4(data)
