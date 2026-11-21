"""GGA Newton-Raphson hydraulic solver for liquid cooling networks.

Implements the Global Gradient Algorithm (Todini & Pilati 1988):
- Schur complement: S = A · diag(1/g) · Aᵀ
- Dense solve via torch.linalg.solve
- Quadratic convergence in 5-15 iterations

Quasi-steady per-time-step hydraulics; no trajectory autograd contract.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

import torch
from torch import Tensor

from backend.liquidcooling.handlers.valve import effective_valve_kv

if TYPE_CHECKING:
    from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph


def _pipe_dp_friction(
    m: Tensor,
    D: float, L: float, A: float,
    K_minor: float, roughness: float,
    rho: float, mu: float,
    eps: float = 1e-8,
) -> Tensor:
    """Compute pipe friction pressure drop (no gravity), differentiable."""
    m_abs = torch.sqrt(m * m + eps * eps)
    v_abs = m_abs / (rho * A)
    Re = rho * v_abs * D / mu

    f_lam = 64.0 / (Re + eps)
    e_D = roughness / D
    log_arg = e_D / 3.7 + 5.74 / (Re + eps).pow(0.9)
    f_turb = 0.25 / (torch.log10(log_arg) + 1e-10).pow(2)
    sigma = torch.sigmoid((Re - 2300.0) / 200.0)
    f = (1.0 - sigma) * f_lam + sigma * f_turb

    coeff = (f * L / D + K_minor) / (2.0 * rho * A * A)
    return coeff * m * m_abs


def pipe_headloss(
    m: Tensor,
    D: float, L: float, A: float,
    K_minor: float, roughness: float, dh: float,
    rho: float, mu: float,
    g_acc: float = 9.81,
    eps: float = 1e-8,
) -> tuple[Tensor, Tensor]:
    """Pipe: Darcy-Weisbach friction + minor losses + gravity.

    dp = (f*L/D + K) * m*|m| / (2*rho*A^2) + rho*g*dh
    g  = d(dp)/d(m) via autograd (exact, includes df/dm term)
    """
    # Ensure m has gradient tracking for the derivative computation.
    # `.sum()` makes this work for a batch of edges as well as a single one: dp_e
    # depends only on m_e, so the gradient of the sum IS the per-edge derivative, and
    # one backward covers the whole slice instead of one per edge.
    m_for_grad = m.detach().requires_grad_(True)
    dp_friction = _pipe_dp_friction(m_for_grad, D, L, A, K_minor, roughness, rho, mu, eps)
    (g,) = torch.autograd.grad(dp_friction.sum(), m_for_grad, create_graph=False)

    # Compute dp with original m (preserves grad_fn if m.requires_grad)
    dp_friction_out = _pipe_dp_friction(m, D, L, A, K_minor, roughness, rho, mu, eps)
    dp_gravity = rho * g_acc * dh
    dp = dp_friction_out + dp_gravity

    return dp, g.detach()


def quadratic_headloss(
    m: Tensor,
    dp_a: float,
    dp_b: float,
    eps: float = 1e-8,
) -> tuple[Tensor, Tensor]:
    """Cold plate / heat exchanger: dp = dp_a * m * |m| + dp_b * m."""
    m_abs = torch.sqrt(m * m + eps * eps)
    dp = dp_a * m * m_abs + dp_b * m
    g = dp_a * 2.0 * m_abs + dp_b
    return dp, g


def pump_headloss(
    m: Tensor,
    H0: float,
    a_coef: float,
    rpm_ratio: float,
    rho: float,
    g_acc: float = 9.81,
) -> tuple[Tensor, Tensor]:
    """Pump: dp = -rho*g*(H0 - a*Q^2)*N^2  (negative = adds pressure)."""
    Q = m / rho
    N2 = rpm_ratio * rpm_ratio
    H = N2 * (H0 - a_coef * Q * Q)
    dp = -rho * g_acc * H
    g = 2.0 * g_acc * a_coef * m * N2 / rho
    return dp, g


def _at_time(values: Tensor, time_index: int) -> Tensor:
    """Read static [component], broadcast [component,1], or scheduled controls."""
    return values if values.ndim == 1 else values[:, 0 if values.shape[1] == 1 else time_index]


def _has_pressure_reference(graph: CompiledGraph, closed: Tensor) -> bool:
    """Every open-edge connected component needs a prescribed pressure.

    Include zero-flow dead ends: their pressure is recoverable through an open
    edge, but closure removes that datum. This also detects floating components
    before numerical roundoff can make a singular Laplacian appear solvable.
    """
    adjacency = [[] for _ in range(graph.N_nodes)]
    for start, end, shut in zip(graph.edge_from.tolist(), graph.edge_to.tolist(), closed.tolist()):
        if not shut:
            adjacency[start].append(end)
            adjacency[end].append(start)
    reached = set(torch.where(graph.bc_mask)[0].tolist())
    pending = list(reached)
    while pending:
        for node in adjacency[pending.pop()]:
            if node not in reached:
                reached.add(node)
                pending.append(node)
    return len(reached) == graph.N_nodes


def valve_headloss(m: Tensor, Kv: Tensor, rho: float, eps: float = 1e-8) -> tuple[Tensor, Tensor]:
    """Inverse ISA law: dp[Pa] = R m|m| with Kv[m3/h/sqrt(bar)].

    R = 1e5 * (rho/1000) * (3600/(rho*Kv))**2. Closed valves have no
    pressure-drop law: the solver enforces m=0 and removes their conductance.
    """
    safe_kv = torch.where(Kv == 0, torch.ones_like(Kv), Kv)
    resistance = 1e5 * (rho / 1000.) * (3600. / (rho * safe_kv)).square()
    m_abs = torch.sqrt(m.square() + eps**2)
    dp = resistance * m * m_abs
    derivative = resistance * (m_abs + m.square() / m_abs)
    return (torch.where(Kv == 0, torch.zeros_like(dp), dp),
            torch.where(Kv == 0, torch.zeros_like(derivative), derivative))


# ---------------------------------------------------------------------------
# Batch headloss computation over all edges
# ---------------------------------------------------------------------------

def _compute_all_headlosses(
    m: Tensor,
    graph: CompiledGraph,
    time_index: int = 0,
) -> tuple[Tensor, Tensor]:
    """Compute per-edge headloss dp and derivative g for every edge.

    Parameters
    ----------
    m : Tensor [N_edges]
        Current mass flow rates.
    graph : CompiledGraph
        Compiled network graph with all component parameters.

    Returns
    -------
    dp : Tensor [N_edges]
        Pressure drop across each edge (Pa).
    g : Tensor [N_edges]
        d(dp)/d(m) for each edge (Pa s / kg).
    """
    N_edges = graph.N_edges
    _dev = m.device
    dp = torch.zeros(N_edges, dtype=torch.float64, device=_dev)
    g = torch.zeros(N_edges, dtype=torch.float64, device=_dev)

    rho = graph.fluid_rho
    mu = graph.fluid_mu
    g_acc = graph.g_mag

    # Every headloss formula below is elementwise, so each component type is one
    # batched call over its own edge slice. The parameters are ALREADY tensors of the
    # right length — the previous per-edge loop converted them back to Python floats
    # with `.item()`, which is what made this 78% of the Newton solve.

    # --- pipes ---
    if graph.pipe_indices is not None:
        eids = graph.pipe_indices.edge_ids.to(_dev)
        pp = graph.pipe_params
        dp_e, g_e = pipe_headloss(
            m[eids],
            D=pp.D, L=pp.L, A=pp.A,
            K_minor=pp.K_minor, roughness=pp.roughness, dh=pp.dh,
            rho=rho, mu=mu, g_acc=g_acc,
        )
        dp[eids] = dp_e
        g[eids] = g_e

    # --- cold plates (quadratic) ---
    if graph.cp_indices is not None:
        eids = graph.cp_indices.edge_ids.to(_dev)
        cp = graph.cp_params
        dp_e, g_e = quadratic_headloss(m[eids], dp_a=cp.dp_a, dp_b=cp.dp_b)
        dp[eids] = dp_e
        g[eids] = g_e

    # --- pumps ---
    if graph.pump_indices is not None:
        eids = graph.pump_indices.edge_ids.to(_dev)
        pp = graph.pump_params
        rpm_b = _at_time(pp.rpm_ratio, time_index)
        dp_e, g_e = pump_headloss(
            m[eids], H0=pp.H0, a_coef=pp.a_coef, rpm_ratio=rpm_b,
            rho=rho, g_acc=g_acc,
        )
        dp[eids] = dp_e
        g[eids] = g_e

    # --- valves (ISA pressure loss, not a pipe surrogate) ---
    if graph.valve_indices is not None:
        eids = graph.valve_indices.edge_ids.to(_dev)
        vp = graph.valve_params
        kv = effective_valve_kv(vp.Kv_max, _at_time(vp.opening, time_index), vp.characteristic)
        dp[eids], g[eids] = valve_headloss(m[eids], kv, rho)

    # --- heat exchangers (quadratic) ---
    if graph.hx_indices is not None:
        eids = graph.hx_indices.edge_ids.to(_dev)
        hp = graph.hx_params
        dp_e, g_e = quadratic_headloss(m[eids], dp_a=hp.dp_a, dp_b=hp.dp_b)
        dp[eids] = dp_e
        g[eids] = g_e

    return dp, g


# ---------------------------------------------------------------------------
# GGA Newton-Raphson solver
# ---------------------------------------------------------------------------

def _gga_solve_step(
    graph: CompiledGraph,
    tol: float = 1e-6,
    max_iter: int = 50,
    m_init: Tensor | None = None,
    iteration_callback: Callable | None = None,
    time_index: int = 0,
) -> tuple[Tensor, Tensor, dict]:
    """Solve one quasi-steady hydraulic slice, with scalar diagnostics."""
    N_nodes = graph.N_nodes
    N_edges = graph.N_edges

    # Detect device from graph tensors so Newton works on CPU or CUDA
    _dev = graph.bc_P.device

    # The incidence matrix B[node, edge] has exactly two nonzeros per column:
    # B[from, e] = +1, B[to, e] = -1. Every product involving it is therefore an
    # O(E) gather/scatter over (edge_from, edge_to), not an O(N*E) dense matmul, and
    # the Schur complement it induces is a weighted graph Laplacian assembled in O(E).
    # Densifying B threw all of that away.
    ef = graph.edge_from.to(dtype=torch.long, device=_dev)
    et = graph.edge_to.to(dtype=torch.long, device=_dev)

    # Node masks
    bc_mask = graph.bc_mask.to(_dev)
    junc_mask = graph.junction_mask.to(_dev)
    bc_idx = torch.where(bc_mask)[0]      # indices of BC nodes

    # BC pressures
    P_bc = graph.bc_P.to(dtype=torch.float64, device=_dev)

    # Identify dead-end edges and nodes as masks rather than Python sets.
    # Dead-end: junction node with exactly 1 connected edge. Node degree is a bincount
    # over the edge endpoints, so this is O(E) instead of a Python walk over N_nodes
    # that indexed a tensor once per node.
    deg = (torch.bincount(ef, minlength=N_nodes)
           + torch.bincount(et, minlength=N_nodes))
    dead_node_mask = junc_mask & (deg == 1)
    dead_edge_mask = dead_node_mask[ef] | dead_node_mask[et]

    closed_edge_mask = torch.zeros(N_edges, dtype=torch.bool, device=_dev)
    if graph.valve_indices is not None:
        vp = graph.valve_params
        kv = effective_valve_kv(vp.Kv_max, _at_time(vp.opening, time_index), vp.characteristic)
        closed_edge_mask[graph.valve_indices.edge_ids] = kv == 0
    inactive_edge_mask = dead_edge_mask | closed_edge_mask

    # Free (active) junction nodes exclude dead-ends
    free_idx = torch.where(junc_mask & ~dead_node_mask)[0].to(_dev)
    N_free = free_idx.shape[0]

    # node -> row of the free (junction) block, -1 for nodes that are not free.
    # This replaces the dense row-slices A_free = B[free_idx, :] and B_bc = B[bc_idx, :].
    row_of = torch.full((N_nodes,), -1, dtype=torch.long, device=_dev)
    row_of[free_idx] = torch.arange(N_free, dtype=torch.long, device=_dev)
    rf = row_of[ef]           # [N_edges] free-row of each edge's from-node, or -1
    rt = row_of[et]           # [N_edges] free-row of each edge's to-node, or -1

    # BC pressures as a full-length vector, zero on non-BC nodes, so that
    # B_bc.T @ P_bc[bc_idx] is just Pb[ef] - Pb[et].
    Pb = torch.where(bc_mask, P_bc, torch.zeros_like(P_bc)).to(dtype=torch.float64)

    def _A_free_matvec(x: Tensor) -> Tensor:
        """A_free @ x, i.e. sum over each free node's incident edges with +/- signs."""
        out = torch.zeros(N_free, dtype=torch.float64, device=_dev)
        mf = rf >= 0
        mt = rt >= 0
        out.index_add_(0, rf[mf], x[mf])
        out.index_add_(0, rt[mt], -x[mt])
        return out

    # Initial guess for mass flows
    if m_init is not None:
        m = m_init.clone().to(dtype=torch.float64, device=_dev)
    else:
        m = torch.full((N_edges,), 0.01, dtype=torch.float64, device=_dev)

    # Ideal shutoff and terminal continuity both require zero flow.
    m = m.masked_fill(inactive_edge_mask, 0.0)

    if not _has_pressure_reference(graph, closed_edge_mask):
        return P_bc.clone(), m, {
            "converged": False, "iterations": 0, "max_dm": float("inf"),
            "mass_residual": float("inf"), "momentum_residual": float("inf"),
            "reason": "singular_pressure_system",
        }

    # Newton iteration
    converged = False
    iterations = 0
    max_dm = float("inf")
    g_min = 1e-4  # minimum Jacobian clamp to prevent singularity
    H_full = P_bc.clone()  # will be updated each iteration
    reason = "max_iterations"
    mass_residual = momentum_residual = float("inf")
    # Bound residual acceptance independently of user-supplied update tolerance.
    mass_tol = max(1e-10, min(tol, 1e-8))

    def recover_terminal_pressure(pressure, loss):
        # Terminal continuity fixes m=0, but gravity and pump shutoff head can
        # still give a nonzero pressure difference on the terminal edge.
        pressure = pressure.clone()
        if bool(dead_node_mask.any()):
            de = torch.where(dead_edge_mask)[0]
            dead_is_from = dead_node_mask[ef[de]]
            node = torch.where(dead_is_from, ef[de], et[de])
            neighbour = torch.where(dead_is_from, et[de], ef[de])
            pressure[node] = pressure[neighbour] + torch.where(dead_is_from, loss[de], -loss[de])
        return pressure

    def residuals(flow, pressure):
        loss, _ = _compute_all_headlosses(flow, graph, time_index)
        pressure = recover_terminal_pressure(pressure, loss)
        mismatch = (loss - pressure[ef] + pressure[et]).masked_fill(closed_edge_mask, 0.)
        mass = _A_free_matvec(flow)
        max_mass = mass.abs().max().item() if mass.numel() else 0.
        max_momentum = mismatch.abs().max().item() if mismatch.numel() else 0.
        scale = loss.masked_fill(closed_edge_mask, 0.).abs().max().item()
        return max_mass, max_momentum, 1e-5 + 1e-10 * scale

    for it in range(max_iter):
        # 1. Compute headloss and derivative for all edges
        dp, g_vec = _compute_all_headlosses(m, graph, time_index)
        if not bool(torch.isfinite(dp).all() & torch.isfinite(g_vec).all()):
            reason = "nonfinite_headloss"
            break

        # Clamp g to prevent singular Jacobian
        g_vec = torch.clamp(g_vec, min=g_min)

        # Remove closed-valve/dead-end contributions from the Schur system
        # or the flow update. g_vec is set to 1 only to keep the reciprocal finite;
        # inv_g is masked to 0 straight after.
        dp = dp.masked_fill(inactive_edge_mask, 0.0)
        g_vec = g_vec.masked_fill(inactive_edge_mask, 1.0)

        inv_g = (1.0 / g_vec).masked_fill(inactive_edge_mask, 0.0)  # [N_edges]

        # 2. Build Schur complement and solve for junction pressures
        if N_free == 0:
            # All nodes are BCs — pressures are fully known
            H_full = P_bc
        else:
            # S = A_free @ diag(1/g) @ A_free^T is a weighted graph Laplacian over the
            # free nodes: each edge contributes +w to both diagonals and -w to the two
            # off-diagonals. Assembling it that way is O(E); forming it as a dense
            # product is O(N_free^2 * E) — 404 ms per iteration at 500 free nodes
            # against ~1 ms here.
            S = torch.zeros(N_free, N_free, dtype=torch.float64, device=_dev)
            w = inv_g
            mf = rf >= 0
            mt = rt >= 0
            S.index_put_((rf[mf], rf[mf]), w[mf], accumulate=True)
            S.index_put_((rt[mt], rt[mt]), w[mt], accumulate=True)
            both = mf & mt
            S.index_put_((rf[both], rt[both]), -w[both], accumulate=True)
            S.index_put_((rt[both], rf[both]), -w[both], accumulate=True)

            # 3. Build RHS
            # From mass conservation A_free @ m_{k+1} = 0 with flow update
            # m_{k+1} = m_k + inv_g * (B^T H - dp):
            # S H_free = A_free (dp/g - m) - A_inv_g B_bc^T P_bc
            dp_over_g_minus_m = dp * inv_g - m     # [N_edges]
            rhs = _A_free_matvec(dp_over_g_minus_m)  # [N_free]

            bc_contrib = Pb[ef] - Pb[et]           # [N_edges] = B_bc.T @ P_bc[bc_idx]
            rhs = rhs - _A_free_matvec(inv_g * bc_contrib)  # [N_free]

            # 4. Solve for junction pressures
            try:
                H_free = torch.linalg.solve(S, rhs)    # [N_free]
            except torch.linalg.LinAlgError:
                reason = "singular_pressure_system"
                break

            # 5. Assemble full pressure vector
            H_full = P_bc.clone()
            H_full[free_idx] = H_free

        # 6. Update flows: m_new = m - (1/g) * (dp - B^T @ H_full)
        BtH = H_full[ef] - H_full[et]   # [N_edges] = B.T @ H_full, O(E) not O(N*E)
        m_new = m - inv_g * (dp - BtH)

        # 7. Enforce closed-valve and dead-end edges to 0
        m_new = m_new.masked_fill(inactive_edge_mask, 0.0)

        if not bool(torch.isfinite(m_new).all() & torch.isfinite(H_full).all()):
            reason = "nonfinite_state"
            break

        # 8. Check convergence
        max_dm = (m_new - m).abs().max().item()
        m = m_new
        iterations = it + 1

        if iteration_callback is not None:
            iteration_callback(it + 1, max_dm)

        if max_dm < tol:
            mass_residual, momentum_residual, momentum_tol = residuals(m, H_full)
            if not all(math.isfinite(value) for value in (mass_residual, momentum_residual, momentum_tol)):
                reason = "nonfinite_residual"
                break
            if mass_residual <= mass_tol and momentum_residual <= momentum_tol:
                converged = True
                reason = "converged"
                break

    final_loss, _ = _compute_all_headlosses(m, graph, time_index)
    P_full = recover_terminal_pressure(H_full, final_loss)

    if not converged and reason == "max_iterations":
        mass_residual, momentum_residual, _ = residuals(m, P_full)
    info = {
        "converged": converged,
        "iterations": iterations,
        "max_dm": max_dm,
        "mass_residual": mass_residual,
        "momentum_residual": momentum_residual,
        "reason": reason,
    }
    return P_full, m, info


