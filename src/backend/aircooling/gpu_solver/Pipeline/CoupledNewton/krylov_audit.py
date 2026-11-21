"""Readings of one Newton step's Krylov solve against the system it was meant to solve (opt-in instrumentation).

``solve_newton`` calls :func:`audit` after the step's GMRES solve when ``KRYLOV_AUDIT["on"]`` is set (the steady
entry's ``--krylov-audit``); nothing here changes the solve, the step or any later number of the run.  One row per
solve, printed as ``[krylov audit] {json}`` and kept in ``ROWS``:

* the solve's own report: iterations, re-orthogonalisations, the recurrence's last residual and the freshly computed
  residual GMRES ends each cycle on (both relative to the right-hand side);
* the returned vector and the step the Newton loop takes from it (``gauge``: the constant pressure mode removed on a
  case whose pressure level is free): norms per field block, the constant pressure component;
* the true linear residual ``|J d + F| / |F|`` of both, with the Krylov operator, and the slope ``F . J d / |F|^2``
  of the step (-1 for an exact Newton step);
* the directional derivative of the residual along the step by a central difference against ``J d``;
* the preconditioner as an operator: applied twice to one fixed vector (repeatability) and to a sum against the sum
  of its applications (linearity), relative differences;
* each coupled-AMG hierarchy's coarsest level: its size, whether its gauge field is pinned, the LU's smallest and
  largest pivot and their ratio, and the largest entry of the explicit inverse when one was formed.

With ``KRYLOV_AUDIT["levels"]`` also, per level of each hierarchy, the growth of its smoother and of its V-cycle, and on
every smoothed level the prolongation-smoothing weight checked against the spectrum it is meant for
(:func:`prolongation_reading`), and the two-grid cycle with the next level solved exactly (:func:`two_grid_rho`).
"""
from __future__ import annotations

import json
import math

import torch

KRYLOV_AUDIT = {"on": False, "levels": False, "identity": False}   # levels: the per-level contraction too
# (--krylov-audit-levels); identity: each hierarchy against its own rebuild from the matrix it was built from
# (--krylov-audit-identity; the factory keeps that matrix and the build's keywords on the hierarchy, ``identity_args``)
ROWS: list = []


def _rel(a: torch.Tensor, b: torch.Tensor) -> float:
    return float((a - b).norm() / b.norm().clamp_min(1e-300))


def _hierarchies(m) -> list:
    """The coupled-AMG hierarchies inside a preconditioner, the primary first, then a stagnation rescue's built
    rescue and fresh cycles (breadth first, in that order)."""
    out, seen, todo = [], set(), [m]
    while todo:
        p = todo.pop(0)
        if p is None or id(p) in seen:
            continue
        seen.add(id(p))
        if hasattr(p, "values") and hasattr(p, "plan"):
            out.append(p)
        for name in ("primary", "_rescue", "_fresh"):
            q = getattr(p, name, None)
            if q is not None and (hasattr(q, "apply_blocks") or not callable(q)):
                todo.append(q)
    return out


def coarse_reading(amg) -> dict:
    """The coarsest level of one hierarchy: size, gauge pin, LU pivots, the explicit inverse's largest entry."""
    try:
        V = amg.values[-1]
    except (AttributeError, IndexError, TypeError):
        return {}
    lu = getattr(V, "dense_lu", None)
    if lu is None:
        return {}
    piv = lu[0].diagonal().abs()
    out = {"n": int(lu[0].shape[0]), "gauge_field": getattr(amg, "gauge_field", None),
           "pivot_min": float(piv.min()), "pivot_max": float(piv.max()),
           "pivot_ratio": float(piv.min() / piv.max().clamp_min(1e-300))}
    inv = getattr(V, "dense_inv", None)
    if isinstance(inv, torch.Tensor):
        out["inverse_max"] = float(inv.abs().max())
    return out


