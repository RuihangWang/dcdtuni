#!/usr/bin/env python3
"""Synthesise an OpenFOAM ``0/`` boundary specification from UI parameters.

This is step S4 of the chain  UI sliders -> 0/ files -> toml codebook -> h5 text.

Why TEMPLATE SUBSTITUTION and not generation
--------------------------------------------
The frozen MiniLM encoder saw exactly the strings the training pipeline produced. Any
drift in patch ordering, role keywords, or number formatting moves the model's input off
distribution **without raising an error** — the prediction just quietly degrades. So we
never regenerate a ``0/`` file from scratch: we read the base case's own file and
substitute only the numbers we mean to change. Everything we do not touch stays
byte-identical by construction.

What carries each physical quantity (verified against case0)
------------------------------------------------------------
``0/T``  server_outlet_<slot>:
    valueExpr  "tSink_<slot>+<deltaT>"            <- the ONLY power channel
``0/T``  acu_supply_<acu>:
    "supplyAirMassFlowRate = <mdot>" , "t2 = <setpointK>" , "coolingCapacity = 250.0"
``0/U``  server_outlet_<slot> / server_inlet_<slot>:
    volumetricFlowRate  <Q>
``0/U``  acu_supply_<acu> / acu_return_<acu>:
    volumetricFlowRate  <Q>

Physics (calibrated, not assumed):  deltaT = P / (RHO_CP * Q).
RHO_CP=1197.14 was obtained two independent ways: regressing all 317 server valueExpr
literals in case0 against boundary_conditions.json, and reading the ACU expression in
case0/0/T which spells out ``coolingCapacity * 1000 / (supplyAirMassFlowRate * 1006)``
with mdot = Q*1.19 — i.e. cp=1006, rho=1.19, rho*cp=1197.14.

Note the source files store flow at 6 significant digits already (``0.100447``) while
deltaT is stored at full double repr (``14.394536352492217``). We reproduce both.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from typing import Dict, Tuple

RHO_CP = 1197.14          # rho * cp  [J/(m^3 K)]
RHO_AIR = 1.19            # kg/m^3
CP_AIR = 1006.0           # J/(kg K) — as written in the ACU expression


# ── generic OpenFOAM block helpers ────────────────────────────────────────────
def _find_block(text: str, patch: str) -> Tuple[int, int] | None:
    """Return (start, end) covering the balanced ``{...}`` body of ``patch``.

    Patch names are matched on a word boundary so ``server_inlet_x_slot_1`` does not
    match ``server_inlet_x_slot_10``.
    """
    for m in re.finditer(r"(?<![\w])" + re.escape(patch) + r"(?![\w])", text):
        brace = text.find("{", m.end())
        if brace == -1:
            continue
        # Only accept if nothing but whitespace separates the name and the brace.
        if text[m.end():brace].strip():
            continue
        depth, i = 0, brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return brace, i + 1
            i += 1
    return None


def _sub_in_block(text: str, patch: str, pattern: str, repl_fn) -> str:
    """Apply a regex substitution ONLY inside ``patch``'s block."""
    span = _find_block(text, patch)
    if span is None:
        return text
    s, e = span
    body = text[s:e]
    new_body, n = re.subn(pattern, repl_fn, body, count=1)
    if n == 0:
        return text
    return text[:s] + new_body + text[e:]


def fmt_full(v: float) -> str:
    """Full double repr, as the source files store deltaT and supplyAirMassFlowRate."""
    return repr(float(v))


def q6(v: float) -> float:
    """The flow rate ROUNDED TO 6 DECIMALS, which is what the source files store and —
    crucially — what the original generator fed into the deltaT and mdot arithmetic.
    Using the raw value instead shifts deltaT in the 6th significant digit, which is
    exactly where the serialized text is truncated, so it would change the tokens."""
    return float(f"{float(v):.6f}")


