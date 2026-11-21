"""The Shinaide hybrid low-fidelity (LF) oracle: one compiled candidate -> registered task assessment.

Ports ``agent_legacy/harness/shinaide_candidate_lf_oracle_v3.py`` (with
``shinaide_candidate_binding_v3`` and ``shinaide_six_acu_air_compile_v2``). The chain
is the legacy's -- liquid steady state, fan/air contract, zonal air network, shared
plant energy, registered constraints -- but every platform step now goes through
:mod:`agent.tools` (``agent.scripted_api``), and nothing the platform reports is recomputed:

1. binding: the action is legal and compilable (:mod:`.task`, :mod:`.compile`) and the
   candidate is that action's build: every moved body sits where the compiler puts it,
   the containment sheets are the action's;
2. air export: :func:`agent.tools.project.export_air` writes ``air.cfdrun`` and the STL
   batch, hash-checked;
3. liquid: :func:`agent.tools.evaluate.evaluate_liquid` (``run_liquid_case``, steady,
   CPU) -- chip temperatures, per-server heat removed, HX return and rejection, pump
   flow and node pressures, the solver's residual diagnostics;
4. contract: :func:`.geometry.geometry_ledger` and :func:`.fan_contract.build_fan_contract`;
5. air: the family's zonal network (:func:`.air_network.network_for`) at the contract's
   controls, accepted at full residual <= 1e-10;
6. energy: :func:`.energy_inactive_acu.shared_power_with_closed_acus` with the
   platform's HX rejection and return temperature and the secondary pump's
   ``p_to - p_from`` and flow (its hydraulic power is the platform's
   ``pump_hydraulic_power_W`` objective; electricity at the legacy's mock 0.7);
7. assessment: :func:`.constraints.evaluate_registered_constraints`; the pump head check
   uses the product law read back from the solved state (:mod:`.pump_affinity`).

:func:`lf_oracle` is an ``lf`` model for :func:`agent.tools.fidelity.evaluate`
(``evaluate(cfdproj, Fidelity.LF, lf=lf_oracle, action=..., out_dir=...)``); it
returns a result mapping (``status``, ``evaluator``, ``derivatives``, ``inputs``,
``stages``, ``artifacts``) and writes every intermediate into ``out_dir``. A legal
action without a compiler or network yet returns ``failure.stage ==
"dependency_pending"``; a failed step returns ``status == "failed"`` with its stage.
:func:`lf_design` compiles the action first.

With ``derivatives=True`` the oracle also reports the I3 of legacy oracle v2 (its
"nine-control" gradients, here one supply temperature and one flow per active ACU
plus the pump speed): the liquid solve asks the platform for its pump-speed
adjoint, and :func:`.derivatives.oracle_derivatives` composes it with the air
network's implicit tangent and the plant energy (``derivatives.json``;
``result["derivatives"]`` names the controls, ``result["I3"]`` holds the PUE
gradient and its availability).
"""
from __future__ import annotations

import copy
import json
import math
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import numpy as np

from agent.scenarios.air.energy import AirProps, CoilParams, FanParams, PlantParams
from agent.scenarios.hybrid import compile as hybrid_compile
from agent.scenarios.hybrid import energy as hybrid_energy
from agent.scenarios.hybrid.air_network import network_for
from agent.scenarios.hybrid.compile import DependencyPending, HybridDesignToProject
from agent.scenarios.hybrid.constraints import evaluate_registered_constraints
from agent.scenarios.hybrid.energy import pump_electric_power_kw
from agent.scenarios.hybrid.energy_inactive_acu import shared_power_with_closed_acus
from agent.scenarios.hybrid.fan_contract import build_fan_contract
from agent.scenarios.hybrid.geometry import geometry_ledger, load_solids
from agent.scenarios.hybrid.pump_affinity import PRODUCT_PUMP_LAW, solved_pump_heads
from agent.scenarios.hybrid.task import REGISTERED, TaskRef, digest, validate
from agent.tools.edits import ProjectView

