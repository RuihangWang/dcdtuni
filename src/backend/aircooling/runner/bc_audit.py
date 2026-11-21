"""Audit the boundary conditions a solve is about to run with.

Each issue this catches was once silent: every patch had a binding with the
right parameters, and the field file the solver read said something else.

- perforated baffles written as pure ``cyclic`` (no pressure drop) for a month
  of dcCart solves;
- a fan inlet turned into a wall because its paired outlet sub-patch had been
  clipped out of the STL, while the outlets kept blowing (T ran to 1.7 K);
- rack doors with different perforations sharing one merged set of D/I.

An ACU supply is a fixed flow, except in a sealed region with no return
(pressureInletOutletVelocity + totalPressure, fields._sealed_region_supplies).
Its bound flow is the configured one unless the runner balanced the ACUs of a
sealed mesh (fields._balance_sealed_acu_airflow); then the balanced flow from
``acu-airflow-balance.json`` is the intent.

The audit reads the written ``0/`` fields (every processor directory, or the
serial one) and compares them, patch by patch, with the bindings of the
manifest the solve executes and with the runner's own registry (the intent
after its corrections: fan flow split by area, returns pinned to supply,
merged doors aligned). It never changes a field and never stops a solve: the
runner writes ``bc-audit.json`` next to the log and logs each issue.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_PATCH_RE = re.compile(r"\n    (\S+)\s*\n    \{(.*?)\n    \}", re.S)
_TYPE_RE = re.compile(r"\btype\s+(\S+);")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# bcType -> (U types, T types, p_rgh types) a correct field file may carry
EXPECTED_TYPES: Dict[str, tuple] = {
    "acu_supply": ({"flowRateInletVelocity", "fixedValue", "uniformFixedValue"},
                   {"fixedValue", "uniformFixedValue", "exprFixedValue"}, {"fixedFluxPressure"}),
    "acu_return": ({"pressureInletOutletVelocity", "flowRateOutletVelocity"},
                   {"zeroGradient", "inletOutlet"}, {"fixedValue", "fixedFluxPressure"}),
    "fan_inlet": ({"flowRateOutletVelocity"}, {"inletOutlet", "zeroGradient"}, {"fixedFluxPressure"}),
    "fan_outlet": ({"flowRateInletVelocity"}, {"uniformFixedValue", "fixedValue"}, {"fixedFluxPressure"}),
    "wall": ({"noSlip"}, {"zeroGradient"}, {"fixedFluxPressure"}),
    "wall_baffle": ({"noSlip"}, {"zeroGradient"}, {"fixedFluxPressure"}),
    "nonfan_fixed_temp": ({"noSlip"}, {"fixedValue"}, {"fixedFluxPressure"}),
    "fixedTemperature": ({"noSlip"}, {"fixedValue"}, {"fixedFluxPressure"}),
    "nonfan_heat_flux": ({"noSlip"}, {"externalWallHeatFluxTemperature"}, {"fixedFluxPressure"}),
    "heat_source_flux": ({"noSlip"}, {"externalWallHeatFluxTemperature"}, {"fixedFluxPressure"}),
}
# bindings that need not exist as patches: walls can be buried between two
# solids; through zones are cell zones
_MAY_BE_ABSENT = {"wall", "wall_baffle", "through_zone"}


def _field_dirs(run_dir: Path) -> List[Path]:
    procs = sorted(run_dir.glob("processor*/0"))
    return procs if procs else [run_dir / "0"]


def read_patch_blocks(run_dir: Path, field: str) -> Dict[str, str]:
    """{patch: its boundaryField block body} over every processor."""
    out: Dict[str, str] = {}
    for d in _field_dirs(Path(run_dir)):
        path = d / field
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        i = text.find("boundaryField")
        if i < 0:
            continue
        for m in _PATCH_RE.finditer(text[i:]):
            name, body = m.groups()
            if not name.startswith("procBoundary"):
                out.setdefault(name, body)
    return out


def _type(body: Optional[str]) -> Optional[str]:
    m = _TYPE_RE.search(body or "")
    return m.group(1) if m else None


def _scalar(body: Optional[str], key: str) -> Optional[float]:
    m = re.search(rf"\b{key}\s+({_NUM})\s*;", body or "")
    return float(m.group(1)) if m else None


def _uniform(body: Optional[str]) -> Optional[float]:
    m = re.search(rf"\bvalue\s+uniform\s+({_NUM})\s*;", body or "")
    return float(m.group(1)) if m else None


def _close(a: Optional[float], b: Optional[float], rel: float = 1e-4, abs_: float = 1e-9) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def audit(run_dir: Path, manifest: Dict[str, Any], registry: Any = None,
          perforation_coefficients=None) -> Dict[str, Any]:
    """Issues between the manifest/registry intent and the written fields."""
    run_dir = Path(run_dir)
    U, T, P = (read_patch_blocks(run_dir, f) for f in ("U", "T", "p_rgh"))
    issues: List[Dict[str, Any]] = []

    def issue(kind: str, patch: str, detail: str) -> None:
        issues.append({"kind": kind, "patch": patch, "detail": detail})

    def spec(name: str):
        return registry.get(name) if registry is not None else None

    bindings: Dict[str, tuple] = {}
    for b in (manifest.get("boundaryBindings") or {}).values():
        bindings[b.get("patchName")] = (b.get("bcType"), b.get("parameters") or {})
    for b in (manifest.get("porousBindings") or {}).values():
        params = b.get("parameters") or {}
        sigma = params.get("sigma")
        kind = ("porous" if isinstance(sigma, (int, float)) and 0 < sigma < 1 else
                "open" if isinstance(sigma, (int, float)) and sigma >= 1 else "wall_baffle")
        bindings[b.get("patchName")] = (kind, params)

    balanced: Dict[str, float] = {}
    for d in [run_dir, *sorted(run_dir.glob("processor*"))]:
        f = d / "acu-airflow-balance.json"
        if f.is_file():
            try:
                for u in json.loads(f.read_text()).get("units", []):
                    balanced[u["supply"]] = float(u["balanced"])
            except (ValueError, KeyError, TypeError):
                pass
            break

    checked = 0
    for name, (kind, params) in sorted(bindings.items()):
        if not name:
            continue
        if name not in U:
            reg = spec(name)
            if kind in _MAY_BE_ABSENT or kind == "open":
                continue
            if (re.search(r"_door_(master|slave)$", name) and "merged_rack_door_master" in U):
                continue  # identical doors share the merged pair
            if name.startswith("merged_rack_door_"):
                continue  # doors with different perforations have their own pairs
            if kind == "fan_outlet" and reg is not None and not (reg.flow_rate or 0):
                continue  # a sub-patch the export clipped away, given no flow
            stem = re.split(r"_outlet(?:_|$)", name)[0]
            if kind == "fan_outlet" and any(n.startswith(stem + "_outlet") and n in U for n in bindings):
                issue("clipped_subpatch", name, "outlet sub-patch has no face (overlapping sub-patches "
                      "clip each other in the export); its server's flow goes to the meshed ones by area")
                continue
            issue("missing_patch", name, f"{kind} is bound but has no face in the mesh")
            continue
        checked += 1
        u, t, p = _type(U.get(name)), _type(T.get(name)), _type(P.get(name))
        if kind == "porous":
            if p != "porousBafflePressure":
                issue("type", name, f"perforated baffle p_rgh is '{p}', not porousBafflePressure")
            elif perforation_coefficients is not None:
                reg = spec(name)
                if reg is not None and reg.perforated is not None:
                    D, I, _ = perforation_coefficients(reg.perforated)
                    got_d, got_i = _scalar(P[name], "D"), _scalar(P[name], "I")
                    if not (_close(got_d, D, 1e-5, 1e-6) and _close(got_i, I, 1e-5, 1e-6)):
                        issue("value", name, f"porous D/I {got_d}/{got_i}, perforation gives {D:.6g}/{I:.6g}")
            continue
        if kind == "open":
            continue
        expected = EXPECTED_TYPES.get(kind)
        if expected is None:
            continue
        if kind == "acu_supply" and u == "pressureInletOutletVelocity" and p == "totalPressure":
            # The supply of a sealed supply region (contained cold aisle,
            # closed plenum): a pressure inlet that delivers what the
            # region's servers draw; only the pair is valid.
            expected = ({u}, expected[1], {p})
        for field, got, allowed in zip(("U", "T", "p_rgh"), (u, t, p), expected):
            if got not in allowed:
                issue("type", name, f"{kind} {field} is '{got}', expected one of {sorted(allowed)}")
        reg = spec(name)
        if kind == "acu_supply" and u == "flowRateInletVelocity":
            q = _scalar(U[name], "volumetricFlowRate")
            want = balanced.get(name, params.get("flowRateM3s"))
            if q is not None and not _close(q, want):
                issue("value", name, f"supply flow {q} m3/s, bound {want}"
                      + (" (balanced)" if name in balanced else ""))
            if t == "fixedValue" and params.get("temperatureC") is not None:
                tv = _uniform(T[name])
                if tv is not None and not _close(tv, params["temperatureC"] + 273.15, 1e-7, 1e-6):
                    issue("value", name, f"supply T {tv} K, bound {params['temperatureC']} C")
        if kind in ("nonfan_fixed_temp", "fixedTemperature") and t == "fixedValue":
            tv = _uniform(T[name])
            want = params.get("temperatureC")
            if tv is not None and want is not None and not _close(tv, want + 273.15, 1e-7, 1e-6):
                issue("value", name, f"fixed T {tv} K, bound {want} C")

    # mass balance of every fan server (inlet = sum of its outlets)
    # every fan server: inlet = sum of its outlets = the bound outlet total.
    # (Sub-patch flows are split by area, so only the sums are fixed.) A
    # timetable flow is not a number here and leaves its server unchecked.
    servers: Dict[str, Dict[str, Any]] = {}
    for name, (kind, params) in bindings.items():
        if kind not in ("fan_inlet", "fan_outlet") or not name:
            continue
        # a liquid-cooled fan server's air inlet is its front face, <stem>_front
        stem = re.split(r"_(?:inlet|outlet)(?:_|$)", name)[0]
        if kind == "fan_inlet" and stem == name and name.endswith("_front"):
            stem = name[: -len("_front")]
        s = servers.setdefault(stem, {"in": 0.0, "out": 0.0, "bound": 0.0, "table": False})
        if kind == "fan_outlet":
            s["bound"] += float(params.get("flowRateM3s") or 0.0)
        if name not in U:
            continue
        q = _scalar(U[name], "volumetricFlowRate")
        if q is None:
            s["table"] = s["table"] or "volumetricFlowRate" in (U[name] or "")
            continue
        s["in" if kind == "fan_inlet" else "out"] += q
    for stem, s in sorted(servers.items()):
        if s["table"] or (s["in"] == 0 and s["out"] == 0):
            continue
        if not _close(s["in"], s["out"], 1e-6, 1e-9):
            issue("balance", stem, f"fan server draws {s['in']:.6g} m3/s, blows {s['out']:.6g}")
        elif s["bound"] > 0 and not _close(s["out"], s["bound"], 1e-6, 1e-9):
            issue("value", stem, f"fan server blows {s['out']:.6g} m3/s, bound {s['bound']:.6g}")

    return {"format": "dcdtuni.bc-audit/v1", "patchesChecked": checked,
            "bindings": len(bindings), "issues": issues}