def fmt_flow(v: float) -> str:
    """``volumetricFlowRate``: 6 decimal places with trailing zeros stripped
    (``5.109704``, ``0.02512``). Verified against all 317 server + 6 ACU patches."""
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def fmt_setpoint(v: float) -> str:
    """ACU ``t2``: 2 decimals (``295.91``). Verified on all 6 CRACs."""
    return f"{float(v):.2f}"


def delta_t(power_w: float, flow_m3s: float) -> float:
    """Server temperature rise. The association order is not cosmetic: only
    ``P/(q6*rho*cp)`` reproduces all 317 stored literals bit-for-bit (other orderings
    match 89-211/317). All orderings agree once truncated to the 6 significant digits the
    text carries, but we keep the exact form so regenerated cases stay byte-identical."""
    return float(power_w) / (q6(flow_m3s) * RHO_AIR * CP_AIR)


def mass_flow(flow_m3s: float) -> float:
    """ACU supply mass flow — computed from the ROUNDED volumetric flow (6/6 verified)."""
    return q6(flow_m3s) * RHO_AIR


# ── the substitutions ─────────────────────────────────────────────────────────
def set_server_delta_t(t_text: str, slot: str, delta_t: float) -> str:
    """Rewrite ``valueExpr "tSink_<slot>+<deltaT>"`` for one server slot."""
    pat = r'(valueExpr\s+"tSink_' + re.escape(slot) + r'\+)([-\d.eE+]+)(")'
    return _sub_in_block(
        t_text, f"server_outlet_{slot}", pat,
        lambda m: m.group(1) + fmt_full(delta_t) + m.group(3))


def set_flow(u_text: str, patch: str, q: float) -> str:
    """Rewrite ``volumetricFlowRate <Q>`` inside one patch block."""
    return _sub_in_block(
        u_text, patch, r"(volumetricFlowRate\s+)([-\d.eE+]+)",
        lambda m: m.group(1) + fmt_flow(q))


def set_acu_thermal(t_text: str, acu: str, mdot: float, setpoint_k: float) -> str:
    """Rewrite the ACU supply's mass flow and setpoint variables."""
    out = _sub_in_block(
        t_text, f"acu_supply_{acu}",
        r'("supplyAirMassFlowRate\s*=\s*)([-\d.eE+]+)(")',
        lambda m: m.group(1) + fmt_full(mdot) + m.group(3))
    out = _sub_in_block(
        out, f"acu_supply_{acu}",
        r'("t2\s*=\s*)([-\d.eE+]+)(")',
        lambda m: m.group(1) + fmt_setpoint(setpoint_k) + m.group(3))
    return out


