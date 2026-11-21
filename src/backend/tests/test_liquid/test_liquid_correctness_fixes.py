"""Regression locks for the 2026-10-21 liquid correctness fixes (D1-D6, HX model).

Every expected value is derived here from the manifest numbers in
dae_loop_fixture.py, never read back from the production residual.
Report: _tmp/campaign_reports/liquid_correct.md (sections 3.1/3.2).
"""
from __future__ import annotations

import math

import pytest
import torch

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.tests.test_liquid.dae_loop_fixture import CP, RHO, loop_manifest

G = 9.81


def frame_of(sim):
    frame = sim.latest_frame
    edges = {e: i for i, e in enumerate(sim.metadata['edge_ids'])}
    nodes = {n: i for i, n in enumerate(sim.metadata['node_ids'])}
    return frame, edges, nodes


# --------------------------------------------------------------------- D1 ---

@pytest.mark.parametrize('rpm', [1., .8, .6])
def test_d1_pump_obeys_the_affinity_law_at_reduced_speed(rpm):
    """H_N(Q) = N^2*H0 - a*Q^2 with (H0, a) the rated curve."""
    sim = DAESimulation(loop_manifest(rpm=rpm))
    frame, edges, nodes = frame_of(sim)
    m = frame['edge_flow_kg_s'][edges['pump']]
    rise = frame['node_pressure_Pa'][nodes['dis']] - frame['node_pressure_Pa'][nodes['ret']]
    q = m / RHO
    assert rise == pytest.approx(RHO * G * (rpm**2 * 8. - 4e7 * q * q), rel=1e-9)
    if rpm < 1:
        legacy = RHO * G * rpm**2 * (8. - 4e7 * q * q)
        assert abs(rise - legacy) > 1e-3 * rise


def test_d1_pump_law_version_is_registered_in_provenance():
    sim = DAESimulation(loop_manifest())
    assert sim.experiment.equation_graph.provenance['pump_law'] == 'affinity_N2H0_minus_aQabsQ/v3'
    laws = sim.metadata['parameter_provenance']['solver_laws']
    assert laws['pump_law'] == 'affinity_N2H0_minus_aQabsQ/v3'


def test_d1_legacy_pump_handler_uses_the_affinity_law():
    from backend.liquidcooling.graphcompiling.compiled_graph import PumpBatchParams, PumpIndices
    from backend.liquidcooling.handlers.pump import PumpBatchHandler
    f64 = torch.float64
    handler = PumpBatchHandler(PumpBatchParams(H0=torch.tensor([8.], dtype=f64), a_coef=torch.tensor([4e7], dtype=f64),
                                               rpm_ratio=torch.tensor([.6], dtype=f64)), rho=RHO, g_mag=G)
    m = .1
    q = m / RHO
    rise = RHO * G * (.36 * 8. - 4e7 * q * q)
    state = {'P': torch.tensor([[1e5], [1e5 + rise]], dtype=f64), 'm': torch.tensor([[m]], dtype=f64)}
    indices = PumpIndices(edge_ids=torch.tensor([0]), node_from=torch.tensor([0]), node_to=torch.tensor([1]))
    residual = handler.compute_residuals(state, indices, 1., 1)['momentum']
    assert abs(float(residual)) < 1e-9 * rise


@pytest.mark.parametrize('rpm', [0., .5, 1.])
def test_d1_pump_dissipates_reverse_flow_at_every_speed(rpm):
    """H = N^2*H0 - a*Q|Q|. With -a*Q^2 a pump fed energy into reverse flow:
    stopped (N=0, a pump trip) it drove a loop backwards. Forward flow is
    bit-identical to the affinity law above."""
    from backend.liquidcooling.solver.newton_hydraulic import pump_headloss
    m = torch.linspace(-.5, .5, 1001, dtype=torch.float64).requires_grad_(True)
    dp, slope = pump_headloss(m, 8., 4e7, rpm, RHO, G)
    (exact,) = torch.autograd.grad(dp.sum(), m)
    assert torch.allclose(slope, exact, rtol=1e-12, atol=1e-12)
    assert bool((slope >= 0).all())  # strictly passive beyond its shut-off head
    shutoff = -RHO * G * rpm * rpm * 8.
    assert bool(((dp - shutoff) * m >= 0).all())
    forward = m.detach() >= 0
    q = m.detach()[forward] / RHO
    assert torch.equal(dp.detach()[forward], -RHO * G * (rpm * rpm * 8. - 4e7 * q * q))


