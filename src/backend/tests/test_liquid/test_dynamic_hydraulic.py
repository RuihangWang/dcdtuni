"""Behavioral contracts for valve physics and quasi-steady control schedules."""
import json
import math
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.solver import newton_hydraulic as nh


def _graph(opening=(1.0, 0.5, 0.5, 1.0), characteristic="linear", loop=False,
           rpm=(1.0, 1.0, 0.6, 0.6), reverse=False):
    raw = json.loads((Path(__file__).parent / "minimal.liquidrun").read_text())
    raw["time"]["N_t"] = len(opening)
    raw["nodes"][0]["bc"]["P_Pa"] = 100000.0 if reverse else 200000.0
    raw["nodes"][1]["bc"]["P_Pa"] = 200000.0 if reverse else 100000.0
    raw["edges"] = [{"id": "valve", "type": "valve", "from": "supply", "to": "return",
                      "params": {"Kv_max": 2.5, "opening": list(opening),
                                 "characteristic": characteristic}}]
    if loop:
        raw["nodes"][1] = {"id": "return", "type": "junction", "position_m": [0, 0, 1]}
        raw["edges"].append({"id": "pump", "type": "pump", "from": "return", "to": "supply",
                              "params": {"H0_m": 20.0, "a_coef": 5e5, "rpm_ratio": list(rpm)}})
    return compile_graph(LiquidrunConfig.model_validate(raw))


@pytest.mark.parametrize("characteristic", ["linear", "equal_percentage", "quick_opening"])
@pytest.mark.parametrize("reverse", [False, True])
def test_valve_schedule_matches_isa_flow_and_direction(characteristic, reverse):
    graph = _graph(characteristic=characteristic, reverse=reverse)
    _, m, info = nh.gga_solve(graph)
    alpha = torch.tensor([1., .5, .5, 1.], dtype=torch.float64)
    factor = {"linear": alpha, "equal_percentage": 50. ** (alpha - 1.),
              "quick_opening": alpha.sqrt()}[characteristic]
    # Exactly 1 bar: independent ISA oracle in m3/h, converted once to kg/s.
    expected = 2.5 * factor * math.sqrt(1. / (graph.fluid_rho / 1000.)) * graph.fluid_rho / 3600.
    if reverse:
        expected = -expected
    assert info["converged"]
    torch.testing.assert_close(m[0], expected, rtol=1e-8, atol=1e-10)
    assert abs(m[0, 1]) < abs(m[0, 0])


def test_pump_schedule_changes_single_reference_loop_flow():
    graph = _graph(opening=(1., 1., 1., 1.), loop=True)
    P, m, info = nh.gga_solve(graph)
    rho = graph.fluid_rho
    resistance = 1e5 * (rho / 1000.) * (3600. / (rho * 2.5)) ** 2
    rpm = torch.tensor([1., 1., .6, .6], dtype=torch.float64)
    # Affinity law H = N^2*H0 - a*Q^2 (D1): rho*g*(N^2*H0 - a*m^2/rho^2) = R*m^2.
    expected = (rho * graph.g_mag * 20. * rpm**2 /
                (resistance + graph.g_mag * 5e5 / rho)).sqrt()
    assert info["converged"]
    torch.testing.assert_close(m[0], expected, rtol=1e-8, atol=1e-10)
    torch.testing.assert_close(m[0], m[1], rtol=0, atol=1e-10)
    assert (graph.incidence @ m)[graph.junction_mask].abs().max() < 1e-10
    torch.testing.assert_close(P[graph.bc_mask], graph.bc_P[graph.bc_mask, None].expand(-1, graph.N_t))


def test_constant_static_and_broadcast_controls_retain_solution():
    graph = _graph(opening=(.7,) * 4, loop=True, rpm=(.8,) * 4)
    P, m, info = nh.gga_solve(graph)
    graph.valve_params.opening = graph.valve_params.opening[:, 0]
    graph.pump_params.rpm_ratio = graph.pump_params.rpm_ratio[:, :1]
    P_static, m_static, static_info = nh.gga_solve(graph)
    assert info["converged"] and static_info["converged"]
    torch.testing.assert_close(P_static, P, rtol=0, atol=0)
    torch.testing.assert_close(m_static, m, rtol=0, atol=0)
    torch.testing.assert_close(m, m[:, :1].expand_as(m), rtol=0, atol=0)
    assert len(info["per_step"]) == graph.N_t
    assert all(isinstance(step["iterations"], int) for step in info["per_step"])


