# Remove dclib Dependency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove dclib from the monorepo, delete all legacy/unused modules from dctwin, leaving only the `--from-stlgen` OpenFOAM CFD pipeline.

**Architecture:** In-place cleanup of the existing dctwin package. Delete dclib/ entirely, delete all dctwin modules not reachable from the `--from-stlgen` code path, then fix the remaining files' imports to avoid referencing deleted modules.

**Tech Stack:** Python 3.10, Pydantic v2, Jinja2, OpenFOAM, Poetry

---

### Task 1: Delete dclib directory and remove from pyproject.toml

**Files:**
- Delete: `dclib/` (entire directory)
- Modify: `pyproject.toml` (root)
- Modify: `dctwin/pyproject.toml`

- [ ] **Step 1: Remove dclib from root pyproject.toml**

In `pyproject.toml` (root), remove the dclib dependency line:

```toml
# REMOVE this line:
dclib = {path = "./dclib", develop = true}
```

The `[tool.poetry.dependencies]` section should become:

```toml
[tool.poetry.dependencies]
python = ">=3.10,<4.0"
dctwin = {path = "./dctwin", develop = true}
playwright = ">=1.40,<2.0"
websockets = ">=12,<16"
```

- [ ] **Step 2: Remove dclib from dctwin/pyproject.toml**

In `dctwin/pyproject.toml`, remove:

```toml
# REMOVE this line:
dclib = {path = "../dclib", develop = true}
```

Also remove dependencies only used by deleted modules. Keep only what the stlgen pipeline needs. The `[tool.poetry.dependencies]` section should become:

```toml
[tool.poetry.dependencies]
python = ">=3.10,<4.0"
jinja2 = "3.1.2"
markupsafe = "2.0.1"
loguru = "*"
pydantic = ">=2.0"
numpy = "<2.0.0"
psutil = "5.9.8"
```

Note: `loguru` and `pydantic` were implicit transitive deps before — add them explicitly since stlgen_runner.py, cfdproj_schema.py, bridge_bc.py, fields.py all use them directly. Remove: `gym`, `cvxpy`, `opyplus`, `fluidfoam`, `torch`, `gpytorch`, `tqdm`, `protobuf`, `cvxpylayers`, `sympy`, `docker`, `scipy`, `kubernetes`, `eppy`, `sh`, `coolprop`, `torchdiffeq`, `vtk`, `pyvista`, `requests`, `configobj`, `reportlab`, `cython`, `setuptools`.

- [ ] **Step 3: Delete the dclib directory**

```bash
rm -rf dclib/
```

- [ ] **Step 4: Commit**

```bash
git add -A dclib/ pyproject.toml dctwin/pyproject.toml
git commit -m "chore: remove dclib dependency and trim dctwin dependencies

Remove dclib package entirely. Strip dctwin/pyproject.toml down to
only the dependencies needed by the --from-stlgen CFD pipeline."
```

---

### Task 2: Delete unused dctwin top-level directories

**Files:**
- Delete: `dctwin/dctwin/adapters/`
- Delete: `dctwin/dctwin/data/`
- Delete: `dctwin/dctwin/gym_envs/`
- Delete: `dctwin/dctwin/managers/`
- Delete: `dctwin/dctwin/models/`
- Delete: `dctwin/dctwin/registraion.py`

- [ ] **Step 1: Delete all unused directories and files**

```bash
rm -rf dctwin/dctwin/adapters/
rm -rf dctwin/dctwin/data/
rm -rf dctwin/dctwin/gym_envs/
rm -rf dctwin/dctwin/managers/
rm -rf dctwin/dctwin/models/
rm -f  dctwin/dctwin/registraion.py
```

- [ ] **Step 2: Commit**

```bash
git add -A dctwin/dctwin/adapters/ dctwin/dctwin/data/ dctwin/dctwin/gym_envs/ dctwin/dctwin/managers/ dctwin/dctwin/models/ dctwin/dctwin/registraion.py
git commit -m "chore: delete unused dctwin modules (adapters, data, gym_envs, managers, models)"
```

---

### Task 3: Delete unused third_parties modules