def test_d1_a_tripped_pump_cannot_drive_the_loop_backwards():
    """A pump trip from a reverse-flow state: the loop has no drive, so the only
    root is zero flow. With -a*Q^2 a steep stopped pump (g*a/rho above the loop's
    quadratic loss coefficients, 3.9e6 vs 1.2e6 Pa/(kg/s)^2 here) gave a second,
    reverse root m = -R_lin/(g*a/rho - sum dp_a), about -2e-3 kg/s."""
    from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
    from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
    raw = loop_manifest(n_t=2)
    next(e for e in raw['edges'] if e['id'] == 'pump')['params']['a_coef'] = 4e8
    definition = load_dae_input(raw)
    eq = definition.equation_graph
    theta = definition.parameters(0)
    y, _ = solve_steady(eq.problem, eq.initial_guess(theta), theta,
                        conserved=(eq.steady_neutral_storage_mask, eq.initial_guess(theta)))
    flow = eq.state_layout['flow']
    reverse = y.clone()
    reverse[flow] = -.003  # near the spurious reverse root of the old law
    tripped = definition.parameters(1)
    tripped = tripped.index_fill(0, torch.arange(eq.parameter_layout['pump_rpm_ratio'].start,
                                                 eq.parameter_layout['pump_rpm_ratio'].stop), 0.)
    step, report = backward_euler_step(eq.problem, reverse, tripped, time=1., dt=1.)
    assert report.scaled_residual_inf < 1e-8
    assert float(step[flow].abs().max()) < 1e-9


# --------------------------------------------------------------------- D3 ---

def _legacy_friction_dp(m, D, L, A, K_minor, roughness, rho, mu, eps=1e-8):
    """Verbatim copy of the pre-fix _pipe_dp_friction (working-range reference)."""
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


def _churchill(Re, rr):
    a = (2.457 * math.log(1 / ((7 / Re) ** 0.9 + 0.27 * rr))) ** 16
    b = (37530 / Re) ** 16
    return 8 * ((8 / Re) ** 12 + 1 / (a + b) ** 1.5) ** (1 / 12)


@pytest.mark.parametrize('rough', [0., 1.5e-6, 1e-4])
def test_d3_friction_factor_has_no_pole_over_the_whole_reynolds_range(rough):
    """Swamee-Jain's log argument passes 1 near Re=6.97; its pole was multiplied
    by a 1e-5 blend weight and still gave 2.9e7x the laminar loss (report D3)."""
    from backend.liquidcooling.solver.newton_hydraulic import _pipe_dp_friction
    D, L, rho, mu = .025, 1., 1032., .0018
    A = math.pi * D * D / 4
    Re = torch.cat((torch.logspace(-3, 7, 4001, dtype=torch.float64),
                    torch.linspace(6.9, 7.1, 4001, dtype=torch.float64)))
    m = (Re * A * mu / D).requires_grad_(True)
    dp = _pipe_dp_friction(m, D, L, A, 0., rough, rho, mu)
    (slope,) = torch.autograd.grad(dp.sum(), m)
    assert bool(torch.isfinite(dp).all() and torch.isfinite(slope).all())
    assert bool((slope > 0).all())  # strictly passive and monotone in m
    f = (dp * 2 * rho * A * A * D / (L * m * m.abs())).detach()
    laminar = Re < 1000  # blend weight <= 1.5e-3; the transition blend itself starts above
    assert float((f[laminar] / (64 / Re[laminar])).max()) < 1.0005
    for value, r in zip(f[Re > 4000].tolist(), Re[Re > 4000].tolist()):
        assert .9 < value / _churchill(r, rough / D) < 1.1