VERSION = "agent.hybrid.lf-oracle/v1"
RESULT_FORMAT = "agent.hybrid.lf-result/v1"
SECONDARY_PUMP_EFFICIENCY = .7   # the legacy's explicit research mock, unchanged
ACU_FAN = FanParams(2.8, 2.4)    # catalog flow (m^3/s) and fan power (kW) at it, the compiler's
INVALID_I0 = -200.0              # the legacy's reward for a failed evaluation
LOCATION_TOLERANCE_M = 1e-9


def scenario(action: Mapping[str, Any], *, task_ref: TaskRef = REGISTERED, fault_id: str = "normal") -> Dict[str, Any]:
    """The operating scenario of ``action`` in a fault context (the legacy ``scenario_from_candidate`` record)."""
    task = task_ref.load()
    validate(action, task)
    operation = hybrid_compile.operation(action, task_ref, fault_id)
    states = {uid: {"state": "active" if s["active"] else "failed_closed", "supply_flow_m3s": s["flow_m3s"],
                    "return_flow_m3s": s["flow_m3s"], "supply_temperature_C": s["supply_C"],
                    "capacity_kw": s["capacity_kw"], "fan_kw": s["fan_kw"], "geometry_and_UID_retained": True}
              for uid, s in operation["effective_ACU_controls"].items()}
    return {"format": "TaskRef-source-bound-candidate-LF-context/v1", "scenario_id": fault_id,
            "failed_acu_uid": operation["failed_uid"], "all_acu_uids": list(states),
            "active_acu_uids": [u for u in states if states[u]["state"] == "active"], "acu_states": states,
            "architecture": action["architecture"], "plant_design_load_kw": operation["plant_design_load_kw"],
            "task_thresholds": task["thresholds"], "task_ref": {"path": task_ref.path, "sha256": task_ref.sha256},
            "action": copy.deepcopy(dict(action)), "action_sha256": digest(action),
            "candidate_operation": operation}


def registered_it_budget(task: Mapping[str, Any]) -> Dict[str, float]:
    """``{rack/server uid: W}``: the task's registered server IT power (fan electricity included), summing to ``IT_W``."""
    budget = {}
    for uid, record in sorted(task["inventory"]["server_records"].items()):
        power = record["cooling"]["power"]
        if isinstance(power, bool) or not isinstance(power, (int, float)) or not math.isfinite(power) or power <= 0:
            raise ValueError("registered source IT power required: " + uid)
        budget[uid] = float(power)
    if len(budget) != task["inventory"]["server_count"] or math.fsum(budget.values()) != task["inventory"]["IT_W"]:
        raise ValueError("registered source IT budget changed")
    return budget


def design_binding(project: Mapping[str, Any], action: Mapping[str, Any], *, task_ref: TaskRef = REGISTERED,
                   fault_id: str = "normal") -> Dict[str, Any]:
    """Check ``project`` is ``action``'s build: every moved body where the compiler puts it, the action's sheets.

    The ACU set-points and the pump speed are checked later, on the exports
    (fan contract, ``.liquidrun``). Raises ``ValueError`` on the first mismatch;
    :class:`DependencyPending` for an action without a compiler yet.
    """
    edit = HybridDesignToProject(action, task_ref=task_ref, fault_id=fault_id)
    steps = edit.steps(ProjectView(document=json.loads(task_ref.project.read_text(encoding="utf-8")), build_verbs=()))
    models = {uid: model for kind in ("racks", "acus", "cdus", "boxes")
              for uid, model in project["constructions"][kind].items()}
    error = 0.
    for row in steps:
        if row["verb"] != "setModelLocation":
            continue
        uid, expected = row["args"]
        actual = models[uid]["geometry"]["location"]
        error = max(error, *(abs(actual[k] - expected[k]) for k in "xyz"))
    if error > LOCATION_TOLERANCE_M:
        raise ValueError(f"the candidate is not this action's build: a body is {error:.3g} m off its compiled place")
    sheets = sorted(m["uid"] for m in hybrid_compile.containment_sheets(action, task_ref.load())["models"])
    if sorted(project["constructions"]["baffles"]) != sheets:
        raise ValueError(f"containment sheets {sorted(project['constructions']['baffles'])} are not the action's {sheets}")
    return {"compiler": hybrid_compile.VERSION, "moved_bodies": sum(r["verb"] == "setModelLocation" for r in steps),
            "max_location_error_m": error, "containment_sheets": sheets, "action_sha256": digest(action)}


