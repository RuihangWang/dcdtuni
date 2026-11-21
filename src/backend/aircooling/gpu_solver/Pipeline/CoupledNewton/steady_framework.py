"""One iterative framework for steady models: the nonlinear state, its linear sensitivities, and the overdetermined
fit of parameters to measurements (docs/research/2026-11-13-unified-steady-framework.md).

A model implements the ``SteadyProblem`` contract at a state ``u`` and parameters ``theta`` (flat float64 vectors):

    solve(theta, u0=None) -> (u, Certificate)      its own globalised Newton
    residual(u, theta)                             the residual of record r(u; theta)
    jvp(u, theta, v) / vjp(u, theta, w)            J v and J^T w, J = dr/du
    param_jvp(u, theta, d) / param_vjp(u, theta, w)  r_theta d and r_theta^T w
    linear_solve(u, theta, b, transpose)           x with J x = b (J^T x = b), and an info dict
    observe(u, theta) -> {name: tensor}            the quantities callers read, differentiable in u and theta
    param_names                                    one name per entry of theta

On top of it, independent of the model:

    gradient(problem, u, theta, objective)         adjoint gradient of objective(observe(u, theta), theta)
    tangent(problem, u, theta, d)                  du = -J^{-1} r_theta d and the observation change
    observation_jacobian(problem, u, theta, keys)  G = dh/dtheta along the steady manifold (tangent or adjoint mode)
    fit(problem, measurements, ...)                bounded Levenberg-Marquardt Gauss-Newton on the weighted misfit,
                                                   with a Gaussian prior; dense G or matrix-free LSQR (one tangent and
                                                   one adjoint solve per inner iteration: Jacobian-free Gauss-Newton-
                                                   Krylov); posterior covariance and the unidentified directions

``DenseProblem`` is the backend of small models whose residual is a torch function (the zonal network): autograd
Jacobians, LU solves, and the damped Newton / Levenberg-Marquardt / pseudo-transient forward solve.
"""
from __future__ import annotations

import inspect
import math
import os
from pathlib import Path
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence

import torch

__all__ = ["Certificate", "SteadyProblem", "DenseProblem", "TrajectoryProblem", "gradient", "tangent", "observation_jacobian",
           "Measurements", "FitResult", "fit", "check_contract"]


# ------------------------------------------------------------------------------------------------ the contract
@dataclass
class Certificate:
    """What a forward solve reports, in one schema for every model."""
    converged: bool
    residual_inf: float
    iterations: int
    method: str
    reason: str = ""
    wall_s: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"converged": self.converged, "residual_inf": self.residual_inf, "iterations": self.iterations,
                "method": self.method, "reason": self.reason, "wall_s": self.wall_s, **self.extra}


class SteadyProblem(Protocol):
    param_names: Sequence[str]

    def solve(self, theta: torch.Tensor, u0: torch.Tensor | None = None) -> tuple[torch.Tensor, Certificate]: ...
    def residual(self, u: torch.Tensor, theta: torch.Tensor) -> torch.Tensor: ...
    def jvp(self, u, theta, v) -> torch.Tensor: ...
    def vjp(self, u, theta, w) -> torch.Tensor: ...
    def param_jvp(self, u, theta, d) -> torch.Tensor: ...
    def param_vjp(self, u, theta, w) -> torch.Tensor: ...
    def linear_solve(self, u, theta, b, *, transpose: bool = False) -> tuple[torch.Tensor, dict]: ...
    def observe(self, u, theta) -> dict: ...


# ------------------------------------------------------------------------------------------------ autograd helpers
def _flat_obs(problem, u, theta, keys):
    obs = problem.observe(u, theta)
    missing = [k for k in keys if k not in obs]
    if missing:
        raise KeyError(f"observe() has no {missing}")
    return torch.cat([obs[k].reshape(-1).to(theta.device) for k in keys])   # a model on another device


def _obs_sizes(problem, u, theta, keys):
    with torch.no_grad():
        obs = problem.observe(u, theta)
    return [obs[k].numel() for k in keys]


def _obs_vjp(problem, u, theta, keys, w):
    """(h_u^T w, h_theta^T w)."""
    with torch.enable_grad():
        uu = u.detach().clone().requires_grad_(True)
        tt = theta.detach().clone().requires_grad_(True)
        h = _flat_obs(problem, uu, tt, keys)
        gu, gt = torch.autograd.grad(h, (uu, tt), w, allow_unused=True)
    return (torch.zeros_like(u) if gu is None else gu.detach(),
            torch.zeros_like(theta) if gt is None else gt.detach())


def _obs_jvp(problem, u, theta, keys, du, dtheta):
    """h_u du + h_theta dtheta by the double-backward identity (works for any twice-differentiable observe)."""
    with torch.enable_grad():
        uu = u.detach().clone().requires_grad_(True)
        tt = theta.detach().clone().requires_grad_(True)
        h = _flat_obs(problem, uu, tt, keys)
        w = torch.zeros_like(h, requires_grad=True)
        gu, gt = torch.autograd.grad(h, (uu, tt), w, create_graph=True, allow_unused=True)
        s = h.new_zeros(())
        if gu is not None:
            s = s + (gu * du).sum()
        if gt is not None:
            s = s + (gt * dtheta).sum()
        if not s.requires_grad:
            return torch.zeros_like(h).detach()
        (out,) = torch.autograd.grad(s, w)
    return out.detach()


# ------------------------------------------------------------------------------------------------ sensitivities
def gradient(problem, u, theta, objective: Callable[[dict, torch.Tensor], torch.Tensor]):
    """``(f, df/dtheta, info)`` of ``f = objective(observe(u, theta), theta)`` at the steady state ``u`` by one
    transposed solve: ``J^T lam = f_u``, ``df/dtheta = f_theta - r_theta^T lam``."""
    t0 = time.perf_counter()
    with torch.enable_grad():
        uu = u.detach().clone().requires_grad_(True)
        tt = theta.detach().clone().requires_grad_(True)
        f = objective(problem.observe(uu, tt), tt)
        fu, ft = torch.autograd.grad(f, (uu, tt), allow_unused=True)
    fu = torch.zeros_like(u) if fu is None else fu.detach()
    ft = torch.zeros_like(theta) if ft is None else ft.detach()
    lam, linfo = problem.linear_solve(u, theta, fu, transpose=True)
    g = ft - problem.param_vjp(u, theta, lam)
    achieved = float((problem.vjp(u, theta, lam) - fu).norm() / max(float(fu.norm()), 1e-300))
    info = {"adjoint": linfo, "adjoint_rel_residual": achieved, "wall_s": time.perf_counter() - t0}
    return float(f.detach()), g.detach(), info


def tangent(problem, u, theta, d, keys: Sequence[str] = ()):
    """``(du, dh, info)``: the state change ``-J^{-1} r_theta d`` and, for ``keys``, the observation change."""
    rhs = -problem.param_jvp(u, theta, d)
    du, linfo = problem.linear_solve(u, theta, rhs, transpose=False)
    dh = _obs_jvp(problem, u, theta, keys, du, d) if keys else None
    return du, dh, linfo


