"""A time window of the L1 RANS hall as ONE problem of the inversion framework (``steady_framework``), with the
residuals and Jacobian products of all its steps in single batched Warp launches.

The state is the window ``U = (u_1 .. u_K)`` (each ``u_k`` the 7 N unknowns of ``HallStepModel``), from a given start
``u_0``, and the residual stacks the backward-Euler steps of ``HallStepModel``

    R_k = r(u_k; b_k(theta)) + m_k(theta) (u_k - u_{k-1}) / dt,        k = 1 .. K,

``b_k(theta)`` the patch values of snapshot ``first + k - 1`` (``PatchParameterMap.evaluate``) bound into the Warp
boundary tables (``CSPatchBinding.bind``), ``m_k`` the step model's mass vector.  ``DCWarpResidual.window`` evaluates
the K residuals -- or the K products ``J_k v_k`` -- in one launch sequence over ``[K, cells]`` (chunk j = step j), so

* ``residual`` / ``jvp``: one launch each, plus the ``-m_k / dt`` sub-diagonal;
* ``param_jvp`` / ``param_vjp`` / ``batch_tangents``: central differences of the WHOLE window residual in theta, two
  launches per parameter for all K steps at once;
* ``vjp``: the per-step exact transpose (the torch tape of ``dc_adjoint.transpose_tape``) and the super-diagonal;
* ``linear_solve``: block forward substitution (the tangent) or backward substitution (the discrete adjoint in time)
  with the step's own preconditioned FGMRES (``dc_adjoint.solve_tangent`` / ``solve_adjoint``) on ``J_k + m_k / dt``;
* ``solve``: given a trajectory ``u0`` (``fit`` passes the last accepted window) Newton on the whole window (every
  residual one launch, the step by block substitution); otherwise, or when that Newton does not reach the tolerance,
  the step model's own march (``HallStepModel.solve_step``).  The tolerance is the march's: every step's residual
  max-norm at most the largest one an accepted march step had.

All steps of a window must share one solver (one boundary-condition topology: the same ACUs running); a window that
crosses a stop or start of a unit is split there by the caller.
"""
from __future__ import annotations

import math
import time

import torch

from Pipeline.CoupledNewton.dc_adjoint import (
    _clear_solver_autograd,
    _warp_backend,
    solve_adjoint,
    solve_tangent,
    transpose_tape,
)
from Pipeline.CoupledNewton.hall_problem import TANGENT_SETTINGS, TRANSPOSE_SETTINGS
from Pipeline.CoupledNewton.steady_framework import Certificate


