"""Tests for probes file parser + sensor API endpoints.

Spec: docs/superpowers/specs/2026-04-18-sensor-points-design.md
"""
import sys
from pathlib import Path
import pytest

# server.py is at STLGeneratorForOpenFoam/server/server.py
# add that dir to sys.path so we can `from server import ...`
SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


PROBE_SCALAR_SAMPLE = """\
# Probe 0 (0.5 0.2 1.8)
# Probe 1 (1.0 0.8 2.0)
# Time          probe0       probe1
    10       299.5        300.1
    20       299.8        300.4
    30       300.1        300.7
"""

PROBE_VECTOR_SAMPLE = """\
# Probe 0 (0.5 0.2 1.8)
# Probe 1 (1.0 0.8 2.0)
# Time          probe0                  probe1
    10       (0.1 0.2 0.3)      (0.4 0.5 0.6)
    20       (0.11 0.21 0.31)   (0.41 0.51 0.61)
"""


def test_parse_probes_scalar_file(tmp_path):
    from server import parse_probes_file
    f = tmp_path / "T"
    f.write_text(PROBE_SCALAR_SAMPLE)
    steps, values = parse_probes_file(f, is_vector=False, since=-1)
    assert steps == [10, 20, 30]
    assert values == [[299.5, 300.1], [299.8, 300.4], [300.1, 300.7]]


def test_parse_probes_vector_file(tmp_path):
    from server import parse_probes_file
    f = tmp_path / "U"
    f.write_text(PROBE_VECTOR_SAMPLE)
    steps, values = parse_probes_file(f, is_vector=True, since=-1)
    assert steps == [10, 20]
    assert values == [
        [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
        [[0.11, 0.21, 0.31], [0.41, 0.51, 0.61]],
    ]


def test_parse_probes_since_filter(tmp_path):
    from server import parse_probes_file
    f = tmp_path / "T"
    f.write_text(PROBE_SCALAR_SAMPLE)
    steps, values = parse_probes_file(f, is_vector=False, since=10)
    assert steps == [20, 30]
    assert values == [[299.8, 300.4], [300.1, 300.7]]


def test_parse_probes_empty_file(tmp_path):
    from server import parse_probes_file
    f = tmp_path / "T"
    f.write_text("# Probe 0 (0 0 0)\n# Time     probe0\n")
    steps, values = parse_probes_file(f, is_vector=False, since=-1)
    assert steps == []
    assert values == []


def test_parse_probes_missing_file(tmp_path):
    from server import parse_probes_file
    steps, values = parse_probes_file(tmp_path / "nope", is_vector=False, since=-1)
    assert steps == []
    assert values == []


def _write_fake_run(tmp_path, write_interval=10):
    """Build a minimal <case>/log/<run>/postProcessing/probes/0/{T,U,...} tree."""
    case = tmp_path / "case"
    run = case / "log" / "2026-04-18-15-42-08_steadyFoam"
    poly = run / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text("FoamFile {}\n0\n(\n)\n")  # a complete mesh: run_dir detect is strict
    # system/controlDict with writeInterval = N (regex source)
    sysdir = run / "system"
    sysdir.mkdir(parents=True)
    (sysdir / "controlDict").write_text(
        f"functions {{ probes {{ writeInterval {write_interval}; }} }}\n"
    )
    probes0 = run / "postProcessing" / "probes" / "0"
    probes0.mkdir(parents=True)
    (probes0 / "T").write_text(PROBE_SCALAR_SAMPLE)
    (probes0 / "U").write_text(PROBE_VECTOR_SAMPLE)
    for fname in ("p", "p_rgh", "rho"):
        (probes0 / fname).write_text(PROBE_SCALAR_SAMPLE)
    return case, run


def test_find_latest_run_dir(tmp_path):
    import server
    case, run = _write_fake_run(tmp_path)
    assert server._find_latest_run_dir(case) == run


def test_find_latest_run_dir_none(tmp_path):
    import server
    assert server._find_latest_run_dir(tmp_path / "empty") is None


def test_handle_probes_latest_full(tmp_path, monkeypatch):
    import server
    case, _ = _write_fake_run(tmp_path, write_interval=10)
    monkeypatch.setattr(server, "CASE_DIR", case)

    class _Fake:
        def __init__(self):
            self.status = None
            self.payload = None
        def __call__(self, handler, status, payload):
            self.status = status
            self.payload = payload
    fake_send = _Fake()
    monkeypatch.setattr(server, "_send_json", fake_send)

    class H:
        path = "/api/probes-latest?since=-1"
    server.handle_probes_latest(H())
    assert fake_send.status == 200
    p = fake_send.payload
    assert p["steps"] == [10, 20, 30]
    assert p["writeInterval"] == 10
    assert p["fields"]["T"] == [[299.5, 300.1], [299.8, 300.4], [300.1, 300.7]]
    assert p["fields"]["U"][0] == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def test_handle_probes_latest_since_filter(tmp_path, monkeypatch):
    import server
    case, _ = _write_fake_run(tmp_path)
    monkeypatch.setattr(server, "CASE_DIR", case)
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))

    class H:
        path = "/api/probes-latest?since=10"
    server.handle_probes_latest(H())
    status, p = fake_send_calls[0]
    assert status == 200
    assert p["steps"] == [20, 30]


