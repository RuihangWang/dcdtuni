"""Translate a dctwin air-cooling HF case (OpenFOAM ``buoyantBoussinesqSimpleFoam``) into this repository's
``bc/<field>.toml`` and ``fvconfig.toml``.

The mesh itself is converted by ``parse_openfoam_fast.py``; this module only reads the case's dictionaries:
``<run>/processor0/0/{U,p_rgh,T,k,epsilon}`` (or ``<run>/0/`` for a serial case; every rank lists every patch),
``constant/{transportProperties,g,turbulenceProperties}`` and ``system/fvSchemes``.

Patch roles (from the U / T boundary types the dctwin runner writes, ``runner/fields.py``):

==================  =============================  ===================================================
role                OpenFOAM (HF)                  here
==================  =============================  ===================================================
acu_supply          U flowRateInletVelocity,       flowRateInletVelocity; T fixedValue; k, epsilon
                    T fixedValue                   fixedValue from the intensity / mixing-length inlet
                                                   formulas evaluated at U_b = Q / A (uniform, exact)
server_outlet       U flowRateInletVelocity,       flowRateInletVelocity; T rackOutletTemperature (CBBM,
                    T uniformFixedValue expression live intake mean, deltaT from the expression's fixed
                                                   point); k, epsilon as acu_supply
server_inlet,       U flowRateOutletVelocity       rackInletVelocity (uniform suction Q/A, tangential
acu_return_pinned                                  mirrored) -- or flowRateOutletVelocity with
                                                   ``fan_inlet="openfoam"``; T, k, epsilon zeroGradient
acu_return_ref      U pressureInletOutletVelocity, zeroGradient U (its outflow branch); p_rgh fixedValue
                    p_rgh fixedValue 0
wall                noSlip, wall functions         noSlip, kqRWallFunction, epsilonWallFunction
baffle              cyclic + porousBafflePressure  cyclic; p_rgh porousBafflePressure (jump)
==================  =============================  ===================================================

Temperature is carried as the EXCESS over the case's ``TRef`` (here 295.15 K): absolute kelvin exhausts GMRES
(``experiments/DataCenter/CoupledNewton/kepsilon-cbbm-wf/h5/fvconfig.toml``), and ``beta (T - TRef)`` is
unchanged. Deviations from the HF solver are listed in ``deviations`` of the returned summary and written to
``dctwin_map.json`` next to the TOML files.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from Parse_mesh.foamio import FoamDict, read_foam_file

FIELDS = ("U", "p_rgh", "T", "k", "epsilon")
_EXPR = re.compile(r"^\s*([0-9.eE+-]+)\*tSink_\w+\s*\+\s*([0-9.eE+-]+)\*tOutPrev_\w+\s*\+\s*([0-9.eE+-]+)\s*$")
_SINK = re.compile(r"^tSink_\w+\{(\w+)\}\s*=\s*sum\(phi\*T\)/sum\(phi\)\s*$")


def _time0(run: Path) -> Path:
    for cand in (run / "processor0" / "0", run / "0"):
        if (cand / "U").exists():
            return cand
    raise FileNotFoundError(f"{run}: no 0/ directory with U (serial or processor0)")


def _uniform(v):
    """Value of a ``uniform x`` entry (``None`` for anything else)."""
    if isinstance(v, tuple) and len(v) == 2 and v[0] == "uniform":
        a = np.asarray(v[1], dtype=float)
        return float(a) if a.ndim == 0 else a.tolist()
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _scalar_entry(d: FoamDict, key: str, default=None):
    v = d.get(key, default)
    if isinstance(v, tuple):
        v = v[-1]
    if isinstance(v, (FoamDict, dict)):
        raise ValueError(f"{key}: a Function1 dictionary is not supported here ({dict(v)})")
    return None if v is None else float(v)


def _empty_list(v) -> bool:
    return isinstance(v, tuple) and len(v) == 3 and v[0] == "nonuniform" and getattr(v[2], "size", 1) == 0


def _has_empty(d) -> bool:
    return isinstance(d, dict) and any(_empty_list(v) for v in d.values())


def _whole_entries(path: str) -> dict:
    """The boundary entries of one processor's field file that carry no empty list (worker of ``read_case_dicts``)."""
    _, d = read_foam_file(Path(path))
    return {n: e for n, e in d["boundaryField"].items()
            if isinstance(e, dict) and e.get("type") != "processor" and not _has_empty(e)}


