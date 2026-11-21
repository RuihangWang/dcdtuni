"""Scripted liquid run: ``.cfdproj``/``.liquidrun`` -> DAE steady state or transient -> metrics.

Two modes, both on the product path:

``steady``     the equilibrium query of the liquid session
               (``DAESimulation.equilibrium``), objectives and implicit-adjoint
               gradients (below).
``transient``  the product batch march, ``runtime_batch.run_dae_manifest`` --
               the ``/api/liquid/solve`` path for a DAE manifest -- which writes
               ``log/run-<id>/{input,executed}.liquidrun``, ``result.json``,
               ``executed-inputs.json`` and ``checkpoint.json`` (the files
               ``hybrid_air_case_cli --liquid-run`` reads).

``inputs`` (``pump_rpm_ratios`` / ``valve_openings`` / ``primary_inlet_K``,
each ``{edge id: value}``) is one command given to ``DAESimulation.set_inputs``,
the call behind the server's ``POST /api/liquid/runs/<id>/inputs``: it is
queued for the next accepted step. A transient run therefore applies it from
sample 1; a steady answer is the equilibrium query with the command pending
(the server's ``inputs`` then ``equilibrium``), which needs a manifest with a
next step -- for a ``.cfdproj`` the API compiles the liquid side as transient
for that, and a steady-only ``.liquidrun`` is refused.

Both modes report the network: ``metrics.edges[]`` (id, kind, from, to, signed
``massFlowKgS`` -- positive from ``from`` to ``to`` -- the actual direction as
``upstream->downstream`` or ``none`` on a stagnant edge, ``velocityMS`` for
pipes and valves, the temperature leaving the edge in its flow direction, and
``observable`` = not stagnant) and ``metrics.nodes[]`` (id, pressure,
temperature, position, ``observable`` = false where the temperature is only a
numerical gauge), from the result topology fields of
``postprocess/topology.py``.

Nothing here is a new solver. The steady state is the product's own
equilibrium query (:class:`backend.liquidcooling.dae_runtime.DAESimulation`,
the class the liquid server session runs), and gradients come from the
existing implicit adjoint: ``solve_steady`` is an ``autograd.Function``
(``_ImplicitRoot``) whose backward solves the transposed Jacobian system at
the root. The API repeats that steady solve once with a ``requires_grad``
parameter vector ``theta`` (same guess, same conserved-storage selection) and
differentiates scalar objectives of the root with ``torch.autograd.grad``.

Objectives (all functions of the steady state ``y(theta)``):

``max_chip_temperature_K``            max cold-plate solid temperature
``mean_chip_temperature_K``           mean cold-plate solid temperature
``max_coolant_outlet_temperature_K``  max coolant temperature leaving a cold plate
``mean_primary_return_temperature_K`` mean facility-side return temperature at the CDU HX
``pump_hydraulic_power_W``            sum over pumps of (p_out - p_in) * m / rho

Gradient parameters are named slices of ``EquationGraph.parameter_layout``
(for example ``heat_W`` per cold plate, ``pump_rpm_ratio`` per pump,
``valve_opening`` per valve, ``primary_inlet_K``/``primary_flow_kg_s`` per HX);
each component is labelled with its liquidrun edge or node id. A ``max``
objective is differentiated at its active argument (a subgradient where two
components tie). ``fd_check=k`` compares the adjoint gradient with a centred
finite difference of re-solved steady states for the ``k`` largest components
per objective (step ``h = fd_relative_step * max(1, |theta_i|)``, default
1e-4: small enough that truncation is negligible for these smooth residuals,
large enough that the Newton tolerance does not dominate the difference).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.scripted_api import identity as ident
from agent.scripted_api.headless import HeadlessCompileError, compile_project
from agent.scripted_api.rundir import (case_api_root, create_run_dir, parse_json_source, read_json_source, read_source,
                                write_json, write_text)

RESULT_FORMAT = "dcdtuni.api.result/v1"
OBJECTIVES = ("max_chip_temperature_K", "mean_chip_temperature_K", "max_coolant_outlet_temperature_K",
              "mean_primary_return_temperature_K", "pump_hydraulic_power_W")
DEFAULT_GRADIENT_PARAMETERS = ("heat_W", "pump_rpm_ratio", "valve_opening", "primary_inlet_K", "primary_flow_kg_s")
_KIND = {
    **{name: "cp" for name in ("heat_W", "cp_dp_a", "cp_dp_b", "cp_UA", "cp_Csolid", "cp_dT_set", "initial_solid_temperature")},
    **{name: "pump" for name in ("pump_H0", "pump_a", "pump_rpm_ratio", "pump_flow_set")},
    **{name: "valve" for name in ("valve_Kv", "valve_opening")},
    **{name: "hx" for name in ("hx_dp_a", "hx_dp_b", "hx_UA", "primary_flow_kg_s", "primary_inlet_K", "primary_cp",
                               "primary_rho", "primary_volume", "secondary_volume", "hx_T_set", "hx_T_set_rate")},
    **{name: "pipe" for name in ("pipe_D", "pipe_L", "pipe_dh", "pipe_area", "pipe_roughness", "pipe_minor")},
    **{name: "node" for name in ("pressure_bc", "temperature_bc", "node_gauge_temperature")},
    **{name: "cell" for name in ("initial_water_temperature", "cell_volume", "cell_heat_W")},
}


LIQUID_MODES = ("steady", "transient")
#: ``DAESimulation.set_inputs`` command fields the API forwards (``heat_multiplier`` has its own argument).
INPUT_FIELDS = ("pump_rpm_ratios", "valve_openings", "primary_inlet_K")


def normalise_inputs(inputs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Dict[str, float]]]:
    """``{field: {edge id: number}}`` with known fields only; empty means none."""
    if not inputs:
        return None
    if not isinstance(inputs, dict):
        raise ValueError(f"inputs must be an object of {list(INPUT_FIELDS)}, got {type(inputs).__name__}")
    unknown = sorted(set(inputs) - set(INPUT_FIELDS))
    if unknown:
        raise ValueError(f"unknown input field(s) {unknown}; inputs accepts {list(INPUT_FIELDS)} "
                         "(the heat multiplier is its own argument)")
    out: Dict[str, Dict[str, float]] = {}
    for field, commands in inputs.items():
        if not commands:
            continue
        if not isinstance(commands, dict):
            raise ValueError(f"inputs.{field} must map edge ids to numbers")
        out[field] = {}
        for edge_id, value in commands.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"inputs.{field}.{edge_id} must be a number, got {value!r}")
            out[field][str(edge_id)] = float(value)
    return out or None


def network(topology: Dict[str, Any], *, edge_ids: Sequence[str], node_ids: Sequence[str], flow: Sequence[float],
            edge_outlet_K: Sequence[float], stagnant: Sequence[bool], pressure: Sequence[float],
            temperature: Sequence[float], node_observable: Sequence[bool], rho: float) -> Dict[str, List[Dict[str, Any]]]:
    """``edges[]`` and ``nodes[]`` of one solved state (see the module docstring).

    ``topology`` holds ``edge_from``/``edge_to`` (node ids), ``edge_types``,
    ``edge_D_m`` and ``node_positions_m`` (``result_topology``). A flow is
    positive from ``from`` to ``to``; the direction names the actual upstream
    and downstream nodes, and is ``none`` on a stagnant edge (whose flow is a
    round-off residue) or an exactly zero flow.
    """
    edges: List[Dict[str, Any]] = []
    for i, edge_id in enumerate(edge_ids):
        start, end = topology["edge_from"][i], topology["edge_to"][i]
        m = float(flow[i])
        moving = not stagnant[i] and m != 0.0
        upstream, downstream = ((start, end) if m > 0 else (end, start)) if moving else (None, None)
        diameter = topology["edge_D_m"][i]
        speed = (abs(m) / (rho * math.pi * float(diameter) ** 2 / 4.0)
                 if diameter is not None and float(diameter) > 0 else None)
        edges.append({"id": edge_id, "kind": topology["edge_types"][i], "from": start, "to": end,
                      "massFlowKgS": m, "direction": f"{upstream}->{downstream}" if moving else "none",
                      "upstream": upstream, "downstream": downstream, "velocityMS": speed,
                      "diameterM": None if diameter is None else float(diameter),
                      "outletTemperatureK": float(edge_outlet_K[i]), "observable": not bool(stagnant[i])})
    nodes = [{"id": node_id, "pressurePa": float(pressure[i]), "temperatureK": float(temperature[i]),
              "positionM": list(topology["node_positions_m"][i]), "observable": bool(node_observable[i])}
             for i, node_id in enumerate(node_ids)]
    return {"edges": edges, "nodes": nodes}


def network_at(result: Dict[str, Any], index: int) -> Dict[str, Any]:
    """The network of one accepted sample of a liquid ``result.json`` (A1 topology fields)."""
    column = lambda key: [row[index] for row in result[key]]
    times = result.get("time_s") or []
    return {"sampleIndex": index, "timeS": times[index] if times else None,
            **network(result, edge_ids=result["edge_ids"], node_ids=result["node_ids"],
                      flow=column("edge_flow_kg_s"), edge_outlet_K=column("edge_outlet_temp_K"),
                      stagnant=result.get("edge_stagnant") or [False] * len(result["edge_ids"]),
                      pressure=column("node_pressure_Pa"), temperature=column("node_temperature_K"),
                      node_observable=column("node_observable"),
                      rho=float((result.get("fluid") or {})["rho_kg_m3"]))}


def _prepared(liquidrun: Dict[str, Any], device: Optional[str], threads: Optional[int]):
    """A copy with the execution overrides; refuses anything but a DAE (v3/v4) manifest."""
    raw = copy.deepcopy(liquidrun)
    version = raw.get("schemaVersion") or raw.get("version")
    try:
        numeric = float(version)
    except (TypeError, ValueError):
        numeric = 0.0
    if isinstance(version, bool) or numeric < 3:
        raise ValueError(f"the DAE API needs a DAE .liquidrun (v3/v4); got version {version!r}")
    overrides: Dict[str, Any] = {}
    if device is not None:
        raw.setdefault("execution", {})["device"] = device
        overrides["device"] = device
    if threads is not None:
        raw.setdefault("execution", {})["max_threads"] = int(threads)
        overrides["max_threads"] = int(threads)
    return raw, overrides


def is_time_marching(liquidrun: Dict[str, Any]) -> bool:
    analysis = liquidrun.get("analysis") or {}
    samples = (liquidrun.get("time") or {}).get("N_t")
    return analysis.get("mode") == "transient" and isinstance(samples, int) and samples >= 2


def export_liquidrun(cfdproj: Path | str, *, mode: Optional[str] = None, duration_s: Optional[float] = None,
                     solver_type: Optional[str] = None, project: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The ``.liquidrun`` the liquid panel would submit for this saved project.

    ``mode`` goes through the editor's ``setSimulationMode(mode, 'liquid')``,
    ``duration_s`` through the timeline strip's duration edit, ``solver_type``
    (the air mode) through ``setSimulationMode(mode, 'air')`` -- a hybrid run
    passes the air run's mode so both manifests bind the same document.
    ``project`` replaces the file's content (already-applied run settings).
    Returns ``{liquidrun, controllers, simulationMode, implementation}``.
    """
    if mode is not None and mode not in LIQUID_MODES:
        raise ValueError(f"mode must be one of {LIQUID_MODES}, got {mode!r}")
    cfdproj = Path(cfdproj).resolve()
    if project is None:
        _, project = read_json_source(cfdproj)
    options = {key: value for key, value in (("liquidMode", mode), ("durationS", duration_s),
                                               ("solverType", solver_type)) if value is not None}
    return compile_project("liquidrun", project, str(cfdproj), options=options or None)


