"""Control derivatives (I3) of the Shinaide hybrid LF oracle: air states, liquid answers and the hard plant PUE.

Ports the I3 of ``agent_legacy/harness/shinaide_lf_oracle_v2.py`` (the "nine-control"
oracle: four ACU supply temperatures, four ACU flows, the pump speed) with
``shinaide_lf_oracle_availability_v2.py`` (when a hard minimum has an ordinary
derivative) and ``shinaide_lf_gradient_decomposition_v2.py`` (the gradient split
into direct algebra, air state and liquid state). The control vector is now the
candidate's own: each *active* ACU's supply temperature, then each active ACU's
volume flow, then each pump's speed ratio (13 controls with six ACUs, 11 with one
closed; the legacy's 4-ACU reference had nine).

The derivatives come from two solves the oracle already paid for, never from a
new solve:

- **air**: the implicit-function tangent of the zonal network's full residual at
  its accepted root (:func:`air_sensitivity`): ``dU/dc = -F_U^{-1} F_c``, the linear
  residual checked; then the server inlet/exhaust and ACU return temperatures by
  the chain rule. The air network is the agent's own model, so these are exact
  derivatives of the agent-side operator;
- **liquid**: the platform's implicit adjoint (``agent.scripted_api.solve_liquid_steady``,
  ``gradient_parameters=("pump_rpm_ratio",)``): d/dN of the pump hydraulic power,
  the max and mean chip temperature, the max coolant outlet and the primary return
  (:func:`liquid_sensitivity`). The liquid side does not read the ACU controls
  (structural zero), and the air side does not read the pump speed: at a steady state
  a power-mode cold plate removes its declared heat at any speed, so the per-server
  air heat is speed-independent (checked by finite differences in the tests).

:func:`pue_sensitivity` composes them with the shared-plant energy model in
PyTorch. That model has three hard minima (the plant supply reset, the common air
return, the water-return cap); the full PUE gradient is reported only where each
active minimum has an ordinary first derivative (a unique exact minimum, equal
active gradients, or a structural proof that the tied returns are one function) and
every active flow lies strictly inside its fan catalog (:func:`classify_active_min`).
The partial derivative in the pump speed is reported at every point: no minimum
depends on it.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np
import torch

from agent.scenarios.hybrid.network_numerics import stable_residual

VERSION = "agent.hybrid.lf-control-derivatives/v1"
AIR_LINEAR_TOLERANCE = 1e-9          # the legacy's gate on the tangent's linear residual
AIR_OUTPUTS = ("server_inlet_c", "server_exhaust_c", "unit_return_c")
LIQUID_OBJECTIVES = ("pump_hydraulic_power_W", "max_chip_temperature_K", "mean_chip_temperature_K",
                     "max_coolant_outlet_temperature_K", "mean_primary_return_temperature_K")


def control_order(active_uids: Sequence[str], pump_ids: Sequence[str]) -> list:
    """The I3 control names: active supply temperatures, active flows, pump speeds (the legacy's naming)."""
    return ([u + ":supply_temperature_C" for u in active_uids] + [u + ":volume_flow_m3s" for u in active_uids]
            + [p + ":pump_speed_ratio" for p in pump_ids])


# -- air ----------------------------------------------------------------------------------------------

def air_sensitivity(model, state: torch.Tensor, controls: torch.Tensor) -> Dict[str, Any]:
    """d(state)/d(controls) at an accepted air root, and d/d(controls) of :data:`AIR_OUTPUTS` (no new solve)."""
    net, areas = model.network, model.areas
    u, c = state.detach(), controls.detach()
    jac = torch.autograd.functional.jacobian
    J = jac(lambda v: stable_residual(net, v, c, areas), u, vectorize=True)
    B = jac(lambda w: stable_residual(net, u, w, areas), c, vectorize=True)
    d_state = torch.linalg.solve(J, -B)
    linear = float((J @ d_state + B).abs().max())
    if not torch.isfinite(d_state).all() or linear > AIR_LINEAR_TOLERANCE:
        raise RuntimeError(f"the air implicit tangent fails its linear equation ({linear:.3g})")
    gradients = {}
    for key in AIR_OUTPUTS:
        output = lambda s, w: net.temperatures(s, w, areas)[key]
        through_state = jac(lambda s: output(s, c), u, vectorize=True)
        direct = jac(lambda w: output(u, w), c, vectorize=True)
        gradients[key] = through_state @ d_state + direct
    return {"gradients": gradients, "d_state_dcontrols": d_state,
            "summary": {"kind": "air steady implicit-function tangent", "state_count": int(net.state_size),
                        "controls": int(c.numel()), "linear_residual_inf": linear,
                        "physical_full_residual_inf": float(net.residual(u, c, areas).abs().max()),
                        "extra_air_solves": 0}}


# -- liquid -------------------------------------------------------------------------------------------

def liquid_sensitivity(steady: Mapping[str, Any], pump_ids: Sequence[str]) -> Dict[str, Any]:
    """``{objective: [d/dN per pump]}`` from the platform's steady answer (its implicit adjoint)."""
    gradients = steady.get("gradients") or {}
    if "pump_rpm_ratio" not in (steady.get("gradientParameters") or ()):
        raise ValueError("the liquid answer has no pump-speed gradients: solve with gradient_parameters="
                         "('pump_rpm_ratio',)")
    result = {}
    for name in LIQUID_OBJECTIVES:
        if name in gradients:
            row = gradients[name]["pump_rpm_ratio"]
            result[name] = [float(row[p]) for p in pump_ids]
    if "pump_hydraulic_power_W" not in result:
        raise ValueError("the liquid answer has no pump hydraulic power gradient")
    checks = [r for r in steady.get("finiteDifferenceCheck") or () if r.get("parameter") == "pump_rpm_ratio"]
    return {"d_dN": result, "values": {k: float(steady["objectives"][k]) for k in result},
            "platform_fd_check": checks, "source": "agent.scripted_api.solve_liquid_steady implicit adjoint (_ImplicitRoot)"}


def primary_circuit(liquidrun: Mapping[str, Any], steady: Mapping[str, Any]) -> Dict[str, float]:
    """The one CDU heat exchanger's primary side: ``m * cp`` and the check ``Q_rej = m cp (T_ret - T_in)``.

    The rejection's speed derivative is then ``m cp dT_ret/dN`` (the primary flow and
    inlet are fixed parameters, not controls).
    """
    circuits = liquidrun.get("primary_circuits") or []
    returns = steady["metrics"]["primaryReturnK"]
    if len(circuits) != 1 or len(returns) != 1:
        raise ValueError("this derivative model has one CDU heat exchanger")
    circuit = circuits[0]
    capacity = float(circuit["flow_kg_s"]) * float(circuit["cp_J_kgK"])
    inlet = float(steady["controls"]["primary_inlet_K"][circuit["edge_id"]])
    rejection = capacity * (float(returns[circuit["edge_id"]]) - inlet)
    reported = float(steady["metrics"]["primaryRejectionW"])
    if abs(rejection - reported) > 1e-6 * max(1., abs(reported)):
        raise ValueError(f"primary rejection {reported} W is not m cp (T_ret - T_in) = {rejection} W")
    return {"edge_id": circuit["edge_id"], "capacity_W_per_K": capacity, "inlet_K": inlet,
            "rejection_W": reported}


# -- hard minima --------------------------------------------------------------------------------------

def classify_active_min(values, gradient_rows, order: Sequence[str], *, tie_tolerance_K: float = 1e-9,
                        gradient_absolute_tolerance: float = 1e-10, gradient_relative_tolerance: float = 1e-8,
                        alias: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Whether ``min(values)`` has an ordinary first derivative at this point, and which (the legacy's v2 rules).

    A unique exact minimum has one even with near neighbours; exactly equal active
    gradients have one; a verified alias proof (:func:`single_return_alias`) with
    numerically agreeing gradients has one. Numerical agreement alone stays
    uncertified (``None``); disagreeing active gradients have none (``False``).
    """
    v = np.asarray(_numpy(values), dtype=float)
    g = np.asarray(_numpy(gradient_rows), dtype=float)
    if v.ndim != 1 or not len(v) or g.shape != (len(v), len(order)) or not (np.isfinite(v).all()
                                                                          and np.isfinite(g).all()):
        raise ValueError("finite branch values and one gradient row per branch over every control")
    minimum = float(v.min())
    exact = np.flatnonzero(v == minimum)
    near = np.flatnonzero(v - minimum <= tie_tolerance_K)
    active = g[exact]
    delta = np.abs(active[:, None, :] - active[None, :, :])
    allowed = gradient_absolute_tolerance + gradient_relative_tolerance * np.maximum(np.abs(active[:, None, :]),
                                                                                      np.abs(active[None, :, :]))
    agree = bool(np.all(delta <= allowed))
    equal = bool(np.all(active == active[0]))
    proven = bool(alias and alias.get("verified") is True)
    if len(exact) == 1:
        ordinary, kind = True, "unique_exact_minimum"
    elif equal:
        ordinary, kind = True, "coincident_active_branch_gradients"
    elif proven and agree:
        ordinary, kind = True, "source_proven_same_function_alias"
    elif proven:
        ordinary, kind = None, "topology_proof_and_gradient_evidence_conflict"
    elif agree:
        ordinary, kind = None, "numerically_agreeing_active_gradients_exact_differentiability_unproven"
    else:
        ordinary, kind = False, "gradient_disagreeing_active_tie"
    return {"classification": kind, "ordinary_first_order_derivative": ordinary, "minimum_value": minimum,
            "exact_active_indices": exact.tolist(), "tolerance_active_indices": near.tolist(),
            "near_switch": len(near) > len(exact), "control_order": list(order),
            "ordinary_gradient": active[0].tolist() if ordinary is True else None,
            "active_gradient_max_abs_difference": float(delta.max()),
            "tolerances": {"value_absolute_K": tie_tolerance_K, "gradient_absolute": gradient_absolute_tolerance,
                           "gradient_relative": gradient_relative_tolerance},
            "alias": deepcopy(dict(alias)) if alias else None}


def single_return_alias(model) -> Dict[str, Any]:
    """Structural proof that every active ACU return is the same temperature (one common upstream node).

    A return node with exactly one directed passive inflow, its unit's fixed outflow
    and no server heat has ``T_return = T_upstream``; if all returns share that
    upstream node they are one function of the controls (e.g. RF_PERIM_HAC's
    ceiling plenum). Multiple real inflows are never discarded.
    """
    net = model.network
    reasons, upstreams = [], []
    for ret, supply in net.unit_pairs:
        touching = [(i, a, b, bool(net.directed[i])) for i, (a, b) in enumerate(net.passive_pairs) if ret in (a, b)]
        machines = [p for p in net.unit_pairs + net.server_pairs if ret in p]
        if len(touching) != 1:
            reasons.append(net.node_names[ret] + ": not exactly one passive inflow")
            continue
        _, a, b, oneway = touching[0]
        if not oneway or b != ret or machines != [(ret, supply)]:
            reasons.append(net.node_names[ret] + ": incidence or direction differs")
            continue
        upstreams.append(a)
    if len(upstreams) == len(net.unit_pairs) and len(set(upstreams)) != 1:
        reasons.append("the returns have distinct upstream nodes")
    verified = not reasons and len(upstreams) == len(net.unit_pairs)
    return {"kind": "single-common-upstream-return-temperature-alias", "verified": verified,
            "common_upstream_node": net.node_names[upstreams[0]] if verified else None,
            "unavailable_reasons": reasons}


# -- PUE ----------------------------------------------------------------------------------------------

def pue_sensitivity(context: Mapping[str, Any], contract: Mapping[str, Any], model, fields: Mapping[str, Any],
                    liquidrun: Mapping[str, Any], steady: Mapping[str, Any], pumps: Sequence[Mapping[str, Any]],
                    air: Mapping[str, Any], liquid: Mapping[str, Any],
                    controls: Optional[torch.Tensor] = None) -> Dict[str, Any]:
    """d(hard design PUE)/d(controls) through the air and liquid tangents, with its availability.

    The energy is the oracle's (:func:`.oracle.shared_energy`) evaluated in PyTorch on
    the first-order expansion of every solved quantity around this point -- the ACU
    returns along the air tangent, the pump hydraulic power and primary return along
    the liquid one -- whose derivative at the point is the true derivative.
    ``controls`` is the air point ``fields`` and ``air`` were solved at (default:
    the contract's).
    """
    from agent.scenarios.hybrid.oracle import ACU_FAN, SECONDARY_PUMP_EFFICIENCY, shared_energy

    active = tuple(context["active_acu_uids"])
    pump_ids = [r["pump"] for r in pumps]
    order = control_order(active, pump_ids)
    n, k = len(active), len(pump_ids)
    controls0 = (model.nominal_controls if controls is None else controls).detach()
    if [str(u) for u in model.active_uids] != list(active):
        raise ValueError("the air model's active ACUs are not the scenario's")
    speeds0 = torch.tensor([r["speed_ratio"] for r in pumps], dtype=torch.float64)
    x0 = torch.cat((controls0, speeds0))
    x = x0.clone().requires_grad_(True)
    c, N = x[:2 * n], x[2 * n:]
    returns0 = fields["unit_return_c"].detach()
    returns = returns0 + air["gradients"]["unit_return_c"] @ (c - controls0)
    d = liquid["d_dN"]
    hydraulic0 = torch.tensor([r["mass_flow_kg_s"] / r["rho_kg_m3"] * r["static_pressure_rise_Pa"] for r in pumps],
                              dtype=torch.float64)
    if abs(float(hydraulic0.sum()) - liquid["values"]["pump_hydraulic_power_W"]) > 1e-9 * max(1., float(hydraulic0.sum())):
        raise ValueError("the pump rows are not the liquid answer's hydraulic power")
    # One hydraulic objective (the sum over pumps): with one pump it is that pump's row.
    if k != 1:
        raise ValueError("this derivative model has one secondary pump")
    hydraulic = hydraulic0 + torch.tensor(d["pump_hydraulic_power_W"], dtype=torch.float64) * (N - speeds0)
    circuit = primary_circuit(liquidrun, steady)
    return_K0 = float(next(iter(steady["metrics"]["primaryReturnK"].values())))
    d_return = torch.tensor(d.get("mean_primary_return_temperature_K", [0.] * k), dtype=torch.float64)
    return_c = (return_K0 - 273.15) + (d_return * (N - speeds0)).sum()
    load_kw = (circuit["rejection_W"] + circuit["capacity_W_per_K"] * (d_return * (N - speeds0)).sum()) / 1000
    index = {u: i for i, u in enumerate(active)}
    zero = x.new_zeros(())
    flow_by_uid = {u: (c[n + index[u]] if u in index else zero) for u in context["all_acu_uids"]}
    supply_by_uid = {u: (c[index[u]] if u in index else None) for u in context["all_acu_uids"]}
    return_by_uid = {u: (returns[index[u]] if u in index else None) for u in context["all_acu_uids"]}
    energy = shared_energy(context, contract, flow_by_uid=flow_by_uid, supply_by_uid=supply_by_uid,
                           return_by_uid=return_by_uid, liquid_load_kw=load_kw.reshape(1),
                           liquid_supply_c=x.new_tensor([circuit["inlet_K"] - 273.15]),
                           liquid_return_c=return_c.reshape(1),
                           secondary_pump_kw=hydraulic / 1000 / SECONDARY_PUMP_EFFICIENCY)
    pue = energy["total_pue_design"]
    gradient, = torch.autograd.grad(pue, x, retain_graph=True)

    # The three hard minima, each with its own branch rows over every control.
    from agent.scenarios.air.energy import CoilParams, PlantParams
    coil, plant = CoilParams(), PlantParams()
    rows = lambda values: torch.stack([torch.autograd.grad(v, x, retain_graph=True, allow_unused=True)[0]
                                       if v.requires_grad else torch.zeros_like(x) for v in values])
    reset_values = [c[i] - coil.approach_k for i in range(n)] + [x.new_tensor(circuit["inlet_K"] - 273.15)]
    reset = classify_active_min(torch.stack(reset_values), rows(reset_values), order)
    alias = single_return_alias(model)
    ret_values = [returns[i] for i in range(n)]
    common = classify_active_min(torch.stack(ret_values), rows(ret_values), order, alias=alias)
    cap_value = float(returns.detach().min()) - coil.pinch_k - reset["minimum_value"]
    if reset["ordinary_first_order_derivative"] is True and common["ordinary_first_order_derivative"] is True:
        cap_row = np.asarray(common["ordinary_gradient"]) - np.asarray(reset["ordinary_gradient"])
        cap = classify_active_min([cap_value, plant.dt_w_max_k], np.stack((cap_row, np.zeros(len(order)))), order)
    elif cap_value > plant.dt_w_max_k:
        cap = classify_active_min([plant.dt_w_max_k], np.zeros((1, len(order))), order)
    else:
        cap = {"classification": "upstream_minimum_derivative_uncertified", "ordinary_first_order_derivative": None,
               "minimum_value": min(cap_value, plant.dt_w_max_k), "control_order": list(order)}
    branches = {"plant_supply_reset": reset, "common_air_return": common, "water_return_cap": cap}
    reasons = [name + ": " + report["classification"] for name, report in branches.items()
               if report["ordinary_first_order_derivative"] is not True]
    low, high = ACU_FAN.rated_flow_m3s * ACU_FAN.min_fraction, ACU_FAN.rated_flow_m3s * ACU_FAN.max_fraction
    endpoint = [order[n + i] for i in range(n) if not low < float(controls0[n + i]) < high]
    if endpoint:
        reasons.append("catalog endpoint flows " + ", ".join(endpoint)
                       + ": only feasible directional derivatives exist there")
    supported = not reasons

    # The decomposition: direct algebra, through the air state, through the liquid state.
    def partial(value):
        grad, = torch.autograd.grad(pue, value, retain_graph=True, allow_unused=True)
        return torch.zeros_like(value) if grad is None else grad

    air_part = torch.zeros_like(x)
    air_part[:2 * n] = partial(returns) @ air["gradients"]["unit_return_c"]
    liquid_part = torch.zeros_like(x)
    liquid_part[2 * n:] = (partial(hydraulic) * torch.tensor(d["pump_hydraulic_power_W"], dtype=torch.float64)
                           + (partial(return_c) + partial(load_kw).sum() * circuit["capacity_W_per_K"] / 1000)
                           * d_return)
    direct = gradient - air_part - liquid_part
    pump_partial = gradient[2 * n:].tolist()
    return {"version": VERSION, "control_order": order, "point": x0.tolist(), "value": float(pue),
            "full_gradient": gradient.tolist() if supported else None,
            "full_gradient_supported": supported, "unavailable_reasons": reasons,
            "pump_speed_partial": dict(zip(pump_ids, pump_partial)),
            "decomposition": {"direct_algebra": direct.tolist(), "air_state": air_part.tolist(),
                              "liquid_state": liquid_part.tolist()},
            "branches": branches, "catalog_flow_bounds_m3s": [low, high],
            "scope": "first-order derivative of the hard design PUE at this point; no smooth proxy, no new solve"}


def _numpy(value):
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().numpy()
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], torch.Tensor):
        return np.array([float(v) for v in value])
    return value


def oracle_derivatives(context, contract, model, fields, liquidrun, steady, pumps,
                       controls: Optional[torch.Tensor] = None) -> Dict[str, Any]:
    """Every I3 block of one oracle point: air tangents, liquid speed gradients, the PUE gradient.

    ``fields`` is the air answer at ``controls`` (default: the contract's controls).
    """
    controls = model.nominal_controls if controls is None else controls
    air = air_sensitivity(model, fields["state"], controls)
    pump_ids = [r["pump"] for r in pumps]
    liquid = liquid_sensitivity(steady, pump_ids)
    pue = pue_sensitivity(context, contract, model, fields, liquidrun, steady, pumps, air, liquid, controls)
    order = pue["control_order"]
    n = len(model.active_uids)
    air_rows = {key: torch.cat((value, value.new_zeros((value.shape[0], len(pump_ids)))), dim=1).tolist()
                for key, value in air["gradients"].items()}
    liquid_rows = {key: [0.] * 2 * n + values for key, values in liquid["d_dN"].items()}
    return {"version": VERSION, "control_order": order, "air": {"gradients": air_rows, "summary": air["summary"],
                                                                 "uid_order": {"server": list(model.server_uids),
                                                                               "acu": list(model.active_uids)}},
            "liquid": {"gradients": liquid_rows, "values": liquid["values"],
                       "platform_fd_check": liquid["platform_fd_check"], "source": liquid["source"]},
            "PUE": pue,
            "structural_zeros": {"liquid_wrt_ACU_controls": "the liquid solve reads no air control",
                                 "air_wrt_pump_speed": "power-mode cold plates remove their declared heat at any "
                                                       "steady speed; the server air heat does not depend on N"}}


def design_head_gradient(action: Mapping[str, Any], task: Mapping[str, Any], fault_id: str,
                         derivatives: Mapping[str, Any]) -> list:
    """The PUE gradient on the task's named numeric head (:func:`.task.continuous_fields`), where it exists.

    Supply ``supply_C`` maps one to one onto the active unit's supply temperature;
    ``flow_fraction`` onto its flow (x rated flow) in the normal context, while a
    fault context runs every active unit at its rated flow (derivative zero); the
    closed unit's set-points do not enter; ``pump_N`` is the pump speed ratio. Row
    geometry has no derivative yet (``None``).
    """
    from agent.scenarios.hybrid.task import RATED_FLOW_M3S, continuous_fields

    pue = derivatives["PUE"]
    gradient = pue["full_gradient"]
    order = pue["control_order"]
    rows = []
    for field in continuous_fields(action, task):
        name = field["name"]
        parts = name.split(":")
        value, status = None, "available"
        if parts[0] == "row":
            status = "geometry tensorization pending"
        elif gradient is None:
            status = "PUE gradient unavailable at this point"
        elif parts[1] == "pump_N":
            # The CDU ``m_30`` carries the liquid run's pump edge ``m_30_pump``.
            pumps = [i for i, o in enumerate(order) if o.endswith(":pump_speed_ratio")
                     and o.split(":")[0] in (parts[0], parts[0] + "_pump")]
            if len(pumps) != 1:
                raise ValueError("the design head's pump " + parts[0] + " is not one of the I3 controls")
            value = gradient[pumps[0]]
        elif parts[0] + ":supply_temperature_C" not in order:
            value, status = 0., "closed unit: its set-points do not enter"
        elif parts[1] == "supply_C":
            value = gradient[order.index(parts[0] + ":supply_temperature_C")]
        elif fault_id != "normal":
            value, status = 0., "fault context: every active unit runs at its rated flow"
        else:
            value = RATED_FLOW_M3S * gradient[order.index(parts[0] + ":volume_flow_m3s")]
        rows.append({"name": name, "dPUE": value, "status": status})
    if not math.isfinite(sum(r["dPUE"] or 0. for r in rows)):
        raise ValueError("non-finite design-head gradient")
    return rows
