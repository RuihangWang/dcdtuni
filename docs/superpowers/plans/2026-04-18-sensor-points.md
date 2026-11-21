# Sensor Points Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-sidebar "📍 传感器位点" panel (求解 tab) for users to place manual probe points, stream live temperature curves in the solver monitor's new 3rd tab, and dump final-iteration values to `<case>/sensor_results/<YYYY-MM-DD>/*.json` after solve completes.

**Architecture:** Pydantic `SensorPoint` / runner `_extract_sensor_points` / `controlDict.j2` probes block are **already wired** — this plan only adds frontend UI + state + poller + monitor 3rd tab, plus two new server endpoints (`GET /api/probes-latest` for live polling, `POST /api/harvest-sensor-results` for final JSON dump). Live chart uses **file polling (β path)** — OpenFOAM probes FO doesn't emit per-step values to solver log in OF 2406.

**Tech Stack:** Python 3.10 (server.py, pytest), Vanilla JS + Three.js r128 (frontend in single monolithic STL_generate.html), OpenFOAM 2406 probes FO.

**Spec:** `docs/superpowers/specs/2026-04-18-sensor-points-design.md`

---

## File Structure

**Create:**
- `STLGeneratorForOpenFoam/server/tests/__init__.py` (if not exists — package marker)
- `STLGeneratorForOpenFoam/server/tests/test_probes_api.py` — pytest for `parse_probes_file()`, `/api/probes-latest`, `/api/harvest-sensor-results`
- `STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py` — regex-scan structural tests for UI panel / state / dialog / poller / monitor 3rd tab

**Modify:**
- `STLGeneratorForOpenFoam/server/server.py` — add `parse_probes_file()`, 2 endpoint handlers, route registration
- `STLGeneratorForOpenFoam/web/STL_generate.html` — UI panel DOM, state.sensorPoints initialization, window.* CRUD functions, edit dialog, buildCfdrunJson wiring, monitor 3rd tab, `_smSensorPoller`, post-solve harvest hook, "open latest JSON" button

**Test conventions:**
- Backend: pytest with tmp_path fixtures; run via `conda run -n dcdtuni pytest <path>`
- Frontend structural: pattern copied from existing `scripts/test_refinement_redesign_frontend.py` — regex-scan HTML for required function bodies / keys / wiring calls

---

## Task Breakdown

---

### Task 1: Backend — `parse_probes_file()` pure helper

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py` (append helper near other file parsers)
- Test: `STLGeneratorForOpenFoam/server/tests/test_probes_api.py` (create)

- [ ] **Step 1: Create test package marker**

Run:
```bash
mkdir -p /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/tests
touch /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/tests/__init__.py
```

- [ ] **Step 2: Write the failing test file**

Create `STLGeneratorForOpenFoam/server/tests/test_probes_api.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/server/tests/test_probes_api.py -v 2>&1 | tail -20
```

Expected: FAIL with `ImportError: cannot import name 'parse_probes_file' from 'server'`.

- [ ] **Step 4: Implement `parse_probes_file`**

Append to `server.py` (locate a reasonable spot — e.g. near other file-parsing helpers, before handler functions begin):

```python
def parse_probes_file(path, *, is_vector: bool, since: int = -1):
    """Parse an OpenFOAM probes FO output file (scalar or vector).

    Returns (steps, values) where:
      - steps: list[int] of time steps (integers; OF writes int when possible)
      - values: list[list[float]] for scalar, list[list[list[float]]] for vector
               outer index = step, inner index = probe index

    Format (scalar):
        # Probe 0 (x y z)
        # Probe N (x y z)
        # Time    probe0    probeN
            10  299.5      300.1

    Format (vector):
        # Probe 0 (x y z)
        # Time       probe0               probe1
            10   (0.1 0.2 0.3)       (0.4 0.5 0.6)

    `since` filters: only rows with step > since are returned.
    Missing / empty files return ([], []).
    """
    import re
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return [], []
    text = p.read_text(encoding="utf-8", errors="ignore")
    steps = []
    values = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if is_vector:
            vecs = re.findall(r"\(([^)]+)\)", line)
            if not vecs:
                continue
            # First column before any paren is the time
            head = line.split("(", 1)[0].strip()
            try:
                t = float(head)
            except ValueError:
                continue
            step = int(t) if t == int(t) else t
            if step <= since:
                continue
            row = [[float(x) for x in v.split()] for v in vecs]
            steps.append(step)
            values.append(row)
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                t = float(parts[0])
            except ValueError:
                continue
            step = int(t) if t == int(t) else t
            if step <= since:
                continue
            try:
                row = [float(x) for x in parts[1:]]
            except ValueError:
                continue
            steps.append(step)
            values.append(row)
    return steps, values
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/server/tests/test_probes_api.py -v 2>&1 | tail -15
```

Expected: 5 passed.

- [ ] **Step 6: Commit (submodule)**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add server/server.py server/tests/__init__.py server/tests/test_probes_api.py
git commit -m "$(cat <<'EOF'
feat(server): parse_probes_file() scalar+vector parser with since filter

Pure helper for reading OpenFOAM probes FO output. Handles # Probe N (x y z)
headers, scalar values (T/p/p_rgh/rho), and vector values (U) with (ux uy uz)
paren syntax. since<int> filters rows with step > since for incremental polling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Backend — `GET /api/probes-latest` endpoint

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py` (add handler + route)
- Test: `STLGeneratorForOpenFoam/server/tests/test_probes_api.py` (append tests)

- [ ] **Step 1: Add failing test**

Append to `STLGeneratorForOpenFoam/server/tests/test_probes_api.py`:

```python
def _write_fake_run(tmp_path, write_interval=10):
    """Build a minimal <case>/log/<run>/postProcessing/probes/0/{T,U,...} tree."""
    case = tmp_path / "case"
    run = case / "log" / "2026-04-18-15-42-08_steadyFoam"
    poly = run / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").touch()  # satisfies run_dir detect
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
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/server/tests/test_probes_api.py -v 2>&1 | tail -15
```

Expected: 5 new tests FAIL with `AttributeError: module 'server' has no attribute '_find_latest_run_dir'` or `handle_probes_latest`.

- [ ] **Step 3: Add `_find_latest_run_dir` helper**

Append to `server.py` (near other path helpers):

