# Fix oldInternalFaces Bug Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the bug where `subsetMesh` in `_remove_disconnected_cells` Strategy 3 creates `oldInternalFaces` (type=empty), which causes the OpenFOAM momentum predictor to skip U solving — resulting in missing velocity residuals.

**Architecture:** Remove Strategy 3 (underdetermined cells) from `_remove_disconnected_cells` because removing fully-connected cells via subsetMesh exposes internal faces as `oldInternalFaces` (type=empty), which is far worse than keeping 8 bad-quality cells. Add a safety net in `_do_subset_mesh` that converts any `oldInternalFaces` patch from `empty` to `wall` type after subsetMesh, protecting against Strategy 2 edge cases.

**Tech Stack:** Python 3.10, OpenFOAM mesh utilities, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/backend/aircooling/runner/stlgen_runner.py` | Modify | Remove Strategy 3, add wall-type safety net in `_do_subset_mesh` |
| `src/backend/tests/test_mesh_underdetermined_cleanup.py` | Modify or remove | Update/remove tests that expect Strategy 3 behavior |

---

### Task 1: Remove Strategy 3 + add oldInternalFaces safety net

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:5687-5711` (delete Strategy 3)
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:5713-5747` (add safety net to `_do_subset_mesh`)

- [ ] **Step 1: Delete Strategy 3 code block**

In `src/backend/aircooling/runner/stlgen_runner.py`, delete the entire Strategy 3 block (lines 5687-5709):

```python
        # --- Strategy 3: under-determined cells (determinant < 0.001) ---
        small_det = [w for w in warnings if w["code"] == "small_determinant"]
        if small_det:
            n_degenerate = small_det[0]["count"]
            sets_dir = case_dir / "constant" / "polyMesh" / "sets"
            if (sets_dir / "underdeterminedCells").exists():
                logger.warning(
                    f"Found {n_degenerate} under-determined cell(s) (determinant < 0.001) "
                    f"— removing via underdeterminedCells"
                )
                actions = [
                    '    {\n        name    keepCells;\n        type    cellSet;\n'
                    '        action  new;\n        source  cellToCell;\n'
                    '        sourceInfo { set underdeterminedCells; }\n    }',
                    '    {\n        name    keepCells;\n        type    cellSet;\n'
                    '        action  invert;\n    }',
                ]
                removed += self._do_subset_mesh(case_dir, actions, n_degenerate)
            else:
                logger.warning(
                    f"Found {n_degenerate} under-determined cell(s) but "
                    f"underdeterminedCells set not on disk — skipping removal."
                )
```

Replace with a warning-only log:

```python
        # --- Underdetermined cells: warn but do NOT remove ---
        # Removing connected cells via subsetMesh exposes internal faces as
        # "oldInternalFaces" (type=empty), which breaks the momentum predictor
        # in buoyantBoussinesqSimpleFoam (U residuals disappear entirely).
        # A few bad-quality cells are far less harmful than empty-type patches.
        small_det = [w for w in warnings if w["code"] == "small_determinant"]
        if small_det:
            logger.warning(
                f"Found {small_det[0]['count']} under-determined cell(s) "
                f"(determinant < 0.001) — keeping in mesh (removal would "
                f"create oldInternalFaces empty patch)"
            )
```

- [ ] **Step 2: Add oldInternalFaces safety net to `_do_subset_mesh`**

In `_do_subset_mesh`, after the `subsetMesh` command succeeds and before the info log, add a safety net that converts any `oldInternalFaces` patch from `empty` to `wall`. Find this line:

```python
        logger.info(f"Removed {n_removed} disconnected/isolated cell(s) from mesh")
```

Insert the safety net BEFORE it:

```python
        self._fix_old_internal_faces_patch_type(case_dir)
