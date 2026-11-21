# cfdrun Naming Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate residual `cfd_export.json` / `export_cfd` / `--from-stlgen` references across `dcdtuni` + its `STLGeneratorForOpenFoam` submodule so the vocabulary matches the `.cfdrun` runtime-file contract, with no silent fallbacks.

**Architecture:** Four orthogonal edit layers: (1) skill documentation rewrite — the source of the misleading vocabulary for new agents; (2) server-side dual-key body reading + endpoint alias during a one-bump deprecation window; (3) frontend atomic rename of `buildCfdExportJson` + local vars + POST body key (with a window-level alias to cover in-flight bookmarklets); (4) runner internal rename `cfd_export_path → cfdrun_path` with a hard `FileNotFoundError` when no `.cfdrun` exists (replacing the previous `cfd_export.json` fallback). Tests interleave.

**Tech Stack:** Python 3.10 (conda env `dcdtuni`), pytest, `http.server`-based REST backend (`server.py`), Three.js r128 SPA (`STL_generate.html`).

---

## File Structure

**Files modified:**
- `STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md` — full rewrite (Task 1).
- `AGENTS.md` — lines 876, 880 (Task 2).
- `STLGeneratorForOpenFoam/server/server.py` — docstring at L754 (Task 2), dual-key reads at L2447+L2511 + endpoint alias + worker fn alias (Tasks 3–4).
- `STLGeneratorForOpenFoam/web/STL_generate.html` — rename `buildCfdExportJson`, local vars, POST body key at L26907–27143 (Task 6).
- `src/backend/aircooling/runner/stlgen_runner.py` — `STLGenRunner.__init__` param+attr rename + default-raise (Task 8), docstring sweep (Task 11).
- `src/backend/aircooling/runner/bridge_bc.py`, `liquid_runner.py`, `fields.py`, `cfdproj_schema.py` — docstring sweep (Task 11).

**Test files created/modified:**
- `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py` — add `buildCfdrunJson` structural assertion (Task 5).
- `src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py` — **new** file, server dual-key test (Task 3).
- `src/backend/aircooling/runner/tests/test_stlgen_runner_cfdrun_path.py` — **new** file, raise-on-missing test (Task 7).
- `src/backend/aircooling/runner/tests/test_cleanup_preserves_cfd_export.py` — update kwarg from `cfd_export_path=` to `cfdrun_path=` (Task 9).
- `src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py`, `test_partition_hole_fvoptions.py`, `test_stlgen_validation.py`, `test_rack_door_refinement.py`, `test_refinement_redesign.py` — update kwarg (Task 9).

**Files deliberately NOT touched** (see spec §Non-goals):
- `src/backend/aircooling/runner/cfdproj_schema.py` — class name `CFDExport` stays.
- Historical spec docs under `AGENTS/specs/2026-04-09*` and `2026-04-10*` — frozen.
- `STLGeneratorForOpenFoam/scripts/stl_browser_debug.py`, `integrate_v5_cfd_station.py` — one-off scripts.

---

## Test Infrastructure Notes

- **Python tests:** run with `conda run -n dcdtuni pytest <path>`. Tests live next to the code under `src/backend/aircooling/runner/tests/`.
- **Frontend structural tests:** regex-based static scans of `STL_generate.html`; live in `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`. Run with `conda run -n dcdtuni pytest <file>`.
- **E2E verification:** after Task 10, run the headless-browser pipeline at the end of Task 10 to confirm the full chain `buildCfdrunJson → POST cfdrunJson → .cfdrun file written → STLGenRunner loads`.
- **Each Python test starts with `from pathlib import Path; import json, pytest`** and uses `tmp_path` pytest fixture.

---

## Task 1: Rewrite `api-debug-testing.md` (no code, no tests)

**Files:**
- Modify (full rewrite): `STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md`

- [ ] **Step 1: Read the current file to preserve non-legacy sections**

Run: `Read STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md`
Keep: server-startup instructions (L15–35), screenshot endpoint docs, Omniverse references.
Replace: all occurrences of `cfd_export.json`, `/api/browser/export-cfd`, `cfdExportJson`, `--from-stlgen`, `<case_dir>/model/cfd_export.json`.

- [ ] **Step 2: Write the updated skill doc**

Target vocabulary:
- "Export the `.cfdrun` manifest" instead of "export `cfd_export.json`".
- Primary endpoint `/api/browser/export-cfdrun`; mention `/api/browser/export-cfd` as a deprecated alias in a single line under "API 端点全览".
- Primary POST body key `cfdrunJson`; mention `cfdExportJson` only in a "Backward compatibility (deprecated)" note.
- CLI flag `--from-cfdrun` (the only supported flag).
- File location pattern `<case>/model/<basename>.cfdrun` (glob, not a fixed filename).
- Concrete curl example:

```bash
curl -s -X POST http://localhost:8000/api/run-mesh \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
cdj = open('<case_dir>/model/Base_leakage.cfdrun').read()
print(json.dumps({
    'cfdrunJson': cdj,
    'cfdrunFilename': 'Base_leakage.cfdrun',
    'stlFiles': {},
    'meshOnly': True,
    'solveOnly': False,
}))")"
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add .claude/skills/api-debug-testing.md
git commit -m "docs(skill): rewrite api-debug-testing to .cfdrun vocabulary"
```

---

## Task 2: Fix stale references in `AGENTS.md` + `server.py` docstring

**Files:**
- Modify: `AGENTS.md:876,880`
- Modify: `STLGeneratorForOpenFoam/server/server.py:754`

- [ ] **Step 1: Fix AGENTS.md checklist items**

Old (L876, L880):
```
- [ ] Add `--from-stlgen` CLI argument to `run_cfd.py`
…
- [ ] Run `--from-stlgen` path → mesh generation
```

New:
```
- [x] Add `--from-cfdrun` CLI argument to `run_cfd.py` (replaces `--from-stlgen`, completed 2026-04-11)
…
- [x] Run `--from-cfdrun` path → mesh generation (completed 2026-04-11)
```

- [ ] **Step 2: Fix server.py:754 docstring**

Old:
```python
       `--from-stlgen cfd_export.json`.
```

New:
```python
       `--from-cfdrun <basename>.cfdrun` (content identical to the pre-2026-04-11
       `cfd_export.json` manifest; only the filename changed).
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add server/server.py
git commit -m "docs(server): update stale --from-stlgen reference in handle_prepare_case docstring"
cd ..
git add AGENTS.md
git commit -m "docs(AGENTS): mark --from-stlgen→--from-cfdrun migration as completed"
```

---

## Task 3: Server dual-key POST body reading (test-first)

**Files:**
- Create: `src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py`
- Modify: `STLGeneratorForOpenFoam/server/server.py:2447, 2511`

- [ ] **Step 1: Write the failing test**

```python
"""Dual-key POST body acceptance: cfdrunJson (primary) + cfdExportJson (legacy)."""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

SERVER_PATH = Path(__file__).resolve().parents[4] / "STLGeneratorForOpenFoam" / "server" / "server.py"


def _load_server_module():
    # server.py is a __main__-style script; load it fresh under a unique name.
    spec = importlib.util.spec_from_file_location("stlgen_server_under_test", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Prevent sys.exit / arg parsing side effects by stubbing argparse before exec.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeHandler:
    def __init__(self, body_bytes: bytes):
        self._body = body_bytes
        self.headers = {"Content-Length": str(len(body_bytes))}
        self.rfile = types.SimpleNamespace(read=lambda n: self._body[:n])
        self.wfile_writes = []
        self.status = None
        self.sent_headers = {}

    # Minimal BaseHTTPRequestHandler surface needed by _read_body/_send_json.
    def send_response(self, code): self.status = code
    def send_header(self, k, v): self.sent_headers[k] = v
    def end_headers(self): pass
    class _wfile:
        def __init__(self, out): self.out = out
        def write(self, b): self.out.append(b)
    @property
    def wfile(self):
        if not hasattr(self, "_wf"):
            self._wf = self._wfile(self.wfile_writes)
        return self._wf


@pytest.fixture
def server_mod():
    return _load_server_module()


def _read_response(handler):
    return json.loads(b"".join(handler.wfile_writes).decode("utf-8"))


def test_prepare_case_accepts_new_cfdrunJson_key(server_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    body = json.dumps({
        "cfdrunJson": json.dumps({"room": {"height": 3.0}, "meshConfig": {}}),
        "cfdrunFilename": "demo.cfdrun",
        "stlFiles": {},
    }).encode("utf-8")
    handler = _FakeHandler(body)
    server_mod.handle_prepare_case(handler)
    resp = _read_response(handler)
    assert handler.status == 200, f"expected 200, got {handler.status}: {resp}"
    assert "command" in resp
    assert (tmp_path / "model" / "demo.cfdrun").exists()


def test_prepare_case_accepts_legacy_cfdExportJson_key_with_deprecation(
    server_mod, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(server_mod, "CASE_DIR", tmp_path)
    body = json.dumps({
        "cfdExportJson": json.dumps({"room": {"height": 3.0}, "meshConfig": {}}),
        "cfdrunFilename": "demo.cfdrun",
        "stlFiles": {},
    }).encode("utf-8")
    handler = _FakeHandler(body)
    with caplog.at_level("WARNING"):
        server_mod.handle_prepare_case(handler)
    resp = _read_response(handler)
    assert handler.status == 200, f"expected 200 on legacy key, got {handler.status}: {resp}"
    assert (tmp_path / "model" / "demo.cfdrun").exists()
    # Deprecation must be logged SOMEWHERE (warnings module or print) so agents
    # driving the API see the migration hint.
    assert any("cfdExportJson" in rec.message and "deprecated" in rec.message.lower()
               for rec in caplog.records), f"expected deprecation warning, got {[r.message for r in caplog.records]}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py -v`