def observation_jacobian(problem, u, theta, keys: Sequence[str], *, mode: str = "auto",
                         columns: Sequence[int] | None = None):
    """Dense ``G = dh/dtheta`` (rows: the flattened observations ``keys``) along r(u(theta), theta) = 0.

    ``mode``: ``"tangent"`` (one J solve per parameter), ``"adjoint"`` (one J^T solve per observation) or
    ``"auto"`` (the cheaper of the two). ``columns``: the parameters whose columns are needed (tangent mode solves
    only those; the others are left zero)."""
    m = sum(_obs_sizes(problem, u, theta, keys))
    p = theta.numel()
    cols = list(range(p)) if columns is None else list(columns)
    if mode == "auto":
        mode = "tangent" if len(cols) <= m else "adjoint"
    if mode == "tangent" and hasattr(problem, "batch_tangents") and cols:
        D = theta.new_zeros((p, len(cols)))
        for idx, col in enumerate(cols):
            D[col, idx] = 1.0
        du_cols, info_b = problem.batch_tangents(u, theta, D)
        G = theta.new_zeros((m, p))
        for idx, col in enumerate(cols):
            G[:, col] = _obs_jvp(problem, u, theta, keys, du_cols[idx], D[:, idx]).to(G.device)
        return G, {"mode": "tangent-batch", "linear_solves": int(info_b.get("linear_solves", len(cols)))}
    G = theta.new_zeros((m, p))
    solves = 0
    if mode == "tangent":
        for i in cols:
            e = torch.zeros_like(theta)
            e[i] = 1.0
            _, dh, _ = tangent(problem, u, theta, e, keys)
            G[:, i] = dh.to(G.device)
            solves += 1
    elif mode == "adjoint" and hasattr(problem, "param_vjp_batch") and m:
        # every row's transpose solve, then the parameter products of all rows in one batched pass
        hts, lams = [], []
        for j in range(m):
            w = theta.new_zeros(m)
            w[j] = 1.0
            hu, ht = _obs_vjp(problem, u, theta, keys, w)
            lam, _ = problem.linear_solve(u, theta, hu, transpose=True)
            hts.append(ht)
            lams.append(lam.reshape(-1))
            solves += 1
        G = (torch.stack(hts) - problem.param_vjp_batch(u, theta, torch.stack(lams))).to(G.device)
    elif mode == "adjoint":
        for j in range(m):
            w = theta.new_zeros(m)
            w[j] = 1.0
            hu, ht = _obs_vjp(problem, u, theta, keys, w)
            lam, _ = problem.linear_solve(u, theta, hu, transpose=True)
            G[j] = (ht - problem.param_vjp(u, theta, lam)).to(G.device)
            solves += 1
    else:
        raise ValueError(f"mode {mode!r}")
    return G, {"mode": mode, "linear_solves": solves}


# ------------------------------------------------------------------------------------------------ the fit
@dataclass
class Measurements:
    """Measured values ``y`` of observation ``key`` (one tensor per key, the shape of ``observe()[key]``) with their
    standard deviations ``sigma`` (same shapes, or scalars). NaN entries of ``y`` are unmeasured."""
    y: Mapping[str, torch.Tensor]
    sigma: Mapping[str, torch.Tensor | float]

    def keys(self):
        return list(self.y)

    def flat(self, like):
        y = torch.cat([torch.as_tensor(self.y[k], dtype=like.dtype, device=like.device).reshape(-1) for k in self.y])
        s = torch.cat([torch.broadcast_to(torch.as_tensor(self.sigma[k], dtype=like.dtype, device=like.device),
                                          torch.as_tensor(self.y[k]).shape).reshape(-1) for k in self.y])
        mask = torch.isfinite(y)
        if (s[mask] <= 0).any():
            raise ValueError("measurement standard deviations must be positive")
        return y, s, mask


@dataclass
class FitResult:
    theta: torch.Tensor
    state: torch.Tensor
    misfit: float                       # 0.5 * (weighted residual^2 + prior term)
    data_misfit: float                  # 0.5 * weighted residual^2
    iterations: int
    converged: bool
    reason: str
    covariance: torch.Tensor | None     # posterior (G^T W G + P)^{-1} at the solution (dense mode)
    posterior_sigma: torch.Tensor | None
    unidentified: list                  # [(singular value of W^1/2 G P^-1/2, {name: weight})] below the threshold
    history: list
    forward_solves: int
    linear_solves: int
    wall_s: float

    def named(self, names):
        return dict(zip(names, self.theta.tolist()))


def _lsqr(Aop, ATop, b, n, *, damp=0.0, atol=1e-10, btol=1e-10, max_iter=200):
    """Paige & Saunders LSQR for min ||A x - b||^2 + damp^2 ||x||^2 (matrix-free)."""
    u = b.clone()
    beta = float(u.norm())
    x = b.new_zeros(n)
    if beta == 0:
        return x, 0
    u = u / beta
    v = ATop(u)
    alpha = float(v.norm())
    if alpha == 0:
        return x, 0
    v = v / alpha
    w = v.clone()
    phibar, rhobar = beta, alpha
    bnorm = beta
    it = 0
    for it in range(1, max_iter + 1):
        u = Aop(v) - alpha * u
        beta = float(u.norm())
        if beta > 0:
            u = u / beta
        rhobar1 = math.hypot(rhobar, damp)
        cs1, sn1 = rhobar / rhobar1, damp / rhobar1
        phibar = cs1 * phibar
        v = ATop(u) - beta * v
        alpha = float(v.norm())
        if alpha > 0:
            v = v / alpha
        rho = math.hypot(rhobar1, beta)
        c, s = rhobar1 / rho, beta / rho
        theta_ = s * alpha
        rhobar = -c * alpha
        phi = c * phibar
        phibar = s * phibar
        x = x + (phi / rho) * w
        w = v - (theta_ / rho) * w
        if abs(phibar) <= btol * bnorm or abs(phibar * alpha * c) <= atol * bnorm * max(abs(rho), 1e-300):
            break
    return x, it