def test_d3_working_range_is_bit_identical_to_the_previous_formula():
    """Wherever Swamee-Jain's log argument is <= 0.5 (Re >= ~15 for a smooth
    pipe) the loss is unchanged bit for bit; only the pole side is bounded."""
    from backend.liquidcooling.solver.newton_hydraulic import _pipe_dp_friction
    D, L, rho, mu, K = .02, 2.3, 1000., 1e-3, 1.4
    A = math.pi * D * D / 4
    Re = torch.logspace(math.log10(16.), 7, 3001, dtype=torch.float64)
    m = torch.cat((Re, -Re)) * A * mu / D
    for rough in (0., 1.5e-6, 1e-4):
        assert torch.equal(_pipe_dp_friction(m, D, L, A, K, rough, rho, mu),
                           _legacy_friction_dp(m, D, L, A, K, rough, rho, mu))


def test_d3_friction_law_version_is_registered_in_provenance():
    """The capped friction law changes results only where an edge sits below
    Re ~ 15, but a run must still say which law produced it."""
    sim = DAESimulation(loop_manifest())
    expected = 'laminar64_swamee_jain_blend_logcap0p5/v2'
    assert sim.experiment.equation_graph.provenance['friction_law'] == expected
    assert sim.metadata['parameter_provenance']['solver_laws']['friction_law'] == expected
    assert sim.result()['parameter_provenance']['solver_laws']['friction_law'] == expected


@pytest.mark.parametrize('rough', [0., 1.5e-6, 1e-4])
def test_d3_v1_pipe_handler_has_the_same_bounded_friction(rough):
    """The v1 ODIL PipeBatchHandler kept its own copy of Swamee-Jain, pole and
    all (2.9e7x the laminar loss near Re=6.97); it now caps the same way."""
    from backend.liquidcooling.graphcompiling.compiled_graph import PipeBatchParams, PipeIndices
    from backend.liquidcooling.handlers.pipe import PipeBatchHandler
    D, L, rho, mu = .025, 1., 1032., .0018
    A = math.pi * D * D / 4
    f64 = torch.float64
    params = PipeBatchParams(D=torch.tensor([D], dtype=f64), L=torch.tensor([L], dtype=f64),
                             dh=torch.tensor([0.], dtype=f64), K_minor=torch.tensor([0.], dtype=f64),
                             A=torch.tensor([A], dtype=f64), roughness=torch.tensor([rough], dtype=f64))
    handler = PipeBatchHandler(params, rho=rho, mu=mu, cp=4000., g_mag=G)
    index = PipeIndices(edge_ids=torch.tensor([0]), node_from=torch.tensor([0]), node_to=torch.tensor([1]),
                        cell_start=torch.tensor([0]), cell_end=torch.tensor([0]))
    Re = torch.cat((torch.logspace(0, 3, 301, dtype=f64), torch.linspace(6.9, 7.1, 201, dtype=f64)))
    m = Re * A * mu / D
    state = {'P': torch.zeros(2, len(Re), dtype=f64), 'm': m[None, :]}
    dp = -handler.compute_residuals(state, index, 1., 1)['momentum'][0]  # P_from - P_to = 0
    poiseuille = 128 * mu * L * (m / rho) / (math.pi * D ** 4)
    ratio = dp / poiseuille
    assert bool(torch.isfinite(ratio).all())
    assert float((ratio - 1).abs().max()) < 2e-3  # laminar; blend weight <= 1.5e-3 below Re=1000


# --------------------------------------------------------------------- D2 ---

def test_d2_rack_metadata_lists_every_cold_plate_of_a_server_in_series_order():
    from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
    graph = load_dae_input(loop_manifest(plates=(100., 300.))).equation_graph.graph
    (rack,) = graph.rack_meta
    assert rack.server_ids == ['srv_a']
    assert rack.server_edge_ids == [['srv_a_cp0', 'srv_a_cp1']]


def test_d2_server_summary_counts_the_heat_and_chip_of_every_plate():
    """Report D2: a second 300 W plate was invisible (100 W reported, 14.8 K low)."""
    sim = DAESimulation(loop_manifest(plates=(100., 300.)))
    result = sim.result()
    (server,) = result['rack_summaries'][0]['servers']
    assert server['cp_edge_ids'] == ['srv_a_cp0', 'srv_a_cp1']
    assert server['heat_removed_W'][-1] == pytest.approx(400., rel=1e-8)
    assert server['max_chip_temp_K'][-1] == pytest.approx(max(result['chip_temp_K'][0][-1], result['chip_temp_K'][1][-1]))
    edge = result['edge_ids'].index('srv_a_cp1')
    assert server['flow_kg_s'][-1] == pytest.approx(abs(result['edge_flow_kg_s'][edge][-1]))


