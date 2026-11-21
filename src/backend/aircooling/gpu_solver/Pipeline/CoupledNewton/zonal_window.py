"""A time window of the L0 zonal mesh network as ONE problem of the inversion framework (``steady_framework``).

The state is the window ``U = (u_1 .. u_K)`` (each ``u_k``: (N, 2) = p_rgh, T per zone), from a start ``u_0``, and
the residual stacks the backward-Euler steps

    R_k = r(u_k; b_k(theta)) + M (u_k - u_{k-1}) / dt_k,        k = 1 .. K,

with ``b_k(theta)`` the boundary values of step k (``boundary(theta)`` returns all K rows at once, a differentiable
torch function: the fitted parameters and the known inputs of every step) and ``dt_k`` one step for all or one per
step; ``dt_k = inf`` makes step k steady (``M / dt_k = 0``): a window whose first step is steady starts from the
equilibrium of its own boundary values (the initial condition is then part of the fit), and a window of steady steps
is a set of independent snapshots.  Every residual, Jacobian and observation of the window is ONE batched Warp
launch over ``[K, N, 2]`` (``FVsolver.applications.zonal_network_warp``); the steps couple only through
``M / dt_k`` on the enthalpy rows, so

* ``jvp`` / ``vjp``: the batched block-diagonal product plus the sub- (super-) diagonal ``-M / dt_k`` shift;
* ``linear_solve``: GMRES on the whole window (every matvec one batched launch) preconditioned by block forward
  substitution (the tangent) or backward substitution (the discrete adjoint in time) with ONE frozen sparse LU for
  every diagonal block, refactored at the current point only when GMRES needs more than ``refactor_iterations``
  (``exact=True``: one LU per step, exact substitution);
* ``solve``: from scratch it marches (one Newton solve per step); given a trajectory ``u0`` (``fit`` passes the last
  accepted window) it runs Newton on the whole window at once -- every residual and Jacobian of a Newton iteration
  is one launch -- and falls back to marching only if that Newton stalls;
* ``param_jvp`` / ``param_vjp``: autograd through the torch reference residual, batched over the window.

So ``gradient``, ``tangent``, ``observation_jacobian`` and ``fit`` (the JFGNK fit of sensor series over the window)
apply unchanged, and consecutive windows chain through ``u_0`` (moving-horizon estimation).

Case level (the same inputs as the L1 RANS problems): ``zonal_values`` maps the patch values of a
``PatchParameterMap`` (``evaluate(k, theta)``: T, Q, rise by patch name) onto the network's boundary values,
``ZonalObserver`` gives the L1 observer's observations (``probe:i`` point temperatures, ``return_T:<acu>``) on the
zones, and ``zonal_window_problem`` assembles the window of a parameter map's snapshots.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Mapping, Sequence

import numpy as np
import torch

from FVsolver.applications.zonal_network import ZonalBoundaryValues, ZonalMesh
from FVsolver.applications.zonal_network_warp import WarpZonal
from Pipeline.CoupledNewton.steady_framework import Certificate

KELVIN = 273.15


class ZonalWindowProblem:
    """``K`` backward-Euler steps from the state ``u_start`` (N, 2) (None: the first step must be steady);
    ``dt`` one step (s) or K of them (``inf``: a steady step); ``boundary(theta)`` -> the boundary values of the K
    steps (``ZonalBoundaryValues`` with K rows, or one row for all); ``observe(X, theta)`` -> a dict of tensors from
    the window states X (K, N, 2) (differentiable in both); ``capacity(theta)`` (optional) a multiplier of the
    thermal capacity M, one or one per step (differentiable: a fitted thermal mass)."""

    def __init__(self, net: ZonalMesh, warp: WarpZonal, u_start: torch.Tensor | None, dt, steps: int,
                 boundary: Callable[[torch.Tensor], ZonalBoundaryValues],
                 observe: Callable[[torch.Tensor, torch.Tensor], dict], param_names, *,
                 rtol: float = 1e-10, max_iterations: int = 30, krylov_rtol: float = 1e-11,
                 refactor_iterations: int = 12, exact: bool = False,
                 capacity: Callable[[torch.Tensor], torch.Tensor] | None = None):
        self.net, self.wz = net, warp
        self.device = warp.device
        self.N, self.K = net.n_cells, int(steps)
        d = torch.as_tensor(dt, dtype=torch.float64).reshape(-1).expand(self.K).clone()
        self.dts = d
        if u_start is None and math.isfinite(float(d[0])):
            raise ValueError("a window without a start state needs a steady first step (dt[0] = inf)")
        self.u_start = (torch.zeros(1, self.N, 2, dtype=torch.float64, device=self.device) if u_start is None
                        else u_start.detach().to(self.device, torch.float64).reshape(1, self.N, 2))
        self.boundary = boundary
        self._observe = observe
        self.param_names = tuple(param_names)
        self.rtol, self.max_iterations = rtol, max_iterations
        self._inv = torch.where(torch.isfinite(d), 1.0 / d, torch.zeros_like(d)).to(self.device)     # (K,)
        self._mass = net.mass_vector().to(self.device).unsqueeze(0)                                  # (1, N, 2)
        self.capacity = capacity
        self.m_dt = self._mass * self._inv.reshape(-1, 1, 1)          # (K, N, 2) at capacity 1
        self.krylov_rtol, self.refactor_iterations, self.exact = krylov_rtol, refactor_iterations, exact
        self._cache = None
        self._ref_cache = None
        self._frozen = None                  # the frozen LU of every step (and M / dt_k where it was factored)
        self.n_solves = 0
        self.stats = {"factorizations": 0, "gmres": 0, "gmres_iterations": 0, "exact_fallbacks": 0}

    # ------------------------------------------------------------------ helpers
    @property
    def dt(self):
        return self.dts

    def _cap(self, theta, grad=False):
        """(K,) the capacity multiplier of every step at theta (1 without ``capacity``)."""
        if self.capacity is None:
            return torch.ones(self.K, dtype=torch.float64, device=self.device)
        c = self.capacity(theta if grad else theta.detach())
        c = torch.as_tensor(c, dtype=torch.float64).to(self.device).reshape(-1).expand(self.K)
        return c if grad else c.detach()

    def _mdt(self, theta, grad=False):
        """(K, N, 2): c_k M / dt_k."""
        return self._mass * (self._inv * self._cap(theta, grad)).reshape(-1, 1, 1)

    def _dts(self, theta):
        """(K,) the step the kernels take: dt_k / c_k (inf: steady)."""
        return self.dts.to(self.device) / self._cap(theta)

    def _X(self, U):
        return U.to(self.device, torch.float64).reshape(self.K, self.N, 2)

    def _prev(self, X):
        return torch.cat((self.u_start, X[:-1]), dim=0)

    def _bv(self, theta):
        bv = self.boundary(theta.detach())
        return bv.expand(self.K) if bv.flow.shape[0] == 1 else bv

    def _jac(self, U, theta):
        """Residual, Jacobian values and LU factors of every step at (U, theta), cached for the last point."""
        c = self._cache
        if c is not None and torch.equal(c[0], U.detach()) and torch.equal(c[1], theta.detach()):
            return c[2]
        X = self._X(U)
        R, vals = self.wz.evaluate(X, self._bv(theta), X_prev=self._prev(X), dt=self._dts(theta), jacobian=True)
        out = {"R": R, "vals": vals, "lu": None, "m_dt": self._mdt(theta)}
        self._cache = (U.detach().clone(), theta.detach().clone(), out)
        return out

    def _lu(self, U, theta):
        j = self._jac(U, theta)
        if j["lu"] is None:
            j["lu"] = self.wz.factor(j["vals"])
        return j["lu"]

    # ------------------------------------------------------------------ the contract
    def residual(self, U, theta):
        X = self._X(U)
        return self.wz.evaluate(X, self._bv(theta), X_prev=self._prev(X), dt=self._dts(theta)).reshape(-1)

    def jvp(self, U, theta, V):
        j = self._jac(U, theta)
        Vx = self._X(V)
        out = self.wz.jvp(j["vals"], Vx)
        out[1:] -= j["m_dt"][1:] * Vx[:-1]
        return out.reshape(-1)

    def vjp(self, U, theta, W):
        j = self._jac(U, theta)
        Wx = self._X(W)
        out = self.wz.vjp(j["vals"], Wx)
        out[:-1] -= j["m_dt"][1:] * Wx[1:]
        return out.reshape(-1)

    def _reference(self, U, theta):
        """The window residual by the torch reference (differentiable in theta)."""
        X = self._X(U).detach()
        bv = self.boundary(theta)
        bv = bv.expand(self.K) if bv.flow.shape[0] == 1 else bv
        return (self.net.residual(X, bv) + self._mdt(theta, grad=True) * (X - self._prev(X))).reshape(-1)

    def param_jvp(self, U, theta, d):
        with torch.enable_grad():
            _, jv = torch.autograd.functional.jvp(lambda t: self._reference(U, t), theta.detach(), d)
        return jv.detach()

    def _reference_graph(self, U, theta):
        """(theta leaf, reference residual) at (U, theta), kept for the last point: the parameter map's Python
        evaluation runs once per point, every transpose product reuses its graph."""
        c = self._ref_cache
        if c is not None and torch.equal(c[0], U.detach()) and torch.equal(c[1], theta.detach()):
            return c[2], c[3]
        t = theta.detach().clone().requires_grad_(True)
        with torch.enable_grad():
            r = self._reference(U, t)
        self._ref_cache = (U.detach().clone(), theta.detach().clone(), t, r)
        return t, r

    def param_vjp(self, U, theta, w):
        with torch.enable_grad():
            t, r = self._reference_graph(U, theta)
            (g,) = torch.autograd.grad(r, t, w.reshape(-1).to(self.device), retain_graph=True, allow_unused=True)
        return torch.zeros_like(theta) if g is None else g.detach().to(theta.device)

    def param_vjp_batch(self, U, theta, W):
        """r_theta^T w for every row of W (m, n) in one batched backward pass (row by row where the graph has an
        operation the batched pass does not support)."""
        W = W.to(self.device, torch.float64)
        with torch.enable_grad():
            t, r = self._reference_graph(U, theta)
            try:
                (g,) = torch.autograd.grad(r, t, W, retain_graph=True, allow_unused=True, is_grads_batched=True)
            except RuntimeError:
                rows = [torch.autograd.grad(r, t, w, retain_graph=True, allow_unused=True)[0] for w in W]
                g = torch.stack([torch.zeros_like(t) if x is None else x for x in rows]) if rows else None
        if g is None:
            return torch.zeros((W.shape[0], theta.numel()), dtype=theta.dtype, device=theta.device)
        return g.detach().to(theta.device)

    def _exact_solve(self, U, theta, b, transpose):
        lus = self._lu(U, theta)
        mdt = self._jac(U, theta)["m_dt"]
        self.stats["factorizations"] += self.K
        Bx = self._X(b)
        x = torch.empty_like(Bx)
        if not transpose:                                   # forward substitution
            for k in range(self.K):
                rhs = Bx[k:k + 1] if k == 0 else Bx[k:k + 1] + mdt[k:k + 1] * x[k - 1:k]
                x[k:k + 1] = self.wz.lu_solve([lus[k]], rhs)
        else:                                               # backward substitution: the adjoint in time
            for k in range(self.K - 1, -1, -1):
                rhs = Bx[k:k + 1] if k == self.K - 1 else Bx[k:k + 1] + mdt[k + 1:k + 2] * x[k + 1:k + 2]
                x[k:k + 1] = self.wz.lu_solve([lus[k]], rhs, transpose=True)
        return x.reshape(-1)

    def _refactor(self, U, theta):
        """Freeze the LU of every step's block at (U, theta): reused across Newton and Gauss-Newton iterations
        (the window moves little between them) until GMRES needs more than ``refactor_iterations``."""
        self._frozen = (self._lu(U, theta), self._jac(U, theta)["m_dt"])
        self.stats["factorizations"] += self.K

    def _precondition(self, r, transpose):
        """Block substitution with the frozen LU of every diagonal block (exact -M/dt_k off the diagonal)."""
        lus, m_dt = self._frozen
        R = r.reshape(self.K, self.N * 2).cpu().numpy()
        mdt = m_dt.reshape(self.K, -1).cpu().numpy()
        x = np.empty_like(R)
        order = range(self.K - 1, -1, -1) if transpose else range(self.K)
        prev = None
        for k in order:
            if prev is None:
                rhs = R[k]
            else:
                rhs = R[k] + (mdt[k + 1] if transpose else mdt[k]) * prev
            x[k] = lus[k].solve(rhs, trans="T" if transpose else "N")
            prev = x[k]
        return torch.as_tensor(x, device=self.device).reshape(-1)

    def _gmres(self, A, M, b, rtol, maxiter=200, restart=60):
        """Right-preconditioned restarted GMRES (float64, modified Gram-Schmidt); (x, iterations, converged)."""
        nb = float(torch.linalg.vector_norm(b))
        x = torch.zeros_like(b)
        if nb == 0.0:
            return x, 0, True
        its = 0
        while its < maxiter:
            r = b - A(x)
            beta = float(torch.linalg.vector_norm(r))
            if beta <= rtol * nb:
                return x, its, True
            m = min(restart, maxiter - its)
            V = [r / beta]
            Z = []
            H = torch.zeros(m + 1, m, dtype=torch.float64)
            k_done = 0
            for k in range(m):
                z = M(V[k])
                w = A(z)
                for i in range(k + 1):
                    H[i, k] = float(torch.dot(w, V[i]))
                    w = w - H[i, k] * V[i]
                H[k + 1, k] = float(torch.linalg.vector_norm(w))
                Z.append(z)
                its += 1
                k_done = k + 1
                e1 = torch.zeros(k + 2, dtype=torch.float64)
                e1[0] = beta
                y = torch.linalg.lstsq(H[:k + 2, :k + 1], e1.unsqueeze(1)).solution.squeeze(1)
                res = float(torch.linalg.vector_norm(H[:k + 2, :k + 1] @ y - e1))
                if res <= rtol * nb or float(H[k + 1, k]) <= 1e-300:
                    break
                V.append(w / H[k + 1, k])
            y = y.to(b.device)
            x = x + sum(y[i] * Z[i] for i in range(k_done))
            if res <= rtol * nb:
                return x, its, True
        return x, its, False

    def linear_solve(self, U, theta, b, *, transpose=False):
        b = b.to(self.device, torch.float64).reshape(-1)
        if self.exact:
            return self._exact_solve(U, theta, b, transpose), {"method": "block substitution (exact LU per step)",
                                                               "transpose": transpose}
        A = (lambda v: self.vjp(U, theta, v)) if transpose else (lambda v: self.jvp(U, theta, v))
        for attempt in range(2):
            if self._frozen is None:
                self._refactor(U, theta)
            x, its, ok = self._gmres(A, lambda r: self._precondition(r, transpose), b, self.krylov_rtol,
                                     maxiter=self.refactor_iterations)
            self.stats["gmres"] += 1
            self.stats["gmres_iterations"] += its
            if ok:
                return x, {"method": "GMRES, frozen-LU block substitution", "iterations": its, "transpose": transpose}
            self._frozen = None                              # refactor at this point and try once more
        self.stats["exact_fallbacks"] += 1
        return self._exact_solve(U, theta, b, transpose), {"method": "block substitution (exact fallback)",
                                                           "transpose": transpose}

    def batch_tangents(self, U, theta, D):
        """dU for every column of D (p, m): ``J dU = -R_theta D`` (R_theta D column by column in forward mode)."""
        Dd = D.to(self.device, torch.float64)
        cols = []
        for c in range(Dd.shape[1]):
            rhs = -self.param_jvp(U, theta, Dd[:, c].to(theta.device)).to(self.device)
            cols.append(self.linear_solve(U, theta, rhs)[0])
        return torch.stack(cols), {"linear_solves": len(cols), "method": "window GMRES per column"}

    def observe(self, U, theta):
        return self._observe(self._X(U), theta)

    # ------------------------------------------------------------------ forward
    def _tolerance(self, theta):
        bv = self._bv(theta)
        q = float(bv.flow.abs().sum(dim=1).max()) or 1.0
        return torch.tensor([self.rtol * q, self.rtol * q * 10.0], dtype=torch.float64, device=self.device)

    def _march(self, theta):
        """One step at a time: a Newton solve of each step from the one before (WarpZonal.solve, exact LU); a steady
        first step without a start state from the network's own initial state."""
        bv = self._bv(theta)
        dts = self._dts(theta)
        prev, states, its = self.u_start, [], 0
        for k in range(self.K):
            row = ZonalBoundaryValues(bv.flow[k:k + 1], bv.t_fixed[k:k + 1], bv.delta_t[k:k + 1], bv.p_ref[k:k + 1],
                                      None if bv.porous_i is None else bv.porous_i[k:k + 1])
            steady = not math.isfinite(float(dts[k]))
            X0 = None if (steady and k == 0 and not bool(prev.abs().any())) else prev
            X, info = self.wz.solve(row, X0=X0, X_prev=None if steady else prev,
                                    dt=None if steady else float(dts[k]), rtol=self.rtol,
                                    max_iterations=self.max_iterations)
            if not info["converged"]:
                return None, its, f"step {k + 1}: {info['method']} did not converge"
            its += info["iterations"]
            self.stats["factorizations"] += info["iterations"]
            states.append(X)
            prev = X
        return torch.cat(states).reshape(-1), its, ""

    def _window_newton(self, theta, U):
        """Newton on the whole window (exact Jacobian, block-substitution solve, Armijo backtrack on the window)."""
        tol = self._tolerance(theta)
        merit = lambda R: float(((R.reshape(self.K, self.N, 2) / tol) ** 2).sum())  # noqa: E731
        norm = lambda R: float((R.reshape(self.K, self.N, 2).abs() / tol).max())    # noqa: E731
        R = self.residual(U, theta)
        for it in range(self.max_iterations + 1):
            if norm(R) < 1.0:
                return U, it, ""
            step, _ = self.linear_solve(U, theta, -R)
            f0, s = merit(R), 1.0
            while s > 1e-4:
                cand = U + s * step
                Rc = self.residual(cand, theta)
                if math.isfinite(merit(Rc)) and merit(Rc) <= (1.0 - 1e-4 * s) * f0:
                    U, R = cand, Rc
                    break
                s *= 0.5
            else:
                return None, it, "window Newton: no step reduces the residual"
        return None, self.max_iterations, "window Newton: iteration limit"

    def solve(self, theta, u0=None):
        t0 = time.perf_counter()
        theta = theta.detach()
        self.n_solves += 1
        method, its, reason = "window Newton (warm start)", 0, ""
        U = None
        if u0 is not None and u0.numel() == self.K * self.N * 2:
            U, its, reason = self._window_newton(theta, u0.detach().to(self.device, torch.float64).reshape(-1))
        if U is None:
            method = "march + window Newton"
            U, its_m, reason = self._march(theta)
            its += its_m
            if U is not None:
                U, its_w, reason = self._window_newton(theta, U)
                its += its_w
        if U is None:
            return torch.zeros(1, dtype=torch.float64), Certificate(False, math.inf, its, method, reason,
                                                                     time.perf_counter() - t0)
        tol = self._tolerance(theta)
        rel = float((self.residual(U, theta).reshape(self.K, self.N, 2).abs() / tol).max())
        return U, Certificate(True, rel, its, method, "", time.perf_counter() - t0,
                              {"steps": self.K, "residual_rel": rel, "cells": self.N, **self.stats})


