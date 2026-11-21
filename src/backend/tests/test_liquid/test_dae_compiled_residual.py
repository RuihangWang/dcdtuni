"""S1 contract: compile-routed DAE residual (routing rules v3).

Evidence base: docs/benchmarks/dae-baseline-d5388f8a-20260928/ (probes R1-R5d).
The torch 2.5.1 incompatibility class (functorch transforms vs AOT-compiled
backward) makes blanket torch.compile unsafe; routing rules v3 restrict the
compiled surface to calls that can never be backpropped through:
  1. functorch transform active             -> eager
  2. grad mode AND any input requires_grad  -> eager
  3. otherwise                              -> compiled
These tests pin: bit-identical roots/gradients vs eager, exposed routing
counters, compiled usage on the no_grad value path, and explicit (never
silent) degradation when compilation fails.
"""
import logging

import pytest
import torch

from backend.liquidcooling.solver.compile_support import (
    RoutedCompiledResidual,
    compiled_problem,
)
from backend.liquidcooling.solver.dae_newton import (
    DAEProblem,
    backward_euler_step,
    solve_steady,
)
from backend.tests.test_liquid.test_equation_graph import adapter, exact_state


def _fixture():
    eq = adapter()
    theta = eq.parameters()
    guess = eq.initial_guess(theta)
    y_exact = exact_state(eq, theta)
    return eq, theta, guess, y_exact


def test_compiled_problem_steady_and_be_are_bit_identical_to_eager():
    eq, theta, guess, y_exact = _fixture()
    y_e, rep_e = solve_steady(eq.problem, guess, theta)
    pc, router = compiled_problem(eq.problem)
    y_c, rep_c = solve_steady(pc, guess, theta)
    assert (y_c - y_e).abs().max().item() == 0.0
    assert (y_c - y_exact).abs().max().item() < 1e-9
    assert rep_c.iterations == rep_e.iterations
    assert rep_c.scaled_residual_inf == rep_e.scaled_residual_inf

    prev = y_e.detach()
    ys_e, r_e = backward_euler_step(eq.problem, prev, theta, time=0.05, dt=0.05)
    ys_c, r_c = backward_euler_step(pc, prev, theta, time=0.05, dt=0.05)
    assert (ys_c - ys_e).abs().max().item() == 0.0
    assert r_c.iterations == r_e.iterations
    assert router.routing_report()["n_eager_fallback"] == 0


def test_newton_value_calls_route_compiled_and_stay_bit_identical():
    """The no_grad merit/norm value calls inside _newton must hit rule 3."""
    eq, theta, guess, _ = _fixture()
    pc, router = compiled_problem(eq.problem)
    y_c, rep_c = solve_steady(pc, guess, theta)
    report = router.routing_report()
    assert report["n_compiled"] > 0, report        # no_grad merit/norm calls hit rule 3
    assert report["n_eager_grad"] > 0, report      # jacobian(vectorize) FORWARD runs in
    # grad mode with a requires_grad state and is caught by rule 2 (measured:
    # its vmap only wraps the backward batching phase, so the transform count
    # stays 0 here; direct torch.func.jvp coverage lives in its own test).
    assert report["n_eager_fallback"] == 0, report
    y_e, _ = solve_steady(eq.problem, guess, theta)
    assert (y_c - y_e).abs().max().item() == 0.0
    assert rep_c.scaled_residual_inf < 1e-10


def test_parameter_backprop_under_routing_matches_eager_bit_for_bit():
    """Regression for the probe-3/4/5a crash class: theta.requires_grad solve+grad."""
    eq, _, guess, _ = _fixture()
    theta_e = eq.parameters().clone().requires_grad_(True)
    y_e, _ = solve_steady(eq.problem, guess, theta_e)
    g_e = torch.autograd.grad(y_e.square().sum(), theta_e)[0]

    pc, router = compiled_problem(eq.problem)
    theta_c = eq.parameters().clone().requires_grad_(True)
    y_c, _ = solve_steady(pc, guess, theta_c)
    g_c = torch.autograd.grad(y_c.square().sum(), theta_c)[0]
    assert (y_c - y_e).abs().max().item() == 0.0
    assert (g_c - g_e).abs().max().item() == 0.0
    assert router.routing_report()["n_eager_fallback"] == 0


def test_transform_context_routes_to_eager():
    eq, theta, _, y_exact = _fixture()
    pc, router = compiled_problem(eq.problem)
    t0 = theta.new_tensor(0.0)
    z = (y_exact / pc.state_scale).detach()
    gen = torch.Generator().manual_seed(7)
    v = torch.randn(z.shape, dtype=z.dtype, generator=gen)

    def scaled(zz):
        return pc.residual(
            t0, zz * pc.state_scale, torch.zeros_like(zz), theta
        ) / pc.residual_scale

    with torch.no_grad():
        primal, tangent = torch.func.jvp(scaled, (z,), (v,))
    assert router.n_eager_transform >= 1
    assert torch.isfinite(tangent).all()
    assert torch.isfinite(primal).all()


def test_compile_failure_degrades_explicitly_never_silently(caplog):
    def residual(t, y, ydot, th):
        del t, ydot
        return y - th

    problem = DAEProblem(
        residual=residual,
        differential_mask=torch.tensor([True, True]),
        state_scale=torch.ones(2, dtype=torch.float64),
        residual_scale=torch.ones(2, dtype=torch.float64),
    )

    def failing_factory(fn, *, mode, dynamic):
        del fn, mode, dynamic

        def boom(*args):
            raise RuntimeError("inductor exploded (synthetic)")

        return boom

    router = RoutedCompiledResidual(problem.residual, compile_factory=failing_factory)
    th = torch.tensor([1.0, 2.0], dtype=torch.float64)
    yy = torch.tensor([1.5, 2.5], dtype=torch.float64)
    t0 = torch.tensor(0.0, dtype=torch.float64)
    yd = torch.zeros_like(yy)
    with caplog.at_level(logging.ERROR, logger="backend.liquidcooling.solver.compile_support"):
        with torch.no_grad():
            out = router(t0, yy, yd, th)
            out2 = router(t0, yy, yd, th)
    assert torch.equal(out, yy - th)
    assert torch.equal(out2, yy - th)
    report = router.routing_report()
    assert report["n_compiled"] == 0
    assert report["n_eager_fallback"] == 2
    assert report["broken_latch"] is True
    assert any("degrading to eager" in rec.message for rec in caplog.records)