```python
def _find_latest_run_dir(case_dir):
    """Return newest <case>/log/<run>/ that contains constant/polyMesh, or None.

    Pattern copied from handle_pp_init (server.py ~L1396). Run dir names follow
    YYYY-MM-DD-HH-MM-SS_<suffix>; sorted reverse picks newest.
    """
    from pathlib import Path as _P
    log_dir = _P(case_dir) / "log"
    if not log_dir.exists():
        return None
    for d in sorted(log_dir.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if (d / "constant" / "polyMesh").exists():
            return d
    return None


def _read_write_interval(run_dir):
    """Parse writeInterval from run_dir/system/controlDict (probes subdict)."""
    import re
    cd = run_dir / "system" / "controlDict"
    if not cd.exists():
        return 10  # default fallback
    txt = cd.read_text(encoding="utf-8", errors="ignore")
    # Match writeInterval anywhere after 'probes'
    m = re.search(r"probes\s*\{[^}]*?writeInterval\s+(\d+)\s*;",
                  txt, re.DOTALL)
    return int(m.group(1)) if m else 10
```

- [ ] **Step 4: Add `handle_probes_latest` handler**

Append to `server.py`:

```python
def handle_probes_latest(handler):
    """GET /api/probes-latest?since=<int>

    Returns incremental probe values across 5 fields (T, U, p, p_rgh, rho).
    """
    from urllib.parse import urlparse, parse_qs
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    run_dir = _find_latest_run_dir(cd)
    if run_dir is None:
        _send_json(handler, 404, {"error": "No run directory found."})
        return
    probes_root = run_dir / "postProcessing" / "probes"
    if not probes_root.exists():
        # Solver just started — probes FO hasn't written yet
        _send_json(handler, 200, {
            "steps": [], "writeInterval": _read_write_interval(run_dir),
            "fields": {k: [] for k in ("T", "U", "p", "p_rgh", "rho")},
        })
        return
    # Pick earliest subdirectory (probes startTime; usually "0")
    time_dirs = sorted(d for d in probes_root.iterdir() if d.is_dir())
    if not time_dirs:
        _send_json(handler, 200, {
            "steps": [], "writeInterval": _read_write_interval(run_dir),
            "fields": {k: [] for k in ("T", "U", "p", "p_rgh", "rho")},
        })
        return
    t0 = time_dirs[0]

    # Parse `since` query
    qs = parse_qs(urlparse(handler.path).query)
    try:
        since = int(qs.get("since", ["-1"])[0])
    except (ValueError, TypeError):
        since = -1

    fields = {}
    master_steps = None
    for name in ("T", "U", "p", "p_rgh", "rho"):
        is_vec = (name == "U")
        steps, values = parse_probes_file(t0 / name, is_vector=is_vec,
                                          since=since)
        fields[name] = values
        if master_steps is None and steps:
            master_steps = steps

    _send_json(handler, 200, {
        "steps": master_steps or [],
        "writeInterval": _read_write_interval(run_dir),
        "fields": fields,
    })
```

- [ ] **Step 5: Register route**

Find the `do_GET` router dispatch block in `server.py` (grep for `/api/server-patches` to locate the pattern), and add in the same if/elif chain:

```python
elif parsed.path == "/api/probes-latest":
    handle_probes_latest(self)
    return
```

- [ ] **Step 6: Run tests**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/server/tests/test_probes_api.py -v 2>&1 | tail -15
```

Expected: 10 passed (5 from Task 1 + 5 new).

- [ ] **Step 7: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add server/server.py server/tests/test_probes_api.py
git commit -m "$(cat <<'EOF'
feat(server): GET /api/probes-latest — incremental probe-file polling

Handler auto-detects newest case/log/<run>/postProcessing/probes/<t0>/, parses
5 fields (T/U/p/p_rgh/rho) via parse_probes_file(), filters by ?since=<step>.
Returns 200 with empty arrays when solver just started (probes_dir absent).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Backend — `POST /api/harvest-sensor-results` endpoint

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py` (handler + route)
- Test: `STLGeneratorForOpenFoam/server/tests/test_probes_api.py`

- [ ] **Step 1: Add failing tests**

Append to test file:

```python
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
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/server/tests/test_probes_api.py::test_handle_harvest_sensor_results_ok -v 2>&1 | tail -10
```

Expected: FAIL with `AttributeError: no attribute 'handle_harvest_sensor_results'`.

- [ ] **Step 3: Add helpers**

Append to `server.py`:

```python
def _parse_probe_locations(run_dir):
    """Extract probeLocations list from run_dir/system/controlDict.

    Returns list[tuple[float,float,float]] or [] if missing.
    """
    import re
    cd = run_dir / "system" / "controlDict"
    if not cd.exists():
        return []
    txt = cd.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"probeLocations\s*\(\s*(.*?)\s*\)\s*;",
                  txt, re.DOTALL)
    if not m:
        return []
    locs = []
    for tup in re.findall(r"\(\s*([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s*\)",
                          m.group(1)):
        locs.append([float(x) for x in tup])
    return locs


def _convert_temp_from_k(value_k, unit):
    """Convert Kelvin to user unit. unit in {K, C, F}; default C on unknown."""
    if unit == "K":
        return value_k
    if unit == "F":
        return (value_k - 273.15) * 9.0 / 5.0 + 32.0
    return value_k - 273.15  # C
```

- [ ] **Step 4: Add handler**

Append to `server.py`:

```python
def handle_harvest_sensor_results(handler):
    """POST /api/harvest-sensor-results — read last probe row, write JSON."""
    import json as _json
    from datetime import datetime as _dt
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    run_dir = _find_latest_run_dir(cd)
    if run_dir is None:
        _send_json(handler, 404, {"error": "No run directory found."})
        return
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
        body = _json.loads(handler.rfile.read(length) or b"{}")
    except Exception as exc:
        _send_json(handler, 400, {"error": f"Invalid body: {exc}"})
        return
    case_name = body.get("caseName") or "case"
    sensor_names = list(body.get("sensorNames") or [])
    unit_t = body.get("unitTemperature", "C")

    probes_root = run_dir / "postProcessing" / "probes"
    time_dirs = sorted(d for d in probes_root.iterdir() if d.is_dir()) \
        if probes_root.exists() else []
    if not time_dirs:
        _send_json(handler, 500, {"error": "No probe data found in run directory"})
        return
    t0 = time_dirs[0]

    fields_last = {}
    last_step = None
    for name in ("T", "U", "p", "p_rgh", "rho"):
        is_vec = (name == "U")
        steps, values = parse_probes_file(t0 / name, is_vector=is_vec, since=-1)
        if not steps:
            _send_json(handler, 500, {"error": f"No data in probes/{name}"})
            return
        fields_last[name] = values[-1]
        if last_step is None:
            last_step = steps[-1]

    n_columns = len(fields_last["T"])
    if len(sensor_names) != n_columns:
        _send_json(handler, 500, {
            "error": f"sensor count mismatch: frontend={len(sensor_names)}, probes={n_columns}"
        })
        return

    locations = _parse_probe_locations(run_dir)

    sensors = []
    for i, name in enumerate(sensor_names):
        sensors.append({
            "name": name,
            "location": locations[i] if i < len(locations) else None,
            "T_final": _convert_temp_from_k(fields_last["T"][i], unit_t),
            "U_final": fields_last["U"][i],
            "p_final": fields_last["p"][i],
            "p_rgh_final": fields_last["p_rgh"][i],
            "rho_final": fields_last["rho"][i],
        })

    now = _dt.now()
    out_path = (cd / "sensor_results" / now.strftime("%Y-%m-%d")
                / f"{case_name}_sensor_{now.strftime('%H-%M-%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "caseName": case_name,
        "solveRunDir": str(run_dir.relative_to(cd)),
        "finalStep": last_step,
        "writeInterval": _read_write_interval(run_dir),
        "unitLength": "m",
        "unitTemperature": unit_t,
        "generatedAt": now.isoformat(timespec="seconds"),
        "sensors": sensors,
    }
    out_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    _send_json(handler, 200, {"path": str(out_path)})
```

