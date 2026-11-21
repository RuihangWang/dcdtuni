"""Pseudo-time driver for engineering steady states (data-centre line, DC-RANS P2, 2026-11-06; opt-in).

``solve_pseudo_time(cs, x0, ...)`` marches ``V/dt_i (x^{n+1} - x^n) + F(x^{n+1}) = 0`` with ONE inexact Newton-Krylov
step per pseudo-time step (the ODIL-steady / Fluent pseudo-transient style) and stops on a user rule (OpenFOAM
``residualControl``-like, ``Pipeline.CoupledNewton.stop_rules``) instead of the certified ``max|r| < tol``.  It is a
second driver beside ``CollocatedSolver.solve_newton`` (entry: ``--driver pseudo-time``); nothing in that path
changes.

Why (P2 diagnosis, ``experiments/DataCenter/CoupledNewton/bai-dcrans/README.md``): ``solve_newton``'s PTC scales its
shift by ``|diag J|``.  At a cold start the velocity is zero, the momentum / T diagonal is diffusion only, and
``theta |diag J|`` at theta 0.2 is a convective CFL of several hundred; the first "pseudo-time" steps are full Newton
steps from rest.  Here the local time step is the convective + viscous one,

    V_i / dt_i = max(lambda_i, |J_ii|) / CFL,   lambda_i = sum_f ( max(|u_f . n_f|, U_ref) + nu_eff,f / d_f ) A_f,

``U_ref`` the largest boundary normal speed of the start state (a fluid at rest gets the supply's time scale), zero on
the continuity rows (algebraic: Coffey, Kelley & Keyes 2003), the turbulence rows at ``turb_cfl`` x CFL, plus
``pos_diag * max(0, -J_ii)`` so that a production-dominated k row (negative diagonal of the exact linearisation)
keeps a positive shifted diagonal.

One step: GMRES (right preconditioned) on the exact JVP + diag(V/dt) to ``eta`` in at most ``krylov`` iterations
(``scaling="field"``: GMRES minimises the residual with each field's rows weighted by 1 / ||F_f(x)||, so that every
field is solved to about ``eta`` of its own residual -- in the plain 2-norm the k rows, whose residual is orders below
T's and concentrated in the exhaust cells, are not reduced at all at eta 0.1 (P3 diagnosis); ``"jacobi"``: weighted by
diag(J + V/dt)^-1); preconditioner: coupled block AMG (``camg``) on the
colouring-assembled J (``precond="assembled"``; or the Warp compact frozen / Picard blocks) + diag(V/dt) -- or, with
``pc_kind`` a shape of ``preconditioner.simple_preconditioner`` ("covered", "simple", ...), that block preconditioner
on the same shifted blocks (an order of magnitude cheaper to build and apply; solves the step at CFL up to ~10 only,
P4 diagnosis) --, rebuilt when
the CFL moved by more than ``rebuild_factor`` since its build, every ``rebuild_every`` steps, and once more on a step
whose solve missed ``eta`` on a kept build.  The turbulence unknowns are limited cell by cell to
``[clip, 1 + limit] x`` their old value.  A step is undone and retried at ``cutback`` x CFL when the merit (RMS over
the fields of ||F_f|| / ||F_f(x0)||) of its UNSTEADY residual F(x_new) + V/dt (x_new - x) exceeds ``reject`` x the
steady merit at x, or its steady merit is not finite or grew by more than ``blowup``.  CFL law ``"ramp"``: x ``growth``
after an accepted step whose solve met ``eta`` and whose merit rose by less than ``grow_below``, x ``shrink`` after a
solve that missed ``eta``; ``"ser"``: x clip(merit_old / merit_new, 1 / growth, growth).

``max_linear_rel`` (opt-in, P7): a step whose GMRES ended above this relative residual is rejected like a step
that failed the acceptance test (a near-null step from a failed solve otherwise counts as a "stagnant" iterate for
``stop_rules.QuantityStagnation``; P7-b met its rule that way).

``coarse`` (P8-b): a two-level correction applied multiplicatively after the fine preconditioner,
``z = M r; z += Z E^-1 Z^T (r - A z)`` with ``Z`` the piecewise constants of T on aggregates ("global": one constant;
"boxes:<m>": about m boxes of a geometric partition of the cell centres) and ``E = Z^T A Z`` formed by batched products
at every preconditioner build.  ``anderson`` (P8-c): Anderson mixing of depth m over the accepted states, kept only
when it lowers the steady merit.  ``recycle`` (P8-d): GCRO-DR (``SparseSolver.gcro``) with a recycled space of
``recycle`` directions carried from step to step instead of GMRES.

``forcing="ew"`` (P9-b): the GMRES tolerance of each step by Eisenstat-Walker choice 2 on the merit,
0.9 (m_k / m_{k-1})^2 within [0.05, 0.3], instead of the fixed ``eta``.

``rebuild_growth`` (P10-a): also rebuild the preconditioner when the last solve needed more than this x the
iterations of the first solve after the build (0: off).  ``profile`` (P10-c): synchronised timers of the step's parts,
logged at the end.

``reuse_plan`` (P11-a): a rebuild of camg on the assembled J keeps the previous build's plan and recomputes only
values (``CoupledAMG(plan=..., refresh_omega=True)``; a pattern it does not fit falls back to a full build).
``retry="old"`` (P11-b): a missed solve is first continued with the kept preconditioner and twice the Krylov budget,
and a rejected step keeps its preconditioner.

``handover`` (P13-c): once the merit is below it, the march hands over to Newton-like steps (CFL
``handover_cfl``, GMRES budget ``handover_krylov``); a rejected step there cuts the CFL as before.

``segregate`` (P7-d): each step solves the mean-flow + T block (turbulence rows and columns masked; the coupled
preconditioner applied to the masked vector), then the k-epsilon block at the updated mean flow, before the limiter
and the acceptance test of the combined step.

Returns ``(x, history)``: rows in the entry's format (``it``, ``max_abs``, ``l2``, ``lambda``, ``inner_iterations``,
``inner_rtol``, ``reassembled``, ``stalled``; ``ptc_theta`` = 1/CFL and ``ptc_event`` so the entry's progress line shows
them) plus ``cfl``, ``merit``, ``clipped``, ``rejected``, ``seconds``, ``rel_<field>``.
"""
from __future__ import annotations