def read_case_dicts(run: str | Path) -> dict:
    run = Path(run)
    t0 = _time0(run)
    bcs, internal = {}, {}
    for f in FIELDS:
        _, d = read_foam_file(t0 / f)
        bcs[f] = d["boundaryField"]
        internal[f] = _uniform(d["internalField"])
    # a patch without faces on processor0 has its values there as empty lists (``nonuniform List<scalar> 0()``, what
    # decomposePar writes); its entry is taken from the first processor that holds faces of it
    todo = {f: {n for n, e in bcs[f].items() if _has_empty(e)} for f in FIELDS}
    if any(todo.values()) and t0.parent.name == "processor0":
        from concurrent.futures import ProcessPoolExecutor
        others = sorted((q for q in run.glob("processor[0-9]*") if q.name != "processor0" and (q / t0.name).is_dir()),
                        key=lambda q: int(q.name[len("processor"):]))
        jobs = [(f, str(q / t0.name / f)) for f in FIELDS if todo[f] for q in others]
        with ProcessPoolExecutor(max_workers=min(8, max(len(jobs), 1))) as pool:
            for (f, _), whole in zip(jobs, pool.map(_whole_entries, [j[1] for j in jobs])):
                for n in list(todo[f]):
                    if n in whole:
                        bcs[f][n] = whole[n]
                        todo[f].discard(n)
    _, tp = read_foam_file(run / "constant" / "transportProperties")
    _, g = read_foam_file(run / "constant" / "g")
    _, tu = read_foam_file(run / "constant" / "turbulenceProperties")
    _, sch = read_foam_file(run / "system" / "fvSchemes")
    return dict(bc=bcs, internal=internal, transport=tp, g=g, turbulence=tu, schemes=sch, time0=t0)


def acu_patches(run: Path) -> dict[str, dict]:
    """ACU supply / return patch names, ``{acu: {"supply": patch, "return": patch}}``.  The caller's request
    (``<run>/gpuNewton/request.json``, ``supplyPatches`` / ``returnPatches``) is the source when it is there: the
    two sorted lists are paired by their longest common prefix.  Without a request (the research datasets) the
    newest dctwin API result next to the run that carries the ACU metrics (``../api/*/result.json``) is read;
    empty when neither is there (the roles then follow from the boundary-condition types)."""
    import glob
    import os.path
    req = run / "gpuNewton" / "request.json"
    if req.is_file():
        r = json.load(open(req))
        sup, ret = list(r.get("supplyPatches") or []), list(r.get("returnPatches") or [])
        if sup or ret:
            out, left = {}, list(ret)
            for s_ in sup:
                best = max(left, key=lambda q: len(os.path.commonprefix([s_, q])), default=None)
                if best is not None and len(os.path.commonprefix([s_, best])) >= 3:
                    left.remove(best)
                    name = os.path.commonprefix([s_, best])
                    out[name[:name.rfind("_")] if "_" in name else name] = {"supply": s_, "return": best}
                else:
                    out[s_] = {"supply": s_}
            for q in left:
                out[q] = {"return": q}
            return out
    for hit in sorted(glob.glob(str(run.parent / "api" / "*" / "result.json")), reverse=True):
        try:
            acus = json.load(open(hit))["metrics"]["fields"]["acus"]
        except (KeyError, TypeError, ValueError):
            continue                         # a failed or still running job has no field metrics
        out = {}
        for a in acus:
            name = str(a.get("objectPath", a["supplyPatch"])).split(".")[-1]
            out[name] = {"supply": a["supplyPatch"], "return": a["returnPatch"]}
        return out
    return {}