def _labels(graph, kind: Optional[str], count: int) -> List[str]:
    if kind in ("cp", "pump", "valve", "hx", "pipe"):
        indices = getattr(graph, kind + "_indices")
        ids = [] if indices is None else [graph.edge_str_ids[i] for i in indices.edge_ids.tolist()]
    elif kind == "node":
        ids = list(graph.node_ids)
    elif kind == "cell" and getattr(graph, "cell_edge_id", None) is not None and graph.pipe_indices is not None:
        pipes = graph.pipe_indices.edge_ids.tolist()
        ids = [f"{graph.edge_str_ids[pipes[int(e)]]}#{i}" for i, e in enumerate(graph.cell_edge_id.tolist())]
    else:
        ids = []
    return ids if len(ids) == count else [f"[{i}]" for i in range(count)]


def _objectives(equation_graph, y, theta) -> Dict[str, Any]:
    graph = equation_graph.graph
    state = equation_graph.unpack(y, theta)
    out: Dict[str, Any] = {}
    solid = state["solid_temperature"]
    if solid.numel():
        out["max_chip_temperature_K"] = solid.max()
        out["mean_chip_temperature_K"] = solid.mean()
    if graph.cp_indices is not None and graph.cp_indices.edge_ids.numel():
        out["max_coolant_outlet_temperature_K"] = state["edge_outlet_temperature"][graph.cp_indices.edge_ids].max()
    primary = state["primary_return_temperature"]
    if primary.numel():
        out["mean_primary_return_temperature_K"] = primary.mean()
    if graph.pump_indices is not None and graph.pump_indices.edge_ids.numel():
        pumps = graph.pump_indices.edge_ids
        pressure, flow = state["pressure"], state["flow"][pumps]
        rise = pressure[graph.edge_to[pumps]] - pressure[graph.edge_from[pumps]]
        rho = theta[equation_graph.parameter_layout["fluid_rho"]][0]
        out["pump_hydraulic_power_W"] = (rise * flow / rho).sum()
    return out


