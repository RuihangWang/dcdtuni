# Parallel Mesh Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `parallelMesh` / `meshProcess` config actually drive parallel snappyHexMesh via the correct OpenFOAM workflow: blockMesh → decomposePar → mpirun snappyHexMesh -parallel → reconstructParMesh.

**Architecture:** Add a `_run_parallel_mesh_pipeline()` method to `STLGenRunner` that mirrors `_run_serial_mesh_pipeline()` but inserts decomposePar before snappyHexMesh and runs snappy via mpirun. `run_mesh_only()` reads `meshConfig.parallelMesh` and dispatches to the correct pipeline. Existing `generate_decompose_par_dict()` and `_run_cmd()` (which already handles mpirun) are reused.

**Tech Stack:** Python 3.10, pytest, unittest.mock (for subprocess), OpenFOAM v2406

---

### File Structure

| File | Responsibility |
|------|---------------|
| `dctwin/dctwin/runner/stlgen_runner.py` | Add `_run_parallel_mesh_pipeline()`, modify `run_mesh_only()` to dispatch based on config |
| `dctwin/tests/__init__.py` | New test package |
| `dctwin/tests/test_parallel_mesh.py` | All tests for parallel mesh pipeline logic |

---

### Task 1: Set Up Test Infrastructure

**Files:**
- Create: `dctwin/tests/__init__.py`
- Create: `dctwin/tests/test_parallel_mesh.py`

- [ ] **Step 1: Create test package**

```python
# dctwin/tests/__init__.py
# (empty)
```

- [ ] **Step 2: Write first failing test — `run_mesh_only` reads `parallelMesh` config**

This test verifies that `run_mesh_only()` reads `meshConfig.parallelMesh` and `meshConfig.meshProcess` from `cfd_export.json` and calls the parallel pipeline when `parallelMesh=True`.

We mock `_run_serial_mesh_pipeline`, `_run_parallel_mesh_pipeline`, and all prerequisite methods to isolate the dispatch logic.

```python
# dctwin/tests/test_parallel_mesh.py
"""Tests for parallel mesh pipeline dispatch and command ordering."""
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

MODULE = "dctwin.runner.stlgen_runner"


class TestMeshPipelineDispatch:
    """run_mesh_only() dispatches to serial or parallel pipeline based on meshConfig."""

    def _make_runner(self, parallel_mesh: bool, mesh_process: int):
        """Create a STLGenRunner with mocked internals and injected config."""
        from dctwin.runner.stlgen_runner import STLGenRunner

        runner = STLGenRunner.__new__(STLGenRunner)
        runner.case_dir = Path("/tmp/fake_case")
        runner.mesh_dir = Path("/tmp/fake_case/Mesh")
        runner.model_dir = Path("/tmp/fake_case/model")
        runner.tri_surface_dir = runner.mesh_dir / "constant" / "triSurface"
        runner._data = {
            "meshConfig": {
                "baseSize": 0.2,
                "parallelMesh": parallel_mesh,
                "meshProcess": mesh_process,
            },
            "simulationConfig": {},
        }
        runner._stl_solids = {}
        runner._baffle_physics = {}
        runner._wall_baffle_zone_names = set()
        return runner

    @patch(f"{MODULE}.STLGenRunner._setup_complete_case_in_mesh")
    @patch(f"{MODULE}.STLGenRunner._run_parallel_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._run_serial_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._generate_mesh_dicts")
    @patch(f"{MODULE}.STLGenRunner._prepare_runtime_metadata")
    @patch(f"{MODULE}.STLGenRunner._ensure_watertight_room_enclosure")
    @patch(f"{MODULE}.STLGenRunner._load")
    def test_dispatches_to_parallel_when_config_enabled(
        self, mock_load, mock_enclosure, mock_meta, mock_dicts,
        mock_serial, mock_parallel, mock_setup,
    ):
        runner = self._make_runner(parallel_mesh=True, mesh_process=8)
        # Mock mesh_dir to avoid filesystem checks
        runner.mesh_dir = MagicMock()
        foam = MagicMock()
        foam.exists.return_value = False
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: foam)

        runner.run_mesh_only()

        mock_parallel.assert_called_once_with(8)
        mock_serial.assert_not_called()

    @patch(f"{MODULE}.STLGenRunner._setup_complete_case_in_mesh")
    @patch(f"{MODULE}.STLGenRunner._run_parallel_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._run_serial_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._generate_mesh_dicts")
    @patch(f"{MODULE}.STLGenRunner._prepare_runtime_metadata")
    @patch(f"{MODULE}.STLGenRunner._ensure_watertight_room_enclosure")
    @patch(f"{MODULE}.STLGenRunner._load")
    def test_dispatches_to_serial_when_config_disabled(
        self, mock_load, mock_enclosure, mock_meta, mock_dicts,
        mock_serial, mock_parallel, mock_setup,
    ):
        runner = self._make_runner(parallel_mesh=False, mesh_process=8)
        runner.mesh_dir = MagicMock()
        foam = MagicMock()
        foam.exists.return_value = False
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: foam)

        runner.run_mesh_only()

        mock_serial.assert_called_once()
        mock_parallel.assert_not_called()

    @patch(f"{MODULE}.STLGenRunner._setup_complete_case_in_mesh")
    @patch(f"{MODULE}.STLGenRunner._run_parallel_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._run_serial_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._generate_mesh_dicts")
    @patch(f"{MODULE}.STLGenRunner._prepare_runtime_metadata")
    @patch(f"{MODULE}.STLGenRunner._ensure_watertight_room_enclosure")
    @patch(f"{MODULE}.STLGenRunner._load")
    def test_falls_back_to_serial_when_meshProcess_is_1(
        self, mock_load, mock_enclosure, mock_meta, mock_dicts,
        mock_serial, mock_parallel, mock_setup,
    ):
        runner = self._make_runner(parallel_mesh=True, mesh_process=1)
        runner.mesh_dir = MagicMock()
        foam = MagicMock()
        foam.exists.return_value = False
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: foam)

        runner.run_mesh_only()

        mock_serial.assert_called_once()
        mock_parallel.assert_not_called()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni python -m pytest dctwin/tests/test_parallel_mesh.py -v`

