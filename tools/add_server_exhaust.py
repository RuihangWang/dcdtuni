#!/usr/bin/env python3
"""Give every fan server of a .cfdproj whose rear face has no opening the editor's fan-exhaust strip.

A fanServer whose ``faces.outlet.patches`` is empty exhausts through its whole rear face: a slow,
face-wide outflow instead of the jet its fans blow through a strip of that face.  The projects the
editor builds carry one ``rect`` opening ``{x: 0.75, y: 0, w: 0.25, h: 1}`` (face fractions) there;
this tool adds that opening (or ``--rect x,y,w,h``) to every such server, through the editor itself
(``agent.scripted_api.build_project`` with the project as its base, verb ``addServerPatch``), so the
saved bytes are the editor's and its air export checks them.  Servers that already have an outlet
opening, and servers that are not ``fanServer``, are left as they are; a second run changes nothing.

Usage (repository root, dcdtuni environment)::

    python tools/add_server_exhaust.py case/Base201/Base201.cfdproj [--out <path>] [--rect 0.75,0,0.25,1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def missing_exhaust(project: dict) -> list:
    """``(rackUid, serverUid)`` of every fanServer with no outlet opening."""
    rows = []
    for rack_uid, rack in (project.get("constructions", {}).get("racks") or {}).items():
        for srv_uid, srv in ((rack.get("constructions") or {}).get("servers") or {}).items():
            if srv.get("type") != "fanServer":
                continue
            if not ((srv.get("faces") or {}).get("outlet") or {}).get("patches"):
                rows.append((rack.get("uid", rack_uid), srv.get("uid", srv_uid)))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="default: overwrite the project")
    ap.add_argument("--rect", default="0.75,0,0.25,1", help="x,y,w,h in fractions of the rear face")
    a = ap.parse_args(argv)
    x, y, w, h = (float(v) for v in a.rect.split(","))
    project = json.loads(a.project.read_text(encoding="utf-8"))
    todo = missing_exhaust(project)
    print(json.dumps({"project": str(a.project), "fanServersWithoutExhaust": len(todo)}))
    if not todo:
        return 0
    from agent.scripted_api import build_project
    rect = {"x": x, "y": y, "w": w, "h": h}
    steps = [{"verb": "addServerPatch", "args": [r, s, "outlet", rect]} for r, s in todo]
    out = (a.out or a.project).resolve()
    result = build_project(steps, out=str(out), base=str(a.project), overwrite=True, timeout=3600.0)
    keep = {k: result.get(k) for k in ("status", "failure", "out", "modelCount", "selfCheck")}
    print(json.dumps(keep, ensure_ascii=False, indent=1, default=str))
    if result.get("status") != "succeeded":
        return 1
    left = missing_exhaust(json.loads(out.read_text(encoding="utf-8")))
    print(json.dumps({"fanServersWithoutExhaustAfter": len(left)}))
    return 0 if not left and (result.get("selfCheck") or {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
