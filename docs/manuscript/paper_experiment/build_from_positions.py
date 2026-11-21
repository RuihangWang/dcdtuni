#!/usr/bin/env python3
"""Exact-coordinate Builder for Excel-spreadsheet mode (reproducibility tool).

Rebuilds a case's geometry from the precise `Positions` sheet of
`input/layout.xlsx` through the live editor, then transcribes the equipment and
boundary-condition physics (rack servers, ACU supply/return, floor openings)
from the label. This realises the paper's Excel-spreadsheet mode, where the
spreadsheet carries exact coordinates rather than the coarse 0.5 m `Layout`
grid, which cannot encode dense 0.6 m-pitch racks.

## What changed, and why the old version could not run

It drove the editor through `state.construction[...]`, `m.name`, `m.anchor`,
`m.base` and `m.transform.quaternion` — the v3 spelling. The document moved to
V4 in b821e27 (2026-06-15), eight days after this file was last touched: models
now live in `constructions.<bucket>` keyed by uid, with `meta.name` and
`geometry.location/size/quaternion`. Probed against the running editor, every
one of those paths reads `undefined`. It also spliced its result into an
existing `workdir/<case>.cfdproj`, and no case has a workdir any more.

So it now builds from nothing through `window.__dcdtuni`, the automation
contract, and writes the file itself.

Prereqs:
  - openpyxl (declared in pyproject).
  - A server on PORT, started through the launcher — the only supported way:
      ./src/start_server.sh --port 8040 --case-dir <anything>
    Override with DCDTUNI_PORT. Do not point this at a port someone else is on:
    one server holds ONE global case dir.
  - Login: the DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD pair when set, else
    the launcher's local-mode file src/server/auth-local/admin_credentials.

Usage:
  python build_from_positions.py <case> [<case> ...]
Then score:
  python score_case.py <case>
"""
import json
import os
import sys
from pathlib import Path

import requests
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
DEMO = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src/frontend/scripts"))
from cfdproj_migrate import load_cfdproj_v4  # noqa: E402

PORT = os.environ.get("DCDTUNI_PORT", "8040")
BASE = f"http://localhost:{PORT}"

# Models per exec-js call. The server's browser call times out at 30 s and each
# create is a store edit plus a React render, so keep the batches small enough
# that a 300-model case still lands inside one call's budget.
BATCH = 25

CREATE_FN = {
    "rack": "createDefaultRackModel",
    "acu": "createDefaultAcuModel",
    "baffle": "createDefaultBaffleModel",
    "box": "createDefaultBaseModel",
    "floor": "createDefaultFloorModel",
    "wall": "createDefaultWallModel",
    "cdu": "createDefaultCduModel",
}


LOCAL_CREDENTIALS = ROOT / "src" / "server" / "auth-local" / "admin_credentials"


def _credential_candidates():
    """Who the server may run as: the launcher's environment pair, its local-mode file, then the README pair."""
    found = []
    user, password = os.environ.get("DCDTUNI_ADMIN_USER"), os.environ.get("DCDTUNI_ADMIN_PASSWORD")
    if user and password:
        found.append((user, password))
    try:
        lines = LOCAL_CREDENTIALS.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 2 and lines[0].strip() and lines[1]:
            found.append((lines[0].strip(), lines[1]))
    except OSError:
        pass
    found.append(("admin", "WIN#@desk"))
    return list(dict.fromkeys(found))


def login(s):
    """Log in with the first candidate the server accepts (the server has no built-in password)."""
    last = None
    for user, password in _credential_candidates():
        r = s.post(f"{BASE}/api/auth/login", json={"username": user, "password": password}, timeout=30)
        if r.status_code == 200:
            return
        last = r
    raise RuntimeError(f"login failed: HTTP {last.status_code} {last.text[:200]} (tried DCDTUNI_ADMIN_USER/"
                       f"DCDTUNI_ADMIN_PASSWORD, {LOCAL_CREDENTIALS} and the README pair)")


def ex(s, js, timeout=180):
    r = s.post(f"{BASE}/api/browser/exec-js", json={"js": js}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"exec-js HTTP {r.status_code}: {r.text[:600]}")
    j = r.json()
    if isinstance(j, dict) and j.get("error"):
        raise RuntimeError(f"exec-js error: {j['error']}")
    return j.get("result", j)


def read_positions(case):
    wb = load_workbook(DEMO / case / "input" / "layout.xlsx", data_only=True)
    if "Positions" not in wb.sheetnames:
        raise RuntimeError(
            f"{case}: layout.xlsx has no Positions sheet "
            f"(sheets: {', '.join(wb.sheetnames)}) — this builder is the "
            f"exact-coordinate path and needs one")
    rows = list(wb["Positions"].iter_rows(values_only=True))
    hdr = list(rows[0])
    return [dict(zip(hdr, r)) for r in rows[1:] if r and r[0] is not None]


def read_room(case, label):
    """Room footprint and height. The Room sheet wins; the label is the fallback.

    Both are read because the sheet's `height_m` is the WALL height while the
    label's `geometry.height` is the domain height, and the mesh reads the
    latter — a case with a raised floor has two different numbers and picking
    the wrong one silently shortens the domain.
    """
    wb = load_workbook(DEMO / case / "input" / "layout.xlsx", data_only=True)
    plane, height = None, None
    if "Room" in wb.sheetnames:
        params = {r[0]: r[1] for r in wb["Room"].iter_rows(values_only=True)
                  if r and r[0] is not None}
        if params.get("polygon"):
            plane = json.loads(params["polygon"])
    geometry = label.get("geometry") or {}
    height = geometry.get("height")
    if plane is None:
        plane = geometry.get("plane")
    return plane, height