# ---------------------------------------------------------------------------------------------- case level
def zonal_values(net: ZonalMesh, vals: Mapping, base: ZonalBoundaryValues | None = None) -> ZonalBoundaryValues:
    """One row of boundary values from the patch values of ``PatchParameterMap.evaluate`` (``T`` K absolute, ``Q``
    m3/s, ``rise`` K, ``I`` / ``I_regex`` 1/m, by patch name; differentiable): every flowRate patch takes its ``Q``,
    every fixedValue temperature its ``T`` (relative to the network's ``t_ref``), every rack its ``rise``, every
    porous baffle its ``I`` (either side's name, or a regex of ``I_regex`` matching it); patches without a value keep
    ``base`` (default: the case's own).  A fixed wall temperature is refused (the zonal network's walls are
    adiabatic)."""
    if vals.get("wall_T"):
        raise NotImplementedError("zonal network: wall temperatures (the network's walls are adiabatic)")
    base = net.default_values if base is None else base
    dev = net.device

    def row(names, current, given, shift=0.0):
        if not names:
            return current.to(dev)
        cols = []
        for j, name in enumerate(names):
            v = given.get(name)
            if v is None:
                cols.append(current[0, j].to(dev))
            else:
                cols.append(torch.as_tensor(v, dtype=torch.float64, device=dev).reshape(()) - shift)
        return torch.stack(cols).reshape(1, -1)

    porous = None
    if base.porous_i is not None:
        import re
        given = dict(vals.get("I", {}))
        regex = [(re.compile(k), v) for k, v in (vals.get("I_regex") or {}).items()]
        by_col = {}
        for j, (a, b) in enumerate(zip(net.porous_patches, net.porous_partners)):
            v = given.get(a, given.get(b))
            if v is None:
                v = next((val for rx, val in regex if rx.fullmatch(a) or rx.fullmatch(b)), None)
            if v is not None:
                by_col[a] = v
        porous = row(net.porous_patches, base.porous_i, by_col)
    return ZonalBoundaryValues(
        flow=row(net.flow_patches, base.flow, vals.get("Q", {})),
        t_fixed=row(net.fixed_t_patches, base.t_fixed, vals.get("T", {}), net.t_ref),
        delta_t=row(net.racks, base.delta_t, vals.get("rise", {})),
        p_ref=base.p_ref.to(dev),
        porous_i=porous,
    )


