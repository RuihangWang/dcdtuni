#!/usr/bin/env python3
"""Score a generated cfdproj against a label cfdproj.

Usage: python score_case.py <case_name> [case_name2 ...] | all

``all`` scores every ``case_*`` directory; a case whose ``workdir/<case>.cfdproj``
(or label) does not exist yet is skipped with its reason, not a crash.
"""
import json, math, sys, pathlib
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src/frontend/scripts"))
from cfdproj_migrate import _V4_BUCKET_BY_KIND, load_cfdproj_v4

DEMO = Path(__file__).parent

# bucket -> kind, the reverse of the schema's BUCKET_BY_KIND. Several kinds share
# the `boxes` bucket (heat_source, crh, cold_plate all live there); a scoring view
# only needs to tell a box from a rack, so the first mapping wins.
_KIND_BY_BUCKET = {}
for _kind, _bucket in _V4_BUCKET_BY_KIND.items():
    _KIND_BY_BUCKET.setdefault(_bucket, _kind)


def _patch_count(faces):
    if not isinstance(faces, dict):
        return 0
    return sum(len((f or {}).get("patches") or []) for f in faces.values())


def scoring_view(doc):
    """One flat record per model, in the shape the scoring maths below expects.

    Both sides go through this. The scorer used to read a v3 document directly —
    `construction[]`, `.anchor`, `.base`, `.transform.quaternion`, `.faces`,
    `.rack.equipments` — and every one of those names moved in V4 (b821e27):
    models are `constructions.<bucket>` keyed by uid, with `meta.name` and
    `geometry.location/size/quaternion/faces`, and a rack's servers are
    `constructions.servers`. `kind` is not stored at all any more; the bucket
    carries it.

    So a V4 file fed to the old reader yielded ZERO models and a score of zero,
    with no error. Labels are still v3 on disk, which is why nobody noticed:
    both sides were v3 and both migrated the same way.

    Sizes come back as W/D/H because that is what the deduction messages say;
    V4 stores x/y/z with W->x, H->y, D->z.
    """
    out = []
    for bucket, items in (doc.get("constructions") or {}).items():
        kind = _KIND_BY_BUCKET.get(bucket, bucket)
        for uid, m in (items or {}).items():
            if not isinstance(m, dict):
                continue
            g = m.get("geometry") or {}
            size = g.get("size") or {}
            out.append({
                "uid": uid,
                "kind": kind,
                "name": (m.get("meta") or {}).get("name") or uid,
                "anchor": g.get("location") or {},
                "base": {"W": size.get("x", 0), "D": size.get("z", 0), "H": size.get("y", 0)},
                "quaternion": g.get("quaternion") or {},
                "patchCount": _patch_count(g.get("faces")),
                "equipCount": len(((m.get("constructions") or {}).get("servers")) or {}),
            })
    return out


def room_polygon(doc):
    """The room footprint. V4 keeps it at `geometry.plane` as [[x, z], ...]."""
    plane = (doc.get("geometry") or {}).get("plane")
    return plane if isinstance(plane, list) else []