import math
import time

import torch

PRECOND_SOURCES = ("assembled", "compact-frozen", "compact-picard")
PRECOND_KINDS = ("camg", "simple", "covered", "restored", "meanflow", "buoyant", "buoyant-sgs")
CFL_LAWS = ("ramp", "ser")
SCALINGS = ("none", "field", "jacobi")
TURBULENCE_FIELDS = ("k", "epsilon", "omega", "nuTilda")


def _rule_summary(rule) -> str:
    """The stop rule's last reading in one short string (quantity change and count, heat closure), for the log."""
    last = getattr(rule, "last", None) or {}
    items = last.values() if all(isinstance(v, dict) for v in last.values()) and last else [last]
    parts = []
    for d in items:
        if "max_change" in d:
            parts.append("dq %.2e x%d" % (d["max_change"], d["count"])
                         + (" span %.2e" % d["span_change"] if "span_change" in d else "")
                         + (" q " + "/".join("%.4f" % v for v in d["q"]) if "q" in d else ""))
        if "relative_error" in d:
            parts.append("heat %+.2f%%" % (100.0 * d["relative_error"]))
    return ", ".join(parts)


def _sync(device):
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def boundary_reference_velocity(cs) -> float:
    """Largest boundary-face normal speed of the last residual evaluation (the supply / fan faces of a data-centre
    start state).  ``face_volumetric_flux`` is the face NORMAL VELOCITY u_f . n (m/s); the volumetric flux is it
    times ``face_area`` (``timestepping.courant_rate``)."""
    app = cs.solver
    un = app.face_volumetric_flux.reshape(-1).abs().to(torch.float64)
    bnd = app.mask_boundary_non_cyclic_face.reshape(-1).bool()
    return float(un[bnd].max()) if bool(bnd.any()) else 0.0


