"""Differentiable OpenFOAM patch binding, foam-directory editing, and hall probe observation.

Maps low-dimensional physical parameters ``theta`` onto OpenFOAM boundary conditions
both on disk (``apply_to_foam_dir`` for forward ``dc_foam_solve`` runs) and live
in-memory on a built ``DCWarp``/``torch`` solver (``CSPatchBinding`` for ``torch_residual``
parameter sensitivities ``dR/dtheta``), paired with ``HallProbeObserver`` combining
3D cell-nearest interior probes and flux-weighted ACU return temperatures.
"""

from __future__ import annotations

from dataclasses import dataclass
import glob
import math
import os
from pathlib import Path
import re
import shutil
import threading
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from typing import Any, Callable, Mapping, Sequence

import h5py
import numpy as np
from scipy.spatial import cKDTree
import torch

from Post_process.dc_qoi import DCQuantities, flow_rate_outlet_flux
from Pipeline.CoupledNewton.dc_adjoint import torch_residual


__all__ = [
    "ParameterSpec",
    "PatchParameterMap",
    "CSPatchBinding",
    "HallProbeObserver",
    "zeta_idelchik",
    "group_offset_specs",
]


def zeta_idelchik(sigma: float | np.ndarray | torch.Tensor):
    """Idelchik thin perforated-plate loss coefficient ``I = zeta(sigma)`` for open-area ratio ``sigma``."""
    if isinstance(sigma, torch.Tensor):
        s = sigma.clamp(1e-4, 0.99999)
        return (0.707 * torch.sqrt(1.0 - s) + 1.0 - s) ** 2 / (s ** 2)
    s = float(min(max(float(sigma), 1e-4), 0.99999))
    return (0.707 * math.sqrt(1.0 - s) + 1.0 - s) ** 2 / (s ** 2)


@dataclass(frozen=True)
class ParameterSpec:
    """Metadata, prior, and box bounds for one calibration parameter."""

    name: str
    prior_mean: float
    prior_sigma: float
    lower: float = -math.inf
    upper: float = math.inf
    kind: str = "generic"
    description: str = ""


def group_offset_specs(prefix: str, groups: Sequence[str], *, sigma: float = 0.15, bound: float = 1.0,
                       description: str = "log10 deviation of the group from the common level") -> list[ParameterSpec]:
    """One additive parameter ``<prefix>:<group>`` per group, prior 0 +- ``sigma``, within +-``bound``."""
    return [ParameterSpec(f"{prefix}:{g}", 0.0, float(sigma), -float(bound), float(bound), "group_offset",
                          f"{description} ({g})") for g in groups]


# --------------------------------------------------------------------------- OpenFOAM dict editing
def _skip_ws_and_comments(text: str, pos: int) -> int:
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch.isspace():
            pos += 1
        elif text.startswith("//", pos):
            nl = text.find("\n", pos)
            pos = n if nl < 0 else nl + 1
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = n if end < 0 else end + 2
        else:
            break
    return pos


def _find_matching_brace(text: str, open_idx: int) -> int:
    """Return index of the matching closing brace for the opening brace at ``open_idx``."""
    n = len(text)
    depth = 0
    pos = open_idx
    in_str = False
    while pos < n:
        ch = text[pos]
        if in_str:
            if ch == "\\" and pos + 1 < n:
                pos += 2
                continue
            if ch == '"':
                in_str = False
            pos += 1
            continue
        if text.startswith("//", pos):
            nl = text.find("\n", pos)
            pos = n if nl < 0 else nl + 1
            continue
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = n if end < 0 else end + 2
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    raise ValueError("unmatched opening brace in OpenFOAM dictionary")


def _iter_boundary_patch_spans(text: str):
    """Yield ``(patch_name, entry_start, brace_open, brace_close)`` inside top-level ``boundaryField``."""
    m = re.search(r"\bboundaryField\b", text)
    if not m:
        return
    pos = _skip_ws_and_comments(text, m.end())
    if pos >= len(text) or text[pos] != "{":
        return
    bf_close = _find_matching_brace(text, pos)
    pos = pos + 1
    while pos < bf_close:
        pos = _skip_ws_and_comments(text, pos)
        if pos >= bf_close:
            break
        entry_start = pos
        if text[pos] == '"':
            end_q = pos + 1
            while end_q < bf_close and text[end_q] != '"':
                end_q += 2 if text[end_q] == "\\" else 1
            key = text[pos:end_q + 1]
            pos = end_q + 1
        else:
            m_tok = re.match(r"[^\s{};/]+", text[pos:bf_close])
            if not m_tok:
                break
            key = m_tok.group(0)
            pos += len(key)
        pos = _skip_ws_and_comments(text, pos)
        if pos < bf_close and text[pos] == "{":
            b_open = pos
            b_close = _find_matching_brace(text, b_open)
            yield key, entry_start, b_open, b_close
            pos = b_close + 1
        elif pos < bf_close and text[pos] == ";":
            pos += 1
        else:
            semi = text.find(";", pos, bf_close)
            pos = bf_close if semi < 0 else semi + 1


def _match_patch_edit(key: str, exact: dict[str, Any], regexes: list[tuple[re.Pattern, Any]]):
    if key in exact:
        return exact[key]
    stripped = key.strip('"')
    if stripped in exact:
        return exact[stripped]
    for pat, edit in regexes:
        if pat.fullmatch(stripped) or pat.fullmatch(key):
            return edit
    return None


def _edit_patch_body(body: str, edit: dict[str, Any], indent: str = "        ") -> str:
    """Apply key edits to the interior of a single OpenFOAM patch dictionary block."""
    if "raw_body" in edit:
        return str(edit["raw_body"])

    if "replace_block" in edit:
        return str(edit["replace_block"])

    out = body
    def _fmt_kv(k: str, v: str) -> str:
        return f"{k}{' ' * max(1, 16 - len(k))}{v};"

    if "upgrade_from" in edit and re.search(rf"\btype\s+{re.escape(edit['upgrade_from'])}\s*;", out):
        lines = ["\n"]
        for k, v in edit["fields"].items():
            lines.append(f"{indent}{_fmt_kv(k, v)}\n")
        return "".join(lines) + indent[:-4]

    if "set_type" in edit:
        lines = ["\n"]
        for k, v in edit["set_type"].items():
            lines.append(f"{indent}{_fmt_kv(k, v)}\n")
        return "".join(lines) + indent[:-4]

    for key, val in edit.get("keys", {}).items():
        pat = re.compile(rf"([ \t]*\b{re.escape(key)}\b[ \t]+)[^;]*;")
        if pat.search(out):
            out = pat.sub(lambda m, v=val: f"{m.group(1)}{v};", out, count=1)
        else:
            insert = f"\n{indent}{_fmt_kv(key, val)}"
            last_nl = out.rfind("\n")
            if last_nl >= 0:
                out = out[:last_nl] + insert + out[last_nl:]
            else:
                out = out + insert + "\n"

    if "expression_deltaT" in edit:
        dt_val = float(edit["expression_deltaT"])
        m_alpha = re.search(r'expression\s+"\s*([0-9.eE+-]+)\s*\*\s*tSink', out)
        alpha = float(m_alpha.group(1)) if m_alpha else 1.0
        c_val = alpha * dt_val

        def _rep_expr(m):
            prefix = m.group(1)
            sign = "+" if c_val >= 0.0 else "-"
            return f'{prefix}{sign}{abs(c_val):.6f}"'

        out_new, count = re.subn(r'(expression\s+"[^"]*?)[+-]\s*[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\s*"', _rep_expr, out, count=1)
        if count > 0:
            out = out_new

    if "scale_value" in edit:
        factor = float(edit["scale_value"])
        m_val = re.search(r"([ \t]*\bvalue\s+uniform\s+)([0-9.eE+-]+)(\s*;)", out)
        if m_val:
            scaled = float(m_val.group(2)) * factor
            out = out[:m_val.start()] + f"{m_val.group(1)}{scaled:.6g}{m_val.group(3)}" + out[m_val.end():]

    return out