# ── driver ────────────────────────────────────────────────────────────────────
def apply_bc(base_case: str, out_case: str, bc: Dict, link_heavy: bool = True) -> str:
    """Materialise ``out_case`` from ``base_case`` with ``bc`` applied.

    ``bc`` mirrors the dataset's own boundary_conditions.json:
        server_powers                  {slot: W}
        server_volume_flow_rates       {slot: m3/s}
        supply_air_temperatures        {acu: degC}
        supply_air_volume_flow_rates   {acu: m3/s}

    ``constant/`` and ``system/`` are SYMLINKED by default — they are large, identical
    for every case (all 753 share one mesh) and never depend on the BC.
    """
    # Copy the WHOLE 0/ directory first, then overwrite only the fields we change.
    # The text-conditioned model only ever reads T/U/p_rgh, but the numerical arm needs a
    # real OpenFOAM step to produce its t=1 boundary field, and the solver refuses to
    # start without every field it was configured for — it died on a missing 0/nut.
    # Copying first also means any field we do not touch stays byte-identical.
    src0, dst0 = os.path.join(base_case, "0"), os.path.join(out_case, "0")
    if not os.path.isdir(dst0):
        shutil.copytree(src0, dst0)
    else:
        for fn in os.listdir(src0):
            s, d = os.path.join(src0, fn), os.path.join(dst0, fn)
            if os.path.isfile(s) and not os.path.exists(d):
                shutil.copyfile(s, d)
    # constant/ holds the polyMesh (hundreds of MB, identical for all 753 cases) and is
    # only ever READ — safe to symlink. system/ must be COPIED: the 1-step solver in
    # inference.py rewrites system/controlDict and system/fvSolution and restores them
    # afterwards, so a symlink points those writes straight at the shared read-only
    # dataset under /mnt/lv/home/Public/GTC. That actually happened here: case0's
    # controlDict and fvSolution were rewritten through the link (content was restored
    # correctly, but a crash mid-run would have left the shared dataset broken for every
    # other user). system/ is under 1 MB, so copying costs nothing.
    src, dst = os.path.join(base_case, "constant"), os.path.join(out_case, "constant")
    if not os.path.lexists(dst):
        os.symlink(src, dst) if link_heavy else shutil.copytree(src, dst)
    src, dst = os.path.join(base_case, "system"), os.path.join(out_case, "system")
    if not os.path.lexists(dst):
        shutil.copytree(src, dst)

    # Top-level marker files. `case.foam` is what the h5 writer uses to locate the case
    # after a solver run — without it the OpenFOAM step succeeds and then the t=1 write
    # back into the h5 fails with "No .foam file found", losing the whole solve.
    for fn in os.listdir(base_case):
        s = os.path.join(base_case, fn)
        if os.path.isfile(s) and (fn.endswith(".foam") or fn.endswith(".json")):
            d = os.path.join(out_case, fn)
            if not os.path.exists(d):
                shutil.copyfile(s, d)

    t_text = open(os.path.join(base_case, "0", "T")).read()
    u_text = open(os.path.join(base_case, "0", "U")).read()

    # `bc` is an OVERLAY on the base operating point, not a complete specification.
    # Each assignment below needs a PAIR of values (power needs its flow to become a
    # delta-T; an ACU setpoint needs its flow to become a mass flow), and a caller that
    # supplies only one half used to have the whole assignment silently skipped by the
    # `continue` guards. An ACU-temperature-only request therefore wrote a 0/T that was
    # byte-identical to the base case: the solver ran, the model ran, and the prediction
    # came back unchanged with nothing anywhere reporting a problem. Fill the gaps from
    # the base case so a partial request means "change these, keep the rest".
    base_bc = {}
    _bp = os.path.join(base_case, "boundary_conditions.json")
    if os.path.exists(_bp):
        with open(_bp) as _f:
            base_bc = json.load(_f)
    bc = {k: {**base_bc.get(k, {}), **(bc.get(k) or {})}
          for k in ("server_powers", "server_volume_flow_rates",
                    "supply_air_temperatures", "supply_air_volume_flow_rates")}

    powers = bc.get("server_powers", {})
    flows = bc.get("server_volume_flow_rates", {})
    for slot, p in powers.items():
        q = flows.get(slot)
        if not q:
            continue
        t_text = set_server_delta_t(t_text, slot, delta_t(p, q))
    for slot, q in flows.items():
        u_text = set_flow(u_text, f"server_outlet_{slot}", float(q))
        u_text = set_flow(u_text, f"server_inlet_{slot}", float(q))

    acu_T = bc.get("supply_air_temperatures", {})
    acu_Q = bc.get("supply_air_volume_flow_rates", {})
    for acu, tc in acu_T.items():
        q = acu_Q.get(acu)
        if q is None:
            continue
        t_text = set_acu_thermal(t_text, acu, mass_flow(q), float(tc) + 273.15)
    for acu, q in acu_Q.items():
        u_text = set_flow(u_text, f"acu_supply_{acu}", float(q))
        u_text = set_flow(u_text, f"acu_return_{acu}", float(q))

    open(os.path.join(out_case, "0", "T"), "w").write(t_text)
    open(os.path.join(out_case, "0", "U"), "w").write(u_text)
    # p_rgh is BC-independent (fixedFluxPressure everywhere) but the codebook reads it.
    shutil.copyfile(os.path.join(base_case, "0", "p_rgh"),
                    os.path.join(out_case, "0", "p_rgh"))
    with open(os.path.join(out_case, "boundary_conditions.json"), "w") as f:
        json.dump(bc, f, indent=2)
    return out_case


