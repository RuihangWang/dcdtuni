#!/usr/bin/env python3
"""Drive every liquid-solver control in one app and record what the project became."""
import json, os, subprocess, sys

BU = os.path.expanduser("~/.local/bin/browser-use")
LIB = open(os.path.join(os.path.dirname(__file__), "liquid_lib.js")).read()

def ev(js):
    r = subprocess.run([BU, "eval", js], capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.splitlines():
        if line.startswith("result: "):
            return line[8:]
    return "ERR:" + out.strip()[-200:]

APP = sys.argv[1]                     # 'react' | 'html'
STATE = "window.__dcdtuni.getProject()" if APP == "react" else "window.state"

# 每个控件一个已知改动，覆盖全部 11 个
STEPS = [
    ("lsm-dt",          "0.25"),
    ("lsm-nt",          "37"),
    ("lsm-bdf",         "2"),
    ("lsm-tol",         "1e-9"),
    ("lsm-device",      "cuda"),
    ("lsm-fluid-rho",   "1010"),
    ("lsm-fluid-cp",    "3900"),
    ("lsm-fluid-mu",    "0.00123"),
    ("lsm-fluid-k",     "0.55"),
    ("lc-fluid-select", "glycol_50"),   # 预设覆盖上面四项
    ("lc-fluid-select", "custom"),
]

ev(LIB)
rows = []
for cid, val in STEPS:
    ok = ev(f"window.__lqSet({json.dumps(cid)}, {json.dumps(val)})")
    st = ev(f"JSON.stringify(window.__lqRead({STATE}))")
    try:
        parsed = json.loads(st)
    except Exception:
        parsed = {"PARSE_FAIL": st[:160]}
    rows.append({"control": cid, "value": val, "set": ok, "state": parsed})
    print(f"  {cid:18s} <- {val:10s} {ok}")

out = os.path.join(os.path.dirname(__file__), f"liquid_drive_{APP}.json")
json.dump(rows, open(out, "w"), indent=1, sort_keys=True)
print("written", out)