def fit(problem, measurements: Measurements, theta0, *, prior_mean=None, prior_sigma=None, lower=None, upper=None,
        free: Sequence[int] | None = None, max_iterations: int = 30, tolerance: float = 1e-8,
        step_tolerance: float | None = None, max_step: float | Sequence[float] | torch.Tensor | None = None,
        mu0: float = 1e-3, jacobian: str = "dense", lsqr_iterations: int = 50, identify_threshold: float = 1e-2,
        log=None) -> FitResult:
    """Bounded Levenberg-Marquardt Gauss-Newton on

        Phi(theta) = 1/2 sum_j ((h_j(u(theta), theta) - y_j) / sigma_j)^2 + 1/2 sum_i ((theta_i - m_i) / s_i)^2,

    with r(u(theta), theta) = 0 solved by the model at every trial point (warm-started from the last accepted
    state). ``free``: indices of theta that are fitted (default all); the others stay at ``theta0``.
    ``prior_sigma`` entries may be ``inf`` (no prior). ``jacobian``: ``"dense"`` (G by tangent or adjoint solves),
    ``"broyden"`` (exact G at iteration 1, then rank-1 Broyden secant updates along accepted steps), or
    ``"matrix_free"`` (LSQR on the damped normal equations, one tangent and one adjoint solve per inner iteration).
    ``max_step`` caps each trial step while preserving its direction (if scalar float, in units of ``prior_sigma``
    where finite else 1.0; if length p or len(free), directly in parameter units).
    Stops when the relative decrease of Phi falls below ``tolerance``, the scaled step falls below
    ``step_tolerance`` (default ``sqrt(tolerance)``), or the projected gradient vanishes.
    """
    if jacobian not in ("dense", "broyden", "matrix_free"):
        raise ValueError(f"jacobian {jacobian!r} (expected 'dense', 'broyden', or 'matrix_free')")
    t_all = time.perf_counter()
    theta0 = theta0.detach().to(torch.float64)
    p = theta0.numel()
    free = list(range(p)) if free is None else list(free)
    keys = measurements.keys()
    inf = torch.full_like(theta0, math.inf)
    lo = -inf if lower is None else torch.as_tensor(lower, dtype=theta0.dtype, device=theta0.device)
    hi = inf if upper is None else torch.as_tensor(upper, dtype=theta0.dtype, device=theta0.device)
    pm = theta0.clone() if prior_mean is None else torch.as_tensor(prior_mean, dtype=theta0.dtype,
                                                                    device=theta0.device)
    ps = inf if prior_sigma is None else torch.as_tensor(prior_sigma, dtype=theta0.dtype, device=theta0.device)
    pw = torch.where(torch.isfinite(ps), 1.0 / ps.square(), torch.zeros_like(ps))     # prior precision (diagonal)
    F = torch.tensor(free, dtype=torch.long, device=theta0.device)
    counts = {"forward": 0, "linear": 0}

    def forward(theta, u0):
        # a trial parameter set whose forward solve breaks down (a model that raises instead of returning an
        # unconverged certificate: odil-dc's "consecutive pseudo-time steps rejected", Base201 L1 LM trial 2,
        # 2026-11-18) is a rejected trial -- the step is shortened -- not the end of the fit
        try:
            u, cert = problem.solve(theta, u0)
        except RuntimeError as exc:
            if "CUDA" in str(exc) or "out of memory" in str(exc):
                raise
            counts["forward"] += 1
            if log:
                log(f"fit: forward solve raised at trial theta, rejected ({str(exc)[:160]})")
            return None, Certificate(False, float("nan"), 0, "forward", f"raised: {str(exc)[:300]}")
        counts["forward"] += 1
        if not cert.converged:
            return None, cert
        return u, cert

    step_tol = math.sqrt(tolerance) if step_tolerance is None else float(step_tolerance)
    cap_f = None
    if max_step is not None:
        if isinstance(max_step, (int, float)):
            cap_f = float(max_step) * torch.where(torch.isfinite(ps[F]), ps[F], torch.ones_like(ps[F]))
        else:
            ms_t = torch.as_tensor(max_step, dtype=theta0.dtype, device=theta0.device).reshape(-1)
            cap_f = ms_t[F] if ms_t.numel() == p else ms_t
            if cap_f.numel() != len(free):
                raise ValueError(f"max_step has length {ms_t.numel()}, expected {p} or {len(free)}")

    def misfit(u, theta):
        y, s, mask = measurements.flat(theta)
        with torch.no_grad():
            h = _flat_obs(problem, u, theta, keys)
        rho = torch.where(mask, (h - y) / s, torch.zeros_like(h))
        data = 0.5 * float(rho @ rho)
        prior = 0.5 * float((pw * (theta - pm).square()).sum())
        return data + prior, data, rho, s, mask, h

    theta = theta0.clamp(lo, hi)
    u, cert = forward(theta, None)
    if u is None:
        raise RuntimeError(f"fit: the forward solve at theta0 failed ({cert.reason})")
    phi, data, rho, s, mask, h_cur = misfit(u, theta)
    mu = mu0
    history = [{"iteration": 0, "misfit": phi, "data_misfit": data, "mu": mu, "theta": theta.tolist()}]
    converged, reason, it = False, "iteration limit", 0
    Gf = None

    def weighted_G(u, theta):
        Gd, ginfo = observation_jacobian(problem, u, theta, keys, columns=free)
        counts["linear"] += ginfo["linear_solves"]
        return torch.where(mask[:, None], Gd / s[:, None], torch.zeros_like(Gd))

    def _G_cached(u, theta):
        # LPCNS_FIT_G_CACHE=<path>: the weighted G is saved there and reused when a run restarts at the same theta and
        # free set (an L1 window Jacobian costs hours; a crash after it lost one, 2026-11-17)
        path = os.environ.get("LPCNS_FIT_G_CACHE", "")
        if path and os.path.exists(path):
            d = torch.load(path, map_location="cpu")
            th_c = d["theta"].to(theta.device, theta.dtype)
            if (th_c.shape == theta.shape and bool(torch.equal(th_c, theta)) and list(d["free"]) == list(free)
                    and tuple(d["G"].shape) == (int(measurements.flat(theta)[0].numel()), p)):
                if log:
                    log(f"fit: G reused from {path}")
                return d["G"].to(theta.device, theta.dtype)
        G = weighted_G(u, theta)
        if path:
            torch.save({"theta": theta.detach().cpu(), "G": G.detach().cpu(), "free": list(free)}, path)
        return G

    for it in range(1, max_iterations + 1):
        grad_prior = pw * (theta - pm)
        if jacobian == "dense" or (jacobian == "broyden" and Gf is None):
            Gf = _G_cached(u, theta)[:, F]
            g = Gf.T @ rho + grad_prior[F]
        elif jacobian == "broyden":
            g = Gf.T @ rho + grad_prior[F]
        else:
            hu, ht = _obs_vjp(problem, u, theta, keys, torch.where(mask, rho / s, torch.zeros_like(rho)))
            lam, _ = problem.linear_solve(u, theta, hu, transpose=True)
            counts["linear"] += 1
            g = (ht - problem.param_vjp(u, theta, lam))[F] + grad_prior[F]
        # projected gradient: a parameter at a bound whose gradient pushes outward does not move
        at_lo = (theta[F] <= lo[F]) & (g > 0)
        at_hi = (theta[F] >= hi[F]) & (g < 0)
        gproj = torch.where(at_lo | at_hi, torch.zeros_like(g), g)
        if gproj.numel() == 0 or float(gproj.abs().max()) < tolerance * max(1.0, phi):
            converged, reason = True, "projected gradient"
            break
        accepted = False
        # active set: parameters held at a bound by an outward gradient are left out of the step system
        act = torch.nonzero(~(at_lo | at_hi)).reshape(-1)
        Fa = F[act]
        for _ in range(12):
            step = torch.zeros_like(g)
            if jacobian in ("dense", "broyden"):
                Ga = Gf[:, act]
                H = Ga.T @ Ga + torch.diag(pw[Fa])
                D = torch.diag(H.diagonal().clamp_min(1e-12))
                step[act] = torch.linalg.solve(H + mu * D, -g[act])
            else:
                step[act] = _matrix_free_step(problem, u, theta, keys, s, mask, rho, pw, Fa, mu, lsqr_iterations,
                                              counts, grad_prior)
            if cap_f is not None and act.numel():
                ratio = float((step[act].abs() / cap_f[act]).max())
                if ratio > 1.0:
                    step = step / ratio
            cand = theta.clone()
            cand[F] = (theta[F] + step).clamp(lo[F], hi[F])
            if float((cand - theta).abs().max()) == 0.0:
                mu *= 10.0
                continue
            u_c, cert_c = forward(cand, u)
            if u_c is None:
                mu *= 10.0
                continue
            phi_c, data_c, rho_c, _, _, h_c = misfit(u_c, cand)
            if phi_c < phi:
                dphi = (phi - phi_c) / max(phi, 1e-300)
                dstep = float(((cand - theta).abs() / (theta.abs() + 1.0)).max())
                if jacobian == "broyden" and Gf is not None:
                    dx = cand[F] - theta[F]
                    dx2 = float(dx @ dx)
                    if dx2 > 1e-30:
                        dy = torch.where(mask, (h_c - h_cur) / s, torch.zeros_like(h_c))
                        Gf = Gf + torch.outer(dy - Gf @ dx, dx) / dx2
                theta, u, phi, data, rho, h_cur = cand, u_c, phi_c, data_c, rho_c, h_c
                mu = max(mu / 3.0, 1e-12)
                accepted = True
                history.append({"iteration": it, "misfit": phi, "data_misfit": data, "mu": mu,
                                "theta": theta.tolist(), "relative_decrease": dphi, "scaled_step": dstep})
                if log:
                    log(f"fit {it}: misfit {phi:.6e} (data {data:.6e}) mu {mu:.1e} step {dstep:.2e}")
                break
            mu *= 10.0
        if not accepted:
            converged, reason = True, "no step lowers the misfit (local minimum)"
            break
        if history[-1]["scaled_step"] < step_tol:
            converged, reason = True, "step stationary"
            break
        if history[-1]["relative_decrease"] < tolerance:
            converged, reason = True, "misfit stationary"
            break

    cov = sig = None
    unidentified = []
    if jacobian in ("dense", "broyden"):
        if jacobian == "dense" or Gf is None:
            Gf = weighted_G(u, theta)[:, F]
        H = Gf.T @ Gf + torch.diag(pw[F])
        names = [list(problem.param_names)[i] for i in free]
        # identifiability: singular values of G scaled by a unit of each parameter's prior (or 1 without prior),
        # from the eigen-decomposition of the scaled Gauss-Newton matrix (a full basis also when m < p)
        scale = torch.where(torch.isfinite(ps[F]), ps[F], torch.ones_like(ps[F]))
        Gs = Gf * scale[None, :]
        ev, V = torch.linalg.eigh(Gs.T @ Gs)
        sv = ev.clamp_min(0).sqrt()
        smax = float(sv.max()) if sv.numel() else 0.0
        weak = (sv < identify_threshold * max(smax, 1e-300)) | (sv < identify_threshold)
        for k in torch.nonzero(weak).reshape(-1).tolist():
            unidentified.append((float(sv[k]), {n: float(c) for n, c in zip(names, V[:, k]) if abs(float(c)) > 0.1}))
        # posterior covariance (G^T W G + P)^-1 by eigen-decomposition of the Jacobi-scaled matrix D^-1/2 H D^-1/2
        # (independent of the parameters' units: K beside m^3/s made the unscaled test flag well-determined flows,
        # S8 2026-11-13). A parameter with more than 1 % of its scaled weight on a direction the data and the prior
        # do not determine (scaled eigenvalue below the threshold) has an unbounded standard deviation; the others
        # get the covariance of the determined subspace
        dh = H.diagonal().clamp_min(1e-300).sqrt()
        Hs = H / dh[:, None] / dh[None, :]
        eh, Vh_ = torch.linalg.eigh(Hs)
        tiny = eh <= (identify_threshold ** 2) * max(float(eh.max()), 1e-300)
        inv_e = torch.where(tiny, torch.zeros_like(eh), 1.0 / eh.clamp_min(1e-300))
        cov_f = (Vh_ * inv_e[None, :]) @ Vh_.T / dh[:, None] / dh[None, :]
        loads = ((Vh_[:, tiny] ** 2).sum(1) > 1e-2) if bool(tiny.any()) else torch.zeros(len(free), dtype=torch.bool)
        sig_f = cov_f.diagonal().clamp_min(0).sqrt()
        sig_f = torch.where(loads.to(sig_f.device), torch.full_like(sig_f, math.inf), sig_f)
        cov = torch.full((p, p), float("nan"), dtype=theta.dtype, device=theta.device)
        cov[F[:, None], F[None, :]] = cov_f
        sig = torch.full_like(theta, float("nan"))
        sig[F] = sig_f
    return FitResult(theta=theta, state=u, misfit=phi, data_misfit=data, iterations=it, converged=converged,
                     reason=reason, covariance=cov, posterior_sigma=sig, unidentified=unidentified, history=history,
                     forward_solves=counts["forward"], linear_solves=counts["linear"],
                     wall_s=time.perf_counter() - t_all)


