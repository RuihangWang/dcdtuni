"""Adjoint (implicit-function) gradients of data-centre engineering quantities w.r.t. ACU air-side controls.

DC-RANS S0-D / S0-E (docs/research/2026-11-06-dc-rans-s0.md).  For a weighted objective of the dctwin quantities
``J(x, theta) = sum_q w_q q(x, theta)`` at a converged steady state ``r(x*, theta) = 0``::

    J_x^T lambda = dJ/dx                       (ONE transposed solve, whatever the number of controls)
    dJ/dtheta    = dJ/dtheta|_explicit - lambda^T dr/dtheta

    J, dJ, info = dc_gradient(case_dir, state, {"return_T:dcd_irc_01": 1.0},
                              [("T_supply", "dcd_irc_01"), ("flow_fraction", "dcd_irc_01")])

Controls (``params``: ``(kind, unit)`` pairs; ``unit`` is a dctwin ACU name, or a supply patch name on a case without
``dctwin_map.json``):

* ``T_supply`` [K]: an offset added to the supply patch's fixedValue T (base 0, so the derivative is per kelvin);
* ``flow_fraction`` [-]: multiplier f of the unit's base flow (base 1): supply ``flowRateInletVelocity``
  volumetric (or mass) flow rate, or a fixedValue supply velocity, times f; the pinned return's
  ``flowRateOutletVelocity`` flow rate times f; the supply fixedValue k times f^2 and epsilon times f^3 (an
  intensity / mixing-length inlet at fixed intensity and length scale: k ~ U^2, epsilon ~ k^1.5).  The derivative
  per m^3/s of supply flow is ``dJ/df / Q0``.

They enter the TORCH residual as autograd tensors through the BC objects' value specs (``Functionbase`` value_type
``"tensor"``), so ``dr/dtheta`` is exact.  The transposed operator is the vector-Jacobian product of the torch
residual taped once at ``x*`` (the exact ``J^T``: implicit ``d_f``, the three-hop reach, the global reductions of
pinned flow-rate patches and server / rack outlets), checked against the Warp backend's exact forward JVP by a dot
test (``check_transpose``; 4.5e-19 on bai-dcrans).  Linear solvers (``solve_adjoint``): GMRES with one camg cycle
of the transposed assembled (or compact) Jacobian, optionally shifted; flexible GMRES whose preconditioner is an
inner GMRES on the shifted transposed assembled matrix (``shift_cfl``, the primal driver's ``lambda_i / CFL``),
optionally inside an exact-operator two-level coarse correction (``CoarseCorrection``); and the dual-time march
``lambda += (J + S)^{-T} (dJ/dx - J^T lambda)``.

STATUS (2026-11-07, DC-RANS S0-D; dctwin-hf/README.md): at the bai-dcrans plateau state none of these reaches the
adjoint -- one camg cycle AMPLIFIES the residual of the unshifted J and J^T (x17, x5.5), and every shift-
preconditioned Krylov variant stalls at 0.975-0.9999 of the right-hand side within 100-2000 iterations, the residual
locked on the objective's cells; the forward (tangent) system behaves the same.  ``dc_gradient`` therefore raises
unless ``accept_unconverged=True`` (characterisation only).  The gradient error tracks the achieved adjoint residual,
which ``info`` reports (recomputed with the exact operator, not the recurrence's estimate).
"""
from __future__ import annotations

import contextlib
import json
import os
import time

import torch

CONTROL_KINDS = ("T_supply", "flow_fraction")


# ------------------------------------------------------------------------------------------------ case and residual

def _empty_cuda_cache() -> None:
    """torch.cuda.empty_cache(), skipped while the caching allocator holds a CUDA-graph capture.  The coupled AMG
    captures its V-cycle on a side stream; with a capture registered, empty_cache asserts captures_underway.empty()
    (Base201 L1 tangent solves, 2026-11-17: _free_warp_scratch and fgmres).  Releasing the cache only returns memory
    to the driver, so skipping it changes no result."""
    if torch.cuda.is_current_stream_capturing():
        return
    try:
        torch.cuda.empty_cache()
    except RuntimeError as exc:
        if "captures_underway" not in str(exc):
            raise

def install_capture_safe_empty_cache() -> None:
    """Route every torch.cuda.empty_cache() of this process through :func:`_empty_cuda_cache` (idempotent).  The guard
    covered the tangent call sites only; after a 6 h Jacobian the next forward step asserted in odil_inner's own
    empty_cache (Base201 L1 31-sample run, 2026-11-17 22:07) -- some 40 call sites in AMG, cuDSS, assembly and the
    pseudo-time loop can meet a registered capture, so the guard is installed process-wide."""
    if getattr(torch.cuda.empty_cache, "_lpcns_capture_safe", False):
        return
    raw = torch.cuda.empty_cache

    def _safe() -> None:
        if torch.cuda.is_available() and torch.cuda.is_current_stream_capturing():
            return
        try:
            raw()
        except RuntimeError as exc:
            if "captures_underway" not in str(exc):
                raise
    _safe._lpcns_capture_safe = True
    torch.cuda.empty_cache = _safe


def build_solver(case_dir, device, *, backend="warp", mwi=True, mwi_d_scale=None, conservative_cyclic_flux=True):
    """The steady entry's load recipe (``Parallel.part.load_collocated``) with the MWI flux on and, by default, the
    Warp backend attached (its exact JVP and compact blocks; the residual of record is the same function)."""
    from Parallel.part import load_collocated
    state_dir = os.path.join(str(case_dir), "..", ".adjoint_state")
    case, cs = load_collocated(case_dir, device, momentum_interpolation=mwi, state_dir=state_dir)
    if mwi_d_scale is not None:            # opt-in relaxed d_f (entry --mwi-d-scale); before the Warp build
        cs.solver._mwi_d_scale = float(mwi_d_scale)
    if conservative_cyclic_flux:           # as the hall recipe solves (entries/dc_foam_solve.py RECIPE_ARGS)
        cs.solver._conservative_cyclic_flux = True
    if backend == "warp":
        from FVsolver.applications.buoyantBoussinesqpicoext_kepsilon_warp import attach_warp_dc
        attach_warp_dc(cs)
    if getattr(case, "pool", None) is not None and getattr(case.pool, "phi_cpd_cell_pool", None) is not None:
        from entries.solve_with_coupled_newton import internal_start
        cs._internal_start = internal_start(cs, case).detach().clone()
    import numpy as _np
    try:
        import tomllib as _tomllib
    except ModuleNotFoundError:  # Python 3.10 (the dcdtuni environment)
        import tomli as _tomllib
    _coupled_ptypes = ("cyclic", "cyclicAMI", "cyclicSlip", "empty", "wedge", "symmetry", "symmetryPlane", "processor", "processorCyclic")
    _pinfo_path = os.path.join(str(case_dir), "patchinfo.toml")
    patches_j = (_tomllib.load(open(_pinfo_path, "rb")).get("patch", {}) if os.path.isfile(_pinfo_path)
                 else (case.meta_data or {}).get("patch_info", {}))
    bf_arr = _np.concatenate([
        _np.arange(int(p["start_face"]), int(p["start_face"]) + int(p["n_faces"]), dtype=_np.int64)
        for p in patches_j.values()
        if int(p.get("n_faces", 0)) > 0 and p.get("geom_type", p.get("patch_type", "patch")) not in _coupled_ptypes
    ] or [_np.zeros(0, dtype=_np.int64)])
    if hasattr(cs.solver, "face_area") and hasattr(cs.solver, "neighbor_cpd_cell"):
        cs._phys_meta = {
            "bf": torch.as_tensor(bf_arr, device=cs.device, dtype=torch.long),
            "area": cs.solver.face_area.reshape(-1).to(device=cs.device, dtype=torch.float64).detach().clone(),
            "ghost": cs.solver.neighbor_cpd_cell[1].reshape(-1).to(device=cs.device, dtype=torch.long).detach().clone(),
        }
    if hasattr(cs, "resolve_gauge"):
        cs.resolve_gauge(torch.zeros(cs.n_unknowns, device=cs.device, dtype=cs.dtype))
        cs._gauge_resolved_n = int(cs.n_cell)
    return cs


@contextlib.contextmanager
def torch_residual(cs, coefficient="implicit"):
    """The torch forward (``_warp_bypass``), compiler stood down, ``d_f`` differentiated."""
    from Pipeline.CoupledNewton.casestate import _eager
    from Pipeline.CoupledNewton.jacobian import _coefficient
    bypass = getattr(cs, "_warp_bypass", False)
    cs._warp_bypass = True
    try:
        with _eager(), _coefficient(cs, coefficient):
            yield
    finally:
        cs._warp_bypass = bypass


def field_slices(cs):
    from Pipeline.CoupledNewton.stop_rules import field_slices as fs
    return fs(cs.unknowns, int(cs.n_cell))


