#!/usr/bin/env python3
"""Comprehensive cfdproj diff — dc-score compatible continuous deduction scoring.

All dimensions use max(0, 10 - error × scale) style, consistent with score_case.py.
"""

import json, math, sys, os, glob, pathlib
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src/frontend/scripts"))
from cfdproj_migrate import load_cfdproj

DEMO = Path(__file__).parent


def _room(d):
    """v3 meta.room (always a dict)."""
    return (d.get("meta", {}) or {}).get("room", {}) or {}


def _polygon(d):
    """v3 meta.room.polygon, normalised to a dict ({} when None)."""
    return _room(d).get("polygon") or {}


def _sim_flat(d):
    """Flat cfdRoom-equivalent: simulation merged with the two keys
    (height, roomTemperature) that v3 relocates to meta.room."""
    flat = dict(d.get("simulation", {}) or {})
    room = _room(d)
    if room.get("height") is not None:
        flat["height"] = room["height"]
    if room.get("temperature") is not None:
        flat["roomTemperature"] = room["temperature"]
    return flat

def quat_to_yaw(q):
    x, y, z, w = q.get('x',0), q.get('y',0), q.get('z',0), q.get('w',1)
    return round(2 * math.atan2(y, w) * 180 / math.pi, 1)

def model_pos(m):
    a = m.get('anchor', {})
    return (a.get('x',0), a.get('y',0), a.get('z',0))

def match_models(label_models, gen_models, dx=0, dz=0, threshold=0.5):
    matched, used = [], set()
    for lm in label_models:
        best_i, best_d = None, 999
        for gi, gm in enumerate(gen_models):
            if gi in used or gm['kind'] != lm['kind']:
                continue
            la = lm.get('anchor', {})
            ga = gm.get('anchor', {})
            gx, gz = ga.get('x',0)+dx, ga.get('z',0)+dz
            d = math.sqrt((gx-la.get('x',0))**2 + (ga.get('y',0)-la.get('y',0))**2 + (gz-la.get('z',0))**2)
            if d < best_d:
                best_i, best_d = gi, d
        if best_i is not None and best_d < threshold:
            matched.append((lm, gen_models[best_i], best_d))
            used.add(best_i)
    missing = [lm for i, lm in enumerate(label_models) if not any(lm is p[0] for p in matched)]
    extra = [gm for gi, gm in enumerate(gen_models) if gi not in used]
    return matched, missing, extra


def score_physical_condition(label, gen, matched_pairs):
    """Room geometry + BC physics. Each sub-item 10 pts, continuous deduction."""
    score, max_score, details = 0, 0, []

    # --- Room polygon (10 pts) ---
    max_score += 10
    lv = _polygon(label).get('vertices', [])
    gv = _polygon(gen).get('vertices', [])
    if lv and gv and len(lv) == len(gv):
        max_err = max(math.sqrt((a[0]-b[0])**2+(a[1]-b[1])**2)
                      for a, b in zip(sorted(lv), sorted(gv)))
        s = max(0, 10 - max_err * 20)
        score += s
        if s < 9.5:
            details.append(f'polygon: err={max_err:.2f}m (-{10-s:.1f})')
    elif lv and gv:
        details.append(f'polygon: vertex count mismatch ({len(lv)} vs {len(gv)})')

    # --- Room height (10 pts) ---
    max_score += 10
    lh = _polygon(label).get('height',
         _room(label).get('height', 0))
    gh = _polygon(gen).get('height',
         _room(gen).get('height', 0))
    if lh and gh:
        s = max(0, 10 - abs(lh - gh) * 20)
        score += s
        if s < 9.5:
            details.append(f'height: L={lh:.2f} G={gh:.2f} (-{10-s:.1f})')

    # --- Per matched ACU: temp(10) + flowRate(10) + velocity(10) = 30 pts ---
    for lm, gm, dist in matched_pairs:
        if lm['kind'] != 'acu':
            continue
        name = lm.get('name', 'ACU')
        max_score += 30
        lpp = lm.get('cfd', {}).get('patchPhysics', {})
        gpp = gm.get('cfd', {}).get('patchPhysics', {})
        ls = next((v for v in lpp.values() if v.get('role') == 'supply'), {})
        gs = next((v for v in gpp.values() if v.get('role') == 'supply'), {})

        # Temperature (10 pts): ±5°C → 0
        lt, gt = ls.get('temperature'), gs.get('temperature')
        if lt is not None and gt is not None:
            s = max(0, 10 - abs(lt - gt) * 2)
            score += s
            if s < 9.5:
                details.append(f'{name}: temp L={lt} G={gt} (-{10-s:.1f})')
        else:
            details.append(f'{name}: temp MISSING')

        # Flow rate (10 pts): ±100% relative → 0
        lf, gf = ls.get('volumeFlowRate'), gs.get('volumeFlowRate')
        if lf and gf and lf > 0:
            s = max(0, 10 - abs(lf - gf) / lf * 10)
            score += s
            if s < 9.5:
                details.append(f'{name}: flow L={lf} G={gf} (-{10-s:.1f})')
        elif gf is not None:
            score += 5
        else:
            details.append(f'{name}: flow MISSING')

        # Velocity (10 pts): ±100% relative → 0
        lvel, gvel = ls.get('velocity'), gs.get('velocity')
        if lvel and gvel and lvel > 0:
            s = max(0, 10 - abs(lvel - gvel) / lvel * 10)
            score += s
            if s < 9.5:
                details.append(f'{name}: vel L={lvel:.2f} G={gvel} (-{10-s:.1f})')
        elif gvel is not None:
            score += 5
        else:
            details.append(f'{name}: vel MISSING')

    # --- Per matched rack: equip_count(10) + power(10) = 20 pts ---
    for lm, gm, dist in matched_pairs:
        if lm['kind'] != 'rack':
            continue
        name = lm.get('name', 'Rack')
        max_score += 20
        leq = lm.get('rack', {}).get('equipments', [])
        geq = gm.get('rack', {}).get('equipments', [])

        # Equipment count (10 pts)
        s = max(0, 10 - abs(len(leq) - len(geq)) * 3)
        score += s
        if s < 9.5:
            details.append(f'{name}: equip {len(geq)}/{len(leq)} (-{10-s:.1f})')

        # Power (10 pts): relative error
        lp = sum(e.get('power_W', 0) or 0 for e in leq)
        gp = sum(e.get('power_W', 0) or 0 for e in geq)
        if lp > 0 and gp > 0:
            s = max(0, 10 - abs(lp - gp) / lp * 10)
            score += s
            if s < 9.5:
                details.append(f'{name}: power L={lp} G={gp} (-{10-s:.1f})')
        elif gp > 0:
            score += 5
        elif lp > 0:
            details.append(f'{name}: power MISSING')
        else:
            score += 10

    return score, max_score, details


