#!/usr/bin/env python3
"""Solve the three steady anchors the hotspot animation interpolates between.

    S0  normal      all six ACUs as the project ships them
    S1  failure     m_51 / m_52 / m_54 walled off -- the three units feeding the
                    southern contained aisle, i.e. 52% of installed cooling gone
    S2  intervention the three survivors at 1.8x flow and 15 C supply

One mesh, three solves. Geometry never changes -- only patch VALUES -- so the
existing `Mesh/constant/polyMesh` stays valid and every anchor lands on the same
cells, which is what makes a per-point interpolation between them meaningful.
The script hashes the mesh before and after each solve and refuses to continue if
it moved.

A dead ACU becomes a WALL on both its supply and its return face, not a zero-flow
inlet. Zeroing the supply while leaving the return as a p_rgh = 0 outlet would
leave the room draining through a hole with nothing feeding it; a closed damper is
both the standard simplification and the numerically safe one.

These solves are deliberately short and are NOT converged -- see
docs/superpowers/specs/2026-08-18-hotspot-transition-demo-design.md. All three get
the identical budget from the identical initial field, so the differences between
them come from the boundary conditions rather than from unequal convergence.

    python3 hotspot_anchors.py              # solve all three
    python3 hotspot_anchors.py S1 S2        # solve a subset
"""
import hashlib
import http.cookiejar
import json
import os
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[5]
# The administrator login and the port: DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD,
# else the launcher's local-mode file, else the pair written below; the server is
# STLGEN_SERVER, else http://localhost:$DCDTUNI_PORT (default 8000).
sys.path.insert(0, str(REPO / "src" / "frontend" / "scripts"))
from admin_login import base_url, login_payload  # noqa: E402

SERVER = os.environ.get("STLGEN_SERVER") or base_url()
CASE = pathlib.Path(os.environ.get("HOTSPOT_CASE", REPO / "case/GTC"))
CFDRUN = CASE / "model" / "GTC_2025.cfdrun"
# Artefacts live under case/GTC/forOVdemo/, which .gitignore excludes: the anchors, the
# 31 interpolated frames and the viewport shots come to ~6.6 GB per scenario and have no
# business in git. Only the finished videos (copied into the project page's assets), the
# ACU position file and this code are tracked.
ART = pathlib.Path(os.environ.get("HOTSPOT_ART", REPO / "case/GTC/forOVdemo"))
OUT = pathlib.Path(os.environ.get("HOTSPOT_OUT", ART / "hotspot_anchors"))
ITERATIONS = int(os.environ.get("HOTSPOT_ITERS", "600"))

# The three units that feed the southern half. m_51 and m_54 bracket the
# contained aisle at z 4.8-7.2; m_52 sits on the same wall just north of it.
# Which units fail. Overridable so a milder scenario (a single unit) can be produced
# without touching the file: HOTSPOT_DEAD=m_54
DEAD = tuple(x.strip() for x in
             os.environ.get("HOTSPOT_DEAD", "m_51,m_52,m_54").split(",") if x.strip())
BOOST_FLOW = 1.8
BOOST_SUPPLY_C = 15.0

ANCHORS = {
    "S0": {"dead": (), "boost": False, "label": "normal - six ACUs"},
    "S1": {"dead": DEAD, "boost": False,
           "label": f"failure - {len(DEAD)} ACU(s) down"},
    "S2": {"dead": DEAD, "boost": True,
           "label": f"intervention - survivors x{BOOST_FLOW}, {BOOST_SUPPLY_C} C"},
}

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def log(m):
    print(f"[anchors] {m}", flush=True)


def api(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SERVER}{path}", data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def mesh_fingerprint():
    """Hash the two polyMesh files that would change if the mesh were rebuilt."""
    h = hashlib.sha256()
    for name in ("points", "owner"):
        p = CASE / "Mesh" / "constant" / "polyMesh" / name
        h.update(p.read_bytes() if p.exists() else b"MISSING")
    return h.hexdigest()[:16]


def acu_uid(binding):
    """`constructions.acus.m_51` -> `m_51`."""
    return (binding.get("objectPath") or "").rsplit(".", 1)[-1]