def _plain(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _write(path: Path, value: Any) -> str:
    path.write_text(json.dumps(_plain(value), indent=1, allow_nan=False), encoding="utf-8")
    return str(path)


def lf_oracle(cfdproj, *, action: Mapping[str, Any], out_dir, fault_id: str = "normal",
              task_ref: TaskRef = REGISTERED, timestamp: Optional[str] = None, threads: int = 2,
              derivatives: bool = False) -> Dict[str, Any]:
    """Assess the compiled candidate ``cfdproj`` of ``action`` in ``fault_id``; the LF result (see the module).

    ``out_dir`` must not exist or be empty; it receives ``air/`` (the export),
    ``liquid/`` (the platform's liquid run), the ledger, contract, air fields,
    energy, assessment and ``result.json``. ``threads`` bounds the liquid solve.
    ``derivatives`` adds the I3 (see the module).
    """
    from agent.tools.evaluate import evaluate_liquid
    from agent.tools.project import export_air, project_identity

    started = time.perf_counter()
    out = Path(out_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{out} exists and is not an empty directory")
    out.mkdir(parents=True, exist_ok=True)
    source = project_identity(cfdproj)
    result: Dict[str, Any] = {
        "format": RESULT_FORMAT, "kind": "lf", "status": "failed", "failure": None, "admitted": False,
        "evaluator": {"version": VERSION, "pump_law": PRODUCT_PUMP_LAW, "energy": hybrid_energy.VERSION,
                      "compiler": hybrid_compile.VERSION},
        "derivatives": [], "I3_requested": bool(derivatives),
        "inputs": {"cfdproj": source["path"], "cfdprojSha256": source["sha256"], "actionSha256": digest(action),
                   "taskSha256": task_ref.sha256, "faultId": fault_id, "architecture": action.get("architecture")},
        "stages": {}, "artifacts": {"resultJson": str(out / "result.json")},
    }
    stage = "binding"
    clock = time.perf_counter()

    def done(name):
        nonlocal clock
        result["stages"][name] = {"seconds": time.perf_counter() - clock}
        clock = time.perf_counter()

    try:
        task = task_ref.load()
        project = json.loads(Path(cfdproj).read_text(encoding="utf-8"))
        context = scenario(action, task_ref=task_ref, fault_id=fault_id)
        network_for(action["architecture"])  # a family without a network is pending before anything runs
        result["binding"] = design_binding(project, action, task_ref=task_ref, fault_id=fault_id)
        budget = registered_it_budget(task)
        done(stage)

        stage = "airExport"
        air = export_air(cfdproj, out_dir=out / "air", timestamp=timestamp)
        if air["manifestErrors"]:
            raise ValueError(f"the air manifest fails its schema: {air['manifestErrors'][:3]}")
        manifest = json.loads(Path(air["cfdrun"]).read_text(encoding="utf-8"))
        result["inputs"]["cfdrunSha256"] = air["cfdrunSha256"]
        result["artifacts"]["airExport"] = air["outDir"]
        done(stage)

        stage = "liquid"
        liquid = evaluate_liquid(source["path"], mode="steady", gradient_parameters=("pump_rpm_ratio",) if derivatives else (), threads=threads,
                                 output_root=out / "liquid")
        result["artifacts"]["liquidResultJson"] = str(liquid.result_json)
        if not liquid.succeeded:
            raise RuntimeError(f"the liquid run failed at {liquid.failure_stage}: {(liquid.failure or {}).get('reason')}")
        liquidrun = json.loads(Path(liquid["artifacts"]["liquidrun"]).read_text(encoding="utf-8"))
        steady = liquid.metrics
        pump_edges = [e for e in liquidrun["edges"] if e["type"] == "pump"]
        speed = float(action["liquid"]["pump_N"])
        if [e["params"].get("rpm_ratio") for e in pump_edges] != [speed] or \
                list((steady["controls"].get("pump_rpm_ratios") or {}).values()) != [speed]:
            raise ValueError(f"the liquid run did not run the pump at the action's speed {speed}")
        result["inputs"]["liquidrunSha256"] = liquid.stages["export"]["liquidrunSha256"]
        done(stage)

        stage = "fanContract"
        solids = load_solids(out / "air", air["stlSha256"])
        ledger = geometry_ledger(project, manifest, solids)
        result["artifacts"]["geometryLedger"] = _write(out / "geometry_ledger.json", ledger)
        contract = build_fan_contract(project, manifest, ledger, liquidrun, steady, context, budget,
                                      liquid_run={"resultJson": str(liquid.result_json),
                                                  "liquidrunSha256": result["inputs"]["liquidrunSha256"]})
        result["artifacts"]["fanContract"] = _write(out / "fan_air_contract.json", contract)
        done(stage)

        stage = "air"
        model, fields = solve_air(contract, solids)
        result["artifacts"]["airFields"] = _write(out / "air_fields.json", plain_fields(fields))
        result["artifacts"]["airNetwork"] = _write(out / "air_network.json", model.identity)
        result["evaluator"]["air_network"] = {"version": model.VERSION, "family": model.FAMILY,
                                              "contract_sha256": model.identity["contract_sha256"]}
        done(stage)

        stage = "energy"
        energy, pumps = plant_energy(context, contract, fields, liquidrun, steady)
        result["artifacts"]["energy"] = _write(out / "energy.json", energy)
        done(stage)

        stage = "assessment"
        assessment = assess(context, contract, fields, energy, steady, pumps)
        result["artifacts"]["assessment"] = _write(out / "assessment.json", assessment)
        done(stage)
        if derivatives:
            stage = "derivatives"
            from agent.scenarios.hybrid.derivatives import design_head_gradient, oracle_derivatives
            I3 = oracle_derivatives(context, contract, model, fields, liquidrun, steady, pumps)
            I3["design_head"] = design_head_gradient(action, task, fault_id, I3)
            result["artifacts"]["derivatives"] = _write(out / "derivatives.json", I3)
            pue = I3["PUE"]
            result["derivatives"] = list(I3["control_order"])
            result["I3"] = {"version": I3["version"], "control_order": pue["control_order"],
                            "PUE_gradient": pue["full_gradient"], "supported": pue["full_gradient_supported"],
                            "unavailable_reasons": pue["unavailable_reasons"],
                            "pump_speed_partial": pue["pump_speed_partial"], "design_head": I3["design_head"]}
            done(stage)
        result.update(status="succeeded", admitted=assessment["valid"], valid=assessment["valid"],
                      feasible=assessment["feasible"], I0=assessment["I0"], numerical_gates=assessment["numerical_gates"],
                      metrics=summary(context, contract, fields, energy, steady, pumps))
    except DependencyPending as pending:
        result["failure"] = {"stage": "dependency_pending", "reason": str(pending),
                             "dependencies": pending.dependencies}
        result["I0"] = {"reward": INVALID_I0}
    except Exception as exc:  # recorded with its stage, as agent.scripted_api records a failed run
        result["failure"] = {"stage": stage, "reason": f"{type(exc).__name__}: {exc}",
                             "traceback": traceback.format_exc()[-4000:]}
        result["I0"] = {"reward": INVALID_I0}
    result["elapsedSeconds"] = time.perf_counter() - started
    _write(out / "result.json", result)
    return result


def lf_design(action: Mapping[str, Any], *, out_dir, fault_id: str = "normal", task_ref: TaskRef = REGISTERED,
              timestamp: Optional[str] = None, threads: int = 2, timeout: float = 600.0,
              derivatives: bool = False) -> Dict[str, Any]:
    """Compile ``action`` (:func:`.compile.compile_design`) into ``out_dir/candidate`` and run :func:`lf_oracle`."""
    out = Path(out_dir)
    candidate = out / "candidate" / "candidate.cfdproj"
    report = hybrid_compile.compile_design(action, out=candidate, task_ref=task_ref, fault_id=fault_id,
                                           timeout=timeout)
    if report["status"] != "compiled":
        out.mkdir(parents=True, exist_ok=True)
        result = {"format": RESULT_FORMAT, "kind": "lf", "status": "failed", "admitted": False,
                  "evaluator": {"version": VERSION, "compiler": hybrid_compile.VERSION}, "derivatives": [],
                  "failure": {"stage": "dependency_pending", "dependencies": report.get("dependencies")},
                  "I0": {"reward": INVALID_I0}, "inputs": {"actionSha256": digest(action), "faultId": fault_id}}
        _write(out / "result.json", result)
        return result
    _write(out / "candidate" / "compile_report.json",
           {k: v for k, v in report.items() if k not in ("steps", "compiledSteps")})
    return lf_oracle(candidate, action=action, out_dir=out / "oracle", fault_id=fault_id, task_ref=task_ref,
                     timestamp=timestamp, threads=threads, derivatives=derivatives)


# -- the steps after the platform calls (pure: they read the contract, the solids and the steady answer) ----------

def solve_air(contract: Mapping[str, Any], solids: Mapping[str, Any]):
    """The family's network on ``contract`` and its fields at the contract's controls (full residual included)."""
    model = network_for(contract["operating_scenario"]["architecture"])(contract, solids)
    return model, model.steady_fields()


def plain_fields(fields: Mapping[str, Any]) -> Dict[str, Any]:
    """The air fields as JSON-able values (tensors to lists, the solve receipt kept)."""
    return _plain(dict(fields))


def plant_energy(context: Mapping[str, Any], contract: Mapping[str, Any], fields: Mapping[str, Any],
                 liquidrun: Mapping[str, Any], steady: Mapping[str, Any]):
    """The shared-plant energy (closed ACUs explicit) and the pump rows it used.

    The liquid branch is the platform's: the CDU heat exchanger's rejection,
    primary inlet and return; the secondary pump's solved flow and static
    pressure rise, whose hydraulic power must equal the platform's
    ``pump_hydraulic_power_W`` objective.
    """
    rows = solved_pump_heads(liquidrun, steady)
    mass = np.array([r["mass_flow_kg_s"] for r in rows])
    rise = np.array([r["static_pressure_rise_Pa"] for r in rows])
    secondary = pump_electric_power_kw(mass, rise, np.full(len(rows), SECONDARY_PUMP_EFFICIENCY),
                                       rho_kg_m3=rows[0]["rho_kg_m3"],
                                       head_convention="solved static pressure rise p_to - p_from "
                                                       "(agent.scripted_api steady answer)",
                                       efficiency_provenance="explicit research mock 0.7, the legacy convention")
    reported = steady["objectives"]["pump_hydraulic_power_W"]
    if abs(float(secondary["hydraulic_kw"].sum()) * 1000 - reported) > 1e-9 * max(1., abs(reported)):
        raise ValueError("pump hydraulic power differs from the platform's pump_hydraulic_power_W objective")
    network = steady["metrics"]
    if len(network["primaryReturnK"]) != 1:
        raise ValueError("this energy model has one CDU heat exchanger")
    (hx, return_K), = network["primaryReturnK"].items()
    inlet_K = steady["controls"]["primary_inlet_K"][hx]
    energy = shared_energy(
        context, contract,
        flow_by_uid={u: float(v) for u, v in fields["ACU_flow_by_uid"].items()},
        supply_by_uid={u: None if v is None else float(v) for u, v in fields["ACU_supply_c_by_uid"].items()},
        return_by_uid={u: None if v is None else float(v) for u, v in fields["ACU_return_c_by_uid"].items()},
        liquid_load_kw=np.array([network["primaryRejectionW"] / 1000]),
        liquid_supply_c=np.array([inlet_K - 273.15]), liquid_return_c=np.array([return_K - 273.15]),
        secondary_pump_kw=secondary["electrical_kw"])
    energy["secondary_pump"] = secondary
    return energy, rows


def shared_energy(context: Mapping[str, Any], contract: Mapping[str, Any], *, flow_by_uid, supply_by_uid,
                  return_by_uid, liquid_load_kw, liquid_supply_c, liquid_return_c, secondary_pump_kw):
    """The shared-plant energy of one point (closed ACUs explicit), in NumPy or PyTorch alike.

    The oracle's parameters: the compiler's ACU fan, the default plant and coil,
    air 1.19 / 1005, the hard resets (``tau = 0``), the scenario's installed design load.
    :mod:`.derivatives` evaluates it on tensors for the PUE gradient.
    """
    servers = contract["servers"]
    return shared_power_with_closed_acus(
        acu_uids=context["all_acu_uids"], active_uids=context["active_acu_uids"], flow_by_uid=flow_by_uid,
        supply_by_uid=supply_by_uid, return_by_uid=return_by_uid,
        fans_by_uid={u: ACU_FAN for u in context["all_acu_uids"]},
        it_kw=sum(s["samples"][0]["Q_IT_W"] for s in servers) / 1000,
        air_heat_kw=sum(s["samples"][0]["Q_air_W"] for s in servers) / 1000,
        liquid_load_kw=liquid_load_kw, liquid_supply_c=liquid_supply_c, liquid_return_c=liquid_return_c,
        secondary_pump_kw=secondary_pump_kw, plant_design_load_kw=context["plant_design_load_kw"],
        plant=PlantParams(), coil=CoilParams(), air=AirProps(1.19, 1005), tau_k=0)


def assess(context: Mapping[str, Any], contract: Mapping[str, Any], fields: Mapping[str, Any],
           energy: Mapping[str, Any], steady: Mapping[str, Any], pumps) -> Dict[str, Any]:
    """The registered task constraints (:func:`.constraints.evaluate_registered_constraints`) on these results."""
    chips = steady["metrics"]["chipTemperatureK"]
    liquid_root = {"diagnostics": steady["diagnostics"],
                   "pump_volume_flow_m3s": [r["flow_m3s"] for r in pumps],
                   "used_head_m": [r["product_head_m"] for r in pumps],
                   # The head the solved state shows (pressures and heights) against the law's, both as rho g H.
                   "actual_pump_pressure_rise_Pa": [r["rho_kg_m3"] * r["gravity_m_s2"] * r["measured_head_m"]
                                                    for r in pumps],
                   "modeled_pressure_rise_Pa": [r["rho_kg_m3"] * r["gravity_m_s2"] * r["product_head_m"]
                                                for r in pumps]}
    return evaluate_registered_constraints(context, plain_fields(fields), energy,
                                           {"chip_temp_K": [[chips[k]] for k in chips]}, liquid_root, contract)


def summary(context, contract, fields, energy, steady, pumps) -> Dict[str, Any]:
    """The headline numbers of one evaluation (each one is in the written artifacts)."""
    network = steady["metrics"]
    return {"PUE": float(energy["total_pue_design"]), "total_kw": float(energy["total_kw"]),
            "primary_kw": float(energy["primary_kw"]),
            "ACU_return_c": {u: float(fields["ACU_return_c_by_uid"][u]) for u in context["active_acu_uids"]},
            "server_inlet_max_c": float(fields["server_inlet_c"].max()),
            "server_exhaust_max_c": float(fields["server_exhaust_c"].max()),
            "air_coil_removed_kw": float(fields["air_coil_removed_kw"].sum()),
            "air_heat_kw": sum(s["samples"][0]["Q_air_W"] for s in contract["servers"]) / 1000,
            "liquid_heat_kw": network["primaryRejectionW"] / 1000,
            "max_chip_temperature_K": network["maxChipTemperatureK"],
            "pump_flow_m3s": [r["flow_m3s"] for r in pumps],
            "pump_hydraulic_power_W": steady["objectives"]["pump_hydraulic_power_W"],
            "air_full_residual_inf": fields["original_full_residual_inf"],
            "liquid_scaled_residual": steady["diagnostics"]["scaled_residual"]}
