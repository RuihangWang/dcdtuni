"""The caller's contract of an OpenFOAM-layout run directory (dcdtuni / dctwin air runner), served by this solver.

The runner prepares a decomposed OpenFOAM case (``processorN/constant/polyMesh``, ``processorN/0``, ``system``,
``constant``), launches a solver in it and afterwards reads, from that directory only:

* the solver's log lines (``Exec : ...Foam``, ``Time = k``, ``Solving for F, Initial residual = ...``, ``time step
  continuity errors``, ``End``) relayed from stdout;
* ``postProcessing/avgT_<patch>/0/surfaceFieldValue.dat`` (area-mean T of every ACU return patch) and
  ``postProcessing/{heatT_,phi_}<patch>/0/surfaceFieldValue.dat`` (sum(phi T) and sum(phi) of every ACU supply and
  return patch), one row per iteration: the return-air plateau and the energy closure of its convergence verdict;
* ``postProcessing/probes/0/{T,U,p,p_rgh}`` at the ``probeLocations`` of ``system/controlDict``;
* the last time directory of every rank with ``T``, ``U``, ``p_rgh``, ``k``, ``epsilon`` (ASCII volFields with explicit
  patch values) and ``phi`` (the face flux): its field metrics;
* ``system/controlDict``: ``stopAt writeNow`` written there by the runner's watcher ends the solve.

:class:`FoamBridge` is the pseudo-time driver's ``stop_rule``: called with every iterate, it writes the monitor rows
and log lines of each ACCEPTED iterate, applies the caller's own stop rule to them (the thresholds come from the
request: ``stopToleranceK``, ``energyTolerance``, ``window``, ``declaredHeatW``) and honours ``stopAt writeNow``;
``finish(x)`` writes the final time directory.  The rule mirrors dcdtuni ``runner/convergence.py``
(``temperature_series_verdict`` + ``energy_balance_verdict``); the verdict of record stays the runner's, computed from
the files written here.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
try:
    import tomllib
except ModuleNotFoundError:                 # Python 3.10
    import tomli as tomllib
from pathlib import Path

import h5py
import numpy as np
import torch

__all__ = ["FoamBridge", "CASES", "RESULTS", "write_surface_scalar_field", "probe_locations", "control_entry",
           "monitored_patches"]

CASES: dict[str, object] = {}       # run directory -> FoamCase, handed over in-process by entries/dc_foam_solve.py
RESULTS: dict[str, dict] = {}       # run directory -> what finish() returned
OUT = None                          # the stream of the caller's log lines (entries/dc_foam_solve.py: the real stdout)

FIELD_LOG_NAMES = ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "epsilon")
_STOP_AT = re.compile(r"(?m)^\s*stopAt\s+(\w+)\s*;")
_END_TIME = re.compile(r"(?m)^\s*endTime\s+([0-9.eE+-]+)\s*;")


def control_entry(text: str, key: str):
    m = re.search(rf"(?m)^\s*{key}\s+([^;\s]+)\s*;", text)
    return m.group(1) if m else None


def probe_locations(control_text: str) -> tuple[np.ndarray, int, list[str]]:
    """``(locations [P, 3], writeInterval, fields)`` of the ``probes`` function object (empty when there is none)."""
    m = re.search(r"\bprobes\s*\{", control_text)
    if not m:
        return np.zeros((0, 3)), 1, []
    depth, i = 1, m.end()
    while depth and i < len(control_text):
        depth += {"{": 1, "}": -1}.get(control_text[i], 0)
        i += 1
    block = control_text[m.end():i]
    loc = re.search(r"probeLocations\s*\(((?:\s*\([^()]*\)\s*)*)\)", block)
    pts = np.array([[float(v) for v in p.split()] for p in re.findall(r"\(([^()]*)\)", loc.group(1))]) if loc else None
    if pts is None or pts.size == 0:
        return np.zeros((0, 3)), 1, []
    wi = re.search(r"writeInterval\s+(\d+)\s*;", block)
    fl = re.search(r"fields\s*\(([^()]*)\)", block)
    return pts.reshape(-1, 3), int(wi.group(1)) if wi else 1, (fl.group(1).split() if fl else ["T"])


def monitored_patches(control_text: str) -> tuple[list[str], list[str]]:
    """``(returns, supplies)`` from the runner's function objects: ``avgT_<p>`` names an ACU return, and every
    ``heatT_<p>`` that is not a return is an ACU supply (the patch is the block's ``name`` entry)."""
    def names(prefix):
        out = []
        for m in re.finditer(rf"(?m)^\s*{prefix}\w+\s*\{{([^{{}}]*)\}}", control_text):
            n = re.search(r"\bname\s+([^;\s]+)\s*;", m.group(1))
            if n and n.group(1) not in out:
                out.append(n.group(1))
        return out
    ret = names("avgT_")
    return ret, [p for p in names("heatT_") if p not in ret]


def _g(v: float) -> str:
    return f"{v:.9e}"


_COUPLED = ("cyclic", "cyclicAMI", "cyclicSlip", "empty", "wedge", "symmetry", "symmetryPlane", "processor")


class FoamBridge:
    needs_flux = True               # the driver re-evaluates the residual at x before the call when it holds another

    def __init__(self, cs, case_dir, run_dir, request: dict, slices: dict, case=None, out=None):
        self.cs, self.run, self.case_dir = cs, Path(run_dir), Path(case_dir)
        self.req, self.out, self.case = request, out or OUT or sys.stdout, case
        self.t_start = time.perf_counter()
        dev = cs.device
        self.dev = dev
        self.gpu: dict = {}
        h5s = sorted(self.case_dir.glob("*.h5"))
        if len(h5s) != 1:
            raise FileNotFoundError(f"{case_dir}: expected one .h5, found {h5s}")
        self.map = json.load(open(self.case_dir / "dctwin_map.json"))
        self.TRef = float(self.map["TRef"])
        patches = tomllib.load(open(self.case_dir / "patchinfo.toml", "rb"))["patch"]
        self.span = {p["patch_name"]: (int(p["start_face"]), int(p["n_faces"])) for p in patches.values()}
        bf_idx = np.concatenate([
            np.arange(int(p["start_face"]), int(p["start_face"]) + int(p["n_faces"]), dtype=np.int64)
            for p in patches.values()
            if int(p["n_faces"]) > 0 and p.get("geom_type", p.get("patch_type", "patch")) not in _COUPLED
        ] or [np.zeros(0, dtype=np.int64)])
        self._bf = torch.as_tensor(bf_idx, device=dev)
        self._nu = float(request.get("nu", 1.5e-5))
        self.require_physics_on_stop = bool(request.get("requirePhysicsOnStop", True))
        self.speed_factor = float(request.get("speedFactor", 6.0))
        with h5py.File(h5s[0], "r") as f:
            area = np.asarray(f["face|face_area"], dtype=np.float64).reshape(-1)
            nbr = np.asarray(f["cpd|neighbor_cell"])
            cpos = np.asarray(f["cell|cell_pos"], dtype=np.float64)
        self.n_face = int(area.shape[0])
        self.area = torch.as_tensor(area, device=dev)
        self.owner = torch.as_tensor(nbr[0].astype(np.int64), device=dev)
        self.ghost = torch.as_tensor(nbr[1].astype(np.int64), device=dev)      # neighbour-or-ghost compound cell
        self.n = int(cs.n_cell)
        self.sl = slices
        backend = cs._warp
        self.W = getattr(backend, "W", None) or getattr(backend, "implicit", backend)
        self.rho_cp = float(request.get("rhoCp", 1.19 * 1005.0))
        self.control = self.run / "system" / "controlDict"
        ctl = self.control.read_text(encoding="utf-8", errors="replace") if self.control.is_file() else ""
        ret0, sup0 = monitored_patches(ctl)
        ret0, sup0 = request.get("returnPatches", ret0), request.get("supplyPatches", sup0)
        self.supply = [p for p in sup0 if self.span.get(p, (0, 0))[1] > 0]
        self.ret = [p for p in ret0 if self.span.get(p, (0, 0))[1] > 0]
        missing = [p for p in list(sup0) + list(ret0) if p not in self.span]
        if missing:
            raise KeyError(f"foam bridge: monitored patches not in the mesh: {missing}")
        if not self.ret:
            raise ValueError("foam bridge: no ACU return patch to monitor")
        self.mon = self.ret + [p for p in self.supply if p not in self.ret]
        faces, seg = [], []
        for i, p in enumerate(self.mon):
            s, k = self.span[p]
            faces.append(torch.arange(s, s + k, device=dev))
            seg.append(torch.full((k,), i, dtype=torch.long, device=dev))
        self.m_faces, self.m_seg = torch.cat(faces), torch.cat(seg)
        self.m_area = self.area[self.m_faces]
        self.m_ghost = self.ghost[self.m_faces]
        self.m_asum = torch.zeros(len(self.mon), dtype=torch.float64, device=dev).index_add_(0, self.m_seg, self.m_area)
        self.tol = float(request.get("stopToleranceK", 0.5))
        self.window = int(request.get("window", 100))
        self.heat_tol = float(request.get("energyTolerance", 0.01))
        self.change_min = float(request.get("changeMinK", 0.05))
        self.plateau_fraction = float(request.get("plateauFraction", 0.01))
        # "range" (default): every return flat within the tolerance over the window.  "mean": the window mean of
        # every return moved by at most the tolerance against the previous window, and the heat closure of the
        # window mean -- for a pseudo-time iteration that settles into a bounded oscillation (the buoyant plumes of
        # a hall), whose window mean is the steady answer while no single iterate is
        self.plateau_mode = str(request.get("plateauMode", "range"))
        dh = request.get("declaredHeatW")
        self.declared = float(dh) if dh is not None and float(dh) > 0 else None
        self.self_stop = bool(request.get("selfStop", True))
        self._ctl_mtime = None
        self.max_it = int(float(request.get("maxIterations") or control_entry(ctl, "endTime") or 1500))
        pts, self.p_every, self.p_fields = probe_locations(ctl)
        self.p_cells = None
        if pts.shape[0]:
            from scipy.spatial import cKDTree
            self.p_cells = torch.as_tensor(cKDTree(cpos).query(pts)[1].astype(np.int64), device=dev)
            self.p_pts = pts
        self.T_ret, self.wsum, self.fsum = [], [], []        # per accepted iterate: [returns], [mon], [mon]
        self.k = self.k_next = 0
        self.r0 = None
        self._last = self._last_vals = None
        self.step_info = None
        self.cont_cumulative = 0.0
        self.last = {}
        self.stop_reason = None
        self._files = {}
        self._open_monitors()
        if os.environ.get("LPCNS_DC_INIT_FROM"):
            self._init_state_from(os.environ["LPCNS_DC_INIT_FROM"], case)

    def _init_state_from(self, spec: str, case) -> None:
        """Diagnostics: ``LPCNS_DC_INIT_FROM=<run dir>:<time>`` starts the march from the fields of another run of
        the same mesh and decomposition (an OpenFOAM solution, or this solver's own), through the solver's
        ``LPCNS_ODIL_INIT_STATE``; the cold-start residual stays the merit's reference."""
        from Parse_mesh.foamio.fields import read_vol_field
        src, _, t = spec.rpartition(":")
        if case is None:
            raise ValueError("LPCNS_DC_INIT_FROM needs the decomposed case of this run")
        x = np.zeros((7, self.n))
        seen = np.zeros(self.n, dtype=bool)
        for rm in case.ranks:
            ok = rm.cell_global >= 0
            g = rm.cell_global[ok]
            d = Path(src) / rm.path.name / t
            x[0:3, g] = read_vol_field(d / "U", rm.n_cells).internal.reshape(-1, 3)[ok].T
            x[3, g] = read_vol_field(d / "p_rgh", rm.n_cells).internal.reshape(-1)[ok]
            x[4, g] = read_vol_field(d / "T", rm.n_cells).internal.reshape(-1)[ok] - self.TRef
            x[5, g] = read_vol_field(d / "k", rm.n_cells).internal.reshape(-1)[ok]
            x[6, g] = read_vol_field(d / "epsilon", rm.n_cells).internal.reshape(-1)[ok]
            seen[g] = True
        if not seen.all():
            raise ValueError(f"LPCNS_DC_INIT_FROM: {int((~seen).sum())} cells of this mesh are not in {src}")
        out = Path(self.run) / "gpuNewton" / "init_state.pt"
        torch.save(torch.as_tensor(x.reshape(-1)), out)
        os.environ["LPCNS_ODIL_INIT_STATE"] = str(out)

    # ------------------------------------------------------------------------------------------- files
    def _open(self, rel: str, header: str):
        path = self.run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "w", encoding="utf-8")
        fh.write(header)
        self._files[rel] = fh
        return fh

    def _open_monitors(self):
        counts = {p: self.span[p][1] for p in self.mon}
        asum = dict(zip(self.mon, self.m_asum.tolist()))
        for p in self.mon:
            safe = p.replace(" ", "_")
            head = (f"# Region type : patch {p}\n# Faces       : {counts[p]}\n# Area        : {asum[p]:.6e}\n"
                    "# Scale factor: 1.000000e+00\n")
            if p in self.ret:
                self._open(f"postProcessing/avgT_{safe}/0/surfaceFieldValue.dat", head + "# Time        \tareaAverage(T)\n")
            self._open(f"postProcessing/heatT_{safe}/0/surfaceFieldValue.dat",
                       head + "# Weight field: phi\n# Time        \tweightedSum(T)\n")
            self._open(f"postProcessing/phi_{safe}/0/surfaceFieldValue.dat", head + "# Time        \tsum(phi)\n")
        if self.p_cells is not None:
            head = "".join(f"# Probe {i} ({p[0]:g} {p[1]:g} {p[2]:g})\n" for i, p in enumerate(self.p_pts))
            head += "# Time        " + "".join(f"{i:<14d}" for i in range(self.p_pts.shape[0])) + "\n"
            for f in self.p_fields:
                if f in ("T", "U", "p", "p_rgh"):
                    self._open(f"postProcessing/probes/0/{f}", head)
        for fh in self._files.values():
            fh.flush()

    def close(self):
        for fh in self._files.values():
            fh.close()
        self._files = {}

    # ----------------------------------------------------------------------------------------- state
    def _q(self):
        """The compound state (cells + one ghost per boundary face) of the residual's last evaluation."""
        import warp as wp
        return wp.to_torch(self.W._buffers(0, 1)["q"]).reshape(-1, 7)

    def _phi(self):
        """Face flux (m3/s), owner -> neighbour / outward on boundary faces, of the residual's last evaluation."""
        return self.W.face_volumetric_flux.reshape(-1) * self.area

    def _monitors(self):
        q = self._q()
        Tf = q[self.m_ghost, 4] + self.TRef
        phi = self._phi()[self.m_faces]
        z = torch.zeros(len(self.mon), dtype=torch.float64, device=self.dev)
        avg = z.clone().index_add_(0, self.m_seg, self.m_area * Tf) / self.m_asum
        ws = z.clone().index_add_(0, self.m_seg, phi * Tf)
        fs = z.clone().index_add_(0, self.m_seg, phi)
        pv = q[self.p_cells] if self.p_cells is not None and self.k_next % self.p_every == 0 else None
        return torch.stack([avg, ws, fs]).cpu(), (pv.cpu() if pv is not None else None)

    def _scaled_residuals(self, x, R):
        """Per-field residuals in the manner of OpenFOAM's scaled residual, for the caller's log: sum|R_f| over
        sum(a_P |f - mean(f)|) with a_P = half the absolute face flux through the cell (the upwind convection
        diagonal); the continuity row over the total a_P.  Returns (seven values, global continuity error)."""
        n = self.n
        phi = self._phi().abs().to(torch.float64)
        a_p = torch.zeros(n, dtype=torch.float64, device=self.dev).index_add_(0, self.owner, phi)
        inner = self.ghost < n
        a_p.index_add_(0, self.ghost[inner], phi[inner])
        a_p *= 0.5
        X, Rm = x.reshape(7, n), R.reshape(7, n)
        norm = ((X - X.mean(dim=1, keepdim=True)).abs() * a_p).sum(dim=1)
        total = a_p.sum()
        norm[3] = total
        # OpenFOAM's normalisation bounds its residual by 1; this one is not bounded near a uniform field
        scaled = (Rm.abs().sum(dim=1) / norm.clamp_min(1e-300)).clamp_max(1.0)
        return scaled.tolist(), float(Rm[3].sum() / total.clamp_min(1e-300))

    # ------------------------------------------------------------------------------------- stop rule
    def _verdict(self) -> tuple[bool, dict]:
        n = len(self.T_ret)
        info = {"iterations": n}
        if n < self.window:
            info["window"] = f"{n}/{self.window}"
            return False, info
        T = np.asarray(self.T_ret)
        rng = T.max(0) - T.min(0)
        last = T[-self.window:]
        pp = last.max(0) - last.min(0)
        drift = np.abs(last[-1] - last[0])
        tol = np.maximum(self.tol, self.plateau_fraction * rng)
        if self.plateau_mode == "mean":
            if n < 2 * self.window:
                info["window"] = f"{n}/{2 * self.window} (two windows for the mean)"
                return False, info
            mdrift = np.abs(last.mean(0) - T[-2 * self.window:-self.window].mean(0))
            sensors = bool(np.all(rng >= self.change_min) and np.all(mdrift <= tol))
            info.update(mean_drift_K=float(mdrift.max()))
        else:
            sensors = bool(np.all(rng >= self.change_min) and np.all(pp <= tol) and np.all(drift <= tol))
        info.update(peak_to_peak_K=float(pp.max()), moved_min_K=float(rng.min()))
        ns = len(self.ret)
        idx_s = [self.mon.index(p) for p in self.supply]
        W, F = np.asarray(self.wsum[-self.window:]), np.asarray(self.fsum[-self.window:])
        fs = F[:, idx_s].sum(1)
        t_ref = np.where(np.abs(fs) > 1e-12, W[:, idx_s].sum(1) / np.where(np.abs(fs) > 1e-12, fs, 1.0), 0.0)
        removed = self.rho_cp * (W - t_ref[:, None] * F).sum(1)
        if not np.isfinite(removed).all():
            return False, info | {"energy": "non-finite"}
        if self.declared is not None and self.plateau_mode == "mean":
            worst = float(abs(removed.mean() - self.declared) / self.declared)
            info.update(heat_rel=float(removed.mean() / self.declared - 1.0), window_mean_heat_rel=worst)
        elif self.declared is not None:
            worst = float(np.abs(removed - self.declared).max() / self.declared)
            info.update(heat_rel=float(removed[-1] / self.declared - 1.0), window_max_heat_rel=worst)
        else:
            mean = float(removed.mean())
            worst = float((removed.max() - removed.min()) / abs(mean)) if mean else math.inf
            info.update(heat_W=float(removed[-1]), window_heat_drift=worst)
        return sensors and worst <= self.heat_tol, info

    def _physics_ok_now(self, last_info: dict | None = None) -> bool:
        with torch.no_grad():
            q = self._q()
            phi = self._phi()
            if self._bf.numel() > 0:
                ph = phi[self._bf]
                inflow = (-ph[ph < 0]).sum().clamp_min(1e-300)
                mass_ok = bool((ph.sum().abs() / inflow) <= 0.01)
                g = self.ghost[self._bf]
                Tb = q[g, 4] + self.TRef
                t_lo = float(Tb.min().item()) - 2.0
                t_hi = float(Tb.max().item()) + 5.0
                ub = float(torch.linalg.vector_norm(q[g, 0:3], dim=1).max().item())
            else:
                mass_ok = True
                t_lo, t_hi, ub = -math.inf, math.inf, 0.0
            Tc = q[:self.n, 4] + self.TRef
            temp_ok = bool(float(Tc.min().item()) >= t_lo and float(Tc.max().item()) <= t_hi)
            uc = float(torch.linalg.vector_norm(q[:self.n, 0:3], dim=1).max().item())
            speed_ok = bool(uc <= max(self.speed_factor * ub, 30.0))
            k_c, eps_c = q[:self.n, 5], q[:self.n, 6]
            nut_max = float((0.09 * k_c * k_c / eps_c.clamp_min(1e-300)).max().item())
            turb_ok = bool(float(k_c.min().item()) > 0.0 and float(eps_c.min().item()) > 0.0 and nut_max / self._nu <= 1e5)
            heat = (last_info or {}).get("heat_rel")
            heat_ok = bool(heat is None or abs(float(heat)) <= 0.05)
            return mass_ok and heat_ok and temp_ok and speed_ok and turb_ok

    def _stop_requested(self) -> bool:
        try:
            mt = self.control.stat().st_mtime_ns
        except OSError:
            return False
        if mt == self._ctl_mtime:
            return False
        self._ctl_mtime = mt
        m = _STOP_AT.search(self.control.read_text(encoding="utf-8", errors="replace"))
        return bool(m and m.group(1) in ("writeNow", "noWriteNow", "nextWrite"))

    def _sample_gpu(self) -> None:
        """The device's memory in use now (all processes), kept as the largest seen: what the caller reports as the
        solve's footprint and what its memory estimate for the next mesh is calibrated on."""
        if self.dev.type != "cuda":
            return
        free, total = torch.cuda.mem_get_info(self.dev)
        used = (total - free) / 2 ** 20
        if used > self.gpu.get("peakUsedMiB", 0.0):
            self.gpu.update(totalMiB=round(total / 2 ** 20), peakUsedMiB=round(used))

    def __call__(self, x, R, it) -> bool:
        r = R.reshape(7, self.n)
        norms = torch.linalg.vector_norm(r, dim=1)
        if self.r0 is None:
            self.r0 = norms.clamp_min(1e-300)
            self._ctl_mtime = self.control.stat().st_mtime_ns if self.control.is_file() else None
            return False
        if x is self._last:
            return False
        self.k_next = self.k + 1
        vals, pv = self._monitors()
        if self.T_ret and self._last_vals is not None and torch.equal(vals, self._last_vals):
            return False                                   # a rejected step: the same iterate again
        self._last, self._last_vals = x, vals
        self.k = k = self.k_next
        if k % 10 == 1:
            self._sample_gpu()
        avg, ws, fs = (v.tolist() for v in vals)
        nr = len(self.ret)
        self.T_ret.append(avg[:nr])
        self.wsum.append(ws)
        self.fsum.append(fs)
        for i, p in enumerate(self.mon):
            safe = p.replace(" ", "_")
            if i < nr:
                self._files[f"postProcessing/avgT_{safe}/0/surfaceFieldValue.dat"].write(f"{k:<14d}\t{_g(avg[i])}\n")
            self._files[f"postProcessing/heatT_{safe}/0/surfaceFieldValue.dat"].write(f"{k:<14d}\t{_g(ws[i])}\n")
            self._files[f"postProcessing/phi_{safe}/0/surfaceFieldValue.dat"].write(f"{k:<14d}\t{_g(fs[i])}\n")
        if pv is not None:
            pvl = pv.tolist()
            rows = {"T": "".join(f"{v[4] + self.TRef:<14.6g}" for v in pvl),
                    "p_rgh": "".join(f"{v[3]:<14.6g}" for v in pvl), "p": "".join(f"{v[3]:<14.6g}" for v in pvl),
                    "U": "".join(f"({v[0]:.6g} {v[1]:.6g} {v[2]:.6g})  " for v in pvl)}
            for f, row in rows.items():
                fh = self._files.get(f"postProcessing/probes/0/{f}")
                if fh is not None:
                    fh.write(f"{k:<14d}{row}\n")
        for fh in self._files.values():
            fh.flush()
        rel, cont_global = self._scaled_residuals(x, R)
        self.cont_cumulative += cont_global
        info = self.step_info or {}
        lin, nit = float(info.get("inner_rtol", 0.0) or 0.0), int(info.get("inner_iterations", 0) or 0)
        el = time.perf_counter() - self.t_start
        lines = [f"Time = {k}", ""]
        for name, v in zip(FIELD_LOG_NAMES, rel):
            lines.append(f"gpuNewton:  Solving for {name}, Initial residual = {v:.6g}, Final residual = {v * lin:.6g}, "
                         f"No Iterations {nit}")
        lines.append(f"time step continuity errors : sum local = {rel[3]:.6g}, global = {cont_global:.6g}, "
                     f"cumulative = {self.cont_cumulative:.6g}")
        lines.append(f"ExecutionTime = {el:.2f} s  ClockTime = {int(el)} s")
        lines.append("")
        self.out.write("\n".join(lines) + "\n")
        self.out.flush()
        hit, self.last = self._verdict()
        if self._stop_requested():
            self.stop_reason = "stopAt writeNow (the caller's watcher)"
            return True
        if k >= self.max_it:
            self.stop_reason = f"endTime {self.max_it}"
            return True
        if hit and self.self_stop and (not self.require_physics_on_stop or self._physics_ok_now(self.last)):
            self.stop_reason = "the caller's rule (return-air plateau and energy closure) met"
            return True
        return False

    # ----------------------------------------------------------------------------------------- output
    def _mass_diagnostic(self, x, R, case) -> None:
        """LPCNS_FOAM_BRIDGE_DIAG=1: where the final iterate's volume balance goes -- the continuity rows against the
        divergence of the reported face flux, and the flux through every patch type; the state is kept as
        ``<run>/gpuNewton/final_state.pt``."""
        n = self.n
        phi = self._phi().detach().to(torch.float64)
        inner = self.ghost < n
        div = torch.zeros(n, dtype=torch.float64, device=self.dev).index_add_(0, self.owner, phi)
        div.index_add_(0, self.ghost[inner], -phi[inner])
        rc = R.detach().reshape(7, n)[3].to(torch.float64)
        ratio = float((rc * div).sum() / (div * div).sum().clamp_min(1e-300))
        print(f"foam-bridge diag: sum(div phi) {float(div.sum()):+.6e}, sum(continuity rows) {float(rc.sum()):+.6e}, "
              f"|div| {float(div.norm()):.4e}, |rows| {float(rc.norm()):.4e}, rows ~ {ratio:+.4e} x div, "
              f"|rows - ratio div| {float((rc - ratio * div).norm()):.4e}; boundary faces {int((~inner).sum())}, "
              f"sum(phi) there {float(phi[~inner].sum()):+.6e}", flush=True)
        by_type: dict = {}
        ph = phi.cpu().numpy()
        for p in case.patches:
            if p.size:
                v = by_type.setdefault(p.type, [0, 0.0, 0.0])
                seg = ph[p.start:p.start + p.size]
                v[0] += p.size
                v[1] += float(seg.sum())
                v[2] += float(np.abs(seg).sum())
        for k, v in sorted(by_type.items()):
            print(f"foam-bridge diag: patch type {k}: {v[0]} faces, sum(phi) {v[1]:+.6e}, sum|phi| {v[2]:.6e}", flush=True)
        torch.save(x.detach().cpu(), self.run / "gpuNewton" / "final_state.pt")

    def finish(self, x) -> dict:
        """Write ``processorN/<k>/{U,p_rgh,T,k,epsilon,phi}`` of the final iterate (explicit patch values)."""
        from Post_process.to_foam import write_fields
        t0 = time.perf_counter()
        self.close()
        case = self.case if self.case is not None else CASES.get(str(self.run))
        if case is None:
            from Parse_mesh.foamio.decomposed import read_case
            case = read_case(self.run, keep=self.req.get("keep", "connected_open"))
        with torch.no_grad():
            R = self.cs.residual(x)
            q = self._q().detach().cpu().numpy()
            phi = self._phi().detach().cpu().numpy()
        n = self.n
        if os.environ.get("LPCNS_FOAM_BRIDGE_DIAG", "0") == "1":
            self._mass_diagnostic(x, R, case)
        if q.shape[0] != n + (case.n_faces - case.n_internal_faces) or case.n_cells != n:
            raise ValueError(f"foam bridge: state of {q.shape[0]} compound cells does not match the case "
                             f"({case.n_cells} cells, {case.n_faces - case.n_internal_faces} boundary faces)")
        ghost = self.ghost.cpu().numpy()
        cells = {"U": q[:n, 0:3], "p_rgh": q[:n, 3], "T": q[:n, 4] + self.TRef, "k": q[:n, 5], "epsilon": q[:n, 6]}
        cols = {"U": slice(0, 3), "p_rgh": 3, "T": 4, "k": 5, "epsilon": 6}
        bvals = {f: {} for f in cells}
        for p in case.patches:
            if p.size == 0 or p.type in ("cyclic", "cyclicAMI", "cyclicSlip", "empty", "wedge", "symmetry",
                                         "symmetryPlane"):
                continue
            g = ghost[p.start:p.start + p.size]
            for f, c in cols.items():
                v = q[g, c]
                bvals[f][p.name] = v + self.TRef if f == "T" else v
        time_name = str(max(self.k, 1))
        subset = case.source_cells is not None
        workers = int(self.req.get("writeWorkers", min(12, os.cpu_count() or 1)))
        precision = int(self.req.get("writePrecision", 9))
        written = write_fields(case, self.run, time_name, cells, template_time="0",
                               source_time="0" if subset else None, boundary_values=bvals,
                               precision=precision, workers=workers, explicit=True)
        written += write_surface_scalar_field(case, self.run, time_name, phi, precision=precision, workers=workers)
        dt = time.perf_counter() - t0
        self._sample_gpu()
        if self.dev.type == "cuda":
            self.gpu.update(name=torch.cuda.get_device_name(self.dev), cells=self.n,
                            torchPeakMiB=round(torch.cuda.max_memory_allocated(self.dev) / 2 ** 20))
        out = {"time": time_name, "files": len(written), "writeSeconds": dt, "iterations": self.k,
               "stopReason": self.stop_reason, "last": self.last, "gpu": self.gpu,
               "physics": physics_checks(q, phi, ghost, case, n, self.TRef, self.last, speed_factor=self.speed_factor)}
        RESULTS[str(self.run)] = out
        return out