Expected: FAIL — either `cfdrunJson` isn't read (first test) or no deprecation warning on `cfdExportJson` (second test).

- [ ] **Step 3: Implement dual-key acceptance in `handle_prepare_case`**

Replace L2447:
```python
    cfd_export_json: str | None = data.get("cfdExportJson", None)
```

With:
```python
    # 2026-04-18 rename: primary body key is `cfdrunJson`. `cfdExportJson` is
    # accepted for one submodule-bump window of backward compatibility; any use
    # of it logs a deprecation warning so API-driving agents see the migration
    # hint on the very first call.
    cfd_export_json: str | None = data.get("cfdrunJson", None)
    if cfd_export_json is None and data.get("cfdExportJson") is not None:
        import logging
        logging.getLogger(__name__).warning(
            "POST body used deprecated key 'cfdExportJson'; rename to 'cfdrunJson'. "
            "This alias will be removed in the next cleanup pass."
        )
        cfd_export_json = data.get("cfdExportJson")
```

- [ ] **Step 4: Apply the same change in `handle_run_mesh`**

Replace L2511 (same block as Step 3).

- [ ] **Step 5: Run the test to verify it passes**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py -v`
Expected: PASS on both tests.

- [ ] **Step 6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add server/server.py
git commit -m "feat(server): accept cfdrunJson as primary POST key, deprecate cfdExportJson"
cd ..
git add src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py
git commit -m "test(server): dual-key POST body acceptance for cfdrun rename"
```

---

## Task 4: Server endpoint alias `/api/browser/export-cfdrun` + worker fn alias

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py:932, 1999–2008, 3240–3241`

- [ ] **Step 1: Write the failing test (extend the Task 3 test file)**

Append to `src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py`:

```python
def test_export_cfdrun_endpoint_exists(server_mod):
    """Primary export endpoint is /api/browser/export-cfdrun; /api/browser/export-cfd
    remains as an alias for the deprecation window."""
    # Route dispatch happens in STLServerHandler.do_POST; grep the handler source
    # for the literal route-string registration as a structural check.
    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "/api/browser/export-cfdrun" in source, \
        "new primary endpoint /api/browser/export-cfdrun must be registered"
    assert "/api/browser/export-cfd" in source, \
        "legacy alias /api/browser/export-cfd must still be registered"


def test_browser_worker_accepts_export_cfdrun_content_fn_name(server_mod):
    """Browser worker dispatches on fn_name 'export_cfdrun_content' (primary)
    and 'export_cfd_json' (alias)."""
    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'fn_name == "export_cfdrun_content"' in source, \
        "worker must dispatch on new fn_name 'export_cfdrun_content'"
    assert 'fn_name == "export_cfd_json"' in source, \
        "worker must keep dispatching on legacy fn_name 'export_cfd_json'"
```

- [ ] **Step 2: Run to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py::test_export_cfdrun_endpoint_exists src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py::test_browser_worker_accepts_export_cfdrun_content_fn_name -v`
Expected: FAIL — neither new string appears in server.py.

- [ ] **Step 3: Add the worker fn alias**

At L932 of `server.py`, change:
```python
            elif fn_name == "export_cfd_json":
```

To:
```python
            elif fn_name in ("export_cfdrun_content", "export_cfd_json"):
                # 2026-04-18 rename: primary name is `export_cfdrun_content`.
                # Legacy `export_cfd_json` accepted for a deprecation window.
```

- [ ] **Step 4: Add the route alias**

At L3240 of `server.py`, change:
```python
        elif parsed.path == "/api/browser/export-cfd":
            handle_browser_export_cfd(self)
```

To:
```python
        elif parsed.path in ("/api/browser/export-cfdrun", "/api/browser/export-cfd"):
            # 2026-04-18 rename: primary route is /api/browser/export-cfdrun.
            # /api/browser/export-cfd kept as a silent alias during the
            # deprecation window (no warn — the endpoint is semantically
            # identical, just renamed).
            handle_browser_export_cfd(self)
```

- [ ] **Step 5: Update the handler's docstring to reflect both routes**

At L1999–2000:
```python
def handle_browser_export_cfd(handler) -> None:
    """POST /api/browser/export-cfdrun (or legacy /api/browser/export-cfd alias)."""
```

