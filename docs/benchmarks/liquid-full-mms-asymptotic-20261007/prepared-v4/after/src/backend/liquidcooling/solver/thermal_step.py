"""Conservative implicit-Euler thermal step with simultaneous closed-loop mixing.

Kelvin, kg/s, W and J/K throughout. Pipe fluid and CP solid store energy;
CP coolant is quasi-steady NTU (V_fluid is not a resolved storage state).
Facility temperature is a prescribed reservoir. Pumps/valves are adiabatic.
Gradients apply within a fixed nonzero-flow orientation, not across switching.
"""
from dataclasses import dataclass

import torch
from torch import Tensor

from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph


@dataclass
class ThermalStepResult:
    T_node: Tensor
    T_w: Tensor
    T_s: Tensor
    edge_outlet: Tensor
    diagnostics: dict[str, Tensor]


def thermal_step(
    graph: CompiledGraph,
    m: Tensor,
    T_node_prev: Tensor,
    T_w_prev: Tensor,
    T_s_prev: Tensor,
    *,
    dt: float | Tensor | None = None,
    time_index: int = 1,
) -> ThermalStepResult:
    """Advance explicit previous states without mutation or detaching tensors.

    Eliminating local storage equations gives each edge's affine outlet in its
    current upstream node temperature. A dense N_nodes solve closes all loops.
    Boundary exchange is positive INTO the network: enthalpy sent by reservoir
    nodes minus enthalpy returned to them. Pressure BCs represent external flow
    boundaries; zero-inflow source nodes retain their previous display T.
    """
    g = graph
    if m.ndim != 1 or m.shape[0] != g.N_edges or not m.is_floating_point():
        raise ValueError('m must be a floating [N_edges] tensor')
    def value(x):
        return torch.as_tensor(x, dtype=m.dtype, device=m.device)
    def finite(name, x):
        if not bool(torch.isfinite(x).all()):
            raise ValueError(f'{name} must be finite')
    delta = value(g.dt if dt is None else dt)
    if delta.ndim != 0 or not bool(torch.isfinite(delta)) or not bool(delta > 0):
        raise ValueError('dt must be a finite positive scalar')
    if not isinstance(time_index, int) or not 0 <= time_index < g.N_t:
        raise ValueError('time_index must be within graph time samples')
    previous = []
    for name, x, size in [('T_node_prev', T_node_prev, g.N_nodes),
                          ('T_w_prev', T_w_prev, g.N_cells), ('T_s_prev', T_s_prev, g.N_cp)]:
        x = value(x)
        if x.shape != (size,):
            raise ValueError(f'{name} must have shape [{size}]')
        finite(name, x)
        previous.append(x)
    node_old, water_old, solid_old = previous
    finite('m', m)
    cp, rho = value(g.fluid_cp), value(g.fluid_rho)
    for name, x in [('fluid_cp', cp), ('fluid_rho', rho)]:
        finite(name, x)
        if not bool(x > 0):
            raise ValueError(f'{name} must be positive')
    volume = value(g.cell_volume)
    finite('cell_volume', volume)
    if bool((volume <= 0).any()):
        raise ValueError('cell_volume must be positive')
    capacity = rho * cp * volume
    zero, one = m.new_zeros(()), m.new_ones(())
    def sample(x):
        x = value(x)
        finite('thermal parameter', x)
        return x if x.ndim == 1 else x[:, min(time_index, x.shape[1] - 1)]
    cell_heat = m.new_zeros(g.N_cells) if g.cell_heat_W is None else sample(g.cell_heat_W)
    heat = m.new_zeros(g.N_cp) if g.cp_params is None else sample(g.cp_params.Q_W)
    solid_capacity = m.new_zeros(g.N_cp) if g.cp_params is None else value(g.cp_params.C_solid)
    finite('C_solid', solid_capacity)
    if bool((solid_capacity <= 0).any()):
        raise ValueError('C_solid must be positive')
    def mapping(indices):
        return {} if indices is None else {int(e): i for i, e in enumerate(indices.edge_ids)}
    pipes, plates, exchangers = mapping(g.pipe_indices), mapping(g.cp_indices), mapping(g.hx_indices)
    upstream, downstream, outlet_a, outlet_b = [], [], [], []
    water_affine, solid_affine = [None] * g.N_cells, [None] * g.N_cp
    weights = m.abs()
    for e in range(g.N_edges):
        forward = bool(m[e] >= 0)
        u, v = int(g.edge_from[e]), int(g.edge_to[e])
        if not forward:
            u, v = v, u
        upstream.append(u)
        downstream.append(v)
        a, b = one, zero
        adv = weights[e] * cp
        if e in pipes:
            i = pipes[e]
            start, end = int(g.pipe_indices.cell_start[i]), int(g.pipe_indices.cell_end[i])
            cells = range(start, end) if forward else range(end - 1, start - 1, -1)
            for c in cells:
                storage = capacity[c] / delta
                denom = storage + adv
                a, b = adv * a / denom, (storage * water_old[c] + cell_heat[c] + adv * b) / denom
                water_affine[c] = (u, a, b)
        elif e in plates or e in exchangers:
            i = plates[e] if e in plates else exchangers[e]
            params = g.cp_params if e in plates else g.hx_params
            ua = value(params.UA)[i]
            finite('UA', ua)
            if not bool(ua >= 0):
                raise ValueError('UA must be nonnegative')
            # Exact zero advection; a safe inactive denominator avoids NaN grads.
            safe_adv = torch.where(adv > 0, adv, one)
            eps = torch.where(adv > 0, -torch.expm1(-ua / safe_adv), zero)
            if e in plates:
                conductance = adv * eps
                storage = solid_capacity[i] / delta
                sa = conductance / (storage + conductance)
                sb = (storage * solid_old[i] + heat[i]) / (storage + conductance)
                solid_affine[i] = (u, sa, sb)
                a, b = one - eps + eps * sa, eps * sb
            else:
                facility = sample(params.T_secondary)[i]
                a, b = one - eps, eps * facility
        outlet_a.append(a)
        outlet_b.append(b)
    if any(x is None for x in water_affine + solid_affine):
        raise ValueError('thermal storage indices do not cover the graph states')
    up = torch.tensor(upstream, dtype=torch.long, device=m.device)
    down = torch.tensor(downstream, dtype=torch.long, device=m.device)
    ea = torch.stack(outlet_a) if outlet_a else m.new_empty(0)
    eb = torch.stack(outlet_b) if outlet_b else m.new_empty(0)
    incoming = m.new_zeros(g.N_nodes).index_add(0, down, weights)
    outgoing = m.new_zeros(g.N_nodes).index_add(0, up, weights)
    identity = torch.eye(g.N_nodes, dtype=m.dtype, device=m.device)
    rows, rhs, reservoir = [], [], []
    bc = value(g.bc_T) if g.bc_T_series is None else sample(g.bc_T_series)
    for n in range(g.N_nodes):
        fixed = bool(g.bc_T_mask[n])
        dry = bool(incoming[n] == 0)
        reservoir.append(fixed or bool(g.bc_mask[n]) or dry)
        if fixed or dry:
            rows.append(identity[n])
            rhs.append(bc[n] if fixed else node_old[n])
        else:
            mask = down == n
            fractions = weights[mask] / incoming[n]
            rows.append(identity[n].index_add(0, up[mask], -fractions * ea[mask]))
            rhs.append((fractions * eb[mask]).sum())
    matrix, target = torch.stack(rows), torch.stack(rhs)
    finite('thermal matrix', matrix)
    finite('thermal right hand side', target)
    try:
        node = torch.linalg.solve(matrix, target)
    except RuntimeError as exc:
        raise RuntimeError(f'thermal linear solve failed at time index {time_index}: {exc}') from exc
    if not bool(torch.isfinite(node).all()):
        raise RuntimeError(f'thermal linear solve produced nonfinite state at time index {time_index}')
    linear_residual = matrix @ node - target
    if bool(linear_residual.abs().max() > 1e-7 * (1 + target.abs().max())):
        raise RuntimeError(f'thermal linear solve residual too large at time index {time_index}')
    def restore(affine):
        return torch.stack([a * node[u] + b for u, a, b in affine]) if affine else m.new_empty(0)
    water, solid = restore(water_affine), restore(solid_affine)
    outlet = ea * node[up] + eb
    if not all(bool(torch.isfinite(x).all()) for x in (water, solid, outlet)):
        raise RuntimeError(f'thermal solve produced nonfinite storage/outlet state at time index {time_index}')
    storage_rate = ((water - water_old) * capacity).sum() / delta
    storage_rate = storage_rate + ((solid - solid_old) * solid_capacity).sum() / delta
    hx_ids = torch.tensor(list(exchangers), dtype=torch.long, device=m.device)
    rejection = (weights[hx_ids] * cp * (node[up[hx_ids]] - outlet[hx_ids])).sum()
    received = m.new_zeros(g.N_nodes).index_add(0, down, weights * cp * outlet)
    sent = outgoing * cp * node
    reservoirs = torch.tensor(reservoir, dtype=torch.bool, device=m.device)
    boundary = (sent - received)[reservoirs].sum()
    source = cell_heat.sum() + heat.sum()
    return ThermalStepResult(node, water, solid, outlet, {
        'linear_residual_K': linear_residual,
        'mass_residual_kg_s': incoming - outgoing,
        'heat_input_W': source,
        'storage_rate_W': storage_rate,
        'hx_rejection_W': rejection,
        'boundary_exchange_W': boundary,
        'energy_balance_W': source + boundary - storage_rate - rejection,
    })
