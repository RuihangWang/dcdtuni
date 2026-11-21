# Parallel VTK Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace serial `pv.read(vtm_path)` with parallel ThreadPoolExecutor split-read for ~4x speedup on large cases.

**Architecture:** Parse the VTM XML to discover sub-files (internal.vtu + boundary/*.vtp), read them in parallel with 8 threads via `concurrent.futures.ThreadPoolExecutor`, then assemble into the same internal/boundary structure downstream code expects. Falls back to serial read if sub-files don't exist.

**Tech Stack:** Python 3.10, PyVista 0.47, VTK 9.5, concurrent.futures (stdlib)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `dctwin/dctwin/runner/postprocess.py` | Modify | Add `_parallel_read_vtm()` method, update `init()` to call it |

Single file change — all modifications are in `CachedPostProcessor`.

---

### Task 1: Add `_parallel_read_vtm()` method

**Files:**
- Modify: `dctwin/dctwin/runner/postprocess.py`

- [ ] **Step 1: Add import for `concurrent.futures`**

At the top of the file (after line 48 `import threading`), add:

```python
from concurrent.futures import ThreadPoolExecutor
```

- [ ] **Step 2: Add `_parallel_read_vtm()` method to `CachedPostProcessor`**

Add this new method to the `CachedPostProcessor` class, right before the `init()` method (before line 1727). The method parses VTM XML structure, reads internal.vtu + all boundary .vtp files in parallel, and assembles a PyVista MultiBlock for the boundary:

```python
    def _parallel_read_vtm(self, vtm_path: Path) -> Tuple[Optional[Any], Optional[Any]]:
        """Read VTM sub-files in parallel using ThreadPoolExecutor.

        Instead of one slow pv.read(vtm), read internal.vtu and individual
        boundary .vtp files concurrently (8 threads). VTK I/O releases the
        GIL so threads give real parallelism.

        Returns:
            (internal_mesh, boundary_multiblock) or falls back to serial read.
        """
        pv = _get_pyvista()
        vtm_dir = vtm_path.parent
        ts_dir_name = vtm_path.stem  # e.g. "2026-03-22-07-03-03_GTC_1000"
        ts_dir = vtm_dir / ts_dir_name
        internal_vtu = ts_dir / "internal.vtu"
        boundary_dir = ts_dir / "boundary"

        # Fallback: if sub-files don't exist, use serial read
        if not internal_vtu.exists() or not boundary_dir.exists():
            logger.info("[PP] Parallel read unavailable, falling back to serial pv.read")
            mb = pv.read(str(vtm_path))
            internal, boundary = None, None
            for i in range(mb.n_blocks):
                key = mb.keys()[i]
                block = mb[i]
                if block is None:
                    continue
                if key in ("internal", "internalMesh"):
                    internal = block
                elif key == "boundary":
                    boundary = block
            return internal, boundary

        patch_files = sorted(boundary_dir.glob("*.vtp"))

        # Read all sub-files in parallel
        internal = None
        patch_results: list = []

        with ThreadPoolExecutor(max_workers=8) as pool:
            fut_internal = pool.submit(pv.read, str(internal_vtu))
            fut_patches = [(pf.stem, pool.submit(pv.read, str(pf))) for pf in patch_files]

            internal = fut_internal.result()
            patch_results = [(name, fut.result()) for name, fut in fut_patches]

        # Assemble boundary MultiBlock (preserving patch names for downstream)
        boundary = pv.MultiBlock()
        for name, mesh in patch_results:
            boundary.append(mesh, name)

        return internal, boundary
```

- [ ] **Step 3: Commit**

```bash
git add dctwin/dctwin/runner/postprocess.py
git commit -m "feat: add _parallel_read_vtm method for parallel VTK loading

Reads internal.vtu and boundary .vtp files concurrently with 8 threads
via ThreadPoolExecutor. Falls back to serial pv.read if sub-files
don't exist."
```

---

### Task 2: Replace serial read in `init()` with parallel read

**Files:**
- Modify: `dctwin/dctwin/runner/postprocess.py:1803-1828`

- [ ] **Step 1: Replace Step 3 + Step 4 in `init()` with parallel read**

Find this block in `init()` (lines 1803-1828):

```python
            # --- Step 3: pv.read (disk I/O + memory allocation) ---
            t0 = time.perf_counter()
            pv = _get_pyvista()
            self.multi_block = pv.read(str(self._vtm_path))
            timing["pv_read"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] pv.read: {timing['pv_read']}s")
            err = _check_timeout("pv_read")
            if err:
                return err

            # --- Step 4: extract internal/boundary blocks ---
            t0 = time.perf_counter()
            for i in range(self.multi_block.n_blocks):
                key = self.multi_block.keys()[i]
                block = self.multi_block[i]
                if block is None:
                    continue
                if key in ("internal", "internalMesh"):
                    self._internal = block
                elif key == "boundary":
                    self._boundary = block
            timing["extract_blocks"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] extract_blocks: {timing['extract_blocks']}s")
            err = _check_timeout("extract_blocks")
            if err:
                return err
```

Replace with:

```python
            # --- Step 3: parallel read (internal.vtu + boundary patches) ---
            t0 = time.perf_counter()
            self._internal, self._boundary = self._parallel_read_vtm(self._vtm_path)
            self.multi_block = True  # sentinel for "is initialized" checks
            timing["pv_read_parallel"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] pv_read_parallel: {timing['pv_read_parallel']}s")
            if self._internal is not None:
                _tlog(f"[PP-TIMING]   internal: {self._internal.n_cells} cells, {self._internal.n_points} points")
            if self._boundary is not None:
                _tlog(f"[PP-TIMING]   boundary: {self._boundary.n_blocks} patches")
            err = _check_timeout("pv_read_parallel")
            if err:
                return err
```

- [ ] **Step 2: Update `invalidate()` to reset `multi_block` consistently**

In `invalidate()` (line 2203), `self.multi_block = None` is already there and still correct — setting it to `None` signals "not initialized". No change needed.

- [ ] **Step 3: Commit**

```bash
git add dctwin/dctwin/runner/postprocess.py
git commit -m "feat: use parallel VTK read in PP init

Replaces serial pv.read(vtm) + block extraction with parallel
_parallel_read_vtm(). Steps 3+4 merged into one parallel step.
multi_block set to True sentinel for downstream is-None checks."
```

---

### Task 3: Verify on Tri_angle (small case)

**Files:**
- No changes — testing only

- [ ] **Step 1: Restart server with Tri_angle case**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null; sleep 3
conda run -n dcdtuni python -u STLGeneratorForOpenFoam/server/server.py \
  --port 8005 --case-dir dctwin/case/Tri_angle > /tmp/pp_tri_test.log 2>&1 &
sleep 5
```

- [ ] **Step 2: Call PP init and verify timing + correctness**

```bash
COOKIE_JAR=/tmp/pp_verify.txt
curl -s -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}' \
  -c $COOKIE_JAR > /dev/null

curl -s -X POST http://localhost:8005/api/pp/init \
  -H "Content-Type: application/json" \
  -b $COOKIE_JAR \
  -d '{}' | python3 -m json.tool
```

Expected: `success: true`, timing shows `pv_read_parallel`, patches > 0, cells > 0.

- [ ] **Step 3: Verify get_mesh works (surface rendering data)**

```bash
curl -s -X POST http://localhost:8005/api/pp/mesh \
  -H "Content-Type: application/json" \
  -b $COOKIE_JAR \
  -d '{}' -o /tmp/pp_mesh_test.bin -w "%{http_code}"
```

Expected: HTTP 200, binary file > 0 bytes.

- [ ] **Step 4: Verify get_field works (field data)**

```bash
curl -s -X POST http://localhost:8005/api/pp/field \
  -H "Content-Type: application/json" \
  -b $COOKIE_JAR \
  -d '{"field":"T"}' -o /tmp/pp_field_test.bin -w "%{http_code}"
```

Expected: HTTP 200, binary file > 0 bytes.

---

### Task 4: Verify on GTC (medium case, 1M cells)

**Files:**
- No changes — testing only

- [ ] **Step 1: Restart server with GTC case and run init**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null; sleep 3
conda run -n dcdtuni python -u STLGeneratorForOpenFoam/server/server.py \
  --port 8005 --case-dir dctwin/case/GTC > /tmp/pp_gtc_test.log 2>&1 &
sleep 5

COOKIE_JAR=/tmp/pp_verify_gtc.txt
curl -s -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}' \
  -c $COOKIE_JAR > /dev/null

curl -s -X POST http://localhost:8005/api/pp/init \
  -H "Content-Type: application/json" \
  -b $COOKIE_JAR \
  -d '{"timeout":120}' | python3 -m json.tool
```

Expected: `success: true`, `pv_read_parallel` < 15s (was 31s serial), 990 patches.

- [ ] **Step 2: Verify mesh and field endpoints work**

```bash
curl -s -X POST http://localhost:8005/api/pp/mesh \
  -b $COOKIE_JAR -d '{}' -o /tmp/pp_gtc_mesh.bin -w "%{http_code}"

curl -s -X POST http://localhost:8005/api/pp/field \
  -H "Content-Type: application/json" \
  -b $COOKIE_JAR \
  -d '{"field":"T"}' -o /tmp/pp_gtc_field.bin -w "%{http_code}"
```

Expected: Both HTTP 200.

---

### Task 5: Verify on CMI-Guichou (large case, 6335 patches)

**Files:**
- No changes — testing only

- [ ] **Step 1: Restart server with CMI-Guichou and run init**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null; sleep 3
conda run -n dcdtuni python -u STLGeneratorForOpenFoam/server/server.py \
  --port 8005 --case-dir dctwin/case/CMI-Guichou > /tmp/pp_cmi_test.log 2>&1 &
sleep 5

COOKIE_JAR=/tmp/pp_verify_cmi.txt
curl -s -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}' \
  -c $COOKIE_JAR > /dev/null

curl -s -X POST http://localhost:8005/api/pp/init \
  -H "Content-Type: application/json" \
  -b $COOKIE_JAR \
  -d '{"timeout":300}' | python3 -m json.tool
```

Expected: `success: true`, `pv_read_parallel` ~80-90s (was 329s serial), 6335 patches.

- [ ] **Step 2: Commit plan**

```bash
git add -f docs/superpowers/plans/2026-04-05-parallel-vtk-read.md
git commit -m "docs: add parallel VTK read implementation plan"
```