def test_d2_legacy_config_model_keeps_all_pairs():
    from backend.liquidcooling.schema import ServerConfig
    server = ServerConfig.model_validate({'id': 's', 'cp_cu_pairs': [
        {'id': 'a', 'edge_id': 'e0', 'Q_W': 1.}, {'id': 'b', 'edge_id': 'e1', 'Q_W': 2.}]})
    assert server.cold_plate_edge_ids() == ['e0', 'e1']
    assert server.cp_cu.cold_plate_edge_id == 'e0'  # legacy single-pair view retained
    legacy = ServerConfig.model_validate({'id': 's', 'cp_cu': {'server_id': 's', 'cold_plate_edge_id': 'e9'}})
    assert legacy.cold_plate_edge_ids() == ['e9']
    assert ServerConfig.model_validate({'id': 's'}).cold_plate_edge_ids() == []


# --------------------------------------------------------------------- D4 ---

def test_d4_held_secondary_below_the_primary_inlet_is_rejected_with_the_hx_and_values():
    """Report D4: T_set 288.15 K under a 293.15 K primary inlet moved 21 kW
    from cold to hot without any message."""
    raw = loop_manifest(version=4, hx_mode='setpoint', T_set_K=288.15, primary_inlet=293.15)
    with pytest.raises(ValueError) as error:
        DAESimulation(raw)
    message = str(error.value)
    assert "'hx'" in message and '288.15' in message and '293.15' in message
    assert 'second law' in message


def test_d4_feasible_held_secondary_is_admitted_and_rejects_its_duty_to_the_primary():
    raw = loop_manifest(version=4, hx_mode='setpoint', T_set_K=296.15, primary_inlet=293.15)
    sim = DAESimulation(raw)
    frame = sim.latest_frame
    assert frame['secondary_supply_K'][0] == pytest.approx(296.15, abs=1e-9)
    assert frame['diagnostics']['primary_rejection_W'] == pytest.approx(300., rel=1e-8)


def test_d4_held_secondary_heated_above_the_primary_inlet_is_flagged_not_hidden():
    """Ideal-control heating stays admitted (the set-point form's documented
    semantics, which the v4 ramp tests rely on), but it is not a passive HX:
    the 293.15 K primary leaves colder than it came while the secondary is held
    at 330 K. Frames and results say so per HX instead of hiding it."""
    t_set = [300., 300., 330., 330., 330., 330.]
    raw = loop_manifest(version=4, hx_mode='setpoint', T_set_K=t_set, n_t=6, dt=1.)
    sim = DAESimulation(raw)
    frames = [sim.latest_frame] + [sim.step() for _ in range(5)]
    flags = [frame['hx_ideal_heating'] for frame in frames]
    assert flags[:2] == [[False], [False]] and flags[2] == [True]
    for frame in frames:
        heating = frame['secondary_supply_K'][0] > 293.15 and frame['primary_return_K'][0] < 293.15
        assert frame['hx_ideal_heating'] == [heating]
        messages = [w for w in frame['warnings'] if "'hx'" in w and 'ideal-control heating' in w]
        assert bool(messages) == heating
    result = sim.result()
    assert result['hx_ideal_heating'] == [[flag[0] for flag in flags]]
    summary = [w for w in result['warnings'] if "'hx'" in w and 'ideal-control heating' in w]
    assert len(summary) == 1 and '330.00 K' in summary[0]


def test_coolant_outside_the_single_phase_range_is_warned():
    """A converged state can still be unphysical: 200 kW into a 0.22 kg/s loop
    puts the constant-property coolant far above boiling. Say so."""
    sim = DAESimulation(loop_manifest(plates=(2e5,)))
    frame = sim.latest_frame
    assert max(frame['node_temperature_K']) > 373.15
    assert any('single-phase' in w and '373.15' in w for w in frame['warnings'])
    assert any('single-phase' in w for w in sim.result()['warnings'])
    normal = DAESimulation(loop_manifest())
    assert normal.latest_frame['warnings'] == [] and normal.result()['warnings'] == []
    assert normal.result()['hx_ideal_heating'] == [[False]]