def _update_foam_boundary_field(
    path: Path, exact_edits: dict[str, dict], regex_edits: list[tuple[re.Pattern, dict]] | None = None
) -> int:
    """Update matched patch blocks inside an OpenFOAM field file, unlinking symlinks first."""
    if not path.exists() and not path.is_symlink():
        return 0
    if not exact_edits and not regex_edits:
        return 0
    text = path.read_text()
    regex_list = regex_edits or []
    spans = list(_iter_boundary_patch_spans(text))
    pieces = []
    cursor = 0
    changed = 0
    for key, _entry_start, b_open, b_close in spans:
        edit = _match_patch_edit(key, exact_edits, regex_list)
        if edit is None:
            continue
        old_body = text[b_open + 1:b_close]
        new_body = _edit_patch_body(old_body, edit)
        if new_body != old_body:
            pieces.append(text[cursor:b_open + 1])
            pieces.append(new_body)
            cursor = b_close
            changed += 1
    if changed == 0:
        return 0
    pieces.append(text[cursor:])
    new_text = "".join(pieces)
    if path.is_symlink():
        path.unlink()
    path.write_text(new_text)
    return changed


def _ensure_case_dir(run_dir: Path, template_dir: Path | None, *, symlink_mesh: bool = True) -> None:
    """Materialize a lightweight working OpenFOAM run directory from ``template_dir``."""
    if template_dir is None or run_dir.resolve() == template_dir.resolve():
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    for item in template_dir.iterdir():
        name = item.name
        if name in ("gpuNewton", "postProcessing", "dynamicCode") or name.startswith("log"):
            continue
        dst = run_dir / name
        if name.startswith("processor") and item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            for sub in item.iterdir():
                sub_dst = dst / sub.name
                if sub.name == "0" and sub.is_dir():
                    sub_dst.mkdir(parents=True, exist_ok=True)
                    for f in sub.iterdir():
                        f_dst = sub_dst / f.name
                        if not f_dst.exists() and not f_dst.is_symlink():
                            shutil.copy2(f, f_dst)
                elif not sub_dst.exists() and not sub_dst.is_symlink():
                    if symlink_mesh:
                        os.symlink(sub.resolve(), sub_dst)
                    elif sub.is_dir():
                        shutil.copytree(sub, sub_dst)
                    else:
                        shutil.copy2(sub, sub_dst)
        elif name == "0" and item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            for f in item.iterdir():
                f_dst = dst / f.name
                if not f_dst.exists() and not f_dst.is_symlink():
                    shutil.copy2(f, f_dst)
        elif not dst.exists() and not dst.is_symlink():
            if symlink_mesh and name == "constant":
                os.symlink(item.resolve(), dst)
            elif item.is_dir():
                shutil.copytree(item, dst, symlinks=True)
            else:
                shutil.copy2(item, dst)


def _zero_dirs(run_dir: Path) -> list[Path]:
    procs = sorted(run_dir.glob("processor[0-9]*"), key=lambda p: int(p.name[9:]))
    dirs = [p / "0" for p in procs if (p / "0").is_dir()]
    if dirs:
        return dirs
    if (run_dir / "0").is_dir():
        return [run_dir / "0"]
    raise FileNotFoundError(f"{run_dir}: neither processor*/0 nor 0/ directory found")


