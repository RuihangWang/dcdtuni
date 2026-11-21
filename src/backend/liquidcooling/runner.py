"""Orchestrator: read .liquidrun → compile → solve → extract results → save."""

import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import torch

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.adam_odil import solve
from backend.liquidcooling.solver.hybrid import hybrid_solve
from backend.liquidcooling.postprocess.results import LiquidResult, RackSummary, ServerSummary, CpDetail
from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer, StateInitializer, BCICMonitor


def _extract_cp_monitor(model, graph, monitor_cp_ids):
    """Extract T_chip, T_water_out, m for monitored cold plates (real-time)."""
    result = {}
    active_index = getattr(model, "thermal_time_index", None)
    t = -1 if active_index is None else active_index
    if not monitor_cp_ids or graph.cp_indices is None:
        return result
    with torch.no_grad():
        T_all = getattr(model, "thermal_node_history", None)
        if T_all is None:
            T_all = model._build_T_all_nodes(dict(model.state))
        for cp_str_id in monitor_cp_ids:
            if cp_str_id not in graph.edge_str_ids:
                continue
            global_idx = graph.edge_str_ids.index(cp_str_id)
            local_mask = (graph.cp_indices.edge_ids == global_idx)
            if not local_mask.any():
                continue
            local_idx = local_mask.nonzero(as_tuple=True)[0][0].item()

            T_chip = model.state["T_s"][local_idx, t].item()
            m_val = model.state["m"][global_idx, t].abs().item()

            # NTU-effectiveness for T_water_out
            UA = graph.cp_params.UA[local_idx].item()
            m_abs_cp = m_val * graph.fluid_cp + 1e-10
            NTU = UA / m_abs_cp
            eff = 1.0 - np.exp(-NTU) if m_val > 0 else 0.0
            from_node = graph.edge_from[global_idx].item()
            inlet_node = from_node if model.state["m"][global_idx, t] >= 0 else graph.edge_to[global_idx].item()
            T_in = T_all[inlet_node, t].item()
            T_water_out = T_in + eff * (T_chip - T_in)

            result[cp_str_id] = {
                "T_chip": round(T_chip, 2),
                "T_water_out": round(T_water_out, 2),
                "m": round(m_val, 4),
            }
    return result


def _extract_cdu_monitor(model, graph):
    """Extract CDU-level metrics (supply/return pressure, total flow, supply T).

    Identifies the CDU supply BC as the BC node that has outgoing edges, and
    the return BC as the BC node that has incoming edges.  Returns a dict with:

      supply_P_Pa     — pressure at supply BC node (from model.state["P"])
      return_P_Pa     — pressure at return BC node
      total_flow_kg_s — sum of |m| on the supply BC's outgoing edges
      supply_T_K      — solved supply temperature, or prescribed BC temperature

    This keeps the legacy first-pressure-BC identification heuristic; it is
    not a multi-CDU asset mapping. Every metric uses the active thermal sample,
    or the final sample for legacy callers without an active index.
    """
    result = {}
    active_index = getattr(model, "thermal_time_index", None)
    t = -1 if active_index is None else active_index
    with torch.no_grad():
        P = model.state["P"]  # [N_nodes, N_t]

        supply_node = None
        return_node = None

        for i in range(graph.N_nodes):
            if not graph.bc_mask[i]:
                continue
            has_out = len(graph.node_out_edges[i]) > 0
            has_in  = len(graph.node_in_edges[i]) > 0
            if has_out and supply_node is None:
                supply_node = i
            if has_in and return_node is None:
                return_node = i

        if supply_node is not None:
            result["supply_P_Pa"] = round(P[supply_node, t].item(), 2)
            # Total flow: sum of absolute mass flows on outgoing edges
            total_flow = sum(
                model.state["m"][eidx, t].abs().item()
                for eidx in graph.node_out_edges[supply_node]
            )
            result["total_flow_kg_s"] = round(total_flow, 4)
            # Use the already solved frame, never reconstruct a different one.
            history = getattr(model, "thermal_node_history", None)
            if history is not None:
                result["supply_T_K"] = round(history[supply_node, t].item(), 2)
            elif graph.bc_T_mask[supply_node]:
                series = getattr(graph, "bc_T_series", None)
                temperature = (graph.bc_T[supply_node] if series is None
                               else series[supply_node, t])
                result["supply_T_K"] = round(temperature.item(), 2)

        if return_node is not None:
            result["return_P_Pa"] = round(P[return_node, t].item(), 2)

    return result


