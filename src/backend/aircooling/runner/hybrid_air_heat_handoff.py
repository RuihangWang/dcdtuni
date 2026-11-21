"""Compile one audited liquid sample into an air-side heat-flux .cfdrun.

The editable source is the same V4 .cfdproj that produced the air manifest.
The original and accepted V3 liquid inputs, result, and runtime receipt are
audited again here: a caller cannot substitute an unaudited heat report. The
derived .cfdrun and ledger are run artifacts, not a new editable model.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re

from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.stl_geometry_index import index_cfdrun_stls
from backend.aircooling.runner.openfoam_patch_area import (
    measure_openfoam_patch_areas, verify_openfoam_mesh_quality,
)
from backend.liquidcooling.hybrid_heat_audit import audit_hybrid_heat
from backend.liquidcooling.liquidrun_dae_schema import parse_dae_manifest


_SERVER_PATH = re.compile(r"^constructions\.racks\.([^.]+)\.servers\.([^.]+)$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _js_number(value: int | float) -> str:
    """Represent a JSON number as JSON.stringify does for project hashes."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("project contains a non-JSON number")
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        raise ValueError("project integer exceeds the JavaScript safe range")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("project contains a non-finite number") from exc
    if not math.isfinite(number):
        raise ValueError("project contains a non-finite number")
    if number == 0:
        return "0"
    if isinstance(value, int):
        return str(value)
    decimal = Decimal(repr(value))
    if 1e-6 <= abs(number) < 1e21:
        return format(decimal, "f").rstrip("0").rstrip(".") if "." in format(decimal, "f") else format(decimal, "f")
    coefficient, exponent = format(decimal.normalize(), "e").split("e")
    integer_exponent = int(exponent)
    return coefficient + "e" + ("+" if integer_exponent >= 0 else "") + str(integer_exponent)


def _js_object_key_order(key: str) -> tuple:
    # JSON.stringify enumerates integer-index object keys ahead of other keys,
    # even after the exporter's recursive Object.keys(...).sort() insertion.
    if re.fullmatch(r"(?:0|[1-9][0-9]*)", key) and int(key) < 2**32 - 1:
        return (0, int(key))
    return (1, key.encode("utf-16-be", "surrogatepass"))