BUILD_ONE = """(async () => {
  const D = window.__dcdtuni, S = %s, FN = %s;
  const made = [];
  for (const sp of S) {
    // Looked up on the CONTRACT, not on `window`. The bare top-level aliases
    // were removed once the legacy editor went — a dynamic `window[fn]` is
    // exactly the kind of call a name-based rewrite cannot see, and it fails by
    // building nothing rather than by throwing.
    const fn = FN[sp.kind];
    if (!fn || typeof D[fn] !== 'function') { made.push(null); continue; }
    const uid = D[fn]();
    if (!uid) { made.push(null); continue; }
    D.placeModel(uid, {
      name: sp.name,
      location: { x: sp.x_m || 0, y: sp.y_m || 0, z: sp.z_m || 0 },
      size: { W: sp.W_m || 0, D: sp.D_m || 0, H: sp.H_m || 0 },
      rotationDeg: { x: 0, y: sp.yaw_deg || 0, z: 0 },
    });
    if (sp.enabled === false) D.setEnabled(uid, false);
    made.push({ uid: uid, name: sp.name });
  }
  return JSON.stringify(made);
})()"""


def build_geometry(s, case, specs, plane, height):
    # newProject FIRST. Without it a build lands on top of whatever the page
    # already holds — measured once as 14 models built and 28 in the file, every
    # name twice, scoring 50% with fourteen "extra" deductions.
    ex(s, "(() => { const D = window.__dcdtuni; D.newProject();"
          f" D.setProjectMeta({{ name: {json.dumps(case)}, inputUnit: 'm' }});"
          f" D.setRoomPolygon({json.dumps(plane)}, {json.dumps(height)});"
          " return D.allModels().length; })()")
    made = []
    for i in range(0, len(specs), BATCH):
        chunk = specs[i:i + BATCH]
        out = ex(s, BUILD_ONE % (json.dumps(chunk), json.dumps(CREATE_FN)))
        made.extend(json.loads(out) if isinstance(out, str) else out)
    # Sync the scene once at the end. React rebuilds as an effect, so a save or
    # an export issued before it catches up sees the previous scene — silently.
    meshes = ex(s, "(async () => await window.__dcdtuni.buildGeometryData())()")
    return made, meshes


def transcribe_equipment(project, label):
    """Copy the label's precise equipment and BC physics onto the built models.

    Matched by name, because the built models carry the spreadsheet's names and
    nothing else lines the two documents up.

    Splices in V4 terms: a rack's servers live in `constructions.servers`, and a
    face's patches in `geometry.faces[<face>].patches`. `supplyFaces` and
    `returnFaces` come across WITH the faces — they are what the .cfdrun
    exporter reads to emit an ACU's supply and return, and a splice that left
    them behind would produce a room with no cooling in a file that validates.
    """
    def by_name(doc):
        out = {}
        for bucket, items in (doc.get("constructions") or {}).items():
            for uid, m in (items or {}).items():
                name = ((m or {}).get("meta") or {}).get("name")
                if name:
                    out[name] = (bucket, uid, m)
        return out

    label_models = by_name(label)
    copied = {"servers": 0, "faces": 0, "cooling": 0}
    for bucket, items in (project.get("constructions") or {}).items():
        for uid, m in (items or {}).items():
            name = ((m or {}).get("meta") or {}).get("name")
            hit = label_models.get(name)
            if not hit or hit[0] != bucket:
                continue
            lm = hit[2]
            lcons = lm.get("constructions") or {}
            if lcons.get("servers"):
                m.setdefault("constructions", {})["servers"] = lcons["servers"]
                copied["servers"] += len(lcons["servers"])
            lg = lm.get("geometry") or {}
            if lg.get("faces"):
                m.setdefault("geometry", {})["faces"] = lg["faces"]
                copied["faces"] += 1
                for key in ("supplyFaces", "returnFaces"):
                    if key in lg:
                        m["geometry"][key] = lg[key]
            if lm.get("cooling"):
                m["cooling"] = lm["cooling"]
                copied["cooling"] += 1
    return copied


def build(case):
    s = requests.Session()
    login(s)
    label = load_cfdproj_v4(DEMO / case / "label" / f"{case}.cfdproj")
    specs = read_positions(case)
    plane, height = read_room(case, label)

    made, meshes = build_geometry(s, case, specs, plane, height)
    ok = [m for m in made if m]
    saved = ex(s, "window.__dcdtuni.saveProjectJson()")
    project = json.loads(saved)

    copied = transcribe_equipment(project, label)

    workdir = DEMO / case / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"{case}.cfdproj"
    out.write_text(json.dumps(project, indent=2))
    print(f"  {case}: {len(ok)}/{len(specs)} models, {meshes} meshes, "
          f"transcribed {copied['servers']} servers / {copied['faces']} face sets "
          f"/ {copied['cooling']} cooling blocks -> {out.relative_to(ROOT)}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for c in sys.argv[1:]:
        build(c)