def classify(name: str, bc: dict, returns: set[str] | None = None, supplies: set[str] | None = None) -> str:
    """Role of a patch for the engineering quantities (the translation itself is by condition type)."""
    u = bc["U"][name]["type"]
    t = bc["T"][name]["type"]
    p = bc["p_rgh"][name]["type"]
    if u == "processor":
        return "processor"
    if u == "cyclic" or p == "porousBafflePressure":
        return "baffle"
    if u == "noSlip":
        return "wall"
    if u == "flowRateInletVelocity":
        if supplies is not None:
            return "acu_supply" if name in supplies else "server_outlet"
        if t == "fixedValue":
            return "acu_supply"
        if t == "uniformFixedValue":
            return "server_outlet"
    if u == "flowRateOutletVelocity":
        if returns is not None:
            return "acu_return_pinned" if name in returns else "server_inlet"
        return "acu_return_pinned" if name.startswith("irc_") or "_Back_" in name else "server_inlet"
    if u == "pressureInletOutletVelocity":
        return "acu_return_ref"
    if u in ("empty",):
        return "empty"
    raise ValueError(f"patch {name}: unrecognised combination U={u}, T={t}, p_rgh={p}")


def _server_outlet(name: str, tbc: FoamDict) -> tuple[str, float, float]:
    """(intake patch, deltaT, damping) from the dctwin expression ``a*tSink + b*tOutPrev + c``.

    The fixed point of T = a*Tsink + b*T + c with a + b = 1 is T = Tsink + c/a."""
    uv = tbc["uniformValue"]
    if uv.get("type") != "expression":
        raise ValueError(f"{name}: expected an expression uniformValue, got {uv.get('type')}")
    m = _EXPR.match(str(uv["expression"]).strip('"'))
    if m is None:
        raise ValueError(f"{name}: unrecognised outlet expression {uv['expression']!r}")
    a, b, c = (float(x) for x in m.groups())
    if abs(a + b - 1.0) > 1e-9 or a <= 0:
        raise ValueError(f"{name}: expression weights {a}, {b} do not form a damped fixed point")
    intake = None
    for var in uv.get("variables", []):
        mm = _SINK.match(str(var).strip('"'))
        if mm:
            intake = mm.group(1)
    if intake is None:
        raise ValueError(f"{name}: no flux-weighted tSink variable naming the intake patch")
    return intake, c / a, a


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_fmt(x)}" for k, x in v.items()) + "}"
    raise TypeError(type(v))


def _uni(v):
    return {"value_type": "uniform", "val": v}


def _const(v):
    return {"value_type": "constant", "val": float(v)}


def _write_bc_toml(path: Path, field: str, dims, internal, entries: dict[str, dict], note: str):
    lines = [f"# {note}", "[field_info]", f"name = {_fmt(field)}",
             f"description = {_fmt('generated by Parse_mesh/dctwin_bc.py from the dctwin HF case')}",
             "[dimensions]", f"value = {_fmt([float(x) for x in dims])}",
             "[internalField]", 'value_type = "uniform"', f"val = {_fmt(internal)}"]
    for patch, e in entries.items():
        lines.append(f"[boundaryfield.{json.dumps(patch) if not re.fullmatch(r'[A-Za-z0-9_-]+', patch) else patch}]")
        for k, v in e.items():
            lines.append(f"{k} = {_fmt(v)}")
    path.write_text("\n".join(lines) + "\n")


DIMS = {"U": [0, 1, -1, 0, 0, 0, 0], "p_rgh": [0, 2, -2, 0, 0, 0, 0], "T": [0, 0, 0, 1, 0, 0, 0],
        "k": [0, 2, -2, 0, 0, 0, 0], "epsilon": [0, 2, -3, 0, 0, 0, 0]}


# ----------------------------------------------------------------- translation by OpenFOAM condition type
_SAME = {"zeroGradient", "cyclic", "empty", "symmetry"}


def _refuse(field: str, patch: str, d: FoamDict, why: str = "not supported"):
    raise NotImplementedError(f"{field} on patch {patch}: condition {d.get('type')!r} {why}")


def _vec_uniform(d: FoamDict, key: str = "value"):
    v = _uniform(d.get(key))
    if v is None:
        raise NotImplementedError(f"{key} of a {d.get('type')} condition is not uniform")
    return v