- [ ] **Step 5: Register route (POST)**

Find the `do_POST` dispatch block (grep for `/api/pp/init` or `/api/run-solve`) and add:

```python
elif parsed.path == "/api/harvest-sensor-results":
    handle_harvest_sensor_results(self)
    return
```

- [ ] **Step 6: Run tests**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/server/tests/test_probes_api.py -v 2>&1 | tail -20
```

Expected: 12 passed.

- [ ] **Step 7: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add server/server.py server/tests/test_probes_api.py
git commit -m "$(cat <<'EOF'
feat(server): POST /api/harvest-sensor-results — final-step JSON dump

Reads last probe row across 5 fields, joins with probeLocations from
controlDict, converts T by requested unit, writes to
<case>/sensor_results/<YYYY-MM-DD>/<caseName>_sensor_<HH-MM-SS>.json.
Rejects frontend/probes column-count mismatch with 500.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Frontend — `state.sensorPoints` initialization

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`getInitialState` + migration in `_migrateRawState`-style loader)
- Test: `STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py` (create)

- [ ] **Step 1: Create frontend test file with failing test**

Create `STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py`:

```python
"""Structural tests for sensor points feature.

Regex-scan STL_generate.html for required state keys, DOM ids, functions,
wiring. Style mirrors test_refinement_redesign_frontend.py.

Spec: docs/superpowers/specs/2026-04-18-sensor-points-design.md
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "STL_generate.html"
HTML = HTML_PATH.read_text(encoding="utf-8")


def extract_fn_body(name):
    m = re.search(rf"function {re.escape(name)}\s*\([^)]*\)\s*\{{", HTML)
    if not m:
        m = re.search(rf"window\.{re.escape(name)}\s*=\s*function\s*\([^)]*\)\s*\{{", HTML)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    for i, ch in enumerate(HTML[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[start + 1 : i]
    return None


def test_state_sensorpoints_initialized():
    body = extract_fn_body("getInitialState")
    assert body is not None
    assert "sensorPoints" in body, "getInitialState must initialize sensorPoints"
    assert re.search(r"sensorPoints\s*:\s*\[\s*\]", body), \
        "sensorPoints must default to empty array []"
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: FAIL on `sensorPoints in body`.

- [ ] **Step 3: Add field to `getInitialState`**

Edit `STLGeneratorForOpenFoam/web/STL_generate.html`. Find `getInitialState()` (grep for `function getInitialState` — it returns a large state object). Inside the returned object, add (e.g. near `inputUnit: 'mm'`):

```js
            sensorPoints: [],   // {id, name, x, y, z (meters), enabled} — see spec 2026-04-18
```

- [ ] **Step 4: Verify test passes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): add state.sensorPoints[] to initial state

Empty array; populated by addSensorPoint (upcoming). Each entry:
{id, name, x, y, z (meters), enabled}. See spec 2026-04-18.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Frontend — UI panel DOM (prop-group in 求解 tab)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (HTML between residual prop-group and 执行求解 button, inside `#solver-settings-panel`)
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing tests**

Append to `test_sensor_points_frontend.py`:

```python
def test_sensor_panel_dom_exists():
    assert 'id="sensor-list"' in HTML
    assert 'id="sensor-temp-unit"' in HTML
    assert 'id="sensor-input-x"' in HTML
    assert 'id="sensor-input-y"' in HTML
    assert 'id="sensor-input-z"' in HTML
    assert 'id="sensor-open-json-btn"' in HTML


def test_sensor_panel_placed_in_solver_settings():
    """Panel must sit inside #solver-settings-panel, between residual section
    and the 执行求解 button."""
    m = re.search(r'id="solver-settings-panel"', HTML)
    assert m is not None
    tail = HTML[m.start():]
    # residual prop-title before our panel
    pos_res = tail.find("收敛残差阈值设置")
    pos_sensor = tail.find("📍 传感器位点")
    pos_run = tail.find('id="solve-run-btn"')
    assert pos_res < pos_sensor < pos_run, \
        "sensor prop-group must be between residual group and 执行求解 button"


def test_sensor_temp_unit_options():
    """Dropdown has K / C / F with C selected by default."""
    idx = HTML.find('id="sensor-temp-unit"')
    assert idx != -1
    # look at the select element around it
    end = HTML.find('</select>', idx)
    frag = HTML[idx:end]
    assert 'value="K"' in frag
    assert 'value="C"' in frag
    assert 'value="F"' in frag
    assert re.search(r'value="C"[^>]*selected', frag), \
        "°C must be default selected"
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 3 new tests FAIL.

- [ ] **Step 3: Add HTML prop-group**

Find `#solver-settings-panel` in `STL_generate.html` (~L210). Locate the prop-group with title `📉 收敛残差阈值设置` (~L277-284). Directly AFTER its closing `</div>` (the one that ends that prop-group) and BEFORE `<div style="margin:8px;"> ... <button id="check-mesh-btn"...>` (~L285), insert:

```html
            <div class="prop-group" style="border-color:#a569bd; margin:8px;">
                <div class="prop-title" style="color:#a569bd">📍 传感器位点</div>
                <div class="prop-row">
                    <label data-i18n="sensor.temp_unit">温度显示单位</label>
                    <select id="sensor-temp-unit" onchange="window.setSensorTempUnit(this.value)" style="flex:0 0 auto;">
                        <option value="K">K</option>
                        <option value="C" selected>°C</option>
                        <option value="F">°F</option>
                    </select>
                </div>
                <div class="prop-row" style="gap:4px; flex-wrap:wrap;">
                    <label style="flex:0 0 16px;">X</label>
                    <input type="number" id="sensor-input-x" step="0.01" value="0" style="width:60px;">
                    <label style="flex:0 0 16px;">Y</label>
                    <input type="number" id="sensor-input-y" step="0.01" value="0" style="width:60px;">
                    <label style="flex:0 0 16px;">Z</label>
                    <input type="number" id="sensor-input-z" step="0.01" value="0" style="width:60px;">
                    <button onclick="window.addSensorPoint()" style="padding:3px 10px;">+添加</button>
                </div>
                <div id="sensor-list" style="margin-top:4px;"></div>
                <button id="sensor-open-json-btn" onclick="window.openLatestSensorJson()"
                        style="width:100%; margin-top:6px; padding:4px; font-size:0.72rem;"
                        data-i18n="sensor.open_latest_json">📂 打开最近 sensor JSON</button>
            </div>
```

- [ ] **Step 4: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): add 📍 传感器位点 prop-group to 求解 tab right sidebar

DOM only — wire functions come next. Panel sits between residual section and
执行求解 button. Fields: temperature unit dropdown (K/C/F, default C),
X/Y/Z inputs + add button, list container, "open latest JSON" button.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Frontend — CRUD + list renderer

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add functions near `ppEditSlice` or at end of script block)
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing tests**

Append to `test_sensor_points_frontend.py`:

```python
MANDATORY_FUNCS = [
    "addSensorPoint",
    "deleteSensorPoint",
    "setSensorEnabled",
    "setSensorTempUnit",
    "_updateSensorListUI",
]


def test_crud_functions_exist():
    for fn in MANDATORY_FUNCS:
        body = extract_fn_body(fn)
        assert body is not None, f"window.{fn} must be defined"


def test_mutations_call_checkpoint_and_log():
    """Every state-mutating window.* fn MUST pushUndoCheckpoint first
    and logOperation after (CLAUDE.md mandatory rule)."""
    for fn in ["addSensorPoint", "deleteSensorPoint",
               "setSensorEnabled"]:
        body = extract_fn_body(fn)
        assert body is not None, f"{fn} must be defined"
        assert "pushUndoCheckpoint(" in body, \
            f"{fn} must call pushUndoCheckpoint()"
        assert "logOperation(" in body, \
            f"{fn} must call logOperation()"


def test_setsensortempunit_syncs_pptempunit():
    """setSensorTempUnit must write to window._ppTempUnit (mirror)."""
    body = extract_fn_body("setSensorTempUnit")
    assert body is not None
    assert "_ppTempUnit" in body, \
        "setSensorTempUnit must set window._ppTempUnit"


def test_add_sensor_uses_input_unit_scale():
    """addSensorPoint stores xyz in meters by multiplying inputUnit scale."""
    body = extract_fn_body("addSensorPoint")
    assert body is not None
    assert "LENGTH_UNITS" in body or "unitScale" in body, \
        "addSensorPoint must convert inputUnit coords to meters"


def test_list_renderer_shows_checkbox_name_delete():
    """_updateSensorListUI builds rows with checkbox + name + ✕ button."""
    body = extract_fn_body("_updateSensorListUI")
    assert body is not None
    assert "checkbox" in body or "cb" in body
    assert "dblclick" in body
    assert "✕" in body or "&#10005;" in body
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 5 new tests FAIL.

- [ ] **Step 3: Add functions**

Find a place in the main `<script>` block of `STL_generate.html` — suggest near `window.ppEditSlice` / slice-related functions. Insert:

```js
    // ===================== Sensor Points =====================
    // Spec: docs/superpowers/specs/2026-04-18-sensor-points-design.md

    function _sensorNextId() {
        var maxN = 0;
        (state.sensorPoints || []).forEach(function(s) {
            var m = /^sensor_(\d+)$/.exec(s.id || "");
            if (m) maxN = Math.max(maxN, parseInt(m[1], 10));
        });
        return "sensor_" + (maxN + 1);
    }

    function _sensorNameTaken(name, excludeId) {
        return (state.sensorPoints || []).some(function(s) {
            return s.name === name && s.id !== excludeId;
        });
    }

    window.setSensorTempUnit = function(u) {
        if (u !== "K" && u !== "C" && u !== "F") return;
        window._ppTempUnit = u;
        // Mirror to post-processing dropdown if it exists, so both panels stay
        // in sync.
        var ppUnitEl = document.getElementById("pp-temp-unit");
        if (ppUnitEl && ppUnitEl.value !== u) ppUnitEl.value = u;
        // Kick chart redraw if monitor window is open
        if (typeof window._smDrawChart === "function") window._smDrawChart();
        logOperation("sensor", "Set sensor temp unit: " + u, {unit: u}, "view");
    };

    window.addSensorPoint = function() {
        var inputX = parseFloat(document.getElementById("sensor-input-x").value);
        var inputY = parseFloat(document.getElementById("sensor-input-y").value);
        var inputZ = parseFloat(document.getElementById("sensor-input-z").value);
        if (!isFinite(inputX) || !isFinite(inputY) || !isFinite(inputZ)) {
            alert("坐标必须是有限数字");
            return;
        }
        // Convert input units (mm/cm/m/inch) to meters
        var scale = (LENGTH_UNITS[state.inputUnit] || 1);
        var x = inputX * scale, y = inputY * scale, z = inputZ * scale;
        // Bounds check
        if (state.bounds) {
            var b = state.bounds;
            if (x < b.min.x || x > b.max.x ||
                y < b.min.y || y > b.max.y ||
                z < b.min.z || z > b.max.z) {
                alert("点超出房间范围");
                return;
            }
        }
        pushUndoCheckpoint("addSensorPoint");
        if (!state.sensorPoints) state.sensorPoints = [];
        var id = _sensorNextId();
        state.sensorPoints.push({
            id: id, name: id, x: x, y: y, z: z, enabled: true
        });
        window._updateSensorListUI();
        logOperation("sensor", "Added sensor " + id,
                     {x: x, y: y, z: z}, "create");
    };

    window.deleteSensorPoint = function(id) {
        if (!state.sensorPoints) return;
        var idx = state.sensorPoints.findIndex(function(s) { return s.id === id; });
        if (idx < 0) return;
        pushUndoCheckpoint("deleteSensorPoint");
        state.sensorPoints.splice(idx, 1);
        window._updateSensorListUI();
        logOperation("sensor", "Deleted sensor " + id, {id: id}, "delete");
    };

    window.setSensorEnabled = function(id, checked) {
        if (!state.sensorPoints) return;
        var s = state.sensorPoints.find(function(x) { return x.id === id; });
        if (!s) return;
        pushUndoCheckpoint("toggleSensorEnabled");
        s.enabled = !!checked;
        logOperation("sensor", "Toggled sensor " + id + " enabled=" + s.enabled,
                     {id: id, enabled: s.enabled}, "modify");
    };

    window._updateSensorListUI = function() {
        var container = document.getElementById("sensor-list");
        if (!container) return;
        container.innerHTML = "";
        (state.sensorPoints || []).forEach(function(s) {
            var row = document.createElement("div");
            row.style.cssText = "display:flex; align-items:center; gap:4px; padding:2px 0; font-size:0.75rem;";
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = !!s.enabled;
            cb.onchange = function() { window.setSensorEnabled(s.id, cb.checked); };
            var lbl = document.createElement("span");
            lbl.textContent = s.name;
            lbl.title = s.name + " (双击编辑)";
            lbl.style.cssText = "flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text); cursor:pointer;";
            lbl.addEventListener("dblclick", function() {
                window.editSensorPointDialog(s.id);
            });
            var del = document.createElement("button");
            del.textContent = "✕";
            del.style.cssText = "background:none; border:none; color:var(--error-color); cursor:pointer; font-size:0.8rem; padding:0 4px;";
            del.onclick = function() { window.deleteSensorPoint(s.id); };
            row.appendChild(cb);
            row.appendChild(lbl);
            row.appendChild(del);
            container.appendChild(row);
        });
    };
```

Also find `updateUI` or the main post-state-change UI rebuild function and append a call to `window._updateSensorListUI()` (grep for `_ppUpdateSliceListUI()` to find the pattern).

- [ ] **Step 4: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): sensor-point CRUD functions + list renderer

addSensorPoint / deleteSensorPoint / setSensorEnabled / setSensorTempUnit /
_updateSensorListUI. Coords converted input-unit→meters. Bounds-check rejects
points outside state.bounds. Mutations follow pushUndoCheckpoint+logOperation
mandatory rule (CLAUDE.md).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Frontend — edit dialog with name regex + bounds validation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_edit_dialog_exists():
    body = extract_fn_body("editSensorPointDialog")
    assert body is not None


def test_edit_dialog_has_name_regex_check():
    body = extract_fn_body("editSensorPointDialog")
    assert body is not None
    # Spec: /^[A-Za-z0-9_]+$/
    assert re.search(r"\[A-Za-z0-9_\]\+", body) or \
           re.search(r"\[A-Za-z0-9_\]", body), \
        "editSensorPointDialog must validate name with [A-Za-z0-9_]+"


def test_edit_dialog_checks_bounds():
    body = extract_fn_body("editSensorPointDialog")
    assert body is not None
    assert "state.bounds" in body, \
        "editSensorPointDialog must validate against state.bounds"


def test_edit_dialog_calls_editsensorpoint():
    body = extract_fn_body("editSensorPointDialog")
    assert body is not None
    assert "editSensorPoint" in body or "pushUndoCheckpoint" in body


def test_editsensorpoint_mutator_exists():
    body = extract_fn_body("editSensorPoint")
    assert body is not None
    assert "pushUndoCheckpoint" in body
    assert "logOperation" in body
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 5 new tests FAIL.

- [ ] **Step 3: Add edit dialog + mutator**

Add to `STL_generate.html` (near the other sensor functions from Task 6):

```js
    window.editSensorPoint = function(id, patch) {
        if (!state.sensorPoints) return;
        var s = state.sensorPoints.find(function(x) { return x.id === id; });
        if (!s) return;
        pushUndoCheckpoint("editSensorPoint");
        if (patch.name != null) s.name = patch.name;
        if (patch.x != null) s.x = patch.x;
        if (patch.y != null) s.y = patch.y;
        if (patch.z != null) s.z = patch.z;
        window._updateSensorListUI();
        logOperation("sensor", "Edited sensor " + id, patch, "modify");
    };

    function _closeSensorEditDialog() {
        var o = document.getElementById("sensor-edit-overlay");
        if (o && o.parentNode) o.parentNode.removeChild(o);
    }

    window.editSensorPointDialog = function(id) {
        var s = (state.sensorPoints || []).find(function(x) { return x.id === id; });
        if (!s) return;
        _closeSensorEditDialog();

        var scale = (LENGTH_UNITS[state.inputUnit] || 1);
        var uLabel = state.inputUnit || "m";
        var ui = { x: s.x / scale, y: s.y / scale, z: s.z / scale };

        var overlay = document.createElement("div");
        overlay.id = "sensor-edit-overlay";
        overlay.style.cssText = "position:fixed; top:0; left:0; width:0; height:0; z-index:10000; pointer-events:none;";
        var dlg = document.createElement("div");
        dlg.style.cssText = "position:fixed; top:120px; right:280px; width:260px; background:var(--bg-panel); border:1px solid var(--border); padding:10px; pointer-events:auto; box-shadow:0 0 12px rgba(0,0,0,0.4);";
        dlg.innerHTML =
            "<div style='font-size:0.85rem; font-weight:bold; margin-bottom:6px; color:var(--accent);'>Edit " + s.name + "</div>" +
            "<div style='margin:4px 0;'>名字 <input id='sensor-edit-name' value='" + s.name + "' style='width:80%;'></div>" +
            "<div style='margin:4px 0;'>X (" + uLabel + ") <input type='number' id='sensor-edit-x' step='0.01' value='" + ui.x + "' style='width:80%;'></div>" +
            "<div style='margin:4px 0;'>Y (" + uLabel + ") <input type='number' id='sensor-edit-y' step='0.01' value='" + ui.y + "' style='width:80%;'></div>" +
            "<div style='margin:4px 0;'>Z (" + uLabel + ") <input type='number' id='sensor-edit-z' step='0.01' value='" + ui.z + "' style='width:80%;'></div>" +
            "<div id='sensor-edit-err' style='color:#f66; font-size:0.72rem; min-height:14px;'></div>" +
            "<div style='text-align:right; margin-top:6px;'>" +
            "<button id='sensor-edit-cancel' style='margin-right:6px;'>Cancel</button>" +
            "<button id='sensor-edit-apply'>Apply</button>" +
            "</div>";
        overlay.appendChild(dlg);
        document.body.appendChild(overlay);

        document.getElementById("sensor-edit-cancel").onclick = _closeSensorEditDialog;
        document.getElementById("sensor-edit-apply").onclick = function() {
            var err = document.getElementById("sensor-edit-err");
            var newName = document.getElementById("sensor-edit-name").value.trim();
            if (!/^[A-Za-z0-9_]+$/.test(newName)) {
                err.textContent = "名字只能含字母/数字/下划线";
                return;
            }
            if (_sensorNameTaken(newName, id)) {
                err.textContent = "名字已被占用";
                return;
            }
            var nx = parseFloat(document.getElementById("sensor-edit-x").value) * scale;
            var ny = parseFloat(document.getElementById("sensor-edit-y").value) * scale;
            var nz = parseFloat(document.getElementById("sensor-edit-z").value) * scale;
            if (![nx, ny, nz].every(isFinite)) {
                err.textContent = "坐标必须是有限数字";
                return;
            }
            if (state.bounds) {
                var b = state.bounds;
                if (nx < b.min.x || nx > b.max.x ||
                    ny < b.min.y || ny > b.max.y ||
                    nz < b.min.z || nz > b.max.z) {
                    err.textContent = "点超出房间范围";
                    return;
                }
            }
            window.editSensorPoint(id, { name: newName, x: nx, y: ny, z: nz });
            _closeSensorEditDialog();
        };
    };
```

- [ ] **Step 4: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): sensor-point edit dialog + editSensorPoint mutator

Floating non-blocking dialog (pattern copied from ppEditSlice): edit name +
X/Y/Z in current inputUnit. Validates name /[A-Za-z0-9_]+/ unique and xyz
inside state.bounds. Apply triggers editSensorPoint with pushUndoCheckpoint +
logOperation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Frontend — `buildCfdrunJson` emits `sensorPoints[]`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (inside `buildCfdrunJson` ~L28312)
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing test**

Append:

```python
def test_buildcfdrunjson_emits_sensorpoints():
    body = extract_fn_body("buildCfdrunJson")
    assert body is not None
    # Spec: must output sensorPoints field
    assert re.search(r"sensorPoints\s*:", body), \
        "buildCfdrunJson must emit sensorPoints:"
    # Must reference state.sensorPoints as source
    assert "state.sensorPoints" in body, \
        "buildCfdrunJson must read from state.sensorPoints"
```

- [ ] **Step 2: Verify test fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py::test_buildcfdrunjson_emits_sensorpoints -v 2>&1 | tail
```

Expected: FAIL.

- [ ] **Step 3: Modify `buildCfdrunJson`**

Find the return object of `buildCfdrunJson` in `STL_generate.html` (~L28404 region, near `monitoring: window.smGetMonitoringConfig ? ... : {}`). In the returned object, add:

```js
            sensorPoints: (state.sensorPoints || []).map(function(s) {
                // State already stores xyz in meters (see addSensorPoint).
                // Runner expects {x,y,z} keys — include name + enabled for
                // round-tripping to logger JSON, runner ignores unknown keys.
                return { id: s.id, name: s.name,
                         x: s.x, y: s.y, z: s.z, enabled: !!s.enabled };
            }),
```

- [ ] **Step 4: Verify tests pass + runner schema still accepts it**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 15 passed.

Sanity-check the runner accepts extra fields:

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni python -c "
from dcdtuni.runner.cfdproj_schema import SensorPoint
sp = SensorPoint.model_validate({'id':'sensor_1','name':'sensor_1','x':0.5,'y':0.2,'z':1.8,'enabled':True})
print(sp)"
```

Expected: prints the validated model without error (extras silently dropped by Pydantic default config, or accepted).

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): emit sensorPoints[] from buildCfdrunJson

Wires state.sensorPoints into cfdrun output — runner's _extract_sensor_points
will pick up {x,y,z} and feed controlDict probeLocations. Extra keys name/
enabled are for round-tripping to logger JSON (runner ignores unknowns).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Frontend — monitor window 3rd tab "传感器"

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (`_smBuildMonitorWindow` ~L24258+, `_smDrawChart` ~L24548, `_solverMonitor` init ~L24258 + L15117 + L27694)
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_solver_monitor_has_sensors_container():
    """_solverMonitor must initialize a .sensors object alongside
    residuals / temperatures at every init site (3 places in original code)."""
    starts = [m.start() for m in re.finditer(r"_solverMonitor\s*=\s*\{", HTML)]
    assert len(starts) >= 2, f"need >= 2 init sites, got {len(starts)}"
    for pos in starts:
        i = HTML.index("{", pos)
        depth = 0
        end = i
        while end < len(HTML):
            if HTML[end] == "{": depth += 1
            elif HTML[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        block = HTML[i:end + 1]
        assert re.search(r"sensors\s*:", block), \
            f"_solverMonitor init at offset {pos} missing 'sensors:' key"


def test_monitor_window_has_sensor_tab_button():
    assert 'id="sm-tab-sensor"' in HTML, \
        "3rd tab button sm-tab-sensor must be in monitor window"


def test_smdrawchart_has_three_way_datasets():
    body = extract_fn_body("_smDrawChart")
    assert body is not None
    assert "_smActiveTab" in body
    # Must reference .sensors alongside .residuals / .temperatures
    assert ".sensors" in body, "_smDrawChart must read mon.sensors"


def test_tab_switch_covers_sensor():
    """Search for the tab-click handler that sets _smActiveTab; must support
    'sensor' branch."""
    # The click handler sets _smActiveTab via id check
    assert re.search(r"'sensor'|\"sensor\"", HTML), \
        "tab-switch code must reference 'sensor' as _smActiveTab value"
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 4 new tests FAIL.

- [ ] **Step 3: Extend `_solverMonitor` initialization**

Three locations initialize `_solverMonitor`:
1. `window._solverMonitor = {active: false, residuals: {}, temperatures: {}, ...}` (L~15117)
2. `window._solverMonitor = { ... residuals: {}, temperatures: {}, ... }` in a separate block (L~24258)
3. `window._solverMonitor = { ... residuals: {}, temperatures: {}, ... }` in solve-start handler (L~27694)

In ALL three, add `sensors: {},` adjacent to `temperatures: {}`.

- [ ] **Step 4: Add 3rd tab button to `_smBuildMonitorWindow`**

Find `_smBuildMonitorWindow` (~L24258+). Locate `tabBar.innerHTML = '<button id="sm-tab-residual"...'`. The current pattern concatenates buttons. Extend to append a 3rd button AFTER the temperature button:

Change:
```js
tabBar.innerHTML = '<button id="sm-tab-residual" class="sm-tab-btn" ...>...'
```

to include (after the temperature tab button markup):

```js
' <button id="sm-tab-sensor" class="sm-tab-btn" style="padding:3px 12px; font-size:11px; ' +
'background:var(--monitor-tab-inactive-bg); border:1px solid var(--monitor-tab-inactive-border); color:var(--monitor-tab-inactive-text); cursor:pointer; border-radius:3px;">传感器</button>'
```

(Grep around line 24336 to see the exact existing concatenation style and match indentation.)

- [ ] **Step 5: Extend tab-switch handler**

Find the click handler (~L24521): `window._smActiveTab = this.id === 'sm-tab-residual' ? 'residual' : 'temperature';`

Replace with:

```js
if (this.id === 'sm-tab-residual') window._smActiveTab = 'residual';
else if (this.id === 'sm-tab-sensor') window._smActiveTab = 'sensor';
else window._smActiveTab = 'temperature';
```

- [ ] **Step 6: Extend `_smDrawChart` dataset-selection to 3-way**

Find in `_smDrawChart` (~L24560):

```js
var isResidual = (window._smActiveTab === 'residual');
var datasets = isResidual ? mon.residuals : mon.temperatures;
```

Replace with:

```js
var activeTab = window._smActiveTab;
var isResidual = (activeTab === 'residual');
var datasets;
if (activeTab === 'residual')    datasets = mon.residuals;
else if (activeTab === 'sensor') datasets = mon.sensors || {};
else                             datasets = mon.temperatures;
```

And update the placeholder "waiting" message (same function, `ctx.fillText(isResidual ? t('sm.waiting_residual') : t('sm.waiting_temp'), ...)`) to:

```js
var waitKey = activeTab === 'residual' ? 'sm.waiting_residual'
            : activeTab === 'sensor'   ? 'sm.waiting_sensor'
            :                            'sm.waiting_temp';
ctx.fillText(t(waitKey), lW / 2 - 50, lH / 2);
```

- [ ] **Step 7: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 19 passed.

- [ ] **Step 8: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): add 3rd tab "传感器" to solver monitor floating window

- _solverMonitor.sensors container (3 init sites)
- sm-tab-sensor button in _smBuildMonitorWindow
- Tab-switch extended to 3-way (residual/temperature/sensor)
- _smDrawChart reads mon.sensors when activeTab === 'sensor'

No data yet — poller in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Frontend — `_smSensorPoller` (2s fetch loop + banner boost)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_sensor_poller_exists():
    # Declared as a single object with .start/.stop/.fetchNow
    assert "_smSensorPoller" in HTML, "_smSensorPoller must be declared"
    # Contains .start and .fetchNow methods (loose check)
    assert re.search(r"_smSensorPoller\s*=\s*\{[\s\S]*?start\s*:", HTML) or \
           re.search(r"_smSensorPoller\.start", HTML), \
        "_smSensorPoller.start must be defined"
    assert "fetchNow" in HTML, "_smSensorPoller.fetchNow must exist"


def test_poller_hits_probes_latest_api():
    assert "/api/probes-latest" in HTML


def test_poller_boosted_by_banner_regex():
    """_smParseLogLine or its sibling must match the probes-write banner
    and call fetchNow() as an optional freshness boost."""
    body = extract_fn_body("_smParseLogLine")
    assert body is not None
    # Look for probes banner match + fetchNow call
    assert "writing probe data" in body or "probes write" in body or \
           "writing probes" in body, \
        "_smParseLogLine must match a probes-write banner"
    assert "_smSensorPoller" in body, \
        "_smParseLogLine must invoke _smSensorPoller.fetchNow() on banner"


def test_poller_started_on_solve_and_stopped_on_inactive():
    # Search the file globally — start/stop are called near solve-run / solve-end
    assert "_smSensorPoller.start" in HTML
    assert "_smSensorPoller.stop" in HTML
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 4 new tests FAIL.

- [ ] **Step 3: Add `_smSensorPoller`**

Append near the sensor functions from Task 6:

```js
    // ── Sensor poller: pulls /api/probes-latest periodically during solve ──
    window._smSensorPoller = {
        _timer: null,
        _sensorNames: [],
        _lastStep: -1,
        _inflight: false,

        start: function(opts) {
            this._sensorNames = (opts && opts.sensorNames) || [];
            this._lastStep = -1;
            if (window._solverMonitor) window._solverMonitor.sensors = {};
            if (this._timer) clearInterval(this._timer);
            var self = this;
            this._timer = setInterval(function() { self.fetchNow(); }, 2000);
            self.fetchNow();
        },

        stop: function() {
            if (this._timer) {
                clearInterval(this._timer);
                this._timer = null;
            }
        },

        fetchNow: function() {
            if (this._inflight) return;
            if (!this._sensorNames.length) return;
            var self = this;
            this._inflight = true;
            fetch('/api/probes-latest?since=' + this._lastStep)
                .then(function(r) { return r.json(); })
                .catch(function() { return null; })
                .then(function(data) {
                    self._inflight = false;
                    if (!data || !data.steps) return;
                    var mon = window._solverMonitor;
                    if (!mon) return;
                    if (!mon.sensors) mon.sensors = {};
                    for (var i = 0; i < data.steps.length; i++) {
                        var step = data.steps[i];
                        if (step > self._lastStep) self._lastStep = step;
                        for (var j = 0; j < self._sensorNames.length; j++) {
                            var name = self._sensorNames[j];
                            var sp = (state.sensorPoints || []).find(
                                function(p) { return p.name === name; });
                            if (!sp || !sp.enabled) continue;
                            var tempK = (data.fields && data.fields.T
                                         && data.fields.T[i]) ? data.fields.T[i][j] : null;
                            if (tempK == null) continue;
                            if (!mon.sensors[name]) mon.sensors[name] = [];
                            mon.sensors[name].push({ step: step, value: tempK });
                        }
                    }
                    if (window._smActiveTab === 'sensor' &&
                        typeof window._smDrawChart === 'function') {
                        window._smDrawChart();
                    }
                });
        },
    };
```

- [ ] **Step 4: Hook banner-boost in `_smParseLogLine`**

Find `_smParseLogLine` (~L24749). Inside the body (end, before the function closes), add:

```js
        // Sensor-poller boost: probes FO prints "writing probe data" banner
        // at each writeInterval. Opportunistic immediate fetch for freshness.
        if (line.indexOf("writing probe data") !== -1 ||
            line.indexOf("probes write") !== -1) {
            if (window._smSensorPoller && window._smSensorPoller._timer) {
                window._smSensorPoller.fetchNow();
            }
            return;
        }
```

- [ ] **Step 5: Start/stop poller at solve lifecycle**

Locate where `window._solverMonitor.active = true` is set upon solve start (grep around L27694). IMMEDIATELY after that line, add:

```js
        if (window._smSensorPoller && state.sensorPoints && state.sensorPoints.length) {
            window._smSensorPoller.start({
                sensorNames: state.sensorPoints.map(function(p) { return p.name; }),
            });
        }
```

Locate where `window._solverMonitor.active = false` is set upon solve completion/abort (multiple sites near L27739, L27787, L27795, L27807). After EACH of those, add:

```js
        if (window._smSensorPoller) window._smSensorPoller.stop();
```

- [ ] **Step 6: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 23 passed.

- [ ] **Step 7: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): _smSensorPoller — stream probes data into monitor window

start(sensorNames) kicks off 2s interval polling of /api/probes-latest;
each fetch dispatches per-probe T into _solverMonitor.sensors[name] and
redraws chart when active tab is 'sensor'. Banner-boost: _smParseLogLine
invokes fetchNow() whenever probes FO prints 'writing probe data'.

Started when solve begins + sensorPoints.length > 0; stopped on every
solve end/abort site.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Frontend — post-solve harvest hook + "open latest JSON" button

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Test: `scripts/test_sensor_points_frontend.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_openLatestSensorJson_defined():
    body = extract_fn_body("openLatestSensorJson")
    assert body is not None
    assert "_lastSensorJsonPath" in body, \
        "openLatestSensorJson must read window._lastSensorJsonPath"


def test_harvest_hook_posts_to_api():
    """There must be a fetch('/api/harvest-sensor-results', ...) somewhere
    in the script (called when solve completes)."""
    assert "/api/harvest-sensor-results" in HTML
    assert re.search(
        r"fetch\(\s*['\"]\/api\/harvest-sensor-results['\"]", HTML), \
        "harvest endpoint must be POST-fetched from frontend"


def test_harvest_hook_passes_sensor_names_and_unit():
    """Body JSON must contain caseName, sensorNames, unitTemperature keys."""
    # locate the fetch call region
    idx = HTML.find("'/api/harvest-sensor-results'")
    if idx < 0:
        idx = HTML.find('"/api/harvest-sensor-results"')
    assert idx >= 0
    frag = HTML[idx: idx + 1500]
    assert "caseName" in frag
    assert "sensorNames" in frag
    assert "unitTemperature" in frag
```

- [ ] **Step 2: Verify tests fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 3 new tests FAIL.

- [ ] **Step 3: Add `window.openLatestSensorJson`**

Append near sensor functions:

```js
    window._lastSensorJsonPath = null;

    window.openLatestSensorJson = function() {
        if (!window._lastSensorJsonPath) {
            alert("尚无已保存的 sensor JSON — 先运行一次求解");
            return;
        }
        // /api/read-file returns the file body so browser can save-as or show
        var u = '/api/read-file?path=' + encodeURIComponent(window._lastSensorJsonPath);
        window.open(u, '_blank');
    };
```

- [ ] **Step 4: Add harvest hook**

Locate the solve-completion site where `window._solverMonitor.active = false` is set on normal completion (grep around L27739). AFTER the existing `_smSensorPoller.stop()` call added in Task 10, add:

```js
        // Trigger post-solve sensor JSON harvest on normal completion
        if (state.sensorPoints && state.sensorPoints.length > 0) {
            fetch('/api/harvest-sensor-results', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    caseName: (state.dctwinConfig && state.dctwinConfig.caseName) || 'case',
                    sensorNames: state.sensorPoints.map(function(p) { return p.name; }),
                    unitTemperature: window._ppTempUnit || 'C',
                })
            }).then(function(r) { return r.json(); })
              .then(function(r) {
                  if (r && r.path) {
                      window._lastSensorJsonPath = r.path;
                      logOperation('solve', 'Sensor final values saved: ' + r.path,
                                   { path: r.path }, 'file');
                  }
              })
              .catch(function(e) {
                  logOperation('solve', 'Sensor JSON save failed: ' + (e && e.message),
                               {}, 'error');
              });
        }
```

(Only attach this to the NORMAL-completion site, not abort sites — we don't want to dump possibly-empty probes on abort.)

- [ ] **Step 5: Verify tests pass**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py -v 2>&1 | tail
```

Expected: 26 passed.

- [ ] **Step 6: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html scripts/test_sensor_points_frontend.py
git commit -m "$(cat <<'EOF'
feat(ui): post-solve sensor JSON harvest + open-latest button

On normal solve completion with >0 sensor points: POST
/api/harvest-sensor-results with {caseName, sensorNames, unitTemperature},
server returns path to <case>/sensor_results/<YYYY-MM-DD>/*.json, cached in
window._lastSensorJsonPath. 📂 button opens it via /api/read-file.
Abort paths skip harvest (probes may be empty).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Final regression sweep + submodule bump

**Files:**
- Parent repo: no changes, only submodule pointer bump
- Regression: run all existing refinement + door tests

- [ ] **Step 1: Run all structural + backend tests together**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest \
    STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py \
    STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py \
    STLGeneratorForOpenFoam/scripts/test_refinement_redesign_runtime.py \
    STLGeneratorForOpenFoam/server/tests/test_probes_api.py \
    src/backend/aircooling/runner/tests/test_refinement_redesign.py \
    src/backend/aircooling/runner/tests/test_rack_door_refinement.py \
    -v 2>&1 | tail -30
```

Expected: all pass (26 sensor + ~66 refinement + 12 server = 104+ tests).

- [ ] **Step 2: Submodule status check**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && git status --short && git -C STLGeneratorForOpenFoam log --oneline -6
```

Expected: `M STLGeneratorForOpenFoam` in parent repo (submodule has new commits); STLGen has 6 new commits from Tasks 1–11.

- [ ] **Step 3: Parent repo submodule bump commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && git add STLGeneratorForOpenFoam && git commit -m "$(cat <<'EOF'
chore(submodule): bump STLGen — sensor points feature

Full sensor-point workflow landed:
- UI: 📍 传感器位点 prop-group in 求解 tab right sidebar
- State: state.sensorPoints[] + CRUD + edit dialog + bounds/name validation
- Monitor: 3rd tab "传感器" with _smSensorPoller streaming /api/probes-latest
- Server: parse_probes_file + /api/probes-latest + /api/harvest-sensor-results
- Harvest: final-step JSON at case/sensor_results/YYYY-MM-DD/*.json

Spec: docs/superpowers/specs/2026-04-18-sensor-points-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Manual E2E (OpenFOAM required; user-driven, not in plan loop)**

Checklist for user (no auto-verify):
- [ ] On CMI-China or base604 case: open STL_generate.html, switch to 求解 tab
- [ ] Add 2 sensor points at known coordinates (e.g. one near a rack, one mid-aisle)
- [ ] Verify 📍 panel shows both in list with ✕ delete buttons
- [ ] Double-click one entry → rename in dialog → Apply → list shows new name
- [ ] Click "执行求解"
- [ ] Open solver monitor floating window → switch to 3rd tab "传感器" → verify curves appear within 10–20 s (after first writeInterval)
- [ ] Toggle temperature unit K↔°C↔°F at top of sensor panel → confirm curves + list labels redraw
- [ ] Wait for solve to finish → check `<case>/sensor_results/<YYYY-MM-DD>/<caseName>_sensor_<HH-MM-SS>.json` exists with correct schema
- [ ] Click 📂 打开最近 sensor JSON → file opens in new tab

---

## Task Dependency Graph

```
T1 (parser)
 ├─→ T2 (probes-latest endpoint)
 └─→ T3 (harvest endpoint)

T4 (state) ──→ T5 (UI DOM) ──→ T6 (CRUD) ──→ T7 (edit dialog)
                                 │
                                 └─→ T8 (buildCfdrunJson)

T9 (3rd tab) ──→ T10 (poller, needs T2 for API + T9 for container)
T11 (harvest hook, needs T3 for API + T10 for lifecycle hook point)

T12 = regression sweep + submodule bump (after all 11)
```

Suggested serial order: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12.
