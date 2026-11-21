"""S2 contract: right-preconditioned restarted GMRES ('pgmres').

Targets the measured gap: unpreconditioned matrix-free GMRES stagnates on the
NV 1604-state fixture (2.76077 @ 64 it vs target 1.3673e-6, docs/benchmarks/
liquid-dae-matrix-free-2026-09-27/). Algorithm reference: Pro6000
LearnedPreConditionNewtonSolver SparseSolver/gmres.py — studied, independently
implemented, no source copied.

Gates pinned here:
  * ALGEBRAIC IDENTITY: A p(v) == v - (d-step GMRES residual on v) on the
    probe vector — the only gate that catches a wrong harmonic-root set (a
    wrong set is still a linear degree-matvec map);
  * convergence where plain matrix-free gmres provably stagnates;
  * no full Jacobian ever built in pgmres mode (monkeypatch);
  * honest matvec accounting (info.matvecs <= info.budget_matvecs);
  * DAE level: steady/BE roots and parameter gradients match the dense path
    within linear-solver tolerance (roots are unique; bit-equality is NOT
    expected across different linear solvers);
  * float32 tier runs with its own relaxed tolerance (never silently reusing
    float64 thresholds).
"""
import math

import pytest
import torch

from backend.liquidcooling.solver.dae_newton import (
    DAEProblem,
    NewtonOptions,
    _pgmres,
    _polynomial_preconditioner,
    backward_euler_step,
    solve_steady,
)


def _gmres_d_residual(op, v, d):
    """Independent d-step unpreconditioned GMRES residual on A x = v (test-only,
    built from the textbook Arnoldi + least-squares so the identity gate does
    not share code with the implementation under test)."""
    n = v.numel()
    d = min(d, n)
    q = torch.empty((d + 1, n), dtype=v.dtype)
    h = torch.zeros((d + 1, d), dtype=v.dtype)
    beta = torch.linalg.vector_norm(v)
    q[0] = v / beta
    m = d
    for j in range(d):
        w = op(q[j])
        for i in range(j + 1):
            h[i, j] = q[i] @ w
            w = w - h[i, j] * q[i]
        h[j + 1, j] = torch.linalg.vector_norm(w)
        if float(h[j + 1, j]) < 1e-13 * float(beta):
            m = j + 1
            break
        q[j + 1] = w / h[j + 1, j]
    e1 = torch.zeros(m + 1, dtype=v.dtype)
    e1[0] = beta
    c = torch.linalg.lstsq(h[: m + 1, :m], e1).solution
    return v - op(c @ q[:m])