# ------------------------------------------------------------------------------------------------------- controls
def _units(case_dir):
    path = os.path.join(str(case_dir), "dctwin_map.json")
    if not os.path.exists(path):
        return {}, None
    dmap = json.load(open(path))
    units = {}
    for name, a in dmap["acus"].items():
        ret = a["return"]
        units[name] = dict(supply=a["supply"]["patch"], ret=ret["patch"] if ret.get("pinned") else None,
                           Q=float(a["supply"]["Q"]), T_sup=float(a["supply"]["T_supply_K"]))
    return units, dmap


class ControlBinding:
    """Writes control values onto the case's torch boundary conditions (``bind``) and puts the case's own back
    (``restore``).  ``values`` is one tensor per control, in ``controls`` order; leaves keep their graph."""

    def __init__(self, cs, case_dir, controls):
        from FVdomain.SetBC.onthefly.BCbase import VARType
        self.controls = [(str(k), str(u)) for k, u in controls]
        bad = [k for k, _ in self.controls if k not in CONTROL_KINDS]
        if bad:
            raise ValueError(f"unknown control kind(s) {bad}: one of {CONTROL_KINDS}")
        units, _ = _units(case_dir)
        self.units = units
        by = {}
        for bc in cs.solver.bcpatch_batch.iter_conditions():
            by[(bc.patch_name, int(bc.varType))] = bc
        self.by = by
        self.V = VARType
        self.base = [0.0 if k == "T_supply" else 1.0 for k, _ in self.controls]
        self._saved = {}
        self._targets = []                          # (control index, bc, attr, kind, base value, power)
        for i, (kind, unit) in enumerate(self.controls):
            sup = units[unit]["supply"] if unit in units else unit
            ret = units[unit]["ret"] if unit in units else None
            if kind == "T_supply":
                self._fixed(i, sup, VARType.TEMPERATURE, 0, offset=True)
            else:
                ubc = self._get(sup, VARType.VELOCITY)
                name = type(ubc).__name__
                if name == "Flowrateinletvelocity":
                    self._rate(i, ubc)
                elif name == "Fixedvalue":
                    self._fixed(i, sup, VARType.VELOCITY, 1)
                else:
                    raise NotImplementedError(f"{sup}: supply U of type {name} has no flow-fraction binding")
                self._fixed(i, sup, VARType.TURB_K, 2)
                self._fixed(i, sup, VARType.TURB_EPSILON, 3)
                if ret is not None:
                    rbc = self._get(ret, VARType.VELOCITY)
                    if type(rbc).__name__ != "Flowrateoutletvelocity":
                        raise NotImplementedError(f"{ret}: pinned return of type {type(rbc).__name__}")
                    self._rate(i, rbc)

    def _get(self, patch, var):
        bc = self.by.get((patch, int(var)))
        if bc is None:
            raise KeyError(f"no {var.name} boundary condition on patch {patch!r}")
        return bc

    def _fixed(self, i, patch, var, power, offset=False):
        bc = self._get(patch, var)
        if type(bc).__name__ not in ("Fixedvalue", "Uniformfixedvalue") or getattr(bc, "value_tensor", None) is None:
            raise NotImplementedError(f"{patch}: {var.name} condition {type(bc).__name__} has no value binding")
        fb = bc.value_tensor
        if fb.value_type not in ("uniform", "constant"):
            raise NotImplementedError(f"{patch}: {var.name} value_type {fb.value_type!r}")
        # the float32 the case's own Functionbase builds, so the bound residual equals the case's
        base = torch.tensor(fb.value, dtype=torch.float32).to(torch.float64).reshape(-1)
        self._targets.append((i, fb, "fixed", base, power, offset))

    def _rate(self, i, bc):
        if bc.volumetricFlowRate is not None:
            attr = "volumetricFlowRate"
        elif bc.massFlowRate is not None:
            attr = "massFlowRate"
        else:
            raise NotImplementedError(f"{bc.patch_name}: flow-rate condition without a rate")
        spec = getattr(bc, attr)
        if spec["value_type"] != "constant":
            raise NotImplementedError(f"{bc.patch_name}: {attr} value_type {spec['value_type']!r}")
        base = torch.tensor(float(spec["val"]), dtype=torch.float32).to(torch.float64).reshape(-1)
        self._targets.append((i, bc, attr, base, 1, False))

    def bind(self, values):
        for i, obj, attr, base, power, offset in self._targets:
            v = values[i].to(torch.float64)
            val = base.to(v.device) + v if offset else base.to(v.device) * v ** power
            if attr == "fixed":
                if id(obj) not in self._saved:
                    self._saved[id(obj)] = (obj, "fixed", (obj.value_type, obj.value))
                obj.value_type, obj.value = "tensor", val
            else:
                if id(obj) not in self._saved:
                    self._saved[id(obj)] = (obj, attr, getattr(obj, attr))
                setattr(obj, attr, {"value_type": "tensor", "val": val})

    def restore(self):
        for obj, attr, saved in self._saved.values():
            if attr == "fixed":
                obj.value_type, obj.value = saved
            else:
                setattr(obj, attr, saved)
        self._saved = {}

    def explicit(self, values):
        """Per-unit (T_sup offset [K], flow multiplier) tensors the objective reads explicitly (the heats)."""
        out = {}
        for (kind, unit), v in zip(self.controls, values):
            d = out.setdefault(unit, {})
            d[kind] = v
        return out


# ------------------------------------------------------------------------------------------------------ objective
class Objective:
    """``sum_q w_q q(x)``.  Names: the ``Post_process.dc_qoi.DCQuantities`` names (``return_T:<acu>``,
    ``inlet_T:<patch>``, ``heat_W:<acu>``, ``heat_W:total``; the heats read the bound supply T and flow), and on any
    case ``patch_T:<patch>`` (area-mean owner T of a zeroGradient-T patch, K above TRef) and ``volmean_T``."""

    def __init__(self, cs, case_dir, weights):
        self.cs = cs
        self.w = {str(k): float(v) for k, v in weights.items()}
        n = int(cs.n_cell)
        self.n = n
        sl = field_slices(cs)
        self.su, self.st = sl["U"], sl["T"]
        self.dq = None
        if any(k.split(":")[0] in ("return_T", "inlet_T", "heat_W") for k in self.w):
            from Post_process.dc_qoi import DCQuantities
            self.dq = DCQuantities(str(case_dir), device=cs.device, dtype=torch.float64)
            missing = [k for k in self.w if k.split(":")[0] in ("return_T", "inlet_T", "heat_W")
                       and k not in self.dq.names]
            if missing:
                raise KeyError(f"unknown dctwin quantities {missing}")
        app = cs.solver
        self.vol = app.cell_volume.reshape(-1)[:n].to(torch.float64)
        self.patch = {}
        row = torch.full((int(app.cpd_cell_pos.shape[0]),), -1, dtype=torch.long, device=cs.device)
        row[cs.interior] = torch.arange(n, device=cs.device)
        fb = app.fvpatch_batch
        for key in self.w:
            if key.startswith("patch_T:"):
                p = key.split(":", 1)[1]
                bc = next((b for b in app.bcpatch_batch.iter_conditions() if b.patch_name == p), None)
                if bc is None:
                    raise KeyError(f"no patch {p!r}")
                s, e = int(fb.start_idx_face[bc.patch_idx]), int(fb.end_idx_face[bc.patch_idx])
                owner = row[app.neighbor_cpd_cell[0, s:e].long()] if hasattr(app, "neighbor_cpd_cell") else None
                if owner is None or bool((owner < 0).any()):
                    raise RuntimeError(f"{p}: owner rows not resolved")
                self.patch[key] = (owner, app.face_area.reshape(-1)[s:e].to(torch.float64))

    def terms(self, x, explicit=None):
        T = x[self.st]
        out = {}
        if self.dq is not None:
            U = x[self.su].reshape(3, self.n).T
            saved = {}
            if explicit:
                for unit, d in explicit.items():
                    a = self.dq.acus.get(unit)
                    if a is None:
                        continue
                    saved[unit] = (a["Q"], a["T_sup"], a.get("frov"))
                    f = d.get("flow_fraction")
                    if f is not None:
                        a["Q"] = a["Q"] * f
                        if a.get("frov") is not None:
                            a["frov"] = (a["frov"][0] * f, a["frov"][1])
                    dT = d.get("T_supply")
                    if dT is not None:
                        a["T_sup"] = a["T_sup"] + dT
            try:
                q = self.dq(U, T)
            finally:
                for unit, (Q, Ts, frov) in saved.items():
                    a = self.dq.acus[unit]
                    a["Q"], a["T_sup"] = Q, Ts
                    if frov is not None:
                        a["frov"] = frov
            out.update({k: q[k] for k in self.w if k in q})
        for key, (owner, area) in self.patch.items():
            out[key] = (T[owner] * area).sum() / area.sum()
        if "volmean_T" in self.w:
            out["volmean_T"] = (T * self.vol).sum() / self.vol.sum()
        return out

    def __call__(self, x, explicit=None):
        t = self.terms(x, explicit)
        return sum(self.w[k] * t[k] for k in self.w), t


