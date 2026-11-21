"""Python twin of shared/js/cfdproj_transient.js (cfdproj v5 transient keyframes).

Design: docs/developer/transient_keyframes_design_2026-09-30.md (§1). Same
function set in snake_case; test_cfdproj_transient_parity.py runs the JS module
under node on every case and compares. Keep the two in step: a change here is
a change there.
"""
from __future__ import annotations

import copy
import json
import math
import pathlib
import re

EPS = 1e-9
_REGISTRY_PATH = pathlib.Path(__file__).resolve().parents[1] / "shared" / "transient" / "transient_params.json"
_REGISTRY: dict | None = None


def registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    return _REGISTRY


def entry_of(key):
    return next((e for e in registry()["params"] if e["key"] == key), None)


# --- path templates -------------------------------------------------------------

_VAR = re.compile(r"^\{(\w+)\}$")
_IDSEL = re.compile(r"^(\w+)\[id=\{(\w+)\}\]$")


def _parse(path):
    segs = []
    for seg in path.split("."):
        m = _VAR.match(seg)
        if m:
            segs.append(("var", m.group(1)))
            continue
        m = _IDSEL.match(seg)
        if m:
            segs.append(("idsel", m.group(1), m.group(2)))
            continue
        segs.append(("any",) if seg == "*" else ("key", seg))
    return segs


def _fmt(s):
    if s[0] == "key":
        return s[1]
    if s[0] == "var":
        return "{%s}" % s[1]
    if s[0] == "any":
        return "*"
    return "%s[id={%s}]" % (s[1], s[2])


def _dig(obj, dotted):
    for k in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _matches(obj, where):
    if not where:
        return True
    for k, want in where.items():
        v = _dig(obj, k)
        if isinstance(want, list):
            if v not in want:
                return False
        elif v != want:
            return False
    return True


def _is_obj(o):
    return isinstance(o, (dict, list))


def _keys(obj):
    return list(range(len(obj))) if isinstance(obj, list) else list(obj.keys())


def _get(obj, k):
    if isinstance(obj, list):
        return obj[k] if isinstance(k, int) and 0 <= k < len(obj) else None
    return obj.get(k)


def _walk(obj, segs, i, bindings, selected, where, leaf):
    if not _is_obj(obj):
        return
    s = segs[i]
    last = i == len(segs) - 1
    if s[0] == "key":
        if last:
            if isinstance(obj, dict) and _matches(selected, where):
                leaf(obj, s[1], bindings)
            return
        _walk(_get(obj, s[1]) if isinstance(obj, dict) else None, segs, i + 1, bindings, selected, where, leaf)
    elif s[0] == "any":
        for k in _keys(obj):
            _walk(_get(obj, k), segs, i + 1, bindings, selected, where, leaf)
    elif s[0] == "var":
        for k in _keys(obj):
            child = _get(obj, k)
            _walk(child, segs, i + 1, bindings + [str(k)], child, where, leaf)
    else:
        arr = obj.get(s[1]) if isinstance(obj, dict) else None
        if not isinstance(arr, list):
            return
        for el in arr:
            if isinstance(el, dict) and el.get("id") is not None:
                _walk(el, segs, i + 1, bindings + [str(el["id"])], el, where, leaf)


def enumerate_targets(model, entry):
    out = []
    segs = _parse(entry["path"])

    def leaf(parent, key, bindings):
        value = parent.get(key)
        if value is not None:
            out.append({"target": "/".join(bindings), "value": value})
        elif entry.get("createWithDefault"):
            out.append({"target": "/".join(bindings), "value": entry.get("default")})

    _walk(model, segs, 0, [], model, entry.get("where"), leaf)
    if not out and entry.get("createWithDefault") and all(s[0] == "key" for s in segs):
        out.append({"target": "", "value": entry.get("default")})
    return out