# --------------------------------------------------------------------- D5 ---

def test_d5_a_closed_loop_elevation_drives_no_flow_whatever_edge_carries_it():
    """The pump edge rises 0.5 m, the return pipe falls 0.5 m. Ignoring the
    pump's rise (report D5) left a phantom rho*g*0.5 m drive in the loop."""
    flat_raw = loop_manifest()
    # Same route length as the lifted 0.5 m descent, laid out horizontally.
    next(e for e in flat_raw['edges'] if e['id'] == 'return_pipe')['params']['waypoints_m'] = [[2., 0., 0.], [0., 0., -.5]]
    flat, _, _ = frame_of(DAESimulation(flat_raw))
    sim = DAESimulation(loop_manifest(pump_rise_m=.5))
    lifted, edges, nodes = frame_of(sim)
    m = lifted['edge_flow_kg_s'][edges['pump']]
    assert m == pytest.approx(flat['edge_flow_kg_s'][edges['pump']], rel=1e-10)
    q = m / RHO
    rise = lifted['node_pressure_Pa'][nodes['dis']] - lifted['node_pressure_Pa'][nodes['ret']]
    assert rise == pytest.approx(RHO * G * (8. - 4e7 * q * q) - RHO * G * .5, rel=1e-9)


def test_d5_compiled_graph_carries_node_elevation_rise_for_non_pipe_edges():
    from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
    graph = load_dae_input(loop_manifest(pump_rise_m=.5)).equation_graph.graph
    rise = dict(zip(graph.edge_str_ids, graph.component_dh.tolist()))
    assert rise['pump'] == pytest.approx(.5)
    assert rise['hx'] == 0. and rise['srv_a_cp0'] == 0.
    assert rise['return_pipe'] == 0.  # pipes keep their waypoint dh in pipe_params


def test_d5_gga_predictor_includes_non_pipe_hydrostatics():
    from backend.liquidcooling.graphcompiling.time_slice import time_slice
    from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
    from backend.liquidcooling.solver.newton_hydraulic import gga_solve
    flows = []
    flat_raw = loop_manifest()
    next(e for e in flat_raw['edges'] if e['id'] == 'return_pipe')['params']['waypoints_m'] = [[2., 0., 0.], [0., 0., -.5]]
    for raw in (flat_raw, loop_manifest(pump_rise_m=.5)):
        graph = load_dae_input(raw).equation_graph.graph
        _, m, info = gga_solve(time_slice(graph, 0), tol=1e-12, max_iter=80)
        assert info['converged']
        flows.append(float(m[graph.edge_str_ids.index('pump'), 0]))
    assert flows[1] == pytest.approx(flows[0], rel=1e-9)


def test_d5_inconsistent_loop_elevations_are_rejected_at_compile_time():
    from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
    raw = loop_manifest(pump_rise_m=.5)
    pipe = next(e for e in raw['edges'] if e['id'] == 'supply_pipe')
    pipe['params']['waypoints_m'] = [[0., .5, 1.], [2., .7, 1.]]  # route rises 0.2 m, nodes do not
    with pytest.raises(ValueError) as error:
        load_dae_input(raw)
    message = str(error.value)
    assert 'elevation' in message and 'loop' in message and '0.2 m' in message
    assert "pipe 'supply_pipe' route rises 0.2 m but its nodes rise 0 m" in message