Expected: FAIL — `_run_parallel_mesh_pipeline` does not exist yet; `run_mesh_only` always calls `_run_serial_mesh_pipeline`.

- [ ] **Step 4: Commit**

```bash
git add dctwin/tests/__init__.py dctwin/tests/test_parallel_mesh.py
git commit -m "test: add failing tests for parallel mesh pipeline dispatch"
```

---

### Task 2: Implement Dispatch Logic in `run_mesh_only()`

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py:445-458` (run_mesh_only)

- [ ] **Step 1: Add `_run_parallel_mesh_pipeline` stub**

Add a stub method so the dispatch can reference it. Place it right after `_run_serial_mesh_pipeline` (after line 2875):

```python
def _run_parallel_mesh_pipeline(self, n_procs: int) -> None:
    """Run blockMesh → decomposePar → snappyHexMesh (parallel) → reconstructParMesh → createBaffles."""
    raise NotImplementedError("Parallel mesh pipeline not yet implemented")
```

- [ ] **Step 2: Modify `run_mesh_only()` to dispatch**

Replace the direct `_run_serial_mesh_pipeline()` call at line 452 with config-based dispatch:

```python
def run_mesh_only(self) -> Path:
    """Generate mesh + complete OpenFOAM case in Mesh/ directory."""
    logger.info("=== STLGen: Mesh-Only Mode ===")
    self._load()
    self._ensure_watertight_room_enclosure()
    self._prepare_runtime_metadata()
    self._generate_mesh_dicts()

    # Dispatch to serial or parallel mesh pipeline based on meshConfig
    mc = self._data.get("meshConfig", {})
    parallel_mesh = mc.get("parallelMesh", False)
    n_procs = mc.get("meshProcess", 1)
    if parallel_mesh and n_procs > 1:
        logger.info(f"Parallel mesh enabled: {n_procs} processes")
        self._run_parallel_mesh_pipeline(n_procs)
    else:
        logger.info("Serial mesh mode")
        self._run_serial_mesh_pipeline()

    # Generate complete case files in Mesh/ so it's a runnable OpenFOAM case
    self._setup_complete_case_in_mesh()
    foam_file = self.mesh_dir / "case.foam"
    if not foam_file.exists():
        foam_file.touch()
    logger.info(f"Mesh complete. Complete case in {self.mesh_dir}")
    return self.mesh_dir / "constant" / "polyMesh"