def _locate(model, entry, target, create):
    segs = _parse(entry["path"])
    vals = [] if target == "" else str(target).split("/")
    obj = model
    vi = 0
    for i, s in enumerate(segs[:-1]):
        if not _is_obj(obj):
            return None
        if s[0] == "key":
            if not isinstance(obj, dict):
                return None
            if obj.get(s[1]) is None:
                if not create:
                    return None
                obj[s[1]] = {}
            obj = obj[s[1]]
        elif s[0] == "var":
            k = vals[vi]
            vi += 1
            obj = _get(obj, int(k)) if isinstance(obj, list) else obj.get(k)
        elif s[0] == "idsel":
            wanted = vals[vi]
            vi += 1
            arr = obj.get(s[1]) if isinstance(obj, dict) else None
            obj = next((el for el in (arr if isinstance(arr, list) else [])
                        if isinstance(el, dict) and str(el.get("id")) == wanted), None)
        else:
            rest = dict(entry, path=".".join(_fmt(x) for x in segs[i + 1:]))
            rest_target = "/".join(vals[vi:])
            for k in _keys(obj):
                hit = _locate(_get(obj, k), rest, rest_target, False)
                if hit:
                    return hit
            return None
    if not isinstance(obj, dict):
        return None
    return obj, segs[-1][1]


def get_at(model, entry, target):
    hit = _locate(model, entry, target, False)
    return hit[0].get(hit[1]) if hit else None


def set_at(model, entry, target, value):
    hit = _locate(model, entry, target, True)
    if not hit:
        raise ValueError(f'cfdproj_transient: no field for {entry["key"]} target "{target}"')
    hit[0][hit[1]] = value


def delete_at(model, entry, target):
    hit = _locate(model, entry, target, False)
    if hit:
        hit[0].pop(hit[1], None)


# --- evaluation ------------------------------------------------------------------

def value_at(kfs, t):
    prev = kfs[0]["value"]
    for f in kfs[1:]:
        tk, vk, r = f["t"], f["value"], f.get("rampS") or 0
        if t >= tk - EPS:
            prev = vk
            continue
        if r > 0 and t >= tk - r:
            return prev + (vk - prev) * (t - (tk - r)) / r
        return prev
    return prev


def corner_table(kfs, duration_s):
    rows = [[0, kfs[0]["value"]]]
    for k in range(1, len(kfs)):
        r = kfs[k].get("rampS") or 0
        rows.append([kfs[k]["t"] - r, kfs[k - 1]["value"]])
        rows.append([kfs[k]["t"], kfs[k]["value"]])
    last = rows[-1]
    if duration_s > last[0]:
        rows.append([duration_s, last[1]])
    return [row for i, row in enumerate(rows) if i == 0 or row != rows[i - 1]]


def on_grid(x, g):
    q = x / g
    return abs(q - _js_round(q)) < 1e-9 * max(1, abs(q))


def _js_round(x):
    """JavaScript Math.round (half toward +inf), not Python's banker's round."""
    return math.floor(x + 0.5)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else float(v)


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_transient(tr):
    errs = []

    def push(path, msg):
        errs.append({"path": path, "msg": msg})

    if not isinstance(tr, dict):
        return [{"path": "transient", "msg": "missing transient block"}]
    if tr.get("mode") not in ("steady", "transient"):
        push("transient.mode", "mode must be steady or transient")
    for key in ("airMode", "liquidMode"):
        if key in tr and tr[key] not in ("steady", "transient"):
            push(f"transient.{key}", f"{key} must be steady or transient")
    if (tr.get("airMode") in ("steady", "transient") and tr.get("liquidMode") in ("steady", "transient")
            and tr.get("mode") != mode_of_domains(tr["airMode"], tr["liquidMode"])):
        push("transient.mode", "mode must be transient exactly when airMode or liquidMode is")
    if not (_finite(tr.get("durationS")) and tr["durationS"] > 0):
        push("transient.durationS", "duration must be > 0")
    if not (_finite(tr.get("gridS")) and tr["gridS"] > 0):
        push("transient.gridS", "grid must be > 0")
    start = tr.get("start")
    if not isinstance(start, dict) or start.get("kind") not in ("steadyConverged", "uniform"):
        push("transient.start", "start must be steadyConverged or uniform")
    grid = tr.get("gridS") if _finite(tr.get("gridS")) else 0
    duration = tr.get("durationS") if _finite(tr.get("durationS")) else 0
    seen = set()
    for i, trk in enumerate(tr.get("tracks") if isinstance(tr.get("tracks"), list) else []):
        p = f"transient.tracks[{i}]"
        entry = entry_of(trk.get("param") if isinstance(trk, dict) else None)
        if not entry:
            push(p, f'unknown parameter {trk.get("param") if isinstance(trk, dict) else None}')
            continue
        ident = f'{trk.get("modelUid")}|{trk.get("param")}|{trk.get("target")}'
        if ident in seen:
            push(p, "duplicate track")
        seen.add(ident)
        k = trk.get("keyframes") if isinstance(trk.get("keyframes"), list) else []
        if not k or k[0].get("t") != 0 or (k[0].get("rampS") or 0) != 0:
            push(p, "a track needs a keyframe at t = 0 without ramp")
        for j, f in enumerate(k):
            q = f"{p}.keyframes[{j}]"
            v = f.get("value")
            if not _finite(v) or v < entry["min"] or v > entry["max"]:
                push(q, f'value outside [{entry["min"]}, {entry["max"]}] {entry["unit"]}')
            if j == 0:
                continue
            r = f.get("rampS") or 0
            if not f["t"] > k[j - 1]["t"]:
                push(q, "keyframe times must be strictly increasing")
            if r < 0 or r > f["t"] - k[j - 1]["t"] + EPS:
                push(q, "ramp longer than the gap to the previous keyframe")
            if grid > 0 and (not on_grid(f["t"], grid) or not on_grid(f["t"] - r, grid)):
                push(q, f"time not on the {_jsnum(grid)} s grid")
            if f["t"] > duration + EPS:
                push(q, "keyframe after the end of the timeline")
    return errs