def test_d5_v1_handlers_carry_the_rise_of_every_non_pipe_edge():
    """The v1 ODIL model (LiquidODILModel) built its pump, valve, cold-plate and
    HX residuals without the edge's rise: on the lifted loop the pump residual
    of the DAE solution was -rho*g*0.5 = -4905 Pa. Every non-pipe handler now
    adds rho*g*dh from graph.component_dh, the term the DAE and GGA use."""
    from backend.liquidcooling.solver.model import LiquidODILModel
    sim = DAESimulation(loop_manifest(pump_rise_m=.5, extra_branch='parallel', n_t=2))
    frame = sim.latest_frame
    graph = sim.experiment.equation_graph.graph

    def column(key):  # the steady root at both samples (the plate energy residual needs N_t >= 2)
        return torch.tensor(frame[key], dtype=torch.float64)[:, None].repeat(1, 2)
    state = {'P': column('node_pressure_Pa'), 'm': column('edge_flow_kg_s'), 'T_s': column('chip_temp_K')}

    def residuals(g):
        model = LiquidODILModel(g)
        out = {}
        for kind, handler in model.handlers.items():
            index = getattr(g, model._HANDLER_TO_GRAPH_ATTR.get(kind, f'{kind}_indices'))
            out[kind] = handler.compute_residuals(state, index, g.dt, g.bdf_order)['momentum'][:, 0]
        return out
    lifted = residuals(graph)
    for kind in ('pipe', 'pump', 'valve', 'cold_plate', 'heat_exchanger'):
        assert float(lifted[kind].abs().max()) < 1e-6, kind  # the DAE root satisfies every v1 law
    # Shift every non-pipe rise by 0.2 m: the pressure-form residuals move by
    # exactly rho*g*0.2 (sign by convention), the valve's flow-form residual by
    # its law at the shifted pressure drop.
    import copy as _copy
    shifted_graph = _copy.copy(graph)
    rise = graph.component_dh.clone()
    non_pipe = torch.ones_like(rise, dtype=torch.bool)
    non_pipe[graph.pipe_indices.edge_ids] = False
    shifted_graph.component_dh = torch.where(non_pipe, rise + .2, rise)
    shifted = residuals(shifted_graph)
    head = RHO * G * .2
    assert torch.allclose(shifted['pump'] - lifted['pump'], torch.full_like(lifted['pump'], head), atol=1e-6)
    for kind in ('cold_plate', 'heat_exchanger'):
        assert torch.allclose(shifted[kind] - lifted[kind], torch.full_like(lifted[kind], -head), atol=1e-6), kind
    assert float(shifted['valve'].abs().max()) > 1e-3


# ------------------------------------------------------------- D6 and S2 ---

def isolated(**kw):
    return loop_manifest(extra_branch='isolated_loop', valve_opening=0., **kw)


def test_s2_heated_plates_behind_a_closed_valve_have_no_steady_state_and_say_why():
    """Report S2: closing a rack's return valve left a non-tree stagnant region;
    the only message was 'DAE Newton line search failed'."""
    with pytest.raises(ValueError) as error:
        DAESimulation(isolated())
    message = str(error.value)
    for name in ('sub_cpA', 'sub_cpB', 'sub_valve', 'consistent'):
        assert name in message
    assert 'no steady state' in message


def test_s2_unheated_stagnant_region_solves_with_zero_flow_and_is_flagged():
    sim = DAESimulation(isolated(sub_heat=0.))
    frame, edges, nodes = frame_of(sim)
    for name in ('sub_pipe', 'sub_cpA', 'sub_cpB', 'sub_valve'):
        # Exactly zero, as documented: the hydraulic rows are kept, but their
        # Newton residue (~1e-14 kg/s) is not reported as a flow.
        assert frame['edge_flow_kg_s'][edges[name]] == 0.0
        assert frame['edge_stagnant'][edges[name]] is True
    assert frame['edge_stagnant'][edges['srv_a_cp0']] is False
    for name in ('sub_in', 'sub_out'):
        assert frame['node_observable'][nodes[name]] is False
    assert frame['node_observable'][nodes['mf_supply']] is True
    # The stagnant plates keep their stored (initial) temperature.
    cp = sim.metadata['cp_edge_ids']
    assert frame['chip_temp_K'][cp.index('sub_cpA')] == pytest.approx(310.)
    result = sim.result()
    assert result['edge_stagnant'] == frame['edge_stagnant']
    for name in ('sub_pipe', 'sub_cpA', 'sub_cpB', 'sub_valve'):
        assert result['edge_flow_kg_s'][edges[name]] == [0.0]
    # The research tensor state keeps the solved values (residue included).
    flow = sim.experiment.tensor_state[sim.experiment.equation_graph.state_layout['flow']]
    assert float(flow[edges['sub_cpA']].abs()) < 1e-12
    assert result['node_observable'][nodes['sub_in']] == [False]
    assert result['node_observable'][nodes['dis']] == [True]


