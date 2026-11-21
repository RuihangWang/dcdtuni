"""The data-centre hall solver (L1: the coarse level of the hall mesh; HF: the mesh itself) behind the steady-framework
contract (``steady_framework.SteadyProblem``; docs/research/2026-11-13-unified-steady-framework.md).

Parameters ``theta``: per named cooling unit its supply temperature [C] and its supply flow [m^3/s], in the order
``T_supply:<unit>...`` then ``flow:<unit>...``. The case's inlet turbulence follows the flow through its own inlet
conditions (intensity / mixing length: k ~ Q^2, epsilon ~ Q^3), as ``dc_adjoint.ControlBinding`` differentiates it.

* ``solve(theta)``: the hall recipe (``entries/dc_foam_solve.py``, the solver ISPO uses), in a child process, on a
  working copy of the prepared run directory whose supply conditions carry ``theta``; started from the previous
  solve's fields when there is one (``LPCNS_DC_INIT_FROM``). The certificate is the recipe's own stop verdict.
* linearisation at an accepted ``(u, theta)``: the solver is built from the converted case of that solve (its
  boundary conditions are ``theta``'s), so ``J v`` is the Warp backend's exact JVP, ``J^T w`` the taped torch VJP,
  ``r_theta`` the ``ControlBinding`` derivatives at offset 0 / fraction 1, divided by the flow for the flows.
* ``observe``: ``Post_process.dc_qoi.DCQuantities`` -- unit return flux-mean and server intake area-mean
  temperatures [C], differentiable in the state.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import torch

from .steady_framework import Certificate

REPO = Path(__file__).resolve().parents[3]
from .block_amg import SinvStabilizer
TRANSPOSE_SETTINGS = dict(method="fgmres", precond="compact", amg_mode="picard", p_reg=1e-3, shift_cfl=3000.0,
                          inner_its=60, inner_rtol=1e-2, coarse_boxes=64, restart=150, max_cycles=4, rtol=1e-6,
                          outer="exact")   # the adjoint settings that passed on 2026-11-08 / 11-12
TANGENT_SETTINGS = dict(precond="compact", amg_mode="picard", p_reg=1e-3, shift_cfl=3000.0, inner_its=60,
                        inner_rtol=1e-2, coarse_boxes=64, restart=150, max_cycles=4, rtol=1e-6)
_UA_CAMG = dict(smooth_prolongation=False, restriction="transpose", gs_omega=0.8, l1=True)
_UA_CAMG_T = dict(_UA_CAMG, modulator=SinvStabilizer("floor", 1.0), diag_eps=1e-2)
LARGE_HALL_TRANSPOSE_SETTINGS = dict(
    method="fgmres", precond="compact", amg_mode="picard", p_reg=0.05, shift_cfl=1e9,
    amg_cfl=1.0, amg_cfl_T=1.0, camg_kwargs=_UA_CAMG_T, inner_its=60, inner_max_cycles=1,
    inner_rtol=1e-3, coarse_boxes=256, restart=60, max_cycles=2, rtol=1e-5,
    equilibrate_fields=False, fp64_basis=True, outer="exact",
)
LARGE_HALL_TANGENT_SETTINGS = dict(
    precond="compact", amg_mode="picard", p_reg=0.05, shift_cfl=1e9, amg_cfl=2.0,
    amg_cfl_T=200.0, camg_kwargs=_UA_CAMG, inner_its=60, inner_max_cycles=1, inner_rtol=1e-3,
    coarse_boxes=256, restart=60, max_cycles=2, rtol=1e-5, equilibrate_fields=True,
    fp64_basis=True,
)


def _set_patch_entry(path: Path, patch: str, key: str, value: str) -> bool:
    """Replace ``key <old>;`` inside ``patch { ... }`` of an OpenFOAM field file; True when found."""
    text = path.read_text()
    m = re.search(r"(\n\s*" + re.escape(patch) + r"\s*\n\s*\{)(.*?)(\n\s*\})", text, flags=re.S)
    if not m:
        return False
    body = m.group(2)
    nb, n = re.subn(r"(\n\s*" + re.escape(key) + r"\s+)[^;]*;", lambda mm: mm.group(1) + value + ";", body)
    if not n:
        return False
    path.write_text(text[:m.start(2)] + nb + text[m.end(2):])
    return True


# L1 (agglomerated mesh) settings: volume-ratio grading 50, one coarse face per dominant normal direction, aspect
# limit 8, and the smoothed flow-rate outlet switch (docs/research/2026-11-13-unified-steady-framework.md)
L1_RECIPE_ENV = {"LPCNS_DC_AGGLOMERATE_VRATIO": "50", "LPCNS_DC_AGGLOMERATE_SPLIT_DIR": "1",
                 "LPCNS_DC_AGGLOMERATE_ASPECT": "8", "LPCNS_WARP_DC_FRO_BAND": "0.05"}


class HallProblem:
    def __init__(self, run_dir, work_dir, units, *, agglomerate=0.4, device="cuda:0", gpu=None, python=None,
                 extra_env=None, max_iterations=3000, self_stop=True, log=print):
        self.base = Path(run_dir).resolve()
        self.work = Path(work_dir).resolve()
        self.units = list(units)
        self.param_names = tuple([f"T_supply:{u}" for u in self.units] + [f"flow:{u}" for u in self.units])
        self.agglomerate = agglomerate
        self.device = torch.device(device)
        self.gpu = gpu
        self.python = python or sys.executable
        self.extra_env = dict(extra_env or {})
        self.max_iterations = int(max_iterations)
        # False: march max_iterations steps instead of stopping at the recipe's plateau rule (the adjoint of the coarse
        # level converges only on a state past the plateau: 1.5e-6 after 2000 steps against 9.2e-3, 2026-11-13)
        self.self_stop = bool(self_stop)
        self.log = log or (lambda *_: None)
        self.work.mkdir(parents=True, exist_ok=True)
        self.dmap = json.load(open(self._case_map())) if self._case_map().exists() else None
        self.n_solves = 0
        self._prev = None            # (fields dir, time) of the last accepted solve
        self._lin = None             # (key, cs, binding, quantities) at the current linearisation point
        self._solved = []            # [(state, theta, run)] of the last accepted solves (newest last)

    # ------------------------------------------------------------------ the case's units and their base values
    def _case_map(self) -> Path:
        return self.base / "gpuNewton" / "case" / "dctwin_map.json"

    def nominal(self) -> torch.Tensor:
        """theta of the prepared run directory (its supply temperature and flow per unit)."""
        T, Q = [], []
        for u in self.units:
            patch = self._supply_patch(u)
            t = re.search(r"\n\s*" + re.escape(patch) + r"\s*\n\s*\{.*?value\s+uniform\s+([-0-9.eE+]+);",
                          (self.base / "processor0" / "0" / "T").read_text(), flags=re.S)
            q = re.search(r"\n\s*" + re.escape(patch) + r"\s*\n\s*\{.*?volumetricFlowRate\s+([-0-9.eE+]+);",
                          (self.base / "processor0" / "0" / "U").read_text(), flags=re.S)
            T.append(float(t.group(1)) - 273.15)
            Q.append(float(q.group(1)))
        return torch.tensor(T + Q, dtype=torch.float64)

    def _supply_patch(self, unit: str) -> str:
        req = json.load(open(self.base / "gpuNewton" / "request.json"))
        cand = [p for p in req.get("supplyPatches", []) if p.startswith(unit + "_")]
        if len(cand) != 1:
            raise KeyError(f"unit {unit}: supply patch among {req.get('supplyPatches')}")
        return cand[0]

    # ------------------------------------------------------------------ forward
    def solve(self, theta, u0=None):
        t0 = time.perf_counter()
        theta = theta.detach().to(torch.float64).cpu()
        run = self.work / f"solve_{self.n_solves:03d}"
        if run.exists():
            shutil.rmtree(run)
        shutil.copytree(self.base, run, symlinks=True, ignore=shutil.ignore_patterns("gpuNewton"))
        (run / "gpuNewton").mkdir()
        shutil.copy(self.base / "gpuNewton" / "request.json", run / "gpuNewton" / "request.json")
        nu = len(self.units)
        for i, u in enumerate(self.units):
            patch = self._supply_patch(u)
            for proc in sorted(run.glob("processor*")):
                _set_patch_entry(proc / "0" / "T", patch, "value", f"uniform {float(theta[i]) + 273.15!r}")
                _set_patch_entry(proc / "0" / "U", patch, "volumetricFlowRate", repr(float(theta[nu + i])))
        state = run / "gpuNewton" / "state.pt"
        env = {**os.environ, "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4", "LPCNS_DC_KEEP_CASE": "1",
               **self.extra_env}
        if not self.self_stop:
            # past the caller's plateau the march on a flow with an unsteady part can stall; that state is kept
            env.setdefault("LPCNS_ODIL_STALL_STOP", "1e-2")
        if self.gpu:
            env["CUDA_VISIBLE_DEVICES"] = self.gpu
        if self.agglomerate:
            env["LPCNS_DC_AGGLOMERATE"] = str(self.agglomerate)
            # the L1 recipe that converges on all six families (2026-11-13 17:51, design doc "L1 recipe")
            for k_, v_ in L1_RECIPE_ENV.items():
                env.setdefault(k_, v_)
        if self._prev is not None:
            env["LPCNS_DC_INIT_FROM"] = f"{self._prev[0]}:{self._prev[1]}"
        req = json.load(open(run / "gpuNewton" / "request.json"))
        req["maxIterations"] = self.max_iterations
        if not self.self_stop:
            req["selfStop"] = False
        (run / "gpuNewton" / "request.json").write_text(json.dumps(req, indent=1))
        cmd = [self.python, str(REPO / "src" / "entries" / "dc_foam_solve.py"), "--run", str(run), "--no-service",
               "--extra", "--save-state-to", str(state)]
        with open(run / "solve.out", "w") as so, open(run / "solve.err", "w") as se:
            rc = subprocess.run(cmd, stdout=so, stderr=se, env=env).returncode
        self.n_solves += 1
        res = json.load(open(run / "gpuNewton" / "result.json")) if (run / "gpuNewton" / "result.json").exists() else {}
        wall = time.perf_counter() - t0
        ok = rc == 0 and res.get("ok") and state.exists()
        if not ok:
            return (u0 if u0 is not None else torch.zeros(1, dtype=torch.float64)), Certificate(
                False, float("inf"), 0, "hall recipe", str(res.get("error", f"exit {rc}"))[:300], wall)
        x = torch.load(state, map_location=self.device)
        x = (x["x"] if isinstance(x, dict) else x).to(torch.float64).reshape(-1)
        r = res["result"]
        self._prev = (run, r["time"])
        self._solved = (self._solved + [(x, theta.clone(), run)])[-16:]   # LM trials must not evict the accepted state
        last = r.get("last") or {}
        self.log(f"hall solve {self.n_solves}: {r.get('iterations')} steps, {wall:.1f} s, {r.get('stopReason')}")
        return x, Certificate(True, float(last.get("residual_max", last.get("merit", 0.0)) or 0.0),
                              int(r.get("iterations") or 0), "hall recipe", str(r.get("stopReason")), wall,
                              {"run": str(run), "time": r["time"], "cells": res.get("cells")})

    # ------------------------------------------------------------------ linearisation
    def _lookup(self, u):
        """The solve whose state equals ``u`` (by value: callers pass detached copies)."""
        ud = u.detach()
        for x, th, run in reversed(self._solved):
            if x.shape == ud.shape and x.device == ud.device and torch.equal(x, ud):
                return x, th, run
        return None

    def _linearise(self, u, theta=None):
        hit = self._lookup(u)
        if hit is None:
            raise ValueError("linearise at a state returned by this problem's solve()")
        x, th, run = hit
        if theta is not None and (th - theta.detach().cpu()).abs().max() > 1e-12:
            raise ValueError("the state was solved at another theta")
        if self._lin is not None and self._lin[0] is x:
            return self._lin[1:]
        from . import dc_adjoint as A
        from entries import dc_foam_solve as D
        for k in ("LPCNS_FACE_WEIGHTS", "LPCNS_WARP_DC_MWI_GRADW"):      # the options that define the residual
            os.environ[k] = D.RECIPE_ENV[k]
        self._lin = None
        import gc
        gc.collect()
        if self.device.type == "cuda":
            torch.cuda.empty_cache()
        case = run / "gpuNewton" / "case"
        cs = A.build_solver(case, self.device)
        cs.resolve_gauge(x.clone())
        controls = [("T_supply", un) for un in self.units] + [("flow_fraction", un) for un in self.units]
        binding = A.ControlBinding(cs, case, controls)
        from Post_process.dc_qoi import DCQuantities
        q = DCQuantities(str(case), device=self.device, dtype=torch.float64)
        self._lin = (x, cs, binding, q, case, th)
        return self._lin[1:]

    def _scale(self, theta):
        nu = len(self.units)
        return torch.cat((torch.ones(nu, dtype=torch.float64), 1.0 / theta[nu:].detach().cpu())).to(self.device)

    def residual(self, u, theta):
        from . import dc_adjoint as A
        cs, binding, *_ = self._linearise(u, theta)
        with torch.no_grad(), A.torch_residual(cs):
            return cs.residual(u)

    def jvp(self, u, theta, v):
        cs, *_ = self._linearise(u, theta)
        _, Jv = cs._warp.W.jvp(u, v.reshape(1, -1), frozen=False)
        return Jv[0]

    def vjp(self, u, theta, w):
        from . import dc_adjoint as A
        cs, *_ = self._linearise(u, theta)
        apply_t, _ = A.transpose_tape(cs, u)
        return apply_t(w)

    def _param_residual(self, u, theta, values):
        from . import dc_adjoint as A
        cs, binding, *_ = self._linearise(u, theta)
        binding.bind(values)
        try:
            with torch.enable_grad(), A.torch_residual(cs):
                return cs.residual(u.detach().clone().requires_grad_(True))
        finally:
            binding.restore()

    def param_vjp(self, u, theta, w):
        nu = len(self.units)
        leaves = [torch.tensor(0.0 if i < nu else 1.0, dtype=torch.float64, device=self.device, requires_grad=True)
                  for i in range(2 * nu)]
        r = self._param_residual(u, theta, leaves)
        g = torch.autograd.grad(r, leaves, w, allow_unused=True)
        out = torch.stack([torch.zeros((), dtype=torch.float64, device=self.device) if x is None else x for x in g])
        return (out * self._scale(theta)).detach().to(theta.device)

    def param_jvp(self, u, theta, d):
        # r_theta d by the double-backward identity on the bound residual
        nu = len(self.units)
        leaves = [torch.tensor(0.0 if i < nu else 1.0, dtype=torch.float64, device=self.device, requires_grad=True)
                  for i in range(2 * nu)]
        r = self._param_residual(u, theta, leaves)
        w = torch.zeros_like(r, requires_grad=True)
        g = torch.autograd.grad(r, leaves, w, create_graph=True, allow_unused=True)
        dd = (d.to(self.device) * self._scale(theta))
        s = sum((gi * dd[i]).sum() for i, gi in enumerate(g) if gi is not None)
        (out,) = torch.autograd.grad(s, w)
        return out.detach()

    def linear_solve(self, u, theta, b, *, transpose=False):
        from . import dc_adjoint as A
        cs, *_ = self._linearise(u, theta)
        b = b.to(self.device)
        if transpose:
            st = {k: v for k, v in TRANSPOSE_SETTINGS.items()}
            st.pop("method")
            return A.solve_adjoint(cs, u, b, method="fgmres", log=self.log, **st)
        return A.solve_tangent(cs, u, b, log=self.log, **TANGENT_SETTINGS)

    def observe(self, u, theta):
        cs, binding, q, case, th = self._linearise(u, theta)
        n = int(cs.n_cell)
        out = q(u[:3 * n].reshape(3, n).T, u[4 * n:5 * n])
        ret = torch.stack([out[f"return_T:{un}"].reshape(()) for un in q.acus]) - 273.15
        inl = torch.stack([out[f"inlet_T:{s}"].reshape(()) for s in q.servers]) - 273.15
        return {"unit_return_c": ret, "server_inlet_c": inl}
