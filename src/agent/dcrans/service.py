"""Unified lazy service for steady and BDF1 transient JFNK/JFGNK solves and inversions of the data-centre hall.

* ``L0``: the zonal pressure-enthalpy network of the L0 mesh (dcCart ``coarsenLevel = "L0"``) with its own residual
  (``FVsolver/applications/zonal_network*.py``), a whole time window solved and fitted at once
  (``Pipeline/CoupledNewton/zonal_window.py``);
* ``L1``: the buoyant Boussinesq RANS of the L1 mesh (``Pipeline/CoupledNewton/dc_rans_framework.py``).

Both levels take the same input (a prepared OpenFOAM-layout case directory, or a ``.cfdproj`` meshed for it), the
same parameter map (``_discover_case_pmap``: the case's ACUs, servers and baffles) and give the same observation
names (``probe:i``, ``return_T:<acu>``).  Heavy numerical dependencies (``torch``, ``scipy``, ``h5py``, ``warp``)
and solver internals are never imported when this module is imported; every solver call resolves a solver tree
(the vendored GPU solver or ``DCDTUNI_LPCNS_SRC``) and delegates to an isolated worker subprocess with this
process's interpreter (the machine's one ``dcdtuni`` environment).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Union

from agent.scripted_api import identity as api_identity

PathLike = Union[str, os.PathLike]
DCRANS_FORMAT = "dcdtuni.api.result/v1"
REPO = Path(__file__).resolve().parents[3]
DCRANS_SRC_ENV = "DCDTUNI_LPCNS_SRC"


def _solver_src_root(solver_src: Optional[PathLike] = None) -> Path:
    """Resolve the solver ``src`` tree containing ``Pipeline/CoupledNewton/dc_rans_framework.py``."""
    candidates = []
    if solver_src is not None:
        p = Path(solver_src).expanduser().resolve()
        candidates.extend([p, p / "src"])
    env_src = os.environ.get(DCRANS_SRC_ENV)
    if env_src:
        p = Path(env_src).expanduser().resolve()
        candidates.extend([p, p / "src"])
    candidates.append(REPO / "src" / "backend" / "aircooling" / "gpu_solver")
    for root in candidates:
        if (root / "Pipeline" / "CoupledNewton" / "dc_rans_framework.py").is_file():
            return root
    checked = ", ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        f"dcRANS solver modules not found (checked {checked}); vendor the solver or set {DCRANS_SRC_ENV}"
    )


def _solver_identity(solver_root: Path) -> Dict[str, Any]:
    vendored = solver_root / "VENDORED.json"
    if vendored.is_file():
        data = json.loads(vendored.read_text(encoding="utf-8"))
        return {"origin": "vendored", "source": data.get("source") or {}}
    return {"origin": "external", "path": str(solver_root)}


def _probe_python_deps(python_path: Path) -> bool:
    code = "import torch, scipy, h5py, numpy"
    try:
        done = subprocess.run([str(python_path), "-c", code], capture_output=True, text=True, timeout=20)
        return done.returncode == 0
    except Exception:
        return False


def _resolve_dcrans_python(python_exe: Optional[PathLike] = None) -> Path:
    """This process's interpreter (one ``dcdtuni`` environment per machine), or an explicit one for a test."""
    p = Path(python_exe).expanduser().resolve() if python_exe is not None else Path(sys.executable).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"dcRANS python executable not found: {p}")
    if python_exe is None and not _probe_python_deps(p):
        raise RuntimeError(f"{p} lacks torch / scipy / h5py / numpy: install the solver dependencies into this "
                           "environment (setup_env.sh)")
    return p


