"""Audit the cold-plate heat ledger before any liquid-to-air handoff.

The input is an accepted v3 ``executed.liquidrun`` prefix and the corresponding
``DAESimulation.result()`` document. This module neither solves another model
nor assigns the rest of a server's electrical power to air. For each server,
the current backward-Euler solid equation must close in watts:

    sum(Q_CP) = Q_liquid_removed + sum(C_solid * delta(T_chip) / delta(t)).

At sample zero, a steady-initialized run has zero storage. A consistent
initialization can have a nonzero derivative which the result JSON does not
expose, so its first sample is explicitly *not* checked for energy closure.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from backend.liquidcooling.liquidrun_dae_schema import parse_dae_manifest
from backend.liquidcooling.parameters import _digest, resolve_parameter_pack
from backend.liquidcooling.runtime import _canonical_hash


def _object(value, location: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{location} must be an object")
    return value


def _sequence(value, count: int | None, location: str) -> list:
    if not isinstance(value, list) or (count is not None and len(value) != count):
        expected = f" with {count} entries" if count is not None else ""
        raise ValueError(f"{location} must be a list{expected}")
    return value


def _number(value, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{location} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{location} must be a finite number")
    return number


def _series(value, count: int, location: str) -> list[float]:
    return [_number(item, f"{location}[{index}]")
            for index, item in enumerate(_sequence(value, count, location))]


def _identity(value, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} must be a nonempty string")
    return value


def _finite_sum(values, location: str) -> float:
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise ValueError(f"non-finite {location}") from exc
    if not math.isfinite(total):
        raise ValueError(f"non-finite {location}")
    return total


def _unique_index(items: list, key: str, location: str) -> dict[str, Mapping]:
    found: dict[str, Mapping] = {}
    for position, value in enumerate(items):
        item = _object(value, f"{location}[{position}]")
        identity = _identity(item.get(key), f"{location}[{position}].{key}")
        if identity in found:
            raise ValueError(f"{location} has duplicate {key} {identity!r}")
        found[identity] = item
    return found


def audit_hybrid_heat(
    executed_liquidrun: dict,
    result: dict,
    expected_project_uid: str,
    *,
    original_liquidrun: dict | None = None,
    accepted_provenance: dict | None = None,
    atol_W: float = 1e-6,
    rtol: float = 1e-8,
) -> dict:
    """Return a per-server, per-sample W ledger or reject inconsistent inputs.

    ``executed_liquidrun`` must be the strict accepted input prefix, not the
    planned manifest with future events. UID and result-shape checks are done
    before arithmetic; a matching UID alone is not proof that an arbitrary
    result file belongs to this input, so the report retains both identities.
    No source is inferred for total IT power or air heat in this function.
    A two-file audit proves physical closure only. Original input plus the
    runtime's accepted-input receipt are required to prove run identity.
    """
    _object(executed_liquidrun, "executed_liquidrun")
    output = _object(result, "result")
    project_uid = _identity(expected_project_uid, "expected_project_uid")
    absolute = _number(atol_W, "atol_W")
    relative = _number(rtol, "rtol")
    if absolute < 0 or relative < 0:
        raise ValueError("heat-audit tolerances must be nonnegative")

    # Pydantic's public model performs strict v3, graph reference, profile,
    # schedule and event validation without allocating on manifest.device.
    manifest = parse_dae_manifest(executed_liquidrun)
    public = manifest.model_dump(by_alias=True, mode="json", exclude_none=True)
    if manifest.source_project.project_uid != project_uid:
        raise ValueError("executed input source_project project_uid mismatch")
    if not manifest.source.endswith(":accepted-trace") or manifest.events:
        raise ValueError("hybrid heat audit requires an accepted executed.liquidrun prefix")

    sample_count = manifest.time.N_t
    dt = manifest.time.dt_s
    resolved = resolve_parameter_pack(public)
    cp_edges: dict[str, dict] = {}
    for edge in resolved["edges"]:
        if edge["type"] != "cold_plate":
            continue
        edge_id = edge["id"]
        params = edge["params"]
        # A v4 temperature-rise plate has no prescribed power: its input is
        # |m|·cp·dT_set, read from the result's edge flows below.
        rise = params.get("heat_mode") == "temperature_rise"
        cp_edges[edge_id] = {
            "input_W": None if rise else _series(params.get("Q_W"), sample_count,
                                                 f"cold plate {edge_id}.Q_W"),
            "rise_K": _series(params.get("dT_set_K"), sample_count,
                              f"cold plate {edge_id}.dT_set_K") if rise else None,
            "capacity_J_per_K": _number(params.get("C_solid_J_per_K"),
                                         f"cold plate {edge_id}.C_solid_J_per_K"),
        }
    if not cp_edges:
        raise ValueError("accepted input has no cold plate to audit")

    if output.get("solver") != "differentiable_newton_dae" or output.get("converged") is not True:
        raise ValueError("result must be a converged differentiable Newton DAE result")
    run_id = _identity(output.get("run_id"), "result.run_id")
    config_hash = _identity(output.get("config_hash"), "result.config_hash")
    if output.get("object_bindings") != public["object_bindings"]:
        raise ValueError("result object_bindings do not match the accepted input")
    provenance = _object(output.get("parameter_provenance"), "result.parameter_provenance")
    if provenance.get("source_project") != public["source_project"]:
        raise ValueError("result source_project does not match the accepted input")
    if output.get("edge_ids") != [edge["id"] for edge in public["edges"]]:
        raise ValueError("result edge_ids do not match the accepted input")
    if any(item["rise_K"] is not None for item in cp_edges.values()):
        flows = _sequence(output.get("edge_flow_kg_s"), len(public["edges"]), "result.edge_flow_kg_s")
        position = {edge["id"]: index for index, edge in enumerate(public["edges"])}
        cp = _number(public["fluid"]["cp_J_kgK"], "fluid.cp_J_kgK")
        for edge_id, item in cp_edges.items():
            if item["rise_K"] is None:
                continue
            flow = _series(flows[position[edge_id]], sample_count, f"result.edge_flow_kg_s[{edge_id}]")
            item["input_W"] = [abs(m) * cp * rise for m, rise in zip(flow, item["rise_K"])]

    times = _series(output.get("time_s"), sample_count, "result.time_s")
    for index, time in enumerate(times):
        if index and time <= times[index - 1]:
            raise ValueError("result.time_s must be strictly increasing")
        expected = index * dt
        if not math.isclose(time, expected, rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f"result.time_s[{index}] is off the accepted input grid")
    multipliers = _series(output.get("heat_multipliers"), sample_count,
                          "result.heat_multipliers")
    if multipliers[0] != 1.0 or any(value < 0 for value in multipliers):
        raise ValueError("result.heat_multipliers must start at 1 and remain nonnegative")

    if (original_liquidrun is None) != (accepted_provenance is None):
        raise ValueError("original_liquidrun and accepted_provenance must be provided together")
    identity_verified = False
    if original_liquidrun is not None:
        original = parse_dae_manifest(_object(original_liquidrun, "original_liquidrun"))
        original_public = original.model_dump(by_alias=True, mode="json", exclude_none=True)
        receipt = _object(accepted_provenance, "accepted_provenance")
        if original_public["source_project"] != public["source_project"]:
            raise ValueError("original and executed source_project do not match")
        if receipt.get("format") != "liquid-accepted-inputs/v1" or receipt.get("input_file") != "executed.liquidrun":
            raise ValueError("accepted_provenance has an unknown format or input_file")
        if receipt.get("run_id") != run_id:
            raise ValueError("accepted_provenance run_id does not match result")
        original_hash = _canonical_hash(original_public, [])
        if receipt.get("original_config_hash") != original_hash or config_hash != original_hash:
            raise ValueError("result/accepted_provenance original_config_hash does not match original input")
        if receipt.get("executed_config_hash") != _canonical_hash(public, []):
            raise ValueError("accepted_provenance executed_config_hash does not match accepted input")
        original_count = original.time.N_t
        if isinstance(receipt.get("accepted_sample_count"), bool) or receipt.get("accepted_sample_count") != sample_count:
            raise ValueError("accepted_provenance accepted_sample_count mismatch")
        if isinstance(receipt.get("original_sample_count"), bool) or receipt.get("original_sample_count") != original_count:
            raise ValueError("accepted_provenance original_sample_count mismatch")
        accepted_time = _number(receipt.get("accepted_time_s"), "accepted_provenance.accepted_time_s")
        if not math.isclose(accepted_time, times[-1], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError("accepted_provenance accepted_time_s mismatch")
        horizon_complete = sample_count == original_count
        if receipt.get("complete_original_horizon") is not horizon_complete:
            raise ValueError("accepted_provenance complete_original_horizon mismatch")
        identity_verified = True

    details = _unique_index(_sequence(output.get("cp_details"), None,
                                      "result.cp_details"), "edge_id", "result.cp_details")
    if set(details) != set(cp_edges):
        raise ValueError("result cp_details cold-plate edge UIDs do not match the accepted input")
    chip_by_cp = {edge_id: _series(detail.get("chip_temp_K"), sample_count,
                                   f"result.cp_details[{edge_id}].chip_temp_K")
                  for edge_id, detail in details.items()}
    chip_rows = _sequence(output.get("chip_temp_K"), len(cp_edges), "result.chip_temp_K")
    cp_order = [edge["id"] for edge in public["edges"] if edge["type"] == "cold_plate"]
    for row, edge_id in zip(chip_rows, cp_order):
        if _series(row, sample_count, f"result.chip_temp_K[{edge_id}]") != chip_by_cp[edge_id]:
            raise ValueError(f"result chip_temp_K row disagrees with cp_details for {edge_id}")

    diagnostics = _sequence(output.get("diagnostics"), sample_count,
                            "result.diagnostics")
    for index, entry in enumerate(diagnostics):
        item = _object(entry, f"result.diagnostics[{index}]")
        heat = _number(item.get("heat_input_W"),
                       f"result.diagnostics[{index}].heat_input_W")
        imbalance = _number(item.get("energy_balance_W"),
                            f"result.diagnostics[{index}].energy_balance_W")
        tolerance = absolute + relative * max(1.0, abs(heat))
        if not math.isfinite(tolerance):
            raise ValueError("non-finite global heat-audit tolerance")
        if abs(imbalance) > tolerance:
            raise ValueError(f"result.diagnostics[{index}] global energy balance fails")

    summaries = _unique_index(_sequence(output.get("rack_summaries"), None,
                                         "result.rack_summaries"),
                              "rack_id", "result.rack_summaries")
    if set(summaries) != {rack.id for rack in manifest.racks}:
        raise ValueError("result rack UIDs do not match the accepted input")

    cp_owners: dict[str, str] = {}
    servers = []
    uncooled = []
    checked_count = 0
    unchecked_count = 0
    max_residual = 0.0
    totals = [{"time_s": time, "cp_input_W": 0.0,
               "liquid_removed_W": 0.0, "solid_storage_rate_W": 0.0}
              for time in times]

    for rack in manifest.racks:
        rack_binding = manifest.object_bindings.get(rack.id)
        if rack_binding is None or rack_binding.kind != "rack":
            raise ValueError(f"rack {rack.id} lacks a stable rack object_binding UID")
        observed = _unique_index(_sequence(summaries[rack.id].get("servers"), None,
                                           f"result rack {rack.id}.servers"),
                                 "server_id", f"result rack {rack.id}.servers")
        expected_cooled = set()
        for server in rack.servers:
            uid = f"{rack.id}/{server.id}"
            binding = manifest.object_bindings.get(uid)
            if binding is None or binding.kind != "server" or binding.parent_uid != rack.id:
                raise ValueError(f"server {uid} lacks a stable server object_binding UID")
            pairs = server.cp_cu_pairs or ([server.cp_cu] if server.cp_cu is not None else [])
            edge_ids = [pair.edge_id if hasattr(pair, "edge_id") else pair.cold_plate_edge_id
                        for pair in pairs]
            if not edge_ids:
                uncooled.append(uid)
                continue
            expected_cooled.add(server.id)
            if len(edge_ids) != len(set(edge_ids)) or set(edge_ids) - set(binding.edge_ids):
                raise ValueError(f"server {uid} cold plates disagree with object_bindings")
            for edge_id in edge_ids:
                if edge_id not in cp_edges or edge_id in cp_owners:
                    raise ValueError(f"cold plate {edge_id} has a missing or duplicate server UID owner")
                cp_owners[edge_id] = uid
            summary = observed.get(server.id)
            if summary is None or summary.get("cp_edge_ids") != edge_ids:
                raise ValueError(f"result server {uid} cold-plate edge UIDs do not match")
            removed = _series(summary.get("heat_removed_W"), sample_count,
                              f"result server {uid}.heat_removed_W")
            samples = []
            for index, time in enumerate(times):
                cp_input = _finite_sum(
                    (cp_edges[edge_id]["input_W"][index] for edge_id in edge_ids),
                    f"server {uid} cold-plate input")
                liquid_removed = removed[index]
                checked = index > 0 or manifest.analysis.initialization == "steady"
                storage = (_finite_sum(
                    (cp_edges[edge_id]["capacity_J_per_K"] *
                     (chip_by_cp[edge_id][index] - chip_by_cp[edge_id][index - 1]) / dt
                     for edge_id in edge_ids), f"server {uid} solid storage rate") if index > 0 else
                           0.0 if checked else None)
                residual = cp_input - liquid_removed - storage if checked else None
                if checked:
                    if not math.isfinite(residual):
                        raise ValueError(f"non-finite server {uid} heat closure residual")
                    checked_count += 1
                    max_residual = max(max_residual, abs(residual))
                    tolerance = absolute + relative * max(1.0, abs(cp_input),
                                                          abs(liquid_removed), abs(storage))
                    if not math.isfinite(tolerance):
                        raise ValueError(f"non-finite server {uid} heat-audit tolerance")
                    if abs(residual) > tolerance:
                        raise ValueError(f"server {uid} at t={time:g}s violates cold-plate energy closure: {residual:g} W")
                else:
                    unchecked_count += 1
                totals[index]["cp_input_W"] += cp_input
                totals[index]["liquid_removed_W"] += liquid_removed
                if storage is not None:
                    totals[index]["solid_storage_rate_W"] += storage
                if any(not math.isfinite(totals[index][key]) for key in
                       ("cp_input_W", "liquid_removed_W", "solid_storage_rate_W")):
                    raise ValueError(f"non-finite aggregate heat ledger at t={time:g}s")
                samples.append({"time_s": time, "cp_input_W": cp_input,
                                "liquid_removed_W": liquid_removed,
                                "solid_storage_rate_W": storage,
                                "closure_residual_W": residual, "checked": checked,
                                "reason": None if checked else "consistent_initial_derivative_not_exported"})
            servers.append({"rack_uid": rack.id, "server_uid": server.id,
                            "object_binding_uid": uid, "cp_edge_ids": edge_ids,
                            "samples": samples})
        if set(observed) != expected_cooled:
            raise ValueError(f"result rack {rack.id} server UIDs do not match the accepted input")
    if set(cp_owners) != set(cp_edges):
        raise ValueError("accepted cold plates do not have exactly one rack/server UID owner")
    binding_cp_owners: dict[str, str] = {}
    for uid, binding in manifest.object_bindings.items():
        if binding.kind != "server":
            continue
        for edge_id in binding.edge_ids:
            if edge_id not in cp_edges:
                continue
            if edge_id in binding_cp_owners:
                raise ValueError(f"cold plate {edge_id} has duplicate server object_binding owners")
            binding_cp_owners[edge_id] = uid
    if binding_cp_owners != cp_owners:
        raise ValueError("server object_bindings disagree with rack/server cold-plate owners")

    for index, item in enumerate(totals):
        checked = index > 0 or manifest.analysis.initialization == "steady"
        item["checked"] = checked
        item["closure_residual_W"] = (
            item["cp_input_W"] - item["liquid_removed_W"] - item["solid_storage_rate_W"]
            if checked else None)
        if not checked:
            item["solid_storage_rate_W"] = None

    auditable = checked_count > 0
    return {"format": "hybrid-cold-plate-heat-audit/v1",
            "project_uid": project_uid, "liquid_run_id": run_id,
            "liquid_config_hash": config_hash,
            "accepted_input_sha256": _digest(public),
            "accepted_prefix_complete": output.get("completed") is True,
            "identity_verified": identity_verified,
            "auditable": auditable,
            "handoff_ready": (identity_verified and auditable and unchecked_count == 0
                              and output.get("completed") is True),
            "initialization": manifest.analysis.initialization,
            "units": {"heat_rate": "W", "heat_capacity": "J/K",
                      "temperature": "K", "time": "s"},
            "time_s": times, "heat_multipliers": multipliers,
            "audited_server_count": len(servers),
            "uncooled_server_uids": uncooled,
            "checked_server_samples": checked_count,
            "unchecked_server_samples": unchecked_count,
            "max_abs_closure_residual_W": max_residual if auditable else None,
            "servers": servers, "totals": totals}