def score_mesh_config(label, gen):
    """Mesh parameters — 4 items × 10 pts = 40 pts max."""
    score, max_score, details = 0, 0, []
    lr = _sim_flat(label)
    gr = _sim_flat(gen)

    # meshBaseSize (10 pts): ±50% relative → 0
    max_score += 10
    lv, gv = lr.get('meshBaseSize', 0), gr.get('meshBaseSize', 0)
    if lv and gv:
        s = max(0, 10 - abs(lv - gv) / lv * 20)
        score += s
        if s < 9.5:
            details.append(f'meshBaseSize: L={lv} G={gv} (-{10-s:.1f})')

    # maxGlobalCells (10 pts): log2 ratio, ±3 octaves → 0
    max_score += 10
    lv, gv = lr.get('maxGlobalCells', 0), gr.get('maxGlobalCells', 0)
    if lv and gv:
        ratio = math.log2(max(gv, 1) / max(lv, 1))
        s = max(0, 10 - abs(ratio) * 3.3)
        score += s
        if s < 9.5:
            details.append(f'maxCells: L={lv} G={gv} (-{10-s:.1f})')

    # refinementLevel (10 pts): exact=10, partial=5
    max_score += 10
    ref_keys = ['refinementLevel', 'rackRefinementLevel']
    match_count = sum(1 for k in ref_keys if lr.get(k) == gr.get(k))
    s = 10 * match_count / len(ref_keys)
    score += s
    if s < 9.5:
        details.append(f'refinement: {match_count}/{len(ref_keys)} match (-{10-s:.1f})')

    # parallel config (10 pts)
    max_score += 10
    s = 0
    if lr.get('parallelSolve') == gr.get('parallelSolve', True):
        s += 5
    if lr.get('solveProcess', 8) == gr.get('solveProcess', 8):
        s += 5
    score += s
    if s < 9.5:
        details.append(f'parallel: (-{10-s:.1f})')

    return score, max_score, details