# --------------------------------------------------------------------------- Parameter mapping
class PatchParameterMap:
    """Declarative mapping from physical calibration parameters ``theta`` to patch values and OpenFOAM files.

    Supports both general custom evaluators and standard data-centre room parameters:
    ``P_scale``, ``dTsrv``, ``Q100``, ``bT``, ``lsBlank``, ``lsEmpty``, ``lqRev``,
    ``dTwall``, and ``ltau``.

    ``baffles`` groups porous patches (a list of names or one regex per group key) and ``baffle_params`` names the
    log10 open-area parameter of each group; several groups may share one parameter. ``baffle_offsets`` names, per
    group, an additive log10 parameter on top of it: a common level with a deviation per group (the racks of a room
    sharing one blanking-panel leakage and each departing a little from it; ``group_offset_specs`` builds those
    parameters with a zero-mean prior).
    """

    def __init__(
        self,
        params: Sequence[ParameterSpec],
        *,
        TRef: float = 297.15,
        rho: float = 1.19,
        cp: float = 1005.0,
        acus: dict[str, dict] | None = None,
        servers: list[dict] | None = None,
        baffles: dict[str, Any] | None = None,
        baffle_params: dict[str, str] | None = None,
        baffle_offsets: dict[str, str] | None = None,
        walls: list[str] | None = None,
        wall_param: str = "dTwall",
        wall_ref_K: float | None = None,
        snapshots: list[dict] | None = None,
        custom_evaluator: Callable[[int, Any, "PatchParameterMap"], dict] | None = None,
    ):
        self.params = list(params)
        self.param_index = {p.name: i for i, p in enumerate(self.params)}
        self.TRef = float(TRef)
        self.rho = float(rho)
        self.cp = float(cp)
        self.acus = dict(acus or {})
        self.servers = list(servers or [])
        self.baffles = dict(baffles or {})
        self.baffle_params = dict(baffle_params or {})
        self.baffle_offsets = dict(baffle_offsets or {})
        self.walls = list(walls or [])
        self.wall_param = wall_param
        self.wall_ref_K = float(TRef if wall_ref_K is None else wall_ref_K)
        self.snapshots = list(snapshots or [])
        self.custom_evaluator = custom_evaluator

    @property
    def param_names(self) -> list[str]:
        return [p.name for p in self.params]

    def prior_mean_tensor(self, dtype=torch.float64, device="cpu") -> torch.Tensor:
        return torch.tensor([p.prior_mean for p in self.params], dtype=dtype, device=device)

    def prior_sigma_tensor(self, dtype=torch.float64, device="cpu") -> torch.Tensor:
        return torch.tensor([p.prior_sigma for p in self.params], dtype=dtype, device=device)

    def bounds_tensors(self, dtype=torch.float64, device="cpu") -> tuple[torch.Tensor, torch.Tensor]:
        lo = torch.tensor([p.lower for p in self.params], dtype=dtype, device=device)
        hi = torch.tensor([p.upper for p in self.params], dtype=dtype, device=device)
        return lo, hi

    def _param_value(self, theta: Any, name: str, default: float | None = None):
        idx = self.param_index.get(name)
        if idx is None:
            return default
        if isinstance(theta, torch.Tensor):
            return theta[idx]
        return float(theta[idx])

    def evaluate(self, k: int, theta: Any) -> dict[str, Any]:
        """Map ``theta`` at snapshot ``k`` into patch values ``{"T", "Q", "rise", "I", ...}``."""
        if self.custom_evaluator is not None:
            out = dict(self.custom_evaluator(k, theta, self))
            out.setdefault("T", {})
            out.setdefault("Q", {})
            out.setdefault("Q_base", {})
            out.setdefault("rise", {})
            out.setdefault("I", {})
            out.setdefault("I_regex", {})
            out.setdefault("wall_T", {})
            out.setdefault("stopped_acus", {})
            out.setdefault("tau_scale", 1.0)
            return out

        snap = self.snapshots[k] if k < len(self.snapshots) else {}
        is_t = isinstance(theta, torch.Tensor)
        p_scale = self._param_value(theta, "P_scale", 1.0)
        d_tsrv = self._param_value(theta, "dTsrv", 10.0)
        q100 = self._param_value(theta, "Q100", 1.0)
        b_t = self._param_value(theta, "bT", 0.0)
        lq_rev = self._param_value(theta, "lqRev", None)
        d_twall = self._param_value(theta, self.wall_param, None)
        l_tau = self._param_value(theta, "ltau", 0.0)

        T_map: dict[str, Any] = {}
        Q_map: dict[str, Any] = {}
        Q_base_map: dict[str, float] = {}
        rise_map: dict[str, Any] = {}
        I_exact: dict[str, Any] = {}
        I_regex: dict[str, Any] = {}
        wall_T: dict[str, Any] = {}
        stopped_acus: dict[str, Any] = {}

        rack_heat_meas = snap.get("rack_heat_W", {})
        rack_design_sum: dict[str, float] = {}
        for s in self.servers:
            rk = str(s.get("rack", ""))
            rack_design_sum[rk] = rack_design_sum.get(rk, 0.0) + float(s.get("design_heat_W", 0.0))

        rc = self.rho * self.cp
        rise_val = d_tsrv.clamp_min(0.25) if isinstance(d_tsrv, torch.Tensor) else max(float(d_tsrv), 0.25)
        total_heat_w = 0.0
        for s in self.servers:
            rk = str(s.get("rack", ""))
            des_h = float(s.get("design_heat_W", 0.0))
            if rk in rack_heat_meas:
                tot_des = max(rack_design_sum.get(rk, 0.0), 1e-12)
                frac = des_h / tot_des if tot_des > 0 else 0.0
                heat_s = float(rack_heat_meas[rk]) * frac * p_scale
            else:
                heat_s = des_h * p_scale
            total_heat_w += float(heat_s.detach().item() if isinstance(heat_s, torch.Tensor) else heat_s)
            q_min = float(s.get("q_min", 0.0))
            q_raw = heat_s / (rc * rise_val)
            if q_min > 0.0:
                q_s = q_raw.clamp_min(q_min) if isinstance(q_raw, torch.Tensor) else max(float(q_raw), q_min)
                eff_rise = heat_s / (rc * q_s)
            else:
                q_s = q_raw
                eff_rise = rise_val
            Q_map[s["inlet"]] = q_s
            Q_map[s["outlet"]] = q_s
            if "nominal_flow" in s and s["nominal_flow"] is not None:
                Q_base_map[s["inlet"]] = float(s["nominal_flow"])
                Q_base_map[s["outlet"]] = float(s["nominal_flow"])
            rise_map[s["outlet"]] = eff_rise

        acu_speeds = snap.get("acu_speed", {})
        acu_tsup = snap.get("acu_T_sup_K", {})
        q_rev = (10.0 ** lq_rev) if lq_rev is not None else 0.0
        for name, a in self.acus.items():
            sup = a["supply"]
            ret = a.get("return")
            rated = float(a.get("rated_speed", 100.0))
            spd = float(acu_speeds.get(name, rated))
            t_meas = float(acu_tsup.get(name, a.get("nominal_T_sup_K", self.TRef - 4.0)))
            if spd > 0.0:
                if a.get("q100_is_multiplier", False):
                    q_sup = float(a.get("nominal_q100", 1.0)) * (spd / rated) * q100
                else:
                    scale_n = float(a.get("q100_scale", 1.0))
                    q_sup = q100 * scale_n * (spd / rated)
                T_map[sup] = t_meas + b_t
                Q_map[sup] = q_sup
                if ret and not a.get("pressure_ref", False):
                    Q_map[ret] = q_sup
                if "nominal_flow" in a and a["nominal_flow"] is not None:
                    Q_base_map[sup] = float(a["nominal_flow"])
                    if ret and not a.get("pressure_ref", False):
                        Q_base_map[ret] = float(a["nominal_flow"])
            else:
                stopped_acus[name] = q_rev
                if q_rev is not None:
                    Q_map[sup] = q_rev
                    if ret and not a.get("pressure_ref", False):
                        Q_map[ret] = q_rev
                T_map[sup] = t_meas + b_t

        for group_key, target in self.baffles.items():
            pname = self.baffle_params.get(group_key)
            if pname is None or pname not in self.param_index:
                continue
            lval = self._param_value(theta, pname)
            offset = self.baffle_offsets.get(group_key)
            if offset is not None and offset in self.param_index:
                lval = lval + self._param_value(theta, offset)
            zeta = zeta_idelchik(10.0 ** lval)
            if isinstance(target, str):
                I_regex[target] = zeta
            else:
                for patch_name in target:
                    I_exact[patch_name] = zeta

        if d_twall is not None and self.walls:
            t_wall = float(snap.get("wall_ref_K", self.wall_ref_K)) + d_twall
            for wpatch in self.walls:
                T_map[wpatch] = t_wall
                wall_T[wpatch] = t_wall

        if is_t and isinstance(l_tau, torch.Tensor):
            tau_scale = 10.0 ** l_tau
        else:
            tau_scale = 10.0 ** float(l_tau)

        return {
            "T": T_map,
            "Q": Q_map,
            "Q_base": Q_base_map,
            "rise": rise_map,
            "I": I_exact,
            "I_regex": I_regex,
            "wall_T": wall_T,
            "stopped_acus": stopped_acus,
            "tau_scale": tau_scale,
            "declared_heat_W": total_heat_w,
        }

    def apply_to_foam_dir(
        self,
        run_dir: str | Path,
        k: int,
        theta: Any,
        *,
        template_dir: str | Path | None = None,
        symlink_mesh: bool = True,
        reverse_stopped_acu_bc: bool = True,
    ) -> dict[str, Any]:
        """Materialize ``run_dir`` (optionally from ``template_dir``) and write snapshot ``k`` BCs for ``theta``."""
        run_path = Path(run_dir)
        tmpl_path = Path(template_dir) if template_dir is not None else None
        _ensure_case_dir(run_path, tmpl_path, symlink_mesh=symlink_mesh)
        vals = self.evaluate(k, theta)

        def _flt(v) -> float:
            return float(v.detach().item()) if isinstance(v, torch.Tensor) else float(v)

        t_edits: dict[str, dict] = {}
        u_edits: dict[str, dict] = {}
        k_edits: dict[str, dict] = {}
        e_edits: dict[str, dict] = {}
        p_exact: dict[str, dict] = {}
        p_regex: list[tuple[re.Pattern, dict]] = []

        wall_set = set(vals.get("wall_T", {}).keys())
        for patch, tk in vals["T"].items():
            if patch in wall_set:
                t_edits[patch] = {
                    "upgrade_from": "zeroGradient",
                    "fields": {"type": "fixedValue", "value": f"uniform {_flt(tk):.6f}"},
                    "keys": {"value": f"uniform {_flt(tk):.6f}"},
                }
            else:
                t_edits[patch] = {"keys": {"value": f"uniform {_flt(tk):.6f}"}}

        for patch, dT in vals["rise"].items():
            t_edits[patch] = {"expression_deltaT": _flt(dT)}

        stopped_patches_sup: set[str] = set()
        stopped_patches_ret: set[str] = set()
        if reverse_stopped_acu_bc:
            for acu_name in vals.get("stopped_acus", {}):
                a = self.acus.get(acu_name, {})
                sp = a.get("supply")
                rp = a.get("return")
                if sp:
                    stopped_patches_sup.add(sp)
                    t_edits[sp] = {"replace_block": "\n        type            zeroGradient;\n    "}
                    k_edits[sp] = {
                        "replace_block": (
                            "\n        type            inletOutlet;\n"
                            "        inletValue      uniform 0.123462;\n"
                            "        value           uniform 0.123462;\n    "
                        )
                    }
                    e_edits[sp] = {
                        "replace_block": (
                            "\n        type            inletOutlet;\n"
                            "        inletValue      uniform 0.088846;\n"
                            "        value           uniform 0.088846;\n    "
                        )
                    }
                if rp and not a.get("pressure_ref", False):
                    stopped_patches_ret.add(rp)
                    if sp:
                        t_edits[rp] = {
                            "replace_block": (
                                "\n        type            uniformFixedValue;\n"
                                f"        value           uniform {self.TRef:.6g};\n"
                                "        uniformValue\n        {\n"
                                "            type            expression;\n"
                                f"            expression       \"0.5*tSink_{rp}+0.5*tOutPrev_{rp}+0.0\";\n"
                                "            variables\n            (\n"
                                f"                \"tSink_{rp}{{{sp}}} = sum(phi*T)/sum(phi)\"\n"
                                f"                \"tOutPrev_{rp}{{{rp}}} = weightAverage(T)\"\n"
                                "            );\n        };\n    "
                            )
                        }
                    k_edits[rp] = {
                        "replace_block": (
                            "\n        type            turbulentIntensityKineticEnergyInlet;\n"
                            "        intensity       0.1;\n"
                            "        value           uniform 0.1;\n    "
                        )
                    }
                    ml = float(a.get("mixing_length", 0.0557143))
                    e_edits[rp] = {
                        "replace_block": (
                            "\n        type            turbulentMixingLengthDissipationRateInlet;\n"
                            f"        mixingLength    {ml:.7g};\n"
                            "        value           uniform 0.0182478;\n    "
                        )
                    }

        q_base_map = vals.get("Q_base", {})
        for patch, q in vals["Q"].items():
            qv = _flt(q)
            if patch in stopped_patches_sup:
                u_edits[patch] = {"set_type": {"type": "flowRateOutletVelocity", "volumetricFlowRate": f"{qv:.6g}"}}
            elif patch in stopped_patches_ret:
                u_edits[patch] = {"set_type": {"type": "flowRateInletVelocity", "volumetricFlowRate": f"{qv:.6g}"}}
            else:
                u_edits[patch] = {"keys": {"volumetricFlowRate": f"{qv:.6g}"}}

            q0 = q_base_map.get(patch)
            if (
                patch not in stopped_patches_sup
                and patch not in stopped_patches_ret
                and q0
                and q0 > 0.0
                and qv > 0.0
            ):
                r = qv / q0
                k_edits[patch] = {"scale_value": r ** 2}
                e_edits[patch] = {"scale_value": r ** 3}

        for patch, i_val in vals["I"].items():
            p_exact[patch] = {"keys": {"I": f"{_flt(i_val):.6f}"}}
        for pat, i_val in vals.get("I_regex", {}).items():
            p_regex.append((re.compile(pat), {"keys": {"I": f"{_flt(i_val):.6f}"}}))

        for zdir in _zero_dirs(run_path):
            _update_foam_boundary_field(zdir / "T", t_edits)
            _update_foam_boundary_field(zdir / "U", u_edits)
            if k_edits:
                _update_foam_boundary_field(zdir / "k", k_edits)
            if e_edits:
                _update_foam_boundary_field(zdir / "epsilon", e_edits)
            if p_exact or p_regex:
                _update_foam_boundary_field(zdir / "p_rgh", p_exact, p_regex)

        return vals


