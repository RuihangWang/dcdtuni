"""JS and Python migrators must produce VALUE-identical canonical JSON.

Parity contract (decided 2026-06-12): value-parity, not strict byte-parity. A
browser JSON serializer and Python's json cannot agree byte-for-byte on every
number (e.g. scientific-notation thresholds: 1e-05 vs 0.00001), and those forms
round-trip to the identical value. So the canonical comparison normalizes number
representation (integer-valued floats -> int) and compares values. The Python
migrator also collapses whole-number floats on output (7.0 -> 7) so its written
files stay byte-clean for the common case (test_int_float_literal_collapse pins
that). Genuinely divergent VALUES (dropped fields, wrong keys) still fail here.

Covers the regressions found by the Plan-1 final review: numeric equipmentId
keying (C2), whole-number floats (C1), empty-container truthiness (I3),
id-less probes (I4), pipe_junction decomposition (I5), and the v2->v3 leg via the
chained entrypoint (I2). Also pins what the JS schema gained after the port: the
air-side pipe kind ('pipe' -> bucket 'pipes') and the meshMode / meshEngine /
firstCellSize mesh keys.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cfdproj_migrate as cm  # noqa: E402

JS_MIGRATE = str(ROOT / "shared" / "js" / "cfdproj_migrate.js")

CASES = [
    "case/Base_leakage/Base_leakage.cfdproj",
    "case/Shinaide/Shinaide.cfdproj",
    "case/CMI-China/CMI_China.cfdproj",
    "case/STDCT/STDCT.cfdproj",
    "case/Liquid/Liquid.cfdproj",
]


def _js_migrate_file(path, entry="migrateCfdprojV3toV4"):
    """Run the JS migrator on a .cfdproj file via node (reads the file in-process,
    so large real cases don't blow the CLI arg-length limit)."""
    snippet = (
        "const M = require(process.argv[1]);"
        "const fs = require('fs');"
        "const raw = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));"
        f"process.stdout.write(JSON.stringify(M.{entry}(raw)));"
    )
    out = subprocess.run(
        ["node", "-e", snippet, JS_MIGRATE, str(path)],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _js_migrate_obj(obj, entry="migrateCfdprojV3toV4"):
    """Run the JS migrator on a small in-memory fixture via node (passed as a CLI
    arg — keep fixtures small). `entry` selects the v3->v4 leg or chained v2->v3->v4."""
    snippet = (
        "const M = require(process.argv[1]);"
        "const raw = JSON.parse(process.argv[2]);"
        f"process.stdout.write(JSON.stringify(M.{entry}(raw)));"
    )
    out = subprocess.run(
        ["node", "-e", snippet, JS_MIGRATE, json.dumps(obj)],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _norm_numbers(o):
    """Collapse integer-valued floats to int so comparison is value-based."""
    if isinstance(o, dict):
        return {k: _norm_numbers(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_norm_numbers(v) for v in o]
    if isinstance(o, float) and o.is_integer():
        return int(o)
    return o


def _vcanon(obj) -> str:
    return json.dumps(_norm_numbers(obj), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _assert_parity(v3doc, entry="migrateCfdprojV3toV4", py_fn=None):
    py_fn = py_fn or cm.migrate_cfdproj_v3_to_v4
    py = py_fn(json.loads(json.dumps(v3doc)))  # deep copy; migrators may mutate
    js = _js_migrate_obj(v3doc, entry=entry)
    assert _vcanon(py) == _vcanon(js), (
        "JS/Python value divergence:\nPY=" + _vcanon(py) + "\nJS=" + _vcanon(js))
    return py


def _v3doc(construction, **kw):
    doc = {
        "schemaVersion": 3,
        "meta": {"projectName": "T", "exportFileName": "T", "inputUnit": "m",
                 "description": "",
                 "room": {"bounds": None, "polygon": None, "height": 3, "temperature": 22},
                 "case": {"path": "/tmp/T"}},
        "model": {"racks": {}, "acus": {}},
        "construction": construction, "probeData": [], "sensorData": [],
        "simulation": {}, "liquidCooling": {"enabled": False}, "ui": {},
    }
    doc.update(kw)
    return doc


# --- 5 real cases: value-parity on the full migrated document -----------------

def test_parity_on_all_five_cases():
    for rel in CASES:
        p = REPO / rel
        raw = json.loads(p.read_text(encoding="utf-8"))
        py = cm.migrate_cfdproj_v3_to_v4(raw)
        js = _js_migrate_file(p)
        assert _vcanon(py) == _vcanon(js), f"parity mismatch on {rel}"


# --- C2: numeric equipmentId must not drop server physics in Python ----------

def test_parity_numeric_equipment_id():
    rack = {
        "id": "rack1", "kind": "rack", "name": "R1", "enabled": True,
        "anchor": {"x": 0, "y": 0, "z": 0}, "base": {"W": 0.6, "H": 2, "D": 1},
        "faces": {},
        "cfd": {"servers": [{"equipmentId": 5, "powerW": 300, "tempC": 25}]},
        "rack": {"unitCount": 42, "equipments": [
            {"id": 5, "name": "srv5", "type": "fanServer", "startUnit": 1, "spanUnits": 2}]},
    }
    py = _assert_parity(_v3doc([rack]))
    cooling = py["constructions"]["racks"]["rack1"]["constructions"]["servers"]["5"]["cooling"]
    assert cooling == {"powerW": 300, "tempC": 25}, cooling  # not dropped


# --- C1: whole-number floats collapse to int in the Python WRITER ------------

def test_int_float_literal_collapse():
    box = {"id": "b1", "kind": "box", "anchor": {"x": 1, "y": 1, "z": 1},
           "base": {"W": 2.0, "H": 1.0, "D": 3.0}, "faces": {}}
    py = _assert_parity(_v3doc([box], extraWhole=7.0))
    size = py["constructions"]["boxes"]["b1"]["geometry"]["size"]
    assert size == {"x": 2, "y": 1, "z": 3}
    assert all(isinstance(v, int) for v in size.values()), size
    assert py["extraWhole"] == 7 and isinstance(py["extraWhole"], int)
    # The serialized writer output must carry no integer-valued float literals.
    assert "2.0" not in json.dumps(py) and "7.0" not in json.dumps(py)


# --- I3: empty {} / [] containers are JS-truthy; Python must keep them --------

def test_parity_empty_containers():
    box = {"id": "b1", "kind": "box", "anchor": {}, "base": {}, "faces": {},
           "cfd": {"heatSource": {}}}
    pipe = {"id": "p1", "kind": "pipe_segment", "anchor": {"x": 0, "y": 0, "z": 0},
            "base": {"W": 0.1, "H": 0.1, "D": 1}, "faces": {},
            "pipe": {"valves": [], "blindEnds": {}, "waypoints": []}}
    # An empty inletDoor {} is JS-truthy: the door face must be emitted on both
    # sides (the rack door skip-guard is the inverted truthiness idiom).
    rack = {"id": "r1", "kind": "rack", "anchor": {"x": 0, "y": 0, "z": 0},
            "base": {"W": 0.6, "H": 2, "D": 1}, "faces": {},
            "rack": {"inletDoor": {}}}
    py = _assert_parity(_v3doc([box, pipe, rack]))
    door_faces = [f for f in py["constructions"]["racks"]["r1"]["geometry"]["faces"]
                  if f.get("role") == "door"]
    assert any(f["uid"] == "inletDoor" for f in door_faces), door_faces


# --- I4: an id-less probe must not crash Python / diverge from JS -------------

def test_parity_probe_missing_id():
    doc = _v3doc([], probeData=[{"name": "anon", "x": 1, "y": 2, "z": 3}])
    py = _assert_parity(doc)
    assert py["instrumentation"]["probes"] == {}  # skipped, not a sentinel key


# --- I5: pipe_junction decomposition (untested before) -----------------------

def test_parity_pipe_junction():
    junction = {"id": "j1", "kind": "pipe_junction",
                "anchor": {"x": 0, "y": 0, "z": 0}, "base": {"W": 0.1, "H": 0.1, "D": 0.1},
                "faces": {},
                "junction": {"type": "tee", "lossCoeff": 0.5,
                             "connectedPipes": ["p1", "p2"]}}
    py = _assert_parity(_v3doc([junction]))
    jc = py["constructions"]["pipeJunctions"]["j1"]
    assert jc["cooling"]["type"] == "tee"
    assert jc["cooling"]["lossCoeff"] == 0.5
    assert jc["connectedPipes"] == ["p1", "p2"]


# --- air-side pipe: kind 'pipe' -> bucket 'pipes' (JS since 5868b463) ---------

def test_parity_air_pipe():
    # Shaped like the React factory's seed (createAirPipe): a round duct whose
    # waypoints are local to the anchor, joined to the next pipe.
    duct = {"id": "m_54", "kind": "pipe", "name": "Duct_riser_1", "enabled": True,
            "showAnchorMarker": False, "opacity": 1,
            "anchor": {"x": 1.55, "y": 1.1, "z": 1.79}, "base": {"W": 0, "D": 0, "H": 0},
            "faces": {}, "solidRegions": [], "cfd": {"role": "wall"},
            "pipe": {"shape": "round", "diameterM": 0.15, "widthM": 0.2, "heightM": 0.2,
                     "waypoints": [{"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 1.0, "z": 0}],
                     "joins": [{"end": "end", "pipeUid": "m_55", "pipeEnd": "start"}]}}
    # Rectangular with a null size, non-array lists and an unknown key: the null
    # takes its default, non-arrays become [], the unknown key lands in cooling.
    rect = {"id": "m_55", "kind": "pipe", "anchor": {"x": 0, "y": 0, "z": 0}, "faces": {},
            "pipe": {"shape": "rect", "diameterM": None, "widthM": 0.3, "heightM": 0.1,
                     "waypoints": {}, "joins": None, "insulation": "none"}}
    # No pipe block at all: every default applies.
    bare = {"id": "m_56", "kind": "pipe", "anchor": {"x": 0, "y": 0, "z": 0}}
    py = _assert_parity(_v3doc([duct, rect, bare]))
    cons = py["constructions"]
    assert set(cons["pipes"]) == {"m_54", "m_55", "m_56"}, cons["pipes"]
    assert cons["boxes"] == {}, cons["boxes"]  # not migrated box-like
    duct4 = cons["pipes"]["m_54"]
    assert duct4["geometry"]["shape"] == "round" and duct4["geometry"]["diameterM"] == 0.15
    assert duct4["geometry"]["waypoints"] == [{"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 1, "z": 0}]
    assert duct4["geometry"]["joins"] == [{"end": "end", "pipeUid": "m_55", "pipeEnd": "start"}]
    assert duct4["cooling"] == {}, duct4["cooling"]  # cfd.role is dropped
    g = cons["pipes"]["m_55"]["geometry"]
    assert (g["shape"], g["diameterM"], g["widthM"], g["heightM"]) == ("rect", 0.15, 0.3, 0.1)
    assert g["waypoints"] == [] and g["joins"] == []
    assert cons["pipes"]["m_55"]["cooling"] == {"insulation": "none"}
    g = cons["pipes"]["m_56"]["geometry"]
    assert (g["shape"], g["diameterM"], g["widthM"], g["heightM"]) == ("round", 0.15, 0.2, 0.2)
    assert g["waypoints"] == [] and g["joins"] == []


# --- mesh keys: meshMode / meshEngine / firstCellSize go to meshConfig --------

def test_parity_mesh_engine_keys_split_into_mesh_config():
    # JS MESH_KEYS gained meshMode + firstCellSize in e58a7446, meshEngine in 5868b463.
    sim = {"meshBaseSize": 0.3, "meshMode": "cartesianLocalized", "meshEngine": "dcCart",
           "firstCellSize": 0.05, "solverType": "steady"}
    py = _assert_parity(_v3doc([], simulation=sim))
    assert py["runDefaults"]["meshConfig"] == {
        "meshBaseSize": 0.3, "meshMode": "cartesianLocalized", "meshEngine": "dcCart",
        "firstCellSize": 0.05}, py["runDefaults"]["meshConfig"]
    assert py["runDefaults"]["simulationConfig"] == {"solverType": "steady"}


def test_parity_dccart_sizing_keys_split_into_mesh_config():
    # 2026-09-22: weld / first-layer floor / rack-cell floor are mesh keys.
    sim = {"weldTolerance": 0.0012, "firstLayerMinFraction": 0.3, "firstLayerMax": 0.005,
           "firstLayerSealGap": -1, "rackCellMinSize": 0.0175, "endTime": 500}
    py = _assert_parity(_v3doc([], simulation=sim))
    assert py["runDefaults"]["meshConfig"] == {
        "weldTolerance": 0.0012, "firstLayerMinFraction": 0.3, "firstLayerMax": 0.005,
        "firstLayerSealGap": -1, "rackCellMinSize": 0.0175}, py["runDefaults"]["meshConfig"]
    assert py["runDefaults"]["simulationConfig"] == {"endTime": 500}


# --- I2: the v2->v3 leg via the chained entrypoint (96% of real files) --------

def test_parity_v2_chained_entrypoint():
    v2 = {
        "schemaVersion": 2,
        "projectName": "T2", "exportFileName": "T2", "inputUnit": "mm",
        "models": [{"id": "b1", "kind": "box", "anchor": {"x": 1, "y": 1, "z": 1},
                    "base": {"W": 1, "H": 1, "D": 1}, "faces": {}}],
        "cfdRoom": {}, "liquidCooling": {},
    }
    py = cm.migrate_cfdproj_to_v4(json.loads(json.dumps(v2)))
    js = _js_migrate_obj(v2, entry="migrateCfdprojToV4")
    assert py["schemaVersion"] == 4
    assert _vcanon(py) == _vcanon(js), (
        "v2 chained divergence:\nPY=" + _vcanon(py) + "\nJS=" + _vcanon(js))