def _tr_U(n, d, A, fan_inlet, dev):
    """Returns (entry, |U_b| or None)."""
    t = d["type"]
    if t in ("flowRateInletVelocity", "flowRateOutletVelocity"):
        if "volumetricFlowRate" not in d:
            _refuse("U", n, d, "without a constant volumetricFlowRate")
        Q = _scalar_entry(d, "volumetricFlowRate")
        Ub = Q / A if A > 0 else None
        if t == "flowRateInletVelocity":
            return {"type": t, "volumetricFlowRate": _const(Q), "value": _uni([0.0, 0.0, 0.0])}, Ub
        if fan_inlet == "uniform":
            dev.add("flowRateOutletVelocity: uniform suction Q/A with mirrored tangential velocity "
                    "(rackInletVelocity) instead of the rescaled interior profile")
            return {"type": "rackInletVelocity", "volumetricFlowRate": _const(Q), "value": _uni([0.0, 0.0, 0.0])}, Ub
        return {"type": t, "volumetricFlowRate": _const(Q), "value": _uni([0.0, 0.0, 0.0])}, Ub
    if t == "pressureInletOutletVelocity":
        dev.add("pressureInletOutletVelocity: zeroGradient U (its outflow branch)")
        return {"type": "zeroGradient"}, None
    if t == "noSlip":
        return {"type": "noSlip"}, 0.0
    if t == "fixedValue":
        v = _vec_uniform(d)
        return {"type": "fixedValue", "value": _uni(v)}, float(np.linalg.norm(v))
    if t == "symmetryPlane":
        return {"type": "symmetry"}, None
    if t in _SAME | {"slip"}:
        return {"type": t}, None
    if t == "inletOutlet":
        return {"type": "inletOutlet", "inletValue": _uni(_vec_uniform(d, "inletValue")),
                "value": _uni(_vec_uniform(d, "inletValue"))}, None
    _refuse("U", n, d)


def _tr_p(n, d, dev):
    t = d["type"]
    if t == "fixedFluxPressure":
        return {"type": t}
    if t == "fixedValue":
        return {"type": t, "value": _uni(_vec_uniform(d))}
    if t == "totalPressure":
        # p = p0 on outflow faces, p0 - |U|^2 / 2 on inflow faces.  A fixed static p0 in its place was tried on the two
        # cold-aisle variants (2026-11-11, D4): without the dynamic head an inflow opening with a zero-gradient
        # velocity has nothing that limits the inflow, one solve wandered and one ended in non-finite steps.
        _refuse("p_rgh", n, d, "(a total-pressure opening needs the inflow's dynamic head: planned as a lagged condition)")
    if t == "porousBafflePressure":
        if str(d.get("uniformJump", "false")).lower() in ("true", "yes", "on"):
            _refuse("p_rgh", n, d, "with uniformJump true")
        dev.add("porousBafflePressure: jump from the mean owner normal velocity, no relaxation")
        return {"type": t, "I": _scalar_entry(d, "I"), "D": _scalar_entry(d, "D", 0.0),
                "length": _scalar_entry(d, "length", 1.0)}
    if t == "symmetryPlane":
        return {"type": "symmetry"}
    if t in _SAME:
        return {"type": t}
    _refuse("p_rgh", n, d)