# --------------------------------------------------------------------------- Live Solver Binding
class CSPatchBinding:
    """Binds patch parameters directly onto a built solver's torch BC objects and computes ``dR/dtheta``."""

    def __init__(self, cs, TRef: float | None = None):
        from FVdomain.SetBC.onthefly.BCbase import VARType

        self.cs = cs
        self.V = VARType
        if TRef is None:
            TRef = getattr(cs, "t_ref", getattr(getattr(cs, "solver", None), "TRef", 297.15))
        self.TRef = float(TRef)

        self.by: dict[tuple[str, int], Any] = {}
        for bc in cs.solver.bcpatch_batch.iter_conditions():
            self.by[(bc.patch_name, int(bc.varType))] = bc

        self.rack: dict[str, Any] = {}
        self.porous: dict[str, Any] = {}
        all_bcs = list(cs.solver.bcpatch_batch.iter_conditions())
        for bcs in getattr(cs.solver.bcpatch_batch, "coupled", ()):
            all_bcs.extend(bcs)
        for bc in all_bcs:
            cls_name = type(bc).__name__
            if cls_name == "Rackoutlettemperature":
                self.rack[bc.patch_name] = bc
            elif cls_name == "Porousbafflepressure":
                self.porous[bc.patch_name] = bc

        self._saved: list[tuple[Any, str, Any]] = []
        self._base_q0: dict[str, float] = {}
        self._base_turb: dict[tuple[str, int], torch.Tensor] = {}
        self._base_turb_np: dict[tuple[str, int], np.ndarray] = {}
        self._warp_tables: dict[str, Any] | None = None
        self._warp_dirty: bool = False

    def _ensure_warp_tables(self) -> dict[str, Any] | None:
        w_back = getattr(self.cs, "_warp", None)
        W = getattr(w_back, "W", None) if w_back is not None else None
        if W is None or not hasattr(W, "ghost_b") or getattr(W, "_keep_g", None) is None:
            return None
        if self._warp_tables is not None and self._warp_tables.get("W") is W:
            return self._warp_tables
        import warp as wp
        from FVdomain.SetBC.onthefly.setCyclic import get_cyclic_patch_pair_indices

        gb_gpu = wp.to_torch(W.ghost_b)
        ft_gpu = wp.to_torch(W.f_target)
        odt_gpu = wp.to_torch(W.o_dt)
        pz_gpu = wp.to_torch(W.pz_c1)

        ghosts_np = W._keep_g[0].detach().cpu().numpy().astype(np.int64)
        slot_np = np.full(int(W.n_cpd), -1, dtype=np.int64)
        slot_np[ghosts_np] = np.arange(len(ghosts_np), dtype=np.int64)

        app = self.cs.solver
        fvp = app.fvpatch_batch
        batch = app.bcpatch_batch
        unv_np = app.unv_face.detach().cpu().to(torch.float64).numpy()
        area_np = app.face_area.detach().cpu().to(torch.float64).numpy().reshape(-1)
        ncc_ghost_np = app.neighbor_cpd_cell[1].detach().cpu().numpy().astype(np.int64)
        start_cpd_np = fvp.start_idx_cpd_cell.detach().cpu().numpy().astype(np.int64)
        end_cpd_np = fvp.end_idx_cpd_cell.detach().cpu().numpy().astype(np.int64)
        start_f_np = fvp.start_idx_face.detach().cpu().numpy().astype(np.int64)
        end_f_np = fvp.end_idx_face.detach().cpu().numpy().astype(np.int64)

        fixed_slots: dict[tuple[str, int], np.ndarray] = {}
        vel_unit: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        fro_map: dict[str, int] = {}
        rk_map: dict[str, tuple[int, int]] = {}
        pz_map: dict[str, list[tuple[int, int]]] = {}

        fro_idx = 0
        for gi_, bcs in enumerate(batch.first):
            off = int(batch.first_patch_idx_offset[gi_])
            for bc in bcs:
                cls = type(bc).__name__
                gp = int(bc.patch_idx) + off
                if cls == "Flowrateoutletvelocity":
                    fro_map[bc.patch_name] = fro_idx
                    fro_idx += 1
                elif cls in ("Fixedvalue", "Uniformfixedvalue"):
                    sl = slot_np[start_cpd_np[gp]:end_cpd_np[gp]]
                    fixed_slots[(bc.patch_name, int(bc.varType))] = sl
                elif cls == "Flowrateinletvelocity":
                    s_f, e_f = int(start_f_np[gp]), int(end_f_np[gp])
                    sl = slot_np[start_cpd_np[gp]:end_cpd_np[gp]]
                    rho = 1.0 if bc.volumetricFlowRate is not None else float(bc.rho)
                    u_unit = -unv_np[s_f:e_f] / (rho * float(area_np[s_f:e_f].sum()))
                    vel_unit[bc.patch_name] = (sl, np.ascontiguousarray(u_unit, dtype=np.float64))
                elif cls == "Rackoutletvelocity":
                    s_f, e_f = int(start_f_np[gp]), int(end_f_np[gp])
                    sl = slot_np[ncc_ghost_np[s_f:e_f]]
                    u_unit = -unv_np[s_f:e_f] / float(area_np[s_f:e_f].sum())
                    vel_unit[bc.patch_name] = (sl, np.ascontiguousarray(u_unit, dtype=np.float64))

        for gi_, bcs in enumerate(batch.mixed):
            off = int(batch.mixed_patch_idx_offset[gi_])
            for bc in bcs:
                cls = type(bc).__name__
                gp = int(bc.patch_idx) + off
                if cls == "Rackinletvelocity":
                    s_f, e_f = int(start_f_np[gp]), int(end_f_np[gp])
                    sl = slot_np[ncc_ghost_np[s_f:e_f]]
                    u_unit = unv_np[s_f:e_f] / float(area_np[s_f:e_f].sum())
                    vel_unit[bc.patch_name] = (sl, np.ascontiguousarray(u_unit, dtype=np.float64))
                elif cls in ("Fixedvalue", "Uniformfixedvalue"):
                    sl = slot_np[start_cpd_np[gp]:end_cpd_np[gp]]
                    fixed_slots[(bc.patch_name, int(bc.varType))] = sl

        rk_pos = 0
        for gi_, bcs in enumerate(batch.coupled):
            off = int(batch.coupled_patch_idx_offset[gi_])
            for bc in bcs:
                if type(bc).__name__ != "Rackoutlettemperature":
                    continue
                if bc.model == "cbbm" and hasattr(bc, "_cbbm_indices"):
                    s_ghost = bc._cbbm_indices(app, off)[0]
                else:
                    s_ghost = bc._pairing(app, off)[1]
                m = int(s_ghost.numel())
                rk_map[bc.patch_name] = (rk_pos, rk_pos + m)
                rk_pos += m

        pz_pos = 0
        for gi_, bcs in enumerate(batch.cyclic):
            off = int(batch.cyclic_patch_idx_offset[gi_])
            for bc in bcs:
                if type(bc).__name__ != "Porousbafflepressure":
                    continue
                gp = int(bc.patch_idx) + off
                _so, ghost, _to, _ = get_cyclic_patch_pair_indices(fvp, app.neighbor_cpd_cell, gp)
                m = int(ghost.numel())
                pz_map.setdefault(bc.patch_name, []).append((pz_pos, pz_pos + m))
                pz_pos += m

        assert fro_idx == int(W.n_fro), (fro_idx, int(W.n_fro))
        assert rk_pos == int(W.n_rack_ghost), (rk_pos, int(W.n_rack_ghost))
        assert pz_pos == int(W.n_porous), (pz_pos, int(W.n_porous))

        gb_base = gb_gpu.detach().cpu().numpy().copy()
        ft_base = ft_gpu.detach().cpu().numpy().copy()
        odt_base = odt_gpu.detach().cpu().numpy().copy()
        pz_base = pz_gpu.detach().cpu().numpy().copy()

        self._warp_tables = {
            "W": W,
            "gb_gpu": gb_gpu,
            "ft_gpu": ft_gpu,
            "odt_gpu": odt_gpu,
            "pz_gpu": pz_gpu,
            "gb_base": gb_base,
            "ft_base": ft_base,
            "odt_base": odt_base,
            "pz_base": pz_base,
            "gb_work": gb_base.copy(),
            "ft_work": ft_base.copy(),
            "odt_work": odt_base.copy(),
            "pz_work": pz_base.copy(),
            "fixed_slots": fixed_slots,
            "vel_unit": vel_unit,
            "fro_map": fro_map,
            "rk_map": rk_map,
            "pz_map": pz_map,
        }
        return self._warp_tables

    def _save(self, obj: Any, attr: str) -> None:
        self._saved.append((obj, attr, getattr(obj, attr)))

    def _to_tensor(self, val: Any) -> torch.Tensor:
        if isinstance(val, torch.Tensor):
            return val.to(device=self.cs.device, dtype=torch.float64)
        return torch.as_tensor(val, dtype=torch.float64, device=self.cs.device)

    @staticmethod
    def _as_float(val: Any) -> float:
        if isinstance(val, torch.Tensor):
            return float(val.detach().item())
        return float(val)

    def _set_fixed(self, bc: Any, val: Any) -> None:
        fb = bc.value_tensor
        self._save(fb, "value_type")
        self._save(fb, "value")
        fb.value_type = "tensor"
        fb.value = self._to_tensor(val).reshape(-1)

    def _base_fixed(self, key: tuple[str, int], bc: Any) -> torch.Tensor:
        if key not in self._base_turb:
            t_b = torch.as_tensor(bc.value_tensor.value, dtype=torch.float64).reshape(-1).clone()
            self._base_turb[key] = t_b
            self._base_turb_np[key] = t_b.cpu().numpy().copy()
        return self._base_turb[key]

    def bind(self, values: dict[str, Any], *, torch_bcs: bool = True) -> None:
        V = self.V
        wt = self._ensure_warp_tables()
        if wt is not None:
            if not self._warp_dirty:
                wt["gb_work"][:] = wt["gb_base"]
                wt["ft_work"][:] = wt["ft_base"]
                wt["odt_work"][:] = wt["odt_base"]
                wt["pz_work"][:] = wt["pz_base"]
            gb_w = wt["gb_work"]
            ft_w = wt["ft_work"]
            odt_w = wt["odt_work"]
            pz_w = wt["pz_work"]
            f_slots = wt["fixed_slots"]
            v_unit = wt["vel_unit"]
            fro_map = wt["fro_map"]
            rk_map = wt["rk_map"]
            pz_map = wt["pz_map"]

        for p, tk in values.get("T", {}).items():
            bc = self.by.get((p, int(V.TEMPERATURE)))
            if bc is None:
                continue
            if getattr(bc, "value_tensor", None) is not None and type(bc).__name__ in ("Fixedvalue", "Uniformfixedvalue"):
                if torch_bcs:
                    t_exc = self._to_tensor(tk) - self.TRef
                    self._set_fixed(bc, t_exc.reshape(1))
                if wt is not None:
                    sl = f_slots.get((p, int(V.TEMPERATURE)))
                    if sl is not None:
                        gb_w[sl, 4] = self._as_float(tk) - self.TRef

        for p, q in values.get("Q", {}).items():
            ubc = self.by.get((p, int(V.VELOCITY)))
            if ubc is None:
                continue
            cls_name = type(ubc).__name__
            q_f = self._as_float(q)
            if cls_name in ("Rackinletvelocity", "Rackoutletvelocity"):
                if p not in self._base_q0:
                    self._base_q0[p] = float(getattr(ubc, "flow_rate", 1.0))
                q0 = self._base_q0[p]
                if torch_bcs:
                    self._save(ubc, "flow_rate")
                    ubc.flow_rate = self._to_tensor(q).reshape(())
            elif hasattr(ubc, "volumetricFlowRate") and getattr(ubc, "volumetricFlowRate") is not None:
                spec = ubc.volumetricFlowRate
                if p not in self._base_q0:
                    v0 = spec["val"] if isinstance(spec, dict) else spec
                    self._base_q0[p] = float(v0.detach().item() if isinstance(v0, torch.Tensor) else v0)
                q0 = self._base_q0[p]
                if torch_bcs:
                    self._save(ubc, "volumetricFlowRate")
                    ubc.volumetricFlowRate = {"value_type": "tensor", "val": self._to_tensor(q).reshape(())}
            else:
                continue

            if wt is not None:
                vu = v_unit.get(p)
                if vu is not None:
                    gb_w[vu[0], 0:3] = q_f * vu[1]
                elif p in fro_map:
                    ft_w[fro_map[p]] = q_f

            if q0 != 0.0:
                ratio_f = q_f / q0
                ratio_t = (self._to_tensor(q).reshape(()) / q0) if torch_bcs else None
                for var, ch, power in ((V.TURB_K, 5, 2), (V.TURB_EPSILON, 6, 3)):
                    kb = self.by.get((p, int(var)))
                    if (
                        kb is not None
                        and type(kb).__name__ in ("Fixedvalue", "Uniformfixedvalue")
                        and getattr(kb, "value_tensor", None) is not None
                    ):
                        base_t = self._base_fixed((p, int(var)), kb)
                        if torch_bcs:
                            self._set_fixed(kb, base_t.to(device=self.cs.device, dtype=torch.float64) * (ratio_t ** power))
                        if wt is not None:
                            sl_t = f_slots.get((p, int(var)))
                            if sl_t is not None:
                                gb_w[sl_t, ch] = self._base_turb_np[(p, int(var))] * (ratio_f ** power)

        for p, i_val in values.get("I", {}).items():
            bc = self.porous.get(p)
            if bc is not None:
                if torch_bcs:
                    self._save(bc, "I")
                    bc.I = self._to_tensor(i_val).reshape(())
                if wt is not None:
                    iv = 0.5 * self._as_float(i_val)
                    for s0, s1 in pz_map.get(p, ()):
                        pz_w[s0:s1] = iv

        for pat, i_val in values.get("I_regex", {}).items():
            comp = re.compile(pat)
            it = self._to_tensor(i_val).reshape(()) if torch_bcs else None
            iv = 0.5 * self._as_float(i_val)
            for p, bc in self.porous.items():
                if comp.fullmatch(p):
                    if torch_bcs:
                        self._save(bc, "I")
                        bc.I = it
                    if wt is not None:
                        for s0, s1 in pz_map.get(p, ()):
                            pz_w[s0:s1] = iv

        for p, rise in values.get("rise", {}).items():
            bc = self.rack.get(p)
            if bc is not None:
                if torch_bcs:
                    self._save(bc, "deltaT")
                    bc.deltaT = self._to_tensor(rise).reshape(())
                if wt is not None and p in rk_map:
                    s0, s1 = rk_map[p]
                    odt_w[s0:s1] = self._as_float(rise)

        if wt is not None:
            wt["gb_gpu"].copy_(torch.from_numpy(wt["gb_work"]), non_blocking=False)
            if wt["ft_gpu"].numel():
                wt["ft_gpu"].copy_(torch.from_numpy(wt["ft_work"]), non_blocking=False)
            if wt["odt_gpu"].numel():
                wt["odt_gpu"].copy_(torch.from_numpy(wt["odt_work"]), non_blocking=False)
            if wt["pz_gpu"].numel():
                wt["pz_gpu"].copy_(torch.from_numpy(wt["pz_work"]), non_blocking=False)
            self._warp_dirty = True

    def restore(self) -> None:
        for obj, attr, val in reversed(self._saved):
            setattr(obj, attr, val)
        self._saved.clear()
        if self._warp_dirty and self._warp_tables is not None:
            wt = self._warp_tables
            wt["gb_work"][:] = wt["gb_base"]
            wt["ft_work"][:] = wt["ft_base"]
            wt["odt_work"][:] = wt["odt_base"]
            wt["pz_work"][:] = wt["pz_base"]
            wt["gb_gpu"].copy_(torch.from_numpy(wt["gb_base"]), non_blocking=False)
            if wt["ft_gpu"].numel():
                wt["ft_gpu"].copy_(torch.from_numpy(wt["ft_base"]), non_blocking=False)
            if wt["odt_gpu"].numel():
                wt["odt_gpu"].copy_(torch.from_numpy(wt["odt_base"]), non_blocking=False)
            if wt["pz_gpu"].numel():
                wt["pz_gpu"].copy_(torch.from_numpy(wt["pz_base"]), non_blocking=False)
            self._warp_dirty = False

    @staticmethod
    def _values_need_grad(values: dict[str, Any] | None) -> bool:
        if values is None:
            return False
        for v in values.values():
            if isinstance(v, torch.Tensor) and v.requires_grad:
                return True
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, torch.Tensor) and vv.requires_grad:
                        return True
        return False

    def residual(self, u: torch.Tensor, values: dict[str, Any] | None = None, *, force_torch: bool = False) -> torch.Tensor:
        """Evaluate exact residual at state ``u`` with optional bound patch ``values`` (fast Warp when synced, torch fallback for autograd)."""
        need_grad = bool(u.requires_grad) or self._values_need_grad(values) or force_torch
        wt = None if need_grad else self._ensure_warp_tables()
        use_warp = (wt is not None) and (not need_grad) and (getattr(self.cs, "_operator_correction", None) is None)
        u_d = u.to(self.cs.device, dtype=torch.float64).reshape(-1)
        if values is not None:
            self.bind(values, torch_bcs=not use_warp)
        try:
            if use_warp:
                with torch.no_grad():
                    return self.cs.residual(u_d).detach()
            with (torch.enable_grad() if need_grad else torch.no_grad()), torch_residual(self.cs):
                r = self.cs.residual(u_d)
                return r if need_grad else r.detach()
        finally:
            if values is not None:
                self.restore()

    def r_theta_columns(
        self,
        u: torch.Tensor,
        eval_values_fn: Callable[[torch.Tensor], dict[str, Any]],
        theta: torch.Tensor,
        *,
        fd_step: float = 1e-5,
    ) -> torch.Tensor:
        """Return dense ``[N_dof, P]`` parameter sensitivity ``dR_steady/dtheta`` via central differences."""
        th = theta.detach().to(dtype=torch.float64, device="cpu").reshape(-1)
        u_d = u.to(self.cs.device, dtype=torch.float64).reshape(-1)
        n_dof = int(u_d.numel())
        n_par = int(th.numel())
        cols = torch.empty(n_dof, n_par, dtype=torch.float64, device=self.cs.device)
        for j in range(n_par):
            h = fd_step * max(1.0, abs(float(th[j])))
            th_p = th.clone()
            th_m = th.clone()
            th_p[j] += h
            th_m[j] -= h
            rp = self.residual(u_d, eval_values_fn(th_p))
            rm = self.residual(u_d, eval_values_fn(th_m))
            cols[:, j] = (rp - rm) / (2.0 * h)
        return cols