def _matrix_free_step(problem, u, theta, keys, s, mask, rho, pw, F, mu, iters, counts, grad_prior):
    """LSQR on [W^1/2 G_F ; sqrt(P_F + mu I)] step = -[rho ; P^1/2 (theta - m)/sqrt(...)] (damped Gauss-Newton)."""
    nf = F.numel()
    m = rho.numel()
    reg = (pw[F] + mu).sqrt()

    def A(v):
        d = torch.zeros_like(theta)
        d[F] = v
        _, dh, _ = tangent(problem, u, theta, d, keys)
        counts["linear"] += 1
        top = torch.where(mask, dh / s, torch.zeros_like(dh))
        return torch.cat((top, reg * v))

    def AT(w):
        top, bot = w[:m], w[m:]
        hu, ht = _obs_vjp(problem, u, theta, keys, torch.where(mask, top / s, torch.zeros_like(top)))
        lam, _ = problem.linear_solve(u, theta, hu, transpose=True)
        counts["linear"] += 1
        return (ht - problem.param_vjp(u, theta, lam))[F] + reg * bot

    # the prior's gradient enters the right-hand side through its own rows: P^1/2 (theta - m) scaled into reg rows
    prior_rhs = torch.where(reg > 0, grad_prior[F] / reg, torch.zeros_like(reg))
    b = -torch.cat((rho, prior_rhs))
    step, _ = _lsqr(A, AT, b, nf, max_iter=iters)
    return step


# ------------------------------------------------------------------------------------------------ dense backend
class DenseProblem:
    """A model whose residual ``residual_fn(u, theta)`` is a torch function of a small state: autograd Jacobians,
    LU solves, and the globalised Newton forward solve.

    Subclasses (or callers) give ``param_names``, ``observe_fn(u, theta) -> dict`` and ``initial_fn(theta) -> u0``.
    The forward solve: damped Newton (Armijo backtracking on ||r||^2, Levenberg-Marquardt fallback when no Newton step
    is accepted); if that fails to reach ``tolerance`` (max-norm), pseudo-transient continuation from the initial
    state ((J + I/tau) du = -r, switched evolution relaxation, tau grows as the residual falls)."""

    def __init__(self, residual_fn, observe_fn, initial_fn, param_names, *, tolerance=1e-10, max_iterations=100):
        self.residual_fn = residual_fn
        self.observe_fn = observe_fn
        self.initial_fn = initial_fn
        self.param_names = tuple(param_names)
        self.tolerance = tolerance
        self.max_iterations = max_iterations
        self._lu_cache = None

    # contract
    def residual(self, u, theta):
        return self.residual_fn(u, theta)

    def observe(self, u, theta):
        return self.observe_fn(u, theta)

    def jacobian(self, u, theta):
        with torch.enable_grad():
            return torch.autograd.functional.jacobian(lambda x: self.residual_fn(x, theta.detach()), u.detach(),
                                                      vectorize=True).detach()

    def param_jacobian(self, u, theta):
        with torch.enable_grad():
            return torch.autograd.functional.jacobian(lambda t: self.residual_fn(u.detach(), t), theta.detach(),
                                                      vectorize=True).detach()

    def jvp(self, u, theta, v):
        return self.jacobian(u, theta) @ v

    def vjp(self, u, theta, w):
        return self.jacobian(u, theta).T @ w

    def param_jvp(self, u, theta, d):
        return self.param_jacobian(u, theta) @ d

    def param_vjp(self, u, theta, w):
        return self.param_jacobian(u, theta).T @ w

    def _lu(self, u, theta):
        key = (u.data_ptr(), theta.data_ptr(), u._version, theta._version, float(u.sum()), float(theta.sum()))
        if self._lu_cache is None or self._lu_cache[0] != key:
            self._lu_cache = (key, torch.linalg.lu_factor(self.jacobian(u, theta)))
        return self._lu_cache[1]

    def linear_solve(self, u, theta, b, *, transpose=False):
        LU, piv = self._lu(u, theta)
        x = torch.linalg.lu_solve(LU, piv, b.reshape(-1, 1), adjoint=transpose).reshape(-1)
        return x, {"method": "lu", "transpose": transpose}

    def solve(self, theta, u0=None):
        t0 = time.perf_counter()
        theta = theta.detach()
        start = self.initial_fn(theta) if u0 is None else u0.detach().clone()
        res = lambda x: self.residual_fn(x, theta)  # noqa: E731
        with torch.enable_grad():
            u, info = _newton(res, start, self.tolerance, self.max_iterations)
            if u is None and u0 is not None:          # a warm start that fails falls back to the model's own start
                u, info = _newton(res, self.initial_fn(theta), self.tolerance, self.max_iterations)
            if u is None:
                u, info = _pseudo_transient(res, self.initial_fn(theta), self.tolerance, 4 * self.max_iterations)
        wall = time.perf_counter() - t0
        if u is None or not torch.isfinite(u).all():
            return start, Certificate(False, math.inf, 0, "none", info.get("reason", "no state"), wall)
        return u.detach(), Certificate(True, info["residual_inf"], info["iterations"], info["method"], "", wall,
                                       {"levenberg_marquardt_steps": info.get("levenberg_marquardt_steps", 0)})


def _jac(res, state):
    return torch.autograd.functional.jacobian(res, state, vectorize=True).detach()


def _newton(res, state, tolerance, max_iterations):
    lm_steps = 0
    for it in range(max_iterations):
        r = res(state).detach()
        norm = float(r.abs().max())
        if norm < tolerance:
            return state, {"method": "newton", "iterations": it, "residual_inf": norm,
                           "levenberg_marquardt_steps": lm_steps}
        f0 = float(r @ r)
        J = _jac(res, state)
        accepted = False
        try:
            step = torch.linalg.solve(J, -r)
            ok = bool(torch.isfinite(step).all())
        except RuntimeError:
            ok = False
        if ok:
            scale = 1.0
            for _ in range(40):
                cand = state + scale * step
                r2 = res(cand).detach()
                f = float(r2 @ r2)
                if math.isfinite(f) and f <= (1.0 - 1e-4 * scale) * f0:
                    state, accepted = cand, True
                    break
                scale *= 0.5
        if not accepted:
            JtJ, g = J.T @ J, J.T @ r
            base = float(JtJ.diagonal().max())
            for lam in (1e-8, 1e-6, 1e-4, 1e-2, 1.0, 1e2):
                step = torch.linalg.solve(JtJ + lam * base * torch.eye(len(state), dtype=J.dtype, device=J.device), -g)
                cand = state + step
                r2 = res(cand).detach()
                f = float(r2 @ r2)
                if math.isfinite(f) and f < f0:
                    state, accepted = cand, True
                    lm_steps += 1
                    break
        if not accepted:
            return None, {"reason": "newton: no step reduces the residual"}
    return None, {"reason": "newton: iteration limit"}