**Files:**
- Delete: `dctwin/dctwin/third_parties/eplus/`
- Delete: `dctwin/dctwin/third_parties/salome/`
- Delete: `dctwin/dctwin/third_parties/foam/geometry/`
- Delete: `dctwin/dctwin/third_parties/foam/boundary.py`
- Delete: `dctwin/dctwin/third_parties/foam/mesh.py`
- Delete: `dctwin/dctwin/third_parties/foam/solver.py`
- Delete: `dctwin/dctwin/third_parties/foam/utils.py`
- Delete: `dctwin/dctwin/third_parties/foam/__init__.py`
- Modify: `dctwin/dctwin/third_parties/__init__.py`

- [ ] **Step 1: Delete eplus and salome directories**

```bash
rm -rf dctwin/dctwin/third_parties/eplus/
rm -rf dctwin/dctwin/third_parties/salome/
```

- [ ] **Step 2: Delete unused foam modules (keep only template/)**

```bash
rm -rf dctwin/dctwin/third_parties/foam/geometry/
rm -f  dctwin/dctwin/third_parties/foam/boundary.py
rm -f  dctwin/dctwin/third_parties/foam/mesh.py
rm -f  dctwin/dctwin/third_parties/foam/solver.py
rm -f  dctwin/dctwin/third_parties/foam/utils.py
rm -f  dctwin/dctwin/third_parties/foam/__init__.py
```

After this, `dctwin/dctwin/third_parties/foam/` should contain ONLY the `template/` directory (with all its `.j2` and static files).

- [ ] **Step 3: Rewrite third_parties/__init__.py**

Replace the entire contents of `dctwin/dctwin/third_parties/__init__.py` with an empty file (all the old imports reference deleted modules):

```python
```

(Empty file — the package exists only to hold `foam/template/`.)

- [ ] **Step 4: Commit**

```bash
git add -A dctwin/dctwin/third_parties/
git commit -m "chore: delete unused third_parties (eplus, salome, foam legacy modules)

Keep only foam/template/ directory with Jinja2 templates for OpenFOAM case generation."
```

---

### Task 4: Delete unused runner modules

**Files:**
- Delete: `dctwin/dctwin/runner/case.py`
- Delete: `dctwin/dctwin/runner/config.py`
- Delete: `dctwin/dctwin/runner/mesh.py`
- Delete: `dctwin/dctwin/runner/cfdproj_importer.py`
- Delete: `dctwin/dctwin/runner/postprocess.py`

- [ ] **Step 1: Delete legacy runner files**

```bash
rm -f dctwin/dctwin/runner/case.py
rm -f dctwin/dctwin/runner/config.py
rm -f dctwin/dctwin/runner/mesh.py
rm -f dctwin/dctwin/runner/cfdproj_importer.py
rm -f dctwin/dctwin/runner/postprocess.py
```

- [ ] **Step 2: Commit**

```bash
git add -A dctwin/dctwin/runner/case.py dctwin/dctwin/runner/config.py dctwin/dctwin/runner/mesh.py dctwin/dctwin/runner/cfdproj_importer.py dctwin/dctwin/runner/postprocess.py
git commit -m "chore: delete legacy runner modules (case, config, mesh, cfdproj_importer, postprocess)"
```

---

### Task 5: Delete unused utils modules

**Files:**
- Delete: `dctwin/dctwin/utils/config.py`
- Delete: `dctwin/dctwin/utils/config_builder.py`
- Delete: `dctwin/dctwin/utils/errors.py`
- Delete: `dctwin/dctwin/utils/dt_engine_pb2.py`
- Delete: `dctwin/dctwin/utils/json_schema/`
- Delete: `dctwin/dctwin/utils/protos/`

- [ ] **Step 1: Delete unused utils files and directories**

```bash
rm -f  dctwin/dctwin/utils/config.py
rm -f  dctwin/dctwin/utils/config_builder.py
rm -f  dctwin/dctwin/utils/errors.py
rm -f  dctwin/dctwin/utils/dt_engine_pb2.py
rm -rf dctwin/dctwin/utils/json_schema/
rm -rf dctwin/dctwin/utils/protos/
```

- [ ] **Step 2: Commit**

```bash
git add -A dctwin/dctwin/utils/config.py dctwin/dctwin/utils/config_builder.py dctwin/dctwin/utils/errors.py dctwin/dctwin/utils/dt_engine_pb2.py dctwin/dctwin/utils/json_schema/ dctwin/dctwin/utils/protos/
git commit -m "chore: delete unused utils (config, errors, protobuf, json_schema)"
```

---

### Task 6: Delete unused test/run/tutorial directories