def gga_solve(
    graph: CompiledGraph,
    tol: float = 1e-6,
    max_iter: int = 50,
    m_init: Tensor | None = None,
    iteration_callback: Callable | None = None,
) -> tuple[Tensor, Tensor, dict]:
    """Return P[N_nodes,N_t], m[N_edges,N_t] for each hydraulic control slice.

    Each slice solves momentum plus junction mass balance. The last converged
    flow warm-starts the next changed control. Identical adjacent controls reuse
    a converged solution (thermal loads do not enter these equations).

    tol bounds max|delta m| in kg/s; m_init is an optional [N_edges] flow
    guess. Mass residual acceptance is max(1e-10,min(tol,1e-8)) kg/s; momentum
    acceptance is 1e-5 Pa plus 1e-10 times the largest active edge pressure drop.

    Legacy scalar diagnostics are retained: iterations is the maximum iterations
    of a computed slice, max_dm the maximum final update, and converged requires
    all slices. per_step contains time_index, converged, iterations (zero for
    reuse), max_dm, mass_residual[kg/s], momentum_residual[Pa], reason and reused.
    iteration_callback retains (iteration, max_dm), called for computed slices.
    No thermal integration or end-to-end differentiability is provided here.
    """
    pressures, flows, per_step = [], [], []
    warm_start = m_init
    previous_controls = None
    for time_index in range(graph.N_t):
        controls = []
        if graph.pump_indices is not None:
            controls.append(_at_time(graph.pump_params.rpm_ratio, time_index))
        if graph.valve_indices is not None:
            controls.append(_at_time(graph.valve_params.opening, time_index))
        reusable = (previous_controls is not None and per_step[-1]["converged"]
                    and all(torch.equal(a, b) for a, b in zip(controls, previous_controls)))
        if reusable:
            P, m = pressures[-1], flows[-1]
            step = dict(per_step[-1], iterations=0, max_dm=0., reused=True)
        else:
            P, m, step = _gga_solve_step(graph, tol, max_iter, warm_start,
                                        iteration_callback, time_index)
            step["reused"] = False
            if step["converged"]:
                warm_start = m
        step["time_index"] = time_index
        pressures.append(P)
        flows.append(m)
        per_step.append(step)
        previous_controls = [value.clone() for value in controls]
    info = {
        "converged": all(step["converged"] for step in per_step),
        "iterations": max(step["iterations"] for step in per_step),
        "max_dm": max(step["max_dm"] for step in per_step),
        "per_step": per_step,
    }
    return torch.stack(pressures, dim=1), torch.stack(flows, dim=1), info