class ZonalObserver:
    """The observations of ``patch_binding.HallProbeObserver`` on the zones: ``probe:i`` (or the given names) the
    temperature of the zone holding each point, ``return_T:<acu>`` the mean temperature of the zones an ACU's return
    patch draws from (a flowRate return: by the share of every outflow item of that patch; a pressure-reference
    return, an opening: by face area); in K or C (``units``)."""

    def __init__(self, net: ZonalMesh, *, probe_coords=None, probe_names: Sequence[str] | None = None,
                 returns: Mapping[str, str] | None = None, units: str = "C"):
        self.net, self.units = net, units
        dev = net.device
        pts = None if probe_coords is None or len(probe_coords) == 0 else np.asarray(probe_coords, float).reshape(-1, 3)
        self.probe_cells = (torch.zeros(0, dtype=torch.long, device=dev) if pts is None
                            else torch.as_tensor(net.cells_at(pts), dtype=torch.long, device=dev))
        if pts is not None and bool((self.probe_cells < 0).any()):
            bad = int((self.probe_cells < 0).sum())
            raise ValueError(f"zonal observer: {bad} probe point(s) outside every zone")
        self.probe_names = (list(probe_names) if probe_names is not None
                            else [f"probe:{i}" for i in range(0 if pts is None else len(pts))])
        self.returns = {}
        col = {name: j for j, name in enumerate(net.flow_patches)}
        pcol = {name: j for j, name in enumerate(net.pref_patches)}
        for acu, patch in (returns or {}).items():
            if patch in col:                 # a flowRate return: its outflow items, by their share of the flow
                sel = (net.out_patch == col[patch]).nonzero(as_tuple=True)[0]
                cells, w = net.out_cell[sel], net.out_share[sel]
            elif patch in pcol:              # a pressure-reference return (an opening): its zones, by face area
                sel = (net.pref_index == pcol[patch]).nonzero(as_tuple=True)[0]
                cells, w = net.pref_cell[sel], net.pref_area[sel]
            else:
                raise ValueError(f"zonal observer: return patch {patch!r} of {acu!r} is not a boundary of the network")
            if sel.numel() == 0:
                raise ValueError(f"zonal observer: return patch {patch!r} draws from no zone")
            self.returns[acu] = (cells, w / w.sum())
        self.names = self.probe_names + [f"return_T:{a}" for a in self.returns]

    def _units(self, T):
        T = T + self.net.t_ref
        return T - KELVIN if self.units.upper() == "C" else T

    def __call__(self, X: torch.Tensor, theta=None) -> dict:
        """X (K, N, 2) -> {name: (K,)}."""
        T = X[..., 1]
        out = {}
        for j, name in enumerate(self.probe_names):
            out[name] = self._units(T[:, self.probe_cells[j]])
        for acu, (cells, share) in self.returns.items():
            out[f"return_T:{acu}"] = self._units((T[:, cells] * share).sum(dim=1))
        return out


