"""Regenerate src/frontend/shared/testdata/liquid_display_expected.json.

Deliberate rule changes only: the file is the cross-implementation contract of
the liquid display quantities (liquid_colour.quantity_frame <-> liquidDisplay.ts)
and the flow arrows (liquid_flow.flow_arrows <-> liquidFlow.ts)."""
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import liquid_colour as lc  # noqa: E402
from dc.cfd_bridge import liquid_flow as lf  # noqa: E402

TD = os.path.abspath(os.path.join(HERE, "..", "..", "..", "shared", "testdata"))
sys.path.insert(0, HERE)
from test_liquid_display import display_fixture  # noqa: E402

with open(os.path.join(TD, "liquid_flow_fixture.json")) as f:
    data = display_fixture(json.load(f))
samples = list(range(len(data["time_s"])))
colours = {}
for cmap in ("coolwarm", "viridis", "jet"):
    for value, lo, hi in ((0.0, 0.0, 1.0), (0.37, 0.0, 1.0), (0.5, 0.0, 1.0), (1.0, 0.0, 1.0), (2.0, 0.0, 1.0),
                          (150.0, 132.0, 161.4), (-1.0, 0.0, 1.0), (5.0, 5.0, 5.0)):
        colours[f"{cmap}|{value!r}|{lo!r}|{hi!r}"] = list(lc.value_to_rgb8(value, lo, hi, cmap))
expected = {
    "note": "cross-implementation contract for liquid display quantities and flow arrows, on "
            "liquid_flow_fixture.json plus two bindings (test_liquid_display.display_fixture) "
            "(liquid_colour.py/liquid_flow.py <-> liquidDisplay.ts/liquidFlow.ts); "
            "regenerate with cfd_demo/tests/_write_liquid_display_expected.py only on a deliberate rule change",
    "samples": samples,
    "quantities": {q: {str(s): lc.quantity_frame(data, s, q) for s in samples} for q in lc.QUANTITIES},
    "globalDomains": {q: lc.quantity_global_domain(data, q) for q in lc.QUANTITIES},
    "arrows": {str(s): lf.flow_arrows(data, s) for s in samples},
    "arrowGlobalDomains": lf.arrow_global_domains(data),
    "colours": colours,
}
with open(os.path.join(TD, "liquid_display_expected.json"), "w") as f:
    json.dump(expected, f)
    f.write("\n")
print("wrote", len(samples), "samples x", len(lc.QUANTITIES), "quantities and", len(colours), "colours")