```

- [ ] **Step 3: Run dispatch tests to verify they pass**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni python -m pytest dctwin/tests/test_parallel_mesh.py::TestMeshPipelineDispatch -v`

Expected: All 3 dispatch tests PASS (parallel pipeline is mocked, only dispatch logic tested).

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat: dispatch run_mesh_only to serial/parallel based on meshConfig"
```

---

### Task 3: Write Failing Tests for Parallel Pipeline Command Order

**Files:**
- Modify: `dctwin/tests/test_parallel_mesh.py`

- [ ] **Step 1: Write test verifying correct command order**

This is the critical test — it verifies the exact OpenFOAM command sequence for parallel meshing.

```python
class TestParallelMeshPipelineOrder:
    """_run_parallel_mesh_pipeline runs commands in the correct OpenFOAM order."""

    def _make_runner_with_mock_cmd(self, n_procs: int, has_feat_dict: bool = False, has_baffles: bool = False):
        """Create a runner with _run_cmd mocked to record call order."""
        from dctwin.runner.stlgen_runner import STLGenRunner

        runner = STLGenRunner.__new__(STLGenRunner)
        runner.case_dir = Path("/tmp/fake_case")
        runner.mesh_dir = Path("/tmp/fake_case/Mesh")
        runner.model_dir = Path("/tmp/fake_case/model")
        runner.tri_surface_dir = runner.mesh_dir / "constant" / "triSurface"
        runner._data = {
            "meshConfig": {"parallelMesh": True, "meshProcess": n_procs},
            "simulationConfig": {},
        }
        runner._stl_solids = {}
        runner._baffle_physics = {}
        runner._wall_baffle_zone_names = set()
        return runner

    @patch(f"{MODULE}.generate_decompose_par_dict")
    @patch(f"{MODULE}.STLGenRunner._clean_processor_dirs")
    @patch(f"{MODULE}.STLGenRunner._diagnose_background_patch")
    @patch(f"{MODULE}.STLGenRunner._ensure_mesh_system_files")
    @patch(f"{MODULE}.STLGenRunner._run_cmd")
    def test_correct_command_order_for_parallel_mesh(
        self, mock_run_cmd, mock_ensure, mock_diag, mock_clean, mock_gen_decompose,
    ):
        """blockMesh → decomposePar → mpirun snappyHexMesh -parallel → reconstructParMesh."""
        runner = self._make_runner_with_mock_cmd(n_procs=8)

        # Mock filesystem checks
        with patch("pathlib.Path.exists", return_value=False):
            runner._run_parallel_mesh_pipeline(8)

        cmds = [c.args[0] for c in mock_run_cmd.call_args_list]

        assert cmds[0] == "blockMesh"
        assert cmds[1] == "decomposePar -force"
        assert "mpirun" in cmds[2]
        assert "-np 8" in cmds[2]
        assert "snappyHexMesh" in cmds[2]
        assert "-parallel" in cmds[2]
        assert "-overwrite" in cmds[2]
        assert "reconstructParMesh" in cmds[3]
        assert "-constant" in cmds[3]

    @patch(f"{MODULE}.generate_decompose_par_dict")
    @patch(f"{MODULE}.STLGenRunner._clean_processor_dirs")
    @patch(f"{MODULE}.STLGenRunner._diagnose_background_patch")
    @patch(f"{MODULE}.STLGenRunner._ensure_mesh_system_files")
    @patch(f"{MODULE}.STLGenRunner._run_cmd")
    def test_decompose_par_dict_generated_before_decompose(
        self, mock_run_cmd, mock_ensure, mock_diag, mock_clean, mock_gen_decompose,
    ):
        """generate_decompose_par_dict() must be called before decomposePar runs."""
        runner = self._make_runner_with_mock_cmd(n_procs=4)

        with patch("pathlib.Path.exists", return_value=False):
            runner._run_parallel_mesh_pipeline(4)

        mock_gen_decompose.assert_called_once_with(runner.mesh_dir, 4)

    @patch(f"{MODULE}.generate_decompose_par_dict")
    @patch(f"{MODULE}.STLGenRunner._clean_processor_dirs")
    @patch(f"{MODULE}.STLGenRunner._regenerate_fields_for_baffles")
    @patch(f"{MODULE}.STLGenRunner._convert_wall_baffle_patches_to_wall")
    @patch(f"{MODULE}.STLGenRunner._diagnose_background_patch")
    @patch(f"{MODULE}.STLGenRunner._ensure_mesh_system_files")
    @patch(f"{MODULE}.STLGenRunner._run_cmd")
    def test_parallel_pipeline_includes_create_baffles_when_dict_exists(
        self, mock_run_cmd, mock_ensure, mock_diag, mock_convert, mock_regen,
        mock_clean, mock_gen_decompose,
    ):
        """createBaffles runs after reconstructParMesh if createBafflesDict exists."""
        runner = self._make_runner_with_mock_cmd(n_procs=4)

        def exists_side_effect(self_path=None):
            # createBafflesDict exists, surfaceFeatureExtractDict does not
            path_str = str(self_path) if self_path else ""
            return "createBafflesDict" in path_str

        with patch("pathlib.Path.exists", side_effect=lambda: False) as mock_exists:
            # We need a more targeted mock for the dict checks
            baffles_dict = runner.mesh_dir / "system" / "createBafflesDict"
            feat_dict = runner.mesh_dir / "system" / "surfaceFeatureExtractDict"
            with patch.object(type(baffles_dict), "exists", return_value=True):
                with patch.object(type(feat_dict), "exists", return_value=False):
                    runner._run_parallel_mesh_pipeline(4)

        cmds = [c.args[0] for c in mock_run_cmd.call_args_list]
        # Last command should be createBaffles
        assert "createBaffles" in cmds[-1]
        # reconstructParMesh should come before createBaffles
        reconstruct_idx = next(i for i, c in enumerate(cmds) if "reconstructParMesh" in c)
        baffles_idx = next(i for i, c in enumerate(cmds) if "createBaffles" in c)
        assert reconstruct_idx < baffles_idx

    @patch(f"{MODULE}.generate_decompose_par_dict")
    @patch(f"{MODULE}.STLGenRunner._clean_processor_dirs")
    @patch(f"{MODULE}.STLGenRunner._diagnose_background_patch")
    @patch(f"{MODULE}.STLGenRunner._ensure_mesh_system_files")
    @patch(f"{MODULE}.STLGenRunner._run_cmd")
    def test_processor_dirs_cleaned_after_reconstruct(
        self, mock_run_cmd, mock_ensure, mock_diag, mock_clean, mock_gen_decompose,
    ):
        """Processor directories are cleaned up after reconstructParMesh."""
        runner = self._make_runner_with_mock_cmd(n_procs=8)

        with patch("pathlib.Path.exists", return_value=False):
            runner._run_parallel_mesh_pipeline(8)

        mock_clean.assert_called_once_with(runner.mesh_dir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni python -m pytest dctwin/tests/test_parallel_mesh.py::TestParallelMeshPipelineOrder -v`

Expected: FAIL — `_run_parallel_mesh_pipeline` raises `NotImplementedError`.

- [ ] **Step 3: Commit**

```bash
git add dctwin/tests/test_parallel_mesh.py
git commit -m "test: add failing tests for parallel mesh command order"
```

---

### Task 4: Implement `_run_parallel_mesh_pipeline`

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py` (after `_run_serial_mesh_pipeline`, ~line 2875)

- [ ] **Step 1: Add `_clean_processor_dirs` helper**

Place this right before `_run_serial_mesh_pipeline`:

```python
@staticmethod
def _clean_processor_dirs(case_dir: Path) -> None:
    """Remove processor* directories from a case directory."""
    import glob as _glob
    for pdir in sorted(_glob.glob(str(case_dir / "processor*"))):
        shutil.rmtree(pdir, ignore_errors=True)
    logger.info(f"Cleaned processor directories in {case_dir}")
```

- [ ] **Step 2: Add import for `generate_decompose_par_dict` at top of file**

At the imports section of `stlgen_runner.py`, add:

```python
from dctwin.runner.openfoam_case import generate_decompose_par_dict
```

(Check if this import already exists; if `generate_decompose_par_dict` is already imported via another path, use the existing import.)

- [ ] **Step 3: Replace `_run_parallel_mesh_pipeline` stub with real implementation**

```python
def _run_parallel_mesh_pipeline(self, n_procs: int) -> None:
    """Run blockMesh → decomposePar → snappyHexMesh (parallel) → reconstructParMesh → createBaffles.

    Correct OpenFOAM parallel mesh workflow:
    1. blockMesh (serial) — generates base background mesh
    2. decomposePar -force — splits base mesh into processor* dirs
    3. mpirun -np N snappyHexMesh -overwrite -parallel — parallel refinement
    4. reconstructParMesh -constant — reassembles into single polyMesh
    5. surfaceFeatureExtract, createBaffles — serial post-processing
    """
    self._ensure_mesh_system_files()
    case_dir = self.mesh_dir

    baffles_dict = case_dir / "system" / "createBafflesDict"
    total_steps = 4  # blockMesh, decomposePar, snappyHexMesh, reconstructParMesh
    feat_dict = case_dir / "system" / "surfaceFeatureExtractDict"
    if feat_dict.exists():
        total_steps += 1
    if baffles_dict.exists():
        total_steps += 1
    step = 0

    # 1. blockMesh (serial — generates simple background mesh)
    step += 1
    logger.info(f"Step {step}/{total_steps}: blockMesh")
    self._run_cmd("blockMesh", case_dir)

    # 2. surfaceFeatureExtract (serial, before decompose — features are shared)
    if feat_dict.exists():
        step += 1
        logger.info(f"Step {step}/{total_steps}: surfaceFeatureExtract")
        self._run_cmd("surfaceFeatureExtract", case_dir)

    # 3. decomposePar — split base mesh to processor directories
    step += 1
    logger.info(f"Step {step}/{total_steps}: decomposePar -force ({n_procs} processors)")
    generate_decompose_par_dict(case_dir, n_procs)
    self._clean_processor_dirs(case_dir)  # remove stale processor dirs
    self._run_cmd("decomposePar -force", case_dir)

    # 4. snappyHexMesh in parallel
    step += 1
    logger.info(f"Step {step}/{total_steps}: snappyHexMesh -overwrite -parallel ({n_procs} procs)")
    self._run_cmd(
        f"mpirun --oversubscribe -np {n_procs} snappyHexMesh -overwrite -parallel",
        case_dir,
    )

    # 5. reconstructParMesh — merge processor meshes back to single polyMesh
    step += 1
    logger.info(f"Step {step}/{total_steps}: reconstructParMesh -constant")
    self._run_cmd("reconstructParMesh -constant -mergeTol 1E-08 -noZero", case_dir)
    self._diagnose_background_patch(case_dir)

    # 6. Clean up processor directories (no longer needed)
    self._clean_processor_dirs(case_dir)

    # 7. createBaffles (serial, on reconstructed mesh)
    if baffles_dict.exists():
        step += 1
        logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite")
        self._regenerate_fields_for_baffles(case_dir)
        self._run_cmd("createBaffles -overwrite", case_dir)
        self._convert_wall_baffle_patches_to_wall(case_dir)
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni python -m pytest dctwin/tests/test_parallel_mesh.py -v`

Expected: ALL tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat: implement parallel mesh pipeline with correct OpenFOAM order

blockMesh → decomposePar → mpirun snappyHexMesh -parallel → reconstructParMesh → createBaffles.
Reads meshConfig.parallelMesh and meshConfig.meshProcess from cfd_export.json."
```

---

### Task 5: Write Edge Case Tests and Harden

**Files:**
- Modify: `dctwin/tests/test_parallel_mesh.py`

- [ ] **Step 1: Write test for missing meshConfig (backward compatibility)**

```python
class TestMeshConfigEdgeCases:
    """Edge cases: missing config, defaults, backward compatibility."""

    @patch(f"{MODULE}.STLGenRunner._setup_complete_case_in_mesh")
    @patch(f"{MODULE}.STLGenRunner._run_serial_mesh_pipeline")
    @patch(f"{MODULE}.STLGenRunner._generate_mesh_dicts")
    @patch(f"{MODULE}.STLGenRunner._prepare_runtime_metadata")
    @patch(f"{MODULE}.STLGenRunner._ensure_watertight_room_enclosure")
    @patch(f"{MODULE}.STLGenRunner._load")
    def test_missing_mesh_config_defaults_to_serial(
        self, mock_load, mock_enclosure, mock_meta, mock_dicts,
        mock_serial, mock_setup,
    ):
        """When meshConfig is absent, default to serial pipeline."""
        from dctwin.runner.stlgen_runner import STLGenRunner

        runner = STLGenRunner.__new__(STLGenRunner)
        runner.case_dir = Path("/tmp/fake_case")
        runner.mesh_dir = MagicMock()
        foam = MagicMock()
        foam.exists.return_value = False
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: foam)
        runner.model_dir = Path("/tmp/fake_case/model")
        runner.tri_surface_dir = Path("/tmp/fake_case/Mesh/constant/triSurface")
        runner._data = {"simulationConfig": {}}  # no meshConfig
        runner._stl_solids = {}
        runner._baffle_physics = {}
        runner._wall_baffle_zone_names = set()

        runner.run_mesh_only()

        mock_serial.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni python -m pytest dctwin/tests/test_parallel_mesh.py -v`

Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add dctwin/tests/test_parallel_mesh.py
git commit -m "test: add edge case tests for mesh config backward compatibility"
```