class _Geometry:
    """Face -> interior-row maps and the face geometry of the spectral radius."""

    def __init__(self, cs):
        app = cs.solver
        n = int(cs.n_cell)
        row = torch.full((int(app.cpd_cell_pos.shape[0]),), -1, dtype=torch.long, device=cs.device)
        row[cs.interior] = torch.arange(n, device=cs.device)
        C, F = app.C_senders.long().reshape(-1), app.F_receivers.long().reshape(-1)
        self.o, self.nb = row[C], row[F]
        self.mo, self.mn = self.o >= 0, self.nb >= 0
        self.area = app.face_area.reshape(-1).to(torch.float64)
        self.delta = app.dCF.reshape(-1).to(torch.float64).abs().clamp_min(1e-12)
        self.n = n

    def spectral_radius(self, cs, u_ref: float, floor: float | None = None) -> torch.Tensor:
        """lambda_i from the face flux and nu_eff the last residual evaluation left on the application.  The face
        speed is at least ``floor`` (default ``u_ref``: one step size for the whole domain, set by the fastest
        stream; a smaller floor gives the slow regions their own, larger, local step)."""
        app = cs.solver
        un = app.face_volumetric_flux.reshape(-1).abs().to(torch.float64)     # u_f . n (m/s)
        nu = getattr(app, "_nu_eff_face", None)
        nu = (app.nu_coeff if nu is None else nu).reshape(-1).to(torch.float64)
        if nu.numel() == 1:
            nu = nu.expand_as(un)
        lam_f = (torch.clamp(un, min=u_ref if floor is None else floor) + nu / self.delta) * self.area
        lam = torch.zeros(self.n, dtype=torch.float64, device=un.device)
        lam.index_add_(0, self.o[self.mo], lam_f[self.mo])
        lam.index_add_(0, self.nb[self.mn], lam_f[self.mn])
        return lam