**Files:**
- Delete: `dctwin/test/`
- Delete: `dctwin/run/`
- Delete: `dctwin/tutorials/`
- Delete: `dctwin/docs/` (mkdocs for old API)
- Delete: `dctwin/mkdocs.yml`
- Delete: `dctwin/Dockerfile`, `dctwin/faster_build_dockerfile`
- Delete: `dctwin/lint.sh`, `dctwin/pylava.ini`, `dctwin/sonar-project.properties`

- [ ] **Step 1: Delete auxiliary directories and files**

```bash
rm -rf dctwin/test/
rm -rf dctwin/run/
rm -rf dctwin/tutorials/
rm -rf dctwin/docs/
rm -f  dctwin/mkdocs.yml
rm -f  dctwin/Dockerfile
rm -f  dctwin/faster_build_dockerfile
rm -f  dctwin/lint.sh
rm -f  dctwin/pylava.ini
rm -f  dctwin/sonar-project.properties
```

- [ ] **Step 2: Commit**

```bash
git add -A dctwin/test/ dctwin/run/ dctwin/tutorials/ dctwin/docs/ dctwin/mkdocs.yml dctwin/Dockerfile dctwin/faster_build_dockerfile dctwin/lint.sh dctwin/pylava.ini dctwin/sonar-project.properties
git commit -m "chore: delete legacy tests, tutorials, docs, and CI artifacts"
```

---

### Task 7: Fix dctwin/__init__.py

**Files:**
- Modify: `dctwin/dctwin/__init__.py`

- [ ] **Step 1: Rewrite __init__.py**

Replace the entire contents of `dctwin/dctwin/__init__.py` with:

```python
"""
dctwin - Data Center CFD Simulation Engine.

Provides OpenFOAM-based thermal simulation for data center rooms
via the STLGen pipeline (--from-stlgen).
"""

__version__ = "3.0.0"
```

The old file had lazy imports for `data`, `managers`, `models`, `third_parties`, `utils` and a `make_env()` function — all referencing deleted modules. The stlgen pipeline imports `dctwin.runner.*` and `dctwin.utils.*` directly, so no re-exports are needed here.

- [ ] **Step 2: Commit**

```bash
git add dctwin/dctwin/__init__.py
git commit -m "chore: slim dctwin/__init__.py to version string only"
```

---

### Task 8: Fix runner/__init__.py

**Files:**
- Modify: `dctwin/dctwin/runner/__init__.py`

- [ ] **Step 1: Rewrite runner/__init__.py**

Replace the entire contents of `dctwin/dctwin/runner/__init__.py` with:

```python
"""
CFD Runner Module - OpenFOAM simulation workflow for data center thermal analysis.
"""
```

The old file had lazy imports for `CaseConfig`, `MeshGenerator`, `CaseRunner` (all deleted). The stlgen pipeline imports `STLGenRunner` and `FieldGenerator` directly from their modules, not from `__init__.py`, so no re-exports are needed.

- [ ] **Step 2: Commit**

```bash
git add dctwin/dctwin/runner/__init__.py
git commit -m "chore: slim runner/__init__.py, remove lazy imports for deleted modules"
```

---

### Task 9: Fix utils/__init__.py

**Files:**
- Modify: `dctwin/dctwin/utils/__init__.py`

- [ ] **Step 1: Rewrite utils/__init__.py**

Replace the entire contents of `dctwin/dctwin/utils/__init__.py` with:

```python
from .template import template_env, template_dir
from .const import rho_air, air_specific_heat

__all__ = ["template_env", "template_dir", "rho_air", "air_specific_heat"]
```

The old file eagerly imported from `config.py`, `errors.py`, `dt_engine_pb2.py` — all now deleted. Only `template_env` (used by `openfoam_case.py`) and `const` values (used by `fields.py`) are still needed.

- [ ] **Step 2: Commit**

```bash
git add dctwin/dctwin/utils/__init__.py
git commit -m "chore: slim utils/__init__.py to only export template_env and constants"
```

---

### Task 10: Fix runner/fields.py — remove geometry_manager dependency

**Files:**
- Modify: `dctwin/dctwin/runner/fields.py`

- [ ] **Step 1: Remove TYPE_CHECKING imports for deleted modules**

In `dctwin/dctwin/runner/fields.py`, find and delete lines 17-19:

```python
# DELETE these lines:
if TYPE_CHECKING:
    from .config import CaseConfig
    from dctwin.third_parties.foam.geometry.manager import GeometryManager
```

Also remove `TYPE_CHECKING` from the typing import on line 6 (change `from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple` to `from typing import Any, Dict, List, Optional, Tuple`).

- [ ] **Step 2: Simplify FieldGenerator.__init__ signature**

Replace the current `__init__` method (lines 33-60) with:

```python
    def __init__(
        self,
        *,
        case_dir: Path,
        simulation_config: Optional[Dict[str, Any]] = None,
        room_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize field generator.
        
        Args:
            case_dir: Case directory path
            simulation_config: Simulation configuration dict from cfd_export.json
            room_config: Room configuration dict override
        """
        self.case_dir = Path(case_dir).resolve()
        self._simulation_config = simulation_config
        self._room_config = room_config
```

This removes the `config: Optional["CaseConfig"]` and `geometry_manager: Optional["GeometryManager"]` parameters entirely.

- [ ] **Step 3: Simplify _collect_perforated_baffle_config**

Replace the method `_collect_perforated_baffle_config` (around lines 102-110) with:

```python
    def _collect_perforated_baffle_config(self) -> Dict[str, Dict[str, Any]]:
        """Collect perforated baffle boundary condition configuration."""
        return {}
```

The old code delegated to `self.geometry_manager.get_perforated_baffle_bc_config()` which is no longer available. The stlgen pipeline handles perforated baffles through bridge_bc.json instead.

- [ ] **Step 4: Simplify _collect_imported_bc_data**

Replace the method `_collect_imported_bc_data` (around lines 112-144). Remove the entire `if self.geometry_manager is not None:` block, keeping only the bridge_bc.json loading logic:

```python
    def _collect_imported_bc_data(self) -> Dict[str, Dict[str, Any]]:
        """Collect boundary condition data from bridge_bc.json.

        Returns dict mapping sanitized patch_name -> {bc_type, temperature, ...}
        """
        imported_bc: Dict[str, Dict[str, Any]] = {}

        # Load bridge BC registry (generated by STLGenRunner from cfd_export.json)
        bridge_bc_path = self.case_dir / "model" / "bridge_bc.json"
        if bridge_bc_path.exists():
            from dctwin.runner.bridge_bc import BridgeBCRegistry
            registry = BridgeBCRegistry.load(bridge_bc_path)
            for patch_name, spec in registry.items():
                imported_bc[patch_name] = {
                    "bc_type": spec.bc_type,
                    "temperature": spec.temperature,
                    "flow_rate": spec.flow_rate,
                    "power": spec.power,
```

(Keep the rest of the method body after this point unchanged — the bridge_bc.json loading logic continues with sigma, heat_flux, etc.)

- [ ] **Step 5: Update stlgen_runner.py FieldGenerator call site**

In `dctwin/dctwin/runner/stlgen_runner.py`, find where FieldGenerator is instantiated (the call that passes `case_dir=`, `simulation_config=`, `room_config=`). Since we changed `__init__` to keyword-only args and removed `config`/`geometry_manager`, verify the call already uses keyword args. It should look like:

```python
FieldGenerator(
    case_dir=self.case_dir,
    simulation_config=self._data.get("simulationConfig", {}),
    room_config={},
)
```

This should work as-is since `case_dir` is now keyword-only. No change needed if the call already uses keyword arguments.

- [ ] **Step 6: Commit**

```bash
git add dctwin/dctwin/runner/fields.py dctwin/dctwin/runner/stlgen_runner.py
git commit -m "refactor: remove geometry_manager and legacy CaseConfig from FieldGenerator

FieldGenerator now only supports the stlgen pipeline path.
Perforated baffles and imported BCs are handled via bridge_bc.json."
```

---

### Task 11: Fix runner/run_cfd.py — remove legacy code paths

**Files:**
- Modify: `dctwin/dctwin/runner/run_cfd.py`

- [ ] **Step 1: Remove legacy CLI args and code paths**

Replace the entire contents of `dctwin/dctwin/runner/run_cfd.py` with:

```python
#!/usr/bin/env python3
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Usage:
    python -m dctwin.runner.run_cfd --from-stlgen <path/to/cfd_export.json>
    python -m dctwin.runner.run_cfd --from-stlgen <path> --mesh-only
    python -m dctwin.runner.run_cfd --from-stlgen <path> --solve-only
"""
import argparse
import sys
from pathlib import Path

from loguru import logger

# Setup logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run OpenFOAM CFD simulation for data center",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--from-stlgen",
        type=str,
        required=True,
        metavar="CFD_EXPORT_JSON",
        help="Path to cfd_export.json from STLGeneratorForOpenFoam. "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly.",
    )

    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Generate mesh only (skip solver)",
    )

    parser.add_argument(
        "--solve-only",
        action="store_true",
        help="Run solver only (requires existing mesh)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    from dctwin.runner.stlgen_runner import STLGenRunner

    cfdproj_path = Path(args.from_stlgen).resolve()
    if not cfdproj_path.exists():
        logger.error(f"CFD export file not found: {cfdproj_path}")
        sys.exit(1)

    # cfd_export.json lives in <case>/model/ — case_dir is the parent
    case_dir = cfdproj_path.parent
    if case_dir.name == "model":
        case_dir = case_dir.parent

    logger.info(f"STLGen pipeline: {cfdproj_path}")
    logger.info(f"Case directory: {case_dir}")

    try:
        runner = STLGenRunner(case_dir, cfdproj_path)
        if args.mesh_only:
            runner.run_mesh_only()
        elif args.solve_only:
            runner.run_solve_only()
        else:
            runner.run_simulation()
    except Exception as e:
        logger.error(f"STLGen runner failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

This removes `--config`, `--stl-only`, `--from-cfdproj` args and the CaseRunner/CFDProjectImporter code paths. `--from-stlgen` is now required.

- [ ] **Step 2: Commit**

```bash
git add dctwin/dctwin/runner/run_cfd.py
git commit -m "refactor: simplify run_cfd.py to only support --from-stlgen pipeline

Remove legacy --config, --stl-only, --from-cfdproj code paths."
```

---

### Task 12: Clean up __pycache__ and verify import

**Files:**
- No file changes — verification only

- [ ] **Step 1: Clean pycache**

```bash
find dctwin/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
conda run -n dcdtuni python -c "from dctwin.runner.stlgen_runner import STLGenRunner; print('STLGenRunner OK')"
conda run -n dcdtuni python -c "from dctwin.runner.fields import FieldGenerator; print('FieldGenerator OK')"
conda run -n dcdtuni python -c "from dctwin.runner.bridge_bc import BridgeBCRegistry; print('BridgeBCRegistry OK')"
conda run -n dcdtuni python -c "from dctwin.runner.cfdproj_schema import CFDExport; print('CFDExport OK')"
conda run -n dcdtuni python -c "from dctwin.runner.openfoam_case import *; print('openfoam_case OK')"
conda run -n dcdtuni python -c "from dctwin.utils import template_env; print('template_env OK')"
```

All 6 commands should print "OK" with no ImportError.

- [ ] **Step 3: Verify CLI entry point**

```bash
conda run -n dcdtuni python -m dctwin.runner.run_cfd --help
```

Expected output should show `--from-stlgen`, `--mesh-only`, `--solve-only` arguments only.

- [ ] **Step 4: Re-install dctwin in editable mode**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pip install -e ./dctwin
```

Should succeed without requiring dclib.

- [ ] **Step 5: Commit (if any pycache cleanup changed tracked files)**

Only if needed:
```bash
git add -A && git status
# If changes exist:
git commit -m "chore: clean up stale __pycache__ directories"
```

---

### Task 13: Update CLAUDE.md

**Files:**
- Modify: `.claude/CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md to reflect new structure**

Key changes needed in `.claude/CLAUDE.md`:

1. Remove dclib from the "Repository Identity & Scope" table — delete the `dclib` row and the "monolithic repository containing all three components" note about dclib.
2. Remove the entire "dclib" description from "Architecture Overview" section 1. Update dctwin description to note it's now a standalone CFD simulation engine using only the `--from-stlgen` pipeline.
3. Remove mentions of `room.json`, `config.toml`, `--from-cfdproj`, `CaseRunner`, EnergyPlus, co-simulation from the "Build / Run / Test Commands" section.
4. Update the pipeline description: `cfd_export.json` + browser STLs → blockMesh + snappyHexMesh → boundary conditions → solver → results.
5. Remove the "Entity/SubEntity pattern (DCTwin)" from "Key Domain Concepts" since parametric STL generation is removed.

- [ ] **Step 2: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect dclib removal and simplified architecture"
```
