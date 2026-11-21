# Parallel VTK Read for Post-Processing Init — Design Spec

**Date**: 2026-04-05
**Goal**: Replace serial `pv.read(vtm_path)` with parallel split-read using ThreadPoolExecutor to achieve ~4x speedup on large cases.

---

## 1. Problem

`CachedPostProcessor.init()` calls `pv.read(vtm_path)` which reads the entire VTM multiblock serially. For large cases this is the dominant bottleneck:

| Case | Cells | Patches | Serial `pv.read` | Parallel 8T |
|------|-------|---------|-------------------|-------------|
| Tri_angle | 63K | 52 | 0.83s | N/A |
| GTC | 1.07M | 990 | 31.4s | 11.7s |
| CMI-Guichou | 696K | 6335 | 329s | 83.7s |

The VTM file is just XML metadata pointing to sub-files:
- `internal.vtu` — volume mesh (biggest file, but reads fast: 2-3s)
- `boundary/*.vtp` — individual patch files (many small files, I/O-bound)

VTK file I/O releases the Python GIL, so `ThreadPoolExecutor` gives real parallelism.

## 2. Solution

Replace the single `pv.read(vtm_path)` call in `CachedPostProcessor.init()` with a parallel split-read:

```python
from concurrent.futures import ThreadPoolExecutor

# Parse VTM XML to find sub-file paths
ts_dir = vtm_path.parent / vtm_path.stem  # e.g. ..._2000/
internal_vtu = ts_dir / "internal.vtu"
boundary_dir = ts_dir / "boundary"
patch_files = sorted(boundary_dir.glob("*.vtp"))

# Read all files in parallel (8 threads optimal)
with ThreadPoolExecutor(max_workers=8) as pool:
    fut_internal = pool.submit(pv.read, str(internal_vtu))
    fut_patches = [(pf.stem, pool.submit(pv.read, str(pf))) for pf in patch_files]
    
    internal = fut_internal.result()
    boundary_blocks = [(name, fut.result()) for name, fut in fut_patches]

# Assemble into the same structure downstream code expects
```

## 3. Changes

### File: `dctwin/dctwin/runner/postprocess.py`

**What changes in `CachedPostProcessor.init()`** (lines ~1770-1776):

Before:
```python
pv = _get_pyvista()
self.multi_block = pv.read(str(self._vtm_path))

for i in range(self.multi_block.n_blocks):
    key = self.multi_block.keys()[i]
    block = self.multi_block[i]
    if block is None:
        continue
    if key in ("internal", "internalMesh"):
        self._internal = block
    elif key == "boundary":
        self._boundary = block
```

After:
```python
pv = _get_pyvista()
self._internal, self._boundary = self._parallel_read_vtm(self._vtm_path)
self.multi_block = True  # sentinel — downstream only checks `is not None`
```

**New method: `_parallel_read_vtm(vtm_path)`**:

1. Derive `ts_dir` from the VTM path (strip `.vtm` extension → that's the subdirectory name)
2. Locate `internal.vtu` and `boundary/*.vtp` files
3. Read all in parallel with `ThreadPoolExecutor(max_workers=8)`
4. Assemble boundary patches into a `pv.MultiBlock` 
5. Return `(internal, boundary)`

**Fallback**: If `internal.vtu` or `boundary/` directory doesn't exist (older foamToVTK output or different structure), fall back to serial `pv.read(vtm_path)` and extract blocks as before. This ensures backward compatibility.

### `multi_block` usage audit

`self.multi_block` is currently used as:
- A `None` check for "is PP initialized?" — replace with a boolean or check `self._internal is not None`
- `pv.read()` return stored directly — no longer needed since we extract internal/boundary directly

Need to audit all references to `self.multi_block` and ensure they still work. The field is checked in:
- `init()` — cache check (`self.multi_block is not None`)
- `invalidate()` — set to `None`
- Various handlers — check `is not None` before proceeding

Replace these checks with `self._internal is not None` or keep `self.multi_block` as a boolean sentinel.

## 4. Timing Integration

The parallel read replaces step 3 (`pv_read`) in the timing system. Update timing to report:

```python
timing["pv_read_parallel"] = round(elapsed, 2)
timing["pv_read_internal"] = round(internal_time, 2)
timing["pv_read_patches"] = round(patches_time, 2)
timing["pv_read_n_patches"] = len(patch_files)
```

## 5. Thread Count

Benchmark results show 8 threads is optimal:
- 8 threads: 83.7s on CMI-Guichou
- 16 threads: 95.7s (slower due to I/O contention)

Use 8 as default. No need to make configurable.

## 6. Testing

1. **GTC case**: Verify parallel read produces same internal/boundary as serial read (cell counts, field names match)
2. **Tri_angle case**: Verify small cases still work (may have few patches)
3. **CMI-Guichou case**: Verify timing improvement (~84s vs 329s)
4. **Full PP pipeline**: After parallel read, verify `get_mesh()`, `get_field()`, `get_slice()`, `get_volume()` all work correctly

## 7. Non-Goals

- No lazy-loading — all patches read eagerly during init
- No changes to `foamToVTK` conversion step
- No changes to downstream consumers (`get_mesh`, `get_field`, etc.)
- No changes to the server handler or frontend