def physics_checks(q, phi, ghost, case, n, t_ref, last=None, nu=1.5e-5, *,
                   t_lo_margin=2.0, t_hi_margin=5.0, speed_factor=6.0, speed_floor=30.0) -> dict:
    """The C0 physical checks of an answer (docs/research/2026-11-13-unified-steady-framework.md, cascade gates):
    hall mass balance against the inflow, energy closure (the stop rule's heat_rel), every cell temperature inside
    [coldest boundary T - t_lo_margin, hottest boundary T + t_hi_margin] (default [-2 K, +5 K] to allow minor
    cut-cell / bounded-convection undershoot), cell speed <= max(speed_factor * max boundary speed, speed_floor)
    (allowing porous-baffle orifice jets), k, epsilon > 0 and nu_t / nu <= 1e5. ``ok`` is the conjunction."""
    bf = np.concatenate([np.arange(p.start, p.start + p.size) for p in case.patches
                         if p.size and p.type not in _COUPLED] or [np.zeros(0, dtype=np.int64)])
    ph = np.asarray(phi, dtype=np.float64)[bf]
    inflow = float(-ph[ph < 0].sum())
    mass_rel = abs(float(ph.sum())) / max(inflow, 1e-300)
    g = np.asarray(ghost)[bf]
    Tb = q[g, 4] + t_ref
    Tc = q[:n, 4] + t_ref
    t_lo = (float(Tb.min()) - float(t_lo_margin)) if Tb.size else float("-inf")
    t_hi = (float(Tb.max()) + float(t_hi_margin)) if Tb.size else float("inf")
    ub = float(np.linalg.norm(q[g, 0:3], axis=1).max()) if g.size else 0.0
    uc = float(np.linalg.norm(q[:n, 0:3], axis=1).max())
    speed_bound = max(float(speed_factor) * ub, float(speed_floor))
    k, eps = q[:n, 5], q[:n, 6]
    nut = 0.09 * k ** 2 / np.maximum(eps, 1e-300)
    heat = (last or {}).get("heat_rel")
    out = {"mass_rel": mass_rel, "heat_rel": heat, "T_cells_K": [float(Tc.min()), float(Tc.max())],
           "T_bounds_K": [t_lo, t_hi], "speed_max": uc, "speed_bound": speed_bound,
           "k_min": float(k.min()), "eps_min": float(eps.min()), "nut_over_nu_max": float(nut.max() / nu)}
    checks = {"mass": mass_rel <= 0.01, "heat": heat is None or abs(float(heat)) <= 0.05,
              "temperature": bool(Tc.min() >= t_lo and Tc.max() <= t_hi), "speed": uc <= speed_bound,
              "turbulence": bool(k.min() > 0 and eps.min() > 0 and nut.max() / nu <= 1e5)}
    out["checks"] = checks
    out["ok"] = all(checks.values())
    return out