@pytest.mark.parametrize("characteristic", ["linear", "equal_percentage", "quick_opening"])
def test_fully_closed_valve_has_exactly_zero_flow_and_handler_agrees(characteristic):
    from backend.liquidcooling.handlers.valve import ValveBatchHandler
    graph = _graph(opening=(1., 0., 0., 1.), characteristic=characteristic)
    P, m, info = nh.gga_solve(graph)
    assert info["converged"]
    assert torch.equal(m[:, 1:3], torch.zeros_like(m[:, 1:3]))
    assert m[0, 0] > 0 and m[0, 3] > 0
    residual = ValveBatchHandler(graph.valve_params, graph.fluid_rho).compute_residuals(
        {"P": P, "m": m}, graph.valve_indices, 1., 1)["momentum"]
    assert residual.abs().max() < 1e-8


def test_nonfinite_later_control_does_not_hide_behind_first_step_success():
    graph = _graph(opening=(1., 1., 1., 1.), loop=True)
    graph.pump_params.rpm_ratio[0, 2] = float("nan")
    _, _, info = nh.gga_solve(graph)
    assert not info["converged"]
    assert info["per_step"][0]["converged"]
    assert not info["per_step"][2]["converged"]
    assert info["per_step"][3]["converged"]
    assert info["per_step"][2]["reason"] == "nonfinite_headloss"


def test_large_tolerance_cannot_accept_gross_momentum_residual():
    graph = _graph(opening=(1.,))
    _, _, info = nh.gga_solve(graph, tol=1e20, max_iter=1)
    assert not info["converged"]


def test_closed_valve_isolation_reports_unsolvable_pressure_reference():
    graph = _graph(opening=(1., 0.), loop=True, rpm=(1., 1.))
    # Two valves in series connect a pressure reference to a free node. Closing
    # both isolates the node: zero leakage must not manufacture a pressure datum.
    from backend.liquidcooling.graphcompiling.compiled_graph import ValveIndices
    graph.valve_indices = ValveIndices(torch.tensor([0, 1]), graph.edge_from, graph.edge_to)
    graph.valve_params.D = graph.valve_params.D.repeat(2)
    graph.valve_params.Kv_max = graph.valve_params.Kv_max.repeat(2)
    graph.valve_params.characteristic = graph.valve_params.characteristic.repeat(2)
    graph.valve_params.opening = graph.valve_params.opening.repeat(2, 1)
    graph.pump_indices = graph.pump_params = None
    _, m, info = nh.gga_solve(graph)
    assert not info["converged"]
    assert info["per_step"][0]["converged"]
    assert not info["per_step"][1]["converged"]
    assert info["per_step"][1]["reason"] == "singular_pressure_system"
    assert torch.equal(m[:, 1], torch.zeros(2, dtype=torch.float64))

@pytest.mark.parametrize("sign", [-1., 1.])
def test_valve_headloss_derivative_matches_isa_pressure_drop(sign):
    graph = _graph(opening=(.5,))
    m = torch.tensor([sign * .2], dtype=torch.float64)
    dp, derivative = nh._compute_all_headlosses(m, graph)
    rho = graph.fluid_rho
    R = 1e5 * (rho / 1000.) * (3600. / (rho * 1.25)) ** 2
    torch.testing.assert_close(dp, R * m * m.abs(), rtol=1e-12, atol=1e-10)
    torch.testing.assert_close(derivative, 2. * R * m.abs(), rtol=1e-12, atol=1e-10)


def test_closing_only_edge_to_dead_end_does_not_invent_pressure():
    graph = _graph(opening=(1., 0.), loop=True, rpm=(1., 1.))
    # The same one-valve two-node topology, with only its inlet pressure fixed.
    graph.N_edges = 1
    graph.edge_from = graph.edge_from[:1]
    graph.edge_to = graph.edge_to[:1]
    graph.pump_indices = graph.pump_params = None
    _, m, info = nh.gga_solve(graph)
    assert info["per_step"][0]["converged"]
    assert not info["per_step"][1]["converged"]
    assert info["per_step"][1]["reason"] == "singular_pressure_system"
    assert m[0, 1] == 0.