```

- [ ] **Step 3: Add `_fix_old_internal_faces_patch_type` method**

Add this method right after `_do_subset_mesh` (after the `return n_removed` line):

```python
    @staticmethod
    def _fix_old_internal_faces_patch_type(case_dir: Path) -> None:
        """Convert oldInternalFaces from 'empty' to 'wall' type if it exists.

        subsetMesh creates 'oldInternalFaces' (type=empty) for exposed
        internal faces.  'empty' type in a 3D case breaks the momentum
        predictor — buoyantBoussinesqSimpleFoam skips U solving entirely.
        Changing to 'wall' gives those faces a harmless noSlip BC.
        """
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return

        text = boundary_path.read_text(encoding="utf-8")
        if "oldInternalFaces" not in text:
            return

        import re as _re
        pattern = (
            r'(oldInternalFaces\s*\n\s*\{\s*\n'
            r'\s*type\s+)empty(;\s*\n'
            r'\s*inGroups\s+\d+\s*\()\s*empty\s*(\))'
        )
        new_text, n = _re.subn(pattern, r'\1wall\2wall\3', text)
        if n > 0:
            boundary_path.write_text(new_text, encoding="utf-8")
            logger.warning(
                "Converted oldInternalFaces patch type: empty → wall "
                "(empty type breaks momentum predictor in 3D cases)"
            )
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short -q 2>&1 | tail -10`
Expected: Same pass count as before, no new failures.

- [ ] **Step 5: Check if test_mesh_underdetermined_cleanup.py exists and needs updating**

Run: `ls src/backend/tests/test_mesh_underdetermined_cleanup.py 2>/dev/null && echo EXISTS || echo NOT_FOUND`

If it exists, check whether it tests Strategy 3 behavior. If so, update tests to expect the new warning-only behavior. If not found, skip.

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "fix: remove Strategy 3 cell removal, add oldInternalFaces wall-type safety net

Strategy 3 (underdetermined cells) removed connected cells via subsetMesh,
exposing internal faces as oldInternalFaces (type=empty). This broke the
SIMPLE momentum predictor — buoyantBoussinesqSimpleFoam skipped U solving
entirely, causing missing velocity residuals in the monitoring chart.

Fix: keep underdetermined cells (warn only), convert any oldInternalFaces
from empty to wall type as safety net for Strategy 2 edge cases."
```

---

### Task 2: Verify fix on Base604

**Files:**
- Read-only verification

- [ ] **Step 1: Regenerate mesh dicts for Base604 and verify no oldInternalFaces empty patch**

Run:
```bash
conda run -n dcdtuni python -c "
from pathlib import Path
from dcdtuni.runner.stlgen_runner import STLGenRunner

case_dir = Path('case/Base604')
cfdrun = case_dir / 'model' / 'Base604.cfdrun'
r = STLGenRunner(case_dir, cfdrun)
r._load()
r.validate_geometry()
r._preflight_partition_wall_thickness()
r._ensure_watertight_room_enclosure()
r._prepare_runtime_metadata()
r._generate_mesh_dicts()
r._remove_disconnected_cells(r.mesh_dir)

# Check boundary
boundary = (r.mesh_dir / 'constant' / 'polyMesh' / 'boundary').read_text()
if 'oldInternalFaces' in boundary:
    import re
    m = re.search(r'oldInternalFaces.*?type\s+(\w+)', boundary, re.DOTALL)
    patch_type = m.group(1) if m else '?'
    print(f'oldInternalFaces exists, type={patch_type}')
    assert patch_type == 'wall', f'Expected wall, got {patch_type}'
    print('PASS: oldInternalFaces converted to wall type')
else:
    print('PASS: no oldInternalFaces patch (cleanest outcome)')
"
```

Expected: Either "no oldInternalFaces" or "converted to wall type".

- [ ] **Step 2: Run all tests one final time**

Run: `conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short -q 2>&1 | tail -10`
Expected: All tests pass.

---

## Self-Review

- **Requirement 1** (remove Strategy 3): Task 1 Step 1 — delete block, replace with warning ✓
- **Requirement 2** (safety net for oldInternalFaces): Task 1 Steps 2-3 — `_fix_old_internal_faces_patch_type` method ✓
- **Requirement 3** (verify on Base604): Task 2 ✓
- **Placeholder scan:** All code blocks are complete, no TBD/TODO ✓
- **Type consistency:** `_fix_old_internal_faces_patch_type` is a `@staticmethod` taking `case_dir: Path`, consistent with other static methods in the class ✓
