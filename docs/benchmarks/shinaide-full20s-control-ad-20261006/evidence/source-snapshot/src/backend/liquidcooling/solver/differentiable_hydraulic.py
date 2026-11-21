"""First-order implicit hydraulic VJP around the converged own-repository GGA.

Newton iterations are not retained. Backward differentiates the exact physical
headloss residual, then solves its adjoint. Topology/valve-closure masks are fixed
within a derivative; exact shutoff has zero opening sensitivity by convention.
"""
import copy

import torch
from torch.autograd.function import once_differentiable

from backend.liquidcooling.handlers.valve import effective_valve_kv
from .newton_hydraulic import _compute_all_headlosses, gga_solve

# Include independent physical fields only; topology and integer characteristics
# are discrete. A layout parameter can feed both D and A through ordinary Torch.
_FIELDS = {
    'pipe_params': ('D', 'L', 'dh', 'K_minor', 'A', 'roughness'),
    'cp_params': ('dp_a', 'dp_b'),
    'pump_params': ('H0', 'a_coef', 'rpm_ratio'),
    'valve_params': ('Kv_max', 'opening'),
    'hx_params': ('dp_a', 'dp_b'),
}


def _with_values(graph, paths, values):
    result = copy.copy(graph)
    for group in _FIELDS:
        if getattr(graph, group) is not None:
            setattr(result, group, copy.copy(getattr(graph, group)))
    for (group, field), value in zip(paths, values):
        setattr(result if group is None else getattr(result, group), field, value)
    return result


