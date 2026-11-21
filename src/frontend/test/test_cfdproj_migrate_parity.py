import json, pathlib, subprocess, sys
import pytest
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src/frontend/scripts"))
from cfdproj_migrate import migrate_v2_to_v3

JS = ROOT / "src/frontend/shared/js/cfdproj_migrate.js"
# Diverse real fixtures: rect room + snap on; snap off; unversioned legacy + polygon room.
FIXTURES = [
    "case/Base_leakage/Base_leakage.cfdproj",
    "case/SingleRack/SingleRack.cfdproj",
    "docs/manuscript/paper_experiment/case_04_polygon/label/case_04_polygon.cfdproj",
]


@pytest.mark.parametrize("rel", FIXTURES)
def test_js_python_parity(rel):
    src = ROOT / rel
    if not src.exists():
        pytest.skip(f"missing fixture {rel}")
    raw = json.loads(src.read_text(encoding="utf-8"))
    py = migrate_v2_to_v3(raw)
    node_script = (
        "const {migrateCfdprojV2toV3}=require(process.argv[1]);"
        "const fs=require('fs');"
        "const raw=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
        "process.stdout.write(JSON.stringify(migrateCfdprojV2toV3(raw)));"
    )
    out = subprocess.check_output(["node", "-e", node_script, str(JS), str(src)], text=True)
    js = json.loads(out)
    assert js == py, f"JS and Python migrators diverged on {rel}"
