"""The zonal network numerics: the log-form residual is the legacy residual, Newton is scaled and monotone."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
import torch

from agent.scenarios.hybrid import network_numerics as N
from agent.scenarios.hybrid import open_network_numerics as O
from agent.scenarios.hybrid.air_network import network_for

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hybrid_lf" / "hac-normal.json.gz"


@pytest.fixture(scope="module")
def model():
    frozen = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    return network_for(frozen["contract"]["operating_scenario"]["architecture"])(frozen["contract"], frozen["solids"])


def test_the_stable_residual_is_the_original_residual_away_from_and_at_the_root(model):
    net, controls, areas = model.network, model.nominal_controls, model.areas
    state, receipt = O.solve_state(net, controls, areas)
    assert receipt["engine"] == O.VERSION and receipt["original_full_residual_inf"] <= 1e-10
    generator = torch.Generator().manual_seed(7)
    for scale in (0., 1e-3, 1., 50.):  # 50 Pa drives the softplus tail past its -35 asymptote on some passages
        probe = state + scale * torch.randn(state.shape, generator=generator, dtype=state.dtype)
        original = net.residual(probe, controls, areas)
        assert (N.stable_residual(net, probe, controls, areas) - original).abs().max() <= 1e-11 * max(
            1., float(original.abs().max()))


def test_the_implicit_state_gradient_of_the_open_numerics_is_the_adjoint(model):
    net, areas = model.network, model.areas.clone().requires_grad_(True)
    controls = model.nominal_controls.clone().requires_grad_(True)
    state = O.ImplicitState.apply(controls, areas, net)
    weights = torch.linspace(0., 1., state.numel(), dtype=state.dtype)
    (state * weights).sum().backward()
    # Implicit function theorem by hand: J du = -(dF/dc) dc.
    jacobian = torch.autograd.functional.jacobian(lambda u: N.stable_residual(net, u, controls.detach(),
                                                                             areas.detach()), state.detach())
    dF_dc = torch.autograd.functional.jacobian(lambda c: N.stable_residual(net, state.detach(), c, areas.detach()),
                                               controls.detach())
    expected = -(weights @ torch.linalg.solve(jacobian, dF_dc))
    assert controls.grad == pytest.approx(expected, rel=1e-8, abs=1e-12)
    assert areas.grad is not None and torch.isfinite(areas.grad).all()


def test_newton_converges_and_refuses_a_root_it_cannot_reach():
    root, history = N._newton(lambda x: torch.stack((x[0] ** 3 - 8., x[0] + x[1] - 1.)),
                              torch.tensor([1., 0.], dtype=torch.float64), tolerance=1e-12)
    assert root == pytest.approx(torch.tensor([2., -1.], dtype=torch.float64), abs=1e-12)
    assert all(b < a for a, b in zip(history, history[1:]))
    with pytest.raises(RuntimeError, match="line search|linear solve"):
        N._newton(lambda x: x ** 2 + 1., torch.tensor([.5], dtype=torch.float64), tolerance=1e-12)


def test_open_numerics_refuse_a_closed_branch_in_the_controls(model):
    controls = model.nominal_controls.clone()
    controls[-1] = 0.
    with pytest.raises(ValueError, match="positive active branch"):
        O.solve_state(model.network, controls, model.areas)
