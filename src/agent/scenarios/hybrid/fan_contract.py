"""The fan/air handoff of a compiled hybrid candidate: per-server air heat from the platform's own liquid answer.

Ports ``agent_legacy/harness/shinaide_candidate_fan_contract_v3.py`` (format
``ispo-liquid-server-fan-air-contract/v4-closed-ACU``). The legacy read the heat of
its own research liquid worker through ``backend.liquidcooling.hybrid_heat_audit``;
here every liquid number is the platform's steady answer
(``agent.scripted_api.run_liquid_case``, read through :mod:`agent.tools`), and every air
number the editor's export (``agent.tools.project.export_air``):

- one source: the ``.liquidrun``'s ``source_project.sha256``, the air manifest's
  ``sourceProject.contentHash`` and the geometry ledger's hash must be equal;
- per server: ``Q_IT`` is the task's registered IT power (equal to the exported
  ``physics.power``), ``Q_liquid`` the platform's ``heat_removed_W``, the steady
  storage zero (the platform's ``storage_rate_W`` is checked against the task limit)
  and ``Q_air = Q_IT - Q_liquid``. The removed heat must also equal the cold plates'
  input ``Q_W`` within the task's per-UID limit (a steady closure check, not a source);
- every ACU's effective supply flow, temperature and capacity in the manifest must be
  the scenario's (a closed ACU at exactly zero);
- the porous ``sigma`` of every perforated surface (blanking panels, floor tiles, a
  perforated roof), its baffle patches (master/slave) and the floor/baffle
  definitions are the manifest's.

Server fan electricity stays inside the declared IT power; no fan watts are inferred
from a flow.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Dict, Mapping

FORMAT = "agent.hybrid.fan-air-contract/v1"
RHO_KG_M3 = 1.19
CP_J_KGK = 1005.


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
                          .encode()).hexdigest()


def _finite(value: Any, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (
            positive and value <= 0):
        raise ValueError("finite" + (" positive" if positive else "") + " " + name + " required")
    return float(value)


def _cold_plate_input_W(liquidrun: Mapping[str, Any]) -> Dict[str, float]:
    """``{cold-plate edge id: Q_W}`` of a power-mode ``.liquidrun`` (edge params over its profile)."""
    profiles = (liquidrun.get("parameter_pack") or {}).get("profiles") or {}
    heat = {}
    for edge in liquidrun["edges"]:
        if edge["type"] != "cold_plate":
            continue
        params = dict((profiles.get(edge.get("parameter_ref")) or {}).get("params") or {})
        params.update(edge.get("params") or {})
        if params.get("heat_mode", "power") != "power":
            raise ValueError("cold plate " + edge["id"] + ": only power-mode plates have a declared heat input")
        heat[edge["id"]] = _finite(params.get("Q_W"), edge["id"] + ".Q_W")
    return heat


def build_fan_contract(project: Mapping[str, Any], manifest: Mapping[str, Any], ledger: Mapping[str, Any],
                       liquidrun: Mapping[str, Any], liquid_metrics: Mapping[str, Any], scenario: Mapping[str, Any],
                       it_budget_W: Mapping[str, float], *, liquid_run: Mapping[str, Any]) -> Dict[str, Any]:
    """The contract the zonal networks read; ``ValueError`` names the first identity or balance that fails.

    ``liquid_metrics`` is the steady answer (``result["metrics"]`` of
    ``run_liquid_case``), ``liquidrun`` the manifest it solved, ``liquid_run``
    the run's identity (``{runDir, resultJson, liquidrunSha256}``), and
    ``it_budget_W`` ``{rack/server uid: registered IT power}``.
    """
    before = copy.deepcopy(project)
    canonical = liquidrun["source_project"]["sha256"]
    if manifest["sourceProject"]["contentHash"] != canonical or ledger["project_content_hash"] != canonical:
        raise ValueError("air geometry / ledger / liquid run come from different projects")
    limits = scenario["task_thresholds"]
    diagnostics = liquid_metrics["diagnostics"]
    storage = _finite(diagnostics["storage_rate_W"], "liquid steady storage rate")
    if abs(storage) > limits["steady_storage_W_limit"]:
        raise ValueError("the liquid answer is not a steady state (storage rate " + repr(storage) + " W)")
    removed = {}
    for rack in liquid_metrics["metrics"]["racks"]:
        for server in rack["servers"]:
            removed[rack["rack_id"] + "/" + server["server_id"]] = server
    plates = _cold_plate_input_W(liquidrun)
    effective = {(s["sourceRackUid"], s["sourceUid"]): s for r in manifest["meshGeometry"]["racks"].values()
                 for s in r["servers"]}
    faces = {row["server_uid"]: {f["face"]: f for f in row["faces"]} for row in ledger["servers"]}
    if not (set(removed) == set(it_budget_W) == set(faces) == {f"{r}/{s}" for r, s in effective}):
        raise ValueError("exact server UID coverage required across task, liquid run, ledger and export")
    servers = []
    closure = 0.
    for uid in sorted(removed):
        rid, sid = uid.split("/", 1)
        obj = project["constructions"]["racks"][rid]["constructions"]["servers"][sid]
        if obj["type"] != "liquidServer":
            raise ValueError("the liquidServer kind must remain intact: " + uid)
        physics = effective[(rid, sid)]["physics"]
        flow = _finite(obj["cooling"]["volumeFlowRate"], uid + ".stored_airflow_m3s", True)
        it = _finite(it_budget_W[uid], uid + ".registered_IT_W", True)
        if physics["volumeFlowRate"] != flow or physics["power"] != it:
            raise ValueError("exported server power/flow differs from the registered budget: " + uid)
        if set(faces[uid]) != {"front", "rear"} or min(f["area_m2"] for f in faces[uid].values()) <= 0:
            raise ValueError("exactly two exported air faces with positive area required: " + uid)
        row = removed[uid]
        liquid = _finite(row["heat_removed_W"][0], uid + ".liquid_heat_removed_W")
        declared = sum(plates[edge] for edge in row["cp_edge_ids"])
        closure = max(closure, abs(declared - liquid))
        if abs(declared - liquid) > limits["per_UID_heat_closure_W_limit"]:
            raise ValueError("liquid heat removed differs from the cold plates' input: " + uid)
        air = it - liquid
        if air < -1e-6:
            raise ValueError("negative residual server air heat: " + uid)
        servers.append({"uid": uid, "rack_uid": rid, "server_uid": sid, "project_kind": "liquidServer",
                        "fixed_flow_m3s": flow, "baseline_mock_IT_W": it,
                        "samples": [{"time_s": 0.0, "Q_IT_W": it, "actual_Q_liquid_W": liquid, "solid_storage_W": 0.0,
                                     "Q_air_W": air, "expected_delta_T_K": air / (RHO_KG_M3 * CP_J_KGK * flow)}],
                        "cold_plate_edges": list(row["cp_edge_ids"]), "cold_plate_input_W": declared,
                        "rack_geometry": copy.deepcopy(project["constructions"]["racks"][rid]["geometry"]),
                        "server_slot_geometry": copy.deepcopy(obj["geometry"]),
                        "cold_plate_parameter_sha256": _digest(obj.get("liquidCooling", {})),
                        "intake": {"original_solid": faces[uid]["front"]["solid_name"],
                                   "area_m2": faces[uid]["front"]["area_m2"], "future_HF_bc_role": "fan_inlet"},
                        "exhaust": {"original_solid": faces[uid]["rear"]["solid_name"],
                                    "area_m2": faces[uid]["rear"]["area_m2"], "future_HF_bc_role": "fan_outlet"},
                        "server_fan_power_W": None,
                        "server_fan_electricity_accounting": "inside the declared IT power; not measured separately"})
    acus = []
    for uid, obj in sorted(project["constructions"]["acus"].items()):
        geometry = obj["geometry"]
        raw = {}
        for role, selection in (("supply", geometry["supplyFaces"]), ("return", geometry["returnFaces"])):
            raw[role] = [{"face_id": ref["faceId"], "patch_id": ref["patchId"],
                          "physics": copy.deepcopy(next(x for x in geometry["faces"][ref["faceId"]]["patches"]
                                                        if x["id"] == ref["patchId"])["physics"])}
                         for ref in selection]
        inputs = manifest["inputs"]["acus"][uid]
        compiled = [b for b in manifest["boundaryBindings"].values()
                    if b.get("objectPath") == "constructions.acus." + uid]
        supply = [b for b in compiled if b.get("bcType") == "acu_supply"]
        ret = [b for b in compiled if b.get("bcType") == "acu_return"]
        if len(supply) != 1 or len(ret) != 1:
            raise ValueError("exactly one source-bound ACU supply/return binding required: " + uid)
        if (supply[0]["parameters"]["flowRateM3s"] != inputs["supplyAirVolumeFlowRateM3s"]
                or supply[0]["parameters"]["temperatureC"] != inputs["supplyAirTemperatureC"]
                or ret[0]["parameters"]["flowRateM3s"] != inputs["supplyAirVolumeFlowRateM3s"]):
            raise ValueError("effective ACU input/binding mapping disagrees: " + uid)
        controls = {"compiled_supply_flow_m3s": _finite(inputs["supplyAirVolumeFlowRateM3s"], uid + ".flow"),
                    "compiled_supply_temperature_C": _finite(inputs["supplyAirTemperatureC"], uid + ".temperature"),
                    "compiled_return_requested_flow_m3s": float(ret[0]["parameters"]["flowRateM3s"]),
                    "supply_patch_name": supply[0]["patchName"], "return_patch_name": ret[0]["patchName"],
                    "source_kind": "the export's effective inputs and boundary bindings; not a measured field"}
        declared = scenario["acu_states"][uid]
        if (controls["compiled_supply_flow_m3s"] != declared["supply_flow_m3s"]
                or controls["compiled_return_requested_flow_m3s"] != declared["return_flow_m3s"]):
            raise ValueError("exported zero/active flow differs from the registered operating state: " + uid)
        if supply[0]["parameters"]["coolingCapacityW"] != declared["capacity_kw"] * 1000:
            raise ValueError("exported zero/active capacity differs from the registered operating state: " + uid)
        if declared["state"] == "active" and controls["compiled_supply_temperature_C"] != declared["supply_temperature_C"]:
            raise ValueError("exported supply temperature differs from the design: " + uid)
        acus.append({"uid": uid, "name": obj["meta"]["name"], "operating_state": declared["state"],
                     "closed_ports": declared["state"] == "failed_closed", "geometry": copy.deepcopy(geometry),
                     "effective_controls": controls, "raw_editor_controls": raw,
                     "effective_input_record": copy.deepcopy(inputs), "source_bound_BC_bindings": copy.deepcopy(compiled),
                     "ACU_fan_power_W": None,
                     "power_accounting": "distinct from the server fans; the energy model declares ACU fan parameters"})
    porous, porous_patches = {}, {}
    for binding in manifest["porousBindings"].values():
        if binding["type"] == "perforatedBaffle":
            sigma = _finite(binding["parameters"]["sigma"], binding["surfaceUid"] + ".sigma")
            if porous.setdefault(binding["surfaceUid"], sigma) != sigma:
                raise ValueError("ambiguous porous sigma: " + binding["surfaceUid"])
            porous_patches.setdefault(binding["surfaceUid"], []).append(binding["patchName"])
    total = {"Q_IT_W": sum(s["samples"][0]["Q_IT_W"] for s in servers),
             "actual_Q_liquid_W": sum(s["samples"][0]["actual_Q_liquid_W"] for s in servers),
             "solid_storage_W": 0.0, "Q_air_W": sum(s["samples"][0]["Q_air_W"] for s in servers)}
    result = {"format": FORMAT, "air_transfer_model": "fan-enabled-lumped-server",
              "operating_scenario": copy.deepcopy(dict(scenario)),
              "IT_budget_W": dict(sorted(it_budget_W.items())),
              "fidelity": "LF 1D interface only; HF air unassessed", "source_project_uid": project["uid"],
              "canonical_content_sha256": canonical,
              "geometry_sha256": _digest({"geometry": project["geometry"], "constructions": project["constructions"]}),
              "liquid_run": dict(liquid_run), "accepted_liquid_identity_verified": True,
              "liquid_steady_closure_max_W": closure, "liquid_storage_rate_W": storage,
              "rho_kg_m3": RHO_KG_M3, "cp_J_kgK": CP_J_KGK, "servers": servers, "acus": acus,
              "porous_blanking_sigma": porous,
              "porous_baffle_patches": {k: sorted(v) for k, v in sorted(porous_patches.items())},
              "partitions": {"floors": copy.deepcopy(project["constructions"]["floors"]),
                             "baffles": copy.deepcopy(project["constructions"]["baffles"]),
                             "compiled_floor_definitions": copy.deepcopy(manifest["meshGeometry"]["floors"]),
                             "compiled_baffle_definitions": copy.deepcopy(manifest["meshGeometry"]["baffles"])},
              "containment_geometry": copy.deepcopy(scenario["candidate_operation"]["containment_geometry"]),
              "source_STL_hashes": {v["path"]: v["contentHash"]
                                    for v in manifest["geometryExport"]["stlFiles"].values()},
              "total_fixed_server_flow_m3s": sum(s["fixed_flow_m3s"] for s in servers),
              "total_effective_ACU_supply_flow_m3s": sum(a["effective_controls"]["compiled_supply_flow_m3s"]
                                                         for a in acus),
              "totals": [{"time_s": 0.0, **total}],
              "assumptions": ["The registered IT power includes unmeasured server fan electricity; no watts are "
                              "inferred from a flow.",
                              "The liquid answer feeds residual heat one way: fixed cold-plate heat at a steady "
                              "state, not a conjugate air/liquid solve.",
                              "The saved server airflow is a lumped fan stream; not the platform fan BC or an HF "
                              "result."]}
    if project != before:
        raise RuntimeError("the contract builder mutated its source project")
    return result