class RansWindowProblem:
    """``steps`` backward-Euler steps of ``dt`` of ``step_model`` (a ``HallStepModel``) from ``u_start`` (7 N), the
    step k of the window being snapshot ``first + k - 1`` of its parameter map."""

    def __init__(self, step_model, u_start: torch.Tensor, dt: float, steps: int, *, first: int = 1,
                 newton_iterations: int = 6, krylov_rtol: float = 1e-8, fd_step: float | None = None):
        self.sm = step_model
        self.param_names = tuple(step_model.param_names)
        self.K, self.dt, self.first = int(steps), float(dt), int(first)
        self.device = step_model.device
        self.u_start = u_start.detach().to(self.device, torch.float64).reshape(1, -1)
        self.N = int(self.u_start.shape[1])
        self.newton_iterations, self.krylov_rtol = int(newton_iterations), float(krylov_rtol)
        self.fd_step = float(step_model.fd_step if fd_step is None else fd_step)
        self.tolerance = None                     # per-step residual max-norm, set by the first march
        self._tables_cache = None
        self._cols_cache = None
        self.stats = {"window_launches": 0, "step_solves": 0, "marches": 0, "window_newton": 0}

    # ------------------------------------------------------------------ the window's solver and tables
    def snapshots(self):
        return range(self.first, self.first + self.K)

    def _solver(self, theta):
        th = theta.detach().to("cpu", torch.float64).reshape(-1)
        vals = [self.sm.param_map.evaluate(k, th) for k in self.snapshots()]
        topo = {self.sm._topo_key(v) for v in vals}
        if len(topo) != 1:
            raise NotImplementedError("a window over a change of the running units: split the window there")
        cs, binding, _ = self.sm._get_solver(self.first, vals[0])
        return cs, binding, vals

    def _tables(self, theta):
        """The boundary tables of the K steps at theta (K rows of ``DCWarpResidual.step_tables``), the Warp residual
        and the mass of every step over dt (K, 7N)."""
        key = tuple(float(v) for v in theta.detach().reshape(-1).tolist())
        c = self._tables_cache
        if c is not None and c[0] == key:
            return c[1]
        cs, binding, vals = self._solver(theta)
        W = _warp_backend(cs).W
        rows = {k: [] for k in W.step_tables()}
        for v in vals:
            binding.bind(v, torch_bcs=False)
            try:
                for name, t in W.step_tables().items():
                    rows[name].append(t.clone())
            finally:
                binding.restore()
        th = theta.detach().to("cpu", torch.float64)
        m_dt = torch.stack([self.sm.mass_vector(k, th).to(self.device, torch.float64) for k in self.snapshots()])
        out = (cs, binding, vals, W, {k: torch.stack(v) for k, v in rows.items()}, m_dt / self.dt)
        self._tables_cache = (key, out)
        return out

    def _X(self, U):
        return U.to(self.device, torch.float64).reshape(self.K, self.N)

    def _prev(self, X):
        return torch.cat((self.u_start, X[:-1]), dim=0)

    def _window(self, X, theta, V=None):
        cs, _, _, W, tables, m_dt = self._tables(theta)
        self.stats["window_launches"] += 1
        R, J = W.window(X, V, tables=tables)
        return R, (None if J is None else J[:, 0]), m_dt

    # ------------------------------------------------------------------ the contract
    def residual(self, U, theta):
        X = self._X(U)
        R, _, m_dt = self._window(X, theta)
        return (R + m_dt * (X - self._prev(X))).reshape(-1)

    def step_residuals(self, U, theta):
        """max |R_k| of every step (the tolerance is per step)."""
        return self.residual(U, theta).reshape(self.K, self.N).abs().amax(dim=1)

    def jvp(self, U, theta, V):
        X, Vx = self._X(U), self._X(V)
        _, J, m_dt = self._window(X, theta, Vx)
        out = J + m_dt * Vx
        out[1:] -= m_dt[1:] * Vx[:-1]
        return out.reshape(-1)

    def vjp(self, U, theta, Wt):
        X, Wx = self._X(U), self._X(Wt)
        cs, binding, vals, _, _, m_dt = self._tables(theta)
        out = torch.empty_like(Wx)
        for k in range(self.K):
            binding.bind(vals[k])
            try:
                apply, _ = transpose_tape(cs, X[k])
                out[k] = apply(Wx[k]).detach()
                del apply
                _clear_solver_autograd(cs, X[k])
            finally:
                binding.restore()
        out += m_dt * Wx
        out[:-1] -= m_dt[1:] * Wx[1:]
        return out.reshape(-1)

    def _theta_step(self, theta):
        return self.fd_step * max(1.0, float(theta.detach().abs().max()))

    def param_jvp(self, U, theta, d):
        d = d.detach().to(theta.device, torch.float64).reshape(-1)
        nd = float(d.abs().max())
        if nd == 0.0:
            return torch.zeros(self.K * self.N, dtype=torch.float64, device=self.device)
        h = self._theta_step(theta) / nd
        rp = self.residual(U, theta.detach() + h * d)
        rm = self.residual(U, theta.detach() - h * d)
        self._tables_cache = None
        return (rp - rm) / (2.0 * h)

    def _columns(self, U, theta):
        """dR/dtheta (P, K 7N): two window launches per parameter."""
        key = (U.data_ptr(), tuple(float(v) for v in theta.detach().reshape(-1).tolist()))
        if self._cols_cache is not None and self._cols_cache[0] == key:
            return self._cols_cache[1]
        P = theta.numel()
        cols = torch.stack([self.param_jvp(U, theta, torch.eye(P, dtype=torch.float64)[j].to(theta.device))
                            for j in range(P)])
        self._cols_cache = (key, cols)
        return cols

    def param_vjp(self, U, theta, w):
        cols = self._columns(U, theta)
        return (cols @ w.reshape(-1).to(cols.device, torch.float64)).to(theta.device)

    def _step_solve(self, cs, binding, val, x, rhs, m, transpose):
        binding.bind(val)
        try:
            if transpose:
                sol, info = solve_adjoint(cs, x, rhs, diag=m, **{**TRANSPOSE_SETTINGS, "rtol": self.krylov_rtol})
            else:
                sol, info = solve_tangent(cs, x, rhs, diag=m, **{**TANGENT_SETTINGS, "rtol": self.krylov_rtol})
        finally:
            binding.restore()
        self.stats["step_solves"] += 1
        return sol.detach().reshape(-1), info

    def linear_solve(self, U, theta, b, *, transpose=False):
        X, Bx = self._X(U), self._X(b)
        cs, binding, vals, _, _, m_dt = self._tables(theta)
        x = torch.empty_like(Bx)
        infos = []
        order = range(self.K - 1, -1, -1) if transpose else range(self.K)
        prev = None
        for k in order:
            rhs = Bx[k].clone()
            if prev is not None:
                rhs += (m_dt[k + 1] if transpose else m_dt[k]) * prev
            x[k], info = self._step_solve(cs, binding, vals[k], X[k], rhs, m_dt[k], transpose)
            infos.append(info)
            prev = x[k]
        return x.reshape(-1), {"method": "block substitution, step FGMRES", "transpose": transpose,
                               "step_solves": self.K}

    def batch_tangents(self, U, theta, D):
        cols = self._columns(U, theta)
        Dd = D.to(cols.device, torch.float64)
        out = [self.linear_solve(U, theta, -(cols.T @ Dd[:, c]))[0] for c in range(Dd.shape[1])]
        return torch.stack(out), {"linear_solves": len(out), "method": "window block substitution per column"}

    def observe(self, U, theta):
        X = self._X(U)
        per = [self.sm.step_observe(k, X[i], theta) for i, k in enumerate(self.snapshots())]
        return {name: torch.stack([p[name] for p in per]) for name in per[0]}

    # ------------------------------------------------------------------ forward
    def _march(self, theta):
        prev, states, its, worst = self.u_start[0], [], 0, 0.0
        for k in self.snapshots():
            u, cert = self.sm.solve_step(k, prev, self.dt, theta.detach())
            if not cert.converged:
                return None, its, f"snapshot {k}: {cert.reason}"
            its += int(cert.iterations)
            u = u.to(self.device, torch.float64).reshape(-1)
            states.append(u)
            prev = u
        self.stats["marches"] += 1
        U = torch.stack(states).reshape(-1)
        res = self.step_residuals(U, theta)
        worst = float(res.max())
        self.tolerance = max(self.tolerance or 0.0, worst)
        return U, its, ""

    def _window_newton(self, theta, U):
        """Newton on the whole window: residual in one launch, the step by block substitution, Armijo on the window
        (merit: the sum over steps of |R_k|^2 / tol^2)."""
        tol = self.tolerance
        R = self.residual(U, theta)
        merit = lambda r: float((r / tol).square().sum())                                            # noqa: E731
        for it in range(self.newton_iterations + 1):
            if float(R.reshape(self.K, self.N).abs().amax()) <= tol:
                self.stats["window_newton"] += 1
                return U, it, ""
            if it == self.newton_iterations:
                break
            step, _ = self.linear_solve(U, theta, -R)
            f0, s = merit(R), 1.0
            while s > 1.0 / 64.0:
                cand = U + s * step
                Rc = self.residual(cand, theta)
                if math.isfinite(merit(Rc)) and merit(Rc) <= (1.0 - 1e-4 * s) * f0:
                    U, R = cand, Rc
                    break
                s *= 0.5
            else:
                return None, it, "window Newton: no step reduces the residual"
        return None, self.newton_iterations, "window Newton: iteration limit"

    def solve(self, theta, u0=None):
        t0 = time.perf_counter()
        theta = theta.detach()
        self._cols_cache = None
        method, its, reason, U = "window Newton (warm start)", 0, "", None
        if u0 is not None and u0.numel() == self.K * self.N and self.tolerance is not None:
            U, its, reason = self._window_newton(theta, u0.detach().to(self.device, torch.float64).reshape(-1))
        if U is None:
            method = "march"
            U, its_m, reason = self._march(theta)
            its += its_m
        wall = time.perf_counter() - t0
        if U is None:
            return torch.zeros(1, dtype=torch.float64), Certificate(False, math.inf, its, method, reason, wall)
        res = self.step_residuals(U, theta)
        return U, Certificate(True, float(res.max()), its, method, "", wall,
                              {"steps": self.K, "tolerance": self.tolerance, **self.stats})