class _ImplicitHydraulic(torch.autograd.Function):
    @staticmethod
    def forward(ctx, graph, paths, warm_start, tolerance, iterations, *parameters):
        detached = [value.detach() for value in parameters]
        solve_graph = _with_values(graph, paths, detached)
        # The existing pipe Jacobian uses autograd internally, even though the
        # converged state is intentionally detached from the Newton iterations.
        with torch.enable_grad():
            P, m, info = gga_solve(solve_graph, tol=tolerance, max_iter=iterations,
                                 m_init=None if warm_start is None else warm_start.detach())
        if not info['converged']:
            raise RuntimeError(f"implicit hydraulic forward failed: {info['per_step'][0]['reason']}")
        P, m = P[:, 0].detach(), m[:, 0].detach()
        if not bool(torch.isfinite(P).all() and torch.isfinite(m).all()):
            raise RuntimeError('implicit hydraulic forward produced non-finite state')
        ctx.graph, ctx.paths = solve_graph, paths
        ctx.save_for_backward(m, P, *parameters)
        return P, m

    @staticmethod
    @once_differentiable
    def backward(ctx, grad_P, grad_m):
        m, P, *parameters = ctx.saved_tensors
        values = [p.detach().requires_grad_(True) for p in parameters]
        graph = _with_values(ctx.graph, ctx.paths, values)
        free = ~graph.bc_mask
        active = torch.ones(graph.N_edges, dtype=torch.bool, device=m.device)
        if graph.valve_params is not None:
            vp = graph.valve_params
            opening = vp.opening[:, 0] if vp.opening.ndim == 2 else vp.opening
            kv = effective_valve_kv(vp.Kv_max, opening, vp.characteristic)
            active[graph.valve_indices.edge_ids] = kv.detach() != 0
        incidence = graph.incidence.to_dense() if graph.incidence.is_sparse else graph.incidence
        A = incidence.to(m)[free][:, active]
        b_m = torch.zeros_like(m) if grad_m is None else grad_m
        b_P = torch.zeros_like(P) if grad_P is None else grad_P

        with torch.enable_grad():
            probe = m.detach().requires_grad_(True)
            dp, _ = _compute_all_headlosses(probe, graph)
            # Use the exact residual derivative, not Newton safeguards/floors.
            derivative = torch.autograd.grad(dp.sum(), probe, retain_graph=True)[0].detach()[active]
            rhs_m, rhs_P = b_m[active], b_P[free]
            n_edges, n_free = derivative.numel(), int(free.sum())
            rhs = torch.cat((rhs_m, rhs_P))
            if n_edges + n_free:
                # J = [[diag(dp/dm), -A.T], [A, 0]]. Normal operation uses
                # the smaller node Schur solve; zero-slope edges use the full
                # saddle-point matrix, without inventing a regularized gradient.
                def full_adjoint():
                    J = torch.cat((torch.cat((torch.diag(derivative), -A.T), 1),
                                   torch.cat((A, m.new_zeros(n_free, n_free)), 1)), 0)
                    return torch.linalg.solve(J.T, rhs)
                try:
                    if bool((derivative.abs() < 1e-14).any()):
                        adjoint = full_adjoint()
                    else:
                        inv = derivative.reciprocal()
                        dual_P = (torch.linalg.solve((A * inv) @ A.T, rhs_P + A @ (inv * rhs_m))
                                  if n_free else m.new_empty(0))
                        dual_m = inv * (rhs_m - A.T @ dual_P)
                        adjoint = torch.cat((dual_m, dual_P))
                    dual_m, dual_P = adjoint[:n_edges], adjoint[n_edges:]
                    residual = torch.cat((derivative * dual_m + A.T @ dual_P - rhs_m,
                                          -A @ dual_m - rhs_P))
                    if not bool(torch.isfinite(adjoint).all()) or float(residual.abs().max()) > 1e-7 * (1 + float(rhs.abs().max())):
                        adjoint = full_adjoint()
                        dual_m, dual_P = adjoint[:n_edges], adjoint[n_edges:]
                        residual = torch.cat((derivative * dual_m + A.T @ dual_P - rhs_m,
                                              -A @ dual_m - rhs_P))
                    if not bool(torch.isfinite(adjoint).all()) or float(residual.abs().max()) > 1e-7 * (1 + float(rhs.abs().max())):
                        raise RuntimeError('non-finite hydraulic adjoint')
                except RuntimeError as exc:
                    raise RuntimeError('hydraulic adjoint is singular or ill-conditioned') from exc
            else:
                dual_m = m.new_empty(0)
            prescribed = torch.where(graph.bc_mask, graph.bc_P, torch.zeros_like(graph.bc_P))
            momentum = dp - prescribed[graph.edge_from] + prescribed[graph.edge_to]
            objective = (-dual_m.detach() * momentum[active]).sum() + (b_P.detach() * prescribed).sum()
            gradients = torch.autograd.grad(objective, values, allow_unused=True)
        gradients = [torch.zeros_like(p) if g is None else g for p, g in zip(parameters, gradients)]
        return (None, None, None, None, None, *gradients)


def hydraulic_step(graph, *, warm_start=None, tolerance=1e-10, iterations=80):
    """Return differentiable (pressure Pa, mass flow kg/s) for a one-sample graph.

    Sensitivities include pump speed/head, valve opening/Kv, pipe geometry and
    roughness, component resistance, pressure BCs and fluid rho/mu. Supply tensors
    through graph fields; this wrapper preserves their outer computation graphs.
    Thermal-only parameters enter the subsequent thermal kernel directly.
    """
    if graph.N_t != 1:
        raise ValueError('hydraulic_step requires a single time slice')
    paths = [(None, name) for name in ('bc_P', 'fluid_rho', 'fluid_mu', 'g_mag')]
    for group, fields in _FIELDS.items():
        if getattr(graph, group) is not None:
            paths.extend((group, field) for field in fields)
    values = []
    for group, field in paths:
        value = getattr(graph if group is None else getattr(graph, group), field)
        value = torch.as_tensor(value, dtype=torch.float64, device=graph.bc_P.device)
        if not bool(torch.isfinite(value).all()):
            raise ValueError(f'non-finite hydraulic parameter: {group}.{field}')
        values.append(value)
    return _ImplicitHydraulic.apply(graph, paths, warm_start, tolerance, iterations, *values)