# ---------------------------------------------------------------------------------------------- transposed solve
def transpose_tape(cs, x):
    """``w -> J^T w`` by the VJP of the torch residual taped once at ``x`` (exact), and ``r(x)``."""
    leaf = x.detach().clone().requires_grad_(True)
    with torch.enable_grad(), torch_residual(cs):
        r = cs.residual(leaf)

    def apply(w):
        (g,) = torch.autograd.grad(r, leaf, w.reshape(-1), retain_graph=True)
        return g

    return apply, r.detach()


def pseudo_time_shift(cs, x, cfl, *, turb_cfl=0.2, u_ref=None):
    """The primal driver's ``s_i = lambda_i / CFL`` (``Pipeline.CoupledNewton.pseudo_time``: face-flux / viscous
    spectral radius, turbulence rows at ``CFL * turb_cfl``, continuity rows unshifted) at ``x``."""
    from Pipeline.CoupledNewton.pseudo_time import _Geometry, boundary_reference_velocity
    from Pipeline.CoupledNewton.pseudo_time import TURBULENCE_FIELDS
    n, nf = int(cs.n_cell), int(cs.num_fields)
    with torch.no_grad():
        cs.residual(x)                       # leaves the face flux / nu_eff the spectral radius reads
    lam = _Geometry(cs).spectral_radius(cs, boundary_reference_velocity(cs) if u_ref is None else u_ref)
    s = lam.repeat(nf) / cfl
    sl = field_slices(cs)
    for name, rows in sl.items():
        if name in TURBULENCE_FIELDS:
            s[rows] = s[rows] / turb_cfl
    s[cs.velocity:cs.pressure] = 0.0
    return s


def _warp_camg_defaults(dev):
    if dev.type == "cuda":
        from Parallel.newton import enable_warp_amg
        from SparseSolver.gmres import KRYLOV
        from SparseSolver import gmres_warp as _gw
        enable_warp_amg(dev)
        KRYLOV["device_loop"] = False
        KRYLOV["fused_orth"] = True
        KRYLOV["basis_fp32"] = True
        _gw.BACKEND["impl"] = "warp"
        return dict(smoother="gs", restriction="pg", l1=True,
                    fast_kernels=True, block_kernels=True, block_values="fp32",
                    lean=True, fused_numeric=True, gpu_setup=True,
                    colour_coarsest=False, cuda_graph=True, drop_block_values=True)
    return dict(smoother="gs", restriction="pg", l1=True)


def _source_block_csr(cs, x, source: str):
    from Pipeline.CoupledNewton.block_amg import from_field_blocked_csr
    n, nf = int(cs.n_cell), int(cs.num_fields)
    w = getattr(cs, "_warp", None)
    if source in ("assembled", "two_hop") and w is not None and hasattr(w, "two_hop_jacobian"):
        return w.two_hop_jacobian(x, mode="implicit").A
    if source == "assembled":
        cs.assembly_is_operator = False
        Jb = cs.assemble(x, coefficient="implicit")["J"]
        A = from_field_blocked_csr(Jb.crow_indices(), Jb.col_indices(), Jb.values(), n, nf)
        del Jb
        return A
    if source == "compact":
        return cs._warp.compact_jacobian(x, mode="frozen").A
    raise ValueError(f"transposed preconditioner source {source!r}: 'assembled', 'two_hop' or 'compact'")


def _clear_solver_autograd(cs, x):
    import gc
    with torch.no_grad():
        cs.residual(x)
    _free_warp_scratch(cs)
    if cs.device.type == "cuda":
        gc.collect()
        _empty_cuda_cache()

def _free_warp_scratch(cs, *, full: bool = False):
    import gc
    w = getattr(cs, "_warp", None)
    if w is not None and hasattr(w, "W"):
        getattr(w.W, "_compact", {}).clear()
        getattr(w.W, "_ios", {}).clear()
        getattr(w.W, "_bufs", {}).clear()
        if full:
            for attr in ("_keep", "_keep_w", "_keep_c", "_keep_g", "_ew_t", "_rack_t", "_pz_t", "_fro"):
                if hasattr(w.W, attr):
                    setattr(w.W, attr, None)
            cs._warp = None
    if cs.device.type == "cuda":
        gc.collect()
        try:
            import warp as wp
            wp.set_mempool_release_threshold(str(cs.device), 0)
        except Exception:
            pass
        _empty_cuda_cache()


def transposed_preconditioner(cs, x, *, source="assembled", shift=None, camg_kwargs=None):
    """Coupled block AMG of ``(J_src + diag shift)^T``; returns ``(M, seconds, nnz)``."""
    from Pipeline.CoupledNewton.block_amg import CoupledAMG
    t0 = time.perf_counter()
    n, nf = int(cs.n_cell), int(cs.num_fields)
    A = _source_block_csr(cs, x, source)
    _free_warp_scratch(cs)
    if shift is not None:
        dslot = torch.nonzero(A.row == A.col).reshape(-1)
        dslot = dslot[torch.argsort(A.row[dslot])]
        if dslot.numel() != n:
            raise RuntimeError("a shifted row has no stored diagonal")
        f = torch.arange(nf, device=A.val.device)
        A.val[dslot[:, None], f[None, :], f[None, :]] += shift.reshape(nf, n).T
    B = A.transpose(release_self=True)
    nnz = int(B.val.numel())
    del A
    if cs.device.type == "cuda":
        _empty_cuda_cache()
    kw = _warp_camg_defaults(cs.device)
    kw.update(camg_kwargs or {})
    amg = CoupledAMG(B, **kw)
    del B
    torch.cuda.synchronize(cs.device) if cs.device.type == "cuda" else None
    return amg, time.perf_counter() - t0, nnz


def fgmres(A, b, M, *, rtol=1e-6, restart=100, max_cycles=10, x0=None, callback=None, fp64_basis=False):
    """Right-preconditioned FLEXIBLE GMRES (Saad 1993): ``M`` may change between iterations (an inner Krylov solve).
    Vectorised CGS2 on the device with host-stored ``Z`` vectors to minimise GPU memory; Givens rotations on the host.
    Returns ``(x, iterations, history of the recurrence's relative residual)``."""
    nb = float(b.norm())
    x = torch.zeros_like(b) if x0 is None else x0.clone()
    N = int(b.numel())
    its, hist = 0, []
    from SparseSolver.gmres_warp import FusedOrth
    use_warp_orth = b.device.type == "cuda" and not fp64_basis
    for _ in range(int(max_cycles)):
        r = b - A(x)
        beta = float(r.norm())
        hist.append(beta / max(nb, 1e-300))
        if beta <= rtol * nb:
            break
        v_dtype = torch.float32 if use_warp_orth else b.dtype
        V = torch.empty((restart + 1, N), dtype=v_dtype, device=b.device)
        V[0].copy_((r / beta).to(v_dtype))
        del r
        orth = FusedOrth(V, impl="warp") if use_warp_orth else None
        Z = []
        H = torch.zeros(restart + 1, restart, dtype=torch.float64)
        cs_, sn = [], []
        e = [beta]
        k_done = 0
        for k in range(restart):
            vk = V[k].to(b.dtype) if v_dtype != b.dtype else V[k]
            z = M(vk)
            del vk
            w = A(z)
            Z.append(z.cpu())
            del z
            if orth is not None:
                orth.step(w, k + 1)
                del w
                entries, _, hn, _ = orth.read(k + 1)
                H[: k + 1, k] = torch.tensor(entries, dtype=torch.float64)
                H[k + 1, k] = hn
            else:
                Vk = V[: k + 1]
                for _pass in range(2):
                    h_vec = Vk @ w
                    H[: k + 1, k] += h_vec.cpu()
                    w = w - h_vec @ Vk
                hn = float(w.norm())
                H[k + 1, k] = hn
            for i in range(k):
                a, c = float(H[i, k]), float(H[i + 1, k])
                H[i, k], H[i + 1, k] = cs_[i] * a + sn[i] * c, -sn[i] * a + cs_[i] * c
            a, c = float(H[k, k]), float(H[k + 1, k])
            den = (a * a + c * c) ** 0.5
            cs_.append(a / den)
            sn.append(c / den)
            H[k, k], H[k + 1, k] = den, 0.0
            e.append(-sn[k] * e[k])
            e[k] = cs_[k] * e[k]
            its += 1
            k_done = k + 1
            rel = abs(e[k + 1]) / max(nb, 1e-300)
            hist.append(rel)
            if callback is not None:
                callback(its, rel)
            if rel <= rtol or hn == 0.0:
                if orth is None:
                    del w
                break
            if orth is not None:
                orth.store(V[k + 1])
            else:
                V[k + 1].copy_(w / hn)
                del w
        y = torch.linalg.solve_triangular(H[:k_done, :k_done], torch.tensor(e[:k_done], dtype=torch.float64)
                                          .reshape(-1, 1), upper=True).reshape(-1)
        dx = torch.zeros(N, dtype=torch.float64)
        for i in range(k_done):
            dx.add_(Z[i], alpha=float(y[i]))
        x = x + dx.to(b.device, dtype=b.dtype)
        del V, orth, Z, dx
        if b.device.type == "cuda":
            _empty_cuda_cache()
    else:
        hist.append(float((b - A(x)).norm()) / max(nb, 1e-300))
    return x, its, hist