def scale_bc(bc: Dict, power_scale: float = 1.0, flow_scale: float = 1.0,
             acu_delta_c: float = 0.0, acu_set_c: float | None = None,
             acu_flow_scale: float = 1.0) -> Dict:
    """Perturb the BC — used by the sensitivity probes.

    Separate knobs matter: server power reaches the model only through
    ``temperatureDelta_K``, whereas the ACU supply temperature reaches it through
    ``supplySetpoint_K``. Probing them independently tells us whether the encoder ignores
    ALL numeric tokens or only that one.
    """
    out = json.loads(json.dumps(bc))
    if power_scale != 1.0:
        out["server_powers"] = {k: v * power_scale for k, v in bc["server_powers"].items()}
    if flow_scale != 1.0:
        out["server_volume_flow_rates"] = {
            k: v * flow_scale for k, v in bc["server_volume_flow_rates"].items()}
    if acu_set_c is not None:
        out["supply_air_temperatures"] = {
            k: float(acu_set_c) for k in bc.get("supply_air_temperatures", {})}
    elif acu_delta_c:
        out["supply_air_temperatures"] = {
            k: v + acu_delta_c for k, v in bc.get("supply_air_temperatures", {}).items()}
    if acu_flow_scale != 1.0:
        out["supply_air_volume_flow_rates"] = {
            k: v * acu_flow_scale
            for k, v in bc.get("supply_air_volume_flow_rates", {}).items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-case", required=True)
    ap.add_argument("--out-case", required=True)
    ap.add_argument("--bc-json", help="BC dict; defaults to the base case's own")
    ap.add_argument("--power-scale", type=float, default=1.0)
    ap.add_argument("--flow-scale", type=float, default=1.0)
    # ACU knobs: the supply set-point drives the room air temperature directly, so it
    # is a far stronger lever than scaling every server's power uniformly.
    ap.add_argument("--acu-set-c", type=float, default=None)
    ap.add_argument("--acu-delta-c", type=float, default=0.0)
    ap.add_argument("--acu-flow-scale", type=float, default=1.0)
    ap.add_argument("--selfcheck", action="store_true",
                    help="regenerate the base case's own 0/T and 0/U and diff them")
    args = ap.parse_args()

    bc_path = args.bc_json or os.path.join(args.base_case, "boundary_conditions.json")
    with open(bc_path) as f:
        bc = json.load(f)
    bc = scale_bc(bc, args.power_scale, args.flow_scale,
                  acu_delta_c=args.acu_delta_c, acu_set_c=args.acu_set_c,
                  acu_flow_scale=args.acu_flow_scale)

    apply_bc(args.base_case, args.out_case, bc)
    print(f"wrote {args.out_case}/0/{{T,U,p_rgh}}")

    if args.selfcheck:
        ok = True
        for fld in ("T", "U"):
            a = open(os.path.join(args.base_case, "0", fld)).read()
            b = open(os.path.join(args.out_case, "0", fld)).read()
            same = a == b
            ok &= same
            print(f"  0/{fld}: {'IDENTICAL' if same else 'DIFFERS'} "
                  f"({len(a)} vs {len(b)} bytes)")
            if not same:
                import difflib
                d = [l for l in difflib.unified_diff(
                    a.splitlines(), b.splitlines(), lineterm="", n=0)][:20]
                print("\n".join("    " + x for x in d))
        print("SELFCHECK", "PASS" if ok else "FAIL")
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