def solve_liquid_steady(liquidrun: Dict[str, Any], *, objectives: Optional[Sequence[str]] = None,
                        gradient_parameters: Sequence[str] = DEFAULT_GRADIENT_PARAMETERS,
                        heat_multiplier: float = 1.0, device: Optional[str] = "cpu",
                        threads: Optional[int] = None, fd_check: int = 0, fd_relative_step: float = 1e-4,
                        inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Steady state, network, objectives and d(objective)/d(theta) for one DAE ``.liquidrun`` (v3/v4).

    ``inputs`` is queued with ``DAESimulation.set_inputs`` and the answer is the
    equilibrium query with it pending -- the server's ``inputs`` then
    ``equilibrium`` endpoints; the gradients are taken at those inputs. That
    needs a manifest with a next step (``analysis.mode`` transient), as on the
    server. ``device`` defaults to ``cpu`` (``None`` keeps the manifest's).

    Pure function of its inputs: nothing is written to disk. Raises
    ``ValueError`` for an input the DAE admission rejects.
    """
    import torch
    from backend.liquidcooling.dae_runtime import DAESimulation
    from backend.liquidcooling.postprocess.summaries import build_rack_summaries, summaries_json
    from backend.liquidcooling.postprocess.topology import result_topology
    from backend.liquidcooling.solver.dae_newton import solve_steady
    import numpy as np

    raw, overrides = _prepared(liquidrun, device, threads)
    commands = normalise_inputs(inputs)
    wanted = list(objectives) if objectives else list(OBJECTIVES)
    unknown = [name for name in wanted if name not in OBJECTIVES]
    if unknown:
        raise ValueError(f"unknown objectives {unknown}; choose from {list(OBJECTIVES)}")
    timings: Dict[str, float] = {}

    started = time.perf_counter()
    simulation = DAESimulation(raw)  # admission, validation, thread policy, product initializer
    timings["admitAndInitializeSeconds"] = time.perf_counter() - started
    definition = simulation.definition
    equation_graph = definition.equation_graph
    graph = equation_graph.graph
    layout = equation_graph.parameter_layout
    missing = [name for name in gradient_parameters if name not in layout]
    if missing:
        raise ValueError(f"unknown gradient parameters {missing}; available: {sorted(layout)}")

    if commands:
        if simulation.done:
            raise ValueError(
                "inputs are a command queued for the next accepted step (DAESimulation.set_inputs, the server's "
                "inputs endpoint), and this steady-mode .liquidrun (analysis.mode steady, N_t=1) has none: pass a "
                "transient-mode .liquidrun -- run_liquid_case(<.cfdproj>, mode='steady', inputs=...) compiles one "
                "-- or change the inputs in the project")
        simulation.set_inputs(**commands)  # validated exactly as the server validates the command

    # Product equilibrium query (the same call the liquid session answers), with any command pending.
    started = time.perf_counter()
    frame = simulation.equilibrium(heat_multiplier)
    timings["equilibriumSeconds"] = time.perf_counter() - started

    # Differentiable repeat of that steady solve.
    started = time.perf_counter()
    reference = graph.bc_P
    theta0 = definition.parameters(0, heat_multiplier=torch.as_tensor(heat_multiplier, dtype=reference.dtype,
                                                                      device=reference.device))
    rate = layout["hx_T_set_rate"]
    theta0 = torch.cat((theta0[:rate.start], torch.zeros_like(theta0[rate]), theta0[rate.stop:])).detach()
    if commands:
        # The actuator values the equilibrium actually used (its frame reports them per edge, in the
        # experiment's order = the parameter layout's), so the repeat solves the same problem.
        theta0 = theta0.clone()
        for field, key, ids in (("pump_rpm_ratio", "pump_rpm_ratios", simulation.experiment.pump_edge_ids),
                                ("valve_opening", "valve_openings", simulation.experiment.valve_edge_ids),
                                ("primary_inlet_K", "primary_inlet_K", simulation.metadata["hx_edge_ids"])):
            piece = layout[field]
            if piece.stop - piece.start != len(ids):
                raise RuntimeError(f"{field}: {piece.stop - piece.start} parameters for {len(ids)} edges")
            if ids:
                theta0[piece] = torch.as_tensor([frame["controls"][key][edge] for edge in ids],
                                                dtype=theta0.dtype, device=theta0.device)
    base = simulation.experiment.tensor_state.detach()
    conserved = (equation_graph.steady_neutral_storage_mask, base)
    theta = theta0.clone().requires_grad_(True)
    y, report = solve_steady(equation_graph.problem, base, theta, time=0., options=definition.newton_options,
                             conserved=conserved)
    values = _objectives(equation_graph, y, theta)
    timings["differentiableSolveSeconds"] = time.perf_counter() - started
    unpacked = equation_graph.unpack(y.detach(), theta0)
    chip_from_frame = torch.as_tensor(frame["chip_temp_K"], dtype=y.dtype, device=y.device)
    consistency = float((unpacked["solid_temperature"] - chip_from_frame).abs().max()) if chip_from_frame.numel() else 0.0

    started = time.perf_counter()
    gradients: Dict[str, Dict[str, Any]] = {}
    flat: Dict[str, Any] = {}
    for name in wanted:
        if name not in values:
            continue
        grad, = torch.autograd.grad(values[name], theta, retain_graph=True, allow_unused=True)
        grad = torch.zeros_like(theta) if grad is None else grad
        flat[name] = grad.detach()
        gradients[name] = {}
        for parameter in gradient_parameters:
            part = grad[layout[parameter]].detach().cpu().tolist()
            labels = _labels(graph, _KIND.get(parameter), len(part))
            gradients[name][parameter] = dict(zip(labels, part))
    timings["adjointSeconds"] = time.perf_counter() - started

    checks: List[Dict[str, Any]] = []
    if fd_check > 0:
        started = time.perf_counter()

        # A perturbation within the product tolerance would be accepted at
        # iteration 0 (the base state already satisfies it), so the difference
        # solves use a far tighter tolerance; the product tolerance is the
        # fallback where round-off cannot reach it.
        strict = dataclasses.replace(definition.newton_options, atol=min(definition.newton_options.atol, 1e-13),
                                     rtol=0.0, max_iterations=max(definition.newton_options.max_iterations, 60))

        def objective_at(vector):
            with torch.no_grad():
                try:
                    root, _ = solve_steady(equation_graph.problem, base, vector, time=0., options=strict,
                                           conserved=conserved)
                except RuntimeError:
                    fallbacks.append(True)
                    root, _ = solve_steady(equation_graph.problem, base, vector, time=0.,
                                           options=definition.newton_options, conserved=conserved)
                return {k: float(v) for k, v in _objectives(equation_graph, root, vector).items()}

        fallbacks: List[bool] = []

        values_at_base = objective_at(theta0)
        for name, grad in flat.items():
            candidates = []
            for parameter in gradient_parameters:
                piece = layout[parameter]
                labels = _labels(graph, _KIND.get(parameter), piece.stop - piece.start)
                for offset in range(piece.stop - piece.start):
                    candidates.append((abs(float(grad[piece.start + offset])), parameter, labels[offset], piece.start + offset))
            ranked = sorted(candidates, reverse=True)
            # A sensitivity counts as nonzero above round-off: |g|*max(1,|theta_i|)
            # > 1e-12*max(1,|f|). The k largest such components are checked; an
            # objective with none (e.g. a facility return temperature against the
            # secondary pump at fixed heat) is checked once, and its difference must vanish.
            magnitude = max(1.0, abs(values_at_base[name]))
            significant = [row for row in ranked
                           if row[0] * max(1.0, abs(float(theta0[row[3]]))) > 1e-12 * magnitude]
            chosen = significant[:fd_check] or ranked[:1]
            for _, parameter, label, index in chosen:
                value = float(theta0[index])
                step = fd_relative_step * max(1.0, abs(value))
                row: Dict[str, Any] = {"objective": name, "parameter": parameter, "component": label,
                                       "value": value, "step": step, "adjoint": float(grad[index]),
                                       "negligibleAdjoint": not significant}
                try:
                    def shifted(multiple):
                        vector = theta0.clone()
                        vector[index] += multiple * step
                        return objective_at(vector)[name]
                    try:
                        high = shifted(1)
                        row["scheme"] = "central"
                        row["finiteDifference"] = (high - shifted(-1)) / (2 * step)
                    except ValueError:
                        # The + side leaves the admissible set (e.g. a fully open valve):
                        # second-order one-sided difference from the inside.
                        row["scheme"] = "one-sided (3-point, from below)"
                        row["finiteDifference"] = (3 * values_at_base[name] - 4 * shifted(-1) + shifted(-2)) / (2 * step)
                    row["absoluteError"] = abs(row["finiteDifference"] - row["adjoint"])
                    scale = max(abs(row["finiteDifference"]), abs(row["adjoint"]))
                    # Relative error is meaningless between two round-off values.
                    row["relativeError"] = (None if not significant else
                                            row["absoluteError"] / scale if scale > 0 else 0.0)
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                checks.append(row)
        timings["finiteDifferenceSeconds"] = time.perf_counter() - started
        timings["finiteDifferenceToleranceFallbacks"] = len(fallbacks)

    cp_ids = simulation.metadata["cp_edge_ids"]
    hx_ids = simulation.metadata["hx_edge_ids"]
    column = lambda key: np.asarray(frame[key], dtype=float)[:, None]
    edge_index = {name: i for i, name in enumerate(graph.edge_str_ids)}
    edge_out = column("edge_outlet_temp_K")
    water = np.asarray([edge_out[edge_index[cp]] for cp in cp_ids]).reshape(len(cp_ids), 1)
    racks = summaries_json(build_rack_summaries(graph, {"m": column("edge_flow_kg_s")}, water,
                                                column("chip_temp_K"), column("node_temperature_K")))
    chip = dict(zip(cp_ids, frame["chip_temp_K"]))
    nets = network(result_topology(graph), edge_ids=graph.edge_str_ids, node_ids=graph.node_ids,
                   flow=frame["edge_flow_kg_s"], edge_outlet_K=frame["edge_outlet_temp_K"],
                   stagnant=frame["edge_stagnant"], pressure=frame["node_pressure_Pa"],
                   temperature=frame["node_temperature_K"], node_observable=frame["node_observable"],
                   rho=float(theta0[layout["fluid_rho"]][0]))
    evaluated = "steady equilibrium at sample 0 boundary values (DAEExperiment.equilibrium)"
    if commands:
        evaluated = ("steady equilibrium at sample 0 boundary values with the inputs pending "
                     "(DAESimulation.set_inputs, then DAEExperiment.equilibrium: the server's inputs and "
                     "equilibrium endpoints)")
    return {
        "format": "dcdtuni.api.liquid-steady/v1",
        "analysis": copy.deepcopy(simulation.metadata["analysis"]),
        "evaluatedAs": evaluated,
        "heatMultiplier": heat_multiplier,
        "inputs": commands,
        "controls": copy.deepcopy(frame["controls"]),
        "overrides": overrides,
        "configHash": simulation.config_hash,
        "liquidrunSha256": ident.canonical_sha256(liquidrun),
        "execution": simulation.metadata["execution"],
        "initialization": simulation.metadata["initialization_report"],
        "newton": {"equilibrium": frame["newton"],
                   "differentiableRepeat": {"iterations": report.iterations,
                                            "scaled_residual_inf": report.scaled_residual_inf,
                                            "backtracks": report.backtracks}},
        "diagnostics": frame["diagnostics"],
        "graph": {"nodes": len(graph.node_ids), "edges": len(graph.edge_str_ids), "coldPlates": len(cp_ids),
                  "heatExchangers": len(hx_ids), "pumps": len(simulation.experiment.pump_edge_ids),
                  "valves": len(simulation.experiment.valve_edge_ids), "unknowns": int(equation_graph.size),
                  "parameters": int(equation_graph.parameter_count)},
        "metrics": {
            "chipTemperatureK": chip,
            "maxChipTemperatureK": max(chip.values()) if chip else None,
            "primaryReturnK": dict(zip(hx_ids, frame["primary_return_K"])),
            "secondarySupplyK": dict(zip(hx_ids, frame["secondary_supply_K"])),
            "heatInputW": frame["diagnostics"].get("heat_input_W"),
            "primaryRejectionW": frame["diagnostics"].get("primary_rejection_W"),
            "energyBalanceW": frame["diagnostics"].get("energy_balance_W"),
            "racks": racks,
            **nets,
        },
        "objectives": {name: float(value.detach()) for name, value in values.items()},
        "gradients": gradients,
        "gradientParameters": list(gradient_parameters),
        "finiteDifferenceCheck": checks,
        "primalConsistencyMaxAbsK": consistency,
        "timings": timings,
        "objectBindings": copy.deepcopy(raw.get("object_bindings")),
    }


def solve_liquid_transient(liquidrun: Dict[str, Any], run_dir: Path | str, *,
                           inputs: Optional[Dict[str, Any]] = None, heat_multiplier: float = 1.0,
                           device: Optional[str] = "cpu", threads: Optional[int] = None) -> Dict[str, Any]:
    """March one time-marching DAE ``.liquidrun`` through the product batch path.

    ``runtime_batch.run_dae_manifest`` (what ``/api/liquid/solve`` runs for a DAE
    manifest) writes ``<run_dir>/log/run-<id>/`` -- ``input.liquidrun``,
    ``executed.liquidrun``, ``result.json``, ``executed-inputs.json``,
    ``checkpoint.json`` -- the run directory ``hybrid_air_case_cli --liquid-run``
    takes. ``inputs`` (and a ``heat_multiplier`` other than 1) is one command
    queued before the first step, as the server's inputs endpoint queues it, so
    it applies from sample 1. Returns the metrics at the final sample plus the
    run's extremes; the full series stay in the run's ``result.json``.
    """
    from backend.liquidcooling.runtime_batch import run_dae_manifest

    raw, overrides = _prepared(liquidrun, device, threads)
    if not is_time_marching(raw):
        raise ValueError(f"mode='transient' needs a time-marching .liquidrun (analysis.mode transient, N_t >= 2); "
                         f"this one has analysis={raw.get('analysis')}, N_t={(raw.get('time') or {}).get('N_t')} -- "
                         "compile the project with mode='transient' or pass a transient manifest")
    commands: Dict[str, Any] = dict(normalise_inputs(inputs) or {})
    if heat_multiplier != 1.0:
        commands["heat_multiplier"] = float(heat_multiplier)
    run_dir = Path(run_dir)
    started = time.perf_counter()
    run_dae_manifest(raw, run_dir, inputs=commands or None)
    elapsed = time.perf_counter() - started
    folder, = sorted((run_dir / "log").glob("run-*"))
    saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    final = len(saved["time_s"]) - 1
    cp_ids = [row["edge_id"] for row in saved.get("cp_details") or []]
    chip_final = {cp: saved["chip_temp_K"][i][final] for i, cp in enumerate(cp_ids)}
    last = saved["diagnostics"][final]
    balances = [abs(row.get("energy_balance_W", 0.0)) for row in saved["diagnostics"]]
    net = network_at(saved, final)
    return {
        "format": "dcdtuni.api.liquid-transient/v1",
        "mode": "transient",
        "analysis": saved.get("analysis"),
        "evaluatedAs": (f"transient march over {final + 1} samples (dt {raw['time']['dt_s']} s, backward Euler) through "
                        "runtime_batch.run_dae_manifest; metrics at the final sample"),
        "heatMultiplier": heat_multiplier,
        "inputs": normalise_inputs(inputs),
        "appliedInputs": saved.get("applied_inputs"),
        "overrides": overrides,
        "configHash": saved.get("config_hash"),
        "runId": saved.get("run_id"),
        "liquidrunSha256": ident.canonical_sha256(liquidrun),
        "completed": saved.get("completed"),
        "liquidRunDir": str(folder),
        "samples": final + 1,
        "final": {"seq": final, "timeS": saved["time_s"][final]},
        "timeS": saved["time_s"],
        "chipTemperatureK": chip_final,
        "maxChipTemperatureK": max(chip_final.values()) if chip_final else None,
        "peakChipTemperatureK": max((max(row) for row in saved["chip_temp_K"]), default=None),
        "primaryReturnK": {hx: saved["primary_return_K"][i][final] for i, hx in enumerate(saved.get("hx_edge_ids") or [])},
        "secondarySupplyK": {hx: saved["secondary_supply_K"][i][final]
                             for i, hx in enumerate(saved.get("hx_edge_ids") or [])},
        "heatInputW": last.get("heat_input_W"),
        "primaryRejectionW": last.get("primary_rejection_W"),
        "energyBalanceW": last.get("energy_balance_W"),
        "maxAbsEnergyBalanceW": max(balances) if balances else None,
        "racks": saved.get("rack_summaries"),
        "edges": net["edges"],
        "nodes": net["nodes"],
        "timings": {"marchSeconds": elapsed},
    }


def run_liquid_case(source: Path | str, *, output_root: Path | str | None = None, label: Optional[str] = None,
                    mode: str = "steady", inputs: Optional[Dict[str, Any]] = None,
                    duration_s: Optional[float] = None,
                    objectives: Optional[Sequence[str]] = None,
                    gradient_parameters: Sequence[str] = DEFAULT_GRADIENT_PARAMETERS,
                    heat_multiplier: float = 1.0, device: Optional[str] = "cpu", threads: Optional[int] = None,
                    fd_check: int = 0, fd_relative_step: float = 1e-4,
                    raise_on_failure: bool = False) -> Dict[str, Any]:
    """Compile (for a ``.cfdproj``) or load (``.liquidrun``) and solve; writes a dated result directory.

    ``mode`` is ``steady`` (equilibrium, network, objectives, gradients) or
    ``transient`` (the product batch march, see :func:`solve_liquid_transient`).
    For a ``.cfdproj`` the liquid side is compiled in that mode through the
    editor's mode switch, and ``duration_s`` sets the timeline length a
    transient run follows; a steady answer with ``inputs`` is compiled as
    transient so the command has a step to be queued for (the steady state does
    not depend on the horizon). ``inputs``: see the module docstring.
    ``device`` defaults to ``cpu``.

    The directory is ``<case>/log/api/<stamp>__liquid[_label]/`` for a
    ``.cfdproj`` (``<case>`` = its folder) and ``<output_root>/<stamp>__liquid``
    otherwise (``output_root`` is then required). A transient run's own
    directory (``log/run-<id>/``) is inside it.
    """
    if mode not in LIQUID_MODES:
        raise ValueError(f"mode must be one of {LIQUID_MODES}, got {mode!r}")
    commands = normalise_inputs(inputs)
    started_wall, started = ident.now_sgt(), time.perf_counter()
    source = Path(source).resolve()
    is_project = source.suffix == ".cfdproj"
    raw = read_source(source)  # before any directory is made for it
    parsed = parse_json_source(raw, source)  # so is text that is not JSON
    if output_root is None:
        if not is_project:
            raise ValueError("output_root (CLI --out-root) is required for a .liquidrun source: the result "
                             "directory of a .cfdproj is its case's log/api/, a .liquidrun has no case")
        output_root = case_api_root(source.parent)
    run_dir = create_run_dir(output_root, "liquid", label)
    compile_mode = "transient" if mode == "transient" or commands else "steady"
    result: Dict[str, Any] = {
        "format": RESULT_FORMAT, "kind": "liquid", "status": "failed", "failure": None,
        "runDir": str(run_dir), "startedAt": ident.iso(started_wall),
        "inputs": {"source": str(source), "sourceSha256": ident.sha256_bytes(raw), "mode": mode,
                   "inputs": commands, "durationS": duration_s,
                   "compiledLiquidMode": compile_mode if is_project else None,
                   "objectives": list(objectives) if objectives else list(OBJECTIVES),
                   "gradientParameters": list(gradient_parameters), "heatMultiplier": heat_multiplier,
                   "device": device, "threads": threads, "fdCheck": fd_check, "fdRelativeStep": fd_relative_step},
        "identity": {"repository": ident.repository_identity(), "host": ident.host_identity()},
        "stages": {}, "metrics": {}, "artifacts": {"resultJson": str(run_dir / "result.json")},
    }
    stage = "input"
    try:
        stage = "export"
        stage_started = time.perf_counter()
        if is_project:
            try:
                compiled = export_liquidrun(source, mode=compile_mode, duration_s=duration_s)
            except HeadlessCompileError as exc:
                raise ValueError(f"liquid export refused the project: {exc}") from exc
            liquidrun = compiled["liquidrun"]
            result["identity"]["frontend"] = {k: v for k, v in compiled.get("implementation", {}).items()
                                              if k != "compilerStderrTail"}
        else:
            if duration_s is not None:
                raise ValueError("duration_s applies to a .cfdproj (the timeline); a .liquidrun carries its own time grid")
            liquidrun = parsed
        write_json(run_dir / "input.liquidrun", liquidrun)
        result["artifacts"]["liquidrun"] = str(run_dir / "input.liquidrun")
        result["stages"]["export"] = {"seconds": time.perf_counter() - stage_started,
                                      "liquidrunSha256": ident.canonical_sha256(liquidrun),
                                      "version": liquidrun.get("schemaVersion") or liquidrun.get("version"),
                                      "analysis": liquidrun.get("analysis"),
                                      "samples": (liquidrun.get("time") or {}).get("N_t"),
                                      "dtS": (liquidrun.get("time") or {}).get("dt_s"),
                                      "simulationMode": compiled.get("simulationMode") if is_project else None}
        stage = "input"
        if mode == "transient" and not is_time_marching(liquidrun):
            raise ValueError(f"mode='transient' needs a time-marching .liquidrun (analysis.mode transient, "
                             f"N_t >= 2); got analysis={liquidrun.get('analysis')}, "
                             f"N_t={(liquidrun.get('time') or {}).get('N_t')}")
        stage = "solve"
        stage_started = time.perf_counter()
        if mode == "transient":
            metrics = solve_liquid_transient(liquidrun, run_dir, inputs=commands, heat_multiplier=heat_multiplier,
                                             device=device, threads=threads)
            result["stages"]["solve"] = {"seconds": time.perf_counter() - stage_started, **metrics["timings"]}
            result["artifacts"]["liquidRunDir"] = metrics["liquidRunDir"]
            result["metrics"] = metrics
            result["identity"]["solver"] = {"name": "differentiable_newton_dae (runtime_batch.run_dae_manifest)",
                                            "execution": json.loads((Path(metrics["liquidRunDir"]) /
                                                                     "input.liquidrun").read_text())
                                            .get("execution")}
        else:
            steady = solve_liquid_steady(liquidrun, objectives=objectives, gradient_parameters=gradient_parameters,
                                         heat_multiplier=heat_multiplier, device=device, threads=threads,
                                         fd_check=fd_check, fd_relative_step=fd_relative_step, inputs=commands)
            result["stages"]["solve"] = {"seconds": time.perf_counter() - stage_started, **steady["timings"]}
            result["metrics"] = steady
            result["identity"]["solver"] = {"name": "differentiable_newton_dae (solve_steady + _ImplicitRoot adjoint)",
                                            "execution": steady["execution"]}
        result["status"] = "succeeded"
    except Exception as exc:
        result["failure"] = {"stage": stage,
                             "reason": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-6000:]}
    finally:
        result["identity"]["python"] = ident.python_identity()
        result["finishedAt"] = ident.iso(ident.now_sgt())
        result["elapsedSeconds"] = time.perf_counter() - started
        write_json(run_dir / "result.json", result)
    if raise_on_failure and result["status"] != "succeeded":
        raise RuntimeError(f"{result['failure']['stage']}: {result['failure']['reason']}")
    return result