def run_liquid_solve(
    liquidrun_path: Path,
    case_dir: Path = None,
    device: str | None = None,
    callback: Callable = None,
    monitor_cp_ids: list[str] = None,
) -> LiquidResult:
    """
    Full pipeline:
    1. Read .liquidrun JSON
    2. Pydantic validate → LiquidrunConfig
    3. Compile → CompiledGraph
    4. Build LiquidODILModel
    5. Smart initialization (BC pressures, nonzero flow seed)
    6. Adam solve
    7. Extract results from model state
    8. Save to case/Liquid/log/<timestamp>/
    """
    liquidrun_path = Path(liquidrun_path)
    if case_dir is None:
        # Infer: .../model/x.liquidrun → .../
        case_dir = liquidrun_path.parent.parent
    case_dir = Path(case_dir)

    # 1. Read + validate
    raw = json.loads(liquidrun_path.read_text())
    from backend.liquidcooling.liquidrun_dae_schema import is_dae_manifest
    if is_dae_manifest(raw):
        if device is not None and device != raw.get('execution', {}).get('device'):
            raise ValueError('device override conflicts with the explicit DAE execution manifest')
        from backend.liquidcooling.runtime_batch import run_dae_manifest
        return run_dae_manifest(raw, case_dir, callback)
    device = device or 'cpu'
    from backend.liquidcooling.liquidrun_schema import resolve_run_input
    config, controllers = resolve_run_input(raw)
    if config.version == '2.0':
        if device != 'cpu':
            raise ValueError('liquidrun v2 requires CPU PyTorch execution')
        from backend.liquidcooling.runtime_batch import run_transient_manifest
        return run_transient_manifest(config, controllers, case_dir, callback)

    if callback:
        callback({"stage": "validated", "projectName": config.projectName})

    # 2. Compile
    graph = compile_graph(config, device=device)

    if callback:
        callback({"stage": "compiled", "N_nodes": graph.N_nodes,
                  "N_edges": graph.N_edges, "N_cells": graph.N_cells,
                  "N_cp": graph.N_cp, "N_t": graph.N_t})

    # 3. Build model and move to device
    bc_enforcer = BoundaryConditionEnforcer(graph)
    model = LiquidODILModel(graph, bc_enforcer=bc_enforcer)
    if device == "cuda" and torch.cuda.is_available():
        model = model.cuda()
        bc_enforcer = bc_enforcer.to(model.state["P"].device)
    graph = model.graph

    # 4. Smart initialization (topology-aware BFS)
    StateInitializer(graph).initialize(model.state)

    n_params = sum(p.numel() for p in model.parameters())
    if callback:
        callback({"stage": "model_built", "n_parameters": n_params})

    # 4b. torch.compile on CUDA — skip in server threads (CUDA graph TLS assertion)
    _in_server_thread = not (threading.current_thread() is threading.main_thread())
    if device == "cuda" and torch.cuda.is_available() and not _in_server_thread:
        model = torch.compile(model, mode="reduce-overhead")
        if callback:
            callback({"stage": "torch_compiled", "mode": "reduce-overhead"})
    elif callback and device == "cuda":
        callback({"stage": "torch_compiled", "mode": "eager (server thread)"})

    # 5. Solve
    log_dir = _make_log_dir(case_dir)

    # Wrap callback to include cdu_monitor (always) and cp_monitor (when requested)
    bc_ic_monitor = BCICMonitor(graph)
    if callback:
        _orig_cb = callback
        def _monitor_callback(log_dict):
            if log_dict.get("epoch") is not None:
                log_dict["cdu_monitor"] = _extract_cdu_monitor(model, model.graph)
                if monitor_cp_ids:
                    log_dict["cp_monitor"] = _extract_cp_monitor(
                        model, model.graph, monitor_cp_ids
                    )
                bc_ic_metrics = bc_ic_monitor.compute(
                    {k: v.data for k, v in model.state.items()}, bc_enforcer
                )
                log_dict["total_bc"] = bc_ic_metrics["total_bc"]
                log_dict["total_ic"] = bc_ic_metrics["total_ic"]
            _orig_cb(log_dict)
        solve_callback = _monitor_callback
    else:
        solve_callback = callback

    logs = hybrid_solve(model, config.solver, log_dir=log_dir,
                        callback=solve_callback, print_every=100,
                        bc_enforcer=bc_enforcer)

    # 6. Extract results
    result = _extract_results(model, graph, logs, config)

    # 7. Save
    result.save_json(log_dir / "liquid_result.json")
    graph.to_json(log_dir / "compiled_graph.json")
    shutil.copy2(liquidrun_path, log_dir / liquidrun_path.name)

    if callback:
        callback({"stage": "done", "converged": result.converged,
                  "final_loss": result.final_loss, "epochs": result.epochs})

    return result