# --------------------------------------------------------------------------- Differentiable Probe Observer
class HallProbeObserver:
    """Differentiable observation operator combining 3D point probes and ACU return temperatures."""

    def __init__(
        self,
        case_dir: str | Path,
        *,
        probe_coords: np.ndarray | Sequence[Sequence[float]] | None = None,
        probe_names: Sequence[str] | None = None,
        return_acus: Sequence[str] | None = None,
        device: str | torch.device = "cpu",
        dtype: torch.dtype = torch.float64,
        include_inlets: Sequence[str] = (),
        acu_patches: Mapping[str, Mapping[str, str]] | None = None,
        n_cell: int | None = None,
        slices: dict[str, slice] | None = None,
    ):
        self.case_dir = Path(case_dir)
        self.device = torch.device(device)
        self.dtype = dtype
        self._slices = dict(slices) if slices is not None else None
        self._n_cell = int(n_cell) if n_cell is not None else None
        self._probe_coords = None if probe_coords is None or len(probe_coords) == 0 else np.asarray(probe_coords, dtype=np.float64).reshape(-1, 3)
        self._requested_probe_names = None if probe_names is None else list(probe_names)
        self._requested_return_acus = None if return_acus is None else list(return_acus)
        self.inlet_patches = list(include_inlets)
        self._acu_patches: dict[str, dict[str, str]] = (
            {str(k): dict(v) for k, v in acu_patches.items()} if acu_patches is not None else {}
        )

        self.dq: DCQuantities | None = None
        self.TRef: float = 297.15
        self.probe_idx: torch.Tensor | None = None
        self.probe_names: list[str] = (
            []
            if self._probe_coords is None
            else (list(probe_names) if probe_names is not None else [f"probe:{i}" for i in range(self._probe_coords.shape[0])])
        )
        self.probe_distances_m: np.ndarray = np.empty(0, dtype=np.float64)
        self.return_acus: list[str] = list(return_acus) if return_acus is not None else []
        self.names = (
            list(self.probe_names)
            + [f"return_T:{a}" for a in self.return_acus]
            + [f"inlet_T:{p}" for p in self.inlet_patches]
        )
        self._snapshot_q_override: dict[str, float] = {}
        self._snapshot_sup_q_override: dict[str, float] = {}
        self._snapshot_stopped: set[str] = set()
        self._stopped_acus: dict[str, dict[str, Any]] = {}
        self._acu_sup_tables: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._bound_case_dir: Path | None = None

        cdir = self._resolve_h5_dir(self.case_dir)
        if cdir is not None:
            self.bind_case_dir(cdir)

    @staticmethod
    def _resolve_h5_dir(path: Path) -> Path | None:
        if any(path.glob("*.h5")):
            return path
        sub = path / "gpuNewton" / "case"
        if sub.is_dir() and any(sub.glob("*.h5")):
            return sub
        return None

    def _load_face_tables(self, h5_path: Path, patch_names: Sequence[str], span: dict[str, tuple[int, int]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        wanted = [p for p in patch_names if p and p in span]
        if not wanted:
            return out
        with h5py.File(h5_path, "r") as f:
            area_np = np.asarray(f["face|face_area"]).reshape(-1)
            normal_np = np.asarray(f["face|face_normal"])
            owner_np = np.asarray(f["cpd|neighbor_cell"][0])
        for p in wanted:
            s, n = span[p]
            sl = slice(s, s + n)
            out[p] = {
                "owner": torch.as_tensor(np.ascontiguousarray(owner_np[sl]), dtype=torch.long, device=self.device),
                "area": torch.as_tensor(np.ascontiguousarray(area_np[sl]), dtype=self.dtype, device=self.device),
                "normal": torch.as_tensor(np.ascontiguousarray(normal_np[sl]), dtype=self.dtype, device=self.device),
            }
        return out

    def bind_case_dir(self, case_dir: str | Path) -> None:
        cdir = self._resolve_h5_dir(Path(case_dir))
        if cdir is None:
            raise FileNotFoundError(f"{case_dir}: no converted .h5 found for HallProbeObserver")
        with self._lock:
            if self._bound_case_dir is not None and self._bound_case_dir == cdir.resolve():
                return
            self.case_dir = cdir
            self._bound_case_dir = cdir.resolve()
            self.dq = DCQuantities(str(cdir), device=self.device, dtype=self.dtype)
            self.TRef = float(self.dq.TRef)
            h5s = sorted(glob.glob(str(cdir / "*.h5")))
            if len(h5s) != 1:
                raise FileNotFoundError(f"{cdir}: expected one .h5 for probe mapping, found {h5s}")
            h5_path = Path(h5s[0])
            if self._probe_coords is not None:
                with h5py.File(h5_path, "r") as f:
                    cell_pos = np.asarray(f["cell|cell_pos"], dtype=np.float64)
                tree = cKDTree(cell_pos)
                dist, idx = tree.query(self._probe_coords)
                self.probe_distances_m = np.asarray(dist, dtype=np.float64)
                self.probe_idx = torch.as_tensor(np.asarray(idx, dtype=np.int64), dtype=torch.long, device=self.device)
                self.probe_names = (
                    list(self._requested_probe_names)
                    if self._requested_probe_names is not None
                    else [f"probe:{i}" for i in range(self._probe_coords.shape[0])]
                )

            u_bc: dict[str, Any] = {}
            u_toml = cdir / "bc" / "U.toml"
            if u_toml.is_file():
                u_bc = tomllib.loads(u_toml.read_text(encoding="utf-8")).get("boundaryfield", {})

            self._stopped_acus.clear()
            self._acu_sup_tables.clear()
            if self._requested_return_acus is None:
                self.return_acus = sorted(self.dq.acus.keys())
                self.names = (
                    list(self.probe_names)
                    + [f"return_T:{a}" for a in self.return_acus]
                    + [f"inlet_T:{p}" for p in self.inlet_patches]
                )
                return

            servers_map = self.dq.map.get("servers", {})
            acus_map = self.dq.map.get("acus", {})
            span = self.dq.span
            resolved_patches: dict[str, tuple[str | None, str | None, bool, dict | None]] = {}
            needed_patches: list[str] = []
            still_missing: list[str] = []

            for a in self._requested_return_acus:
                if a in self.dq.acus:
                    sup_p = acus_map.get(a, {}).get("supply", {}).get("patch")
                    ret_p = acus_map.get(a, {}).get("return", {}).get("patch")
                    if sup_p and sup_p in span:
                        needed_patches.append(sup_p)
                        resolved_patches[a] = (sup_p, ret_p, False, None)
                    continue

                hint = self._acu_patches.get(a, {})
                ret_p = hint.get("return")
                sup_p = hint.get("supply")
                srv_entry = None
                in_servers = False

                if ret_p and ret_p in servers_map:
                    srv_entry = servers_map[ret_p]
                    in_servers = True
                    sup_p = sup_p or srv_entry.get("intake")
                else:
                    for out_p, s_ent in servers_map.items():
                        if out_p.startswith(f"{a}_") or out_p == f"{a}_return" or out_p == a:
                            ret_p = ret_p or out_p
                            sup_p = sup_p or s_ent.get("intake")
                            srv_entry = s_ent
                            in_servers = True
                            break

                if not ret_p or ret_p not in span:
                    for cand in (f"{a}_Back_patch", f"{a}_return"):
                        if cand in span:
                            ret_p = cand
                            break
                if not sup_p or sup_p not in span:
                    for cand in (f"{a}_Front_patch", f"{a}_supply"):
                        if cand in span:
                            sup_p = cand
                            break

                if not ret_p or ret_p not in span:
                    still_missing.append(a)
                    continue
                needed_patches.append(ret_p)
                if sup_p and sup_p in span:
                    needed_patches.append(sup_p)
                resolved_patches[a] = (sup_p, ret_p, in_servers, srv_entry)

            if still_missing:
                raise KeyError(f"Unknown ACUs in {cdir}: {still_missing}")

            face_tables = self._load_face_tables(h5_path, list(dict.fromkeys(needed_patches)), span)
            for a, (sup_p, ret_p, in_servers, srv_entry) in resolved_patches.items():
                if a in self.dq.acus:
                    if sup_p and sup_p in face_tables:
                        self._acu_sup_tables[a] = {
                            "patch": sup_p,
                            "ret_patch": ret_p,
                            "table": face_tables[sup_p],
                        }
                    continue
                sup_tbl = face_tables.get(sup_p) if sup_p else None
                ret_tbl = face_tables[ret_p]
                sup_frov = None
                if sup_p:
                    ent = u_bc.get(sup_p, {})
                    if ent.get("type") == "flowRateOutletVelocity":
                        if ent.get("volumetricFlowRate") is not None:
                            val = ent["volumetricFlowRate"]
                            q_val = float(val["val"] if isinstance(val, dict) else val)
                            sup_frov = (q_val, 1.0)
                        elif ent.get("massFlowRate") is not None:
                            val = ent["massFlowRate"]
                            q_val = float(val["val"] if isinstance(val, dict) else val)
                            sup_frov = (q_val, float(ent.get("rho", 1.0)))
                    elif srv_entry is not None and srv_entry.get("Q") is not None:
                        sup_frov = (float(srv_entry["Q"]), 1.0)
                self._stopped_acus[a] = {
                    "sup_patch": sup_p,
                    "ret_patch": ret_p,
                    "sup": sup_tbl,
                    "ret": ret_tbl,
                    "in_servers": bool(in_servers),
                    "sup_frov": sup_frov,
                    "ret_Q": None if srv_entry is None else srv_entry.get("Q"),
                }

            self.return_acus = list(self._requested_return_acus)
            self.names = (
                list(self.probe_names)
                + [f"return_T:{a}" for a in self.return_acus]
                + [f"inlet_T:{p}" for p in self.inlet_patches]
            )

    def set_snapshot_flows(self, patch_values: dict[str, Any] | None) -> None:
        """Update per-ACU target flow rates (m^3/s) and stopped flags used for ``return_T`` flux averaging."""
        with self._lock:
            self._snapshot_q_override.clear()
            self._snapshot_sup_q_override.clear()
            self._snapshot_stopped.clear()
            if not patch_values or self.dq is None:
                return
            qmap = patch_values.get("Q", {})
            self._snapshot_stopped.update(str(k) for k in (patch_values.get("stopped_acus") or {}).keys())

            def _to_float(val: Any) -> float:
                return float(val.detach().item()) if isinstance(val, torch.Tensor) else float(val)

            for name in self.dq.acus:
                map_acu = self.dq.map.get("acus", {}).get(name, {})
                ret_patch = map_acu.get("return", {}).get("patch")
                sup_patch = map_acu.get("supply", {}).get("patch")
                if ret_patch and ret_patch in qmap:
                    self._snapshot_q_override[name] = _to_float(qmap[ret_patch])
                if sup_patch and sup_patch in qmap:
                    self._snapshot_sup_q_override[name] = _to_float(qmap[sup_patch])

            for name, info in self._stopped_acus.items():
                ret_patch = info.get("ret_patch")
                sup_patch = info.get("sup_patch")
                if ret_patch and ret_patch in qmap:
                    self._snapshot_q_override[name] = _to_float(qmap[ret_patch])
                if sup_patch and sup_patch in qmap:
                    self._snapshot_sup_q_override[name] = _to_float(qmap[sup_patch])

    def _extract_UT(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        xf = x.reshape(-1)
        if self._slices is not None and self._n_cell is not None:
            n = self._n_cell
            U = xf[self._slices["U"]].reshape(3, n).T
            T = xf[self._slices["T"]]
            return U, T
        if xf.numel() % 7 != 0:
            raise ValueError(f"Expected 7-field flat state or explicit slices, got length {xf.numel()}")
        n = xf.numel() // 7
        U = xf[: 3 * n].reshape(3, n).T
        T = xf[4 * n : 5 * n]
        return U, T

    def observe(self, x: torch.Tensor, *, units: str = "K") -> torch.Tensor:
        """Return 1D differentiable vector of probe, ACU return, and optional server intake temperatures."""
        if self.dq is None:
            raise RuntimeError("HallProbeObserver has no bound .h5 case directory yet; call bind_case_dir() or run solve()")
        if units not in ("K", "C"):
            raise ValueError(f"units must be 'K' or 'C', got {units!r}")
        offset = 0.0 if units == "K" else -273.15
        x_dev = x.to(device=self.device, dtype=self.dtype)
        U, T = self._extract_UT(x_dev)
        parts = []
        if self.probe_idx is not None:
            parts.append(T[self.probe_idx] + self.TRef + offset)

        for name in self.return_acus:
            is_stopped_snap = name in self._snapshot_stopped
            if name in self.dq.acus and not is_stopped_snap:
                a = self.dq.acus[name]
                r = a["ret"]
                Tf = T[r["owner"]] + self.TRef + offset
                q_ov = self._snapshot_q_override.get(name)
                if a.get("frov") is not None and (q_ov is None or q_ov > 0.0):
                    target = q_ov if q_ov is not None else a["frov"][0]
                    rho_f = a["frov"][1] if q_ov is None else 1.0
                    phi = flow_rate_outlet_flux(U[r["owner"]], r["normal"], r["area"], target, rho_f)
                elif a["pinned"] and (q_ov is None or q_ov > 0.0):
                    q_val = q_ov if q_ov is not None else a["Q"]
                    phi = r["area"] * (q_val / r["area"].sum())
                else:
                    phi = (U[r["owner"]] * r["normal"]).sum(-1) * r["area"]
                sphi = phi.sum()
                if float(torch.abs(sphi.detach())) <= 1e-12:
                    area = r["area"]
                    ret_t = (area * Tf).sum() / area.sum()
                else:
                    ret_t = (phi * Tf).sum() / sphi
                parts.append(ret_t.reshape(1))
                continue

            if name in self._stopped_acus:
                sinfo = self._stopped_acus[name]
                sup_tbl = sinfo.get("sup")
                ret_tbl = sinfo["ret"]
                # Reverse-flow recirculation copies supply flux-mean temperature onto the return patch.
                if (sinfo.get("in_servers") or is_stopped_snap) and sup_tbl is not None:
                    Tf = T[sup_tbl["owner"]] + self.TRef + offset
                    sup_frov = sinfo.get("sup_frov")
                    q_sup_ov = self._snapshot_sup_q_override.get(name)
                    if sup_frov is not None and (q_sup_ov is None or q_sup_ov > 0.0):
                        target = q_sup_ov if q_sup_ov is not None else sup_frov[0]
                        rho_f = sup_frov[1] if q_sup_ov is None else 1.0
                        phi = flow_rate_outlet_flux(U[sup_tbl["owner"]], sup_tbl["normal"], sup_tbl["area"], target, rho_f)
                    elif q_sup_ov is not None and q_sup_ov > 0.0:
                        phi = flow_rate_outlet_flux(U[sup_tbl["owner"]], sup_tbl["normal"], sup_tbl["area"], q_sup_ov, 1.0)
                    else:
                        phi = sup_tbl["area"]
                    sphi = phi.sum()
                    if float(torch.abs(sphi.detach())) <= 1e-12:
                        area = sup_tbl["area"]
                        ret_t = (area * Tf).sum() / area.sum()
                    else:
                        ret_t = (phi * Tf).sum() / sphi
                    parts.append(ret_t.reshape(1))
                else:
                    Tf = T[ret_tbl["owner"]] + self.TRef + offset
                    area = ret_tbl["area"]
                    ret_t = (area * Tf).sum() / area.sum()
                    parts.append(ret_t.reshape(1))
                continue

            # Snapshot says stopped even though case was originally bound when ACU was running.
            a = self.dq.acus[name]
            sup_info = self._acu_sup_tables.get(name)
            q_sup_ov = self._snapshot_sup_q_override.get(name)
            if sup_info is not None:
                sup_tbl = sup_info["table"]
                Tf = T[sup_tbl["owner"]] + self.TRef + offset
                if q_sup_ov is not None and q_sup_ov > 0.0:
                    phi = flow_rate_outlet_flux(U[sup_tbl["owner"]], sup_tbl["normal"], sup_tbl["area"], q_sup_ov, 1.0)
                    sphi = phi.sum()
                    ret_t = (sup_tbl["area"] * Tf).sum() / sup_tbl["area"].sum() if float(torch.abs(sphi.detach())) <= 1e-12 else (phi * Tf).sum() / sphi
                else:
                    ret_t = (sup_tbl["area"] * Tf).sum() / sup_tbl["area"].sum()
                parts.append(ret_t.reshape(1))
            else:
                r = a["ret"]
                Tf = T[r["owner"]] + self.TRef + offset
                ret_t = (r["area"] * Tf).sum() / r["area"].sum()
                parts.append(ret_t.reshape(1))

        if self.inlet_patches:
            q_dict = self.dq(U, T)
            for p in self.inlet_patches:
                parts.append((q_dict[f"inlet_T:{p}"] + offset).reshape(1))

        return torch.cat(parts) if parts else torch.empty(0, dtype=self.dtype, device=self.device)
