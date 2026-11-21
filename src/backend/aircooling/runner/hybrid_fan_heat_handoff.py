"""Fan enthalpy lowering of one accepted liquid sample, retaining domain UIDs.

The source IT power declaration and air power keyframes remain source inputs.
Only the derived owned air snapshot receives Q_IT - Q_liquid - storage.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import math
from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.stl_geometry_index import index_cfdrun_stls
from backend.aircooling.runner.cfdrun_reader import _schedule_value
from backend.aircooling.runner.hybrid_air_heat_handoff import _finite, _SERVER_PATH, _HASH

_ENERGY = ("total_it_W", "cp_input_W", "liquid_removed_W", "solid_storage_rate_W", "air_residual_W")


def _at(parameters: dict, field: str, time: float) -> float:
    # Use the reader's exact right-continuous/clamped keyframe semantics.
    rows = (parameters.get("schedules") or {}).get(field)
    return _finite(_schedule_value(rows, time, "right") if rows else parameters.get(field), field)


def _uid(binding: dict) -> str | None:
    path = binding.get("objectPath")
    match = _SERVER_PATH.fullmatch(path) if isinstance(path, str) else None
    return f"{match[1]}/{match[2]}" if match else None


def compile_fan_heat(project, air, tri_dir, budget, audit, expected, content_hash, sample_index):
    time = _finite(audit["time_s"][sample_index], "accepted time")
    multiplier = _finite(audit["heat_multipliers"][sample_index], "heat multiplier")
    if air.get("transient") and time > air["transient"]["durationS"]:
        raise ValueError("accepted liquid sample lies beyond the air source timeline")
    groups = {uid: {"fan_inlet": [], "fan_outlet": []} for uid in expected}
    target = set()
    for key, binding in air["boundaryBindings"].items():
        uid = _uid(binding)
        if uid not in expected:
            continue
        bc = binding["bcType"]
        if bc == "wall":
            continue
        if bc not in ("fan_inlet", "fan_outlet"):
            raise ValueError(f"server {uid} has a non-fan thermal boundary")
        name = binding.get("solidName")
        if not isinstance(name, str) or binding.get("patchName") != name or name in target:
            raise ValueError(f"server {uid} fan solid is missing or duplicated")
        if binding.get("parameters", {}).get("timeVarying"):
            raise ValueError("legacy timeVarying fan inputs need conversion to explicit keyframe schedules")
        target.add(name)
        groups[uid][bc].append((key, binding))
    if any(not g["fan_inlet"] or not g["fan_outlet"] for g in groups.values()):
        raise ValueError("every liquidServer needs complete inlet and outlet fan bindings")
    for term in air.get("sourceTerms", {}).values():
        if _uid(term) in expected:
            raise ValueError("liquidServer sourceTerm would double count heat")

    compiled = index_cfdrun_stls(air, tri_dir, target_solid_names=target)
    solids = {v["solidName"]: v for v in compiled["geometryExport"]["solids"].values()
              if v["solidName"] in target}
    if set(solids) != target:
        raise ValueError("fan solids lack exact STL identity")
    if sum(v["solidName"] in target for v in compiled["geometryExport"]["solids"].values()) != len(target):
        raise ValueError("fan solid has duplicate geometryExport identity")
    accepted = {row["object_binding_uid"]: row for row in audit["servers"]}
    totals = {name: 0.0 for name in _ENERGY}
    totals["face_heat_W"] = 0.0
    rows = []
    for uid in sorted(expected):
        group = groups[uid]
        powers = {}
        flows = {}
        for bc, faces in group.items():
            flows[bc] = sum(_at(b["parameters"], "flowRateM3s", time) for _, b in faces)
            if any(_at(b["parameters"], "flowRateM3s", time) <= 0 for _, b in faces):
                raise ValueError(f"server {uid} requires positive fan flow on each patch")
            powers[bc] = sum(_at(b["parameters"], "powerW", time) for _, b in faces)
        if not math.isclose(flows["fan_inlet"], flows["fan_outlet"], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError(f"server {uid} inlet/outlet flow differs")
        if not math.isclose(powers["fan_inlet"], powers["fan_outlet"], rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"server {uid} source inlet/outlet IT power differs")
        baseline = sum(_at(b["parameters"], "powerW", 0.0) for _, b in group["fan_outlet"])
        if not math.isclose(baseline, budget["servers"][uid]["total_it_power_W"], rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"server {uid} explicit IT budget differs from source power at t=0")
        sample = accepted[uid]["samples"][sample_index]
        if not sample["checked"] or sample["solid_storage_rate_W"] is None:
            raise ValueError(f"server {uid} has no checked storage rate")
        total = powers["fan_outlet"] * multiplier
        removed = _finite(sample["liquid_removed_W"], "liquid removed")
        storage = _finite(sample["solid_storage_rate_W"], "solid storage")
        heat = total - removed - storage
        tol = 1e-6 + 1e-8 * max(1, abs(total), abs(removed), abs(storage))
        if total < 0 or heat < -tol:
            raise ValueError(f"server {uid} has insufficient IT power for audited liquid removal/storage")
        heat = max(0., heat)
        row = {"server_uid": uid, "baseline_total_it_W": baseline,
               "source_total_it_W": powers["fan_outlet"], "total_it_W": total,
               "cp_input_W": _finite(sample["cp_input_W"], "CP input"),
               "liquid_removed_W": removed, "solid_storage_rate_W": storage,
               "air_residual_W": heat, "closure_residual_W": total - removed - storage - heat,
               "flow_rate_m3_s": flows["fan_outlet"], "faces": []}
        for bc, faces in group.items():
            for key, original in faces:
                name = original["solidName"]
                solid = solids[name]
                if solid["objectPath"] != original["objectPath"] or solid["patchName"] != name:
                    raise ValueError(f"server {uid} fan boundary/geometry UID differs")
                area = _finite(solid["areaM2"], "STL fan area", positive=True)
                flow = _at(original["parameters"], "flowRateM3s", time)
                power = heat * flow / flows[bc]
                p = compiled["boundaryBindings"][key]["parameters"]
                p["flowRateM3s"] = flow
                p["powerW"] = power
                p["timeVarying"] = {}
                # Optional inlet T uses the same accepted-time evaluator before
                # its schedule is frozen. Missing/null T keeps runtime behavior.
                original_parameters = original["parameters"]
                if bc == "fan_inlet" and (
                    original_parameters.get("temperatureC") is not None
                    or (original_parameters.get("schedules") or {}).get("temperatureC")
                ):
                    p["temperatureC"] = _at(original_parameters, "temperatureC", time)
                for field in ("powerW", "flowRateM3s", "temperatureC"):
                    if p.get("schedules"):
                        p["schedules"].pop(field, None)
                if not p.get("schedules"):
                    p.pop("schedules", None)
                # Runtime fields.py derives outlet enthalpy from current inlet T.
                if bc == "fan_outlet":
                    p.pop("temperatureC", None)
                stl = compiled["geometryExport"]["stlFiles"][solid["stlFileId"]]
                row["faces"].append({"face": bc, "patch_name": name, "solid_name": name,
                    "object_path": original["objectPath"], "boundary_type": bc,
                    "area_m2": area, "flow_rate_m3_s": flow, "power_W": power,
                    "heat_W": power if bc == "fan_outlet" else 0.,
                    "stl_file_id": solid["stlFileId"], "stl_path": stl["path"],
                    "stl_sha256": stl["contentHash"]})
                if bc == "fan_outlet":
                    totals["face_heat_W"] += power
        for name in _ENERGY:
            totals[name] += row[name]
        rows.append(row)
    if not math.isclose(totals["face_heat_W"], totals["air_residual_W"], rel_tol=1e-10, abs_tol=1e-6):
        raise ValueError("fan outlet heat does not equal the audited air residual")
    # Transitional carrier has explicit source UIDs; domain liquid config is retained.
    by_uid = {row["server_uid"]: row for row in rows}
    for rack in (compiled.get("meshGeometry") or {}).get("racks", {}).values():
        for server in rack.get("servers", []):
            uid = str(server.get("sourceRackUid", "")) + "/" + str(server.get("sourceUid", ""))
            if uid in by_uid:
                if server.get("sourceType") != "liquidServer" or server.get("type") != "fanServer":
                    raise ValueError("hybrid carrier lost its liquidServer source identity")
                server["physics"]["power"] = by_uid[uid]["air_residual_W"]
                server["physics"]["volumeFlowRate"] = by_uid[uid]["flow_rate_m3_s"]
                server["physics"].pop("timeVarying", None)
    source_run_id = compiled["runId"]
    identity = f"{content_hash}:{audit['accepted_input_sha256']}:{audit['liquid_run_id']}:{sample_index}:fan"
    compiled["runId"] = "hybrid_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    CfdrunV2.model_validate(compiled)
    ledger = {"format": "hybrid-air-heat-handoff/v2", "air_transfer": "fan_enthalpy",
        "scope": "one accepted liquid sample into an air snapshot",
        "project_uid": project["uid"], "project_content_hash": content_hash,
        "project_source": budget["source"], "source_reference": budget["source_reference"],
        "air_source_run_id": source_run_id, "air_derived_run_id": compiled["runId"],
        "liquid_run_id": audit["liquid_run_id"], "liquid_config_hash": audit["liquid_config_hash"],
        "accepted_input_sha256": audit["accepted_input_sha256"], "identity_verified": audit["identity_verified"],
        "sample_index": sample_index, "time_s": time, "heat_multiplier": multiplier,
        "load_mapping": budget["load_mapping"], "units": {"heat_rate": "W", "flow": "m3/s", "area": "m2", "time": "s"},
        "servers": rows, "totals": totals}
    return compiled, ledger


def verify_fan_heat_for_mesh(compiled, ledger, patch_areas, *, mesh_sha256):
    CfdrunV2.model_validate(compiled)
    if ledger.get("air_derived_run_id") != compiled.get("runId") or not isinstance(mesh_sha256, str) or not _HASH.fullmatch(mesh_sha256):
        raise ValueError("fan hybrid ledger/run or mesh identity differs")
    adjusted, checked = deepcopy(compiled), deepcopy(ledger)
    by_patch = {b["patchName"]: b for b in adjusted["boundaryBindings"].values()}
    seen = set()
    aggregate = {name: 0. for name in _ENERGY}
    heat_sum = 0.
    for row in checked["servers"]:
        values = {name: _finite(row[name], name) for name in _ENERGY}
        if not math.isclose(values["total_it_W"], values["liquid_removed_W"] + values["solid_storage_rate_W"] + values["air_residual_W"], rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError("fan hybrid per-UID energy no longer closes")
        flows = {"fan_inlet": 0., "fan_outlet": 0.}
        powers = {"fan_inlet": 0., "fan_outlet": 0.}
        for face in row["faces"]:
            name, bc = face["patch_name"], face["boundary_type"]
            if name in seen or name not in by_patch or bc not in flows or name not in patch_areas:
                raise ValueError("fan hybrid mesh patch is missing or duplicated")
            seen.add(name)
            binding = by_patch[name]
            if binding["bcType"] != bc or binding["solidName"] != face["solid_name"] or binding["objectPath"] != face["object_path"] or _uid(binding) != row["server_uid"]:
                raise ValueError("fan hybrid boundary identity differs")
            p = binding["parameters"]
            flow, power = _finite(p.get("flowRateM3s"), "fan flow", positive=True), _finite(p.get("powerW"), "fan power")
            if power < 0 or p.get("schedules") or p.get("timeVarying") or not math.isclose(flow, face["flow_rate_m3_s"], rel_tol=1e-12, abs_tol=1e-12) or not math.isclose(power, face["power_W"], rel_tol=1e-10, abs_tol=1e-6):
                raise ValueError("fan hybrid snapshot power/flow changed")
            face["mesh_area_m2"] = _finite(patch_areas[name], "mesh fan area", positive=True)
            face["mesh_heat_W"] = power if bc == "fan_outlet" else 0.
            flows[bc] += flow
            powers[bc] += power
        if not math.isclose(flows["fan_inlet"], flows["fan_outlet"], rel_tol=1e-10, abs_tol=1e-12) or not math.isclose(flows["fan_outlet"], row["flow_rate_m3_s"], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("mesh fan UID flow is no longer conserved")
        if any(not math.isclose(power, values["air_residual_W"], rel_tol=1e-10, abs_tol=1e-6) for power in powers.values()):
            raise ValueError("mesh fan UID residual heat differs")
        row["mesh_face_heat_W"] = powers["fan_outlet"]
        heat_sum += powers["fan_outlet"]
        for name in _ENERGY:
            aggregate[name] += values[name]
    if not seen or not math.isclose(heat_sum, ledger["totals"]["face_heat_W"], rel_tol=1e-10, abs_tol=1e-6):
        raise ValueError("fan hybrid mesh heat sum differs")
    for name, value in aggregate.items():
        if not math.isclose(value, ledger["totals"][name], rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError("fan hybrid aggregate energy differs")
    checked["totals"]["mesh_face_heat_W"] = heat_sum
    checked["mesh_area_verified"] = True
    checked["mesh_sha256"] = mesh_sha256
    return adjusted, checked