def zonal_window_problem(net: ZonalMesh, wz: WarpZonal, pmap, snapshots: Sequence[int], dt, *,
                         u_start: torch.Tensor | None = None, observer: ZonalObserver | None = None,
                         **kw) -> ZonalWindowProblem:
    """The window of the snapshots ``snapshots`` of the parameter map ``pmap`` (a ``PatchParameterMap``: the same
    parameters, priors and patch values as the L1 RANS problems); ``dt`` one step or one per snapshot (``inf``:
    steady -- a first steady step without ``u_start`` starts the window from the equilibrium of that snapshot)."""
    ks = [int(k) for k in snapshots]
    obs = observer if observer is not None else ZonalObserver(net)

    def boundary(theta):
        rows = [zonal_values(net, pmap.evaluate(k, theta)) for k in ks]
        return ZonalBoundaryValues(*(None if getattr(rows[0], f) is None else torch.cat([getattr(r, f) for r in rows])
                                     for f in ("flow", "t_fixed", "delta_t", "p_ref", "porous_i")))

    def capacity(theta):                     # the parameter map's thermal-mass multiplier (ltau), every step
        return torch.stack([torch.as_tensor(pmap.evaluate(k, theta).get("tau_scale", 1.0), dtype=torch.float64)
                            .reshape(()) for k in ks])

    return ZonalWindowProblem(net, wz, u_start, dt, len(ks), boundary, obs, pmap.param_names, capacity=capacity,
                              **kw)
