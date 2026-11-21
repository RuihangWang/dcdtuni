"""Tensor-only transient liquid rollout for calibration/control optimization.

Returns all samples including t=0. Hydraulics are quasi-steady, thermal storage is
implicit Euler. The same production kernels power the JSON runtime, but no JSON,
float conversion of controls, or no_grad boundary interrupts this trajectory.
"""
from dataclasses import dataclass
from typing import Any, Callable

import torch
from torch import Tensor

from backend.liquidcooling.graphcompiling.time_slice import time_slice
from .differentiable_hydraulic import hydraulic_step
from .thermal_step import thermal_step


@dataclass
class TensorFrame:
    pressure: Tensor
    flow: Tensor
    node_temperature: Tensor
    water_temperature: Tensor
    solid_temperature: Tensor
    edge_outlet: Tensor


@dataclass
class TensorTrajectory:
    time_s: Tensor
    pressure: Tensor
    flow: Tensor
    node_temperature: Tensor
    water_temperature: Tensor
    solid_temperature: Tensor
    edge_outlet: Tensor
    diagnostics: list[dict[str, Tensor]]
    controller_state: Any = None


# A controller receives the PREVIOUS accepted frame and explicit state. Its
# tensor outputs replace one tick's scheduled controls; state can be PIDState.
Controller = Callable[[int, TensorFrame, Any], tuple[dict[str, Tensor], Any]]


def differentiable_rollout(graph, *, pump_rpm_ratio=None, valve_opening=None,
                           heat_W=None, dt=None, initial_state=None,
                           controller: Controller | None = None, controller_state=None):
    """Run a compiled graph with optional [component, sample] tensor schedules.

    Schedules may also be [component] constants. Supported controller output keys
    are pump_rpm_ratio and valve_opening, each a [component] tensor. New state must
    be returned explicitly, allowing re-entrant objectives and reproducible runs.
    This API provides first-order derivatives, with fixed topology/flow regimes.
    """
    device = graph.bc_P.device
    def tensor(value):
        return torch.as_tensor(value, dtype=torch.float64, device=device)
    delta = tensor(graph.dt if dt is None else dt)
    if delta.ndim != 0 or not bool(torch.isfinite(delta)) or not bool(delta > 0):
        raise ValueError('dt must be a finite positive scalar')
    if graph.N_t < 1:
        raise ValueError('rollout requires at least one sample')
    specs = {'pump_rpm_ratio': ('pump_params', 'rpm_ratio'),
             'valve_opening': ('valve_params', 'opening'), 'heat_W': ('cp_params', 'Q_W')}
    schedules = {}
    for key, value in [('pump_rpm_ratio', pump_rpm_ratio), ('valve_opening', valve_opening), ('heat_W', heat_W)]:
        if value is None:
            continue
        group, field = specs[key]
        params = getattr(graph, group)
        if params is None:
            raise ValueError(f'{key} supplied but the graph has no corresponding component')
        count = getattr(params, field).shape[0]
        value = tensor(value)
        if value.shape == (count,):
            value = value[:, None].expand(count, graph.N_t)
        if value.shape != (count, graph.N_t) or not bool(torch.isfinite(value).all()):
            raise ValueError(f'{key} must be finite with shape [{count}, {graph.N_t}] or [{count}]')
        schedules[key] = value

    def tick_graph(index, commands):
        g = time_slice(graph, index)
        values = {key: value[:, index] for key, value in schedules.items()}
        if set(commands) - {'pump_rpm_ratio', 'valve_opening'}:
            raise ValueError('controller returned an unsupported control field')
        values.update(commands)
        for key, value in values.items():
            group, field = specs[key]
            params = getattr(g, group)
            if params is None:
                raise ValueError(f'controller supplied {key} for an absent component')
            value = tensor(value)
            count = getattr(params, field).shape[0]
            if value.shape != (count,) or not bool(torch.isfinite(value).all()):
                raise ValueError(f'{key} must be a finite [{count}] vector')
            if bool((value < 0).any()) or (key == 'valve_opening' and bool((value > 1).any())):
                raise ValueError(f'{key} is outside its physical range')
            setattr(params, field, value[:, None])
        return g

    nodes = tensor(graph.T_fluid_init).expand(graph.N_nodes).clone()
    nodes = torch.where(graph.bc_T_mask, graph.bc_T, nodes)
    water = (tensor(graph.T_fluid_init).expand(graph.N_cells).clone() if graph.cell_T_init is None else graph.cell_T_init.clone())
    solid = tensor(graph.T_solid_init).expand(graph.N_cp).clone()
    if initial_state is not None:
        if len(initial_state) != 3:
            raise ValueError('initial_state is (node, water, solid) temperature')
        nodes, water, solid = [tensor(v) for v in initial_state]
    for value, count in [(nodes, graph.N_nodes), (water, graph.N_cells), (solid, graph.N_cp)]:
        if value.shape != (count,) or not bool(torch.isfinite(value).all()):
            raise ValueError('invalid initial temperature state')
    g = tick_graph(0, {})
    pressure, flow = hydraulic_step(g)
    outlet = nodes[torch.where(flow >= 0, graph.edge_from, graph.edge_to)]
    frames = [TensorFrame(pressure, flow, nodes, water, solid, outlet)]
    diagnostics = []
    state = controller_state
    for index in range(1, graph.N_t):
        commands = {}
        if controller is not None:
            commands, state = controller(index, frames[-1], state)
        g = tick_graph(index, commands)
        pressure, flow = hydraulic_step(g, warm_start=frames[-1].flow)
        thermal = thermal_step(g, flow, nodes, water, solid, dt=delta, time_index=0)
        nodes, water, solid = thermal.T_node, thermal.T_w, thermal.T_s
        frames.append(TensorFrame(pressure, flow, nodes, water, solid, thermal.edge_outlet))
        diagnostics.append(thermal.diagnostics)
    values = {name: torch.stack([getattr(frame, name) for frame in frames], dim=1)
              for name in TensorFrame.__dataclass_fields__}
    return TensorTrajectory(torch.arange(graph.N_t, dtype=torch.float64, device=device) * delta,
                            **values, diagnostics=diagnostics, controller_state=state)