def _tr_T(n, d, TRef, dev):
    t = d["type"]
    if t == "fixedValue":
        return {"type": t, "value": _uni(_vec_uniform(d) - TRef)}
    if t == "uniformFixedValue":
        uv = d.get("uniformValue")
        if isinstance(uv, FoamDict) and uv.get("type") == "expression":
            intake, dT, lam = _server_outlet(n, d)
            dev.add("server outlet T: undamped fixed point Tsink + deltaT of the damped HF expression")
            # meanWeight "flux": the HF tSink is sum(phi*T)/sum(phi) (_SINK); equal to the area mean under uniform
            # suction, and the right weighting under fan_inlet="openfoam"
            return {"type": "rackOutletTemperature", "model": "cbbm", "lagMean": False, "partner_patch": intake,
                    "deltaT": dT, "value": _uni(dT), "meanWeight": "flux", "_damping": lam}
        if isinstance(uv, tuple) and uv[0] == "constant":
            return {"type": "fixedValue", "value": _uni(float(uv[1]) - TRef)}
        if isinstance(uv, (int, float)):
            return {"type": "fixedValue", "value": _uni(float(uv) - TRef)}
        _refuse("T", n, d, f"with uniformValue {uv!r}")
    if t == "inletOutlet":
        dev.add("inletOutlet T/k/epsilon: zeroGradient (outflow branch)")
        return {"type": "zeroGradient"}
    if t == "symmetryPlane":
        return {"type": "symmetry"}
    if t in _SAME:
        return {"type": t}
    if t == "externalWallHeatFluxTemperature":
        _refuse("T", n, d, "(heat-flux walls: planned, needs the wall alphaEff)")
    _refuse("T", n, d)


def _tr_k(n, d, Ub, dev):
    t = d["type"]
    if t == "turbulentIntensityKineticEnergyInlet":
        if Ub is None:
            v = _uniform(d.get("value"))
            if v is None:
                _refuse("k", n, d, "on a patch whose velocity magnitude is not fixed")
            dev.add("turbulent inlet k without a fixed |U_b|: the case's written value")
            return {"type": "fixedValue", "value": _uni(v)}
        dev.add("turbulent inlets: k and epsilon fixed at U_b = Q/A (exact for a uniform flow-rate inlet)")
        return {"type": "fixedValue", "value": _uni(1.5 * (_scalar_entry(d, "intensity") * Ub) ** 2)}
    if t in ("kqRWallFunction",):
        return {"type": t}
    return _tr_turb_common("k", n, d, dev)


def _tr_eps(n, d, kval, cmu, dev):
    t = d["type"]
    if t == "turbulentMixingLengthDissipationRateInlet":
        if kval is None:
            _refuse("epsilon", n, d, "where k is not a fixed value")
        return {"type": "fixedValue", "value": _uni(cmu ** 0.75 * kval ** 1.5 / _scalar_entry(d, "mixingLength"))}
    if t == "epsilonWallFunction":
        # OpenFOAM's wall functions read turbulenceModel::y() = nearWallDist (Turbulence.near_wall_dist)
        return {"type": t, "wallDistance": "nearWallDist"}
    return _tr_turb_common("epsilon", n, d, dev)


def _tr_turb_common(field, n, d, dev):
    t = d["type"]
    if t == "fixedValue":
        return {"type": t, "value": _uni(_vec_uniform(d))}
    if t == "inletOutlet":
        dev.add("inletOutlet T/k/epsilon: zeroGradient (outflow branch)")
        return {"type": "zeroGradient"}
    if t == "symmetryPlane":
        return {"type": "symmetry"}
    if t in _SAME:
        return {"type": t}
    _refuse(field, n, d)


_ALLOWED_FVOPTIONS = {"limitTurbulenceViscosity"}


def _check_fv_options(run: Path):
    """Refuse, by name, any active fvOptions source other than the inert nut limiter."""
    f = run / "system" / "fvOptions"
    if not f.exists():
        return
    _, d = read_foam_file(f)
    bad = []
    for name, src in d.items():
        if not isinstance(src, FoamDict):
            continue
        if str(src.get("active", "true")).lower() in ("false", "no", "off"):
            continue
        if src.get("type") not in _ALLOWED_FVOPTIONS:
            bad.append(f"{name} ({src.get('type')})")
    if bad:
        raise NotImplementedError(f"{f}: fvOptions sources not supported yet: {', '.join(bad)}")