def score_solver_config(label, gen):
    """Solver parameters — 4 items × 10 pts = 40 pts max."""
    score, max_score, details = 0, 0, []
    lr = _sim_flat(label)
    gr = _sim_flat(gen)

    # solverType (10 pts): exact match
    max_score += 10
    if lr.get('solverType') == gr.get('solverType'):
        score += 10
    else:
        details.append(f'solverType: L={lr.get("solverType")} G={gr.get("solverType")} (-10)')

    # endTime (10 pts): relative error
    max_score += 10
    le, ge = lr.get('endTime', 0), gr.get('endTime', 0)
    if le and ge:
        s = max(0, 10 - abs(le - ge) / max(le, 1) * 10)
        score += s
        if s < 9.5:
            details.append(f'endTime: L={le} G={ge} (-{10-s:.1f})')

    # roomTemperature (10 pts): ±5°C → 0
    max_score += 10
    lt, gt = lr.get('roomTemperature'), gr.get('roomTemperature')
    if lt is not None and gt is not None:
        s = max(0, 10 - abs(lt - gt) * 2)
        score += s
        if s < 9.5:
            details.append(f'roomTemp: L={lt} G={gt} (-{10-s:.1f})')

    # schemes (10 pts): fraction of matching entries
    max_score += 10
    ls, gs = lr.get('schemes', {}), gr.get('schemes', {})
    if ls and gs:
        common = set(ls.keys()) & set(gs.keys())
        if common:
            frac = sum(1 for k in common if ls[k] == gs[k]) / len(common)
            s = 10 * frac
            score += s
            if s < 9.5:
                details.append(f'schemes: {frac:.0%} match (-{10-s:.1f})')
    elif gs:
        score += 5

    return score, max_score, details


def diff_case(case_dir, gen_suffix=''):
    case_name = os.path.basename(case_dir)
    # Use the exact case file, not a glob — workdir may also hold variant
    # exports (e.g. <case>.image_only.cfdproj) that must not be scored here.
    # gen_suffix='' scores the current (exact) build; '.gridbuild_backup' scores
    # the preserved coarse 0.5 m-grid build.
    label_file = Path(case_dir) / 'label' / f'{case_name}.cfdproj'
    gen_file = Path(case_dir) / 'workdir' / f'{case_name}.cfdproj{gen_suffix}'
    if not label_file.exists() or not gen_file.exists():
        return None
    label = load_cfdproj(label_file)
    gen = load_cfdproj(gen_file)
    result = {'case': os.path.basename(case_dir)}

    # Polygon centroid offset (same as score_case.py)
    gp = _polygon(gen).get('vertices', [])
    lp = _polygon(label).get('vertices', [])
    dx, dz = 0.0, 0.0
    if gp and lp and len(gp) == len(lp):
        gcx = sum(v[0] for v in gp) / len(gp)
        gcz = sum(v[1] for v in gp) / len(gp)
        lcx = sum(v[0] for v in lp) / len(lp)
        lcz = sum(v[1] for v in lp) / len(lp)
        dx, dz = lcx - gcx, lcz - gcz
        if abs(dx) < 0.1 and abs(dz) < 0.1:
            dx, dz = 0.0, 0.0

    lmodels = [m for m in label.get('construction', []) if m.get('enabled', True)]
    gmodels = [m for m in gen.get('construction', []) if m.get('enabled', True)]
    matched, missing, extra = match_models(lmodels, gmodels, dx, dz)
    result['model_count'] = {
        'label': len(lmodels), 'gen': len(gmodels),
        'matched': len(matched), 'missing': len(missing), 'extra': len(extra),
    }

    # Physical condition
    ps, pm, pd = score_physical_condition(label, gen, matched)
    result['physical_condition'] = {
        'score': round(ps, 1), 'max': pm,
        'pct': round(100 * ps / pm, 1) if pm else 0, 'details': pd}

    # Mesh config
    ms, mm, md = score_mesh_config(label, gen)
    result['mesh_config'] = {
        'score': round(ms, 1), 'max': mm,
        'pct': round(100 * ms / mm, 1) if mm else 0, 'details': md}

    # Solver config
    ss, sm, sd = score_solver_config(label, gen)
    result['solver_config'] = {
        'score': round(ss, 1), 'max': sm,
        'pct': round(100 * ss / sm, 1) if sm else 0, 'details': sd}

    return result


def main(out_name='cfdproj_diff_results.json', gen_suffix=''):
    cases = sorted(DEMO.glob('case_*'))
    all_results = []
    for case_dir in cases:
        r = diff_case(str(case_dir), gen_suffix=gen_suffix)
        if r:
            all_results.append(r)

    print(f"{'Case':<25s} {'Match':>7s} {'PhysCond':>9s} {'Mesh':>6s} {'Solver':>7s}")
    print('-' * 60)
    for r in all_results:
        mc = r['model_count']
        print(f"{r['case']:<25s} "
              f"{mc['matched']:>3d}/{mc['label']:<3d}  "
              f"{r['physical_condition']['pct']:7.1f}%  "
              f"{r['mesh_config']['pct']:5.1f}%  "
              f"{r['solver_config']['pct']:5.1f}%")

    out_path = DEMO / out_name
    json.dump(all_results, open(out_path, 'w'), indent=2)
    print(f'\nSaved: {out_path}')


if __name__ == '__main__':
    if '--coarse' in sys.argv:
        # Multi-dimensional breakdown of the coarse 0.5 m-grid build.
        main('cfdproj_diff_coarse.json', '.gridbuild_backup')
    else:
        main()