def _js_stable_json(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_js_stable_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("project has a non-string JSON object key")
        ordered = sorted(value, key=_js_object_key_order)
        return "{" + ",".join(_js_stable_json(key) + ":" + _js_stable_json(value[key])
                              for key in ordered) + "}"
    raise ValueError(f"project contains non-JSON value {type(value).__name__}")


def cfdproj_content_hash(project: dict) -> str:
    """Return the React exporter's recursive-key-sorted SHA-256 project hash."""
    if not isinstance(project, dict):
        raise ValueError("project must be a JSON object")
    return hashlib.sha256(_js_stable_json(project).encode("utf-8")).hexdigest()


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _finite(value, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{label} must be {'positive ' if positive else ''}finite")
    return number


def _liquid_server_uids(project: dict) -> set[str]:
    racks = _object(_object(project.get("constructions"), "project.constructions").get("racks"),
                    "project.constructions.racks")
    found: set[str] = set()
    for rack_uid, rack in racks.items():
        if not isinstance(rack_uid, str) or not rack_uid or "." in rack_uid:
            raise ValueError("rack UID cannot be represented by a server objectPath")
        rack = _object(rack, f"rack {rack_uid}")
        if rack.get("uid") != rack_uid:
            raise ValueError(f"rack {rack_uid} UID mismatch")
        servers = _object(_object(rack.get("constructions"), f"rack {rack_uid}.constructions").get("servers"),
                          f"rack {rack_uid}.servers")
        for server_uid, server in servers.items():
            server = _object(server, f"server {rack_uid}/{server_uid}")
            if server.get("uid") != server_uid or not server_uid or "." in server_uid:
                raise ValueError(f"server {rack_uid}/{server_uid} UID mismatch")
            if server.get("type") == "liquidServer":
                found.add(f"{rack_uid}/{server_uid}")
    if not found:
        raise ValueError("project has no liquidServer to couple")
    return found


def _validated_budget(project: dict, expected_servers: set[str]) -> tuple[dict, dict]:
    budget = _object(_object(project.get("runDefaults"), "project.runDefaults").get("hybridHeatBudget"),
                     "runDefaults.hybridHeatBudget")
    if type(budget.get("version")) is int and budget["version"] == 2:
        if set(budget) != {"version", "source", "source_reference", "load_mapping", "air_transfer", "servers"}:
            raise ValueError("hybridHeatBudget requires strict version 2 fields")
        if (budget["source"] not in ("mock", "vendor", "calibrated") or
                not isinstance(budget["source_reference"], str) or not budget["source_reference"].strip() or
                budget["load_mapping"] != "server_power_times_shared_heat_multiplier" or
                budget["air_transfer"] != "fan_enthalpy"):
            raise ValueError("fan hybrid budget requires explicit source and fan_enthalpy mapping")
        servers = _object(budget["servers"], "hybridHeatBudget.servers")
        if set(servers) != expected_servers:
            raise ValueError("fan hybrid budget must cover exactly the liquidServer UIDs")
        for uid, entry in servers.items():
            entry = _object(entry, f"hybridHeatBudget.servers[{uid}]")
            if set(entry) != {"total_it_power_W"}:
                raise ValueError(f"server {uid} requires only total_it_power_W")
            _finite(entry["total_it_power_W"], f"server {uid}.total_it_power_W", positive=True)
        return budget, {}
    if (set(budget) != {"version", "source", "source_reference", "load_mapping",
                        "air_surface_allocation", "servers"} or
            type(budget["version"]) is not int or budget["version"] != 1):
        raise ValueError("hybridHeatBudget requires strict version 1 fields")
    if budget["source"] not in ("mock", "vendor", "calibrated") or not isinstance(budget["source_reference"], str) or not budget["source_reference"].strip():
        raise ValueError("hybridHeatBudget requires an explicit valid source")
    if budget["load_mapping"] != "shared_heat_multiplier":
        raise ValueError("hybridHeatBudget load_mapping must be shared_heat_multiplier")
    allocation = _object(budget["air_surface_allocation"], "air_surface_allocation")
    if set(allocation) != {"front_fraction", "rear_fraction"}:
        raise ValueError("air_surface_allocation requires only front_fraction and rear_fraction")
    front = _finite(allocation["front_fraction"], "front_fraction")
    rear = _finite(allocation["rear_fraction"], "rear_fraction")
    if front < 0 or rear < 0 or abs(front + rear - 1) > 1e-12:
        raise ValueError("air_surface_allocation fractions must be nonnegative and sum to 1")
    servers = _object(budget["servers"], "hybridHeatBudget.servers")
    if set(servers) != expected_servers:
        raise ValueError("hybridHeatBudget must cover exactly the liquidServer UIDs")
    for uid, entry in servers.items():
        entry = _object(entry, f"hybridHeatBudget.servers[{uid}]")
        if set(entry) != {"total_it_power_W"}:
            raise ValueError(f"hybridHeatBudget.servers[{uid}] requires only total_it_power_W")
        _finite(entry["total_it_power_W"], f"hybridHeatBudget.servers[{uid}].total_it_power_W",
                positive=True)
    return budget, {"front": front, "rear": rear}


def _face_for_patch(name: str) -> str | None:
    if name.endswith("_front"):
        return "front"
    if re.search(r"_outlet_p[0-9]+$", name):
        return "rear"
    return None


def compile_hybrid_air_heat(
    project: dict,
    air_cfdrun: dict,
    tri_surface_dir: str | Path,
    *,
    original_liquidrun: dict,
    executed_liquidrun: dict,
    liquid_result: dict,
    accepted_provenance: dict,
    sample_index: int,
) -> tuple[dict, dict]:
    """Return a derived strict V2 .cfdrun and conservation/provenance sidecar.

    Each liquidServer needs exactly one exported ``_front`` and one
    ``_outlet_pN`` thermal face. All its former 35°C fixed faces are replaced:
    a positive allocation becomes flux and zero allocation becomes a wall.
    The W/m² denominator is the actual grouped ASCII STL triangle area.
    """
    project = _object(project, "project")
    # V5 adds transient tracks and saves the fields they own stripped. Coupling
    # reads only static structure (server types, runDefaults), which V5 keeps as
    # is, and hashes the document as saved, as the React exporter does.
    if project.get("schemaVersion") not in (4, 5) or not isinstance(project.get("uid"), str) or not project["uid"]:
        raise ValueError("hybrid coupling requires a V4/V5 project with stable UID")
    expected_servers = _liquid_server_uids(project)
    budget, fractions = _validated_budget(project, expected_servers)
    air_model = CfdrunV2.model_validate(air_cfdrun)
    expected_stls = air_model.geometryExport.stlFiles
    if not expected_stls or any(
        not isinstance(item.contentHash, str) or not _HASH.fullmatch(item.contentHash)
        for item in expected_stls.values()
    ):
        raise ValueError("hybrid air .cfdrun requires export-time STL content hashes in geometryExport.stlFiles")
    source = air_model.sourceProject
    content_hash = cfdproj_content_hash(project)
    if (source is None or source.uid != project["uid"] or source.schemaVersion != project["schemaVersion"] or
            not isinstance(source.contentHash, str) or not _HASH.fullmatch(source.contentHash) or
            source.contentHash != content_hash):
        raise ValueError("air sourceProject UID/contentHash does not match this .cfdproj revision")
    original_liquid = parse_dae_manifest(original_liquidrun)
    if original_liquid.source_project.sha256 != content_hash:
        raise ValueError("liquid source_project.sha256 does not match this .cfdproj revision")
    if isinstance(sample_index, bool) or not isinstance(sample_index, int):
        raise ValueError("sample_index must be an integer")

    audit = audit_hybrid_heat(
        executed_liquidrun, liquid_result, project["uid"],
        original_liquidrun=original_liquidrun, accepted_provenance=accepted_provenance)
    if not audit["handoff_ready"]:
        raise ValueError("liquid heat audit is incomplete or not ready for handoff")
    if audit["uncooled_server_uids"] or {row["object_binding_uid"] for row in audit["servers"]} != expected_servers:
        raise ValueError("liquid audit server UIDs do not cover exactly the project liquidServer UIDs")
    if not 0 <= sample_index < len(audit["time_s"]):
        raise ValueError("sample_index is outside accepted liquid samples")
    multiplier = audit["heat_multipliers"][sample_index]
    if multiplier < 0:
        raise ValueError("accepted heat multiplier is negative")

    if budget["version"] == 2:
        from backend.aircooling.runner.hybrid_fan_heat_handoff import compile_fan_heat
        return compile_fan_heat(project, air_cfdrun, tri_surface_dir, budget, audit,
                                expected_servers, content_hash, sample_index)

    bindings: dict[str, dict[str, tuple[str, dict]]] = {uid: {} for uid in expected_servers}
    target_names: set[str] = set()
    for key, value in air_cfdrun["boundaryBindings"].items():
        path = value.get("objectPath")
        match = _SERVER_PATH.fullmatch(path) if isinstance(path, str) else None
        uid = f"{match[1]}/{match[2]}" if match else None
        if uid not in expected_servers:
            continue
        if value.get("bcType") in ("nonfan_heat_flux", "heat_source_flux", "fixedTemperature"):
            raise ValueError(f"server {uid} has existing heat boundary that would double count air heat")
        if value.get("bcType") != "nonfan_fixed_temp":
            continue
        name = value.get("solidName")
        face = _face_for_patch(name) if isinstance(name, str) else None
        if face is None or face in bindings[uid] or value.get("patchName") != name or name in target_names:
            raise ValueError(f"server {uid} has duplicate or unmapped thermal front/rear face")
        bindings[uid][face] = (key, value)
        target_names.add(name)
    if any(set(faces) != {"front", "rear"} for faces in bindings.values()):
        raise ValueError("every liquidServer requires exactly one fixed-temperature front and rear thermal face")
    for value in air_cfdrun.get("sourceTerms", {}).values():
        match = _SERVER_PATH.fullmatch(value.get("objectPath", "")) if isinstance(value.get("objectPath"), str) else None
        if match and f"{match[1]}/{match[2]}" in expected_servers:
            raise ValueError("liquidServer sourceTerm would double count the air heat budget")

    compiled = index_cfdrun_stls(air_cfdrun, tri_surface_dir, target_solid_names=target_names)
    geometry_by_name: dict[str, dict] = {}
    for value in compiled["geometryExport"]["solids"].values():
        if value["solidName"] in target_names:
            if value["solidName"] in geometry_by_name:
                raise ValueError("thermal solid has duplicate geometryExport bindings")
            geometry_by_name[value["solidName"]] = value
    if set(geometry_by_name) != target_names:
        raise ValueError("thermal solid lacks geometryExport/STL identity")

    server_audit = {row["object_binding_uid"]: row for row in audit["servers"]}
    server_rows = []
    totals = {"total_it_W": 0.0, "cp_input_W": 0.0, "liquid_removed_W": 0.0,
              "solid_storage_rate_W": 0.0, "air_residual_W": 0.0, "face_heat_W": 0.0}
    for uid in sorted(expected_servers):
        sample = server_audit[uid]["samples"][sample_index]
        if not sample["checked"] or sample["solid_storage_rate_W"] is None:
            raise ValueError(f"server {uid} has an unchecked liquid storage rate")
        total = _finite(budget["servers"][uid]["total_it_power_W"], "total_it_power_W") * multiplier
        removed = _finite(sample["liquid_removed_W"], "liquid_removed_W")
        storage = _finite(sample["solid_storage_rate_W"], "solid_storage_rate_W")
        cp_input = _finite(sample["cp_input_W"], "cp_input_W")
        air_heat = total - removed - storage
        tolerance = 1e-6 + 1e-8 * max(1., abs(total), abs(removed), abs(storage))
        if air_heat < -tolerance:
            raise ValueError(f"server {uid} total IT heat is insufficient: negative air residual {air_heat:g} W")
        if air_heat < 0:
            air_heat = 0.0  # only tolerated roundoff; preserve the signed closure below
        face_rows = []
        for face in ("front", "rear"):
            key, binding = bindings[uid][face]
            name = binding["solidName"]
            solid = geometry_by_name[name]
            if solid["objectPath"] != binding["objectPath"] or solid["patchName"] != name:
                raise ValueError(f"server {uid} thermal solid and boundary objectPath differ")
            area = _finite(solid["areaM2"], f"solid {name}.areaM2", positive=True)
            heat = air_heat * fractions[face]
            flux = heat / area
            boundary = compiled["boundaryBindings"][key]
            boundary["bcType"] = "nonfan_heat_flux" if heat > 0 else "wall"
            boundary["role"] = boundary["bcType"]
            boundary["parameters"] = {"heatFluxWm2": flux} if heat > 0 else {}
            solid["role"] = boundary["bcType"]
            file_id = solid["stlFileId"]
            stl = compiled["geometryExport"]["stlFiles"][file_id]
            face_rows.append({"face": face, "patch_name": name, "solid_name": name,
                              "object_path": binding["objectPath"],
                              "allocation_fraction": fractions[face], "area_m2": area,
                              "heat_W": heat, "heat_flux_W_m2": flux,
                              "boundary_type": boundary["bcType"],
                              "stl_file_id": file_id, "stl_path": stl["path"],
                              "stl_sha256": stl["contentHash"]})
            totals["face_heat_W"] += heat
        row = {"server_uid": uid, "baseline_total_it_W": budget["servers"][uid]["total_it_power_W"],
               "total_it_W": total, "cp_input_W": cp_input,
               "liquid_removed_W": removed, "solid_storage_rate_W": storage,
               "air_residual_W": air_heat,
               "closure_residual_W": total - removed - storage - air_heat,
               "faces": face_rows}
        server_rows.append(row)
        for field in ("total_it_W", "cp_input_W", "liquid_removed_W",
                      "solid_storage_rate_W", "air_residual_W"):
            totals[field] += row[field]
    if not math.isclose(totals["face_heat_W"], totals["air_residual_W"], rel_tol=1e-12, abs_tol=1e-6):
        raise ValueError("compiled patch heat does not equal air residual heat")

    source_run_id = compiled["runId"]
    receipt_material = f"{content_hash}:{audit['accepted_input_sha256']}:{audit['liquid_run_id']}:{sample_index}"
    compiled["runId"] = "hybrid_" + hashlib.sha256(receipt_material.encode()).hexdigest()[:20]
    CfdrunV2.model_validate(compiled)
    ledger = {"format": "hybrid-air-heat-handoff/v1",
              "project_uid": project["uid"], "project_content_hash": content_hash,
              "project_source": budget["source"], "source_reference": budget["source_reference"],
              "air_source_run_id": source_run_id, "air_derived_run_id": compiled["runId"],
              "liquid_run_id": audit["liquid_run_id"],
              "liquid_config_hash": audit["liquid_config_hash"],
              "accepted_input_sha256": audit["accepted_input_sha256"],
              "identity_verified": audit["identity_verified"],
              "sample_index": sample_index, "time_s": audit["time_s"][sample_index],
              "heat_multiplier": multiplier, "load_mapping": budget["load_mapping"],
              "air_surface_allocation": deepcopy(budget["air_surface_allocation"]),
              "heat_flux_sign": "positive_q_into_air_OpenFOAM_v2412_verified_small_box",
              "units": {"heat_rate": "W", "heat_flux": "W/m2", "area": "m2", "time": "s"},
              "servers": server_rows, "totals": totals}
    return compiled, ledger


def retarget_hybrid_air_heat_to_mesh(
    compiled_cfdrun: dict,
    heat_ledger: dict,
    mesh_patch_areas_m2: dict[str, float],
    *,
    mesh_sha256: str,
) -> tuple[dict, dict]:
    """Recompute q after meshing so each *actual* patch integrates to its W.

    ``geometryExport.areaM2`` continues to mean the original STL triangle
    area. The accepted mesh-area input must come from the exact mesh to be
    solved, identified by a SHA-256 digest of its points/faces/boundary files.
    The compiler's auditable per-face W allocation is never changed here.
    """
    if heat_ledger.get("format") == "hybrid-air-heat-handoff/v2":
        from backend.aircooling.runner.hybrid_fan_heat_handoff import verify_fan_heat_for_mesh
        return verify_fan_heat_for_mesh(compiled_cfdrun, heat_ledger, mesh_patch_areas_m2,
                                        mesh_sha256=mesh_sha256)
    CfdrunV2.model_validate(compiled_cfdrun)
    ledger = _object(heat_ledger, "heat_ledger")
    if (ledger.get("format") != "hybrid-air-heat-handoff/v1" or
            ledger.get("air_derived_run_id") != compiled_cfdrun.get("runId") or
            not isinstance(mesh_sha256, str) or not _HASH.fullmatch(mesh_sha256)):
        raise ValueError("hybrid ledger/run identity or mesh_sha256 is invalid")
    patch_areas = _object(mesh_patch_areas_m2, "mesh_patch_areas_m2")
    adjusted = deepcopy(compiled_cfdrun)
    checked = deepcopy(ledger)
    by_patch = {binding.get("patchName"): binding
                for binding in adjusted["boundaryBindings"].values()}
    if len(by_patch) != len(adjusted["boundaryBindings"]):
        raise ValueError("compiled .cfdrun has duplicate patchName")
    seen: set[str] = set()
    total_heat = 0.0
    aggregate = {name: 0.0 for name in ("total_it_W", "cp_input_W", "liquid_removed_W",
                                        "solid_storage_rate_W", "air_residual_W")}
    for row in checked.get("servers", []):
        server = _object(row, "hybrid server ledger row")
        values = {name: _finite(server.get(name), f"server.{name}") for name in aggregate}
        if not math.isclose(values["total_it_W"],
                            values["liquid_removed_W"] + values["solid_storage_rate_W"] +
                            values["air_residual_W"], rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"server {server.get('server_uid')} IT energy ledger no longer closes")
        for name, value in values.items():
            aggregate[name] += value
        faces = server.get("faces")
        if not isinstance(faces, list) or len(faces) != 2:
            raise ValueError("hybrid server ledger must have front and rear faces")
        server_heat = 0.0
        for face in faces:
            face = _object(face, "hybrid face ledger row")
            patch = face.get("patch_name")
            if not isinstance(patch, str) or patch in seen or patch not in by_patch:
                raise ValueError(f"hybrid patch {patch!r} is missing or duplicated")
            seen.add(patch)
            if patch not in patch_areas:
                raise ValueError(f"mesh patch {patch!r} is missing an actual area")
            area = _finite(patch_areas[patch], f"mesh patch {patch}.area", positive=True)
            heat = _finite(face.get("heat_W"), f"hybrid patch {patch}.heat_W")
            source_area = _finite(face.get("area_m2"), f"STL patch {patch}.area_m2", positive=True)
            if heat < 0:
                raise ValueError(f"hybrid patch {patch} has negative air heat")
            binding = by_patch[patch]
            expected_bc = "nonfan_heat_flux" if heat > 0 else "wall"
            if binding.get("bcType") != expected_bc or face.get("boundary_type") != expected_bc:
                raise ValueError(f"hybrid patch {patch} boundary type changed before mesh retargeting")
            if heat > 0:
                original_flux = _finite(binding.get("parameters", {}).get("heatFluxWm2"),
                                        f"hybrid patch {patch}.heatFluxWm2")
                if not math.isclose(original_flux * source_area, heat, rel_tol=1e-10, abs_tol=1e-6):
                    raise ValueError(f"hybrid patch {patch} source STL flux no longer matches ledger watts")
                if not math.isclose(original_flux,
                                    _finite(face.get("heat_flux_W_m2"), f"hybrid patch {patch}.ledger flux"),
                                    rel_tol=1e-10, abs_tol=1e-6):
                    raise ValueError(f"hybrid patch {patch} manifest and ledger flux disagree")
            flux = heat / area
            binding["parameters"] = {"heatFluxWm2": flux} if heat > 0 else {}
            face["mesh_area_m2"] = area
            face["mesh_area_relative_change"] = (area - source_area) / source_area
            face["mesh_heat_flux_W_m2"] = flux
            face["mesh_heat_W"] = flux * area
            server_heat += face["mesh_heat_W"]
        if not math.isclose(server_heat, _finite(server.get("air_residual_W"),
                                                "server.air_residual_W"),
                            rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"server {server.get('server_uid')} mesh heat does not close")
        server["mesh_face_heat_W"] = server_heat
        total_heat += server_heat
    if not seen or not math.isclose(total_heat,
                                    _finite(checked.get("totals", {}).get("air_residual_W"),
                                            "totals.air_residual_W"),
                                    rel_tol=1e-10, abs_tol=1e-6):
        raise ValueError("mesh patch heat does not equal audited air residual")
    for name, value in aggregate.items():
        if not math.isclose(value, _finite(checked["totals"].get(name), f"totals.{name}"),
                            rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"hybrid aggregate energy ledger {name} no longer closes")
    if not math.isclose(total_heat,
                        _finite(checked["totals"].get("face_heat_W"), "totals.face_heat_W"),
                        rel_tol=1e-10, abs_tol=1e-6):
        raise ValueError("hybrid face_heat_W ledger no longer closes")
    checked["totals"]["mesh_face_heat_W"] = total_heat
    checked["mesh_area_verified"] = True
    checked["mesh_sha256"] = mesh_sha256
    CfdrunV2.model_validate(adjusted)
    return adjusted, checked


def finalize_hybrid_air_heat_for_mesh(
    compiled_cfdrun: dict,
    heat_ledger: dict,
    mesh_case_dir: str | Path,
    *,
    checkmesh_binary: str = "checkMesh",
    postprocess_binary: str = "postProcess",
) -> tuple[dict, dict]:
    """Quality-gate a mesh, measure its heat patches, then conserve watts.

    This is the pre-solve gate. It leaves the input manifest untouched and
    returns a final derived manifest that the air runner can consume. A
    checkMesh failure blocks the solve even if OpenFOAM exits with status 0.
    """
    quality = verify_openfoam_mesh_quality(mesh_case_dir, checkmesh_binary=checkmesh_binary)
    patch_names = {face["patch_name"] for server in heat_ledger["servers"]
                   for face in server["faces"]}
    measured = measure_openfoam_patch_areas(
        mesh_case_dir, patch_names, postprocess_binary=postprocess_binary)
    if measured["mesh_sha256"] != quality["mesh_sha256"]:
        raise ValueError("mesh changed between checkMesh and patch-area measurement")
    adjusted, checked = retarget_hybrid_air_heat_to_mesh(
        compiled_cfdrun, heat_ledger,
        {name: item["area_m2"] for name, item in measured["patches"].items()},
        mesh_sha256=quality["mesh_sha256"])
    checked["mesh_quality"] = quality
    checked["mesh_area_measurement"] = {
        "format": measured["format"],
        "patch_count": len(measured["patches"]),
        "postprocess_stdout_sha256": measured["postprocess_stdout_sha256"],
        "postprocess_stderr_sha256": measured["postprocess_stderr_sha256"],
    }
    return adjusted, checked