def _cfd_source_identity(source: Union[PathLike, Mapping[str, Any]]) -> Dict[str, Any]:
    if isinstance(source, Mapping):
        data = dict(source)
        sha = api_identity.canonical_sha256(data)
        return {"kind": "dict", "sha256": sha, "cfdprojSha256": sha}
    p = Path(source).expanduser().resolve()
    if p.is_file():
        sha = api_identity.sha256_bytes(p.read_bytes())
        return {"kind": "file", "path": str(p), "sha256": sha, "cfdprojSha256": sha}
    hits = sorted(p.glob("*.cfdproj")) if p.is_dir() else []
    if hits:
        sha = api_identity.sha256_bytes(hits[0].read_bytes())
        return {"kind": "case_dir", "path": str(p), "cfdproj": str(hits[0]), "sha256": sha, "cfdprojSha256": sha}
    return {"kind": "case_dir", "path": str(p)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _make_run_dir(output_root: Optional[PathLike], tag: str) -> Path:
    base = Path(output_root).expanduser().resolve() if output_root is not None else (REPO / "_tmp" / "dcrans_runs")
    base.mkdir(parents=True, exist_ok=True)
    stamp = api_identity.now_sgt().strftime("%Y%m%d_%H%M%S_SGT")
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip("-") or "run"
    return Path(tempfile.mkdtemp(prefix=f"{stamp}__{safe_tag}_", dir=str(base)))


_WORKER_SCRIPT = r"""
import json, math, os, re, sys, time
from pathlib import Path
import numpy as np
import torch

task = sys.argv[1]
payload = json.loads(sys.stdin.read())


def _boundary_audit(case_dir, expected_patches=None, run_acceptance=True):
    from backend.aircooling.runner.patch_audit import verify_patch_preservation
    return verify_patch_preservation(case_dir, expected_patches=expected_patches, run_acceptance=run_acceptance)


def _has_polymesh(case_dir: Path) -> bool:
    return (case_dir / "constant" / "polyMesh" / "points").is_file() or any(
        case_dir.glob("processor[0-9]*/constant/polyMesh/points")
    )


def _do_audit(p):
    case = Path(p["case_dir"]).resolve()
    res = _boundary_audit(case, expected_patches=p.get("expected_patches"), run_acceptance=bool(p.get("run_acceptance", True)))
    box = p.get("agglomerate_box_m")
    agg_ok = True
    if p.get("check_agglomeration", True) and box is not None and float(box) > 0 and _has_polymesh(case):
        from Parse_mesh.foamio.decomposed import adaptive_box_labels, agglomerate, read_case, verify_agglomerated_patches
        fine = read_case(case)
        labels, info = adaptive_box_labels(
            fine,
            float(box),
            max_volume_ratio=float(p.get("vratio", 50.0)),
            max_aspect=float(p.get("aspect", 8.0)),
        )
        coarse = agglomerate(fine, labels, info)
        chk = verify_agglomerated_patches(fine, coarse)
        agg_ok = bool(chk.get("ok", False))
        res["agglomeration"] = {
            "ok": agg_ok,
            "box_m": float(box),
            "fine_cells": int(chk.get("fine_cells", fine.n_cells)),
            "coarse_cells": int(chk.get("agg_cells", coarse.n_cells)),
            "n_patches": int(chk.get("patch_count", len(coarse.patches))),
            "max_rel_area_err": float(chk.get("max_rel_area_error", 0.0)),
            "dropped_patches": list(chk.get("dropped_patches", [])),
        }
    ok = (not res.get("dropped_patches")) and (not res.get("face_count_mismatches")) and agg_ok
    return {"status": "succeeded" if ok else "failed", "audit": res}


def _agglomerate_box(p, case_dir):
    # the agglomeration box of an L1 solve: the caller's, else none on a dcCart coarse level (the case is already the
    # coarsened mesh, dccart-coarsen.json in its run directory: agglomerating it again leaves cells the recipe does not
    # converge on, 80 GMRES iterations at CFL 1 per Newton step on Base201 L1) and 0.4 m on a full-resolution case
    box = p.get("agglomerate_box_m")
    if box is not None:
        return float(box)
    return 0.0 if (Path(case_dir) / "dccart-coarsen.json").is_file() else 0.4


def _obs_lists(obs):
    # every observed quantity as plain lists (a transient one per step): the quantities without measurements too
    return {str(k): [float(x) for x in v.detach().to("cpu", torch.float64).reshape(-1).tolist()] for k, v in obs.items()}


def _compute_rmse(obs_pred, target):
    diffs = []
    for k, y_t in target.items():
        if k not in obs_pred:
            continue
        p_flat = obs_pred[k].detach().to("cpu", torch.float64).reshape(-1)
        y_flat = y_t.detach().to("cpu", torch.float64).reshape(-1)
        if p_flat.numel() != y_flat.numel():
            raise ValueError(f"observation shape mismatch for {k!r}: pred={p_flat.numel()} target={y_flat.numel()}")
        mask = torch.isfinite(y_flat)
        if bool(mask.any()):
            diffs.append(p_flat[mask] - y_flat[mask])
    if not diffs:
        return float("nan")
    cat = torch.cat(diffs)
    return float(torch.sqrt(torch.mean(cat.square())))


def _l0_specs(p, mode):
    from Pipeline.CoupledNewton.dc_rans_framework import default_datacentre_param_specs
    if p.get("include_wall"):
        raise ValueError("L0: the zonal network's walls are adiabatic (no wall temperature parameter)")
    return default_datacentre_param_specs(include_empty=bool(p.get("include_empty", False)),
                                          include_rev=bool(p.get("include_rev", False)),
                                          include_tau=(mode == "transient" or bool(p.get("include_tau", False))),
                                          q100_bounds=tuple(p.get("q100_bounds") or (0.3, 3.0)))


def _l0_snapshots(snaps, mode, dt_s, count=None):
    if snaps is not None:
        snaps = [dict(s) for s in snaps]
        if mode == "transient" and len(snaps) == 1:
            snaps.append(dict(snaps[0], t_s=float(snaps[0].get("t_s", 0.0)) + dt_s))
        return snaps
    n = 1 if mode == "steady" else max(2, int(count or 5))
    return [{"t_s": float(k) * dt_s} for k in range(n)]


def _l0_problem(p, specs, snaps, mode, dt_s):
    # The window of the L0 case: the zonal network of its mesh, the case's parameter map, the L1 observation
    # names; steady: every snapshot steady; transient: the first snapshot steady (the initial condition), then
    # backward-Euler steps of dt_s.
    from FVsolver.applications.zonal_network import build_zonal_mesh
    from FVsolver.applications.zonal_network_warp import WarpZonal
    from Parse_mesh import dctwin_bc
    from Parse_mesh.foamio.decomposed import read_case
    from Pipeline.CoupledNewton.zonal_window import ZonalObserver, zonal_window_problem
    case_dir = Path(p["case_dir"]).resolve()
    work = Path(p["work_dir"]) / "zonal"
    case = read_case(case_dir, keep="connected_open")
    areas = {q.name: float(case.magSf[q.start:q.start + q.size].sum()) for q in case.patches if q.size}
    dctwin_bc.translate(case_dir, work, areas)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    net = build_zonal_mesh(case_dir, work / "bc", work / "fvconfig.toml", case=case, device=dev)
    wz = WarpZonal(net, dev)
    pmap = _discover_case_pmap(case_dir, specs, snaps, p.get("patch_map_config"),
                               per_rack_leakage=bool(p.get("per_rack_leakage")),
                               rack_leakage_sigma=float(p.get("rack_leakage_sigma", 0.15)),
                               rack_names=p.get("rack_names"),
                               server_min_flow=float(p.get("server_min_flow") or 0.0))
    acus = p.get("return_acus") or [a for a, c in pmap.acus.items() if c.get("return")]
    returns = {a: pmap.acus[a]["return"] for a in acus if (pmap.acus.get(a) or {}).get("return")}
    obs = ZonalObserver(net, probe_coords=p.get("probe_coords"), probe_names=p.get("probe_names"), returns=returns,
                        units=p.get("units", "C"))
    K = len(snaps)
    dt = math.inf if mode == "steady" else [math.inf] + [float(dt_s)] * (K - 1)
    return zonal_window_problem(net, wz, pmap, list(range(K)), dt, observer=obs), net


def _extend(values, specs, field):
    # values for the leading parameters (or None), completed with field of the parameters after them (the per-rack
    # deviations a case appends).
    vals = [float(v) for v in values] if values else []
    if len(vals) > len(specs):
        raise ValueError(f"{len(vals)} values for {len(specs)} parameters")
    return vals + [float(getattr(sp, field)) for sp in specs[len(vals):]]


def _solve_l0(p):
    mode = p["mode"]
    dt_s = float(p.get("dt_s", 300.0))
    count = p.get("n_steps") or (round(float(p["duration_s"]) / dt_s) + 1 if p.get("duration_s") else None)
    snaps = _l0_snapshots(p.get("snapshots"), mode, dt_s, count)
    specs = _l0_specs(p, mode)
    prob, net = _l0_problem(p, specs, snaps, mode, dt_s)
    theta = torch.tensor([float(v) for v in _extend(p.get("theta"), specs, "prior_mean")], dtype=torch.float64)
    U, cert = prob.solve(theta)
    out = {
        "status": "succeeded" if cert.converged else "failed",
        "converged": bool(cert.converged),
        "residual_inf": float(cert.residual_inf),
        "residual_relative_to_tolerance": True,
        "iterations": int(cert.iterations),
        "reason": str(cert.reason),
        "method": str(cert.method),
        "wall_s": float(cert.wall_s),
        "model": "zonal network of the L0 mesh",
        "n_zones": int(net.n_cells),
        "network": net.report,
        "param_names": [s.name for s in specs],
        "theta": [float(v) for v in theta.tolist()],
    }
    if not cert.converged:
        return out
    obs = prob.observe(U, theta)
    traj = [{k: float(v[i]) for k, v in obs.items()} for i in range(len(snaps))]
    out["observations"] = traj[-1]
    if mode == "transient":
        out.update(n_steps=len(snaps) - 1, n_snapshots=len(snaps), dt_s=dt_s, trajectory_observations=traj)
    return out


def _solve_l1(p):
    from Pipeline.CoupledNewton.dc_rans_framework import _load_template_request, _run_dc_foam_solve
    from Pipeline.CoupledNewton.patch_binding import _ensure_case_dir
    case_dir = Path(p["case_dir"]).resolve()
    work_dir = Path(p["work_dir"]).resolve()
    audit = _boundary_audit(case_dir, run_acceptance=False)
    if audit.get("dropped_patches"):
        return {
            "status": "failed",
            "converged": False,
            "failure": {"stage": "patch_audit", "reason": f"dropped patches: {audit.get('dropped_patches')}"},
            "patch_audit": audit,
        }
    solve_case = work_dir / "l1_solve_case"
    _ensure_case_dir(solve_case, case_dir, symlink_mesh=True)
    req = _load_template_request(case_dir)
    req["selfStop"] = True
    req["requirePhysicsOnStop"] = True
    req["maxIterations"] = int(p.get("max_iterations", 3000))
    box = _agglomerate_box(p, case_dir)
    if box > 0:
        req["agglomerateBoxM"] = box
    env_ex = {
        "LPCNS_DC_AGGLOMERATE_VRATIO": str(p.get("vratio", 50.0)),
        "LPCNS_DC_AGGLOMERATE_SPLIT_DIR": str(p.get("split_dir", 1)),
        "LPCNS_DC_AGGLOMERATE_ASPECT": str(p.get("aspect", 8.0)),
        "LPCNS_DC_AGGLOMERATE_LAYERS": str(p.get("layers", 1)),
    }
    if p.get("agglomerate_cache"):
        env_ex["LPCNS_DC_AGGLOMERATE_CACHE"] = str(p["agglomerate_cache"])
    extra = list(p.get("extra_solve_flags") or ())
    if p["mode"] == "transient":
        dt_s = float(p.get("dt_s", 300.0))
        extra.extend(["--transient", "--dt-s", str(dt_s)])
    gpu = p.get("gpu")
    sp, cert = _run_dc_foam_solve(solve_case, request=req, extra=tuple(extra), gpu=gpu, env_extra=env_ex)
    return {
        "status": "succeeded" if cert.converged else "failed",
        "converged": bool(cert.converged),
        "residual_inf": float(cert.residual_inf),
        "iterations": int(cert.iterations),
        "reason": str(cert.reason),
        "wall_s": float(cert.wall_s),
        "state_path": str(sp),
        "patch_audit": audit,
    }


def _parse_transient_target(obs_raw):
    if isinstance(obs_raw, list):
        if len(obs_raw) < 2:
            raise ValueError("transient inversion expects at least 2 observation steps")
        keys = sorted({str(k) for step in obs_raw for k in step.keys()})
        return {
            k: torch.tensor([float(step.get(k, float("nan"))) for step in obs_raw], dtype=torch.float64)
            for k in keys
        }, len(obs_raw)
    if isinstance(obs_raw, dict) and obs_raw:
        lengths = {len(v) for v in obs_raw.values() if isinstance(v, (list, tuple))}
        if not lengths or len(lengths) != 1:
            raise ValueError("transient observation dict must map keys to equal-length sequences")
        T = next(iter(lengths))
        if T < 2:
            raise ValueError("transient inversion expects at least 2 observation steps")
        return {str(k): torch.tensor([float(x) for x in v], dtype=torch.float64) for k, v in obs_raw.items()}, T
    raise ValueError("transient inversion expects non-empty list of step dicts or dict of sequences")


def _invert_l0(p):
    from Pipeline.CoupledNewton.steady_framework import Measurements, check_contract, fit
    mode = p["mode"]
    obs_raw = p["observations"]
    dt_s = float(p.get("dt_s", 300.0))
    if mode == "steady":
        if not isinstance(obs_raw, dict) or not obs_raw:
            raise ValueError("steady L0 inversion expects non-empty dict of observations")
        snaps = _l0_snapshots(p.get("snapshots"), mode, dt_s)
        target = {str(k): torch.tensor([float(v)] * len(snaps), dtype=torch.float64) for k, v in obs_raw.items()}
    else:
        target, T = _parse_transient_target(obs_raw)
        snaps = _l0_snapshots(p.get("snapshots"), mode, dt_s, T)
        if len(snaps) != T:
            raise ValueError(f"snapshots length ({len(snaps)}) must match transient observation steps ({T})")
    specs = _l0_specs(p, mode)
    prob, net = _l0_problem(p, specs, snaps, mode, dt_s)
    names = [s.name for s in specs]
    lo = torch.tensor([float(s.lower) for s in specs], dtype=torch.float64)
    hi = torch.tensor([float(s.upper) for s in specs], dtype=torch.float64)
    prior_vals = _extend(p.get("prior"), specs, "prior_mean")
    sigma_vals = _extend(p.get("prior_sigma"), specs, "prior_sigma")
    prior = torch.tensor([float(v) for v in prior_vals], dtype=torch.float64)
    prior_sigma = torch.tensor([float(v) for v in sigma_vals], dtype=torch.float64)
    theta0 = torch.tensor([float(v) for v in (_extend(p.get("theta0"), specs, "prior_mean")
                                              if p.get("theta0") else prior_vals)], dtype=torch.float64)
    free = p.get("free_indices")
    if free is not None and p.get("per_rack_leakage"):
        n_room = len(p.get("prior") or p.get("theta0") or []) or len(specs)
        free = sorted(set(int(i) for i in free) | set(range(n_room, len(specs))))
    # the fit on the network's device (its observations live there)
    dev = prob.device
    target = {k: v.to(dev) for k, v in target.items()}
    lo, hi, prior, prior_sigma, theta0 = (v.to(dev) for v in (lo, hi, prior, prior_sigma, theta0))

    u_prior, cert_prior = prob.solve(theta0)
    if not cert_prior.converged:
        return {"status": "failed", "converged": False, "reason": f"prior solve failed: {cert_prior.reason}"}
    obs_prior = prob.observe(u_prior, theta0)
    prior_rmse = _compute_rmse(obs_prior, target)
    sigma_val = float(p.get("obs_sigma", 0.5))
    meas = Measurements(y=target, sigma={k: sigma_val for k in target})
    res = fit(prob, meas, theta0, prior_mean=prior, prior_sigma=prior_sigma, lower=lo, upper=hi,
              free=free, max_iterations=int(p.get("max_iters", 10)),
              mu0=float(p.get("lam0", 1e-2)), tolerance=float(p.get("tol", 1e-5)),
              jacobian=str(p.get("jacobian", "dense")))
    obs_post = prob.observe(res.state, res.theta)
    post_rmse = _compute_rmse(obs_post, target)
    contract = None
    if p.get("fd_check"):
        keys = list(target.keys())[: min(4, len(target))]
        chk = check_contract(prob, res.state, res.theta, keys=keys, h=1e-5)
        contract = {str(k): float(v) for k, v in chk.items()}
    ok = bool(res.converged) or (math.isfinite(post_rmse) and post_rmse <= prior_rmse)
    return {
        "status": "succeeded" if ok else "failed",
        "converged": bool(res.converged),
        "reason": str(res.reason),
        "model": "zonal network of the L0 mesh",
        "n_zones": int(net.n_cells),
        "misfit": float(res.misfit),
        "data_misfit": float(res.data_misfit),
        "iterations": int(res.iterations),
        "wall_s": float(res.wall_s),
        "forward_solves": int(res.forward_solves),
        "linear_solves": int(res.linear_solves),
        "param_names": names,
        "theta_prior": [float(v) for v in prior.tolist()],
        "theta_post": [float(v) for v in res.theta.tolist()],
        "posterior_sigma": [float(v) for v in res.posterior_sigma.tolist()] if getattr(res, "posterior_sigma", None) is not None else None,
        "prior_rmse": float(prior_rmse),
        "post_rmse": float(post_rmse),
        "observations_prior": _obs_lists(obs_prior),
        "observations_post": _obs_lists(obs_post),
        "history": res.history,
        "certificates": [{"converged": bool(res.converged), "prior_converged": bool(cert_prior.converged),
                          "prior_residual_inf": float(cert_prior.residual_inf), "iterations": int(res.iterations),
                          "reason": str(res.reason)}],
        "contract": contract,
    }


def _first_scalar(entry, default=0.0):
    if not isinstance(entry, dict):
        return float(default)
    for k in ("volumetricFlowRate", "massFlowRate", "deltaT", "uniformValue", "value"):
        if k in entry:
            v = entry[k]
            if isinstance(v, (int, float)):
                return float(v)
            # OpenFOAM's "constant 0.36" / "uniform 291.15" as the dictionary reader returns them
            if isinstance(v, (tuple, list)) and len(v) == 2 and isinstance(v[1], (int, float)):
                return float(v[1])
            if isinstance(v, str):
                m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", v)
                if m:
                    return float(m.group(0))
    return float(default)


# A blanking-panel bucket of one rack (meshConfig.blankingPanelPatches = "perRack"): the rack's stem in the name.
_RACK_BLANKING = re.compile(r"^merged_(?:perforated|wall)BlankingPanel_(.+)_\d+U(?:_(?:master|slave))?$")


def _rack_blanking_groups(baffle_patches):
    # {rack: [patches]} of the per-rack blanking buckets, and the baffle patches that belong to no rack.
    groups, rest = {}, []
    for n in baffle_patches:
        m = _RACK_BLANKING.match(n)
        if m:
            groups.setdefault(m.group(1), []).append(n)
        else:
            rest.append(n)
    return groups, rest


def _rack_of(patch, rack_names):
    # the project's rack a server patch belongs to: the exporter names a server <rack>_<server stem>, so the longest
    # rack name followed by "_" that starts the patch name; the patch itself without the project's racks
    best = None
    for r in rack_names or ():
        if patch.startswith(r + "_") and (best is None or len(r) > len(best)):
            best = r
    return best or patch


def _discover_case_pmap(case_dir: Path, specs, snaps, cfg, *, per_rack_leakage=False, rack_leakage_sigma=0.15,
                        rack_names=None, server_min_flow=0.0):
    # The parameter map of the case (or of cfg). per_rack_leakage: every rack's blanking panels are a group of their
    # own, leakage lsBlank + dlsBlank:<rack>; the deviations are appended to specs (in place) with the prior 0 +-
    # rack_leakage_sigma (log10 open area), so the racks share one level and each may depart a little from it. Needs
    # the per-rack blanking patches of meshConfig.blankingPanelPatches = "perRack".
    from Parse_mesh import dctwin_bc
    from Pipeline.CoupledNewton.patch_binding import PatchParameterMap
    if cfg:
        return PatchParameterMap(
            specs,
            TRef=float(cfg.get("TRef", 297.15)),
            rho=float(cfg.get("rho", 1.19)),
            cp=float(cfg.get("cp", 1005.0)),
            acus=cfg.get("acus"),
            servers=cfg.get("servers"),
            baffles=cfg.get("baffles"),
            baffle_params=cfg.get("baffle_params"),
            walls=cfg.get("walls"),
            wall_ref_K=cfg.get("wall_ref_K"),
            snapshots=snaps,
        )
    c = dctwin_bc.read_case_dicts(case_dir)
    bc = c["bc"]
    TRef = float(dctwin_bc._scalar_entry(c["transport"], "TRef"))
    rho = float(dctwin_bc._scalar_entry(c["transport"], "rho", 1.19)) if hasattr(dctwin_bc, "_scalar_entry") else 1.19
    cp = 1005.0
    acu_map = dctwin_bc.acu_patches(case_dir)
    returns = {v["return"] for v in acu_map.values() if "return" in v} or None
    supplies = {v["supply"] for v in acu_map.values() if "supply" in v} or None
    names = [n for n in bc["U"].keys() if bc["U"][n].get("type") != "processor"]
    roles = {}
    for n in names:
        try:
            roles[n] = dctwin_bc.classify(n, bc, returns, supplies)
        except ValueError:
            roles[n] = "other"

    acus = {}
    for aname, pair in acu_map.items():
        sup = pair.get("supply")
        ret = pair.get("return")
        if not sup:
            continue
        q_nom = max(_first_scalar(bc["U"].get(sup), 1.0), 1e-4)
        t_nom = _first_scalar(bc["T"].get(sup), TRef - 4.0)
        is_ref = bool(ret and roles.get(ret) == "acu_return_ref")
        acus[aname] = {
            "supply": sup,
            "return": ret,
            "pressure_ref": is_ref,
            "nominal_q100": q_nom,
            "q100_is_multiplier": True,
            "nominal_T_sup_K": t_nom,
            "nominal_flow": q_nom,
        }

    # servers: every rack outlet whose temperature is the CBBM condition names its intake (dctwin_bc's own
    # reading of the condition: the paired intake patch and the temperature rise) -- the pairing of the case, not
    # of patch names
    servers = []
    rc = rho * cp
    for nout in (n for n, r in roles.items() if r == "server_outlet"):
        try:
            intake, rise, _ = dctwin_bc._server_outlet(nout, bc["T"][nout])
        except Exception:
            continue
        q_s = max(_first_scalar(bc["U"].get(nout), 0.1), 1e-5)
        servers.append({
            "name": nout,
            "rack": _rack_of(nout, rack_names),
            "inlet": intake,
            "outlet": nout,
            "design_heat_W": rc * q_s * max(float(rise), 0.25),
            "nominal_flow": q_s,
            "q_min": float(server_min_flow) * q_s,
        })
    walls = [n for n, r in roles.items() if r == "wall"]
    baffle_patches = sorted(n for n, r in roles.items() if r == "baffle")
    baffles = {"blank": baffle_patches} if baffle_patches else {}
    baffle_params = {"blank": "lsBlank"} if baffle_patches else {}
    baffle_offsets = {}
    if per_rack_leakage:
        from Pipeline.CoupledNewton.patch_binding import group_offset_specs
        groups, rest = _rack_blanking_groups(baffle_patches)
        if not groups:
            raise ValueError("per-rack leakage: the mesh has no per-rack blanking patches (set meshConfig."
                             "blankingPanelPatches = \"perRack\" and remesh)")
        racks = sorted(groups)
        baffles = {f"blank:{r}": groups[r] for r in racks}
        baffle_params = {f"blank:{r}": "lsBlank" for r in racks}
        baffle_offsets = {f"blank:{r}": f"dlsBlank:{r}" for r in racks}
        if rest:
            baffles["blank"] = rest
            baffle_params["blank"] = "lsBlank"
        known = {sp.name for sp in specs}
        specs.extend(sp for sp in group_offset_specs("dlsBlank", racks, sigma=float(rack_leakage_sigma),
                                                     description="log10 blanking-panel open-area deviation of rack")
                     if sp.name not in known)
    return PatchParameterMap(
        specs,
        TRef=TRef,
        rho=rho,
        cp=cp,
        acus=acus,
        servers=servers,
        baffles=baffles,
        baffle_params=baffle_params,
        baffle_offsets=baffle_offsets,
        walls=walls,
        wall_ref_K=TRef,
        snapshots=snaps,
    )


def _invert_l1(p):
    from Pipeline.CoupledNewton.dc_rans_framework import (
        HallSteadyProblem,
        HallStepModel,
        HallTrajectoryProblem,
        default_datacentre_param_specs,
    )
    from Pipeline.CoupledNewton.patch_binding import HallProbeObserver
    from Pipeline.CoupledNewton.steady_framework import Measurements, check_contract, fit

    case_dir = Path(p["case_dir"]).resolve()
    work_dir = Path(p["work_dir"]).resolve()
    audit = _boundary_audit(case_dir, run_acceptance=False)
    if audit.get("dropped_patches"):
        return {
            "status": "failed",
            "converged": False,
            "reason": f"dropped patches: {audit.get('dropped_patches')}",
            "patch_audit": audit,
        }

    mode = p["mode"]
    obs_raw = p["observations"]
    snaps = p.get("snapshots")
    dt_s = float(p.get("dt_s", 300.0))
    if mode == "steady":
        if not isinstance(obs_raw, dict) or not obs_raw:
            raise ValueError("steady L1 inversion expects non-empty dict of observations")
        if snaps is None:
            snaps = [{}]
        target = {str(k): torch.tensor([float(v)] * len(snaps), dtype=torch.float64) for k, v in obs_raw.items()}
    else:
        target, T = _parse_transient_target(obs_raw)
        if snaps is None:
            snaps = [{"t_s": float(k) * dt_s} for k in range(T)]
        elif len(snaps) != T:
            raise ValueError(f"snapshots length ({len(snaps)}) must match transient observation steps ({T})")

    specs = default_datacentre_param_specs(include_empty=bool(p.get("include_empty", False)), include_rev=bool(p.get("include_rev", False)), include_wall=bool(p.get("include_wall", False)), include_tau=(mode == "transient" or bool(p.get("include_tau", False))),
                                          q100_bounds=tuple(p.get("q100_bounds") or (0.3, 3.0)))
    pmap = _discover_case_pmap(case_dir, specs, snaps, p.get("patch_map_config"),
                               per_rack_leakage=bool(p.get("per_rack_leakage")),
                               rack_leakage_sigma=float(p.get("rack_leakage_sigma", 0.15)),
                               rack_names=p.get("rack_names"),
                               server_min_flow=float(p.get("server_min_flow") or 0.0))
    names = [s.name for s in specs]
    lo = torch.tensor([float(s.lower) for s in specs], dtype=torch.float64)
    hi = torch.tensor([float(s.upper) for s in specs], dtype=torch.float64)
    prior_vals = _extend(p.get("prior"), specs, "prior_mean")
    sigma_vals = _extend(p.get("prior_sigma"), specs, "prior_sigma")
    prior = torch.tensor([float(v) for v in prior_vals], dtype=torch.float64)
    prior_sigma = torch.tensor([float(v) for v in sigma_vals], dtype=torch.float64)
    theta0 = torch.tensor([float(v) for v in (_extend(p.get("theta0"), specs, "prior_mean")
                                              if p.get("theta0") else prior_vals)], dtype=torch.float64)
    free_indices = p.get("free_indices")
    if free_indices is not None and p.get("per_rack_leakage"):
        n_room = len(p.get("prior") or p.get("theta0") or []) or len(specs)
        free_indices = sorted(set(int(i) for i in free_indices) | set(range(n_room, len(specs))))

    return_acus = p.get("return_acus")
    if return_acus is None:
        inferred = [k.split("return_T:", 1)[1] for k in target.keys() if k.startswith("return_T:")]
        return_acus = inferred or list(pmap.acus.keys())

    observer = HallProbeObserver(
        case_dir,
        probe_coords=p.get("probe_coords"),
        probe_names=p.get("probe_names"),
        return_acus=return_acus,
    )
    box = _agglomerate_box(p, case_dir)
    gpu = p.get("gpu")
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    if mode == "steady":
        prob = HallSteadyProblem(
            pmap,
            list(range(len(snaps))),
            case_dir,
            work_dir / "l1_inv_runs",
            observer,
            agglomerate=box,
            device=device,
            gpu=gpu,
            max_iterations=int(p.get("max_solve_iterations", 3000)),
            units=p.get("units", "C"),
        )
    else:
        dts = [dt_s] * (len(snaps) - 1)
        step = HallStepModel(
            pmap,
            case_dir,
            work_dir / "l1_inv_runs",
            observer,
            agglomerate=box,
            device=device,
            gpu=gpu,
            max_iterations=int(p.get("max_solve_iterations", 3000)),
            units=p.get("units", "C"),
            **dict(p.get("step_options") or {}),
        )
        # initial_state: the window starts from that fixed state (a solver state file of this case, e.g. a steady
        # solve's state_path) instead of the steady state of the first snapshot under theta; the first snapshot is
        # the time of that state and its observations are not fitted
        u0 = None
        if p.get("initial_state"):
            u0 = torch.load(p["initial_state"], map_location="cpu", weights_only=False).to(torch.float64).reshape(-1)
            target = {k: v[1:] for k, v in target.items()}
        prob = HallTrajectoryProblem(step, dts, u0=u0, checkpoint_dir=work_dir / "l1_inv_ckpt")

    u_prior, cert_prior = prob.solve(theta0)
    if not cert_prior.converged:
        return {"status": "failed", "converged": False, "reason": f"prior L1 solve failed: {cert_prior.reason}"}
    obs_prior = prob.observe(u_prior, theta0)
    prior_rmse = _compute_rmse(obs_prior, target)

    sigma_val = float(p.get("obs_sigma", 0.5))
    meas = Measurements(y=target, sigma={k: sigma_val for k in target})
    res = fit(
        prob,
        meas,
        theta0,
        prior_mean=prior,
        prior_sigma=prior_sigma,
        lower=lo,
        upper=hi,
        free=free_indices,
        max_iterations=int(p.get("max_iters", 10)),
        mu0=float(p.get("lam0", 1e-2)),
        tolerance=float(p.get("tol", 1e-5)),
        jacobian=str(p.get("jacobian", "dense")),
    )
    obs_post = prob.observe(res.state, res.theta)
    post_rmse = _compute_rmse(obs_post, target)

    contract = None
    if p.get("fd_check"):
        keys = list(target.keys())[: min(4, len(target))]
        chk = check_contract(prob, res.state, res.theta, keys=keys, h=1e-4)
        contract = {str(k): float(v) for k, v in chk.items()}

    ok = bool(res.converged) or (math.isfinite(post_rmse) and post_rmse <= prior_rmse)
    return {
        "status": "succeeded" if ok else "failed",
        "converged": bool(res.converged),
        "reason": str(res.reason),
        "misfit": float(res.misfit),
        "data_misfit": float(res.data_misfit),
        "iterations": int(res.iterations),
        "wall_s": float(res.wall_s),
        "forward_solves": int(res.forward_solves),
        "linear_solves": int(res.linear_solves),
        "param_names": names,
        "theta_prior": [float(v) for v in prior.tolist()],
        "theta_post": [float(v) for v in res.theta.tolist()],
        "prior_rmse": float(prior_rmse),
        "post_rmse": float(post_rmse),
        "observations_prior": _obs_lists(obs_prior),
        "observations_post": _obs_lists(obs_post),
        "history": res.history,
        "certificates": [
            {
                "converged": bool(res.converged),
                "prior_converged": bool(cert_prior.converged),
                "prior_residual_inf": float(cert_prior.residual_inf),
                "residual_inf": 0.0,
                "iterations": int(res.iterations),
                "reason": str(res.reason),
            }
        ],
        "contract": contract,
        "patch_audit": audit,
    }


if task == "audit":
    out = _do_audit(payload)
elif task == "solve_l0":
    out = _solve_l0(payload)
elif task == "solve_l1":
    out = _solve_l1(payload)
elif task == "invert_l0":
    out = _invert_l0(payload)
elif task == "invert_l1":
    out = _invert_l1(payload)
else:
    raise ValueError(f"unknown worker task {task!r}")
print("__DCRANS_JSON__" + json.dumps(out))
"""


def _run_worker(
    worker_task: str,
    payload: Mapping[str, Any],
    *,
    python_exe: Optional[PathLike] = None,
    solver_src: Optional[PathLike] = None,
    gpu: Optional[Union[str, int]] = None,
    timeout: float = 3600.0,
) -> Dict[str, Any]:
    solver_root = _solver_src_root(solver_src)
    py = _resolve_dcrans_python(python_exe)
    env = dict(os.environ)
    pp = [str(solver_root), str(REPO / "src")]
    if env.get("PYTHONPATH"):
        pp.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pp)
    if gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    done = subprocess.run(
        [str(py), "-u", "-c", _WORKER_SCRIPT, worker_task],
        input=json.dumps(_json_safe(payload)),
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    marker = "__DCRANS_JSON__"
    for line in reversed(done.stdout.splitlines()):
        if line.startswith(marker):
            parsed = json.loads(line[len(marker):])
            parsed["_worker_meta"] = {"python": str(py), "solver": _solver_identity(solver_root)}
            return parsed
    raise RuntimeError(
        f"dcRANS worker {worker_task!r} failed (rc={done.returncode}): "
        f"{done.stderr[-1200:] or done.stdout[-1200:]}"
    )


def _project_rack_names(source, case_dir: Optional[Path]) -> Optional[list]:
    """The rack names of the project of a solve, as the exporter writes them into patch names (every character outside
    [A-Za-z0-9_] becomes ``_``): ``source`` when it is a ``.cfdproj``, else the project that owns the case directory
    (``<project dir>/log/<run>``). None when there is none."""
    cands = []
    if not isinstance(source, Mapping) and Path(source).expanduser().is_file():
        cands.append(Path(source).expanduser())
    for d in ([Path(case_dir)] if case_dir is not None else []):
        for up in (d, *list(d.parents)[:3]):
            cands += sorted(up.glob("*.cfdproj"))
    for f in cands:
        try:
            racks = (json.loads(f.read_text(encoding="utf-8")).get("constructions") or {}).get("racks") or {}
        except (OSError, ValueError):
            continue
        names = [re.sub(r"[^A-Za-z0-9_]", "_", str((r.get("meta") or {}).get("name") or "")) for r in racks.values()]
        names = [n for n in names if n]
        if names:
            return names
    return None


def _resolve_case_dir(
    source: Union[PathLike, Mapping[str, Any]],
    *,
    case_dir: Optional[PathLike],
    run_dir: Path,
    options: Optional[Mapping[str, Any]] = None,
    level: str = "L1",
) -> tuple[Optional[Path], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """The prepared case of a solve: ``source`` itself when it is a case directory, else the case meshed from the
    ``.cfdproj`` (L0: a project whose ``meshConfig.coarsenLevel`` is ``"L0"``)."""
    if isinstance(source, Mapping):
        raise TypeError(f"{level} dcRANS requires a case directory path or .cfdproj path")
    if level == "L0" and Path(source).expanduser().is_file():
        level_of = str(((json.loads(Path(source).expanduser().read_text(encoding="utf-8")).get("meshConfig") or {})
                        .get("coarsenLevel") or "")).upper()
        if level_of != "L0":
            raise ValueError(f"L0 dcRANS: {source} is not an L0 project (meshConfig.coarsenLevel {level_of or 'unset'}"
                             "); give an L0 project (e.g. case/Base201_L0) or an L0 case directory")
    src_path = Path(source).expanduser().resolve()
    if src_path.is_dir():
        return src_path, None, None
    if not src_path.is_file():
        raise FileNotFoundError(f"dcRANS source path not found: {src_path}")
    resolved_case = Path(case_dir).expanduser().resolve() if case_dir is not None else (run_dir / "meshed_case")
    if (resolved_case / "constant" / "polyMesh" / "boundary").is_file():
        return resolved_case, None, None
    from agent.scripted_api import air as api_air
    t_m0 = time.perf_counter()
    mesh_res = api_air.run_air_case(
        str(src_path),
        case_dir=str(resolved_case),
        stages=("mesh",),
        max_processes=int((options or {}).get("max_processes", 2)),
    )
    stage_info = {"seconds": time.perf_counter() - t_m0, "status": mesh_res.get("status")}
    if mesh_res.get("status") != "succeeded":
        failure = mesh_res.get("failure") or {"stage": "mesh", "reason": "meshing failed"}
        return None, stage_info, failure
    return resolved_case, stage_info, None


def verify_dcrans_patches(
    case_dir: PathLike,
    *,
    agglomerate_box_m: Optional[float] = 0.4,
    vratio: float = 50.0,
    split_dir: int = -1,
    aspect: float = 8.0,
    expected_patches: Optional[Iterable[str]] = None,
    check_agglomeration: bool = True,
    run_acceptance: bool = True,
    output_root: Optional[PathLike] = None,
    python_exe: Optional[PathLike] = None,
    solver_src: Optional[PathLike] = None,
) -> Dict[str, Any]:
    """Audit boundary/baffle patch preservation across mesh and agglomeration stages."""
    t0 = time.perf_counter()
    started_wall = api_identity.now_sgt()
    case = Path(case_dir).expanduser().resolve()
    if not case.is_dir():
        raise FileNotFoundError(f"case_dir does not exist: {case}")
    run_dir = _make_run_dir(output_root, f"dcrans_audit_{case.name}")
    solver_root = _solver_src_root(solver_src)
    res = _run_worker(
        "audit",
        {
            "case_dir": str(case),
            "work_dir": str(run_dir),
            "agglomerate_box_m": agglomerate_box_m,
            "vratio": vratio,
            "split_dir": split_dir,
            "aspect": aspect,
            "expected_patches": list(expected_patches) if expected_patches is not None else None,
            "check_agglomeration": bool(check_agglomeration),
            "run_acceptance": bool(run_acceptance),
        },
        python_exe=python_exe,
        solver_src=solver_root,
    )
    elapsed = time.perf_counter() - t0
    audit = res.get("audit") or {}
    raw: Dict[str, Any] = {
        "format": DCRANS_FORMAT,
        "kind": "dcrans_audit",
        "status": res.get("status", "failed"),
        "admitted": res.get("status") == "succeeded",
        "failure": None if res.get("status") == "succeeded" else {"stage": "patch_audit", "reason": "patch loss or mismatch"},
        "startedAt": api_identity.iso(started_wall),
        "finishedAt": api_identity.iso(api_identity.now_sgt()),
        "elapsedSeconds": elapsed,
        "stages": {"audit": {"seconds": elapsed}},
        "inputs": {"caseDir": str(case), "agglomerateBoxM": agglomerate_box_m, "expectedPatches": list(expected_patches) if expected_patches is not None else None},
        "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(), "python": api_identity.python_identity(), "solver": res["_worker_meta"]["solver"]},
        "metrics": audit,
        "dropped_patches": audit.get("dropped_patches", []),
        "face_count_mismatches": audit.get("face_count_mismatches", {}),
        "zero_face_patches": audit.get("zero_face_patches", []),
        "runDir": str(run_dir),
        "artifacts": {"resultJson": str(run_dir / "result.json")},
    }
    (run_dir / "result.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    return raw


def solve_dcrans(
    source: Union[PathLike, Mapping[str, Any]],
    *,
    level: str = "L1",
    mode: str = "steady",
    theta: Optional[Sequence[float]] = None,
    snapshots: Optional[list] = None,
    duration_s: Optional[float] = None,
    dt_s: float = 300.0,
    n_steps: Optional[int] = None,
    return_acus: Optional[Sequence[str]] = None,
    units: str = "C",
    probe_coords: Optional[Sequence[Sequence[float]]] = None,
    probe_names: Optional[Sequence[str]] = None,
    agglomerate_box_m: Optional[float] = None,
    vratio: float = 50.0,
    split_dir: int = -1,
    aspect: float = 8.0,
    max_iterations: int = 3000,
    case_dir: Optional[PathLike] = None,
    output_root: Optional[PathLike] = None,
    label: Optional[str] = None,
    gpu: Optional[Union[str, int]] = None,
    python_exe: Optional[PathLike] = None,
    solver_src: Optional[PathLike] = None,
    per_rack_leakage: bool = False,
    rack_leakage_sigma: float = 0.15,
    options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the forward solve at ``L0`` (the zonal network of the L0 mesh) or ``L1`` (the RANS of the L1 mesh)."""
    lvl = str(level).upper()
    m = str(mode).lower()
    if lvl not in ("L0", "L1"):
        raise ValueError(f"unsupported dcRANS level {level!r}; expected 'L0' or 'L1'")
    if m not in ("steady", "transient"):
        raise ValueError(f"unsupported dcRANS mode {mode!r}; expected 'steady' or 'transient'")

    t0 = time.perf_counter()
    started_wall = api_identity.now_sgt()
    run_dir = _make_run_dir(output_root, label or f"dcrans_{lvl.lower()}_{m}")
    solver_root = _solver_src_root(solver_src)
    stages: Dict[str, Any] = {}
    src_id = _cfd_source_identity(source)
    inputs_meta: Dict[str, Any] = {"source": src_id, "level": lvl, "mode": m, "dtS": float(dt_s)}
    if "cfdprojSha256" in src_id:
        inputs_meta["cfdprojSha256"] = src_id["cfdprojSha256"]

    resolved_case, mesh_stage, mesh_fail = _resolve_case_dir(source, case_dir=case_dir, run_dir=run_dir, options=options, level=lvl)
    if mesh_stage is not None:
        stages["mesh"] = mesh_stage
    if mesh_fail is not None or resolved_case is None:
        elapsed = time.perf_counter() - t0
        raw = {
            "format": DCRANS_FORMAT,
            "kind": "dcrans",
            "status": "failed",
            "admitted": False,
            "failure": mesh_fail or {"stage": "mesh", "reason": "meshing failed"},
            "level": lvl,
            "mode": m,
            "startedAt": api_identity.iso(started_wall),
            "finishedAt": api_identity.iso(api_identity.now_sgt()),
            "elapsedSeconds": elapsed,
            "stages": stages,
            "inputs": inputs_meta,
            "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(), "python": api_identity.python_identity(), "solver": _solver_identity(solver_root)},
            "metrics": {},
            "runDir": str(run_dir),
            "artifacts": {"resultJson": str(run_dir / "result.json")},
        }
        (run_dir / "result.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        return raw

    t_s0 = time.perf_counter()
    if lvl == "L0":
        res = _run_worker(
            "solve_l0",
            {
                "rack_names": _project_rack_names(source, resolved_case),
                "per_rack_leakage": bool(per_rack_leakage),
                "rack_leakage_sigma": float(rack_leakage_sigma),
                "case_dir": str(resolved_case),
                "work_dir": str(run_dir),
                "mode": m,
                "theta": list(theta) if theta is not None else None,
                "snapshots": snapshots,
                "duration_s": duration_s,
                "dt_s": float(dt_s),
                "n_steps": n_steps,
                "return_acus": list(return_acus) if return_acus is not None else None,
                "probe_coords": [list(c) for c in probe_coords] if probe_coords is not None else None,
                "probe_names": list(probe_names) if probe_names is not None else None,
                "units": units,
                "patch_map_config": dict((options or {}).get("patch_map_config") or {}) or None,
            },
            python_exe=python_exe,
            solver_src=solver_root,
            gpu=gpu,
        )
    else:
        res = _run_worker(
            "solve_l1",
            {
                "case_dir": str(resolved_case),
                "work_dir": str(run_dir),
                "mode": m,
                "dt_s": float(dt_s),
                "agglomerate_box_m": None if agglomerate_box_m is None else float(agglomerate_box_m),
                "vratio": float(vratio),
                "split_dir": int(split_dir),
                "aspect": float(aspect),
                "max_iterations": int(max_iterations),
                "gpu": gpu,
                "agglomerate_cache": (options or {}).get("agglomerate_cache"),
                "extra_solve_flags": list((options or {}).get("extra_solve_flags") or ()),
            },
            python_exe=python_exe,
            solver_src=solver_root,
            gpu=gpu,
        )
    stages["solve"] = {"seconds": time.perf_counter() - t_s0}

    elapsed = time.perf_counter() - t0
    ok = res.get("status") == "succeeded"
    raw = {
        "format": DCRANS_FORMAT,
        "kind": "dcrans",
        "status": "succeeded" if ok else "failed",
        "admitted": ok,
        "failure": None if ok else (res.get("failure") or {"stage": "solve", "reason": str(res.get("reason", "solve failed"))}),
        "level": lvl,
        "mode": m,
        "startedAt": api_identity.iso(started_wall),
        "finishedAt": api_identity.iso(api_identity.now_sgt()),
        "elapsedSeconds": elapsed,
        "stages": stages,
        "inputs": inputs_meta,
        "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(), "python": api_identity.python_identity(), "solver": res["_worker_meta"]["solver"]},
        "metrics": {k: v for k, v in res.items() if k not in ("status", "_worker_meta", "failure")},
        "runDir": str(run_dir),
        "artifacts": {"resultJson": str(run_dir / "result.json")},
    }
    (run_dir / "result.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    return raw


def invert_dcrans(
    source: Union[PathLike, Mapping[str, Any]],
    observations: Union[PathLike, Mapping[str, Any], Sequence[Mapping[str, Any]]],
    *,
    level: str = "L0",
    mode: str = "steady",
    theta0: Optional[Sequence[float]] = None,
    prior: Optional[Sequence[float]] = None,
    prior_sigma: Optional[Sequence[float]] = None,
    free_indices: Optional[Sequence[int]] = None,
    snapshots: Optional[list] = None,
    return_acus: Optional[Sequence[str]] = None,
    probe_coords: Optional[Sequence[Sequence[float]]] = None,
    probe_names: Optional[Sequence[str]] = None,
    units: str = "C",
    agglomerate_box_m: Optional[float] = None,
    case_dir: Optional[PathLike] = None,
    dt_s: float = 300.0,
    max_iters: int = 10,
    max_solve_iterations: int = 3000,
    obs_sigma: float = 0.5,
    lam0: float = 1e-2,
    tol: float = 1e-5,
    jacobian: str = "dense",
    fd_check: bool = False,
    patch_map_config: Optional[Mapping[str, Any]] = None,
    output_root: Optional[PathLike] = None,
    label: Optional[str] = None,
    gpu: Optional[Union[str, int]] = None,
    python_exe: Optional[PathLike] = None,
    solver_src: Optional[PathLike] = None,
    server_min_flow: float = 0.0,
    include_rev: bool = False,
    initial_state: Optional[PathLike] = None,
    per_rack_leakage: bool = False,
    rack_leakage_sigma: float = 0.15,
    options: Optional[Mapping[str, Any]] = None,
    worker_timeout_s: float = 3600.0,
) -> Dict[str, Any]:
    """Run the JFGNK calibration (steady or BDF1 transient) at L0 (the zonal network of the L0 mesh, the window
    fitted at once) or L1 (the RANS of the L1 mesh)."""
    lvl = str(level).upper()
    m = str(mode).lower()
    if lvl not in ("L0", "L1"):
        raise ValueError(f"unsupported dcRANS level {level!r}")
    if m not in ("steady", "transient"):
        raise ValueError(f"unsupported dcRANS mode {mode!r}")

    obs_obj: Any = observations
    if isinstance(observations, (str, os.PathLike)):
        obs_obj = json.loads(Path(observations).expanduser().resolve().read_text(encoding="utf-8"))

    t0 = time.perf_counter()
    started_wall = api_identity.now_sgt()
    run_dir = _make_run_dir(output_root, label or f"dcrans_invert_{lvl.lower()}_{m}")
    solver_root = _solver_src_root(solver_src)
    stages: Dict[str, Any] = {}
    src_id = _cfd_source_identity(source)
    inputs_meta: Dict[str, Any] = {"source": src_id, "level": lvl, "mode": m, "dtS": float(dt_s)}
    if "cfdprojSha256" in src_id:
        inputs_meta["cfdprojSha256"] = src_id["cfdprojSha256"]

    resolved_case, mesh_stage, mesh_fail = _resolve_case_dir(source, case_dir=case_dir, run_dir=run_dir, options=options, level=lvl)
    if mesh_stage is not None:
        stages["mesh"] = mesh_stage
    if mesh_fail is not None or resolved_case is None:
        elapsed = time.perf_counter() - t0
        raw = {
            "format": DCRANS_FORMAT,
            "kind": "dcrans_inversion",
            "status": "failed",
            "admitted": False,
            "failure": mesh_fail or {"stage": "mesh", "reason": "meshing failed"},
            "level": lvl,
            "mode": m,
            "startedAt": api_identity.iso(started_wall),
            "finishedAt": api_identity.iso(api_identity.now_sgt()),
            "elapsedSeconds": elapsed,
            "stages": stages,
            "inputs": inputs_meta,
            "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(), "python": api_identity.python_identity(), "solver": _solver_identity(solver_root)},
            "metrics": {},
            "runDir": str(run_dir),
            "artifacts": {"resultJson": str(run_dir / "result.json")},
        }
        (run_dir / "result.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        return raw

    if lvl == "L0":
        res = _run_worker(
            "invert_l0",
            {
                "rack_names": _project_rack_names(source, resolved_case),
                "q100_bounds": (options or {}).get("q100_bounds"),
                "server_min_flow": float(server_min_flow),
                "include_rev": bool(include_rev),
                "per_rack_leakage": bool(per_rack_leakage),
                "rack_leakage_sigma": float(rack_leakage_sigma),
                "case_dir": str(resolved_case),
                "work_dir": str(run_dir),
                "mode": m,
                "observations": obs_obj,
                "theta0": list(theta0) if theta0 is not None else None,
                "prior": list(prior) if prior is not None else None,
                "prior_sigma": list(prior_sigma) if prior_sigma is not None else None,
                "free_indices": list(free_indices) if free_indices is not None else None,
                "snapshots": snapshots,
                "return_acus": list(return_acus) if return_acus is not None else None,
                "probe_coords": [list(c) for c in probe_coords] if probe_coords is not None else None,
                "probe_names": list(probe_names) if probe_names is not None else None,
                "units": units,
                "dt_s": float(dt_s),
                "max_iters": int(max_iters),
                "obs_sigma": float(obs_sigma),
                "lam0": float(lam0),
                "tol": float(tol),
                "jacobian": str(jacobian),
                "fd_check": bool(fd_check),
                "patch_map_config": dict(patch_map_config) if patch_map_config is not None else None,
            },
            python_exe=python_exe,
            solver_src=solver_root,
            gpu=gpu,
            timeout=float(worker_timeout_s),
        )
    else:
        res = _run_worker(
            "invert_l1",
            {
                "rack_names": _project_rack_names(source, resolved_case),
                "q100_bounds": (options or {}).get("q100_bounds"),
                "initial_state": str(Path(initial_state).expanduser().resolve()) if initial_state else None,
                "step_options": dict((options or {}).get("step_options") or {}),
                "server_min_flow": float(server_min_flow),
                "include_rev": bool(include_rev),
                "per_rack_leakage": bool(per_rack_leakage),
                "rack_leakage_sigma": float(rack_leakage_sigma),
                "case_dir": str(resolved_case),
                "mode": m,
                "observations": obs_obj,
                "theta0": list(theta0) if theta0 is not None else None,
                "prior": list(prior) if prior is not None else None,
                "prior_sigma": list(prior_sigma) if prior_sigma is not None else None,
                "free_indices": list(free_indices) if free_indices is not None else None,
                "snapshots": snapshots,
                "return_acus": list(return_acus) if return_acus is not None else None,
                "probe_coords": [list(c) for c in probe_coords] if probe_coords is not None else None,
                "probe_names": list(probe_names) if probe_names is not None else None,
                "units": units,
                "agglomerate_box_m": None if agglomerate_box_m is None else float(agglomerate_box_m),
                "dt_s": float(dt_s),
                "max_iters": int(max_iters),
                "max_solve_iterations": int(max_solve_iterations),
                "obs_sigma": float(obs_sigma),
                "lam0": float(lam0),
                "tol": float(tol),
                "jacobian": str(jacobian),
                "fd_check": bool(fd_check),
                "patch_map_config": dict(patch_map_config) if patch_map_config is not None else None,
                "gpu": gpu,
                "work_dir": str(run_dir),
            },
            python_exe=python_exe,
            solver_src=solver_root,
            gpu=gpu,
            timeout=float(worker_timeout_s),
        )

    elapsed = time.perf_counter() - t0
    stages["invert"] = {"seconds": elapsed}
    ok = res.get("status") == "succeeded"
    raw = {
        "format": DCRANS_FORMAT,
        "kind": "dcrans_inversion",
        "status": "succeeded" if ok else "failed",
        "admitted": ok,
        "failure": None if ok else {"stage": "invert", "reason": str(res.get("reason", "inversion failed"))},
        "level": lvl,
        "mode": m,
        "startedAt": api_identity.iso(started_wall),
        "finishedAt": api_identity.iso(api_identity.now_sgt()),
        "elapsedSeconds": elapsed,
        "stages": stages,
        "inputs": inputs_meta,
        "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(), "python": api_identity.python_identity(), "solver": res["_worker_meta"]["solver"]},
        "metrics": {k: v for k, v in res.items() if k not in ("status", "_worker_meta")},
        **{k: v for k, v in res.items() if k in ("param_names", "theta_prior", "theta_post", "prior_rmse", "post_rmse", "history", "certificates", "contract", "misfit", "data_misfit", "iterations", "converged")},
        "runDir": str(run_dir),
        "artifacts": {"resultJson": str(run_dir / "result.json")},
    }
    (run_dir / "result.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    return raw
