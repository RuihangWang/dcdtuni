"""L1 (coarse dcRANS) forward JFNK and steady/transient JFGNK problems of the data-centre hall.

They share the ``steady_framework.py`` ``SteadyProblem`` / ``TransientStepModel`` / ``fit`` contracts:
  - L1 steady:    ``HallSteadyProblem`` (``SnapshotCollectionProblem`` over ``HallSingleSnapshotProblem``)
  - L1 transient: ``HallStepModel`` + ``HallTrajectoryProblem`` (``--phys-dt`` BDF1 + ``diag=D`` adjoint/tangent),
    or a whole time window at once: ``rans_window.RansWindowProblem`` (every residual and product of the window in
    one batched Warp launch).
L0 is the zonal pressure-enthalpy network on the L0 mesh, with its own residual: ``FVsolver/applications/
zonal_network*.py`` and ``Pipeline/CoupledNewton/zonal_window.py`` (the same parameter map and observation names).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

import torch

from Pipeline.CoupledNewton.dc_adjoint import (
    _clear_solver_autograd,
    _empty_cuda_cache,
    install_capture_safe_empty_cache,
    _warp_backend,
    build_solver,
    pseudo_time_shift,
    solve_adjoint,
    solve_tangent,
    torch_residual,
    transpose_tape,
)
# odil_inner, AMG and assembly call torch.cuda.empty_cache directly; see install_capture_safe_empty_cache
install_capture_safe_empty_cache()

from Pipeline.CoupledNewton.hall_problem import (
    L1_RECIPE_ENV,
    LARGE_HALL_TANGENT_SETTINGS,
    LARGE_HALL_TRANSPOSE_SETTINGS,
    TANGENT_SETTINGS,
    TRANSPOSE_SETTINGS,
)
from Pipeline.CoupledNewton.patch_binding import (
    CSPatchBinding,
    HallProbeObserver,
    ParameterSpec,
    PatchParameterMap,
)
from Pipeline.CoupledNewton.steady_framework import (
    Certificate,
    CheckpointedTrajectoryProblem,
    TransientStepModel,
)


__all__ = [
    "HallSingleSnapshotProblem",
    "SnapshotCollectionProblem",
    "HallSteadyProblem",
    "HallStepModel",
    "HallTrajectoryProblem",
    "default_datacentre_param_specs",
]


def default_datacentre_param_specs(
    *,
    include_empty: bool = False,
    include_rev: bool = False,
    include_wall: bool = False,
    include_tau: bool = False,
    q100_prior: float = 1.0,
    q100_sigma: float = 0.25,
    q100_bounds: tuple[float, float] = (0.3, 3.0),
) -> list[ParameterSpec]:
    """Standard calibration parameter specifications for data-centre thermal calibration."""
    specs = [
        ParameterSpec("P_scale", 1.5, 0.5, 0.5, 3.5, "heat_scale", "IT rack power scale factor"),
        ParameterSpec("dTsrv", 12.0, 4.0, 3.0, 28.0, "server_rise", "Nominal server air temperature rise [K]"),
        ParameterSpec("Q100", q100_prior, q100_sigma, q100_bounds[0], q100_bounds[1], "acu_flow", "ACU full-speed airflow"),
        ParameterSpec("bT", 0.0, 1.0, -5.0, 5.0, "supply_offset", "ACU effective supply temperature offset [K]"),
        ParameterSpec("lsBlank", -1.5, 1.0, -3.5, -0.05, "porous_log10", "log10 blanking-panel open-area ratio"),
    ]
    if include_empty:
        specs.append(ParameterSpec("lsEmpty", -1.5, 1.0, -3.5, -0.05, "porous_log10", "log10 empty-rack open-area ratio"))
    if include_rev:
        specs.append(ParameterSpec("lqRev", -0.7, 0.8, -3.0, 0.7, "rev_log10_flow", "log10 stopped-ACU reverse flow [m3/s]"))
    if include_wall:
        specs.append(ParameterSpec("dTwall", 0.0, 3.0, -10.0, 15.0, "wall_temp", "Outer wall/ceiling temperature offset [K]"))
    if include_tau:
        specs.append(ParameterSpec("ltau", 0.0, 1.0, -1.5, 2.5, "thermal_tau", "log10 effective thermal capacitance multiplier"))
    return specs


# --------------------------------------------------------------------------- L1 Hall Subprocess & Cache Helpers
def _solver_repo_src() -> str:
    return str(Path(__file__).resolve().parents[2])


def _load_template_request(template_dir: Path) -> dict[str, Any]:
    req_path = Path(template_dir) / "gpuNewton" / "request.json"
    if req_path.is_file():
        return json.loads(req_path.read_text())
    return {}


def _run_dc_foam_solve(
    run_dir: Path,
    *,
    request: dict[str, Any],
    extra: Sequence[str] = (),
    gpu: int | str | None = None,
    python_exe: str | None = None,
    env_extra: dict[str, str | None] | None = None,
    save_state_to: Path | None = None,
    init_state_path: Path | None = None,
    timeout: float = 3600.0,
) -> tuple[Path, Certificate]:
    """Run ``entries.dc_foam_solve`` as a subprocess and return ``(state_path, Certificate)``."""
    t0 = time.perf_counter()
    gn = run_dir / "gpuNewton"
    gn.mkdir(parents=True, exist_ok=True)
    req_path = gn / "request.json"
    req_path.write_text(json.dumps(request, indent=2))
    state_out = Path(save_state_to) if save_state_to is not None else (gn / "state.pt")
    log_path = gn / "solve_driver.log"

    env = dict(os.environ)
    src_root = _solver_repo_src()
    env["PYTHONPATH"] = src_root if not env.get("PYTHONPATH") else f"{src_root}:{env['PYTHONPATH']}"
    for k, v in L1_RECIPE_ENV.items():
        env.setdefault(k, v)
    env.setdefault("LPCNS_DC_KEEP_CASE", "1")
    env.setdefault("LPCNS_ODIL_NUT_MAX", "0.75")
    env.setdefault("LPCNS_WARP_DC_FRO_BAND", "0.05")
    if gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    if init_state_path is not None and Path(init_state_path).is_file():
        env["LPCNS_ODIL_INIT_STATE"] = str(Path(init_state_path).resolve())
    if env_extra:
        for k, v in env_extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = str(v)

    py = python_exe or sys.executable
    cmd = [
        py, "-m", "entries.dc_foam_solve",
        "--run", str(run_dir),
        "--request", str(req_path),
        "--no-service",
        "--extra", *list(extra), "--save-state-to", str(state_out),
    ]
    with open(log_path, "w") as lf:
        proc = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)

    result_path = gn / "result.json"
    status_path = gn / "status.json"
    if result_path.is_file():
        doc = json.loads(result_path.read_text())
    elif status_path.is_file():
        doc = json.loads(status_path.read_text())
    else:
        doc = {}
    inner = doc.get("result") or {}
    phys = inner.get("physics") or {}
    phys_checks = phys.get("checks") or {}
    reason = str(inner.get("stopReason") or doc.get("error") or doc.get("reason") or f"rc={proc.returncode}")
    its = int(inner.get("iterations", doc.get("iterations", 0)) or 0)
    is_transient = float(request.get("physDt") or 0.0) > 0.0
    if is_transient:
        phys_ok = all(bool(phys_checks.get(k, True)) for k in ("mass", "speed", "turbulence"))
        ok = bool(
            proc.returncode == 0
            and doc.get("ok")
            and state_out.is_file()
            and reason.startswith("physical time step")
            and phys_ok
        )
    else:
        phys_ok = bool(phys.get("ok", True))
        ok = bool(
            proc.returncode == 0
            and doc.get("ok")
            and state_out.is_file()
            and not reason.startswith("endTime")
            and phys_ok
        )
    res = float(doc.get("residual", doc.get("finalResidual", 0.0 if ok else float("nan"))))
    return state_out, Certificate(ok, res, its, "dc_foam_solve", reason, time.perf_counter() - t0)


class _SingleSlotSolverCache:
    """Single-slot GPU solver cache evicting the previous built solver before loading another topology."""

    def __init__(self, device: str = "cuda:0"):
        self.device = torch.device(device)
        self._entry: tuple[tuple, Any, CSPatchBinding, torch.Tensor] | None = None

    def get(self, case_dir: Path, stopped_key: tuple, wall_active: bool, TRef: float):
        key = (str(case_dir.resolve()), tuple(sorted(stopped_key)), bool(wall_active))
        if self._entry is not None and self._entry[0] == key:
            return self._entry[1], self._entry[2], self._entry[3]
        if self._entry is not None:
            self._entry = None
            if self.device.type == "cuda":
                _empty_cuda_cache()
        from entries.dc_foam_solve import RECIPE_ENV
        # In-process JFGNK requires float64 compact Jacobian blocks (LPCNS_WARP_DC_VALS32=0) and persistent buffers
        os.environ["LPCNS_WARP_DC_VALS32"] = "0"
        os.environ.setdefault("LPCNS_WARP_DC_FREE", "0")
        for _ek, _ev in (
            ("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"),
            ("LPCNS_FACE_WEIGHTS", "normal"),
            ("LPCNS_WARP_DC_MWI_GRADW", "volume"),
            ("LPCNS_ODIL_NUT_MAX", "0.75"),
            ("LPCNS_WARP_DC_FRO_BAND", "0.05"),
            ("LPCNS_ODIL_LAM_FLOOR_CAP", "8"),
        ):
            os.environ.setdefault(_ek, _ev)
        for _ek, _ev in RECIPE_ENV.items():
            if _ek not in ("LPCNS_WARP_DC_VALS32", "LPCNS_WARP_DC_FREE"):
                os.environ.setdefault(_ek, _ev)
        cs = build_solver(str(case_dir), device=self.device)
        binding = CSPatchBinding(cs, TRef=TRef)
        Wr = getattr(getattr(cs, "_warp", None), "W", None)
        if Wr is not None and hasattr(Wr, "c_vol"):
            import warp as wp
            vol = wp.to_torch(Wr.c_vol).to(torch.float64).reshape(-1)[: int(cs.n_cell)].clone()
        else:
            vol = cs.solver.cell_volume.reshape(-1)[: int(cs.n_cell)].to(torch.float64).clone()
        self._entry = (key, cs, binding, vol)
        return cs, binding, vol


def _large_hall_flow_shift(cs: Any, u_d: torch.Tensor) -> torch.Tensor | None:
    if getattr(cs, "_gauge_resolved_n", None) != int(cs.n_cell):
        cs.resolve_gauge(u_d.clone())
        cs._gauge_resolved_n = int(cs.n_cell)
    n = int(cs.n_cell)
    if n <= 100_000:
        return None
    s_f = pseudo_time_shift(cs, u_d, 3.0).clone()
    if int(cs.num_fields) >= 5:
        s_f[4 * n : 5 * n] = 0.0
    return s_f


# --------------------------------------------------------------------------- L1 Steady Problem
class HallSingleSnapshotProblem:
    """Steady JFGNK problem for one snapshot ``k`` bound through ``PatchParameterMap``."""

    def __init__(
        self,
        param_map: PatchParameterMap,
        snapshot_idx: int,
        template_dir: str | Path,
        work_dir: str | Path,
        observer: HallProbeObserver,
        *,
        agglomerate: float = 0.4,
        pure_box: bool = True,
        vratio: float | str | None = None,
        split_dir: int | str | None = None,
        aspect: float | str | None = None,
        passes: int | str | None = None,
        layers: int | str | None = None,
        agglomerate_cache: str | Path | None = None,
        device: str = "cuda:0",
        gpu: int | str | None = None,
        python_exe: str | None = None,
        max_iterations: int = 6000,
        fd_step: float = 1e-5,
        units: str = "C",
        solver_cache: _SingleSlotSolverCache | None = None,
        warm_state_path: str | Path | None = None,
        extra_solve_flags: Sequence[str] = (),
        base_request: dict[str, Any] | None = None,
    ):
        self.param_map = param_map
        self.param_names = tuple(param_map.param_names)
        self.k = int(snapshot_idx)
        self.template_dir = Path(template_dir)
        self.work_dir = Path(work_dir)
        self.observer = observer
        self.agglomerate = float(agglomerate) if agglomerate else 0.0
        self.pure_box = bool(pure_box)
        self.vratio = vratio
        self.split_dir = split_dir
        self.aspect = aspect
        self.passes = passes
        self.layers = layers
        self.agglomerate_cache = Path(agglomerate_cache) if agglomerate_cache else None
        self.device = torch.device(device)
        self.gpu = gpu
        self.python_exe = python_exe
        self.max_iterations = int(max_iterations)
        self.fd_step = float(fd_step)
        self.units = units
        self._cache = solver_cache if solver_cache is not None else _SingleSlotSolverCache(device=str(self.device))
        self.warm_state_path = Path(warm_state_path) if warm_state_path else None
        self.extra_solve_flags = list(extra_solve_flags)
        self.base_request = dict(base_request or {})

        self._solve_count = 0
        self._state_store: list[tuple[torch.Tensor, torch.Tensor, Path]] = []
        self._last_converged_state: torch.Tensor | None = None
        self._rtheta_cache: tuple[int, tuple, torch.Tensor] | None = None

    def _lookup(self, u: torch.Tensor) -> tuple[torch.Tensor, Path]:
        u_cpu = u.detach().to("cpu", dtype=torch.float64).reshape(-1)
        for us, _, rdir in reversed(self._state_store):
            if us.numel() == u_cpu.numel() and torch.equal(us, u_cpu):
                return us, rdir
        raise ValueError("Linearisation requested at a state not produced by this problem's solve()")

    def _get_solver(self, run_dir: Path, vals: dict):
        stopped = tuple(sorted(vals.get("stopped_acus", {}).keys()))
        wall_active = bool(vals.get("wall_T"))
        case_sub = run_dir / "gpuNewton" / "case"
        cdir = case_sub if case_sub.is_dir() else run_dir
        cs, binding, _ = self._cache.get(cdir, stopped, wall_active, self.param_map.TRef)
        self.observer.bind_case_dir(cdir)
        return cs, binding

    def solve(self, theta: torch.Tensor, u0: torch.Tensor | None = None) -> tuple[torch.Tensor, Certificate]:
        th = theta.detach().to("cpu", dtype=torch.float64).reshape(-1)
        self._solve_count += 1
        h = hashlib.sha256(th.numpy().tobytes()).hexdigest()[:10]
        run_dir = self.work_dir / f"snap_{self.k:04d}_s{self._solve_count:03d}_{h}"
        vals = self.param_map.apply_to_foam_dir(run_dir, self.k, th, template_dir=self.template_dir)

        init_path = self.warm_state_path
        if u0 is not None:
            init_path = run_dir / "gpuNewton" / "init_state.pt"
            init_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(u0.detach().to("cpu", dtype=torch.float64), init_path)
        elif self._last_converged_state is not None:
            init_path = run_dir / "gpuNewton" / "init_state.pt"
            init_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(self._last_converged_state, init_path)

        req = _load_template_request(self.template_dir)
        req.setdefault("stopToleranceK", 0.1)
        req.setdefault("changeMinK", 0.0)
        req.setdefault("plateauMode", "mean")
        req.setdefault("selfStop", True)
        req.update(self.base_request)
        if "maxIterations" not in self.base_request:
            req["maxIterations"] = int(self.max_iterations)
        if vals.get("wall_T"):
            req["declaredHeatW"] = None
        elif "declared_heat_W" in vals and "declaredHeatW" not in self.base_request:
            req["declaredHeatW"] = float(vals["declared_heat_W"])
        if self.agglomerate > 0:
            req["agglomerateBoxM"] = self.agglomerate
        env_ex: dict[str, str | None] = {}
        if self.pure_box and self.vratio is None and self.aspect is None:
            env_ex.update({
                "LPCNS_DC_AGGLOMERATE_VRATIO": None,
                "LPCNS_DC_AGGLOMERATE_SPLIT_DIR": None,
                "LPCNS_DC_AGGLOMERATE_ASPECT": None,
            })
        else:
            req["agglomerateAspect"] = True
            if self.vratio is not None:
                env_ex["LPCNS_DC_AGGLOMERATE_VRATIO"] = str(self.vratio)
            if self.split_dir is not None:
                env_ex["LPCNS_DC_AGGLOMERATE_SPLIT_DIR"] = str(self.split_dir)
            if self.aspect is not None:
                env_ex["LPCNS_DC_AGGLOMERATE_ASPECT"] = str(self.aspect)
            else:
                env_ex.setdefault("LPCNS_DC_AGGLOMERATE_ASPECT", None)
            if self.passes is not None:
                env_ex["LPCNS_DC_AGGLOMERATE_PASSES"] = str(self.passes)
            if self.layers is not None:
                env_ex["LPCNS_DC_AGGLOMERATE_LAYERS"] = str(self.layers)
            for _ak in ("LPCNS_DC_AGGLOMERATE_JUMP", "LPCNS_DC_AGGLOMERATE_NONORTHO", "LPCNS_DC_AGGLOMERATE_DISSOLVE", "LPCNS_DC_AGGLOMERATE_ABSORB"):
                if os.environ.get(_ak) is not None:
                    env_ex[_ak] = os.environ.get(_ak)
        if self.agglomerate_cache is not None:
            is_graded = (not self.pure_box) or (self.vratio is not None) or (self.aspect is not None)
            cache_dir = (
                self.agglomerate_cache
                if (is_graded or self.agglomerate_cache.name == "purebox")
                else self.agglomerate_cache / "purebox"
            )
            env_ex["LPCNS_DC_AGGLOMERATE_CACHE"] = str(cache_dir)
        if init_path is not None and init_path.is_file():
            env_ex["LPCNS_ODIL_BETA_RAMP"] = "0"
        extra = list(self.extra_solve_flags)
        state_path, cert = _run_dc_foam_solve(
            run_dir,
            request=req,
            extra=extra,
            gpu=self.gpu,
            python_exe=self.python_exe,
            env_extra=env_ex,
            init_state_path=init_path,
        )
        if not cert.converged:
            return torch.zeros(1, dtype=torch.float64), cert
        self.observer.bind_case_dir(run_dir)
        x = torch.load(state_path, map_location="cpu", weights_only=False).to(torch.float64).reshape(-1)
        self._last_converged_state = x.clone()
        self._state_store = (self._state_store + [(x.clone(), th.clone(), run_dir)])[-16:]
        return x, cert

    def _r_theta(self, u: torch.Tensor, theta: torch.Tensor):
        _, run_dir = self._lookup(u)
        th_cpu = theta.detach().to("cpu", dtype=torch.float64).reshape(-1)
        vals = self.param_map.evaluate(self.k, th_cpu)
        cs, binding = self._get_solver(run_dir, vals)
        th_key = tuple(round(float(v), 12) for v in th_cpu.tolist())
        uid = id(u)
        if self._rtheta_cache is not None and self._rtheta_cache[0] == uid and self._rtheta_cache[1] == th_key:
            return cs, binding, vals, self._rtheta_cache[2]
        u_dev = u.to(self.device, dtype=torch.float64).reshape(-1)
        r_th = binding.r_theta_columns(u_dev, lambda t: self.param_map.evaluate(self.k, t), th_cpu, fd_step=self.fd_step)
        self._rtheta_cache = (uid, th_key, r_th)
        return cs, binding, vals, r_th

    def residual(self, u: torch.Tensor, theta: torch.Tensor) -> torch.Tensor:
        vals = self.param_map.evaluate(self.k, theta)
        _, run_dir = self._lookup(u) if self._state_store else (u, self.template_dir)
        cs, binding = self._get_solver(run_dir, vals)
        return binding.residual(u, vals).to(u.device, u.dtype)

    def jvp(self, u: torch.Tensor, theta: torch.Tensor, v: torch.Tensor) -> torch.Tensor:
        vals = self.param_map.evaluate(self.k, theta)
        _, run_dir = self._lookup(u)
        cs, binding = self._get_solver(run_dir, vals)
        binding.bind(vals)
        try:
            u_d = u.to(self.device, dtype=torch.float64).reshape(-1)
            v_d = v.to(self.device, dtype=torch.float64).reshape(-1)
            s_f = _large_hall_flow_shift(cs, u_d)
            with torch.no_grad(), torch_residual(cs):
                W_ = _warp_backend(cs).W
                _, jv = W_.jvp(u_d, v_d.reshape(1, -1), frozen=False)
                out = jv[0].detach()
            if s_f is not None:
                out = out + s_f * v_d
            return out.to(u.device, u.dtype)
        finally:
            binding.restore()

    def vjp(self, u: torch.Tensor, theta: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        vals = self.param_map.evaluate(self.k, theta)
        _, run_dir = self._lookup(u)
        cs, binding = self._get_solver(run_dir, vals)
        binding.bind(vals)
        try:
            u_d = u.to(self.device, dtype=torch.float64).reshape(-1)
            w_d = w.to(self.device, dtype=torch.float64).reshape(-1)
            s_f = _large_hall_flow_shift(cs, u_d)
            apply, _ = transpose_tape(cs, u_d)
            out = apply(w_d).detach()
            del apply
            _clear_solver_autograd(cs, u_d)
            if s_f is not None:
                out = out + s_f * w_d
            return out.to(u.device, u.dtype)
        finally:
            binding.restore()

    def param_jvp(self, u: torch.Tensor, theta: torch.Tensor, d: torch.Tensor) -> torch.Tensor:
        _, _, _, r_th = self._r_theta(u, theta)
        return (r_th @ d.to(r_th.device, dtype=r_th.dtype).reshape(-1)).to(u.device, u.dtype)

    def param_vjp(self, u: torch.Tensor, theta: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        _, _, _, r_th = self._r_theta(u, theta)
        return (r_th.T @ w.to(r_th.device, dtype=r_th.dtype).reshape(-1)).to(theta.device, theta.dtype)

    def linear_solve(self, u: torch.Tensor, theta: torch.Tensor, b: torch.Tensor, *, transpose: bool = False) -> tuple[torch.Tensor, dict]:
        _, run_dir = self._lookup(u)
        vals = self.param_map.evaluate(self.k, theta)
        cs, binding = self._get_solver(run_dir, vals)
        binding.bind(vals)
        try:
            u_d = u.to(self.device, dtype=torch.float64).reshape(-1)
            b_d = b.to(self.device, dtype=torch.float64)
            s_f = _large_hall_flow_shift(cs, u_d)
            if transpose:
                sol, info = solve_adjoint(cs, u_d, b_d.reshape(-1), diag=s_f, **TRANSPOSE_SETTINGS)
                return sol.detach().to(u.device, u.dtype), info
            sol, info = solve_tangent(cs, u_d, b_d, diag=s_f, **TANGENT_SETTINGS)
            return sol.detach().to(u.device, u.dtype), info
        finally:
            binding.restore()

    def batch_tangents(self, u: torch.Tensor, theta: torch.Tensor, D_cols: torch.Tensor) -> tuple[torch.Tensor, dict]:
        """Solve ``-J^{-1} r_theta D_cols`` across ``M`` columns in one AMG setup."""
        _, _, vals, r_th = self._r_theta(u, theta)
        _, run_dir = self._lookup(u)
        cs, binding = self._get_solver(run_dir, vals)
        D_dev = D_cols.to(r_th.device, dtype=r_th.dtype)
        rhs_rows = -(r_th @ D_dev).T.contiguous()
        binding.bind(vals)
        try:
            u_d = u.to(self.device, dtype=torch.float64).reshape(-1)
            s_f = _large_hall_flow_shift(cs, u_d)
            sol_rows, info = solve_tangent(cs, u_d, rhs_rows, diag=s_f, **TANGENT_SETTINGS)
            return sol_rows.detach().to(u.device, u.dtype), {"linear_solves": int(D_cols.shape[1]), "info": info}
        finally:
            binding.restore()

    def observe(self, u: torch.Tensor, theta: torch.Tensor) -> dict[str, torch.Tensor]:
        vals = self.param_map.evaluate(self.k, theta.detach())
        if self.observer.dq is None and self._state_store:
            _, rdir = self._lookup(u)
            self.observer.bind_case_dir(rdir)
        self.observer.set_snapshot_flows(vals)
        vec = self.observer.observe(u, units=self.units).to(u.device, u.dtype)
        return dict(zip(self.observer.names, vec))


class SnapshotCollectionProblem:
    """Combines multiple independent ``SteadyProblem`` snapshots into one ``SteadyProblem``."""

    def __init__(self, subproblems: Sequence[Any], *, param_names: Sequence[str] | None = None):
        self.subproblems = list(subproblems)
        if not self.subproblems:
            raise ValueError("SnapshotCollectionProblem requires at least one subproblem")
        self.param_names = tuple(self.subproblems[0].param_names if param_names is None else param_names)
        self._last_split: list[int] | None = None

    def _split(self, U: torch.Tensor) -> list[torch.Tensor]:
        K = len(self.subproblems)
        if self._last_split is not None and sum(self._last_split) == U.numel():
            return list(torch.split(U.reshape(-1), self._last_split))
        if U.numel() % K != 0:
            raise ValueError(f"State length {U.numel()} cannot be split evenly across {K} snapshots without prior sizes")
        return list(U.reshape(K, -1).unbind(0))

    def solve(self, theta: torch.Tensor, u0: torch.Tensor | None = None) -> tuple[torch.Tensor, Certificate]:
        t0 = time.perf_counter()
        u0s = None if u0 is None else self._split(u0)
        states, certs = [], []
        for idx, sub in enumerate(self.subproblems):
            uk, cert = sub.solve(theta, None if u0s is None else u0s[idx])
            if not cert.converged:
                return torch.zeros(1, dtype=torch.float64), Certificate(
                    False, cert.residual_inf, len(certs), cert.method, f"snapshot {idx}: {cert.reason}",
                    time.perf_counter() - t0,
                )
            states.append(uk.detach().to("cpu", dtype=torch.float64).reshape(-1))
            certs.append(cert)
        self._last_split = [s.numel() for s in states]
        U = torch.cat(states)
        res_max = max(float(c.residual_inf) for c in certs)
        its_sum = sum(int(c.iterations) for c in certs)
        return U, Certificate(True, res_max, its_sum, "snapshot_collection", "converged", time.perf_counter() - t0)

    def residual(self, U: torch.Tensor, theta: torch.Tensor) -> torch.Tensor:
        return torch.cat([s.residual(uk, theta).to(U.device, U.dtype) for s, uk in zip(self.subproblems, self._split(U))])

    def jvp(self, U: torch.Tensor, theta: torch.Tensor, V: torch.Tensor) -> torch.Tensor:
        return torch.cat([s.jvp(uk, theta, vk).to(U.device, U.dtype) for s, uk, vk in zip(self.subproblems, self._split(U), self._split(V))])

    def vjp(self, U: torch.Tensor, theta: torch.Tensor, W: torch.Tensor) -> torch.Tensor:
        return torch.cat([s.vjp(uk, theta, wk).to(U.device, U.dtype) for s, uk, wk in zip(self.subproblems, self._split(U), self._split(W))])

    def param_jvp(self, U: torch.Tensor, theta: torch.Tensor, d: torch.Tensor) -> torch.Tensor:
        return torch.cat([s.param_jvp(uk, theta, d).to(U.device, U.dtype) for s, uk in zip(self.subproblems, self._split(U))])

    def param_vjp(self, U: torch.Tensor, theta: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        acc = torch.zeros_like(theta)
        for s, uk, wk in zip(self.subproblems, self._split(U), self._split(w)):
            acc = acc + s.param_vjp(uk, theta, wk).to(acc.device, acc.dtype)
        return acc

    def linear_solve(self, U: torch.Tensor, theta: torch.Tensor, b: torch.Tensor, *, transpose: bool = False) -> tuple[torch.Tensor, dict]:
        xs, infos = [], []
        for s, uk, bk in zip(self.subproblems, self._split(U), self._split(b)):
            xk, info = s.linear_solve(uk, theta, bk, transpose=transpose)
            xs.append(xk.to(U.device, U.dtype))
            infos.append(info)
        return torch.cat(xs), {"method": "snapshot_collection", "steps": infos}

    def batch_tangents(self, U: torch.Tensor, theta: torch.Tensor, D_cols: torch.Tensor) -> tuple[torch.Tensor, dict]:
        M = int(D_cols.shape[1])
        blocks = [[] for _ in range(M)]
        solves = 0
        from .steady_framework import tangent as _sf_tangent
        for s, uk in zip(self.subproblems, self._split(U)):
            if hasattr(s, "batch_tangents"):
                du_k, info = s.batch_tangents(uk, theta, D_cols)
                solves += int(info.get("linear_solves", M))
            else:
                cols_k = []
                for m in range(M):
                    du_m, _, _ = _sf_tangent(s, uk, theta, D_cols[:, m], ())
                    cols_k.append(du_m)
                    solves += 1
                du_k = torch.stack(cols_k) if cols_k else uk.new_zeros((0, uk.numel()))
            for m in range(M):
                blocks[m].append(du_k[m].to(U.device, U.dtype))
        return torch.stack([torch.cat(b) for b in blocks]), {"linear_solves": solves}

    def observe(self, U: torch.Tensor, theta: torch.Tensor) -> dict[str, torch.Tensor]:
        obs_list = [s.observe(uk, theta) for s, uk in zip(self.subproblems, self._split(U))]
        return {key: torch.stack([o[key].to(U.device, U.dtype) for o in obs_list]) for key in obs_list[0]}


class HallSteadyProblem(SnapshotCollectionProblem):
    """Multi-snapshot steady JFGNK problem composing independent ``HallSingleSnapshotProblem`` instances."""

    def __init__(
        self,
        param_map: PatchParameterMap,
        snapshot_indices: Sequence[int],
        template_dir: str | Path,
        work_dir: str | Path,
        observer: HallProbeObserver,
        *,
        agglomerate: float = 0.4,
        pure_box: bool = True,
        vratio: float | str | None = None,
        split_dir: int | str | None = None,
        aspect: float | str | None = None,
        passes: int | str | None = None,
        layers: int | str | None = None,
        agglomerate_cache: str | Path | None = None,
        device: str = "cuda:0",
        gpu: int | str | None = None,
        python_exe: str | None = None,
        max_iterations: int = 6000,
        fd_step: float = 1e-5,
        units: str = "C",
        warm_states: dict[int, str | Path] | None = None,
        extra_solve_flags: Sequence[str] = (),
        base_request: dict[str, Any] | None = None,
    ):
        shared_cache = _SingleSlotSolverCache(device=device)
        warm = warm_states or {}
        subproblems = [
            HallSingleSnapshotProblem(
                param_map,
                int(k),
                template_dir,
                Path(work_dir),
                observer,
                agglomerate=agglomerate,
                pure_box=pure_box,
                vratio=vratio,
                split_dir=split_dir,
                aspect=aspect,
                passes=passes,
                layers=layers,
                agglomerate_cache=agglomerate_cache,
                device=device,
                gpu=gpu,
                python_exe=python_exe,
                max_iterations=max_iterations,
                fd_step=fd_step,
                units=units,
                solver_cache=shared_cache,
                warm_state_path=warm.get(int(k)),
                extra_solve_flags=extra_solve_flags,
                base_request=base_request,
            )
            for k in snapshot_indices
        ]
        super().__init__(subproblems, param_names=param_map.param_names)


# --------------------------------------------------------------------------- L1 Transient Problem
class HallStepModel(TransientStepModel):
    """Physical BDF1 transient step model backed by ``dc_foam_solve --phys-dt`` and ``dc_adjoint``."""

    def __init__(
        self,
        param_map: PatchParameterMap,
        template_dir: str | Path,
        work_dir: str | Path,
        observer: HallProbeObserver,
        *,
        agglomerate: float = 0.4,
        pure_box: bool = True,
        vratio: float | str | None = None,
        split_dir: int | str | None = None,
        aspect: float | str | None = None,
        passes: int | str | None = None,
        layers: int | str | None = None,
        agglomerate_cache: str | Path | None = None,
        device: str = "cuda:0",
        gpu: int | str | None = None,
        python_exe: str | None = None,
        max_iterations: int = 4000,
        phys_tol: float = 1e-5,
        phys_rtol: float = 0.05,
        phys_turb_rtol: float = 1.0,
        phys_turb_tol: float = 1e-2,
        phys_flow_tol: float = 1e-3,
        turb_min: float = 1e-12,
        fd_step: float = 1e-5,
        units: str = "C",
        solver_cache: _SingleSlotSolverCache | None = None,
        base_request: dict[str, Any] | None = None,
        extra_solve_flags: Sequence[str] = (),
        canonical_case_dir: str | Path | None = None,
        inprocess_transient: bool = True,
        phys_mass_tol: float = 0.005,
        rebuild_adapt: int = 40,
    ):
        self.param_map = param_map
        self.param_names = tuple(param_map.param_names)
        self.template_dir = Path(template_dir)
        self.work_dir = Path(work_dir)
        self.observer = observer
        self.agglomerate = float(agglomerate) if agglomerate else 0.0
        self.pure_box = bool(pure_box)
        self.vratio = vratio
        self.split_dir = split_dir
        self.aspect = aspect
        self.passes = passes
        self.layers = layers
        self.agglomerate_cache = Path(agglomerate_cache) if agglomerate_cache else None
        self.device = torch.device(device)
        self.gpu = gpu
        self.python_exe = python_exe
        self.max_iterations = int(max_iterations)
        self.phys_tol = float(phys_tol)
        self.phys_rtol = float(phys_rtol)
        self.phys_turb_rtol = float(phys_turb_rtol)
        self.phys_turb_tol = float(phys_turb_tol)
        self.phys_flow_tol = float(phys_flow_tol)
        # a step is solved only with the hall's mass balance closed to phys_mass_tol of the inflow (under the 1 % C0
        # check, which the per-field criterion alone missed on Base201 L1 with measured unit flows), and the Picard
        # blocks are rebuilt whenever a GMRES took more than rebuild_adapt iterations (the timed rebuild every 80 left
        # 60 pseudo-time steps on a stale preconditioner there): 81 -> 11 pseudo-time steps on its first 120 s step
        self.phys_mass_tol = float(phys_mass_tol)
        self.rebuild_adapt = int(rebuild_adapt)
        self.turb_min = float(turb_min)
        self.fd_step = float(fd_step)
        self.units = units
        self._cache = solver_cache if solver_cache is not None else _SingleSlotSolverCache(device=str(self.device))
        self.base_request = dict(base_request or {})
        self.extra_solve_flags = list(extra_solve_flags)

        self._step_runs: dict[int, Path] = {}
        self._case_dirs_by_topo: dict[tuple[tuple, bool], Path] = {}
        self.inprocess_transient = bool(inprocess_transient)
        if canonical_case_dir is not None:
            cpath = Path(canonical_case_dir).resolve()
            if (cpath / "gpuNewton" / "case").is_dir():
                cpath = (cpath / "gpuNewton" / "case").resolve()
            self._canonical_case_dir: Path | None = cpath
        else:
            self._canonical_case_dir = None
        self._rtheta_cache: tuple[int, tuple, torch.Tensor] | None = None
        self._vol_cache: torch.Tensor | None = None
        self._eval_idx = 0

    @staticmethod
    def _topo_key(vals: dict) -> tuple[tuple, bool]:
        return (tuple(sorted(vals.get("stopped_acus", {}).keys())), bool(vals.get("wall_T")))

    def _find_h5_case_for_topo(self, topo: tuple[tuple, bool]) -> Path | None:
        c = self._case_dirs_by_topo.get(topo)
        if c is not None and any(c.glob("*.h5")):
            return c
        if self._canonical_case_dir is not None and any(self._canonical_case_dir.glob("*.h5")):
            bc_t = self._canonical_case_dir / "bc" / "T.toml"
            if bc_t.is_file():
                try:
                    import tomllib
                except ModuleNotFoundError:  # Python 3.10 (the dcdtuni environment)
                    import tomli as tomllib
                bdoc = tomllib.loads(bc_t.read_text()).get("boundaryfield", {})
                # the parameter map names the wall patches whose temperature is a parameter
                has_fixed_wall = any(bdoc.get(w, {}).get("type") == "fixedValue" for w in self.param_map.walls)
                stopped_in_toml = tuple(sorted(
                    u for u, cfg in self.param_map.acus.items()
                    if bdoc.get(cfg["supply"], {}).get("type") == "zeroGradient"
                ))
                if has_fixed_wall == topo[1] and stopped_in_toml == topo[0]:
                    self._case_dirs_by_topo[topo] = self._canonical_case_dir
                    return self._canonical_case_dir
            else:
                self._case_dirs_by_topo[topo] = self._canonical_case_dir
                return self._canonical_case_dir
        return None

    def _get_solver(self, k: int, vals: dict):
        topo = self._topo_key(vals)
        cdir = self._find_h5_case_for_topo(topo)
        if cdir is None:
            rdir = self._step_runs.get(int(k), self.template_dir)
            case_sub = rdir / "gpuNewton" / "case"
            cdir = case_sub if case_sub.is_dir() else rdir
            if not any(cdir.glob("*.h5")) and self._step_runs:
                for rp in self._step_runs.values():
                    if (rp / "gpuNewton" / "case").is_dir() and any((rp / "gpuNewton" / "case").glob("*.h5")):
                        cdir = rp / "gpuNewton" / "case"
                        break
            if any(cdir.glob("*.h5")):
                self._case_dirs_by_topo[topo] = cdir.resolve()
        stopped, wall_active = topo
        cs, binding, vol = self._cache.get(cdir, stopped, wall_active, self.param_map.TRef)
        self.observer.bind_case_dir(cdir)
        self._vol_cache = vol.detach().to("cpu")
        return cs, binding, vol

    def _parse_extra_odil_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = dict(
            cfl0=5.0, cfl_max=12.0, growth=2.0, reject=1.25, max_du=0.5, beta=100.0,
            turb_cfl=1.0, pos_diag=1.0, clip=0.2, restart=40, krylov=80, rtol=0.03,
            rebuild_every=80, law="ser+",
        )
        flags = list(self.extra_solve_flags)
        i = 0
        while i < len(flags):
            f = str(flags[i])
            v = str(flags[i + 1]) if i + 1 < len(flags) else ""
            if f == "--odil-rebuild-every":
                kw["rebuild_every"] = int(v); i += 2; continue
            if f == "--odil-reject":
                kw["reject"] = float(v); i += 2; continue
            if f == "--pt-cfl-max":
                kw["cfl_max"] = float(v); i += 2; continue
            if f == "--pt-cfl0":
                kw["cfl0"] = float(v); i += 2; continue
            if f == "--pt-growth":
                kw["growth"] = float(v); i += 2; continue
            if f == "--pt-eta":
                kw["rtol"] = float(v); i += 2; continue
            if f == "--pt-krylov":
                kw["krylov"] = int(v); i += 2; continue
            if f == "--odil-beta":
                kw["beta"] = float(v); i += 2; continue
            if f == "--odil-law":
                kw["law"] = str(v); i += 2; continue
            i += 1
        return kw

    def _check_physics_gpu(self, cs: Any, bool_req: bool = True) -> tuple[bool, dict[str, bool]]:
        if not bool_req:
            return True, {"mass": True, "speed": True, "turbulence": True}
        pm = getattr(cs, "_phys_meta", None)
        Wr = getattr(getattr(cs, "_warp", None), "W", None)
        if pm is None or Wr is None:
            return True, {"mass": True, "speed": True, "turbulence": True}
        import warp as wp
        with torch.no_grad():
            q = wp.to_torch(Wr._buffers(0, 1)["q"]).reshape(-1, 7)
            phi = Wr.face_volumetric_flux.reshape(-1) * pm["area"]
            bf = pm["bf"]
            n = int(cs.n_cell)
            if bf.numel() > 0:
                ph = phi[bf]
                inflow = (-ph[ph < 0]).sum().clamp_min(1e-300)
                mass_ok = bool((ph.sum().abs() / inflow) <= 0.01)
                g = pm["ghost"][bf]
                ub = float(torch.linalg.vector_norm(q[g, 0:3], dim=1).max().item())
            else:
                mass_ok = True
                ub = 0.0
            speed_factor = float(self.base_request.get("speedFactor", 6.0))
            uc = float(torch.linalg.vector_norm(q[:n, 0:3], dim=1).max().item())
            speed_ok = bool(uc <= max(speed_factor * ub, 30.0))
            k_c, eps_c = q[:n, 5], q[:n, 6]
            nu = float(self.base_request.get("nu", 1.5e-5))
            nut_max = float((0.09 * k_c * k_c / eps_c.clamp_min(1e-300)).max().item())
            turb_ok = bool(float(k_c.min().item()) > 0.0 and float(eps_c.min().item()) > 0.0 and nut_max / nu <= 1e5)
        return (mass_ok and speed_ok and turb_ok), {"mass": mass_ok, "speed": speed_ok, "turbulence": turb_ok}

    def _solve_step_inprocess(
        self,
        k: int,
        u_prev: torch.Tensor,
        dt: float,
        th_cpu: torch.Tensor,
        vals: dict,
        init_u: torch.Tensor | None,
    ) -> tuple[torch.Tensor, Certificate]:
        from Pipeline.CoupledNewton.odil_inner import solve_odil_dc
        t0 = time.perf_counter()
        cs, binding, _ = self._get_solver(int(k), vals)
        tau_raw = vals.get("tau_scale", 1.0)
        tau_val = float(tau_raw.detach().item()) if isinstance(tau_raw, torch.Tensor) else float(tau_raw)

        t_vals = [float(v.detach().item() if isinstance(v, torch.Tensor) else v) - self.param_map.TRef
                  for v in (vals.get("T") or {}).values()]
        lo_fix = min([0.0, *t_vals])
        hi_fix = max([0.0, *t_vals])
        rises = [float(v.detach().item() if isinstance(v, torch.Tensor) else v)
                 for v in (vals.get("rise") or {}).values()]
        max_rise = max(rises, default=0.0)
        tbox = f"{min(-10.0, lo_fix - 10.0):g},{max(30.0, max(hi_fix, 0.0) + 1.5 * max_rise + 5.0):g}"

        env_overrides = {
            "LPCNS_ODIL_PHYS_TOL": str(self.phys_tol),
            "LPCNS_ODIL_PHYS_RTOL": str(self.phys_rtol),
            "LPCNS_ODIL_PHYS_TURB_RTOL": str(self.phys_turb_rtol),
            "LPCNS_ODIL_PHYS_TURB_TOL": str(self.phys_turb_tol),
            "LPCNS_ODIL_PHYS_FLOW_TOL": str(self.phys_flow_tol),
            "LPCNS_ODIL_PHYS_MASS_TOL": str(self.phys_mass_tol),
            "LPCNS_ODIL_REBUILD_ADAPT": str(self.rebuild_adapt),
            "LPCNS_ODIL_TURB_MIN": str(self.turb_min),
            "LPCNS_ODIL_EW": "",
            "LPCNS_ODIL_FDJVP": "1",
            "LPCNS_ODIL_PHYS_COLD_REF": os.environ.get("LPCNS_ODIL_PHYS_COLD_REF", "1"),
            "LPCNS_ODIL_PHYS_REF_OLD": os.environ.get("LPCNS_ODIL_PHYS_REF_OLD", "1"),
            "LPCNS_ODIL_BETA_RAMP": "0" if init_u is not None else "20",
            "LPCNS_ODIL_TBOX": tbox,
        }
        saved_env = {ek: os.environ.get(ek) for ek in env_overrides}
        for ek, ev in env_overrides.items():
            os.environ[ek] = ev

        odil_kw = self._parse_extra_odil_kwargs()
        x_cold = getattr(cs, "_internal_start", None)
        if x_cold is None:
            x_cold = u_prev.to(device=cs.device, dtype=torch.float64)
        init_use = u_prev if init_u is None else init_u

        # the pseudo-time history of every in-process step: work_dir/inprocess/step_<k>_<eval>.log
        log_dir = self.work_dir / "inprocess"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"step_{int(k):04d}_{self._eval_idx:04d}.log"
        log_f = open(log_path, "a", buffering=1)

        def _log(msg: str) -> None:
            log_f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

        binding.bind(vals)
        try:
            x_out, hist = solve_odil_dc(
                cs, x_cold, maxit=2 * int(self.max_iterations), tol=0.0,
                init_state=init_use, phys_dt=float(dt), phys_old=u_prev, phys_t_mult=tau_val,
                log=_log, **odil_kw,
            )
            r_steady = cs.residual(x_out).detach()
            m_dt = self.mass_vector(int(k), th_cpu).to(cs.device, dtype=torch.float64) / float(dt)
            r_bdf1 = r_steady.reshape(-1) + m_dt * (x_out.reshape(-1) - u_prev.to(cs.device, dtype=torch.float64).reshape(-1))
            res_inf = float(r_bdf1.abs().max().item())
            phys_ok, checks = self._check_physics_gpu(cs, bool(self.base_request.get("requirePhysicsOnStop", True)))
        finally:
            log_f.close()
            binding.restore()
            for ek, ev in saved_env.items():
                if ev is None:
                    os.environ.pop(ek, None)
                else:
                    os.environ[ek] = ev

        its = int(hist[-1]["it"]) if hist else 0
        phys_conv = bool((hist and hist[-1].get("phys_converged", False)) or res_inf < 1e-10)
        ok = bool(phys_conv and phys_ok and torch.isfinite(x_out).all())
        reason = (f"physical time step(s) 1/1 (dt={float(dt):g} s) converged"
                  if ok else f"inprocess_failed(phys={phys_conv},checks={checks})")
        wall = time.perf_counter() - t0
        if not ok:
            return torch.zeros(1, dtype=torch.float64), Certificate(False, res_inf, its, "odil_inprocess", reason, wall)
        return x_out.detach().to("cpu", dtype=torch.float64).reshape(-1), Certificate(
            True, res_inf, its, "odil_inprocess", reason, wall, {"checks": checks}
        )

    def solve_step(
        self,
        k: int,
        u_prev: torch.Tensor | None,
        dt: float | None,
        theta_k: torch.Tensor,
        init_u: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, Certificate]:
        th_cpu = theta_k.detach().to("cpu", dtype=torch.float64).reshape(-1)
        if (self.inprocess_transient and int(k) > 0 and dt is not None and float(dt) > 0.0
                and os.environ.get("LPCNS_DISABLE_INPROCESS_BDF1", "0") != "1"):
            if u_prev is None:
                raise ValueError(f"step {k}: u_prev is required when dt > 0")
            vals_fast = self.param_map.evaluate(int(k), th_cpu)
            if self._find_h5_case_for_topo(self._topo_key(vals_fast)) is not None:
                self._eval_idx += 1
                return self._solve_step_inprocess(int(k), u_prev, float(dt), th_cpu, vals_fast, init_u)
        self._eval_idx += 1
        h = hashlib.sha256(th_cpu.numpy().tobytes()).hexdigest()[:8]
        run_dir = self.work_dir / f"traj_{self._eval_idx:04d}_{h}" / f"step_{int(k):04d}"
        vals = self.param_map.apply_to_foam_dir(run_dir, int(k), th_cpu, template_dir=self.template_dir)
        self._step_runs[int(k)] = run_dir

        req = _load_template_request(self.template_dir)
        req.update(self.base_request)
        if "maxIterations" not in self.base_request:
            req["maxIterations"] = int(self.max_iterations)
        if self.agglomerate > 0:
            req["agglomerateBoxM"] = self.agglomerate

        gn = run_dir / "gpuNewton"
        gn.mkdir(parents=True, exist_ok=True)
        init_path = None
        if init_u is not None:
            init_path = gn / "init_state.pt"
            torch.save(init_u.detach().to("cpu", dtype=torch.float64), init_path)

        is_graded = (not self.pure_box) or (self.vratio is not None) or (self.aspect is not None)
        if is_graded:
            req["agglomerateAspect"] = True
            env_ex: dict[str, str | None] = {
                "LPCNS_DC_AGGLOMERATE_VRATIO": str(self.vratio) if self.vratio is not None else None,
                "LPCNS_DC_AGGLOMERATE_SPLIT_DIR": str(self.split_dir) if self.split_dir is not None else None,
                "LPCNS_DC_AGGLOMERATE_ASPECT": str(self.aspect) if self.aspect is not None else None,
                "LPCNS_DC_AGGLOMERATE_PASSES": str(self.passes) if self.passes is not None else None,
                "LPCNS_DC_AGGLOMERATE_LAYERS": str(self.layers) if self.layers is not None else None,
                "LPCNS_DC_AGGLOMERATE_JUMP": os.environ.get("LPCNS_DC_AGGLOMERATE_JUMP"),
                "LPCNS_DC_AGGLOMERATE_NONORTHO": os.environ.get("LPCNS_DC_AGGLOMERATE_NONORTHO"),
                "LPCNS_DC_AGGLOMERATE_DISSOLVE": os.environ.get("LPCNS_DC_AGGLOMERATE_DISSOLVE"),
                "LPCNS_DC_AGGLOMERATE_ABSORB": os.environ.get("LPCNS_DC_AGGLOMERATE_ABSORB"),
                "LPCNS_ODIL_EW": "",
                "LPCNS_ODIL_FDJVP": "1",
            }
        else:
            env_ex = {
                "LPCNS_DC_AGGLOMERATE_VRATIO": None,
                "LPCNS_DC_AGGLOMERATE_SPLIT_DIR": None,
                "LPCNS_DC_AGGLOMERATE_ASPECT": None,
                "LPCNS_ODIL_EW": "",
                "LPCNS_ODIL_FDJVP": "1",
            }
        if self.agglomerate_cache is not None:
            cache_dir = (
                self.agglomerate_cache
                if (is_graded or self.agglomerate_cache.name == "purebox")
                else self.agglomerate_cache / "purebox"
            )
            env_ex["LPCNS_DC_AGGLOMERATE_CACHE"] = str(cache_dir)
        if self.inprocess_transient and self._find_h5_case_for_topo(self._topo_key(vals)) is None:
            env_ex["LPCNS_DC_KEEP_CASE"] = "1"
        if init_path is not None:
            env_ex["LPCNS_ODIL_BETA_RAMP"] = "0"
        if k > 0 and dt is not None and float(dt) > 0.0:
            env_ex.setdefault("LPCNS_ODIL_REJECT", "1.25")
            env_ex["LPCNS_ODIL_PHYS_MASS_TOL"] = str(self.phys_mass_tol)
            env_ex["LPCNS_ODIL_REBUILD_ADAPT"] = str(self.rebuild_adapt)
            if u_prev is None:
                raise ValueError(f"step {k}: u_prev is required when dt > 0")
            old_path = gn / "phys_old.pt"
            torch.save(u_prev.detach().to("cpu", dtype=torch.float64), old_path)
            tau_raw = vals.get("tau_scale", 1.0)
            tau_val = float(tau_raw.detach().item()) if isinstance(tau_raw, torch.Tensor) else float(tau_raw)
            req["physDt"] = float(dt)
            req["physOldState"] = str(old_path)
            req["physTol"] = self.phys_tol
            req["physRtol"] = self.phys_rtol
            req["physTurbRtol"] = self.phys_turb_rtol
            req["physTurbTol"] = self.phys_turb_tol
            req["physFlowTol"] = self.phys_flow_tol
            req["turbMin"] = self.turb_min
            req["physTMult"] = tau_val
            req["physSteps"] = 1
            req["selfStop"] = False
            req.pop("declaredHeatW", None)
        else:
            req.setdefault("stopToleranceK", 0.1)
            req.setdefault("changeMinK", 0.0)
            req.setdefault("plateauMode", "mean")
            req.setdefault("selfStop", True)
            if vals.get("wall_T"):
                req["declaredHeatW"] = None
            elif "declared_heat_W" in vals and "declaredHeatW" not in self.base_request:
                req["declaredHeatW"] = float(vals["declared_heat_W"])

        extra = list(self.extra_solve_flags)
        state_path, cert = _run_dc_foam_solve(
            run_dir,
            request=req,
            extra=extra,
            gpu=self.gpu,
            python_exe=self.python_exe,
            env_extra=env_ex,
            init_state_path=init_path,
        )
        if not cert.converged:
            return torch.zeros(1, dtype=torch.float64), cert
        csub = run_dir / "gpuNewton" / "case"
        if csub.is_dir() and any(csub.glob("*.h5")):
            self._case_dirs_by_topo[self._topo_key(vals)] = csub.resolve()
        self.observer.bind_case_dir(run_dir)
        x = torch.load(state_path, map_location="cpu", weights_only=False).to(torch.float64).reshape(-1)
        return x, cert

    def mass_vector(self, k: int, theta_k: torch.Tensor | None = None) -> torch.Tensor:
        th = torch.zeros(len(self.param_names), dtype=torch.float64) if theta_k is None else theta_k
        if self._vol_cache is None:
            vals = self.param_map.evaluate(int(k), th.detach())
            self._get_solver(int(k), vals)
        assert self._vol_cache is not None
        vol = self._vol_cache.to(th.device, dtype=torch.float64)
        tau = self.param_map.evaluate(int(k), th).get("tau_scale", 1.0)
        z = torch.zeros_like(vol)
        return torch.cat([vol, vol, vol, z, vol * tau, vol, vol], dim=0)

    def _diag(self, k: int, theta_k: torch.Tensor, dt: float | None) -> torch.Tensor | None:
        if k == 0 or dt is None:
            return None
        return self.mass_vector(k, theta_k.detach()) / float(dt)

    def step_residual(self, k: int, u_k: torch.Tensor, u_prev: torch.Tensor | None, dt: float | None, theta_k: torch.Tensor) -> torch.Tensor:
        vals = self.param_map.evaluate(int(k), theta_k)
        _, binding, _ = self._get_solver(int(k), vals)
        r = binding.residual(u_k, vals).to(u_k.device, u_k.dtype)
        if k == 0 or dt is None:
            return r
        assert u_prev is not None
        m = self.mass_vector(k, theta_k).to(u_k.device, u_k.dtype)
        return r + (m / float(dt)) * (u_k.reshape(-1) - u_prev.to(u_k.device, u_k.dtype).reshape(-1))

    def _r_theta(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor):
        th_cpu = theta_k.detach().to("cpu", dtype=torch.float64).reshape(-1)
        vals = self.param_map.evaluate(int(k), th_cpu)
        cs, binding, _ = self._get_solver(int(k), vals)
        th_key = (int(k),) + tuple(round(float(v), 12) for v in th_cpu.tolist())
        uid = id(u_k)
        if self._rtheta_cache is not None and self._rtheta_cache[0] == uid and self._rtheta_cache[1] == th_key:
            return cs, binding, vals, self._rtheta_cache[2]
        u_d = u_k.to(self.device, dtype=torch.float64).reshape(-1)
        r_th = binding.r_theta_columns(u_d, lambda t: self.param_map.evaluate(int(k), t), th_cpu, fd_step=self.fd_step)
        self._rtheta_cache = (uid, th_key, r_th)
        return cs, binding, vals, r_th

    def _effective_diag(self, cs: Any, k: int, u_d: torch.Tensor, theta_k: torch.Tensor, dt: float | None) -> torch.Tensor | None:
        D = self._diag(k, theta_k, dt)
        diag_d = None if D is None else D.to(self.device, dtype=torch.float64)
        s_f = _large_hall_flow_shift(cs, u_d)
        if s_f is not None:
            diag_d = s_f if diag_d is None else (diag_d + s_f)
        return diag_d

    def step_jvp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, v: torch.Tensor, dt: float | None = None) -> torch.Tensor:
        vals = self.param_map.evaluate(int(k), theta_k.detach())
        cs, binding, _ = self._get_solver(int(k), vals)
        binding.bind(vals)
        try:
            u_d = u_k.to(self.device, dtype=torch.float64).reshape(-1)
            v_d = v.to(self.device, dtype=torch.float64).reshape(-1)
            diag_d = self._effective_diag(cs, k, u_d, theta_k, dt)
            with torch.no_grad(), torch_residual(cs):
                W_ = _warp_backend(cs).W
                _, jv = W_.jvp(u_d, v_d.reshape(1, -1), frozen=False)
                out = jv[0].detach()
            if diag_d is not None:
                out = out + diag_d * v_d
            return out.to(u_k.device, u_k.dtype)
        finally:
            binding.restore()

    def step_vjp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, w: torch.Tensor, dt: float | None = None) -> torch.Tensor:
        vals = self.param_map.evaluate(int(k), theta_k.detach())
        cs, binding, _ = self._get_solver(int(k), vals)
        binding.bind(vals)
        try:
            u_d = u_k.to(self.device, dtype=torch.float64).reshape(-1)
            w_d = w.to(self.device, dtype=torch.float64).reshape(-1)
            diag_d = self._effective_diag(cs, k, u_d, theta_k, dt)
            apply, _ = transpose_tape(cs, u_d)
            out = apply(w_d).detach()
            del apply
            _clear_solver_autograd(cs, u_d)
            if diag_d is not None:
                out = out + diag_d * w_d
            return out.to(u_k.device, u_k.dtype)
        finally:
            binding.restore()

    def step_param_jvp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, d: torch.Tensor) -> torch.Tensor:
        _, _, _, r_th = self._r_theta(k, u_k, theta_k)
        return (r_th @ d.to(r_th.device, dtype=r_th.dtype).reshape(-1)).to(u_k.device, u_k.dtype)

    def step_param_vjp(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        _, _, _, r_th = self._r_theta(k, u_k, theta_k)
        return (r_th.T @ w.to(r_th.device, dtype=r_th.dtype).reshape(-1)).to(theta_k.device, theta_k.dtype)

    def step_linear_solve(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, b: torch.Tensor, *, transpose: bool = False, dt: float | None = None) -> tuple[torch.Tensor, dict]:
        vals = self.param_map.evaluate(int(k), theta_k.detach())
        cs, binding, _ = self._get_solver(int(k), vals)
        binding.bind(vals)
        try:
            u_d = u_k.to(self.device, dtype=torch.float64).reshape(-1)
            b_d = b.to(self.device, dtype=torch.float64)
            diag_d = self._effective_diag(cs, k, u_d, theta_k, dt)
            is_large = int(cs.n_cell) > 80_000
            is_large_bdf1 = is_large and (dt is not None and float(dt) > 0.0)
            rtol_use = 1e-5 if is_large else 1e-8
            t_kw = LARGE_HALL_TRANSPOSE_SETTINGS if is_large_bdf1 else TRANSPOSE_SETTINGS
            f_kw = LARGE_HALL_TANGENT_SETTINGS if is_large_bdf1 else TANGENT_SETTINGS
            if transpose:
                sol, info = solve_adjoint(cs, u_d, b_d.reshape(-1), diag=diag_d, **{**t_kw, "rtol": rtol_use})
                return sol.detach().to(u_k.device, u_k.dtype), info
            sol, info = solve_tangent(cs, u_d, b_d, diag=diag_d, **{**f_kw, "rtol": rtol_use})
            return sol.detach().to(u_k.device, u_k.dtype), info
        finally:
            binding.restore()

    def step_linear_solve_many(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor, rhs_rows: torch.Tensor, *, dt: float | None = None,
                               x0_rows: torch.Tensor | None = None) -> tuple[torch.Tensor, dict]:
        vals = self.param_map.evaluate(int(k), theta_k.detach())
        cs, binding, _ = self._get_solver(int(k), vals)
        binding.bind(vals)
        try:
            u_d = u_k.to(self.device, dtype=torch.float64).reshape(-1)
            rows_d = rhs_rows.contiguous().to(self.device, dtype=torch.float64)
            diag_d = self._effective_diag(cs, k, u_d, theta_k, dt)
            is_large = int(cs.n_cell) > 80_000
            is_large_bdf1 = is_large and (dt is not None and float(dt) > 0.0)
            # a BDF1 step's tangent to 1e-5 on every hall size (the large halls' value): the Gauss-Newton Jacobian of a
            # window fit needs no more, and at 1e-8 one step's 7 tangents on Base201 L1 (44k cells) ran past 15 min
            # (2026-11-17); LPCNS_BDF1_TAN_RTOL overrides it; steady tangents keep 1e-8 (the adjoint checks)
            is_bdf1 = dt is not None and float(dt) > 0.0
            rtol_use = 1e-5 if (is_large or is_bdf1) else 1e-8
            if is_bdf1 and os.environ.get("LPCNS_BDF1_TAN_RTOL"):
                rtol_use = float(os.environ["LPCNS_BDF1_TAN_RTOL"])
            # a BDF1 step takes the BDF1 tangent settings (amg_cfl 2, equilibrated fields) on every hall size: with the
            # steady settings (shift_cfl 3000) Base201 L1's BDF1 tangent fell 0.90 -> 0.87 in 30 outer FGMRES
            # iterations (2026-11-17); LPCNS_BDF1_TAN_SETTINGS=steady restores them below the large-hall size
            if is_bdf1 and os.environ.get("LPCNS_BDF1_TAN_SETTINGS", "bdf1") != "steady":
                is_large_bdf1 = True
            f_kw = dict(LARGE_HALL_TANGENT_SETTINGS if is_large_bdf1 else TANGENT_SETTINGS)
            # LPCNS_BDF1_TAN_OVERRIDES='{"amg_cfl": 12, "inner_its": 20}': settings of a BDF1 step's tangent solve
            # (preconditioner studies)
            if is_bdf1 and os.environ.get("LPCNS_BDF1_TAN_OVERRIDES"):
                f_kw.update(json.loads(os.environ["LPCNS_BDF1_TAN_OVERRIDES"]))
            if is_large_bdf1:
                if os.environ.get("LPCNS_LARGE_TAN_RTOL"):
                    rtol_use = float(os.environ["LPCNS_LARGE_TAN_RTOL"])
                if os.environ.get("LPCNS_LARGE_TAN_RESTART"):
                    f_kw["restart"] = int(os.environ["LPCNS_LARGE_TAN_RESTART"])
                if os.environ.get("LPCNS_LARGE_TAN_CYCLES"):
                    f_kw["max_cycles"] = int(os.environ["LPCNS_LARGE_TAN_CYCLES"])
            t_tan = time.perf_counter()
            log_path = self.work_dir / "inprocess" / "tangent.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # the log stays open while the solve runs: a run in its Jacobian can be stopped by fuser -k on this file
            tlog_fh = open(log_path, "a", buffering=1)

            def _tlog(msg: str) -> None:
                tlog_fh.write(f"{time.strftime('%H:%M:%S')} step {int(k)} {msg}\n")

            try:
                _tlog(f"tangents: {int(rows_d.shape[0])} rhs, rtol {rtol_use:g}, "
                      f"{'BDF1' if is_large_bdf1 else 'steady'} settings")
                x0_d = None if x0_rows is None else x0_rows.contiguous().to(self.device, dtype=torch.float64)
                sol_rows, info = solve_tangent(cs, u_d, rows_d, diag=diag_d, log=_tlog, x0=x0_d,
                                               **{**f_kw, "rtol": rtol_use})
            finally:
                tlog_fh.close()
            # the tangent solves' cost, one line per step and batch: work_dir/inprocess/tangent.log
            try:
                log_dir = self.work_dir / "inprocess"
                log_dir.mkdir(parents=True, exist_ok=True)
                brief = {kk: vv for kk, vv in (info or {}).items() if isinstance(vv, (int, float, str, bool))}
                with open(log_dir / "tangent.log", "a") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} step {int(k)} rhs {int(rows_d.shape[0])} rtol {rtol_use:g} "
                             f"wall {time.perf_counter() - t_tan:.1f} s {brief}\n")
            except OSError:
                pass
            return sol_rows.detach().to(u_k.device, u_k.dtype), {"linear_solves": int(rows_d.shape[0]), "info": info}
        finally:
            binding.restore()

    def step_observe(self, k: int, u_k: torch.Tensor, theta_k: torch.Tensor) -> dict[str, torch.Tensor]:
        vals = self.param_map.evaluate(int(k), theta_k.detach())
        if self.observer.dq is None and int(k) in self._step_runs:
            self.observer.bind_case_dir(self._step_runs[int(k)])
        self.observer.set_snapshot_flows(vals)
        vec = self.observer.observe(u_k, units=self.units).to(u_k.device, u_k.dtype)
        return dict(zip(self.observer.names, vec))


class HallTrajectoryProblem(CheckpointedTrajectoryProblem):
    """L1 coarse dcRANS transient JFGNK trajectory problem over BDF1 physical time steps."""

    def __init__(
        self,
        step_model: HallStepModel,
        dts: Sequence[float],
        *,
        schedule: Callable[[int, torch.Tensor], torch.Tensor] | None = None,
        u0: torch.Tensor | None = None,
        checkpoint_dir: str | Path | None = None,
        max_step_attempts: int = 2,
    ):
        self.step_model = step_model
        sched = (lambda k, th: th) if schedule is None else schedule
        super().__init__(
            step_model,
            dts,
            sched,
            param_names=step_model.param_names,
            u0=u0,
            state_device="cpu",
            checkpoint_dir=checkpoint_dir,
            max_step_attempts=max_step_attempts,
        )
