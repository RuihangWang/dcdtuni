"""Two-stage hybrid solver: Newton GGA hydraulics + direct thermal march."""

import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import torch

from backend.liquidcooling.schema import SolverConfig
from backend.liquidcooling.solver.common import SolveLog, print_log, save_loss_csv


def hybrid_solve(
    model,
    config: SolverConfig,
    log_dir: Path = None,
    callback: Callable = None,
    print_every: int = 100,
    bc_enforcer=None,
) -> list:
    """Two-phase hybrid solver: Newton hydraulics + direct thermal march."""
    from backend.liquidcooling.solver.newton_hydraulic import gga_solve

    t_start = time.time()
    logs = []
    epoch_counter = 0

    # Phase 1: GGA Newton hydraulics
    def _newton_cb(iteration, max_dm):
        nonlocal epoch_counter
        log = SolveLog(
            epoch=epoch_counter,
            total=max_dm,
            losses={"newton_max_dm": max_dm},
            wall_time_s=time.time() - t_start,
        )
        logs.append(log)
        epoch_counter += 1
        if callback:
            callback(asdict(log))

    P_newton, m_newton, info = gga_solve(
        model.graph, iteration_callback=_newton_cb,
    )
    model.hydraulic_info = info
    model.hybrid_converged = False
    if not info["converged"]:
        failed = [step for step in info.get("per_step", []) if not step["converged"]]
        raise RuntimeError(f"hydraulic solve failed; thermal trajectory not published: {failed or info}")

    model.set_hydraulic_state(P_newton, m_newton)
    if bc_enforcer is not None:
        bc_enforcer.enforce(model.state)

    # Phase 2: Direct thermal solve
    def _thermal_cb(t_step, n_steps, max_chip_K):
        nonlocal epoch_counter
        log = SolveLog(
            epoch=epoch_counter,
            total=max_chip_K,
            losses={"max_chip_temp_K": max_chip_K, "thermal_step": float(t_step)},
            wall_time_s=time.time() - t_start,
        )
        logs.append(log)
        epoch_counter += 1
        if callback:
            callback({**asdict(log), "time_index": t_step,
                      "time_s": t_step * model.graph.dt})

    T_node = _solve_thermal_direct(model, model.graph, timestep_callback=_thermal_cb)

    losses = model.thermal_forward(T_all_nodes_override=T_node)
    loss_dict = {k: v.item() for k, v in losses.items() if k != "total"}
    loss_dict["newton_iterations"] = float(info["iterations"])
    loss_dict["newton_max_dm"] = info["max_dm"]

    diagnostics = model.thermal_diagnostics
    linear_max = max((d["linear_residual_K"].abs().max().detach().item() for d in diagnostics), default=0.)
    energy_max = max((d["energy_balance_W"].abs().detach().item() for d in diagnostics), default=0.)
    loss_dict.update(thermal_linear_residual_K=linear_max, thermal_energy_balance_W=energy_max,
                     hydraulic_converged=1.0)
    # Legacy ODIL handler losses remain available but use smoothed advection and
    # may use a different BDF order. They do not define this implicit-Euler solve.
    cp_total = linear_max
    model.hybrid_converged = True
    final_log = SolveLog(
        epoch=epoch_counter,
        total=cp_total,
        losses=loss_dict,
        wall_time_s=time.time() - t_start,
    )
    logs.append(final_log)
    if callback:
        callback({**asdict(final_log), "time_index": model.graph.N_t - 1,
                  "time_s": (model.graph.N_t - 1) * model.graph.dt})
    else:
        print_log(final_log)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        save_loss_csv(logs, log_dir / "loss_history.csv")
        torch.save(model.state_dict(), log_dir / "model_state.pt")

    return logs


def _solve_thermal_direct(model, graph, timestep_callback=None):
    """March current signed flow samples through the conservative pure kernel.

    The tensor history is retained for gradients. Detached copies go only to
    legacy display parameters, after each computed step. t=0 is initial data.
    """
    from backend.liquidcooling.solver.thermal_step import thermal_step

    g = graph
    m = model.state["m"]
    node = m.new_full((g.N_nodes,), g.T_fluid_init)
    boundary = g.bc_T if g.bc_T_series is None else g.bc_T_series[:, 0]
    node = torch.where(g.bc_T_mask.to(m.device), boundary.to(m), node)
    water = (m.new_full((g.N_cells,), g.T_fluid_init) if g.cell_T_init is None
             else g.cell_T_init.to(m))
    solid = m.new_full((g.N_cp,), g.T_solid_init)
    nodes, waters, solids = [node], [water], [solid]
    diagnostics = []
    with torch.no_grad():
        model.state["T_w"][:, 0].copy_(water)
        model.state["T_s"][:, 0].copy_(solid)
    for t in range(1, g.N_t):
        result = thermal_step(g, m[:, t], node, water, solid, time_index=t)
        node, water, solid = result.T_node, result.T_w, result.T_s
        nodes.append(node)
        waters.append(water)
        solids.append(solid)
        diagnostics.append(result.diagnostics)
        with torch.no_grad():
            model.state["T_w"][:, t].copy_(water)
            model.state["T_s"][:, t].copy_(solid)
        if timestep_callback is not None:
            # Make exactly the solved prefix available before monitors run.
            model.thermal_node_history = torch.stack(nodes, dim=1)
            model.thermal_time_index = t
            try:
                timestep_callback(t, g.N_t, solid.max().detach().item() if solid.numel() else 0.)
            finally:
                # Outside a transient callback, legacy monitors read the final sample.
                model.thermal_time_index = None
    model.thermal_node_history = torch.stack(nodes, dim=1)
    model.thermal_water_history = torch.stack(waters, dim=1)
    model.thermal_solid_history = torch.stack(solids, dim=1)
    model.thermal_diagnostics = diagnostics
    return model.thermal_node_history