def _jsnum(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


# --- per-domain modes (E8b; twin of cfdproj_transient.js) -------------------------
def mode_of_domains(air, liquid):
    return "transient" if "transient" in (air, liquid) else "steady"


def domain_modes(doc):
    """{"air", "liquid"} of a document; blocks written before E8b derive them
    from what each run followed (air: solverType; liquid: the timeline when it
    was transient, unless its DAE was steady)."""
    doc = doc if isinstance(doc, dict) else {}
    tr = doc.get("transient") if isinstance(doc.get("transient"), dict) else {}
    ok = ("steady", "transient")
    run = doc.get("runDefaults") if isinstance(doc.get("runDefaults"), dict) else {}
    sim = run.get("simulationConfig") if isinstance(run.get("simulationConfig"), dict) else {}
    liquid = run.get("liquid") if isinstance(run.get("liquid"), dict) else {}
    exp = liquid.get("experiment") if isinstance(liquid.get("experiment"), dict) else {}
    dae = exp.get("dae") if isinstance(exp.get("dae"), dict) else None
    analysis = dae.get("analysis") if dae and isinstance(dae.get("analysis"), dict) else {}
    air = tr["airMode"] if tr.get("airMode") in ok else ("transient" if sim.get("solverType") == "transient" else "steady")
    if tr.get("liquidMode") in ok:
        liq = tr["liquidMode"]
    else:
        liq = "transient" if tr.get("mode") == "transient" and analysis.get("mode") != "steady" else "steady"
    return {"air": air, "liquid": liq}


def with_domain_modes(doc):
    """`doc` with both domain modes present and `mode` derived (in place)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("transient"), dict):
        return doc
    m = domain_modes(doc)
    doc["transient"]["airMode"] = m["air"]
    doc["transient"]["liquidMode"] = m["liquid"]
    doc["transient"]["mode"] = mode_of_domains(m["air"], m["liquid"])
    return doc


def default_transient(doc):
    room = (doc or {}).get("geometry", {}).get("temperature") if isinstance((doc or {}).get("geometry"), dict) else None
    room_t = room if _finite(room) else 22
    return {"mode": "steady", "airMode": "steady", "liquidMode": "steady", "durationS": 600, "gridS": 1,
            "start": {"kind": "uniform", "uniform": {"airT_C": room_t, "waterT_C": 30, "solidT_C": 40}},
            "tracks": []}


# --- tracks <-> fields ------------------------------------------------------------

def for_each_model(doc, fn):
    for bucket, items in ((doc or {}).get("constructions") or {}).items():
        if not isinstance(items, dict):
            continue
        for uid, model in items.items():
            if isinstance(model, dict):
                fn(bucket, uid, model)


def _entries_for(bucket):
    return [e for e in registry()["params"] if e["bucket"] == bucket]


def track_key(uid, param, target):
    return f"{uid}|{param}|{target}"


def snapshot_targets(doc):
    out = {}

    def visit(bucket, uid, model):
        for e in _entries_for(bucket):
            for hit in enumerate_targets(model, e):
                out[track_key(uid, e["key"], hit["target"])] = {"uid": uid, "entry": e, "target": hit["target"], "value": hit["value"]}

    for_each_model(doc, visit)
    return out


def tracks_from_fields(doc):
    return [{"modelUid": s["uid"], "param": s["entry"]["key"], "target": s["target"],
             "keyframes": [{"t": 0, "value": _num(s["value"])}]} for s in snapshot_targets(doc).values()]


def _model_index(doc):
    idx = {}
    for_each_model(doc, lambda b, uid, m: idx.__setitem__(uid, m))
    return idx


def _tracks_of(doc):
    tr = (doc or {}).get("transient")
    return tr.get("tracks") if isinstance(tr, dict) and isinstance(tr.get("tracks"), list) else []


def materialize_at(doc, t):
    idx = _model_index(doc)
    for trk in _tracks_of(doc):
        model = idx.get(trk.get("modelUid"))
        e = entry_of(trk.get("param"))
        if model is not None and e and trk.get("keyframes"):
            set_at(model, e, trk["target"], value_at(trk["keyframes"], t))
    return doc


def strip_tracked_fields(doc):
    idx = _model_index(doc)
    for trk in _tracks_of(doc):
        model = idx.get(trk.get("modelUid"))
        e = entry_of(trk.get("param"))
        if model is not None and e:
            delete_at(model, e, trk["target"])
    return doc


def project_at(doc, t):
    """A deep copy of a v5 document with every tracked field filled in at t."""
    return materialize_at(copy.deepcopy(doc), t)


def upsert_keyframe(trk, t, value):
    k = trk["keyframes"]
    i = next((j for j, f in enumerate(k) if abs(f["t"] - t) < EPS), -1)
    if i == 0:
        k[0] = {"t": 0, "value": value}
        return
    if i > 0:
        k[i] = dict(k[i], value=value)
        return
    at = next((j for j, f in enumerate(k) if f["t"] > t), len(k))
    k.insert(at, {"t": t, "value": value, "rampS": 0})
    if at + 1 < len(k):
        nxt = k[at + 1]
        if (nxt.get("rampS") or 0) > nxt["t"] - t:
            nxt["rampS"] = nxt["t"] - t


# --- migration v4 -> v5 --------------------------------------------------------------

def _round9(x):
    return _js_round(x * 1e9) / 1e9


def _k_to_c(k):
    return _round9(k - 273.15)


def keyframes_from_points(points):
    kfs = [{"t": 0, "value": points[0]["value"]}]
    for i in range(1, len(points)):
        p, q = points[i], points[i - 1]
        if p["value"] == q["value"]:
            continue
        gap = _round9(p["t"] - q["t"])
        kf = {"t": q["t"], "value": p["value"], "rampS": 0} if gap <= 0.1 + 1e-9 \
            else {"t": p["t"], "value": p["value"], "rampS": gap}
        last = kfs[-1]
        if kf["t"] <= last["t"] + EPS:
            if last["t"] == 0:
                kfs[0] = {"t": 0, "value": kf["value"]}
            else:
                last["value"] = kf["value"]
        else:
            kfs.append(kf)
    return kfs


def _migrate_to_v4(raw):
    import cfdproj_migrate  # sibling module (scripts/)
    return cfdproj_migrate.migrate_cfdproj_to_v4(raw)


def migrate_v4_to_v5_with_report(raw):
    if not isinstance(raw, dict):
        return raw, []
    if (raw.get("schemaVersion") or 0) >= 5:
        return raw, []
    base = _migrate_to_v4(raw) if (raw.get("schemaVersion") or 0) < 4 else raw
    doc = copy.deepcopy(base)
    report = []
    tracks = tracks_from_fields(doc)

    def find(uid, param, target):
        return next((x for x in tracks if x["modelUid"] == uid and x["param"] == param and x["target"] == target), None)

    liquid = ((doc.get("runDefaults") or {}).get("liquid") or {})
    exp = liquid.get("experiment") if isinstance(liquid, dict) else None
    dae = exp.get("dae") if isinstance(exp, dict) else None
    sim = (doc.get("runDefaults") or {}).get("simulationConfig") or {}
    analysis = (dae or {}).get("analysis") if isinstance(dae, dict) else None
    liquid_transient = isinstance(analysis, dict) and analysis.get("mode") == "transient"
    mode = "transient" if sim.get("solverType") == "transient" or liquid_transient else "steady"

    for pc in (dae.get("primary_circuits") if isinstance(dae, dict) and isinstance(dae.get("primary_circuits"), list) else []):
        uid = re.sub(r"_hx$", "", str((pc or {}).get("edge_id") or ""))
        ti = find(uid, "cdu.primaryInletT", "")
        if ti and _finite(pc.get("inlet_K")):
            ti["keyframes"][0]["value"] = _k_to_c(pc["inlet_K"])
        tf = find(uid, "cdu.primaryFlow", "")
        if tf and _finite(pc.get("flow_kg_s")):
            tf["keyframes"][0]["value"] = pc["flow_kg_s"]

    def convert_curves(bucket, uid, model):
        tv = model.get("timeVarying")
        if not isinstance(tv, dict):  # an empty {} is still dropped below, as in JS
            return
        params = sorted(tv.keys(), key=lambda p: 0 if p == "heatOutput" else 1)
        for param in params:
            pts = [p for p in (tv[param] if isinstance(tv[param], list) else [])
                   if isinstance(p, dict) and _finite(p.get("t")) and _finite(p.get("value"))]
            pts.sort(key=lambda p: p["t"])
            if not pts:
                continue
            targets = []
            if bucket == "acus" and param in ("airflow", "supplyTemp"):
                key = "acu.airflow" if param == "airflow" else "acu.supplyTemp"
                targets = [(trk, lambda v: v) for trk in tracks if trk["modelUid"] == uid and trk["param"] == key]
            elif bucket == "boxes" and param == "heatOutput":
                targets = [(trk, lambda v: v) for trk in tracks if trk["modelUid"] == uid and trk["param"] == "box.heat"]
            elif bucket == "racks" and param == "heatOutput":
                servers = [trk for trk in tracks if trk["modelUid"] == uid and trk["param"] == "server.power"]
                h0 = pts[0]["value"]

                def scaler(p0, n=len(servers)):
                    return (lambda v: _round9(p0 * v / h0)) if h0 != 0 else (lambda v: _round9(v / n))

                targets = [(trk, scaler(trk["keyframes"][0]["value"])) for trk in servers]
                if targets:
                    report.append(f"{uid}: rack heatOutput curve applied to its {len(targets)} servers in proportion to their power")
            elif bucket == "racks" and param.startswith("power:"):
                trk = find(uid, "server.power", param[6:])
                if trk:
                    targets = [(trk, lambda v: v)]
            if not targets:
                report.append(f'{uid}: timeVarying "{param}" has no keyframable parameter here and was dropped')
                continue
            for trk, scale in targets:
                kfs = keyframes_from_points([{"t": p["t"], "value": scale(p["value"])} for p in pts])
                field = trk["keyframes"][0]["value"]
                if kfs[0]["value"] != field:
                    if mode == "steady":
                        report.append(f'{uid} {trk["param"]} {trk["target"]}: curve starts at {_jsnum(kfs[0]["value"])}, kept the steady value {_jsnum(field)} at t = 0')
                        kfs[0] = {"t": 0, "value": field}
                    else:
                        report.append(f'{uid} {trk["param"]} {trk["target"]}: start value {_jsnum(field)} → {_jsnum(kfs[0]["value"])} (from its curve)')
                trk["keyframes"] = kfs
        model.pop("timeVarying", None)

    for_each_model(doc, convert_curves)

    liquid_scheduled = False
    if isinstance(exp, dict) and isinstance(exp.get("settings"), dict):
        s = exp["settings"]
        if s.get("loadStep") is True and _finite(s.get("stepTime")) and _finite(s.get("loadFactor")):
            liquid_scheduled = True
            heat = [x for x in tracks if x["param"] == "cp.heat"]
            for trk in heat:
                upsert_keyframe(trk, s["stepTime"], _round9(trk["keyframes"][0]["value"] * s["loadFactor"]))
            report.append(f'liquid load step ×{_jsnum(s["loadFactor"])} at {_jsnum(s["stepTime"])} s → {len(heat)} cold-plate heat keyframes')
            s["loadStep"] = False
        ss = s.get("supplySchedule")
        if isinstance(ss, dict) and ss.get("mode") != "fixed":
            liquid_scheduled = True
            for trk in [x for x in tracks if x["param"] == "cdu.primaryInletT"]:
                if ss.get("mode") == "step":
                    trk["keyframes"] = [{"t": 0, "value": _k_to_c(ss["from"])}, {"t": ss["stepTime"], "value": _k_to_c(ss["to"]), "rampS": 0}]
                elif ss.get("mode") == "ramp":
                    trk["keyframes"] = [{"t": 0, "value": _k_to_c(ss["from"])},
                                        {"t": _round9(ss["stepTime"] + ss["rampTime"]), "value": _k_to_c(ss["to"]), "rampS": ss["rampTime"]}]
                elif ss.get("mode") == "sinusoid":
                    dt = exp["dt_s"] if _finite(exp.get("dt_s")) and exp["dt_s"] > 0 else 1
                    n = _js_round((exp.get("duration_s") or 0) / dt)

                    def at(t):
                        return _k_to_c(ss["mean"] + ss["amplitude"] * math.sin(2 * math.pi * t / ss["period"]))

                    trk["keyframes"] = [{"t": 0, "value": at(0)}] + [
                        {"t": _round9(k * dt), "value": at(k * dt), "rampS": dt} for k in range(1, n + 1)]
            suffix = " (sampled every dt as linear segments)" if ss.get("mode") == "sinusoid" else ""
            report.append(f'liquid supply schedule "{ss.get("mode")}" → primary-inlet temperature keyframes{suffix}')
            s.pop("supplySchedule", None)
    # A liquid experiment without a DAE (.liquidrun v2) marches in time too; its
    # load step / supply schedule are keyframes now, so the timeline is transient.
    if liquid_scheduled and mode == "steady":
        mode = "transient"
        report.append("the liquid run steps its inputs over time → the project timeline is transient")

    if isinstance(exp, dict) and _finite(exp.get("duration_s")) and exp["duration_s"] > 0:
        duration = exp["duration_s"]
    elif _finite(sim.get("endTimeSeconds")) and sim["endTimeSeconds"] > 0:
        duration = sim["endTimeSeconds"]
    else:
        duration = 600
    last_t = max([0] + [x["keyframes"][-1]["t"] for x in tracks])
    if last_t > duration:
        report.append(f"timeline extended from {_jsnum(duration)} s to {_jsnum(last_t)} s to hold every keyframe")
        duration = last_t
    times = []
    for trk in tracks:
        for f in trk["keyframes"]:
            times.append(f["t"])
            times.append(_round9(f["t"] - (f.get("rampS") or 0)))
    grid = next((g for g in (1, 0.5, 0.1, 0.05, 0.01, 0.001) if all(on_grid(x, g) for x in times)), 0.001)
    dt = exp["dt_s"] if (liquid_transient or liquid_scheduled) and _finite(exp.get("dt_s")) and exp["dt_s"] > 0 else None
    if dt and all(on_grid(x, dt) for x in times) and on_grid(duration, dt):
        grid = dt
    geometry = doc.get("geometry") if isinstance(doc.get("geometry"), dict) else {}
    room = geometry.get("temperature")
    room_t = room if _finite(room) else 22
    init = analysis.get("initialization") if isinstance(analysis, dict) else None
    air_mode = "transient" if sim.get("solverType") == "transient" else "steady"
    liquid_mode = "transient" if liquid_transient or liquid_scheduled else "steady"
    doc["transient"] = {
        "mode": mode, "airMode": air_mode, "liquidMode": liquid_mode, "durationS": duration, "gridS": grid,
        "start": {"kind": "steadyConverged" if init == "steady" else "uniform",
                  "uniform": {"airT_C": room_t, "waterT_C": 30, "solidT_C": 40}},
        "tracks": tracks,
    }
    strip_tracked_fields(doc)
    doc["schemaVersion"] = 5
    return doc, report


def migrate_v4_to_v5(raw):
    return migrate_v4_to_v5_with_report(raw)[0]


def migrate_to_v5(raw):
    return migrate_v4_to_v5(raw)