def test_dead_end_pressure_retains_static_head_in_residual_check():
    raw = json.loads((Path(__file__).parent / "minimal.liquidrun").read_text())
    raw["nodes"][1] = {"id": "return", "type": "junction", "position_m": [0., 1., 0.]}
    raw["edges"][0]["params"]["waypoints_m"] = [[0., 0., 0.], [0., 1., 0.]]
    graph = compile_graph(LiquidrunConfig.model_validate(raw))
    P, m, info = nh.gga_solve(graph)
    assert info["converged"]
    assert torch.equal(m, torch.zeros_like(m))
    expected_drop = graph.fluid_rho * graph.g_mag * graph.pipe_params.dh[0]
    assert abs(expected_drop) > 1000.
    torch.testing.assert_close(P[0] - P[1], expected_drop.expand(graph.N_t), rtol=0, atol=1e-8)


def test_nonfinite_trial_residual_cannot_pass_relative_tolerance():
    graph = _graph(opening=(1.,))
    graph.bc_P[0] = 1e200  # finite input, but its first Newton trial overflows m²
    _, _, info = nh.gga_solve(graph, tol=1e250, max_iter=1)
    assert not info["converged"]


def _valve_kv_oracle(openings, characteristic, kv_max=2.5):
    values, gradients = [], []
    for opening in openings:
        if opening <= 0.:
            value, gradient = 0., 0.  # chosen closed-endpoint backward convention
        elif characteristic == "linear":
            value, gradient = kv_max * opening, kv_max
        elif characteristic == "equal_percentage":
            value = kv_max * 50. ** (opening - 1.)
            gradient = value * math.log(50.)
        else:
            value = kv_max * math.sqrt(opening)
            gradient = kv_max / (2. * math.sqrt(opening))
        values.append(value)
        gradients.append(gradient)
    return torch.tensor(values, dtype=torch.float64), torch.tensor(gradients, dtype=torch.float64)


@pytest.mark.parametrize("characteristic", ["linear", "equal_percentage", "quick_opening"])
def test_valve_helper_closed_opening_gradients_are_finite(characteristic):
    from backend.liquidcooling.handlers.valve import effective_valve_kv
    openings = [-.1, 0., 1e-12, .5, 1.]
    graph = _graph(opening=openings, characteristic=characteristic)
    params = graph.valve_params
    params.opening.requires_grad_(True)
    kv = effective_valve_kv(params.Kv_max[:, None], params.opening, params.characteristic[:, None])
    gradient, = torch.autograd.grad(kv.sum(), params.opening)
    expected, derivative = _valve_kv_oracle(openings, characteristic)
    assert torch.isfinite(gradient).all()
    torch.testing.assert_close(kv[0], expected, rtol=1e-14, atol=0.)
    torch.testing.assert_close(gradient[0], derivative, rtol=1e-14, atol=0.)


@pytest.mark.parametrize("characteristic", ["linear", "equal_percentage", "quick_opening"])
def test_valve_handler_closed_opening_gradients_are_finite(characteristic):
    from backend.liquidcooling.handlers.valve import ValveBatchHandler
    openings = [-.1, 0., 1e-12, .5, 1.]
    graph = _graph(opening=openings, characteristic=characteristic)
    graph.valve_params.opening.requires_grad_(True)
    pressure = graph.bc_P[:, None].expand(-1, graph.N_t)
    residual = ValveBatchHandler(graph.valve_params, graph.fluid_rho).compute_residuals(
        {"P": pressure, "m": torch.zeros(1, graph.N_t, dtype=torch.float64)},
        graph.valve_indices, 1., 1)["momentum"]
    gradient, = torch.autograd.grad(residual.sum(), graph.valve_params.opening)
    kv, derivative = _valve_kv_oracle(openings, characteristic)
    # Handler's existing ISA smoothing, with exactly one bar driving pressure.
    isa_scale = math.sqrt(1. / (graph.fluid_rho / 1000.) + 1e-10) * graph.fluid_rho / 3600.
    assert torch.isfinite(gradient).all()
    torch.testing.assert_close(residual[0], -kv * isa_scale, rtol=1e-14, atol=0.)
    torch.testing.assert_close(gradient[0], -derivative * isa_scale, rtol=1e-14, atol=0.)