def _div_scheme(v, key: str) -> str:
    """OpenFOAM ``divSchemes`` entry -> this repository's spelling; anything else is refused by name."""
    t = [str(x) for x in (v if isinstance(v, tuple) else (v,))]
    bounded = t[:1] == ["bounded"]
    t = t[1:] if bounded else t
    if t[:1] != ["Gauss"] or len(t) < 2:
        raise NotImplementedError(f"fvSchemes {key}: {v!r}")
    name = t[1]
    if name == "upwind":
        out = "Upwind"
    elif name in ("linearUpwind", "linearUpwindV"):
        out = "LinearUpwind"
    elif name in ("limitedLinear", "limitedLinearV"):
        out = f"LimitedLinear {t[2] if len(t) > 2 else 1}"
    elif name == "linear":
        out = "Linear corrected"
    else:
        raise NotImplementedError(f"fvSchemes {key}: convection scheme {name!r} is not supported")
    return f"bounded {out}" if bounded else out


def _case_div_schemes(schemes) -> dict[str, str]:
    div = schemes.subdict("divSchemes")
    out = {}
    for field in ("U", "T", "k", "epsilon"):
        v = div.lookup(f"div(phi,{field})", None)
        if v is None:
            v = div.get("default")
        if v is None or v == "none":
            raise NotImplementedError(f"fvSchemes: no div(phi,{field}) and no default")
        out[field] = _div_scheme(v, f"div(phi,{field})")
    return out