def _stiff_operator(n=200, seed=5, dtype=torch.float64, hi=(4, 6)):
    """SPD, n >> iteration budget, two-scale spectrum: plain 64-step GMRES
    stagnates (per-step factor ~1-2/sqrt(cond)) while the degree-8 polynomial
    + wide window clears the linear tolerance. n must exceed the plain budget
    or exact-arithmetic GMRES terminates in <= n steps by construction (the
    48-dim first draft of this fixture 'converged' for exactly that reason
    and proved nothing).

    MEASURED BOUNDARY (2026-09-28): with hi=(6,8) (cond 1e8) the degree-8
    polynomial + restart 30 x 6 cycles (1682 matvecs) only reaches true
    residual 0.77 — polynomial preconditioning alone is NOT a universal
    breaker of two-cluster cond-1e8 spectra; that regime needs the M2
    block-triangular / coarse-correction tier (plan assumption A4). The
    contract test therefore pins cond 1e6, which is also the regime the
    fixture is a contract for: 'converges where plain provably stagnates'."""
    gen = torch.Generator().manual_seed(seed)
    q, _ = torch.linalg.qr(torch.randn(n, n, generator=gen, dtype=dtype))
    spectrum = torch.cat((
        torch.logspace(0, 2, n // 2, dtype=dtype),
        torch.logspace(hi[0], hi[1], n - n // 2, dtype=dtype),
    ))
    a = q @ torch.diag(spectrum) @ q.T
    a = (a + a.T) / 2
    return (lambda v: a @ v), a


def test_polynomial_preconditioner_satisfies_the_residual_polynomial_identity():
    op, a = _stiff_operator(n=24, seed=3)
    degree = 6
    p = _polynomial_preconditioner(op, 24, degree, device=torch.device("cpu"),
                                   dtype=torch.float64)
    v = p.probe
    lhs = op(p(v))
    rhs = v - _gmres_d_residual(op, v, degree)
    rel = float(torch.linalg.norm(lhs - rhs) / torch.linalg.norm(v))
    assert rel < 1e-8, f"algebraic identity violated: rel {rel:.3e}"
    assert p.matvecs_per_call <= degree


def test_polynomial_degree_bounds_and_errors():
    op, _ = _stiff_operator(n=8, seed=1)
    with pytest.raises(ValueError):
        _polynomial_preconditioner(op, 8, 0, device=torch.device("cpu"), dtype=torch.float64)
    with pytest.raises(ValueError):
        _polynomial_preconditioner(op, 8, 101, device=torch.device("cpu"), dtype=torch.float64)


def _tail_heavy_operator(n=200, seed=5, dtype=torch.float64):
    """Narrow-band bulk [1,3] (120 dirs) + 80 ISOLATED stiff tail directions
    [1e3,1e5]. Plain 64-step GMRES provably stagnates (the tail exceeds its
    budget; measured 0.837 @ 64 it, 2026-09-28); pgmres converges in 1403
    matvecs (~5 cycles; true residual 1.39e-10, solution rel-err 1.46e-11).

    MEASURED M1 BOUNDARIES (documented, not hidden — same-day diagnostic
    grid .codex-tmp/diag_pgmres2.py): two-cluster cond>=1e6 spectra stall at
    0.106-0.77; tail-100 spanning (2,6) and log-uniform(0,6) continua stall
    at 0.26/0.23 after 20 cycles (geometric gain ~0.4/cycle). Broad continua
    belong to the M2 block-triangular / coarse-correction tier — which is
    exactly the open question G2 (benchmark_nv_pgmres on the real NV 1604
    saddle-point operator) answers; this contract test pins the mechanism in
    M1's effective regime, not M1's universality."""
    gen = torch.Generator().manual_seed(seed)
    q, _ = torch.linalg.qr(torch.randn(n, n, generator=gen, dtype=dtype))
    spectrum = torch.cat((
        torch.logspace(0, math.log10(3.0), 120, dtype=dtype),
        torch.logspace(3, 5, 80, dtype=dtype),
    ))
    a = q @ torch.diag(spectrum) @ q.T
    a = (a + a.T) / 2
    return (lambda v: a @ v), a


def test_pgmres_converges_where_plain_gmres_stagnates():
    op, a = _tail_heavy_operator()
    n = a.shape[0]
    gen = torch.Generator().manual_seed(9)
    b = torch.randn(n, generator=gen, dtype=torch.float64)

    plain = NewtonOptions(linear_solver="gmres", linear_max_iterations=64,
                          linear_atol=1e-12, linear_rtol=1e-10)
    from backend.liquidcooling.solver.dae_newton import _gmres
    with pytest.raises(RuntimeError, match="did not converge"):
        _gmres(op, b, plain)

    pre = NewtonOptions(linear_solver="pgmres", pgmres_restart=30,
                        pgmres_max_cycles=6, poly_degree=8,
                        linear_atol=1e-12, linear_rtol=1e-10)
    x, info = _pgmres(op, b, pre)
    assert info["converged"]
    assert info["matvecs"] <= info["budget_matvecs"]
    true_rel = float(torch.linalg.norm(op(x) - b) / torch.linalg.norm(b))
    assert true_rel < 1e-10
    x_ref = torch.linalg.solve(a, b)
    assert float(torch.linalg.norm(x - x_ref) / torch.linalg.norm(x_ref)) < 1e-8


def test_pgmres_budget_zero_degree_is_plain_restarted_gmres():
    # mild spectrum: with cond 1e8 the float64 exact-termination error scales
    # like kappa*eps (~1e-8), which measures LAPACK, not this solver
    op, a = _stiff_operator(n=16, seed=7, hi=(2, 4))
    b = torch.randn(16, generator=torch.Generator().manual_seed(2), dtype=torch.float64)
    options = NewtonOptions(linear_solver="pgmres", pgmres_restart=16,
                            pgmres_max_cycles=4, poly_degree=0,
                            linear_atol=1e-12, linear_rtol=1e-10)
    x, info = _pgmres(op, b, options)
    assert info["degree"] == 0 and info["per_iteration_matvecs"] == 1
    assert info["converged"]
    assert float(torch.linalg.norm(op(x) - b)) < 1e-8 * float(torch.linalg.norm(b))


def _two_state_problem(dtype=torch.float64):
    def residual(time, state, rate, theta):
        del time
        k, heat = theta.unbind()
        return torch.stack((rate[0] + k * state[0] - heat,
                            state[1] - state[0].square()))
    return DAEProblem(
        residual=residual,
        differential_mask=torch.tensor([True, False]),
        state_scale=torch.ones(2, dtype=dtype),
        residual_scale=torch.ones(2, dtype=dtype),
    )


def _pgmres_options(dtype=torch.float64):
    tol = 1e-7 if dtype == torch.float32 else 1e-12
    return NewtonOptions(linear_solver="pgmres", pgmres_restart=8,
                         pgmres_max_cycles=8, poly_degree=2,
                         linear_atol=tol, linear_rtol=tol,
                         atol=1e-7 if dtype == torch.float32 else 1e-10,
                         rtol=1e-7 if dtype == torch.float32 else 1e-10)


def test_pgmres_forward_solve_builds_no_full_jacobian(monkeypatch):
    """FORWARD contract: matrix-free Newton — no full Jacobian ever built.

    The M1-stage adjoint DELIBERATELY uses the dense Jacobian (transpose-Krylov
    stagnation measured on NV 1604: plateau 0.0044 @ 6983 matvecs, see the
    dae_newton.py pgmres-backward comment and docs/benchmarks/
    dae-pgmres-nv-20260928/), so the ban binds forward solves only; this test
    therefore does no backprop. M2 owns the matrix-free transpose."""
    def forbidden(*args, **kwargs):
        raise AssertionError("pgmres forward built a full Jacobian")
    monkeypatch.setattr(torch.autograd.functional, "jacobian", forbidden)
    problem = _two_state_problem()
    theta = torch.tensor([2.0, 6.0], dtype=torch.float64)  # no grad: forward only
    steady, report = solve_steady(problem, torch.ones(2, dtype=torch.float64),
                                  theta, options=_pgmres_options())
    torch.testing.assert_close(steady, torch.tensor([3.0, 9.0], dtype=torch.float64),
                               rtol=1e-9, atol=1e-10)
    assert report.scaled_residual_inf < 1e-9
    previous = torch.tensor([1.0, 99.0], dtype=torch.float64)
    stepped, _ = backward_euler_step(problem, previous, theta, time=0.1, dt=0.1,
                                     options=_pgmres_options())
    x = (1.0 + 0.1 * 6.0) / (1.0 + 0.1 * 2.0)
    torch.testing.assert_close(stepped, torch.tensor([x, x * x], dtype=torch.float64),
                               rtol=1e-9, atol=1e-10)


def test_pgmres_parameter_gradient_hits_analytic_value():
    """Backprop contract: gradients via the M1 dense adjoint hit the analytic
    value — the gradient does not depend on the forward linear path (unique
    root), which is what licenses the mixed forward-matrix-free / adjoint-dense
    mode."""
    problem = _two_state_problem()
    theta = torch.tensor([2.0, 6.0], dtype=torch.float64, requires_grad=True)
    steady, _ = solve_steady(problem, torch.ones(2, dtype=torch.float64), theta,
                             options=_pgmres_options())
    (grad,) = torch.autograd.grad(steady[1], theta)
    torch.testing.assert_close(grad, torch.tensor([-9.0, 3.0], dtype=torch.float64),
                               rtol=1e-7, atol=1e-8)


def test_pgmres_gradients_match_dense_path():
    problem = _two_state_problem()
    dense = NewtonOptions()  # default dense
    theta_d = torch.tensor([2.3, 5.7], dtype=torch.float64, requires_grad=True)
    y_d, _ = solve_steady(problem, torch.ones(2, dtype=torch.float64), theta_d, options=dense)
    g_d = torch.autograd.grad(y_d.square().sum(), theta_d)[0]
    theta_p = torch.tensor([2.3, 5.7], dtype=torch.float64, requires_grad=True)
    y_p, _ = solve_steady(problem, torch.ones(2, dtype=torch.float64), theta_p,
                          options=_pgmres_options())
    g_p = torch.autograd.grad(y_p.square().sum(), theta_p)[0]
    # different linear solvers -> roots agree to solver tolerance, not bitwise
    rel = float(torch.linalg.norm(g_p - g_d) / torch.linalg.norm(g_d))
    assert rel < 1e-8, rel


def test_pgmres_float32_tier_uses_its_own_tolerance():
    problem = _two_state_problem(dtype=torch.float32)
    theta = torch.tensor([2.0, 6.0], dtype=torch.float32, requires_grad=True)
    y, report = solve_steady(problem, torch.ones(2, dtype=torch.float32), theta,
                             options=_pgmres_options(dtype=torch.float32))
    torch.testing.assert_close(y, torch.tensor([3.0, 9.0], dtype=torch.float32),
                               rtol=1e-4, atol=1e-4)
    assert report.scaled_residual_inf < 1e-5