def _global_patch_lr(cs, x):
    import warp as wp
    w = getattr(cs, "_warp", None)
    if w is None or not hasattr(w, "W"):
        return None, None
    W = w.W
    if not W.n_rack and not W.n_fro:
        return None, None
    dev = cs.device
    n, nf = int(cs.n_cell), int(cs.num_fields)
    N = n * nf
    W.set_old_state(getattr(cs, "_old_state", None))
    W.residual(x)
    q = W.compound_state()
    v_rows, v_cols, v_vals = [], [], []
    col_id = 0
    if W.n_rack:
        r_off = wp.to_torch(W.r_off).long()
        r_src = wp.to_torch(W.r_src).long()
        r_area = wp.to_torch(W.r_area).to(torch.float64)
        r_asum = wp.to_torch(W.r_asum).to(torch.float64)
        r_flux = wp.to_torch(W.r_flux).long()
        r_gh = wp.to_torch(W.r_gh).long()
        r_n = wp.to_torch(W.r_n).to(torch.float64)
        for r in range(W.n_rack):
            s, e = int(r_off[r]), int(r_off[r + 1])
            src = r_src[s:e]
            area = r_area[s:e]
            if int(r_flux[r]) == 1:
                gh = r_gh[s:e]
                nv = r_n[s:e]
                phi = (q[gh, 0] * nv[:, 0] + q[gh, 1] * nv[:, 1] + q[gh, 2] * nv[:, 2]) * area
                den = phi.sum()
                w_t = phi / den
            else:
                w_t = area / r_asum[r]
            cells, inv = torch.unique(src, return_inverse=True)
            vec_t = torch.zeros(cells.numel(), dtype=torch.float64, device=dev).scatter_add_(0, inv, w_t)
            nrm = float(vec_t.norm())
            if nrm > 1e-14:
                v_rows.append(4 * n + cells)
                v_cols.append(torch.full_like(cells, col_id))
                v_vals.append(vec_t / nrm)
                col_id += 1
            if int(r_flux[r]) == 1:
                T_src = q[src, 4]
                rmean_val = (T_src * phi).sum() / den
                uo = q[src, 0:3]
                nup = (uo * nv).sum(dim=1)
                w_u = torch.where(nup > 0.0, (T_src - rmean_val) * phi / (den * nup.clamp_min(1e-30)),
                                  torch.zeros_like(nup))
                for d in range(3):
                    vec_ud = torch.zeros(cells.numel(), dtype=torch.float64, device=dev).scatter_add_(
                        0, inv, w_u * nv[:, d])
                    nrm_u = float(vec_ud.norm())
                    if nrm_u > 1e-14:
                        v_rows.append(d * n + cells)
                        v_cols.append(torch.full_like(cells, col_id))
                        v_vals.append(vec_ud / nrm_u)
                        col_id += 1
    if W.n_fro:
        f_off = wp.to_torch(W.f_off).long()
        f_owner = wp.to_torch(W.f_owner).long()
        f_n = wp.to_torch(W.f_n).to(torch.float64)
        f_rhoa = wp.to_torch(W.f_rhoa).to(torch.float64)
        for p in range(W.n_fro):
            s, e = int(f_off[p]), int(f_off[p + 1])
            own = f_owner[s:e]
            nv = f_n[s:e]
            rhoa = f_rhoa[s:e]
            uo = q[own, 0:3]
            nup = (uo * nv).sum(dim=1)
            w_f = torch.where(nup > 0.0, rhoa, torch.zeros_like(rhoa))
            cells, inv = torch.unique(own, return_inverse=True)
            for d in range(3):
                vec_fd = torch.zeros(cells.numel(), dtype=torch.float64, device=dev).scatter_add_(
                    0, inv, w_f * nv[:, d])
                nrm_f = float(vec_fd.norm())
                if nrm_f > 1e-14:
                    v_rows.append(d * n + cells)
                    v_cols.append(torch.full_like(cells, col_id))
                    v_vals.append(vec_fd / nrm_f)
                    col_id += 1
    if not v_rows:
        return None, None
    rows_cat = torch.cat(v_rows)
    cols_cat = torch.cat(v_cols)
    vals_cat = torch.cat(v_vals)
    u_rows, r_inv = torch.unique(rows_cat, return_inverse=True)
    V_sub = torch.zeros((u_rows.numel(), col_id), dtype=torch.float64, device=dev)
    V_sub.index_put_((r_inv, cols_cat), vals_cat, accumulate=True)
    Q_sub, R_sub = torch.linalg.qr(V_sub, mode="reduced")
    keep_cols = torch.nonzero(torch.diagonal(R_sub).abs() > 1e-10).reshape(-1)
    if keep_cols.numel() == 0:
        return None, None
    Q_sub = Q_sub[:, keep_cols]
    nz_r, nz_c = torch.nonzero(Q_sub.abs() > 1e-14, as_tuple=True)
    K = int(keep_cols.numel())
    row_cols, row_vals = [], []
    blk = 8
    frozen = bool(cs.solver._mwi_frozen_coefficient)
    for s in range(0, K, blk):
        e = min(K, s + blk)
        m = e - s
        D = torch.zeros((blk, N), dtype=torch.float64, device=dev)
        for j in range(m):
            c = s + j
            mask = nz_c == c
            D[j, u_rows[nz_r[mask]]] = Q_sub[nz_r[mask], c]
        _, J0 = W.jvp(x, D, frozen=frozen, drop_global=False)
        _, J1 = W.jvp(x, D, frozen=frozen, drop_global=True)
        U_blk = J0[:m] - J1[:m]
        for j in range(m):
            c_idx = torch.nonzero(U_blk[j].abs() > 1e-14).reshape(-1)
            row_cols.append(c_idx)
            row_vals.append(U_blk[j, c_idx])
        del D, J0, J1, U_blk
    _free_warp_scratch(cs)
    BS = 256
    ch_c, ch_v, ch_r = [], [], []
    for k_i, (rc, rv) in enumerate(zip(row_cols, row_vals)):
        L = int(rc.numel())
        for s_i in range(0, L, BS):
            e_i = min(L, s_i + BS)
            m_i = e_i - s_i
            if m_i < BS:
                pad_c = torch.zeros(BS, dtype=torch.long, device=dev)
                pad_v = torch.zeros(BS, dtype=torch.float64, device=dev)
                pad_c[:m_i] = rc[s_i:e_i]
                pad_v[:m_i] = rv[s_i:e_i]
                ch_c.append(pad_c)
                ch_v.append(pad_v)
            else:
                ch_c.append(rc[s_i:e_i])
                ch_v.append(rv[s_i:e_i])
            ch_r.append(k_i)
    if not ch_c:
        return None, None
    ut_c_ch = torch.stack(ch_c, dim=0)
    ut_v_ch = torch.stack(ch_v, dim=0)
    ch_k = torch.tensor(ch_r, dtype=torch.long, device=dev)

    def add_lr(y, v):
        c_ch = (ut_v_ch * v[ut_c_ch]).sum(dim=1)
        c_k = torch.zeros(K, dtype=c_ch.dtype, device=dev).scatter_add_(0, ch_k, c_ch)
        y.index_add_(0, u_rows, torch.mv(Q_sub, c_k))
        return y

    # the forward term U Q^T: the same low-rank pieces, for the tangent's preconditioner (transpose=False)
    flat_c = ut_c_ch.reshape(-1)

    def add_lr_fwd(y, v):
        c = Q_sub.T @ v[u_rows]
        y.index_add_(0, flat_c, (ut_v_ch * c[ch_k][:, None]).reshape(-1))
        return y

    return add_lr, add_lr_fwd