def test_handle_probes_latest_no_case(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, "CASE_DIR", None)
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))
    class H:
        path = "/api/probes-latest?since=-1"
    server.handle_probes_latest(H())
    assert fake_send_calls[0][0] == 400


def test_handle_probes_latest_no_run(tmp_path, monkeypatch):
    import server
    case = tmp_path / "empty_case"
    case.mkdir()
    monkeypatch.setattr(server, "CASE_DIR", case)
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))
    class H:
        path = "/api/probes-latest?since=-1"
    server.handle_probes_latest(H())
    assert fake_send_calls[0][0] == 404


def test_handle_harvest_sensor_results_ok(tmp_path, monkeypatch):
    import server, json
    case, run = _write_fake_run(tmp_path)
    monkeypatch.setattr(server, "CASE_DIR", case)
    # Add sensor locations in controlDict (needed by harvest to reflect back)
    cd_path = run / "system" / "controlDict"
    cd_path.write_text(
        "functions {\n"
        "  probes {\n"
        "    writeInterval 10;\n"
        "    probeLocations (\n"
        "        (0.5 0.2 1.8)\n"
        "        (1.0 0.8 2.0)\n"
        "    );\n"
        "  }\n"
        "}\n"
    )
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))

    class H:
        path = "/api/harvest-sensor-results"
        headers = {"Content-Length": "0"}
        def __init__(self):
            import io
            body = json.dumps({
                "caseName": "TestCase",
                "sensorNames": ["sensor_1", "sensor_2"],
                "unitTemperature": "C",
            }).encode()
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)
    server.handle_harvest_sensor_results(H())
    status, payload = fake_send_calls[0]
    assert status == 200
    out_path = Path(payload["path"])
    assert out_path.exists()
    doc = json.loads(out_path.read_text())
    assert doc["version"] == "1.0"
    assert doc["caseName"] == "TestCase"
    assert doc["finalStep"] == 30
    assert doc["writeInterval"] == 10
    assert doc["unitLength"] == "m"
    assert doc["unitTemperature"] == "C"
    assert len(doc["sensors"]) == 2
    # sensor_1: T_final = 300.1 K → 27 (rounded ~°C); location = first probe
    s1 = doc["sensors"][0]
    assert s1["name"] == "sensor_1"
    assert s1["location"] == [0.5, 0.2, 1.8]
    assert abs(s1["T_final"] - (300.1 - 273.15)) < 1e-6
    assert s1["U_final"] == [0.11, 0.21, 0.31]


def test_handle_harvest_sensor_count_mismatch(tmp_path, monkeypatch):
    import server, json, io
    case, run = _write_fake_run(tmp_path)
    monkeypatch.setattr(server, "CASE_DIR", case)
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))

    class H:
        path = "/api/harvest-sensor-results"
        def __init__(self):
            body = json.dumps({
                "caseName": "TestCase",
                "sensorNames": ["only_one"],  # but probes file has 2 columns
                "unitTemperature": "K",
            }).encode()
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)
    server.handle_harvest_sensor_results(H())
    status, payload = fake_send_calls[0]
    assert status == 500
    assert "sensor count mismatch" in payload["error"]


def test_handle_harvest_rho_optional(tmp_path, monkeypatch):
    """2026-04-18 E2E-bugfix regression: Boussinesq solvers (e.g.
    buoyantBoussinesqSimpleFoam) don't write rho. Harvest must NOT 500 on
    missing optional fields — only T and U are required."""
    import server, json, io
    case, run = _write_fake_run(tmp_path)
    # Delete rho probe file to simulate Boussinesq output
    (run / "postProcessing" / "probes" / "0" / "rho").unlink()
    monkeypatch.setattr(server, "CASE_DIR", case)
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))

    class H:
        path = "/api/harvest-sensor-results"
        def __init__(self):
            body = json.dumps({
                "caseName": "Bouss",
                "sensorNames": ["sensor_1", "sensor_2"],
                "unitTemperature": "C",
            }).encode()
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)
    server.handle_harvest_sensor_results(H())
    status, payload = fake_send_calls[0]
    assert status == 200, f"expected 200, got {status}: {payload}"
    doc = json.loads(Path(payload["path"]).read_text())
    # rho should be None, not an error
    for s in doc["sensors"]:
        assert s["rho_final"] is None