def _pseudo_transient(res, state, tolerance, max_iterations):
    r = res(state).detach()
    norm = float(r.abs().max())
    J = _jac(res, state)
    tau = 1.0 / max(float(J.diagonal().abs().max()), 1e-12)
    eye = torch.eye(len(state), dtype=J.dtype, device=J.device)
    for it in range(max_iterations):
        if norm < tolerance:
            return state, {"method": "pseudo-transient", "iterations": it, "residual_inf": norm,
                           "levenberg_marquardt_steps": 0}
        step = torch.linalg.solve(J + eye / tau, -r)
        cand = state + step
        r2 = res(cand).detach()
        n2 = float(r2.abs().max())
        if not math.isfinite(n2):
            tau *= 0.25
            continue
        tau = min(tau * max(0.1, min(10.0, norm / max(n2, 1e-300))), 1e12)
        state, r, norm = cand, r2, n2
        J = _jac(res, state)
    return None, {"reason": "pseudo-transient: iteration limit"}


# ------------------------------------------------------------------------------------------------ checks
def check_contract(problem, u, theta, *, keys: Sequence[str] = (), seed: int = 20261113, h: float = 1e-6):
    """Dot tests of a model's operators at ``(u, theta)``: <w, J v> = <J^T w, v>, <w, r_theta d> = <r_theta^T w, d>,
    the linear solves' residuals, and (with ``keys``) the tangent observation change against a central difference
    of the forward solve. Returns relative errors."""
    g = torch.Generator(device="cpu").manual_seed(seed)
    rnd = lambda like: torch.randn(like.shape, generator=g, dtype=like.dtype).to(like.device)  # noqa: E731
    r = problem.residual(u, theta)
    v, w, d = rnd(u), rnd(r), rnd(theta)
    a, b = float(w @ problem.jvp(u, theta, v)), float(problem.vjp(u, theta, w) @ v)
    c, e = float(w @ problem.param_jvp(u, theta, d)), float(problem.param_vjp(u, theta, w) @ d)
    x, _ = problem.linear_solve(u, theta, w, transpose=False)
    xt, _ = problem.linear_solve(u, theta, w, transpose=True)
    out = {"transpose": abs(a - b) / max(abs(a), abs(b), 1e-300),
           "param_transpose": abs(c - e) / max(abs(c), abs(e), 1e-300),
           "solve": float((problem.jvp(u, theta, x) - w).norm() / w.norm()),
           "solve_transpose": float((problem.vjp(u, theta, xt) - w).norm() / w.norm())}
    if keys:
        _, dh, _ = tangent(problem, u, theta, d, keys)
        up, _ = problem.solve(theta + h * d, u)
        um, _ = problem.solve(theta - h * d, u)
        with torch.no_grad():
            fd = (_flat_obs(problem, up, theta + h * d, keys) - _flat_obs(problem, um, theta - h * d, keys)) / (2 * h)
        out["tangent_vs_difference"] = float((dh - fd).norm() / max(float(fd.norm()), 1e-300))
    return out


# ------------------------------------------------------------------------------------------------ transient
class TrajectoryProblem:
    """A time window of a model as ONE steady problem (docs/research/2026-11-13-unified-steady-framework.md, S8).

    The state is the trajectory ``U = (u_0, u_1 .. u_K)`` (flattened) and the residual stacks

        R_0 = r(u_0; theta_0)                                        (the steady state the window starts from)
        R_k = mass * (u_k - u_{k-1}) / dt_k + r(u_k; theta_k)       (implicit Euler, k = 1..K)

    with ``theta_k = schedule(k, theta)`` (a differentiable torch function: the fitted parameters and the known inputs
    of step k, e.g. a setpoint step). With a given ``u0`` the window starts from that fixed state instead and
    ``U = (u_1 .. u_K)``. ``mass`` is the diagonal capacity of each state entry (0: an algebraic row, e.g. the
    pressures of a network). ``solve`` marches: the steady block by the model's own solve, each step by its Newton
    on the step residual; the linear solves are block forward (tangent) / backward (adjoint in time) substitution
    with one step-Jacobian solve per block; ``observe`` stacks ``observe(u_k, theta_k)``. So ``gradient``,
    ``tangent``, ``observation_jacobian`` and ``fit`` (the JFGNK fit of sensor series) apply unchanged.

    ``model``: a ``DenseProblem`` (``residual_fn``, ``observe_fn``, ``solve``)."""

    def __init__(self, model, mass, dts, schedule, param_names, *, u0=None, tolerance=1e-10, max_iterations=60):
        self.model = model
        self.mass = torch.as_tensor(mass, dtype=torch.float64)
        self.dts = [float(d) for d in dts]
        self.K = len(self.dts)
        self.schedule = schedule
        self.param_names = tuple(param_names)
        self.u0 = None if u0 is None else u0.detach()
        self.k0 = 1 if self.u0 is not None else 0          # step index of the first block
        self.nb = self.K + 1 - self.k0
        self.tolerance = tolerance
        self.max_iterations = max_iterations

    def _split(self, U):
        return U.reshape(self.nb, -1)

    def _res_k(self, k, uk, uprev, theta):
        th = self.schedule(k, theta)
        if k == 0:
            return self.model.residual_fn(uk, th)
        return self.mass * (uk - uprev) / self.dts[k - 1] + self.model.residual_fn(uk, th)

    def _prev(self, Us, j):
        return self.u0 if j == 0 else Us[j - 1]          # (unused for the steady block)

    def residual(self, U, theta):
        Us = self._split(U)
        return torch.cat([self._res_k(j + self.k0, Us[j], self._prev(Us, j), theta) for j in range(self.nb)])

    def solve(self, theta, u0=None):
        t0 = time.perf_counter()
        theta = theta.detach()
        states, its = [], 0
        if self.k0 == 0:
            u, cert = self.model.solve(self.schedule(0, theta).detach())
            if not cert.converged:
                return torch.zeros(1, dtype=torch.float64), Certificate(False, math.inf, 0, "implicit Euler + Newton",
                                                                         f"initial state: {cert.reason}", 0.0)
            states.append(u.detach())
            prev = u.detach()
        else:
            prev = self.u0
        with torch.enable_grad():
            for k in range(1, self.K + 1):
                res = lambda x, k=k, p=prev: self._res_k(k, x, p, theta)  # noqa: E731
                u, info = _newton(res, prev.clone(), self.tolerance, self.max_iterations)
                if u is None:
                    return torch.zeros(1, dtype=torch.float64), Certificate(
                        False, math.inf, its, "implicit Euler + Newton", f"step {k}: {info['reason']}",
                        time.perf_counter() - t0)
                its += info["iterations"]
                states.append(u.detach())
                prev = u.detach()
        U = torch.cat(states)
        r = float(self.residual(U, theta).abs().max())
        return U, Certificate(True, r, its, "implicit Euler + Newton", "", time.perf_counter() - t0,
                              {"steps": self.K})

    def _blocks(self, U, theta):
        """The diagonal blocks and their LU factors at (U, theta), cached for the last point (a fit asks for many
        products and solves at one iterate)."""
        c = getattr(self, "_block_cache", None)
        if c is not None and c[0].shape == U.shape and torch.equal(c[0], U.detach()) and torch.equal(c[1], theta.detach()):
            return c[2], c[3]
        Us = self._split(U)
        D = [self._D(Us, theta, j) for j in range(self.nb)]
        lu = [torch.linalg.lu_factor(d) for d in D]
        self._block_cache = (U.detach().clone(), theta.detach().clone(), D, lu)
        return D, lu

    def _D(self, Us, theta, j):
        """Diagonal block j: J(u_k) (+ mass/dt_k for a time step)."""
        k = j + self.k0
        th = self.schedule(k, theta).detach()
        with torch.enable_grad():
            J = torch.autograd.functional.jacobian(lambda x: self.model.residual_fn(x, th), Us[j].detach(),
                                                   vectorize=True).detach()
        return J if k == 0 else J + torch.diag(self.mass / self.dts[k - 1])

    def _sub(self, j):
        """Sub-diagonal block coefficient of block j (on u_{j-1}): -mass/dt_k, or None for the first block."""
        return None if j == 0 else -self.mass / self.dts[j + self.k0 - 1]

    def jvp(self, U, theta, V):
        D, _ = self._blocks(U, theta)
        Vs = self._split(V)
        out = []
        for j in range(self.nb):
            y = D[j] @ Vs[j]
            if j > 0:
                y = y + self._sub(j) * Vs[j - 1]
            out.append(y)
        return torch.cat(out)

    def vjp(self, U, theta, W):
        D, _ = self._blocks(U, theta)
        Ws = self._split(W)
        out = []
        for j in range(self.nb):
            y = D[j].T @ Ws[j]
            if j + 1 < self.nb:
                y = y + self._sub(j + 1) * Ws[j + 1]
            out.append(y)
        return torch.cat(out)

    def _param_jac(self, U, theta):
        c = getattr(self, "_param_cache", None)
        if c is not None and c[0].shape == U.shape and torch.equal(c[0], U.detach()) and torch.equal(c[1], theta.detach()):
            return c[2]
        with torch.enable_grad():
            P = torch.autograd.functional.jacobian(lambda t: self.residual(U.detach(), t), theta.detach()).detach()
        self._param_cache = (U.detach().clone(), theta.detach().clone(), P)
        return P

    def param_jvp(self, U, theta, d):
        return self._param_jac(U, theta) @ d

    def param_vjp(self, U, theta, w):
        return self._param_jac(U, theta).T @ w

    def linear_solve(self, U, theta, b, *, transpose=False):
        _, lu = self._blocks(U, theta)
        Bs = self._split(b)
        x = [None] * self.nb
        if not transpose:                        # forward substitution
            for j in range(self.nb):
                rhs = Bs[j] if j == 0 else Bs[j] - self._sub(j) * x[j - 1]
                x[j] = torch.linalg.lu_solve(*lu[j], rhs.unsqueeze(-1)).squeeze(-1)
        else:                                    # backward substitution: the discrete adjoint in time
            for j in range(self.nb - 1, -1, -1):
                rhs = Bs[j] if j == self.nb - 1 else Bs[j] - self._sub(j + 1) * x[j + 1]
                x[j] = torch.linalg.lu_solve(*lu[j], rhs.unsqueeze(-1), adjoint=True).squeeze(-1)
        return torch.cat(x), {"method": "block substitution", "transpose": transpose, "blocks": self.nb}

    def observe(self, U, theta):
        Us = self._split(U)
        obs = [self.model.observe_fn(Us[j], self.schedule(j + self.k0, theta)) for j in range(self.nb)]
        return {key: torch.stack([o[key] for o in obs]) for key in obs[0]}


