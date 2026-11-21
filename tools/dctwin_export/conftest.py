"""The exporter's tests import the backend of this checkout (an editable install may point at another one)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for entry in (str(HERE), str(HERE.parents[1] / "src")):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

# The templates (templates_ispo/) are files of the exported folder and import the exported module names
# (backend.api, backend.dcdesign, backend.plant: export.MOVED). Since 2026-11-16 those modules live in src/agent in
# this checkout, so the templates' tests run in an export (export.py --profile ispo, then pytest in that folder),
# not here.
collect_ignore = ["templates_ispo/test_direct_session.py"]