def translate(run: str | Path, out_dir: str | Path, patch_areas: dict[str, float], *,
              fan_inlet: str = "openfoam", cmu_inlet: float = 0.09, div_scheme: str | None = None,
              grad_scheme: str = "1st", lap_scheme: str = "Linear corrected") -> dict:
    """Write ``bc/*.toml``, ``fvconfig.toml`` and ``dctwin_map.json`` into ``out_dir``.

    ``patch_areas``: total face area of every physical patch with faces (needed for U_b = Q/A at the
    flow-rate patches, which fixes the turbulent inlet k and epsilon).
    """
    run, out = Path(run), Path(out_dir)
    (out / "bc").mkdir(parents=True, exist_ok=True)
    c = read_case_dicts(run)
    bc = c["bc"]
    tp = c["transport"]
    TRef = float(_scalar_entry(tp, "TRef"))
    names = [n for n in bc["U"].keys() if bc["U"][n].get("type") != "processor"]
    acu_map = acu_patches(run)
    returns = {v["return"] for v in acu_map.values() if "return" in v} or None
    supplies = {v["supply"] for v in acu_map.values() if "supply" in v} or None
    acu_of = {v[k]: a for a, v in acu_map.items() for k in ("supply", "return") if k in v}
    roles = {}
    for n in names:
        try:
            roles[n] = classify(n, bc, returns, supplies)
        except ValueError:
            roles[n] = "other"          # translated by condition type below; no engineering quantity attached
    dev: set[str] = set()
    servers, acus = {}, {}
    ent = {f: {} for f in FIELDS}
    for n in names:
        r = roles[n]
        ub, pb, tb, kb, eb = (bc[f][n] for f in FIELDS)
        A = patch_areas.get(n, 0.0)
        ent["U"][n], Ub = _tr_U(n, ub, A, fan_inlet, dev)
        ent["p_rgh"][n] = _tr_p(n, pb, dev)
        ent["T"][n] = _tr_T(n, tb, TRef, dev)
        ent["k"][n] = _tr_k(n, kb, Ub, dev)
        kval = ent["k"][n].get("value", {}).get("val") if ent["k"][n]["type"] == "fixedValue" else None
        ent["epsilon"][n] = _tr_eps(n, eb, kval, cmu_inlet, dev)
        Q = _scalar_entry(ub, "volumetricFlowRate") if "volumetricFlowRate" in ub else None
        if r == "acu_supply":
            acus.setdefault(acu_of.get(n, n), {})["supply"] = dict(patch=n, Q=Q, T_supply_K=_uniform(tb["value"]), area=A)
        elif r == "acu_return_pinned":
            acus.setdefault(acu_of.get(n, n), {})["return"] = dict(patch=n, Q=Q, pinned=True, area=A)
        elif r == "acu_return_ref":
            acus.setdefault(acu_of.get(n, n), {})["return"] = dict(patch=n, pinned=False, area=A)
        elif r == "server_outlet" and ent["T"][n]["type"] == "rackOutletTemperature":
            t = ent["T"][n]
            servers[n] = dict(intake=t["partner_patch"], Q=Q, deltaT=t["deltaT"], damping_hf=t.pop("_damping"), area=A)
    for t in ent["T"].values():
        t.pop("_damping", None)
    deviations = sorted(dev) + ["limitTurbulenceViscosity fvOption (nut <= c nu, c = 1e5) accepted but not applied",
                                "laplacian/snGrad 'limited corrected 0.5' translated as fully corrected",
                                "gradients: weighted least squares (\"1st\") instead of Gauss linear (contract.py)"]
    _check_fv_options(run)
    seeds = c["internal"]
    for f in FIELDS:
        internal = seeds[f]
        if f == "T":
            internal = internal - TRef
        _write_bc_toml(out / "bc" / f"{f}.toml", f, DIMS[f], internal, ent[f],
                       "dctwin HF case translated by Parse_mesh/dctwin_bc.py; T is the excess over TRef")

    nu = float(_scalar_entry(tp, "nu"))
    Pr = float(_scalar_entry(tp, "Pr"))
    Prt = float(_scalar_entry(tp, "Prt", 0.85))
    beta = float(_scalar_entry(tp, "beta"))
    gv = np.asarray(c["g"]["value"], dtype=float).tolist()
    model = str(c["turbulence"].subdict("RAS")["RASModel"]) if "RAS" in c["turbulence"] else "laminar"
    # Convection schemes from the case's own system/fvSchemes (owner 2026-11-06: adapt to every dcdtuni case;
    # the dcdtuni presets are bounded upwind or linearUpwind U/T + limitedLinear 1 k/epsilon); div_scheme overrides.
    divs = ({f: (f"bounded {div_scheme}" if not div_scheme.startswith("bounded") else div_scheme)
             for f in ("U", "T", "k", "epsilon")} if div_scheme else _case_div_schemes(c["schemes"]))
    # The HF walls carry alphatJayatillekeWallFunction; the model's wall alphat must follow (realizable_ke.py).
    jay = False
    try:
        _, at = read_foam_file(c["time0"] / "alphat")
        jay = any(isinstance(v, FoamDict) and v.get("type") == "alphatJayatillekeWallFunction"
                  for v in at["boundaryField"].values())
    except FileNotFoundError:
        pass
    turb_section = '[turbulence]\nwallAlphat = "jayatilleke"\n' if jay else ""
    cfg = f"""# Generated by Parse_mesh/dctwin_bc.py from the dctwin HF case {run.name}.
# Physics as the HF solver (buoyantBoussinesqSimpleFoam, dctwin runner): nu, Pr, Prt, beta and g from the case;
# T carried as the excess over TRef = {TRef} K, so TRef here is 0.
[solver]
type = "buoyantboussinesqpicoext"
turbulence = "{model}"
dim = 3
[theta_pde]
unsteady = false
time_step = 1
rho = {{value_type = "constant", val = 1.0}}
mu = {{value_type = "constant", val = {nu!r}}}
nu = {{value_type = "constant", val = {nu!r}}}
alpha = {{value_type = "constant", val = {nu / Pr!r}}}
Pr = {{value_type = "constant", val = {Pr!r}}}
beta = {{value_type = "constant", val = {beta!r}}}
TRef = {{value_type = "constant", val = 0.0}}
hRef = {{value_type = "constant", val = 0.0}}
g = {{value_type = "constant", val = {gv!r}}}
dt = {{value_type = "constant", val = 1.0}}
L = 1.0
source = {{value_type = "constant", val = 0.0}}
[transport]
Prt = {Prt!r}
{turb_section}[fvSchemes]
ddtSchemes = "steadyState"
# From the case's system/fvSchemes divSchemes.
divSchemes = {{U = "{divs['U']}", T = "{divs['T']}", k = "{divs['k']}", epsilon = "{divs['epsilon']}"}}
laplacianSchemes = "{lap_scheme}"
gradSchemes = "{grad_scheme}"
snGradSchemes = "corrected"
interpolationSchemes = "Linear corrected"
gradInterpolationSchemes = "Linear overRelaxed"
boundaryDiffusionScheme = "snGrad"
momentumResidual = "cell"
momentumInterpolation = "MWI"
faceCvAvg = "half"
[fvSolution]
inner_iter = 1
pRefCell = -1
pRefValue = 0
loss_cont = 1
loss_mom = 1
loss_energy = 1
loss_turb = 1
loss_phi_jump = 0
loss_pRef = 0
loss_mom_adaptive_balance = false
[stencil]
khops = 1
"""
    (out / "fvconfig.toml").write_text(cfg)
    # the fixed temperatures of the case as excess over TRef (supplies, fixed-temperature objects): with the
    # servers' rises they bound the temperatures a solve may reach (the caller's guard box)
    fixed = [float(np.max(np.atleast_1d(t["value"]["val"]))) for t in ent["T"].values() if t["type"] == "fixedValue"]
    fixed += [float(np.min(np.atleast_1d(t["value"]["val"]))) for t in ent["T"].values() if t["type"] == "fixedValue"]
    summary = dict(run=str(run), TRef=TRef, nu=nu, Pr=Pr, Prt=Prt, beta=beta, g=gv, model=model,
                   declared_it_heat_W=declared_it_heat(servers),
                   T_fixed_excess_K=[min(fixed, default=0.0), max(fixed, default=0.0)],
                   roles={r: sum(1 for x in roles.values() if x == r) for r in sorted(set(roles.values()))},
                   acus=acus, servers=servers, deviations=deviations,
                   hf_schemes={k: dict(v) for k, v in c["schemes"].items() if isinstance(v, FoamDict)})
    (out / "dctwin_map.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def declared_it_heat(servers: dict, rho: float = 1.19, cp: float = 1005.0) -> float:
    """The declared IT heat (W) of a dctwin design: sum over servers of rho cp Q deltaT (rho 1.19 kg/m3, cp 1005 J/kg K,
    dctwin's constants; ``servers`` as written to ``dctwin_map.json``).  The stop rule's heat closure reads it
    (registration section 4c)."""
    return float(sum(rho * cp * float(s["Q"]) * float(s["deltaT"]) for s in servers.values()))


def patch_areas_from_ranks(run: str | Path) -> dict[str, float]:
    """Total area per physical patch, summed over the ranks (each rank's polyMesh read independently)."""
    from concurrent.futures import ThreadPoolExecutor
    from Parse_mesh.foamio import read_polymesh

    run = Path(run)
    ranks = sorted(run.glob("processor[0-9]*"), key=lambda p: int(p.name[9:])) or [run]

    def one(rank: Path):
        m = read_polymesh(rank)
        return {p.name: float(m.magSf[p.slice].sum()) for p in m.patches if p.type != "processor"}

    with ThreadPoolExecutor(max_workers=min(12, len(ranks))) as ex:
        parts = list(ex.map(one, ranks))
    out: dict[str, float] = {}
    for d in parts:
        for k, v in d.items():
            out[k] = out.get(k, 0.0) + v
    return out


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="dctwin run directory (serial or with processorN/)")
    ap.add_argument("--out", required=True, help="case directory receiving bc/, fvconfig.toml, dctwin_map.json")
    ap.add_argument("--fan-inlet", choices=("uniform", "openfoam"), default="openfoam",
                    help="openfoam (default since 2026-11-06): flowRateOutletVelocity as the HF writes it; uniform: rackInletVelocity suction Q/A (local, a recorded deviation)")
    ap.add_argument("--grad-scheme", default="1st",
                    help="this repository's collocated Newton contract requires a least-squares gradient (\"1st\"); "
                         "the HF case uses Gauss linear (recorded as a deviation)")
    args = ap.parse_args()
    t0 = time.time()
    areas = patch_areas_from_ranks(args.run)
    t1 = time.time()
    s = translate(args.run, args.out, areas, fan_inlet=args.fan_inlet, grad_scheme=args.grad_scheme)
    print(f"areas {t1 - t0:.1f}s, translate {time.time() - t1:.1f}s; roles {s['roles']}")
    print(f"{len(s['servers'])} server outlets, ACUs {sorted(s['acus'])}")