class TransientStepModel(Protocol):
    """Step-level operator interface consumed by :class:`CheckpointedTrajectoryProblem`.

    ``k == 0`` is the optional steady start block (``dt is None``, ``u_prev is None``); ``k = 1..K`` are implicit-Euler
    steps ``mass(k, theta_k) * (u_k - u_{k-1}) / dt_k + r(u_k; theta_k) = 0``. State tensors returned or consumed here
    live on the step solver's device; :class:`CheckpointedTrajectoryProblem` gathers the stacked trajectory ``U`` on
    ``state_device`` (typically CPU) so GPU RAM stays O(1) across K steps.
    """

    param_names: Sequence[str]

    def solve_step(self, k: int, u_prev: torch.Tensor | None, dt: float | None, theta_k: torch.Tensor,
                   init_u: torch.Tensor | None = None) -> tuple[torch.Tensor, Certificate]: ...
    def mass_vector(self, k: int, theta_k: torch.Tensor | None = None) -> torch.Tensor: ...
    def step_residual(self, k: int, u_k: torch.Tensor, u_prev: torch.Tensor | None, dt: float | None,
                      theta_k: torch.Tensor) -> torch.Tensor: ...
    def step_jvp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, v: torch.Tensor,
                 dt: float | None = None) -> torch.Tensor: ...
    def step_vjp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, w: torch.Tensor,
                 dt: float | None = None) -> torch.Tensor: ...
    def step_param_jvp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, d: torch.Tensor) -> torch.Tensor: ...
    def step_param_vjp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, w: torch.Tensor) -> torch.Tensor: ...
    def step_linear_solve(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, b: torch.Tensor, *,
                          transpose: bool = False, dt: float | None = None) -> tuple[torch.Tensor, dict]: ...
    def step_observe(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor) -> dict[str, torch.Tensor]: ...


class DenseStepModel:
    """Adapts a :class:`DenseProblem` + diagonal ``mass`` (tensor or ``callable(theta_k) -> Tensor``) to :class:`TransientStepModel`."""

    def __init__(self, model: DenseProblem, mass, *, tolerance: float = 1e-10, max_iterations: int = 60):
        self.model = model
        self._mass_spec = mass if callable(mass) else torch.as_tensor(mass, dtype=torch.float64).reshape(-1)
        self.param_names = tuple(model.param_names)
        self.tolerance = float(tolerance)
        self.max_iterations = int(max_iterations)

    def mass_vector(self, k: int, theta_k: torch.Tensor | None = None) -> torch.Tensor:
        if callable(self._mass_spec):
            return self._mass_spec(theta_k).reshape(-1)
        return self._mass_spec

    def solve_step(self, k: int, u_prev: torch.Tensor | None, dt: float | None, theta_k: torch.Tensor,
                   init_u: torch.Tensor | None = None) -> tuple[torch.Tensor, Certificate]:
        th = theta_k.detach()
        if k == 0 or dt is None:
            return self.model.solve(th, init_u)
        t0 = time.perf_counter()
        m_dt = self.mass_vector(k, th).detach() / float(dt)
        up = u_prev.detach()
        u0 = (up if init_u is None else init_u.detach()).clone()
        res = lambda x: m_dt * (x - up) + self.model.residual_fn(x, th)  # noqa: E731
        with torch.enable_grad():
            u, info = _newton(res, u0, self.tolerance, self.max_iterations)
            if u is None and init_u is not None:
                u, info = _newton(res, up.clone(), self.tolerance, self.max_iterations)
            if u is None:
                u, info = _pseudo_transient(res, u0.clone(), self.tolerance, 4 * self.max_iterations)
        wall = time.perf_counter() - t0
        if u is None or not torch.isfinite(u).all():
            return u0, Certificate(False, math.inf, 0, "none", info.get("reason", "no state"), wall)
        return u.detach(), Certificate(True, info["residual_inf"], info["iterations"], info["method"], "", wall,
                                       {"levenberg_marquardt_steps": info.get("levenberg_marquardt_steps", 0)})

    def step_residual(self, k: int, u_k: torch.Tensor, u_prev: torch.Tensor | None, dt: float | None,
                      theta_k: torch.Tensor) -> torch.Tensor:
        r = self.model.residual_fn(u_k, theta_k)
        if k == 0 or dt is None:
            return r
        m = self.mass_vector(k, theta_k).to(u_k.device, u_k.dtype)
        return r + (m / float(dt)) * (u_k - u_prev.to(u_k.device, u_k.dtype))

    def step_jvp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, v: torch.Tensor,
                 dt: float | None = None) -> torch.Tensor:
        y = self.model.jvp(u_k, theta_k, v)
        if k > 0 and dt is not None:
            m = self.mass_vector(k, theta_k).detach().to(v.device, v.dtype)
            y = y + (m / float(dt)) * v
        return y

    def step_vjp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, w: torch.Tensor,
                 dt: float | None = None) -> torch.Tensor:
        y = self.model.vjp(u_k, theta_k, w)
        if k > 0 and dt is not None:
            m = self.mass_vector(k, theta_k).detach().to(w.device, w.dtype)
            y = y + (m / float(dt)) * w
        return y

    def step_param_jvp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, d: torch.Tensor) -> torch.Tensor:
        return self.model.param_jvp(u_k, theta_k, d)

    def step_param_vjp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        return self.model.param_vjp(u_k, theta_k, w)

    def step_linear_solve(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, b: torch.Tensor, *,
                          transpose: bool = False, dt: float | None = None) -> tuple[torch.Tensor, dict]:
        if k == 0 or dt is None:
            return self.model.linear_solve(u_k, theta_k, b, transpose=transpose)
        m_dt = self.mass_vector(k, theta_k).detach().to(u_k.device, u_k.dtype) / float(dt)
        th = theta_k.detach()
        with torch.enable_grad():
            J = torch.autograd.functional.jacobian(lambda x: self.model.residual_fn(x, th), u_k.detach(),
                                                   vectorize=True).detach()
        A = J + torch.diag(m_dt)
        x = torch.linalg.solve(A.T if transpose else A, b)
        return x, {"method": "dense", "transpose": transpose}

    def step_observe(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor) -> dict[str, torch.Tensor]:
        return self.model.observe_fn(u_k, theta_k)


