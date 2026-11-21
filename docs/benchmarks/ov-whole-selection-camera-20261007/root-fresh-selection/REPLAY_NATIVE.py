"""Controlled actual-source first-only counterexample; no Kit/browser services."""
import importlib.util
import json
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("source_selection_tests", root / "candidate/src/frontend/omniverse/cfd_demo/tests/test_source_selection.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = root / sys.argv[1] / "src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py"
if sys.argv[2] == "programmatic14":
    original_harness = module.harness
    module.harness = lambda: original_harness(source)
    test = module.SourceSelectionTests("test_programmatic_fourteen_object_locate_echo_does_not_collapse_to_first")
    try:
        test.test_programmatic_fourteen_object_locate_echo_does_not_collapse_to_first()
    except AssertionError as error:
        raise AssertionError("PRODUCT RED: programmatic 14 UID selection collapses to first native echo") from error
    print(json.dumps({"case": "programmatic14", "passed": True, "source": str(source)}), flush=True)
    raise SystemExit(0)
ext, selection, stage, paths, events = module.harness(source)
selection.paths = paths["UPS_Rack"][:]
ext._emit_source_pick()
if sys.argv[2] == "shift":
    selection.paths.append(paths["CDU_1"][0])
    expected = {"UPS_Rack", "CDU_1"}
else:
    selection.paths = []
    expected = set()
ext._emit_source_pick()
actual = set(module.emitted_uids(events[-1]))
print(json.dumps({"case": sys.argv[2], "source": str(source), "events": events, "actual": sorted(actual), "expected": sorted(expected)}, ensure_ascii=False), flush=True)
assert actual == expected, "PRODUCT RED: selected complete UID set did not reach outbound event"