def shifted_inner(cs, x, *, cfl, inner_rtol=1e-2, inner_its=60, inner_max_cycles=1, source="assembled",
                  camg_kwargs=None, apply_t=None, amg_mode="frozen", p_reg=0.0, transpose=True, extra_shift=None,
                  amg_cfl=None, amg_cfl_T=None, equilibrate=None, exact_op=None):
    """The adjoint preconditioner ``v -> (J_src + S)^{-T} v`` by GMRES on the transposed shifted matrix,
    right-preconditioned by camg of the same matrix (``S``: the pseudo-time shift at ``cfl``).  Returns
    ``(apply, build seconds, counter dict)``; the inner iterations are counted in ``counter['inner']``.

    On the compact path (``source="compact"`` or a large 2-hop matrix) camg is built on the transposed 1-hop
    ``compact_jacobian(mode=amg_mode)`` (``"frozen"`` | ``"picard"``; ``"two_hop"``: camg of the inner operator
    itself, the transposed shifted 2-hop matrix, as on bai-dcrans); ``p_reg`` > 0 adds ``p_reg * mean|D_pp|`` to
    the p_rgh diagonal of THAT camg matrix only (the inner GMRES operator and the outer ``J^T`` are unchanged).

    ``transpose=False``: the same preconditioner of the forward (tangent) system, ``v -> (J_src + S)^{-1} v``,
    with the forward low-rank global-patch term ``U Q^T`` (the outer operator is the exact JVP).

    ``extra_shift``: a diagonal added to the pseudo-time shift (the physical-step ``V / dt`` of a time march, so the
    preconditioner approximates the operator the outer solve actually has)."""
    from SparseSolver.gmres import gmres
    from Pipeline.CoupledNewton.block_amg import CoupledAMG
    t0 = time.perf_counter()
    if apply_t is None:
        _clear_solver_autograd(cs, x)
    tr = (lambda M: M.transpose(release_self=True)) if transpose else (lambda M: M)
    n, nf = int(cs.n_cell), int(cs.num_fields)
    N = n * nf
    if (exact_op is not None and not transpose and cs.device.type == "cuda"
            and getattr(cs, "_warp", None) is not None and amg_mode != "two_hop"):
        s_pt = (pseudo_time_shift(cs, x, cfl) if (cfl and float(cfl) < 1e8) else None)
        kw = _warp_camg_defaults(cs.device)
        kw.update(camg_kwargs or {})
        A1 = cs._warp.compact_jacobian(x, mode=amg_mode).A
        _free_warp_scratch(cs)
        B = A1
        del A1
        if B.val.dtype != torch.float64 or B.val.requires_grad:
            B.val = B.val.detach().to(torch.float64)
        dslot_b = torch.nonzero(B.row == B.col).reshape(-1)
        dslot_b = dslot_b[torch.argsort(B.row[dslot_b])]
        f = torch.arange(nf, device=B.val.device)
        if amg_cfl is not None:
            s_b = pseudo_time_shift(cs, x, float(amg_cfl)).clone()
            if amg_cfl_T is not None and nf >= 5:
                s_b[4 * n : 5 * n] = pseudo_time_shift(cs, x, float(amg_cfl_T))[4 * n : 5 * n]
        elif s_pt is not None:
            s_b = s_pt.clone()
        else:
            s_b = torch.zeros(N, dtype=x.dtype, device=cs.device)
        if extra_shift is not None:
            s_b = s_b + extra_shift.reshape(s_b.shape).to(s_b.dtype)
        B.val[dslot_b[:, None], f[None, :], f[None, :]] += s_b.reshape(nf, n).T
        if p_reg:
            ip = int(cs.velocity // n)
            dpp = B.val[dslot_b, ip, ip]
            B.val[dslot_b, ip, ip] = dpp + float(p_reg) * float(dpp.abs().mean())
        amg = CoupledAMG(B, **kw)
        del B
        _free_warp_scratch(cs)
        if getattr(amg, "cuda_graph", False):
            amg._fast_levels()
            _free_warp_scratch(cs)
            _ = amg(torch.zeros(N, dtype=x.dtype, device=cs.device))
            _free_warp_scratch(cs)
        op = exact_op if s_pt is None else (lambda v: exact_op(v) + s_pt * v)
        counter = {"inner": 0, "calls": 0}
        use_warp_orth = True
        if equilibrate is not None:
            rw = equilibrate.reshape(-1)
            def apply(v):
                res = gmres(lambda u: rw * op(u), rw * v, restart=inner_its, max_cycles=inner_max_cycles,
                            M=lambda y: amg(y / rw), rtol=inner_rtol, fused_orth=use_warp_orth, basis_fp32=use_warp_orth)
                counter["inner"] += int(res.iterations)
                counter["calls"] += 1
                return res.x
        else:
            def apply(v):
                res = gmres(op, v, restart=inner_its, max_cycles=inner_max_cycles, M=amg, rtol=inner_rtol,
                            fused_orth=use_warp_orth, basis_fp32=use_warp_orth)
                counter["inner"] += int(res.iterations)
                counter["calls"] += 1
                return res.x

        def _cleanup():
            getattr(amg, "_graphs", {}).clear()
            if getattr(amg, "_fast", None):
                amg._fast.clear()
            if hasattr(amg, "values") and amg.values:
                amg.values.clear()
        apply.fast_t = None
        apply.op, apply.amg = op, amg
        apply.op_nolr = op
        apply._cleanup = _cleanup
        _sync(cs.device)
        return apply, time.perf_counter() - t0, counter

    op_source = "two_hop" if source == "compact" else source
    fast_t = None
    V_sp, UT_sp = None, None
    if cs.device.type == "cuda":
        w = getattr(cs, "_warp", None)
        if w is not None and hasattr(w, "W"):
            V_sp, UT_sp = _global_patch_lr(cs, x)
            if not transpose:              # the tangent: the forward low-rank term
                V_sp, UT_sp = UT_sp, None
            _free_warp_scratch(cs)
    A = _source_block_csr(cs, x, op_source)
    _free_warp_scratch(cs)
    if cs.device.type == "cuda":
        from Pipeline.CoupledNewton.block_amg_warp import bsr_mv_field_blocked
        AT0 = tr(A)
        del A
        if AT0.val.dtype != torch.float64 or AT0.val.requires_grad:
            AT0.val = AT0.val.detach().to(torch.float64)
        _empty_cuda_cache()
        warp_bmv_fb = bsr_mv_field_blocked(AT0, dtype=torch.float64)
    else:
        AT0 = tr(A)
        del A
    s = (pseudo_time_shift(cs, x, cfl)
         if (cfl and float(cfl) < 1e8)
         else torch.zeros(N, dtype=x.dtype, device=cs.device))
    if extra_shift is not None:
        s = s + extra_shift.reshape(s.shape).to(s.dtype)
    dslot = torch.nonzero(AT0.row == AT0.col).reshape(-1)
    dslot = dslot[torch.argsort(AT0.row[dslot])]
    if dslot.numel() != n:
        raise RuntimeError("a cell has no diagonal block")
    f = torch.arange(nf, device=AT0.val.device)
    AT0.val[dslot[:, None], f[None, :], f[None, :]] += s.reshape(nf, n).T
    kw = _warp_camg_defaults(cs.device)
    kw.update(camg_kwargs or {})
    if (amg_mode != "two_hop" and cs.device.type == "cuda" and (source == "compact" or int(AT0.row.numel()) > 8_000_000)
            and getattr(cs, "_warp", None) is not None):
        from Pipeline.CoupledNewton.block_amg_warp import bsr_mv_field_blocked
        from Pipeline.CoupledNewton.block_amg import BlockCSR
        at0_row, at0_col, at0_shape = AT0.row, AT0.col, AT0.shape
        at0_val_cpu = AT0.val.cpu()
        del AT0, warp_bmv_fb
        _free_warp_scratch(cs)
        A1 = cs._warp.compact_jacobian(x, mode=amg_mode).A
        _free_warp_scratch(cs, full=bool(transpose and torch.cuda.get_device_properties(cs.device).total_memory < 48 * 2**30))
        B = tr(A1)
        del A1
        if B.val.dtype != torch.float64 or B.val.requires_grad:
            B.val = B.val.detach().to(torch.float64)
        dslot_b = torch.nonzero(B.row == B.col).reshape(-1)
        dslot_b = dslot_b[torch.argsort(B.row[dslot_b])]
        if amg_cfl is not None:
            s_b = pseudo_time_shift(cs, x, float(amg_cfl)).clone()
            if amg_cfl_T is not None and nf >= 5:
                s_b[4 * n : 5 * n] = pseudo_time_shift(cs, x, float(amg_cfl_T))[4 * n : 5 * n]
            if extra_shift is not None:
                s_b = s_b + extra_shift.reshape(s_b.shape).to(s_b.dtype)
        else:
            s_b = s
        B.val[dslot_b[:, None], f[None, :], f[None, :]] += s_b.reshape(nf, n).T
        if p_reg:
            ip = int(cs.velocity // n)               # the p_rgh field index (continuity rows)
            dpp = B.val[dslot_b, ip, ip]
            B.val[dslot_b, ip, ip] = dpp + float(p_reg) * float(dpp.abs().mean())
        amg = CoupledAMG(B, **kw)
        del B
        _free_warp_scratch(cs)
        if getattr(amg, "cuda_graph", False):
            amg._fast_levels()
            _free_warp_scratch(cs)
            _ = amg(torch.zeros(N, dtype=x.dtype, device=cs.device))
            _free_warp_scratch(cs)
        AT0_restored = BlockCSR(at0_row, at0_col, at0_val_cpu.to(cs.device), at0_shape)
        del at0_val_cpu, at0_row, at0_col
        warp_bmv_fb = bsr_mv_field_blocked(AT0_restored, dtype=torch.float64)
        del AT0_restored
        _empty_cuda_cache()
    else:
        B = AT0
        del AT0
        if p_reg:
            raise NotImplementedError("p_reg on the two_hop camg would change the inner operator too")
        amg = CoupledAMG(B, **kw)
        if cs.device.type == "cuda":
            del B
            _free_warp_scratch(cs)
            if getattr(amg, "cuda_graph", False):
                amg._fast_levels()
                _free_warp_scratch(cs)
                _ = amg(torch.zeros(N, dtype=x.dtype, device=cs.device))
                _free_warp_scratch(cs)
    if cs.device.type == "cuda":
        if V_sp is not None:
            fast_t = lambda v: V_sp(warp_bmv_fb(v).sub_(s * v), v)
            op = lambda v: V_sp(warp_bmv_fb(v), v)
        else:
            fast_t = lambda v: warp_bmv_fb(v).sub_(s * v)
            op = warp_bmv_fb
    else:
        op = lambda v: B.matvec(v.reshape(nf, n).T).T.reshape(-1)
    counter = {"inner": 0, "calls": 0}

    use_warp_orth = cs.device.type == "cuda"
    if equilibrate is not None:
        rw = equilibrate.reshape(-1)
        if not transpose:
            def apply(v):
                res = gmres(lambda u: rw * op(u), rw * v, restart=inner_its, max_cycles=inner_max_cycles,
                            M=lambda y: amg(y / rw), rtol=inner_rtol, fused_orth=use_warp_orth, basis_fp32=use_warp_orth)
                counter["inner"] += int(res.iterations)
                counter["calls"] += 1
                return res.x
        else:
            def apply(v):
                res = gmres(lambda y: op(rw * y), v, restart=inner_its, max_cycles=inner_max_cycles,
                            M=lambda u: amg(u) / rw, rtol=inner_rtol, fused_orth=use_warp_orth, basis_fp32=use_warp_orth)
                counter["inner"] += int(res.iterations)
                counter["calls"] += 1
                return rw * res.x
    else:
        def apply(v):
            res = gmres(op, v, restart=inner_its, max_cycles=inner_max_cycles, M=amg, rtol=inner_rtol,
                        fused_orth=use_warp_orth, basis_fp32=use_warp_orth)
            counter["inner"] += int(res.iterations)
            counter["calls"] += 1
            return res.x

    def _cleanup():
        getattr(amg, "_graphs", {}).clear()
        if getattr(amg, "_fast", None):
            amg._fast.clear()
        if hasattr(amg, "values") and amg.values:
            amg.values.clear()
    apply.fast_t = fast_t
    apply.op, apply.amg = op, amg
    apply.op_nolr = warp_bmv_fb if cs.device.type == "cuda" else op
    apply._cleanup = _cleanup
    _sync(cs.device)
    return apply, time.perf_counter() - t0, counter


def box_aggregates(cs, boxes: int):
    """Cell -> box index for ``boxes`` axis-aligned boxes over the interior cell centres (``boxes`` <= 1: one)."""
    n = int(cs.n_cell)
    if boxes <= 1:
        return torch.zeros(n, dtype=torch.long, device=cs.device), 1
    pos = cs.solver.cpd_cell_pos[cs.interior].to(torch.float64)
    lo, hi = pos.min(0).values, pos.max(0).values
    ext = (hi - lo).clamp_min(1e-12)
    per = (boxes / float(ext.prod())) ** (1.0 / 3.0)
    nb = torch.clamp((ext * per).round().long(), min=1)
    ijk = torch.minimum(((pos - lo) / ext * nb).long(), nb - 1)
    key = (ijk[:, 0] * nb[1] + ijk[:, 1]) * nb[2] + ijk[:, 2]
    _, agg = torch.unique(key, return_inverse=True)
    return agg, int(agg.max()) + 1


class CoarseCorrection:
    """Two-level wrapper of a preconditioner for ``A`` (here the EXACT ``J^T``): ``Z`` = piecewise constants of the
    named fields on cell boxes, ``E = Z^T A Z`` by ``A`` applied to every column (so the global reductions the
    assembled preconditioner omits -- rack / server outlet means -- are in it), and the multiplicative cycle
    ``z1 = Z E^-1 Z^T v;  z = z1 + M(v - A z1)``."""

    def __init__(self, cs, A, M, *, fields=("T",), boxes=64, batch=16, batch_A=None):
        sl = field_slices(cs)
        n = int(cs.n_cell)
        self.A, self.M = A, M
        agg, na = box_aggregates(cs, boxes)
        for f in fields:
            if sl[f].stop - sl[f].start != n:
                raise ValueError(f"coarse field {f!r} is not one scalar per cell")
        self.na, self.nf_c = na, len(fields)
        self.idx = torch.cat([sl[f].start + torch.arange(n, device=cs.device) for f in fields])  # its rows
        self.cidx = torch.cat([agg + k * na for k in range(len(fields))])  # their coarse dof
        nc = na * len(fields)
        self.nc = nc
        N = int(cs.n_unknowns)
        E = torch.zeros(nc, nc, dtype=torch.float64, device=cs.device)
        t0 = time.perf_counter()
        if batch_A is not None and nc > 0:
            chunk = max(int(batch), 32)
            Z_chunk = torch.zeros((min(chunk, nc), N), dtype=torch.float64, device=cs.device)
            for j0 in range(0, nc, chunk):
                j1 = min(j0 + chunk, nc)
                m = j1 - j0
                Zb = Z_chunk[:m]
                Zb.zero_()
                cols = torch.arange(j0, j1, device=cs.device)[:, None]
                Zb[:, self.idx] = (self.cidx[None, :] == cols).to(torch.float64)
                AZ = batch_A(Zb)
                for loc in range(m):
                    E[:, j0 + loc] = self.restrict(AZ[loc])
            del Z_chunk
        else:
            z = torch.zeros(N, dtype=torch.float64, device=cs.device)
            for j in range(nc):
                z.zero_()
                z[self.idx] = (self.cidx == j).to(torch.float64)
                E[:, j] = self.restrict(A(z))
            del z
        self.seconds = time.perf_counter() - t0
        self.lu = torch.linalg.lu_factor(E)
        self.N = N
        self.dev = cs.device

    def restrict(self, v):
        return torch.zeros(self.nc, dtype=v.dtype, device=v.device).index_add_(0, self.cidx, v[self.idx])

    def prolong(self, c):
        out = torch.zeros(self.N, dtype=c.dtype, device=c.device)
        out[self.idx] = c[self.cidx]
        return out

    def __call__(self, v):
        c = torch.linalg.lu_solve(*self.lu, self.restrict(v).reshape(-1, 1)).reshape(-1)
        z1 = self.prolong(c)
        return z1 + self.M(v - self.A(z1))


def _sync(dev):
    if dev.type == "cuda":
        torch.cuda.synchronize(dev)


def _field_equilibration(cs, x, dg):
    dev = cs.device
    n_c, nf_c = int(cs.n_cell), int(cs.num_fields)
    vp = torch.randn(n_c * nf_c, generator=torch.Generator().manual_seed(11), dtype=torch.float64).to(dev)
    _, jv_p = _warp_backend(cs).W.jvp(x, vp.reshape(1, -1), frozen=False)
    jv_tot = jv_p[0] if dg is None else jv_p[0] + dg * vp
    f_scale = torch.tensor(
        [max(float(jv_tot[f * n_c : (f + 1) * n_c].norm() / (n_c ** 0.5)), 1e-12) for f in range(nf_c)],
        dtype=torch.float64, device=dev,
    )
    return (1.0 / f_scale).repeat_interleave(n_c)


def solve_adjoint(cs, x, g, *, apply_t=None, method="fgmres", precond="assembled", shift_cfl=None,
                  camg_kwargs=None, rtol=1e-8, restart=200, max_cycles=10, inner_rtol=0.1, inner_its=200,
                  inner_max_cycles=1, coarse_boxes=0, coarse_fields=("T",), dual_time=0, amg_mode="frozen",
                  p_reg=0.0, amg_cfl=None, amg_cfl_T=None, equilibrate_fields=False, fp64_basis=False,
                  outer="fast", diag=None, log=print):
    """``lambda`` with ``J^T lambda = g`` at ``x``; returns ``(lambda, info)``.

    ``diag``: a diagonal added to the operator, ``(J + diag)^T lambda = g`` -- one backward step of the discrete
    adjoint of a physical-time march (``"fgmres"`` only); the preconditioner carries it too, as in ``solve_tangent``.

    ``method``:
    * ``"gmres"``: GMRES on the exact ``J^T`` right-preconditioned by ONE camg cycle of ``(J_src + S)^T``
      (``S`` = 0 without ``shift_cfl``);
    * ``"fgmres"`` (default): flexible GMRES on the exact ``J^T``, each preconditioner application an inner GMRES
      (``inner_rtol`` / ``inner_its``, camg-preconditioned) on the assembled shifted transposed matrix at
      ``shift_cfl`` (default 10), optionally inside a two-level ``CoarseCorrection`` (``coarse_boxes``);
    * ``"dual"``: the dual-time march ``lambda += (J + S)^{-T} (g - J^T lambda)`` for ``dual_time`` steps, the inner
      solve as for ``"fgmres"``.
    The achieved residual is recomputed with the exact operator."""
    from SparseSolver.gmres import gmres
    dev = cs.device
    info = {"method": method}
    rhs = cs.gauge(g.reshape(-1)) if getattr(cs, "_gauge_free", False) else g.reshape(-1)
    nb = float(rhs.norm())
    dg = None if diag is None else diag.reshape(-1).to(rhs.dtype)
    _warp_backend(cs)    # a previous adjoint's compact set-up may have freed it (one solve per step of a time march)
    if dg is not None and method != "fgmres":
        raise ValueError("solve_adjoint(diag=...) is implemented for method='fgmres'")
    if method == "gmres":
        if apply_t is None:
            t0 = time.perf_counter()
            apply_t, _ = transpose_tape(cs, x)
            _sync(dev)
            info["tape_s"] = time.perf_counter() - t0
        shift = pseudo_time_shift(cs, x, shift_cfl) if shift_cfl else None
        M, info["precond_s"], info["precond_nnz"] = transposed_preconditioner(cs, x, source=precond, shift=shift,
                                                                              camg_kwargs=camg_kwargs)
        t0 = time.perf_counter()
        res = gmres(apply_t, rhs, rtol=rtol, restart=restart, max_cycles=max_cycles, M=M)
        lam, info["iterations"] = res.x, int(res.iterations)
    else:
        cfl = float(shift_cfl or 10.0)
        rw = _field_equilibration(cs, x, dg) if equilibrate_fields else None
        inner, info["precond_s"], counter = shifted_inner(
            cs, x, cfl=cfl, inner_rtol=inner_rtol, inner_its=inner_its, inner_max_cycles=inner_max_cycles,
            source=precond, camg_kwargs=camg_kwargs, apply_t=apply_t, amg_mode=amg_mode, p_reg=p_reg,
            extra_shift=dg, amg_cfl=amg_cfl, amg_cfl_T=amg_cfl_T, equilibrate=rw,
        )
        fast_op = getattr(inner, "fast_t", None)
        outer_op = fast_op if outer == "fast" else None
        if outer not in ("fast", "exact"):
            raise ValueError(f"adjoint outer operator {outer!r}: 'fast' | 'exact'")
        if outer_op is None:
            if apply_t is None:
                t0 = time.perf_counter()
                apply_t, _ = transpose_tape(cs, x)
                _sync(dev)
                info["tape_s"] = time.perf_counter() - t0
            outer_op = apply_t
        if dg is not None:
            outer_op = (lambda op: (lambda v: op(v) + dg * v.reshape(-1)))(outer_op)
        M = inner
        if coarse_boxes:
            # the coarse matrix from the fast operator when there is one (it only preconditions)
            fast_coarse = (None if fast_op is None else
                           (fast_op if dg is None else (lambda v: fast_op(v) + dg * v.reshape(-1))))
            M = CoarseCorrection(cs, outer_op if fast_coarse is None else fast_coarse, inner, fields=coarse_fields,
                                 boxes=coarse_boxes)
            info["coarse_dofs"], info["coarse_s"] = M.nc, M.seconds
        t0 = time.perf_counter()
        if method == "fgmres":
            def _cb(it, rel):
                if log is not None and it % 10 == 0:
                    log(f"  [fgmres] outer={it} rel={rel:.3e} inner={counter['inner']} t={time.perf_counter()-t0:.1f}s")
            lam, its, hist = fgmres(outer_op, rhs, M, rtol=rtol, restart=restart, max_cycles=max_cycles,
                                    callback=_cb, fp64_basis=fp64_basis)
            info["iterations"], info["history_tail"], info["history"] = its, hist[-5:], hist
        elif method == "dual":
            s = pseudo_time_shift(cs, x, cfl)
            lam, hist = torch.zeros_like(rhs), []
            for k in range(int(dual_time)):
                d = rhs - outer_op(lam)
                hist.append(float(d.norm()) / max(nb, 1e-300))
                if hist[-1] <= rtol:
                    break
                lam = lam + M(d)
            info["iterations"], info["dual_history_tail"] = len(hist), hist[-5:]
        else:
            raise ValueError(f"adjoint method {method!r}: gmres | fgmres | dual")
        info["inner_iterations"] = counter["inner"]
        if hasattr(inner, "_cleanup"):
            inner._cleanup()
        del inner, M, outer_op, fast_op
        if dev.type == "cuda":
            import gc
            gc.collect()
            _empty_cuda_cache()
    _sync(dev)
    info["krylov_s"] = time.perf_counter() - t0
    if apply_t is None:
        t0 = time.perf_counter()
        apply_t, _ = transpose_tape(cs, x)
        _sync(dev)
        info["tape_s"] = time.perf_counter() - t0
    achieved = float((apply_t(lam) + (0.0 if dg is None else dg * lam) - rhs).norm()) / max(nb, 1e-300)
    del apply_t
    _clear_solver_autograd(cs, x)
    info["achieved_rel_residual"] = achieved
    info["requested_rtol"] = rtol
    log(f"adjoint ({method}, {precond}, CFL {shift_cfl}): {info['iterations']} outer iterations "
        f"({info.get('inner_iterations', 0)} inner) to {achieved:.3e} (requested {rtol:.1e}), "
        f"precond {info['precond_s']:.1f} s, Krylov {info['krylov_s']:.1f} s")
    return lam, info


def solve_tangent(cs, x, b, *, shift_cfl=None, camg_kwargs=None, rtol=1e-8, restart=200, max_cycles=10,
                  inner_rtol=0.1, inner_its=200, inner_max_cycles=1, precond="assembled", amg_mode="frozen",
                  p_reg=0.0, amg_cfl=None, amg_cfl_T=None, equilibrate_fields=False, fp64_basis=False,
                  coarse_boxes=0, coarse_fields=("T",), diag=None, log=print, x0=None):
    """``s`` with ``J s = b`` at ``x`` (the tangent system): flexible GMRES on the Warp backend's exact JVP
    (implicit ``d_f``), each preconditioner application an inner GMRES on the shifted assembled matrix, as
    ``solve_adjoint``'s ``"fgmres"`` for the transposed system, optionally inside the same two-level
    ``CoarseCorrection`` built on the exact JVP (``coarse_boxes``). Returns ``(s, info)``; the achieved residual is
    recomputed with the exact operator.

    ``b`` of shape ``(p, N)``: ``p`` right-hand sides (one per parameter) solved with ONE preconditioner set-up;
    returns ``(S, info)`` with ``S`` of the same shape and ``info["per_rhs"]`` the per-column iterations / residuals."""
    dev = cs.device
    info = {"method": "fgmres-tangent"}
    multi = b.dim() == 2
    B = b if multi else b.reshape(1, -1)
    gauged = getattr(cs, "_gauge_free", False)
    rhss = [cs.gauge(B[i].reshape(-1)) if gauged else B[i].reshape(-1) for i in range(B.shape[0])]
    rhs = rhss[0]
    W = _warp_backend(cs).W

    # ``diag``: a diagonal added to the operator, (J + diag) s = b -- the implicit-Euler step of a physical-time
    # march (diag = cell volume / dt on the non-continuity rows); the preconditioner keeps its pseudo-time shift
    dg = None if diag is None else diag.reshape(-1).to(rhs.dtype)

    def jvp(v):
        _, Jv = W.jvp(x, v.reshape(1, -1), frozen=False)
        return Jv[0] if dg is None else Jv[0] + dg * v.reshape(-1)

    def batch_jvp(V):
        _, JV = W.jvp(x, V, frozen=False)
        return JV if dg is None else JV + dg[None, :] * V

    cfl = float(shift_cfl or 10.0)
    rw = _field_equilibration(cs, x, dg) if equilibrate_fields else None
    use_jvp_inner = (precond == "compact" and dev.type == "cuda" and amg_mode != "two_hop"
                     and os.environ.get("LPCNS_TAN_TWO_HOP", "0") != "1")
    inner, info["precond_s"], counter = shifted_inner(
        cs, x, cfl=cfl, inner_rtol=inner_rtol, inner_its=inner_its, inner_max_cycles=inner_max_cycles,
        source=precond, camg_kwargs=camg_kwargs, amg_mode=amg_mode, p_reg=p_reg, transpose=False,
        extra_shift=dg, amg_cfl=amg_cfl, amg_cfl_T=amg_cfl_T, equilibrate=rw,
        exact_op=(jvp if use_jvp_inner else None),
    )
    M = inner
    if coarse_boxes:
        coarse_op = jvp if use_jvp_inner else (inner.op if (coarse_boxes > 64 and hasattr(inner, "op")) else jvp)
        M = CoarseCorrection(cs, coarse_op, inner, fields=coarse_fields, boxes=coarse_boxes, batch=32,
                             batch_A=(batch_jvp if coarse_op is jvp else None))
        info["coarse_dofs"], info["coarse_s"] = M.nc, M.seconds
    t0 = time.perf_counter()
    sols, per = [], []
    its = 0
    # x0: initial guesses, one per right-hand side (shape of b); a BDF1 window passes the previous step's tangents
    X0 = None if x0 is None else x0.reshape(len(rhss), -1).to(device=dev, dtype=rhs.dtype)
    for i_r, r in enumerate(rhss):
        nb = float(r.norm())
        if nb == 0.0:
            sols.append(torch.zeros_like(r))
            per.append({"iterations": 0, "achieved_rel_residual": 0.0, "history_tail": []})
            continue
        x_init = None if X0 is None else X0[i_r].reshape(r.shape)
        if x_init is not None and log is not None:
            log(f"  [fgmres-tan] initial guess: rel {float((r - jvp(x_init)).norm()) / nb:.3e}")
        def _cb_t(it, rel):
            if log is not None and it % 10 == 0:
                log(f"  [fgmres-tan] outer={it} rel={rel:.3e} inner={counter['inner']} t={time.perf_counter()-t0:.1f}s")
        s, its, hist = fgmres(jvp, r, M, rtol=rtol, restart=restart, max_cycles=max_cycles, callback=_cb_t,
                              fp64_basis=fp64_basis, x0=x_init)
        per.append({"iterations": its, "achieved_rel_residual": float((jvp(s) - r).norm()) / max(nb, 1e-300),
                    "history_tail": hist[-5:]})
        sols.append(s)
    info["iterations"] = max(q["iterations"] for q in per)
    info["inner_iterations"], info["history_tail"] = counter["inner"], per[0]["history_tail"]
    if hasattr(inner, "_cleanup"):
        inner._cleanup()
    del inner
    _sync(dev)
    info["krylov_s"] = time.perf_counter() - t0
    info["achieved_rel_residual"] = max(q["achieved_rel_residual"] for q in per)
    info["per_rhs"] = per
    info["requested_rtol"] = rtol
    s = torch.stack(sols) if multi else sols[0]
    if log is not None:
        log(f"tangent: {its} outer iterations ({counter['inner']} inner) to {info['achieved_rel_residual']:.3e}, "
            f"precond {info['precond_s']:.1f} s, Krylov {info['krylov_s']:.1f} s")
    return s, info


# ---------------------------------------------------------------------------------------------------- the API
def dc_gradient(case_dir, state, objective_weights, params, *, cs=None, device=None, values=None,
                method="fgmres", precond="assembled", shift_cfl=None, camg_kwargs=None, rtol=1e-6, restart=200,
                max_cycles=10, inner_rtol=0.1, inner_its=200, coarse_boxes=0, dual_time=0,
                accept_unconverged=False, amg_mode="frozen", p_reg=0.0, coarse_fields=("T",), outer="fast",
                log=print):
    """``(J, {f"{kind}:{unit}": dJ/dtheta}, info)`` by one transposed solve (module docstring).

    ``state``: a path (``torch.save`` of the flat x, or a dict with ``'x'``) or a tensor; ``cs``: an already built
    solver of ``case_dir`` (else ``build_solver``); ``values``: control values (default the case's own: offset 0,
    fraction 1) -- the gradient is taken at the case as bound, the state must be its converged solution."""
    t_all = time.perf_counter()
    if device is None:
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(device)
        if device.type == "cuda" and device.index is None:
            device = torch.device("cuda", torch.cuda.current_device())
    if cs is None:
        cs = build_solver(case_dir, torch.device(device))
    dev = cs.device
    if isinstance(state, (str, os.PathLike)):
        x = torch.load(state, map_location=dev)
        x = x["x"] if isinstance(x, dict) else x
    else:
        x = state
    x = x.to(dev, torch.float64).reshape(-1).detach()
    if x.numel() != cs.n_unknowns:
        raise ValueError(f"state has {x.numel()} unknowns, the case {cs.n_unknowns}")
    cs.resolve_gauge(x)
    binding = ControlBinding(cs, case_dir, params)
    base = binding.base if values is None else [float(v) for v in values]
    vals = [torch.tensor(v, dtype=torch.float64, device=dev) for v in base]
    obj = Objective(cs, case_dir, objective_weights)
    info = {"controls": [f"{k}:{u}" for k, u in binding.controls], "values": base}
    try:
        # the case as bound (detached values): the residual of record and the tape
        binding.bind(vals)
        xg = x.clone().requires_grad_(True)
        leaves = [v.clone().requires_grad_(True) for v in vals]
        J, terms = obj(xg, binding.explicit(leaves))
        grads = torch.autograd.grad(J, [xg] + leaves, allow_unused=True)
        g = grads[0].detach()
        explicit = [0.0 if gg is None else float(gg.detach()) for gg in grads[1:]]
        info["terms"] = {k: float(v.detach()) for k, v in terms.items()}
        J_val = float(J.detach())
        del xg, leaves, J, terms, grads
        with torch.no_grad():
            r = cs.residual(x)
            info["residual_max_abs"] = float(r.abs().max())
            del r
        _free_warp_scratch(cs)
        lam, sinfo = solve_adjoint(cs, x, g, apply_t=None, method=method, precond=precond,
                                   shift_cfl=shift_cfl, camg_kwargs=camg_kwargs, rtol=rtol, restart=restart,
                                   max_cycles=max_cycles, inner_rtol=inner_rtol, inner_its=inner_its,
                                   coarse_boxes=coarse_boxes, coarse_fields=tuple(coarse_fields),
                                   dual_time=dual_time, amg_mode=amg_mode,
                                   p_reg=p_reg, outer=outer, log=log)
        info.update(sinfo)
        if info["achieved_rel_residual"] > rtol and not accept_unconverged:
            raise RuntimeError(f"adjoint reached {info['achieved_rel_residual']:.3e} against {rtol:.1e}")
        # lambda^T dr/dtheta through the bound leaves
        t0 = time.perf_counter()
        leaves = [v.clone().requires_grad_(True) for v in vals]
        with torch.enable_grad(), torch_residual(cs):
            binding.bind(leaves)
            # x as a grad-tracking leaf too: the forward then clones the field views it writes the bound boundary
            # values into (a detached x makes those in-place writes land on a leaf)
            rr = cs.residual(x.detach().clone().requires_grad_(True))
            vjp = torch.autograd.grad(rr, leaves, lam, allow_unused=True)
        if any(v is None for v in vjp):
            raise RuntimeError(f"controls {[c for c, v in zip(info['controls'], vjp) if v is None]} never reached "
                               "the residual")
        _sync(dev)
        info["param_vjp_s"] = time.perf_counter() - t0
        dJ = {c: e - float(v) for c, e, v in zip(info["controls"], explicit, vjp)}
        info["explicit"] = dict(zip(info["controls"], explicit))
        info["lambda"] = lam.detach()
    finally:
        binding.restore()
    info["total_s"] = time.perf_counter() - t_all
    return J_val, dJ, info


def _warp_backend(cs):
    """The Warp backend of ``cs``, attached again when an adjoint's set-up freed it (``_free_warp_scratch`` with
    ``full``: the tangent after an adjoint at the same linearisation, HallProblem 2026-11-13)."""
    if getattr(cs, "_warp", None) is None:
        from FVsolver.applications.buoyantBoussinesqpicoext_kepsilon_warp import attach_warp_dc
        attach_warp_dc(cs)
    return cs._warp


# ------------------------------------------------------------------------------------------------------ checks
def check_transpose(cs, x, apply_t, *, seed=20261107, k=2):
    """``|w . (J v) - (J^T w) . v| / (|w| |J v|)`` with ``J v`` the Warp backend's exact JVP (implicit ``d_f``) and
    ``J^T w`` the torch tape, for ``k`` random pairs; and the torch vs Warp residual at ``x``."""
    gen = torch.Generator(device=cs.device).manual_seed(seed)
    out = []
    W = _warp_backend(cs).W
    for _ in range(k):
        v = torch.randn(x.numel(), generator=gen, device=cs.device, dtype=torch.float64)
        w = torch.randn(x.numel(), generator=gen, device=cs.device, dtype=torch.float64)
        _, Jv = W.jvp(x, v.reshape(1, -1), frozen=False)
        a = float(w @ Jv[0])
        b = float(apply_t(w) @ v)
        out.append(abs(a - b) / max(float(w.norm() * Jv[0].norm()), 1e-300))
    with torch.no_grad():
        rw = cs._warp.rows(x)
        with torch_residual(cs):
            rt = cs.residual(x)
    return {"dot_rel": out, "residual_gap_max": float((rw - rt).abs().max()),
            "residual_max": float(rt.abs().max())}


def check_param_derivative(cs, case_dir, x, controls, lam, h=(1e-3, 1e-4)):
    """``lambda . dr/dtheta`` by autograd vs a central difference of the torch residual in each control."""
    binding = ControlBinding(cs, case_dir, controls)
    dev = cs.device
    out = {}
    try:
        for i, c in enumerate(binding.controls):
            vals = [torch.tensor(v, dtype=torch.float64, device=dev) for v in binding.base]
            leaves = [v.clone().requires_grad_(True) for v in vals]
            with torch.enable_grad(), torch_residual(cs):
                binding.bind(leaves)
                ad = float(torch.autograd.grad(cs.residual(x.detach().clone().requires_grad_(True)), leaves[i],
                                               lam)[0])
            fd = []
            for hh in h:
                vals_p = list(vals)
                vals_m = list(vals)
                vals_p[i] = vals[i] + hh
                vals_m[i] = vals[i] - hh
                with torch.no_grad(), torch_residual(cs):
                    binding.bind(vals_p)
                    rp = cs.residual(x)
                    binding.bind(vals_m)
                    rm = cs.residual(x)
                fd.append(float(lam @ (rp - rm)) / (2 * hh))
            out[f"{c[0]}:{c[1]}"] = {"autograd": ad, "central": fd}
    finally:
        binding.restore()
    return out