def solve_pseudo_time(cs, x0: torch.Tensor, *, stop_rule=None, maxit: int = 300, tol: float = 0.0,
                      cfl0: float = 5.0, cfl_max: float = 1.0e4, cfl_min: float = 1.0e-2, law: str = "ramp",
                      growth: float = 1.5, grow_below: float = 1.5, shrink: float = 0.7, cutback: float = 0.25,
                      reject: float = 1.0, blowup: float = 100.0, max_rejections: int = 8, u_ref: float | None = None,
                      turb_cfl: float = 1.0, pos_diag: float = 1.0, eta: float = 0.1, krylov: int = 60,
                      scaling: str = "none", precond: str = "assembled", rebuild_factor: float = 4.0,
                      rebuild_every: int = 10, retry_rebuild: bool = True, clip: float = 0.2,
                      limit: float | None = None, camg_kwargs: dict | None = None, pc_kind: str = "camg",
                      pressure: str = "mwi_block", sweeps: int = 2, cycles: int = 1, segregate: bool = False,
                      max_linear_rel: float | None = None, coarse: str = "none", anderson: int = 0,
                      recycle: int = 0, forcing: str = "fixed", rebuild_growth: float = 0.0,
                      profile: bool = False, reuse_plan: bool = False, retry: str = "rebuild",
                      handover: float | None = None, handover_cfl: float = 1.0e6, handover_krylov: int = 400,
                      progress=None, log=None):
    from SparseSolver.gmres import gmres
    from Pipeline.CoupledNewton.block_amg import CoupledAMG, from_field_blocked_csr
    from Pipeline.CoupledNewton.stop_rules import field_slices
    if precond not in PRECOND_SOURCES:
        raise ValueError(f"pseudo-time precond {precond!r}: one of {PRECOND_SOURCES}")
    if pc_kind not in PRECOND_KINDS:
        raise ValueError(f"pseudo-time preconditioner kind {pc_kind!r}: one of {PRECOND_KINDS}")
    if law not in CFL_LAWS:
        raise ValueError(f"pseudo-time CFL law {law!r}: one of {CFL_LAWS}")
    if scaling not in SCALINGS:
        raise ValueError(f"pseudo-time scaling {scaling!r}: one of {SCALINGS}")
    warp = getattr(cs, "_warp", None)
    if precond.startswith("compact") and not hasattr(warp, "compact_jacobian"):
        raise ValueError("pseudo-time precond compact-*: needs the Warp backend (--backend warp); use 'assembled'")
    if getattr(cs, "_ptc_shift", None) is not None and getattr(cs, "_p17_pin", None) is None:
        raise ValueError("pseudo-time driver: a PTC shift is armed on the solver")
    log = log or (lambda *a: None)
    # the assembled matrix is a preconditioner here (the Krylov operator is the exact JVP): global reductions such as
    # the CBBM intake mean are omitted from it rather than refused (as solve_newton does under --operator jvp)
    cs.assembly_is_operator = False
    device = cs.device
    n = int(cs.n_cell)
    nf = int(cs.num_fields)
    slices = field_slices(cs.unknowns, n)
    positive = [slices[name] for name in slices if name in TURBULENCE_FIELDS]
    geom = _Geometry(cs)
    differential = torch.ones(nf * n, dtype=torch.float64, device=device)
    differential[cs.velocity:cs.pressure] = 0.0           # continuity stays algebraic
    turb = torch.zeros(nf * n, dtype=torch.bool, device=device)
    for sl in positive:
        turb[sl] = True
    plan_box = {}
    amg_kw = dict(smoother="gs", restriction="pg", l1=True)
    amg_kw.update(camg_kwargs or {})
    # coarse space of the T unknowns (P8-b): aggregate of every cell, piecewise constants of T
    t_rows = slices.get("T")
    agg = n_agg = None
    if coarse != "none":
        if t_rows is None:
            raise ValueError("pseudo-time coarse space: the case has no T unknown")
        if coarse == "global":
            agg = torch.zeros(n, dtype=torch.long, device=device)
        elif coarse.startswith("boxes:"):
            want = int(coarse.split(":")[1])
            pos = cs.solver.cpd_cell_pos[cs.interior].to(torch.float64)
            lo, hi = pos.min(0).values, pos.max(0).values
            ext = (hi - lo).clamp_min(1e-12)
            per = (want / float(ext.prod())) ** (1.0 / 3.0)
            nb = torch.clamp((ext * per).round().long(), min=1)
            ijk = torch.minimum(((pos - lo) / ext * nb).long(), nb - 1)
            key = (ijk[:, 0] * nb[1] + ijk[:, 1]) * nb[2] + ijk[:, 2]
            _, agg = torch.unique(key, return_inverse=True)
        else:
            raise ValueError(f"pseudo-time coarse {coarse!r}: 'none', 'global' or 'boxes:<count>'")
        n_agg = int(agg.max()) + 1
    coarse_lu = {"lu": None}
    space = {"U": None}

    def zsum(v):
        return torch.zeros(n_agg, dtype=v.dtype, device=v.device).index_add_(0, agg, v[t_rows])

    def zscatter(c):
        out = torch.zeros(nf * n, dtype=c.dtype, device=c.device)
        out[t_rows] = c[agg]
        return out

    def build_coarse(Jx, s):
        """E = Z^T (J + diag s) Z for the piecewise constants Z of T, by batched products."""
        cols = []
        batch = 32
        for j0 in range(0, n_agg, batch):
            js = torch.arange(j0, min(n_agg, j0 + batch), device=device)
            dirs = torch.zeros(js.numel(), nf * n, dtype=torch.float64, device=device)
            hit = agg[None, :] == js[:, None]
            dirs[:, t_rows] = hit.to(torch.float64)
            AZ = (Jx.batched(dirs) if hasattr(Jx, "batched") else torch.stack([Jx(d) for d in dirs])) + s * dirs
            cols.append(torch.stack([zsum(a) for a in AZ], dim=1))
        E = torch.cat(cols, dim=1).cpu()
        coarse_lu["lu"] = torch.linalg.lu_factor(E)

    prof = {}

    def timed(key, fn):
        """``profile`` (P10-c): synchronised wall time of every call of ``fn`` accumulated under ``key``."""
        if not profile:
            return fn

        def wrapped(*a, **k):
            _sync(device)
            t = time.perf_counter()
            out = fn(*a, **k)
            _sync(device)
            prof[key] = prof.get(key, 0.0) + time.perf_counter() - t
            return out
        return wrapped

    def product_at(x):
        if warp is not None:
            P = warp.product(cs, x, "implicit")
        else:
            P = cs.linearise_forward(x, coefficient="implicit")
        if not profile:
            return P
        Pt = timed("jvp", P)
        if hasattr(P, "batched"):
            Pt.batched = P.batched
        return Pt

    def linearisation(x):
        """(signed diag J [nf n], source) of the preconditioner at x."""
        if precond.startswith("compact"):
            cj = warp.compact_jacobian(x, mode=precond.split("-")[1])
            return torch.diagonal(cj.A.val[cj.diag], dim1=1, dim2=2).T.reshape(-1).clone(), cj
        from Pipeline.CoupledNewton.newton import _csr_diagonal_slots
        blocks = cs.assemble(x)
        J = blocks["J"]
        slot = _csr_diagonal_slots(J)
        d = torch.zeros(nf * n, dtype=torch.float64, device=device)
        d[slot >= 0] = J.values()[slot[slot >= 0]]
        return d, (blocks, slot)

    def shift_of(lam, dsig, cfl):
        base = torch.maximum(lam.repeat(nf), dsig.abs())
        c = torch.where(turb, cfl * turb_cfl, torch.full_like(base, cfl))
        return (base / c + pos_diag * torch.clamp(-dsig, min=0.0)) * differential

    def build(src, s):
        if pc_kind != "camg":
            # the SIMPLE-family block preconditioners of preconditioner.py on the shifted blocks
            if precond.startswith("compact"):
                src.add_diagonal(s)
                blocks = src.blocks()
            else:
                from Pipeline.CoupledNewton.newton import _reshift
                blocks = _reshift(cs, src[0], s)
                if blocks is None:
                    raise RuntimeError("pseudo-time: a shifted row has no stored diagonal in the assembled J")
            return cs.simple_preconditioner(blocks, sweeps=sweeps, cycles=cycles, pressure=pressure, shape=pc_kind)
        if precond.startswith("compact"):
            src.add_diagonal(s)
            return CoupledAMG(src.A, **amg_kw)
        J, slot = src[0]["J"], src[1]
        vals = J.values().clone()
        keep = slot >= 0
        vals[slot[keep]] += s[keep]
        A = from_field_blocked_csr(J.crow_indices(), J.col_indices(), vals, n, nf)
        old = plan_box.get("plan")
        if reuse_plan and old is not None:
            # P11-a: numeric-only rebuild on the previous build's plan (aggregates, transfer patterns), omega refreshed;
            # a pattern the plan does not fit falls back to a full build
            try:
                amg = CoupledAMG(A, plan=old, refresh_omega=True, **amg_kw)
                plan_box["reused"] = plan_box.get("reused", 0) + 1
                return amg
            except Exception as exc:                                  # noqa: BLE001
                plan_box["fallbacks"] = plan_box.get("fallbacks", 0) + 1
                plan_box["last_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        amg = CoupledAMG(A, **amg_kw)
        plan_box["plan"] = getattr(amg, "plan", None)
        return amg

    def krylov_solve(Jx, s, dsig, b, M, mask=None):
        """GMRES on (J + diag s) d = b; ``mask`` (segregated step): the block of the masked rows and columns only,
        the preconditioner applied to the masked vector and restricted to it."""
        if mask is not None:
            b = b * mask
            Mfull = M

            def operator(v):
                v = v * mask
                return (Jx(v) + s * v) * mask

            def M(v, Mfull=Mfull):
                return Mfull(v * mask) * mask
        else:
            def operator(v):
                return Jx(v) + s * v
        if coarse_lu["lu"] is not None and mask is None:
            # two-level, multiplicative after the fine preconditioner: z = M r, z += Z E^-1 Z^T (r - A z)
            Mfine = M
            LU, piv = coarse_lu["lu"]

            def M(v, Mfine=Mfine):
                z = Mfine(v)
                c = torch.linalg.lu_solve(LU, piv, zsum(v - operator(z)).cpu()[:, None])[:, 0]
                return z + zscatter(c.to(v.device))

        def run_krylov(Aop, rhs, Mop):
            Mop = timed("precond_apply", Mop)
            if recycle > 0:
                from SparseSolver.gcro import gcro_dr
                res = gcro_dr(Aop, rhs, M=Mop, U=space["U"], k=recycle, restart=krylov, max_iterations=krylov,
                              rtol=eta_step["eta"])
                space["U"] = res.space
                return res
            return gmres(Aop, rhs, M=Mop, restart=krylov, max_cycles=1, rtol=eta_step["eta"])
        if scaling == "none":
            return run_krylov(operator, b, M)
        if scaling == "field":
            D = torch.ones_like(b)
            for sl in slices.values():
                norm = float(b[sl].norm())
                if norm > 0.0:
                    D[sl] = 1.0 / norm
        else:
            D = 1.0 / (dsig + s).abs().clamp_min(1e-300)
        return run_krylov(lambda v: D * operator(v), D * b, lambda v: M(v / D))

    def gmres_more(Jx, s, dsig, b, M, x_first):
        """Continue from ``x_first`` with the kept preconditioner and twice the budget: solve for the correction."""
        r0 = b - (Jx(x_first) + s * x_first)
        D = torch.ones_like(b)
        if scaling == "field":
            for sl in slices.values():
                norm = float(b[sl].norm())
                if norm > 0.0:
                    D[sl] = 1.0 / norm
        res = gmres(lambda v: D * (Jx(v) + s * v), D * r0, M=lambda v: M(v / D), restart=2 * krylov, max_cycles=1,
                    rtol=eta_step["eta"] * float((D * b).norm()) / max(float((D * r0).norm()), 1e-300))
        from types import SimpleNamespace
        scale = float((D * r0).norm()) / max(float((D * b).norm()), 1e-300)
        return SimpleNamespace(x=x_first + res.x, residuals=tuple(v * scale for v in res.residuals))

    x = x0.detach().clone().to(torch.float64)
    t_start = time.perf_counter()
    R = cs.residual(x).detach()
    if u_ref is None:
        u_ref = boundary_reference_velocity(cs)
    R0 = {f: max(float(R[sl].norm()), 1e-300) for f, sl in slices.items()}

    names = list(slices)

    def relative(r):
        # one host sync for all fields (P11-c)
        norms = torch.stack([r[slices[f]].norm() for f in names]).tolist()
        return {f: v / R0[f] for f, v in zip(names, norms)}

    def merit(r):
        rel = relative(r)
        return math.sqrt(sum(v * v for v in rel.values()) / len(rel))

    m_old = merit(R)
    history = [{"it": 0, "max_abs": float(R.abs().max()), "l2": float(R.norm()), "lambda": float("nan"),
                "inner_iterations": 0, "inner_rtol": 0.0, "reassembled": False, "stalled": False,
                "cfl": float(cfl0), "merit": m_old, "clipped": 0, "rejected": 0, "seconds": 0.0}]
    log("pseudo-time driver: U_ref %.4g m/s, CFL %g (%s x%g, max %g), turbulence CFL x%g, positive diagonal %g, "
        "GMRES %d to %g (%s scaling), %s on %s (rebuilt at CFL x%g, every %d steps%s), turbulence limited to "
        "[%g, %s] x old, reject at unsteady merit x%g or steady merit x%g"
        % (u_ref, cfl0, law, growth, cfl_max, turb_cfl, pos_diag, krylov, eta, scaling, pc_kind, precond,
           rebuild_factor,
           rebuild_every, ", again on a missed solve" if retry_rebuild else "", clip,
           "inf" if limit is None else "%g" % (1.0 + limit), reject, blowup))
    cfl = float(cfl0)
    M, build_cfl, build_it, dsig = None, None, 0, None
    aa_x, aa_g = [], []
    build_its = last_its = None
    handed = False
    eta_step = {"eta": eta}
    rejections = total_krylov = builds = it = 0
    stopped = False
    while it < maxit:
        if history[-1]["max_abs"] < tol:
            break
        if stop_rule is not None and timed("stop_rule", stop_rule)(x, R, it):
            history[-1]["stop_rule"] = stopped = True
            break
        rule_state = _rule_summary(stop_rule) if stop_rule is not None else ""
        it += 1
        _sync(device)
        t0 = time.perf_counter()
        timed("residual_top", cs.residual)(x)    # the face flux / nu_eff of x on the application (R unchanged)
        lam = timed("spectral_radius", geom.spectral_radius)(cs, u_ref)
        rebuilt = False
        grown = (rebuild_growth > 0.0 and build_its is not None and last_its is not None
                 and last_its > rebuild_growth * build_its)
        if (M is None or max(cfl / build_cfl, build_cfl / cfl) > rebuild_factor or it - build_it >= rebuild_every
                or grown):
            M = None
            dsig, src = timed("assembly", linearisation)(x)
            M = timed("precond_build", build)(src, shift_of(lam, dsig, cfl))
            build_its = None
            del src
            build_cfl, build_it, rebuilt = cfl, it, True
            builds += 1
        s = shift_of(lam, dsig, cfl)
        Jx = product_at(x)
        if agg is not None and rebuilt:
            build_coarse(Jx, s)
        mean_mask = (~turb).to(torch.float64) if segregate else None
        if forcing == "ew" and len(history) >= 2 and not history[-1].get("rejected"):
            # Eisenstat-Walker choice 2 on the merit (P9-b): eta = 0.9 (m_k / m_{k-1})^2 within [0.05, 0.3]
            ratio_m = history[-1]["merit"] / max(history[-2]["merit"], 1e-300)
            eta_step["eta"] = min(0.3, max(0.05, 0.9 * ratio_m ** 2))
        result = timed("krylov_total", krylov_solve)(Jx, s, dsig, -R, M, mask=mean_mask)
        its = len(result.residuals)
        rel_lin = result.residuals[-1] if result.residuals else 1.0
        if rel_lin > eta_step["eta"] * 1.0001 and not rebuilt and retry_rebuild and retry == "old":
            # P11-b: first the kept preconditioner with twice the Krylov budget (from the first attempt's answer)
            first = result
            result = timed("krylov_retry", gmres_more)(Jx, s, dsig, -R, M, first.x)
            its += len(result.residuals)
            rel_lin = result.residuals[-1] if result.residuals else 1.0
        if rel_lin > eta_step["eta"] * 1.0001 and not rebuilt and retry_rebuild:
            M = None
            dsig, src = linearisation(x)
            s = shift_of(lam, dsig, cfl)
            M = timed("precond_build", build)(src, s)
            del src
            build_its = None
            build_cfl, build_it, rebuilt = cfl, it, True
            builds += 1
            if agg is not None:
                build_coarse(Jx, s)
            result = timed("krylov_retry", krylov_solve)(Jx, s, dsig, -R, M, mask=mean_mask)
            its += len(result.residuals)
            rel_lin = result.residuals[-1] if result.residuals else 1.0
        if segregate:
            # segregated turbulence update (P7-d): the solve above was the mean-flow + T block (turbulence columns
            # and rows masked out); now the k-epsilon block at the updated mean flow, then both are one step
            mid = x + result.x
            for sl in positive:
                mid[sl] = x[sl]
            Rm = cs.residual(mid).detach()
            Jm = product_at(mid)
            tres = krylov_solve(Jm, s, dsig, -Rm, M, mask=turb.to(torch.float64))
            its += len(tres.residuals)
            rel_lin = max(rel_lin, tres.residuals[-1] if tres.residuals else 1.0)
            result.x.copy_(mid - x + tres.x)
        total_krylov += its
        last_its = its
        if build_its is None:
            build_its = max(its, 1)
        candidate = x + result.x
        clipped = 0
        for sl in positive:
            old = x[sl]
            lo = clip * old
            clipped += int((candidate[sl] < lo).sum())
            candidate[sl] = torch.maximum(candidate[sl], lo)
            if limit is not None:
                hi = (1.0 + limit) * old
                clipped += int((candidate[sl] > hi).sum())
                candidate[sl] = torch.minimum(candidate[sl], hi)
        Rn = timed("residual", cs.residual)(candidate).detach()
        m_new = merit(Rn)
        # acceptance reads the UNSTEADY residual F(x_new) + V/dt (x_new - x) of the step it solved (solve_newton's PTC
        # line search does the same, Yildirim 2019 eq. 16): a backward-Euler step legitimately raises the STEADY
        # residual (the pressure adjusts to continuity at once, the momentum rows then carry grad p until the flow
        # accelerates), so the steady merit only guards against a blow-up
        m_unsteady = merit(Rn + s * (candidate - x))
        _sync(device)
        seconds = time.perf_counter() - t0
        row = {"it": it, "inner_iterations": its, "inner_rtol": eta, "inner_rel": rel_lin, "reassembled": rebuilt,
               "cfl": cfl, "ptc_theta": 1.0 / cfl, "clipped": clipped, "seconds": seconds,
               "merit_unsteady": m_unsteady, "stop_rule_state": rule_state}
        # ``max_linear_rel`` (opt-in, P7): a step whose linear solve left more than this of its residual is not
        # taken -- it barely moves x, and accepting it lets a quantity-stagnation rule count a stalled march
        null_step = max_linear_rel is not None and rel_lin > max_linear_rel
        if not math.isfinite(m_new) or m_unsteady > reject * m_old or m_new > blowup * m_old or null_step:
            rejections += 1
            row.update(max_abs=history[-1]["max_abs"], l2=history[-1]["l2"], **{"lambda": 0.0}, stalled=True,
                       merit=m_old, rejected=1, ptc_event="rejected")
            history.append(row)
            log("pseudo-time %3d REJECTED at CFL %.3g: merit %.3e -> %.3e (unsteady %.3e; %s; GMRES %d to %.1e, "
                "%d limited); CFL x%g" % (it, cfl, m_old, m_new, m_unsteady,
                                          " ".join(f"{f} {v:.1e}" for f, v in relative(Rn).items()), its, rel_lin,
                                          clipped, cutback))
            if progress is not None:
                progress(row)
            if rejections > max_rejections:
                log("pseudo-time: %d consecutive rejections, stopped" % rejections)
                break
            cfl = max(cfl_min, cfl * cutback)
            if retry != "old":
                M = None            # P11-b keeps the preconditioner across a rejection (the CFL rule may still rebuild)
            continue
        rejections = 0
        ratio = m_old / max(m_new, 1e-300)
        x_prev = x
        x, R, m_old = candidate, Rn, m_new
        if anderson > 0:
            # Anderson acceleration (P8-c, type II, depth ``anderson``) on the accepted pseudo-time map x -> G(x):
            # the mixed state replaces G(x) only when its steady merit is lower; otherwise the history restarts
            aa_x.append(x_prev)
            aa_g.append(candidate)
            del aa_x[:-(anderson + 1)], aa_g[:-(anderson + 1)]
            if len(aa_g) >= 2:
                f = [g - xx for g, xx in zip(aa_g, aa_x)]
                w = torch.ones_like(f[-1])
                for sl in slices.values():
                    w[sl] = 1.0 / max(float(f[-1][sl].norm()), 1e-300)
                dF = torch.stack([(f[i + 1] - f[i]) * w for i in range(len(f) - 1)], dim=1)
                dG = torch.stack([aa_g[i + 1] - aa_g[i] for i in range(len(f) - 1)], dim=1)
                gram = dF.T @ dF
                gram = gram + 1e-10 * torch.trace(gram) / gram.shape[0] * torch.eye(gram.shape[0], dtype=gram.dtype,
                                                                                       device=gram.device)
                gamma = torch.linalg.solve(gram, dF.T @ (f[-1] * w))
                xa = candidate - dG @ gamma
                for sl in positive:
                    xa[sl] = torch.maximum(xa[sl], clip * x_prev[sl])
                Ra = cs.residual(xa).detach()
                ma = merit(Ra)
                if math.isfinite(ma) and ma < m_new:
                    x, R, m_old = xa, Ra, ma
                    aa_g[-1] = xa
                    row["anderson"] = 1
                else:
                    del aa_x[:-1], aa_g[:-1]
                    row["anderson"] = 0
            m_new = m_old
        rel = relative(R)
        row.update(max_abs=float(R.abs().max()), l2=float(R.norm()), **{"lambda": 1.0}, stalled=False, merit=m_new,
                   rejected=0, **{f"rel_{f}": v for f, v in rel.items()})
        history.append(row)
        if progress is not None:
            progress(row)
        log("pseudo-time %3d CFL %8.3g merit %.3e  %s  GMRES %d to %.1e%s, %d limited, %.2f s%s"
            % (it, cfl, m_new, " ".join(f"{f} {v:.1e}" for f, v in rel.items()), its, rel_lin,
               " (pc rebuilt)" if rebuilt else "", clipped, seconds,
               "  [rule at the previous iterate: %s]" % rule_state if rule_state else ""))
        if handover is not None and not handed and m_new < handover:
            # P13-c: the Newton tail -- CFL to ``handover_cfl``, the Krylov budget to ``handover_krylov``; the steady
            # merit then has to fall (the unsteady test is the steady one up to the tiny shift)
            handed = True
            cfl, cfl_max, krylov, reject = handover_cfl, max(cfl_max, handover_cfl), handover_krylov, 1.0
            rebuild_factor = max(rebuild_factor, 1e30)
            M = None
            log("pseudo-time %3d: merit %.3e below %.1e, Newton tail: CFL %g, GMRES %d" % (it, m_new, handover,
                                                                                        cfl, krylov))
            continue
        if law == "ser":
            cfl = cfl * min(max(ratio, 1.0 / growth), growth)
        elif rel_lin > eta_step["eta"] * 1.0001:
            cfl = cfl * shrink
        elif ratio >= 1.0 / grow_below:
            cfl = cfl * growth
        cfl = min(cfl_max, max(cfl_min, cfl))
    _sync(device)
    wall = time.perf_counter() - t_start
    if plan_box:
        log("pseudo-time camg plan reuse: %s" % {k: v for k, v in plan_box.items() if k != "plan"})
    if profile:
        log("pseudo-time profile (s): " + ", ".join("%s %.1f" % kv for kv in sorted(prof.items(), key=lambda kv: -kv[1]))
            + "; total %.1f" % (time.perf_counter() - t_start))
    log("pseudo-time driver: %d steps, %d GMRES iterations, %d preconditioner builds, %.1f s, final merit %.3e, "
        "max|r| %.3e, stop rule %s" % (it, total_krylov, builds, wall, m_old, history[-1]["max_abs"],
                                      "MET" if stopped else "not met"))
    history[-1]["pt_summary"] = {"steps": it, "krylov": total_krylov, "builds": builds, "seconds": wall,
                                 "stop_rule": stopped}
    return x, history