class CheckpointedTrajectoryProblem:
    """O(1)-GPU-memory discrete trajectory wrapper over a :class:`TransientStepModel` implementing :class:`SteadyProblem`.

    Stores the stacked trajectory ``U = [u_{k0}, ..., u_K]`` on ``state_device`` (default ``"cpu"``) and optionally
    writes per-step checkpoints into ``checkpoint_dir``. Evaluates forward solves, block-forward tangent sweeps,
    block-backward discrete adjoint sweeps, and batched multi-RHS tangents (sharing one per-step linear setup across
    all parameter columns) one physical time step at a time.
    """

    def __init__(self, step_model: TransientStepModel, dts: Sequence[float],
                 schedule: Callable[[int, torch.Tensor], torch.Tensor],
                 param_names: Sequence[str] | None = None, *, u0: torch.Tensor | None = None,
                 state_device: str | torch.device = "cpu", checkpoint_dir: str | Path | None = None,
                 max_step_attempts: int = 2):
        self.step_model = step_model
        self.dts = [float(d) for d in dts]
        self.K = len(self.dts)
        self.schedule = schedule
        self.param_names = tuple(step_model.param_names if param_names is None else param_names)
        self.u0 = None if u0 is None else u0.detach().to(torch.device(state_device), dtype=torch.float64).clone()
        self.k0 = 1 if self.u0 is not None else 0
        self.nb = self.K + 1 - self.k0
        self.state_device = torch.device(state_device)
        self.checkpoint_dir = None if checkpoint_dir is None else Path(checkpoint_dir)
        self.max_step_attempts = max(1, int(max_step_attempts))
        self.n_solves = 0
        self._last_U = None
        sig = inspect.signature(step_model.mass_vector)
        self._mass_takes_theta = len(sig.parameters) >= 2

    def _mass(self, k: int, th_k: torch.Tensor | None) -> torch.Tensor:
        return self.step_model.mass_vector(k, th_k) if self._mass_takes_theta else self.step_model.mass_vector(k)

    def _split(self, U: torch.Tensor) -> torch.Tensor:
        return U.reshape(self.nb, -1)

    def _dt_of(self, k: int) -> float | None:
        return None if k == 0 else self.dts[k - 1]

    def _prev_of(self, Us: torch.Tensor, j: int) -> torch.Tensor | None:
        if j == 0:
            return None if self.k0 == 0 else self.u0
        return Us[j - 1]

    def _sched_jvp(self, k: int, theta: torch.Tensor, d: torch.Tensor) -> torch.Tensor:
        with torch.enable_grad():
            tt = theta.detach().clone().requires_grad_(True)
            th_k = self.schedule(k, tt)
            w = torch.zeros_like(th_k, requires_grad=True)
            (gt,) = torch.autograd.grad(th_k, tt, w, create_graph=True, allow_unused=True)
            if gt is None or not gt.requires_grad:
                return torch.zeros_like(th_k).detach()
            (out,) = torch.autograd.grad((gt * d.to(gt.device, gt.dtype)).sum(), w)
        return out.detach()

    def _sched_vjp(self, k: int, theta: torch.Tensor, g_thk: torch.Tensor) -> torch.Tensor:
        with torch.enable_grad():
            tt = theta.detach().clone().requires_grad_(True)
            th_k = self.schedule(k, tt)
            (gt,) = torch.autograd.grad(th_k, tt, g_thk.to(th_k.device, th_k.dtype), allow_unused=True)
        return torch.zeros_like(theta) if gt is None else gt.detach().to(theta.device, theta.dtype)

    def _mass_param_jvp(self, k: int, uk: torch.Tensor, uprev: torch.Tensor, dt: float,
                        th_k: torch.Tensor, d_k: torch.Tensor) -> torch.Tensor:
        if not self._mass_takes_theta:
            return uk.new_zeros(uk.shape)
        with torch.enable_grad():
            tk = th_k.detach().clone().requires_grad_(True)
            term = self._mass(k, tk).to(uk.device, uk.dtype) * (uk.detach() - uprev.detach().to(uk.device, uk.dtype)) / float(dt)
            if not term.requires_grad:
                return uk.new_zeros(uk.shape)
            w = torch.zeros_like(term, requires_grad=True)
            (gt,) = torch.autograd.grad(term, tk, w, create_graph=True, allow_unused=True)
            if gt is None or not gt.requires_grad:
                return uk.new_zeros(uk.shape)
            (out,) = torch.autograd.grad((gt * d_k.to(gt.device, gt.dtype)).sum(), w)
        return out.detach()

    def _mass_param_vjp(self, k: int, uk: torch.Tensor, uprev: torch.Tensor, dt: float,
                        th_k: torch.Tensor, wj: torch.Tensor) -> torch.Tensor:
        if not self._mass_takes_theta:
            return torch.zeros_like(th_k)
        with torch.enable_grad():
            tk = th_k.detach().clone().requires_grad_(True)
            term = self._mass(k, tk).to(wj.device, wj.dtype) * (uk.detach().to(wj.device, wj.dtype) - uprev.detach().to(wj.device, wj.dtype)) / float(dt)
            if not term.requires_grad:
                return torch.zeros_like(th_k)
            (gt,) = torch.autograd.grad(term, tk, wj.detach(), allow_unused=True)
        return torch.zeros_like(th_k) if gt is None else gt.detach()

    def residual(self, U: torch.Tensor, theta: torch.Tensor) -> torch.Tensor:
        Us = self._split(U)
        parts = []
        for j in range(self.nb):
            k = j + self.k0
            th_k = self.schedule(k, theta)
            r_k = self.step_model.step_residual(k, Us[j], self._prev_of(Us, j), self._dt_of(k), th_k)
            parts.append(r_k.to(U.device, U.dtype))
        return torch.cat(parts)

    def solve(self, theta: torch.Tensor, u0: torch.Tensor | None = None) -> tuple[torch.Tensor, Certificate]:
        t0 = time.perf_counter()
        th_base = theta.detach()
        warm_Us = None
        if u0 is not None and u0.numel() > self.nb and u0.numel() % self.nb == 0:
            warm_Us = u0
        elif self._last_U is not None:
            warm_Us = self._last_U
        warm_split = None if warm_Us is None else self._split(warm_Us)
        states, certs = [], []
        prev = None if self.k0 == 0 else self.u0
        ckpt_sub = None
        if self.checkpoint_dir is not None:
            ckpt_sub = self.checkpoint_dir / f"solve_{self.n_solves:04d}"
            ckpt_sub.mkdir(parents=True, exist_ok=True)
        for j in range(self.nb):
            k = j + self.k0
            th_k = self.schedule(k, th_base).detach()
            init_j = None
            if warm_split is not None:
                init_j = warm_split[j]
            elif prev is not None:
                init_j = prev
            try:
                uk, cert = self.step_model.solve_step(k, prev, self._dt_of(k), th_k, init_u=init_j)
            except RuntimeError as exc:
                if "CUDA" in str(exc) or "out of memory" in str(exc) or init_j is None or prev is None \
                        or init_j is prev or self.max_step_attempts <= 1:
                    raise
                # the warm start broke the step down: the retry from the previous step's state below
                uk, cert = None, Certificate(False, float("nan"), 0, "step", f"raised: {str(exc)[:200]}")
            if not cert.converged and init_j is not None and prev is not None and init_j is not prev and self.max_step_attempts > 1:
                uk, cert = self.step_model.solve_step(k, prev, self._dt_of(k), th_k, init_u=prev)
            if not cert.converged:
                self.n_solves += 1
                return torch.zeros(1, dtype=torch.float64, device=self.state_device), Certificate(
                    False, cert.residual_inf, len(certs), cert.method, f"step {k}: {cert.reason}",
                    time.perf_counter() - t0)
            uk_host = uk.detach().to(self.state_device, dtype=torch.float64).reshape(-1)
            states.append(uk_host)
            certs.append(cert)
            prev = uk_host
            if ckpt_sub is not None:
                torch.save(uk_host, ckpt_sub / f"step_{k:04d}.pt")
        U = torch.cat(states)
        self.n_solves += 1
        self._last_U = U
        res_max = max(float(c.residual_inf) for c in certs) if certs else 0.0
        its_sum = sum(int(c.iterations) for c in certs)
        return U, Certificate(True, res_max, its_sum, "checkpointed implicit Euler", "converged",
                              time.perf_counter() - t0)

    def jvp(self, U: torch.Tensor, theta: torch.Tensor, V: torch.Tensor) -> torch.Tensor:
        Us = self._split(U)
        Vs = self._split(V)
        th_d = theta.detach()
        out = []
        for j in range(self.nb):
            k = j + self.k0
            dt = self._dt_of(k)
            th_k = self.schedule(k, th_d).detach()
            y = self.step_model.step_jvp(k, Us[j], th_k, Vs[j], dt=dt).to(U.device, U.dtype)
            if j > 0:
                m_dt = self._mass(k, th_k).detach().to(U.device, U.dtype) / float(dt)
                y = y - m_dt * Vs[j - 1]
            out.append(y)
        return torch.cat(out)

    def vjp(self, U: torch.Tensor, theta: torch.Tensor, W: torch.Tensor) -> torch.Tensor:
        Us = self._split(U)
        Ws = self._split(W)
        th_d = theta.detach()
        out = []
        for j in range(self.nb):
            k = j + self.k0
            th_k = self.schedule(k, th_d).detach()
            y = self.step_model.step_vjp(k, Us[j], th_k, Ws[j], dt=self._dt_of(k)).to(U.device, U.dtype)
            if j + 1 < self.nb:
                k_next = k + 1
                th_next = self.schedule(k_next, th_d).detach()
                m_dt = self._mass(k_next, th_next).detach().to(U.device, U.dtype) / float(self._dt_of(k_next))
                y = y - m_dt * Ws[j + 1]
            out.append(y)
        return torch.cat(out)

    def param_jvp(self, U: torch.Tensor, theta: torch.Tensor, d: torch.Tensor) -> torch.Tensor:
        Us = self._split(U)
        th_d = theta.detach()
        out = []
        for j in range(self.nb):
            k = j + self.k0
            dt = self._dt_of(k)
            th_k = self.schedule(k, th_d).detach()
            d_k = self._sched_jvp(k, th_d, d)
            r_jvp = self.step_model.step_param_jvp(k, Us[j], th_k, d_k).to(U.device, U.dtype)
            if k > 0 and dt is not None:
                prev = self._prev_of(Us, j)
                r_jvp = r_jvp + self._mass_param_jvp(k, Us[j], prev, dt, th_k, d_k).to(U.device, U.dtype)
            out.append(r_jvp)
        return torch.cat(out)

    def param_vjp(self, U: torch.Tensor, theta: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        Us = self._split(U)
        Ws = self._split(w)
        th_d = theta.detach()
        acc = torch.zeros_like(th_d)
        for j in range(self.nb):
            k = j + self.k0
            dt = self._dt_of(k)
            th_k = self.schedule(k, th_d).detach()
            g_thk = self.step_model.step_param_vjp(k, Us[j], th_k, Ws[j])
            if k > 0 and dt is not None:
                prev = self._prev_of(Us, j)
                g_thk = g_thk + self._mass_param_vjp(k, Us[j], prev, dt, th_k, Ws[j]).to(g_thk.device, g_thk.dtype)
            acc = acc + self._sched_vjp(k, th_d, g_thk)
        return acc

    def linear_solve(self, U: torch.Tensor, theta: torch.Tensor, b: torch.Tensor, *,
                     transpose: bool = False) -> tuple[torch.Tensor, dict]:
        Us = self._split(U)
        Bs = self._split(b)
        th_d = theta.detach()
        xs: list[torch.Tensor | None] = [None] * self.nb
        infos = []
        if not transpose:
            for j in range(self.nb):
                k = j + self.k0
                dt = self._dt_of(k)
                th_k = self.schedule(k, th_d).detach()
                rhs = Bs[j]
                if j > 0:
                    m_dt = self._mass(k, th_k).detach().to(rhs.device, rhs.dtype) / float(dt)
                    rhs = rhs + m_dt * xs[j - 1].to(rhs.device, rhs.dtype)
                xj, info = self.step_model.step_linear_solve(k, Us[j], th_k, rhs, transpose=False, dt=dt)
                xs[j] = xj.to(self.state_device, dtype=torch.float64).reshape(-1)
                infos.append(info)
        else:
            for j in range(self.nb - 1, -1, -1):
                k = j + self.k0
                dt = self._dt_of(k)
                th_k = self.schedule(k, th_d).detach()
                rhs = Bs[j]
                if j + 1 < self.nb:
                    k_next = k + 1
                    th_next = self.schedule(k_next, th_d).detach()
                    m_dt = self._mass(k_next, th_next).detach().to(rhs.device, rhs.dtype) / float(self._dt_of(k_next))
                    rhs = rhs + m_dt * xs[j + 1].to(rhs.device, rhs.dtype)
                xj, info = self.step_model.step_linear_solve(k, Us[j], th_k, rhs, transpose=True, dt=dt)
                xs[j] = xj.to(self.state_device, dtype=torch.float64).reshape(-1)
                infos.append(info)
        return torch.cat([x for x in xs if x is not None]), {"method": "checkpointed block substitution",
                                                             "transpose": transpose, "blocks": self.nb,
                                                             "steps": infos}

    def batch_tangents(self, U: torch.Tensor, theta: torch.Tensor, D_cols: torch.Tensor) -> tuple[torch.Tensor, dict]:
        """Solve ``nb`` forward block sweeps across all ``M`` parameter tangent columns in one per-step pass."""
        Us = self._split(U)
        th_d = theta.detach()
        M = int(D_cols.shape[1])
        cell_dim = Us.shape[1]
        X_out = torch.zeros((M, self.nb, cell_dim), dtype=torch.float64, device=self.state_device)
        solves = 0
        for j in range(self.nb):
            k = j + self.k0
            dt = self._dt_of(k)
            th_k = self.schedule(k, th_d).detach()
            rhs_cols = []
            m_dt = None if j == 0 else (self._mass(k, th_k).detach().to(self.state_device, torch.float64) / float(dt))
            prev = self._prev_of(Us, j)
            for idx in range(M):
                d_k = self._sched_jvp(k, th_d, D_cols[:, idx])
                r_k = -self.step_model.step_param_jvp(k, Us[j], th_k, d_k).to(self.state_device, torch.float64)
                if k > 0 and dt is not None:
                    r_k = r_k - self._mass_param_jvp(k, Us[j], prev, dt, th_k, d_k).to(self.state_device, torch.float64)
                if m_dt is not None:
                    r_k = r_k + m_dt * X_out[idx, j - 1]
                rhs_cols.append(r_k)
            if hasattr(self.step_model, "step_linear_solve_many"):
                # the previous step's tangents as the initial guesses (a window of slowly varying steps: the tangent of
                # step j is close to that of step j - 1) -- measured on Base201 L1 it is not (the guess's residual 1.50 of |b|,
                # 2026-11-17), so off by default; LPCNS_TAN_WARM=1 turns it on
                kw_x0 = {}
                if j > 0 and os.environ.get("LPCNS_TAN_WARM", "0") == "1":
                    if "x0_rows" in inspect.signature(self.step_model.step_linear_solve_many).parameters:
                        kw_x0["x0_rows"] = X_out[:, j - 1]
                X_j, info = self.step_model.step_linear_solve_many(k, Us[j], th_k, torch.stack(rhs_cols), dt=dt, **kw_x0)
                X_out[:, j, :] = X_j.to(self.state_device, dtype=torch.float64)
                solves += int(info.get("linear_solves", M))
            else:
                for idx, rhs in enumerate(rhs_cols):
                    xj, _ = self.step_model.step_linear_solve(k, Us[j], th_k, rhs, transpose=False, dt=dt)
                    X_out[idx, j, :] = xj.to(self.state_device, dtype=torch.float64).reshape(-1)
                    solves += 1
        return X_out.reshape(M, self.nb * cell_dim), {"linear_solves": solves}

    def observe(self, U: torch.Tensor, theta: torch.Tensor) -> dict[str, torch.Tensor]:
        Us = self._split(U)
        obs = [self.step_model.step_observe(j + self.k0, Us[j], self.schedule(j + self.k0, theta))
               for j in range(self.nb)]
        return {key: torch.stack([o[key].to(U.device, U.dtype) for o in obs]) for key in obs[0]}