- [ ] **Step 6: Run to verify the tests pass**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
cd STLGeneratorForOpenFoam
git add server/server.py
git commit -m "feat(server): add /api/browser/export-cfdrun endpoint + export_cfdrun_content worker alias"
cd ..
git add src/backend/aircooling/runner/tests/test_cfdrun_dual_key_body.py
git commit -m "test(server): structural assertions for cfdrun endpoint+worker aliases"
```

---

## Task 5: Frontend structural test for `buildCfdrunJson`

**Files:**
- Modify: `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

- [ ] **Step 1: Write the failing test**

Append to the existing file:

```python
def test_buildCfdrunJson_is_defined_with_cfdExportJson_alias():
    """The primary export function is buildCfdrunJson. The legacy name
    buildCfdExportJson must still resolve (window.buildCfdExportJson =
    buildCfdrunJson) so old bookmarklets / external scripts keep working
    during the deprecation window."""
    # HTML is already loaded into module-level HTML constant by the
    # existing test harness at the top of this file.
    body = extract_fn_body("buildCfdrunJson")
    assert body is not None, "buildCfdrunJson() must be defined"
    # Alias must be a simple assignment, not a re-implementation.
    assert re.search(r"window\.buildCfdExportJson\s*=\s*buildCfdrunJson",
                     HTML), "window.buildCfdExportJson must alias buildCfdrunJson"


def test_cfdrunJson_is_the_post_body_key():
    """POST body key sent to /api/run-mesh and /api/prepare-case is cfdrunJson.
    cfdExportJson may still appear as a legacy alias, but cfdrunJson must be
    the primary key literally present in the fetch payloads."""
    # Count POST body sites; there are two (runMeshGeneration + runSolve).
    matches = re.findall(r"cfdrunJson\s*:\s*cfdrunJson\b", HTML)
    assert len(matches) >= 2, \
        f"expected cfdrunJson: cfdrunJson in ≥2 POST sites, got {len(matches)}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py::test_buildCfdrunJson_is_defined_with_cfdExportJson_alias STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py::test_cfdrunJson_is_the_post_body_key -v`
Expected: FAIL — `buildCfdrunJson` isn't defined yet.

- [ ] **Step 3: Commit (test only — fails intentionally pending Task 6)**

```bash
git add STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py
git commit -m "test(frontend): structural assertions for buildCfdrunJson + POST key rename (xfail until impl)"
```

---