def test_handle_harvest_sentinel_to_null(tmp_path, monkeypatch):
    """2026-04-18 E2E-bugfix regression: probes outside the mesh domain get
    OpenFOAM's -1e+300 sentinel. Harvest must map that to JSON null — both
    scalars and vector components (U)."""
    import server, json, io
    case, run = _write_fake_run(tmp_path)
    # Patch probe 0 T to the sentinel
    tpath = run / "postProcessing" / "probes" / "0" / "T"
    sentinel_scalar = """\
# Probe 0 (0.5 0.2 1.8)
# Probe 1 (1.0 0.8 2.0)
# Time          probe0       probe1
    10       -1e+300       300.1
    20       -1e+300       300.4
    30       -1e+300       300.7
"""
    tpath.write_text(sentinel_scalar)
    # Also patch U to have all-sentinel first-probe vectors
    upath = run / "postProcessing" / "probes" / "0" / "U"
    sentinel_vec = """\
# Probe 0 (0.5 0.2 1.8)
# Probe 1 (1.0 0.8 2.0)
# Time          probe0                              probe1
    10       (-1e+300 -1e+300 -1e+300)      (0.4 0.5 0.6)
    20       (-1e+300 -1e+300 -1e+300)      (0.41 0.51 0.61)
"""
    upath.write_text(sentinel_vec)
    monkeypatch.setattr(server, "CASE_DIR", case)
    fake_send_calls = []
    monkeypatch.setattr(server, "_send_json",
        lambda h, s, p: fake_send_calls.append((s, p)))

    class H:
        path = "/api/harvest-sensor-results"
        def __init__(self):
            body = json.dumps({
                "caseName": "SentinelTest",
                "sensorNames": ["sensor_1", "sensor_2"],
                "unitTemperature": "K",
            }).encode()
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)
    server.handle_harvest_sensor_results(H())
    status, payload = fake_send_calls[0]
    assert status == 200
    doc = json.loads(Path(payload["path"]).read_text())
    # sensor_1 is outside-mesh → all sentinels → null
    assert doc["sensors"][0]["T_final"] is None
    assert doc["sensors"][0]["U_final"] is None
    # sensor_2 has valid data
    assert doc["sensors"][1]["T_final"] == 300.7
    assert doc["sensors"][1]["U_final"] == [0.41, 0.51, 0.61]


# ---------------------------------------------------------------------------
# Which run the live poller reads (2026-09-22: Base604 solve clicked at
# 11:45:56, runner named the dir 11:46:02 -> the minute prefix never matched
# and the sensor tab stayed at "waiting for sensor data" for the whole hour)
# ---------------------------------------------------------------------------

def _partitioned_run(case, name, ranks=2):
    run = case / "log" / name
    for rank in range(ranks):
        poly = run / f"processor{rank}" / "constant" / "polyMesh"
        poly.mkdir(parents=True)
        for f in ("points", "faces", "owner", "neighbour", "boundary"):
            (poly / f).write_text("FoamFile {}\n0\n(\n)\n")
    return run


def test_find_latest_run_dir_started_at_survives_a_minute_boundary(tmp_path):
    import server
    from datetime import datetime
    case, run = _write_fake_run(tmp_path)  # 2026-04-18-15-42-08
    stale = _partitioned_run(case, "2026-04-18-15-30-00_old")
    started = datetime(2026, 4, 18, 15, 41, 56)
    assert server._find_latest_run_dir(case, prefix=started.strftime("%Y-%m-%d-%H-%M")) is None
    assert server._find_latest_run_dir(case, started_at=started) == run
    # a run older than the click is never the live one
    assert server._find_latest_run_dir(case, started_at=datetime(2026, 4, 18, 15, 50, 0)) is None
    assert stale.exists()


def test_capture_solve_run_dir_pins_the_runner_line(tmp_path, monkeypatch):
    import server
    case, run = _write_fake_run(tmp_path)
    monkeypatch.setattr(server, "_LATEST_SOLVE_RUN_DIR", None)
    assert server._capture_solve_run_dir("2026-09-22 11:46:02 | INFO | Time = 10") is None
    got = server._capture_solve_run_dir(f"2026-09-22 11:46:02 | INFO | Created run directory: {run}")
    assert got == run and server._LATEST_SOLVE_RUN_DIR == run


def test_handle_probes_latest_reads_the_pinned_run_dir(tmp_path, monkeypatch):
    import server
    case, run = _write_fake_run(tmp_path)
    # a newer, unrelated run that the pin must beat
    _partitioned_run(case, "2026-04-18-16-00-00_other")
    monkeypatch.setattr(server, "CASE_DIR", case)
    monkeypatch.setattr(server, "_LATEST_SOLVE_PREFIX", "2026-04-18-15-41")
    monkeypatch.setattr(server, "_LATEST_SOLVE_RUN_DIR", run)
    calls = []
    monkeypatch.setattr(server, "_send_json", lambda h, s, p: calls.append((s, p)))

    class H:
        path = "/api/probes-latest?since=-1"
    server.handle_probes_latest(H())
    assert calls[0][0] == 200
    assert calls[0][1]["steps"] == [10, 20, 30]