def audit(krylov_map, rhs: torch.Tensor, result, step: torch.Tensor, residual_fn, x: torch.Tensor, precondition,
          gauge, fields: list | None = None, n_cell: int | None = None, pressure_slice=None, h: float = 1e-7) -> dict:
    """The readings of the module docstring for one solve (``rhs`` = -F)."""
    with torch.enable_grad():          # the Krylov operator may be an autograd Jacobian-vector product
        F = -rhs
        nF = float(F.norm().clamp_min(1e-300))
        raw = result.x
        row = {"iterations": int(result.iterations), "reorthogonalisations": int(result.reorthogonalisations),
               "recurrence": float(result.residuals[-1]) if result.residuals else None,
               "cycle_true": float(result.cycle_residuals[-1]) if result.cycle_residuals else None,
               "converged": bool(result.converged), "cycle_lengths": list(result.cycle_lengths),
               "cycle_true_all": list(result.cycle_residuals), "breakdowns": int(getattr(result, "breakdowns", 0))}
        Jraw, Jstep = krylov_map(raw).detach(), krylov_map(step).detach()
        row["true_raw"] = float((Jraw - rhs).norm()) / nF
        row["true_step"] = float((Jstep - rhs).norm()) / nF
        row["slope_step"] = float(torch.dot(F, Jstep)) / (nF * nF)
        row["norm_raw"], row["norm_step"] = float(raw.norm()), float(step.norm())
        if pressure_slice is not None:
            p = raw[pressure_slice]
            row["pressure_mean_raw"] = float(p.mean())
            row["pressure_rest_raw"] = float((p - p.mean()).norm())
            # the continuity rows' indicator as a candidate left null vector of J: how far the range of J is from
            # orthogonal to it (two random directions), and the share of F along it (the least-squares floor of the
            # solve when it is one)
            ell = torch.zeros_like(F)
            ell[pressure_slice] = 1.0
            ell = ell / ell.norm()
            gen = torch.Generator(device="cpu").manual_seed(7)
            tests = []
            for _ in range(2):
                d = torch.randn(F.numel(), generator=gen, dtype=F.dtype).to(F.device)
                Jd = krylov_map(d).detach()
                tests.append(abs(float(torch.dot(ell, Jd))) / float(Jd.norm().clamp_min(1e-300)))
            row["ind_left_null_test"] = max(tests)
            row["ind_floor"] = abs(float(torch.dot(ell, F))) / nF
        if fields and n_cell:
            off, per = 0, {}
            for name, w in fields:
                part = step[off * n_cell:(off + int(w)) * n_cell]
                per[str(name)] = float(part.norm())
                off += int(w)
            row["step_per_field"] = per
        try:
            s = float(step.norm().clamp_min(1e-300))
            t = h / s
            plus, minus = residual_fn(gauge(x + t * step)).detach(), residual_fn(gauge(x - t * step)).detach()
            fd = (plus - minus) / (2.0 * t)
            row["fd_vs_jd"] = _rel(fd, Jstep)
        except Exception as exc:              # noqa: BLE001 -- a reading, never a failure of the run
            row["fd_vs_jd"] = f"{type(exc).__name__}: {exc}"[:200]
        if precondition is not None:
            g = torch.Generator(device="cpu").manual_seed(12)
            a = torch.randn(rhs.numel(), generator=g, dtype=rhs.dtype).to(rhs.device)
            b = torch.randn(rhs.numel(), generator=g, dtype=rhs.dtype).to(rhs.device)
            za, za2, zb, zab = (precondition(v).detach() for v in (a, a, b, a + b))
            row["m_repeat"] = _rel(za2.reshape(-1), za.reshape(-1))
            row["m_linearity"] = _rel((za + zb).reshape(-1), zab.reshape(-1))
            row["m_on_rhs_norm"] = float(precondition(rhs).detach().norm())
        row["coarse"] = [coarse_reading(m) for m in _hierarchies(precondition)]
        if KRYLOV_AUDIT.get("levels"):
            row["levels"] = [level_contraction(m) for m in _hierarchies(precondition)]
        if KRYLOV_AUDIT.get("identity"):
            row["identity"] = [identity_reading(m) for m in _hierarchies(precondition)]
    ROWS.append(row)
    print("[krylov audit] " + json.dumps(row, default=str), flush=True)
    return row