def test_s2_consistent_transient_heats_the_isolated_plates_at_q_over_c():
    raw = isolated(n_t=4, dt=2., analysis={'mode': 'transient', 'initialization': 'consistent'})
    sim = DAESimulation(raw)
    cp = sim.metadata['cp_edge_ids'].index('sub_cpB')
    temps = [sim.latest_frame['chip_temp_K'][cp]]
    while not sim.done:
        temps.append(sim.step()['chip_temp_K'][cp])
    # No coolant flow: C dT/dt = Q exactly, 200 W into 200 J/K -> 1 K/s.
    assert [b - a for a, b in zip(temps, temps[1:])] == pytest.approx([2., 2., 2.], abs=1e-9)


def test_d6_open_valve_keeps_the_sub_rack_active_and_observable():
    sim = DAESimulation(loop_manifest(extra_branch='isolated_loop'))
    frame, edges, nodes = frame_of(sim)
    assert not any(frame['edge_stagnant'])
    assert all(frame['node_observable'])
    assert frame['edge_flow_kg_s'][edges['sub_cpA']] > 1e-3


def test_d6_dead_end_branch_is_stagnant_and_its_tip_unobservable():
    raw = loop_manifest()
    raw['nodes'].append({'id': 'tip', 'type': 'junction', 'position_m': [2., 0., 2.]})
    raw['edges'].append({'id': 'stub', 'type': 'pipe', 'from': 'mf_supply', 'to': 'tip',
                         'params': {'D_m': .01, 'n_cells': 2, 'waypoints_m': [[2., 0., 1.], [2., 0., 2.]]}})
    frame, edges, nodes = frame_of(DAESimulation(raw))
    assert frame['edge_stagnant'][edges['stub']] is True
    assert frame['node_observable'][nodes['tip']] is False


# ------------------------------------------------- result topology (item 9) ---

def test_result_carries_the_topology_needed_to_read_flows():
    raw = loop_manifest(extra_branch='parallel')
    result = DAESimulation(raw).result()
    by_id = {e['id']: e for e in raw['edges']}
    assert result['edge_from'] == [by_id[e]['from'] for e in result['edge_ids']]
    assert result['edge_to'] == [by_id[e]['to'] for e in result['edge_ids']]
    assert result['edge_types'] == [by_id[e]['type'] for e in result['edge_ids']]
    diameters = dict(zip(result['edge_ids'], result['edge_D_m']))
    assert diameters['supply_pipe'] == .02 and diameters['srv_b_valve'] == .015
    assert diameters['pump'] is None and diameters['hx'] is None and diameters['srv_a_cp0'] is None
    positions = {n['id']: n['position_m'] for n in raw['nodes']}
    assert result['node_positions_m'] == [positions[n] for n in result['node_ids']]
    import json
    json.dumps(result, allow_nan=False)


def test_server_summary_keeps_the_signed_flow_beside_the_magnitude():
    raw = loop_manifest(plates=(100., 300.))
    for edge in raw['edges']:  # author the server's plates against the flow
        if edge['id'].startswith('srv_a_cp'):
            edge['from'], edge['to'] = edge['to'], edge['from']
    for server in raw['racks'][0]['servers']:
        server['cp_cu_pairs'].reverse()
    result = DAESimulation(raw).result()
    (server,) = result['rack_summaries'][0]['servers']
    edge = result['edge_ids'].index(server['cp_edge_ids'][0])
    signed = result['edge_flow_kg_s'][edge][-1]
    assert signed < 0
    assert server['signed_flow_kg_s'][-1] == signed
    assert server['flow_kg_s'][-1] == abs(signed)


