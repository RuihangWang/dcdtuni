"""Regenerate src/frontend/shared/testdata/liquid_colour_expected.json (deliberate rule changes only)."""
import json, os, sys
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import liquid_colour as lc  # noqa: E402
TD = os.path.abspath(os.path.join(HERE, "..", "..", "..", "shared", "testdata"))
r = lc.LiquidResult(json.load(open(os.path.join(TD, "liquid_result_fixture.json"))))
frames = {}
for s in range(r.samples):
    fr = r.frame(s)
    frames[str(s)] = {"|".join([t["kind"], t["modelUid"], t["equipmentUid"], t["liquidPart"]]): {"T": t["T"], "rgb": t["rgb"], "group": t["group"]} for t in fr["targets"]}
    frames[str(s)]["__domains__"] = {g: v["domain"] for g, v in fr["groups"].items()}
json.dump({"note": "cross-implementation contract for liquid result colours (liquid_colour.py <-> liquidTempColor.ts)", "frames": frames}, open(os.path.join(TD, "liquid_colour_expected.json"), "w"), indent=1, sort_keys=True)
print("wrote", len(frames), "frames")