_PHI: dict = {}                     # write_surface_scalar_field's arguments, inherited by its forked workers


def _scalar_list(v: np.ndarray) -> str:
    if v.size == 0:
        return "nonuniform List<scalar> 0()"
    fmt = _PHI.get("float", repr)
    return f"nonuniform List<scalar> \n{v.size}\n(\n" + "\n".join(map(fmt, v.tolist())) + "\n)\n"


def _write_surface_rank(r: int) -> Path:
    from Post_process.to_foam import _BANNER, rank_dir
    case, values, name, time_name = _PHI["case"], _PHI["values"], _PHI["name"], _PHI["time"]
    rm = case.ranks[r]
    g = rm.face_global
    loc = np.zeros(rm.n_faces)
    ok = g >= 0
    loc[ok] = np.where(rm.face_flip[ok], -values[g[ok]], values[g[ok]])
    parts = [_BANNER, "FoamFile\n{\n    version     2.0;\n    format      ascii;\n",
             '    arch        "LSB;label=32;scalar=64";\n', "    class       surfaceScalarField;\n",
             f'    location    "{time_name}";\n', f"    object      {name};\n}}\n",
             "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n",
             f"dimensions      {_PHI['dimensions']};\n\noriented        oriented;\n\n",
             f"internalField   {_scalar_list(loc[:rm.n_internal_faces])};\n\nboundaryField\n{{\n"]
    for lp in rm.patches:
        kind = lp.type if lp.type in ("processor", "cyclic", "processorCyclic", "empty", "symmetry",
                                      "symmetryPlane", "wedge") else "calculated"
        parts.append(f"    {lp.name}\n    {{\n        type            {kind};\n"
                     f"        value           {_scalar_list(loc[lp.slice])};\n    }}\n")
    parts.append("}\n\n\n// ************************************************************************* //\n")
    path = rank_dir(case, rm, _PHI["out_dir"]) / time_name / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts))
    return path


def write_surface_scalar_field(case, out_dir, time_name: str, values: np.ndarray, name: str = "phi",
                               dimensions: str = "[0 3 -1 0 0 0 0]", precision: int | None = None,
                               workers: int = 1) -> list[Path]:
    """Write a global face field (owner -> neighbour orientation) as an oriented ``surfaceScalarField`` per rank;
    faces dropped by a subset get 0."""
    from Post_process.to_foam import run_forked
    values = np.asarray(values, dtype=np.float64)
    if values.shape[0] != case.n_faces:
        raise ValueError(f"{name}: {values.shape[0]} values for {case.n_faces} faces")
    _PHI.update(case=case, values=values, name=name, time=time_name, dimensions=dimensions, out_dir=Path(out_dir),
                float=repr if precision is None else f"%.{int(precision)}g".__mod__)
    try:
        return run_forked(_write_surface_rank, range(len(case.ranks)), workers)
    finally:
        _PHI.clear()