def _rho(apply, e: torch.Tensor, steps: int) -> float:
    """Growth per application of the error propagation ``apply`` over its last half of ``steps`` power steps."""
    norms = []
    for _ in range(steps):
        e = apply(e)
        norms.append(float(e.norm()))
        if not norms[-1] > 0.0 or norms[-1] != norms[-1]:
            return norms[-1]
        e = e / norms[-1]
    half = norms[len(norms) // 2:]
    out = 1.0
    for v in half:
        out *= v
    return out ** (1.0 / len(half))


@torch.no_grad()
def level_contraction(amg, steps: int = 8, seed: int = 9) -> list:
    """Per level of one coupled-AMG hierarchy (block values kept): the growth per application of its pre-smoother's
    error propagation ``e - S_k(A_k e)`` and of the V-cycle from that level ``e - C_k(A_k e)``, by power steps from
    a fixed random vector; a level whose values are not available reads None."""
    out = []
    for k, V in enumerate(getattr(amg, "values", []) or []):
        A = getattr(V, "A", None)
        if A is None or getattr(A, "val", None) is None:
            out.append({"level": k, "n": None})
            continue
        n, F = A.shape[0], A.F
        g = torch.Generator(device="cpu").manual_seed(seed + k)
        e = torch.randn(n, F, generator=g, dtype=A.val.dtype).to(A.val.device)
        row = {"level": k, "n": int(n)}
        try:
            if getattr(V, "dense_lu", None) is None:
                pre = amg._pre_of(k)
                row["smoother_rho"] = _rho(lambda x: x - amg._smooth(k, A.matvec(x), None, pre), e, steps)
            row["cycle_rho"] = _rho(lambda x: x - amg._cycle(k, A.matvec(x)), e, steps)
            p = prolongation_reading(amg, k)
            if p is not None:
                row.update(p)
            tg = two_grid_rho(amg, k, steps)
            if tg is not None:
                row["twogrid_rho"] = tg
        except Exception as exc:              # noqa: BLE001 -- a reading, never a failure of the run
            row["error"] = f"{type(exc).__name__}: {exc}"[:160]
        out.append(row)
    return out


def ritz_values(apply, n: int, F: int, m: int = 60, seed: int = 11, dtype=torch.float64, device="cpu") -> torch.Tensor:
    """The Ritz values of ``apply`` (an operator on ``[n, F]`` arrays) from an ``m``-step Arnoldi process (classical
    Gram-Schmidt applied twice, a fixed random start); fewer when the Krylov space closes earlier."""
    g = torch.Generator(device="cpu").manual_seed(seed)
    v = torch.randn(n * F, generator=g, dtype=dtype).to(device)
    V = torch.zeros(m + 1, n * F, dtype=dtype, device=device)
    H = torch.zeros(m + 1, m, dtype=dtype, device=device)
    V[0] = v / v.norm()
    k = m
    for j in range(m):
        w = apply(V[j].view(n, F)).reshape(-1)
        for _ in range(2):
            h = V[:j + 1] @ w
            w = w - V[:j + 1].T @ h
            H[:j + 1, j] += h
        hn = float(w.norm())
        H[j + 1, j] = hn
        if not hn > 1e-14 * float(H[:j + 2, j].norm()) or not math.isfinite(hn):
            k = j + 1
            break
        V[j + 1] = w / hn
    return torch.linalg.eigvals(H[:k, :k].cpu())


@torch.no_grad()
def prolongation_reading(amg, k: int, m: int = 60) -> dict | None:
    """Level ``k``'s prolongation-smoothing weight ``omega = 4/3 / rho_est(D^-1 A_k)`` (the plan's power estimate)
    against the spectrum of ``D^-1 A_k`` read by an ``m``-step Arnoldi process: ``rho_code`` (the estimate the weight
    came from), ``rho_repeat`` (that power iteration again on the operator rebuilt here: equal to ``rho_code`` when this
    is the operator the plan used), ``rho_ref`` (the largest Ritz value in modulus), ``over_smoothing`` = rho_ref /
    rho_code, ``p_smoother_max`` = max |1 - omega lambda| over the Ritz values and ``p_smoother_outer`` the same over
    the three of largest modulus (above 1: the smoothing step of the prolongation amplifies the modes it is meant to
    damp; near 1 at the smallest eigenvalues by design); None on a level without a smoothed prolongation."""
    from Pipeline.CoupledNewton.block_amg import _safe_block_inverse, spectral_radius_estimate
    plan = getattr(amg, "plan", None)
    levels = getattr(plan, "levels", None) or []
    V = (getattr(amg, "values", None) or [None])[k] if k < len(getattr(amg, "values", []) or []) else None
    if V is None or k >= len(levels) or getattr(V, "A", None) is None:
        return None
    L, A = levels[k], V.A
    omega = float(getattr(L, "omega", 0.0) or 0.0)
    if not (omega > 0.0 and math.isfinite(omega)):
        return None
    val = A.val
    Dinv = _safe_block_inverse(plan._smoother_diagonal(L, val))
    n, F = A.shape[0], int(val.shape[1])

    def op(v):
        return torch.einsum("nfg,ng->nf", Dinv, A.matvec(v))

    rho_code = (4.0 / 3.0) / omega
    rho_repeat = spectral_radius_estimate(op, n, F, dtype=val.dtype, device=val.device)
    lam = ritz_values(op, n, F, m=m, dtype=val.dtype, device=val.device)
    mod = lam.abs()
    rho_ref = float(mod.max())
    amp = (1.0 - omega * lam).abs()
    i = int(amp.argmax())
    outer = lam[torch.argsort(mod, descending=True)[:3]]
    return {"omega": omega, "rho_code": rho_code, "rho_repeat": rho_repeat, "rho_ref": rho_ref,
            "over_smoothing": rho_ref / rho_code, "p_smoother_max": float(amp[i]),
            "p_smoother_outer": float((1.0 - omega * outer).abs().max()),
            "p_smoother_at": [float(lam[i].real), float(lam[i].imag)],
            "ritz_outer": [[float(z.real), float(z.imag)] for z in outer], "ritz_m": int(lam.numel())}


TWO_GRID_MAX_DENSE = 25000      # largest coarse operator (scalar unknowns) the two-grid reading solves by a dense LU


@torch.no_grad()
def two_grid_rho(amg, k: int, steps: int = 8, seed: int = 13) -> float | None:
    """Growth per application of level ``k``'s two-grid cycle as an error propagation, ``e <- e - T_k(A_k e)``: the
    level's own pre-smoothing, the coarse correction with level ``k + 1`` solved exactly (its dense LU; the
    hierarchy's own when that level is the coarsest), and the post-smoothing, as ``CoupledAMG._cycle`` runs them.
    Below 1 while the V-cycle from ``k`` grows: the growth comes from the recursion below ``k + 1``; above 1: from
    level ``k``'s smoother and its coarse correction.  None on the coarsest level, or when level ``k + 1`` has more
    than ``TWO_GRID_MAX_DENSE`` scalar unknowns."""
    values = getattr(amg, "values", None) or []
    if k + 1 >= len(values) or values[k].dense_lu is not None or values[k].P is None:
        return None
    V, C = values[k], values[k + 1]
    if C.dense_lu is not None:
        lu = C.dense_lu
    else:
        if C.A.shape[0] * C.A.F > TWO_GRID_MAX_DENSE:
            return None
        cache = amg.__dict__.setdefault("_audit_coarse_lu", {})
        if k + 1 not in cache:
            cache[k + 1] = torch.linalg.lu_factor(C.A.to_dense())
        lu = cache[k + 1]

    def cycle(b):
        x = amg._smooth(k, b, None, amg._pre_of(k))
        rc = V.R.matvec(b - V.A.matvec(x))
        ec = torch.linalg.lu_solve(*lu, rc.reshape(-1, 1)).reshape(rc.shape)
        x = x + V.P.matvec(ec)
        return amg._smooth(k, b, x, amg._post_of(k), reverse=True)

    A = V.A
    g = torch.Generator(device="cpu").manual_seed(seed + k)
    e = torch.randn(A.shape[0], A.F, generator=g, dtype=A.val.dtype).to(A.val.device)
    return _rho(lambda x: x - cycle(A.matvec(x)), e, steps)


def _rel_diff(a: torch.Tensor, b: torch.Tensor) -> float:
    a, b = a.detach().double().reshape(-1), b.detach().double().reshape(-1)
    if a.shape != b.shape:
        return float("inf")
    return float((a - b).abs().max() / b.abs().max().clamp_min(1e-300)) if b.numel() else 0.0


def state_checksums(amg) -> dict:
    """Exact sums of every stored value array of a hierarchy, per level: the operator, the smoother inverses, P, R
    (the block values and, on the block kernels, their sliced-ELL copies), so that a later change in place shows."""
    out = {}
    for k, V in enumerate(getattr(amg, "values", []) or []):
        for name, t in (("A", getattr(getattr(V, "A", None), "val", None)), ("Sinv", V.Sinv),
                        ("P", getattr(getattr(V, "P", None), "val", None)),
                        ("R", getattr(getattr(V, "R", None), "val", None))):
            if isinstance(t, torch.Tensor):
                out[f"{k}:{name}"] = float(t.detach().double().sum())
    for k, lv in enumerate(getattr(amg, "_fast", None) or []):
        blk = getattr(lv, "blk", None)
        for name in ("A", "A64", "sinv", "P", "R"):
            t = getattr(blk, name, None)
            t = getattr(t, "values", t)
            if isinstance(t, torch.Tensor):
                out[f"{k}:fast-{name}"] = float(t.detach().double().sum())
    return out


@torch.no_grad()
def identity_reading(amg, seed: int = 21) -> dict:
    """A hierarchy against itself and against its rebuild: the exact checksums of its stored values now against
    those recorded when it was built (a change in place), its action on a fixed vector by the path it runs (a replayed
    graph where one is captured) against its eager fast cycle (a graph reading memory that changed), and that eager
    action against a hierarchy rebuilt from the matrix and the keywords it was built from (``identity_args``: a copy of
    the block values, the plan, the keywords), with the largest relative difference of the rebuilt level values."""
    from Pipeline.CoupledNewton.block_amg import CoupledAMG
    out = {"rebuilt": False}
    record = getattr(amg, "identity_checksums", None)
    if record is not None:
        now = state_checksums(amg)
        out["checksum_changed"] = sorted(k for k in record if now.get(k) != record[k])
    V0 = (getattr(amg, "values", None) or [None])[0]
    if V0 is None or getattr(V0, "A", None) is None:
        return out
    n, F = V0.A.shape[0], V0.A.F
    g = torch.Generator(device="cpu").manual_seed(seed)
    v = torch.randn(n, F, generator=g, dtype=V0.A.val.dtype).to(V0.A.val.device)
    by_path = amg.apply_blocks(v)
    fast = getattr(amg, "fast_kernels", False) and amg._fast_eligible(v)
    eager = amg._fast_apply(v.reshape(-1).contiguous(), "ci").view(n, F) if fast else amg._cycle(0, v)
    out["path_vs_eager"] = _rel_diff(by_path, eager)
    args = getattr(amg, "identity_args", None)
    if args is None:
        return out
    A, plan, kw = args
    rebuilt = CoupledAMG(A, plan=plan, refresh_omega=plan is not None, **kw)
    worst, where = 0.0, None
    for k, (Va, Vb) in enumerate(zip(amg.values, rebuilt.values)):
        for name in ("A", "Sinv", "P", "R"):
            a, b = getattr(Va, name, None), getattr(Vb, name, None)
            a, b = getattr(a, "val", a), getattr(b, "val", b)
            if isinstance(a, torch.Tensor) and isinstance(b, torch.Tensor):
                d = _rel_diff(a, b)
                if d > worst or where is None:
                    worst, where = d, f"{k}:{name}"
    fast_r = getattr(rebuilt, "fast_kernels", False) and rebuilt._fast_eligible(v)
    again = rebuilt._fast_apply(v.reshape(-1).contiguous(), "ci").view(n, F) if fast_r else rebuilt._cycle(0, v)
    out.update(rebuilt=True, levels=len(rebuilt.values) == len(amg.values), values_rel_max=worst, values_worst=where,
               eager_vs_rebuilt=_rel_diff(eager, again))
    del rebuilt
    return out