## Task 6: Frontend rename `buildCfdExportJson → buildCfdrunJson` + POST body key

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:27781 (fn def), 26907, 26911–26912, 27004, 27124, 27127–27128, 27143`

- [ ] **Step 1: Rename the function definition**

At L27781, change:
```javascript
    function buildCfdExportJson() {
```

To:
```javascript
    function buildCfdrunJson() {
```

- [ ] **Step 2: Update both POST-site local vars + body keys**

At L26907–26912 and L27124–27128, change (both occurrences):
```javascript
        let cfdExportJson;
        try {
            exportData = buildCfdExportJson();
            cfdExportJson = JSON.stringify(exportData);
```

To:
```javascript
        let cfdrunJson;
        try {
            exportData = buildCfdrunJson();
            cfdrunJson = JSON.stringify(exportData);
```

At L27004 and L27143, change (both occurrences):
```javascript
                    cfdExportJson: cfdExportJson,
```

To:
```javascript
                    cfdrunJson: cfdrunJson,
```

- [ ] **Step 3: Add the window-level backward-compat alias**

Immediately after the `function buildCfdrunJson() { … }` definition (a few lines before L27800 depending on where the function body ends — place right after the closing `}` of the function):

```javascript
    // 2026-04-18 rename: buildCfdExportJson was renamed to buildCfdrunJson.
    // Keep the old name as a window-level alias so bookmarklets / external
    // scripts calling the legacy name continue to work during the deprecation
    // window. Remove in the next cleanup pass.
    window.buildCfdExportJson = buildCfdrunJson;
    window.buildCfdrunJson = buildCfdrunJson;
```

- [ ] **Step 4: Update the one internal reference in an existing comment**

At L24548, change:
```javascript
    // scanRackDoorMinUnitBuckets (UI) and buildCfdExportJson
```

To:
```javascript
    // scanRackDoorMinUnitBuckets (UI) and buildCfdrunJson
```

- [ ] **Step 5: Update the other internal call site**

At L28312, change:
```javascript
            const cfdData = buildCfdExportJson();
```

To:
```javascript
            const cfdData = buildCfdrunJson();
```

And the comment one line later (L28321):
```javascript
            // stlFile references are already set correctly in buildCfdExportJson()
```

To:
```javascript
            // stlFile references are already set correctly in buildCfdrunJson()
```

- [ ] **Step 6: Run Task 5's tests to verify they pass**

Run: `conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: all 42 tests PASS (40 pre-existing + 2 new from Task 5).

- [ ] **Step 7: Run the full frontend test file to catch regressions**

Run: `conda run -n dcdtuni pytest STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py -v`
Expected: full green.

- [ ] **Step 8: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "refactor(ui): rename buildCfdExportJson → buildCfdrunJson + POST body key cfdrunJson"
```

---

## Task 7: Runner test — raise `FileNotFoundError` on missing `.cfdrun`

**Files:**
- Create: `src/backend/aircooling/runner/tests/test_stlgen_runner_cfdrun_path.py`

- [ ] **Step 1: Write the failing test**

```python
"""STLGenRunner default-path contract: raises FileNotFoundError when no
.cfdrun exists in model/, with NO silent fallback to legacy cfd_export.json.

Policy source: docs/superpowers/specs/2026-04-17-cfdrun-naming-cleanup-design.md
"""
import json
from pathlib import Path

import pytest

from dcdtuni.runner.stlgen_runner import STLGenRunner


@pytest.fixture
def empty_case(tmp_path: Path) -> Path:
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_default_init_raises_when_no_cfdrun_exists(empty_case):
    """No .cfdrun in model/ → FileNotFoundError on load, even if a legacy
    cfd_export.json is present. The legacy file must NOT act as a silent
    fallback (spec §Data flow + the 2026-04-18 no-silent-fallback policy)."""
    # Simulate a pre-refactor rollback scenario: legacy file present.
    (empty_case / "model" / "cfd_export.json").write_text(
        json.dumps({"room": {"height": 3.0}, "meshConfig": {}}), encoding="utf-8"
    )
    runner = STLGenRunner(empty_case)
    with pytest.raises(FileNotFoundError, match=r"no .*\.cfdrun"):
        runner.validate_geometry()  # triggers _load_cfd_json_only


def test_default_init_discovers_cfdrun_by_glob(empty_case):
    """When a *.cfdrun file exists in model/, the runner auto-picks it."""
    cfdrun_content = json.dumps({
        "room": {"height": 3.0, "plane": [], "locationInMesh": {"x": 0, "y": 0, "z": 1.5}},
        "meshConfig": {"baseSize": 0.2, "maxGlobalCells": 1000000,
                       "blockMeshBounds": {"min": {"x": 0, "y": 0, "z": 0},
                                           "max": {"x": 1, "y": 1, "z": 3}}},
        "floors": {},
    })
    (empty_case / "model" / "Base_demo.cfdrun").write_text(cfdrun_content, encoding="utf-8")
    runner = STLGenRunner(empty_case)
    # Should pick up Base_demo.cfdrun automatically via glob.
    assert runner.cfdrun_path.name == "Base_demo.cfdrun"


def test_multiple_cfdrun_files_raises(empty_case):
    """Ambiguous state: >1 .cfdrun in model/ → RuntimeError so the user
    explicitly passes the right one rather than getting a glob coin-flip."""
    (empty_case / "model" / "A.cfdrun").write_text("{}", encoding="utf-8")
    (empty_case / "model" / "B.cfdrun").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"multiple.*\.cfdrun"):
        STLGenRunner(empty_case)


def test_explicit_cfdrun_path_overrides_glob(empty_case):
    """Passing cfdrun_path= bypasses the glob — explicit path wins."""
    (empty_case / "model" / "Ambiguous.cfdrun").write_text("{}", encoding="utf-8")
    elsewhere = empty_case / "model" / "Chosen.cfdrun"
    elsewhere.write_text(
        json.dumps({"room": {"height": 3.0}, "meshConfig": {}}),
        encoding="utf-8",
    )
    runner = STLGenRunner(empty_case, cfdrun_path=elsewhere)
    assert runner.cfdrun_path == elsewhere.resolve()
```

- [ ] **Step 2: Run to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_stlgen_runner_cfdrun_path.py -v`
Expected: all four tests FAIL — current `STLGenRunner.__init__` uses a silent `cfd_export.json` default and has no `cfdrun_path` kwarg.

- [ ] **Step 3: Commit (test only)**

```bash
git add src/backend/aircooling/runner/tests/test_stlgen_runner_cfdrun_path.py
git commit -m "test(runner): STLGenRunner cfdrun_path contract — raise on missing/ambiguous"
```

---

## Task 8: Runner rename `cfd_export_path → cfdrun_path` + default-raise

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:674–682, 718–726, 1410–1416`

- [ ] **Step 1: Update `STLGenRunner.__init__`**

Replace L674–690:
```python
    def __init__(self, case_dir: Path, cfd_export_path: Optional[Path] = None) -> None:
        self.case_dir = Path(case_dir).resolve()
        self.mesh_dir = self.case_dir / "Mesh"
        self.model_dir = self.case_dir / "model"
        self.tri_surface_dir = self.mesh_dir / "constant" / "triSurface"

        if cfd_export_path is None:
            cfd_export_path = self.model_dir / "cfd_export.json"
        self.cfd_export_path = Path(cfd_export_path).resolve()

        self._data: Optional[Dict[str, Any]] = None   # parsed cfd_export.json
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()
        # In-memory bridge BC registry; populated by _build_bridge_bc() during
        # _prepare_runtime_metadata(). Replaces the old model/bridge_bc.json file.
        self._bridge_registry: Optional["BridgeBCRegistry"] = None
```

With:
```python
    def __init__(self, case_dir: Path, cfdrun_path: Optional[Path] = None) -> None:
        """Construct a runner for `case_dir`.

        `cfdrun_path` — explicit path to the `.cfdrun` manifest (JSON content,
        identical format to the pre-2026-04-11 `cfd_export.json`). If omitted,
        the runner globs `<case_dir>/model/*.cfdrun`:

        - exactly one `.cfdrun` found → used;
        - zero `.cfdrun` found → `FileNotFoundError` (NO silent fallback to
          the legacy `cfd_export.json` — deliberate per the 2026-04-18
          no-silent-fallback policy);
        - two or more `.cfdrun` files → `RuntimeError` asking the user to
          pass `cfdrun_path=` explicitly.
        """
        self.case_dir = Path(case_dir).resolve()
        self.mesh_dir = self.case_dir / "Mesh"
        self.model_dir = self.case_dir / "model"
        self.tri_surface_dir = self.mesh_dir / "constant" / "triSurface"

        if cfdrun_path is None:
            candidates = sorted(self.model_dir.glob("*.cfdrun"))
            if len(candidates) == 0:
                raise FileNotFoundError(
                    f"no *.cfdrun file found in {self.model_dir}. Re-export "
                    f"from the STL UI (or /api/browser/export-stl + "
                    f"/api/browser/export-cfdrun), or pass cfdrun_path= "
                    f"explicitly. Note: the legacy cfd_export.json is not "
                    f"auto-loaded — use the pre-2026-04-11 `main` branch if "
                    f"you need to run a rolled-back checkout."
                )
            if len(candidates) > 1:
                raise RuntimeError(
                    f"multiple *.cfdrun files in {self.model_dir}: "
                    f"{[c.name for c in candidates]}. Pass cfdrun_path= "
                    f"explicitly to disambiguate."
                )
            cfdrun_path = candidates[0]
        self.cfdrun_path = Path(cfdrun_path).resolve()

        self._data: Optional[Dict[str, Any]] = None   # parsed .cfdrun manifest
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()
        # In-memory bridge BC registry; populated by _build_bridge_bc() during
        # _prepare_runtime_metadata(). Replaces the old model/bridge_bc.json file.
        self._bridge_registry: Optional["BridgeBCRegistry"] = None
```

- [ ] **Step 2: Update `_load_cfd_json_only` to use the new attribute name**

At L720–722, replace:
```python
        if not self.cfd_export_path.exists():
            raise FileNotFoundError(f"cfdrun input file not found: {self.cfd_export_path}")
        with open(self.cfd_export_path, encoding="utf-8") as fh:
```

With:
```python
        if not self.cfdrun_path.exists():
            raise FileNotFoundError(f"cfdrun input file not found: {self.cfdrun_path}")
        with open(self.cfdrun_path, encoding="utf-8") as fh:
```

- [ ] **Step 3: Update `_load` (full-load path) to use the new attribute name**

At L1410–1416, replace the three `self.cfd_export_path` references with `self.cfdrun_path`.

- [ ] **Step 4: Run the Task 7 tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_stlgen_runner_cfdrun_path.py -v`
Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "refactor(runner): rename cfd_export_path → cfdrun_path, raise on missing/ambiguous"
```

---

## Task 9: Update existing test call sites to use `cfdrun_path=` kwarg

**Files:**
- Modify: `src/backend/aircooling/runner/tests/test_cleanup_preserves_cfd_export.py:50, 73`
- Modify: `src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py:104`
- Modify: `src/backend/aircooling/runner/tests/test_partition_hole_fvoptions.py:95, 130, 149, 177, 202, 228, 256, 278, 383`
- Modify: `src/backend/aircooling/runner/tests/test_stlgen_validation.py:36`
- Modify: `src/backend/aircooling/runner/tests/test_rack_door_refinement.py:170`
- Modify: `src/backend/aircooling/runner/tests/test_refinement_redesign.py:172`

- [ ] **Step 1: Rename all `cfd_export_path=` kwargs to `cfdrun_path=`**

Use a single `sed` pass across the test files:

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
grep -l "cfd_export_path=" src/backend/aircooling/runner/tests/ | xargs sed -i 's/cfd_export_path=/cfdrun_path=/g'
```

- [ ] **Step 2: Check no `cfd_export_path=` remains**

Run: `grep -r "cfd_export_path=" src/backend/`
Expected: no output.

- [ ] **Step 3: Run the full runner test suite**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -x --tb=short`
Expected: no test regresses because of this rename. (The 4 pre-existing `partition_hole_fvoptions` failures are unrelated; verify the failure count is still 4, not 5+.)

- [ ] **Step 4: Commit**

```bash
git add src/backend/aircooling/runner/tests/
git commit -m "test(runner): rename cfd_export_path kwarg to cfdrun_path across test files"
```

---

## Task 10: Update `run_cfd.py` positional call

**Files:**
- Modify: `src/backend/aircooling/runner/run_cfd.py:76`

- [ ] **Step 1: Update the positional argument variable name**

The call site already uses positional form so no kwarg to rename, but the local variable `cfdrun_path` on L62 was already correct. Only thing to confirm is the CLI integration end-to-end still works. Run:

```bash
conda run -n dcdtuni python -c "
from dcdtuni.runner.stlgen_runner import STLGenRunner
from pathlib import Path
# Sanity: constructor signature has cfdrun_path, not cfd_export_path
import inspect
sig = inspect.signature(STLGenRunner.__init__)
assert 'cfdrun_path' in sig.parameters, f'missing cfdrun_path: {sig}'
assert 'cfd_export_path' not in sig.parameters, f'cfd_export_path still present: {sig}'
print('signature OK:', sig)
"
```
Expected: `signature OK: (self, case_dir: pathlib.Path, cfdrun_path: Optional[pathlib.Path] = None) -> None`

- [ ] **Step 2: End-to-end via headless UI**

Run the following to exercise the whole chain `buildCfdrunJson → POST cfdrunJson → .cfdrun written → STLGenRunner globs it`:

```bash
# Server should already be running on port 8025 from earlier tasks; if not, start it.
fuser 8025/tcp 2>&1 || (
  cd /mnt/lv/litianyu/mycode/dcdtuni
  nohup conda run -n dcdtuni --no-capture-output python STLGeneratorForOpenFoam/server/server.py \
    --case-dir case/base_leakage --port 8025 --ws-port 8026 > /tmp/stlgen_server.log 2>&1 &
  disown; sleep 6
)

curl -s -c /tmp/cookie -X POST http://localhost:8025/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}' > /dev/null
curl -s -b /tmp/cookie -X POST http://localhost:8025/api/browser/load-proj \
  -H "Content-Type: application/json" \
  -d '{"path":"/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj"}' > /dev/null
curl -s -b /tmp/cookie -X POST http://localhost:8025/api/browser/export-stl > /dev/null
curl -s -b /tmp/cookie -X POST http://localhost:8025/api/browser/export-cfdrun > /tmp/e2e_cfd.json
python3 -c "
import json
with open('/tmp/e2e_cfd.json') as f: r = json.load(f)
assert r['success'] is True, r
print('export-cfdrun OK')
"
```
Expected: `export-cfdrun OK`

- [ ] **Step 3: Commit**

Nothing to commit — Step 1 was a verification, Step 2 just runs the server. If Step 1 printed OK and Step 2 said `export-cfdrun OK`, the tree is clean.

Run `git status --short` to confirm clean tree.

---

## Task 11: Docstring sweep

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:10, 18, 684, 792, 1666, 2504, 2521, 2535`
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:2, 469`
- Modify: `src/backend/aircooling/runner/bridge_bc.py:5`
- Modify: `src/backend/liquidcooling/liquid_runner.py:4, 30`
- Modify: `src/backend/aircooling/runner/fields.py:80`

- [ ] **Step 1: Sweep `stlgen_runner.py` header**

At L10:
```python
1.  Read cfd_export.json  (model/cfd_export.json)
```
Replace with:
```python
1.  Read the .cfdrun manifest (model/<basename>.cfdrun; JSON content
    identical to the pre-2026-04-11 cfd_export.json).
```

At L18:
```python
6.  Build in-memory BridgeBCRegistry from cfd_export.json physics
```
Replace with:
```python
6.  Build in-memory BridgeBCRegistry from .cfdrun physics data
```

- [ ] **Step 2: Sweep remaining stlgen_runner.py docstring hits**

At L684, L792, L1666, L2504, L2521, L2535 — replace every mention of "cfd_export.json" used as an active description (not as legacy-comparison context) with ".cfdrun". Keep the L816–820 NOTE about deliberately not deleting legacy cfd_export.json (that comment protects rollback safety, see spec §Layer 4).

Concrete grep-and-replace check:
```bash
grep -n "cfd_export.json" src/backend/aircooling/runner/stlgen_runner.py
```
Every surviving occurrence must be inside a comment/string that explicitly describes the legacy file for rollback context.

- [ ] **Step 3: Sweep `cfdproj_schema.py`**

At L2:
```python
"""Pydantic v2 validation models for the CFD Export bridge JSON (cfd_export.json)."""
```
Replace with:
```python
"""Pydantic v2 validation models for the .cfdrun manifest (JSON content,
identical to the pre-2026-04-11 cfd_export.json)."""
```

At L469:
```python
    """Root schema for cfd_export.json from STLGeneratorForOpenFoam."""
```
Replace with:
```python
    """Root schema for the .cfdrun manifest emitted by STLGeneratorForOpenFoam."""
```

- [ ] **Step 4: Sweep `bridge_bc.py`**

At L5:
```python
cfd_export.json from STLGeneratorForOpenFoam) and the FieldGenerator (which writes
```
Replace with:
```python
the .cfdrun manifest from STLGeneratorForOpenFoam) and the FieldGenerator (which writes
```

- [ ] **Step 5: Sweep `liquid_runner.py`**

At L4:
```python
    cfd_export.json → topology → solve → thermal → results
```
Replace with:
```python
    .cfdrun → topology → solve → thermal → results
```

At L30:
```python
        cfd_export: Parsed cfd_export.json dict.
```
Replace with:
```python
        cfd_export: Parsed .cfdrun dict (legacy param name kept for API stability).
```

- [ ] **Step 6: Sweep `fields.py`**

At L80:
```python
            simulation_config: Simulation configuration dict from cfd_export.json
```
Replace with:
```python
            simulation_config: Simulation configuration dict from the .cfdrun manifest
```

- [ ] **Step 7: Run the full runner test suite to confirm no regressions**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -x --tb=short 2>&1 | tail -10`
Expected: pass count unchanged from Task 9 baseline (4 pre-existing partition_hole failures, all other tests green).

- [ ] **Step 8: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/cfdproj_schema.py \
  src/backend/aircooling/runner/bridge_bc.py src/backend/liquidcooling/liquid_runner.py \
  src/backend/aircooling/runner/fields.py
git commit -m "docs(runner): sweep cfd_export.json → .cfdrun in active docstrings (keep legacy-context comments)"
```

---

## Task 12: Submodule bump in parent repo

**Files:**
- Modify: `STLGeneratorForOpenFoam` submodule pointer (parent repo commit)

- [ ] **Step 1: Stage the submodule bump**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam
```

- [ ] **Step 2: Confirm the parent diff is a pure submodule pointer bump**

```bash
git diff --cached --stat
```
Expected output contains exactly one line referencing `STLGeneratorForOpenFoam` and nothing else (no stray parent-repo edits).

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
chore(submodule): bump STLGen — cfdrun naming cleanup

Ships Tasks 1-6 of docs/superpowers/plans/2026-04-18-cfdrun-naming-cleanup.md:
- api-debug-testing.md skill doc rewritten to .cfdrun vocabulary
- server.py:754 stale --from-stlgen docstring corrected
- /api/browser/export-cfdrun endpoint + export_cfdrun_content worker fn
  added; /api/browser/export-cfd alias retained for the deprecation window
- POST body now primary-reads cfdrunJson, accepts cfdExportJson with a
  logged deprecation warning
- buildCfdExportJson → buildCfdrunJson (window.buildCfdExportJson alias
  preserved); local vars + POST body keys renamed at both call sites
EOF
)"
```

---

## Self-Review Checklist Results

**Spec coverage:**
- Layer 1 (skill doc + AGENTS.md + server.py:754): Tasks 1–2 ✓
- Layer 2 backend wire (server dual-key + endpoint alias): Tasks 3–4 ✓
- Layer 3 frontend (buildCfdrunJson + POST key + window alias): Tasks 5–6 ✓
- Layer 2 internal (cfd_export_path → cfdrun_path + raise on missing): Tasks 7–8 ✓
- Test-call-site updates: Task 9 ✓
- E2E chain verification: Task 10 ✓
- Docstring sweep (bridge_bc / liquid_runner / fields / cfdproj_schema / stlgen_runner): Task 11 ✓
- Submodule bump: Task 12 ✓
- Layer 4 (non-goals — `CFDExport` class, `test_cleanup_preserves_cfd_export.py`, historical specs, one-off scripts): confirmed untouched; no task edits those paths ✓

**Placeholder scan:** no TBD/TODO/"similar to"/"add error handling" in any step ✓.

**Type consistency:** attribute is `cfdrun_path` in Task 8 definition, Task 9 kwarg rename, Task 10 inspect check — consistent. Route name `/api/browser/export-cfdrun` + worker fn `export_cfdrun_content` used identically in Tasks 1, 4, 5, 10. POST body key `cfdrunJson` used identically in Tasks 1, 3, 5, 6, 10.

No gaps or inconsistencies found.