---

### Task 6: Integration Smoke Test via API (Manual Verification)

This task verifies the full end-to-end flow through `server.py`. It cannot be automated as it requires OpenFOAM and the actual case files.

- [ ] **Step 1: Prepare a test case with parallel mesh enabled**

Edit the cfd_export.json for the canonical test case to ensure `parallelMesh: true` and a small `meshProcess` value (e.g. 2):

```bash
# Read current config
cat STLGeneratorForOpenFoam/projects/V5_CFD_Station_1773892919284.cfdproj | python -m json.tool | grep -A5 meshConfig
```

If the project's meshConfig already has `parallelMesh: true`, no change needed.

- [ ] **Step 2: Start server and trigger mesh via API**

```bash
# Kill existing server
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null

# Start server
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py \
  --case-dir STLGeneratorForOpenFoam/projects/V5_CFD_Station_1773892919284.cfdproj &

# Trigger mesh generation
curl -X POST http://localhost:8005/api/run-mesh \
  -H "Content-Type: application/json" \
  -d '{"meshOnly": true}'
```

- [ ] **Step 3: Verify correct command order in logs**

Check the server output / Operation Log for this sequence:
1. `blockMesh` 
2. `surfaceFeatureExtract` (if applicable)
3. `decomposePar -force` ← must appear BEFORE snappyHexMesh
4. `mpirun --oversubscribe -np N snappyHexMesh -overwrite -parallel`
5. `reconstructParMesh -constant`
6. `createBaffles` (if applicable)