def build_variant(base, dead, boost):
    """Return a cfdrun dict with the anchor's ACU boundary conditions applied.

    Three places carry ACU state and they must agree. `boundaryBindings` is the
    one the solver actually reads (cfdrun_reader materialises it into the BC
    registry); `inputs.acus` is the raw UI state; `meshGeometry.acus` is
    buildCfdrunJson's derived blob that the mesh pipeline reads. Writing only the
    first would leave a manifest that contradicts itself.
    """
    d = json.loads(json.dumps(base))  # deep copy

    for key, binding in d["boundaryBindings"].items():
        role = binding.get("role")
        if role not in ("acu_supply", "acu_return"):
            continue
        uid = acu_uid(binding)
        if uid in dead:
            # Closed damper: a plain wall, and drop the now-meaningless
            # flow/temperature parameters so the BCSpec carries nothing stale.
            binding["role"] = "wall"
            binding["bcType"] = "wall"
            binding["parameters"] = {}
        elif boost:
            p = binding.setdefault("parameters", {})
            if "flowRateM3s" in p and p["flowRateM3s"]:
                p["flowRateM3s"] = round(p["flowRateM3s"] * BOOST_FLOW, 4)
            if role == "acu_supply":
                p["temperatureC"] = BOOST_SUPPLY_C
                p["setPointTempC"] = BOOST_SUPPLY_C
                # minSupplyTempC would otherwise clamp the new set point back up.
                if p.get("minSupplyTempC") is not None:
                    p["minSupplyTempC"] = min(p["minSupplyTempC"], BOOST_SUPPLY_C)

    for uid, acu in d.get("inputs", {}).get("acus", {}).items():
        if uid in dead:
            acu["acuSwitch"] = False
            acu["supplyAirVolumeFlowRateM3s"] = 0.0
        elif boost:
            acu["supplyAirVolumeFlowRateM3s"] = round(
                acu.get("supplyAirVolumeFlowRateM3s", 0) * BOOST_FLOW, 4)
            acu["supplyAirTemperatureC"] = BOOST_SUPPLY_C
            acu["setPointTemperatureC"] = BOOST_SUPPLY_C

    for uid, acu in d.get("meshGeometry", {}).get("acus", {}).items():
        for face in list(acu.get("supplyFaces", [])) + list(acu.get("returnFaces", [])):
            ph = face.setdefault("physics", {})
            if uid in dead:
                ph["volumeFlowRate"] = 0.0
                ph["velocity"] = 0.0
            elif boost:
                for k in ("volumeFlowRate", "velocity"):
                    if ph.get(k):
                        ph[k] = round(ph[k] * BOOST_FLOW, 4)
                if "temperature" in ph:
                    ph["temperature"] = BOOST_SUPPLY_C

    # Iteration budget. The runner reads the meshGeometry copy of
    # simulationConfig, not the root one, so set both and let them agree.
    for cfg in (d.get("simulationConfig"),
                d.get("meshGeometry", {}).get("simulationConfig")):
        if isinstance(cfg, dict):
            cfg["endTime"] = ITERATIONS
            cfg["writeInterval"] = ITERATIONS
            cfg["startFrom"] = "startTime"
    return d


def newest_log_dir():
    logs = [p for p in (CASE / "log").glob("*") if p.is_dir()]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def solve(name, base):
    cfg = ANCHORS[name]
    log(f"=== {name}: {cfg['label']} ===")
    before_fp = mesh_fingerprint()
    before_log = newest_log_dir()

    variant = build_variant(base, set(cfg["dead"]), cfg["boost"])
    CFDRUN.write_text(json.dumps(variant))
    log(f"manifest written ({CFDRUN.stat().st_size / 1e6:.1f} MB), "
        f"{ITERATIONS} iterations, dead={cfg['dead'] or 'none'}")

    resp = api("POST", "/api/run-mesh", {"solveOnly": True})
    job = resp.get("jobId")
    if not job:
        raise SystemExit(f"{name}: run-mesh gave no jobId: {resp}")
    log(f"job {job} started")

    t0 = time.time()
    rc = None
    while time.time() - t0 < 7200:
        st = api("GET", f"/api/job-status?job_id={job}")
        if st.get("done") is True:
            rc = st.get("returncode", -1)
            break
        time.sleep(15)
    if rc is None:
        raise SystemExit(f"{name}: timed out after {time.time() - t0:.0f}s")
    log(f"job finished rc={rc} in {time.time() - t0:.0f}s")

    after_fp = mesh_fingerprint()
    if after_fp != before_fp:
        raise SystemExit(f"{name}: the mesh MOVED ({before_fp} -> {after_fp}); "
                         "anchors would no longer share cells")

    out = newest_log_dir()
    if out is None or (before_log is not None and out == before_log):
        raise SystemExit(f"{name}: no new log directory appeared")
    log(f"result -> {out}")
    return {"anchor": name, "label": cfg["label"], "dead": list(cfg["dead"]),
            "boost": cfg["boost"], "iterations": ITERATIONS,
            "returncode": rc, "logDir": str(out), "meshFingerprint": after_fp}


def main():
    wanted = [a for a in sys.argv[1:] if a in ANCHORS] or list(ANCHORS)
    OUT.mkdir(parents=True, exist_ok=True)

    orig = CFDRUN.with_suffix(".cfdrun.orig")
    if not orig.exists():
        shutil.copy2(CFDRUN, orig)
        log(f"original manifest backed up -> {orig.name}")
    base = json.loads(orig.read_text())

    reply = api("POST", "/api/auth/login", login_payload(("admin", "WIN#@desk")))
    if not reply.get("ok"):
        raise SystemExit(f"login to {SERVER} failed: {reply.get('error') or reply}")
    if not api("POST", "/api/set-case-dir", {"caseDir": str(CASE)}).get("ok"):
        raise SystemExit("set-case-dir failed")
    log(f"case dir {CASE}, mesh fingerprint {mesh_fingerprint()}")

    manifest_path = OUT / "anchors.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for name in wanted:
        manifest[name] = solve(name, base)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        log(f"manifest -> {manifest_path}")

    shutil.copy2(orig, CFDRUN)
    log("original manifest restored")


if __name__ == "__main__":
    main()