def test_legacy_runtime_result_carries_the_same_topology_fields():
    from backend.liquidcooling.runtime import LiquidSimulation
    from backend.tests.test_liquid.test_runtime import runtime_config
    config = runtime_config()
    sim = LiquidSimulation(config)
    while not sim.done:
        sim.step()
    result = sim.result()
    raw = config.model_dump(by_alias=True)
    by_id = {e['id']: e for e in raw['edges']}
    assert result['edge_from'] == [by_id[e]['from'] for e in result['edge_ids']]
    assert result['edge_types'] == [by_id[e]['type'] for e in result['edge_ids']]
    assert len(result['node_positions_m']) == len(result['node_ids'])
    assert len(result['node_observable']) == len(result['node_ids'])
    assert all(len(row) == len(result['time_s']) for row in result['node_observable'])


# ------------------------------------------- saved runs before solver_laws ---

def _saved_run_without(tmp_path, drop):
    """A finished run whose saved provenance lacks the solver_laws keys in
    ``drop`` (all of them: a run saved before 2026-10-21)."""
    import json
    from backend.liquidcooling.run_manager import LiquidRunManager
    from backend.tests.test_liquid.test_dae_runtime import case
    manager = LiquidRunManager()
    run_id = manager.create(case(), case_dir=tmp_path)['run_id']
    manager.operate(run_id, 'cancel')
    run_dir = tmp_path / 'Liquid' / 'log' / f'run-{run_id}'
    for name, keys in (('input.json', ('metadata', 'parameter_provenance')), ('result.json', ('parameter_provenance',))):
        document = json.loads((run_dir / name).read_text())
        provenance = document
        for key in keys:
            provenance = provenance[key]
        if drop == 'all':
            del provenance['solver_laws']
        else:
            for key in drop:
                del provenance['solver_laws'][key]
        (run_dir / name).write_text(json.dumps(document))
    restored = LiquidRunManager()
    restored.restore(run_id, case_dir=tmp_path)
    return restored, run_id


def test_a_run_saved_before_solver_laws_restores_naming_the_laws_of_its_solver(tmp_path):
    """Before 2026-10-21 the solver had one law of each kind, so a saved run
    without provenance.solver_laws was solved with exactly these; the restored
    view says so and marks every key it filled in as inferred."""
    manager, run_id = _saved_run_without(tmp_path, 'all')
    status = manager.get(run_id)
    hx_ids = status['metadata']['hx_edge_ids']
    expected = {'pump_law': 'affinity_N2_times_H0_minus_aQ2/v1',
                'hydrostatics': 'pipes_only_rho_g_dh/v1',
                'friction_law': 'laminar64_swamee_jain_blend_uncapped/v1',
                'hx_models': {hx: 'mixed-volumes' for hx in hx_ids},
                'tee_law': 'tee_K0p9_per_pipe_endpoint/v1',
                'inferred': ['friction_law', 'hx_models', 'hydrostatics', 'pump_law', 'tee_law']}
    assert status['metadata']['parameter_provenance']['solver_laws'] == expected
    assert manager.result(run_id)['parameter_provenance']['solver_laws'] == expected


def test_a_run_saved_before_hx_models_infers_only_the_missing_keys(tmp_path):
    """A run from the hour between the pump-law and hxModel commits recorded
    pump_law and hydrostatics; only hx_models (mixed-volumes, the only model
    then) is inferred. Its friction law is left unrecorded, because the
    friction cap and the pump-law record landed five minutes apart."""
    manager, run_id = _saved_run_without(tmp_path, ('hx_models', 'friction_law'))
    laws = manager.result(run_id)['parameter_provenance']['solver_laws']
    assert laws['pump_law'] == 'affinity_N2H0_minus_aQabsQ/v3'
    assert laws['hydrostatics'] == 'all_edges_rho_g_dh/v2'
    assert set(laws['hx_models'].values()) == {'mixed-volumes'}
    assert 'friction_law' not in laws
    assert laws['inferred'] == ['hx_models']


def test_a_run_saved_with_every_solver_law_restores_unchanged(tmp_path):
    manager, run_id = _saved_run_without(tmp_path, ())
    laws = manager.result(run_id)['parameter_provenance']['solver_laws']
    assert 'inferred' not in laws and laws['friction_law'] == 'laminar64_swamee_jain_blend_logcap0p5/v2'