def score_cfdproj(case_name, gen_path=None, out_name="score_result.json"):
    if gen_path is None:
        gen_path = DEMO / case_name / "workdir" / f"{case_name}.cfdproj"
    else:
        gen_path = Path(gen_path)
    label_path = DEMO / case_name / "label" / f"{case_name}.cfdproj"

    if not gen_path.exists():
        return {"case": case_name, "score_pct": 0, "error": "no cfdproj generated"}
    if not label_path.exists():
        return {"case": case_name, "score_pct": 0, "error": "no label"}

    # BOTH sides migrated all the way to V4. Labels are v3 on disk and anything
    # the editor writes today is V4; comparing them in one space is the only way
    # the two are comparable at all.
    gen = load_cfdproj_v4(gen_path)
    label = load_cfdproj_v4(label_path)

    def get_yaw(m):
        q = m.get('quaternion', {})
        return round(2 * math.atan2(q.get('y', 0), q.get('w', 1)) * 180 / math.pi, 1)

    def count_patches(m):
        return m.get('patchCount', 0)

    def count_equip(m):
        return m.get('equipCount', 0)

    # Compute polygon centroid offset for non-origin rooms
    gp = room_polygon(gen)
    lp = room_polygon(label)
    dx, dz = 0.0, 0.0
    if gp and lp and len(gp) == len(lp):
        gcx = sum(v[0] for v in gp) / len(gp)
        gcz = sum(v[1] for v in gp) / len(gp)
        lcx = sum(v[0] for v in lp) / len(lp)
        lcz = sum(v[1] for v in lp) / len(lp)
        dx, dz = lcx - gcx, lcz - gcz
        if abs(dx) < 0.1 and abs(dz) < 0.1:
            dx, dz = 0.0, 0.0

    gm = scoring_view(gen)
    lm = scoring_view(label)

    # Position-based matching with offset compensation
    matched, used = [], set()
    for g in gm:
        best_i, best_d = None, 999
        for i, l in enumerate(lm):
            if i in used or g['kind'] != l['kind']:
                continue
            ga, la = g.get('anchor', {}), l.get('anchor', {})
            gx = ga.get('x', 0) + dx
            gz = ga.get('z', 0) + dz
            d = ((gx - la.get('x', 0))**2 + (ga.get('y', 0) - la.get('y', 0))**2 + (gz - la.get('z', 0))**2)**0.5
            if d < best_d:
                best_i, best_d = i, d
        if best_i is not None and best_d < 0.5:
            matched.append((g, lm[best_i]))
            used.add(best_i)

    total, maxs, deductions = 0, 0, []
    for g, l in matched:
        ga, la = g.get('anchor', {}), l.get('anchor', {})
        gx, gz = ga.get('x', 0) + dx, ga.get('z', 0) + dz
        pos = max(0, 10 - ((gx-la.get('x',0))**2 + (ga.get('y',0)-la.get('y',0))**2 + (gz-la.get('z',0))**2)**0.5 * 20)

        gb, lb = g.get('base', {}), l.get('base', {})
        sz = max(0, 10 - sum((gb.get(k,0)-lb.get(k,0))**2 for k in ('W','D','H'))**0.5 * 20)

        gy, ly = get_yaw(g) % 360, get_yaw(l) % 360
        ye = min(abs(gy - ly), 360 - abs(gy - ly))
        yaw = max(0, 10 - ye / 9)

        eq = 10
        if g['kind'] == 'rack':
            ge, le = count_equip(g), count_equip(l)
            if ge != le:
                eq = max(0, 10 - abs(ge - le) * 3)
        elif g['kind'] in ('acu', 'floor'):
            gp2, lp2 = count_patches(g), count_patches(l)
            if gp2 != lp2:
                eq = max(0, 10 - abs(gp2 - lp2) * 5)

        item = pos + sz + yaw + eq
        if item < 39.5:
            pts = []
            if pos < 9.5: pts.append(f"pos(-{10-pos:.1f})")
            if sz < 9.5: pts.append(f"size(-{10-sz:.1f})")
            if yaw < 9.5: pts.append(f"yaw(-{10-yaw:.1f})")
            if eq < 9.5: pts.append(f"eq(-{10-eq:.1f})")
            deductions.append(f"{g['name']}: {' '.join(pts)}")
        total += item
        maxs += 40

    missing = len(lm) - len(used)
    extra = len(gm) - len(matched)
    # Quality-weighted detection accuracy, bounded to (0, 100]. Each matched pair earns up to
    # 40 quality points (position + size + orientation + equipment). Every unmatched model —
    # missing (false negative) or extra/hallucinated (false positive) — adds 40 to the
    # denominator with zero credit. Extra models thus drive the score toward 0 but can never
    # make it negative, so no clamping is needed and gradation between cases is preserved.
    maxs += (missing + extra) * 40
    final = total
    pct = 100 * final / maxs if maxs > 0 else 0

    result = {
        "case": case_name,
        "score_pct": round(pct, 1),
        "score": round(final, 1),
        "max_score": round(maxs, 1),
        "matched": len(matched),
        "total_label": len(lm),
        "missing": missing,
        "extra": extra,
        "deductions": deductions,
    }
    if abs(dx) > 0.1 or abs(dz) > 0.1:
        result["polygon_offset"] = f"dx={dx:.2f},dz={dz:.2f}"

    out = DEMO / case_name / "workdir" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    return result


def main(argv=None):
    """Score the named cases (or ``all``). A case without a generated project or label is skipped and named."""
    cases = list(sys.argv[1:] if argv is None else argv)
    if not cases:
        print("Usage: python score_case.py <case_name> [case_name2 ...] | all")
        return 1
    if cases == ["all"]:
        cases = sorted(d.name for d in DEMO.iterdir() if d.is_dir() and d.name.startswith("case_"))

    results, skipped = [], []
    for cn in cases:
        r = score_cfdproj(cn)
        if "error" in r:
            skipped.append(r)
            print(f"  {r['case']:22s} skipped: {r['error']}")
            continue
        results.append(r)
        s = "+" if r["score_pct"] >= 85 else "-"
        print(f"{s} {r['case']:22s} {r['score_pct']:6.1f}% ({r['matched']}/{r['total_label']}) "
              f"miss={r.get('missing',0)} extra={r.get('extra',0)}")
        for d in r.get("deductions", []):
            print(f"    {d}")

    if len(results) > 1:
        avg = sum(r["score_pct"] for r in results) / len(results)
        good = sum(1 for r in results if r["score_pct"] >= 85)
        print(f"\nAverage: {avg:.1f}%  Good(≥85%): {good}/{len(results)}")
    if skipped:
        print(f"skipped {len(skipped)} case(s) without a generated project or a label: "
              + ", ".join(r["case"] for r in skipped))
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