**Red flag:** If you see `snappyHexMesh -overwrite` without `mpirun` and `-parallel`, the serial pipeline was used instead.

- [ ] **Step 4: Verify mesh output**

```bash
# Check polyMesh was created (reconstructed single mesh)
ls -la dctwin/case/V5_CFD_Station/Mesh/constant/polyMesh/

# No processor* dirs should remain
ls -d dctwin/case/V5_CFD_Station/Mesh/processor* 2>/dev/null && echo "FAIL: processor dirs not cleaned" || echo "OK: no stale processor dirs"
```

---

## Summary of Changes

| What | Where | Why |
|------|-------|-----|
| Dispatch logic | `run_mesh_only()` line ~452 | Read `meshConfig.parallelMesh` + `meshProcess`, call parallel or serial pipeline |
| `_run_parallel_mesh_pipeline()` | New method after `_run_serial_mesh_pipeline` | Correct OpenFOAM order: blockMesh → decomposePar → mpirun snappyHexMesh → reconstructParMesh → createBaffles |
| `_clean_processor_dirs()` | New static method | Reusable cleanup of processor* dirs (used in parallel mesh and already needed by `_setup_complete_case_in_mesh`) |
| Import `generate_decompose_par_dict` | Top of `stlgen_runner.py` | Already exists in `openfoam_case.py`, reuse for mesh decomposition |
| Tests | `dctwin/tests/test_parallel_mesh.py` | Verify dispatch logic, command order, edge cases |