def _make_log_dir(case_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_dir = case_dir / "log" / timestamp
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _extract_results(model: LiquidODILModel, graph, logs, config=None) -> LiquidResult:
    """Extract numpy arrays from the model's converged state."""
    graph = graph.to("cpu")
    state = {k: v.detach().cpu().numpy() for k, v in model.state.items()}

    # M3: Use real edge IDs from compiled graph
    edge_id_names = list(graph.edge_str_ids)

    # Chip temperature
    chip_temp = state.get("T_s", np.array([]))

    # Node temperatures (needed for rack supply temp lookups and CP inlet temps)
    with torch.no_grad():
        T_history = getattr(model, "thermal_node_history", None)
        if T_history is None:
            T_history = model._build_T_all_nodes(dict(model.state))
        T_all = T_history.detach().cpu().numpy()

    # M2: Compute actual cold plate outlet water temperatures via NTU-effectiveness
    water_outlet = np.zeros_like(chip_temp)
    if graph.N_cp > 0 and graph.cp_indices is not None:
        cp_edge_ids = graph.cp_indices.edge_ids.numpy()
        m_abs = np.abs(state["m"][cp_edge_ids])  # [N_cp, N_t]
        m_abs_cp = m_abs * graph.fluid_cp + 1e-10  # [N_cp, N_t]
        UA = graph.cp_params.UA.numpy()[:, np.newaxis]  # [N_cp, 1]
        NTU = UA / m_abs_cp  # [N_cp, N_t]
        eff = np.where(m_abs > 0, 1.0 - np.exp(-NTU), 0.)  # [N_cp, N_t]
        cp_from_nodes = graph.cp_indices.node_from.numpy()
        cp_to_nodes = graph.cp_indices.node_to.numpy()
        T_in = np.where(state["m"][cp_edge_ids] >= 0,
                        T_all[cp_from_nodes], T_all[cp_to_nodes])
        T_s = chip_temp  # [N_cp, N_t]
        water_outlet = T_in + eff * (T_s - T_in)

    cp_details = []
    if graph.N_cp > 0 and graph.cp_indices is not None:
        for local_i in range(graph.N_cp):
            global_edge_i = graph.cp_indices.edge_ids[local_i].item()
            edge_str = graph.edge_str_ids[global_edge_i]
            from_node = graph.cp_indices.node_from[local_i].item()
            to_node = graph.cp_indices.node_to[local_i].item()
            cp_details.append({
                "edge_id": edge_str,
                "chip_temp_K": chip_temp[local_i],
                "water_inlet_temp_K": T_in[local_i],
                "water_outlet_temp_K": water_outlet[local_i],
                "inlet_pressure_Pa": np.where(state["m"][global_edge_i] >= 0, state["P"][from_node], state["P"][to_node]),
                "outlet_pressure_Pa": np.where(state["m"][global_edge_i] >= 0, state["P"][to_node], state["P"][from_node]),
            })

    # Time axis
    time_s = np.arange(graph.N_t) * graph.dt

    final_log = logs[-1] if logs else None

    # M5: Use convergence_tol from config (if available) instead of hardcoded 1.0
    if config is not None:
        conv_tol = config.solver.convergence_tol
    else:
        conv_tol = 1e-6
    converged = (bool(model.hybrid_converged) if hasattr(model, "hybrid_converged")
                 else final_log.total < conv_tol if final_log else False)

    # M1: Populate rack summaries (pass T_all for actual per-node supply temps)
    rack_summaries = _build_rack_summaries(graph, state, water_outlet, chip_temp, T_all)

    return LiquidResult(
        converged=converged,
        final_loss=final_log.total if final_log else float("inf"),
        epochs=final_log.epoch if final_log else 0,
        node_pressure_Pa=state["P"],
        node_ids=graph.node_ids,
        edge_flow_kg_s=state["m"],
        edge_ids=edge_id_names,
        cell_temp_K=state.get("T_w", np.array([])),
        cell_positions=graph.cell_position.numpy() if graph.cell_position.numel() > 0 else np.array([]),
        chip_temp_K=chip_temp,
        water_outlet_temp_K=water_outlet,
        time_s=time_s,
        rack_summaries=rack_summaries,
        cp_details=[
            CpDetail(
                edge_id=cd["edge_id"],
                chip_temp_K=cd["chip_temp_K"],
                water_inlet_temp_K=cd["water_inlet_temp_K"],
                water_outlet_temp_K=cd["water_outlet_temp_K"],
                inlet_pressure_Pa=cd["inlet_pressure_Pa"],
                outlet_pressure_Pa=cd["outlet_pressure_Pa"],
            ) for cd in cp_details
        ],
    )


# Shared with runtime sessions; retain the historical private import name.
from backend.liquidcooling.postprocess.summaries import build_rack_summaries as _build_rack_summaries
