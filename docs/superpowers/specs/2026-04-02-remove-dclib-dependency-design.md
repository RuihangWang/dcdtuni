# Remove dclib Dependency - Design Spec

**Date**: 2026-04-02
**Goal**: Remove dclib from the repository, making dctwin a standalone CFD simulation tool focused on data center thermal scenarios.
**Approach**: Option 1 - In-place cleanup within dctwin package structure.

## Context

The `--from-stlgen` pipeline (currently active) has **zero dclib imports**. All 93 dclib import statements across 54 files are in legacy/unused modules (EnergyPlus, co-simulation, RL environments, legacy `--from-cfdproj` path).

The active CFD data flow:
```
Frontend (STL_generate.html)
  → buildCfdExportJson() + STL files
  → POST /api/run-mesh
  → server.py saves cfd_export.json + STL files
  → python -m dctwin.runner.run_cfd --from-stlgen cfd_export.json
  → STLGenRunner → bridge_bc.json → OpenFOAM mesh → solve
```

## Files to KEEP (~15 files + templates)

### runner/ (7 files)
| File | Purpose |
|------|---------|
| `run_cfd.py` | CLI entry point with `--from-stlgen` |
| `stlgen_runner.py` | Main STLGen pipeline orchestrator |
| `fields.py` | OpenFOAM 0/ field file generation |
| `bridge_bc.py` | STL solid name → BC spec mapping |
| `cfdproj_schema.py` | cfd_export.json Pydantic validation |
| `openfoam_case.py` | OpenFOAM case dict generation (Jinja2) |
| `__init__.py` | Package init (needs slimming) |

### utils/ (3 files)
| File | Purpose |
|------|---------|
| `template.py` | Jinja2 Environment definition |
| `const.py` | Physical constants (rho_air, air_specific_heat) |
| `__init__.py` | Rewrite to only export template_env, template_dir |

### third_parties/foam/template/ (all Jinja2 templates)
All `.j2` and static template files under `0/`, `constant/`, `system/`, `mesh/`.

### dctwin/__init__.py
Slim down to minimal package init.

## Files/Directories to DELETE

### dclib/ (entire directory)
~100+ files. Remove from root `pyproject.toml` workspace and `dctwin/pyproject.toml` dependency.

### runner/ (5 files)
- `case.py` — CaseRunner (legacy room.json pipeline)
- `config.py` — CaseConfig (legacy config loader)
- `mesh.py` — MeshGenerator (legacy parametric mesh)
- `cfdproj_importer.py` — .cfdproj → room.json converter
- `postprocess.py` — Post-processing (unused by stlgen)

### Entire directories (8)
- `managers/` — HVAC/ITE managers (dclib-dependent)
- `models/` — Equipment physics models (dclib-dependent)
- `adapters/` — EnergyPlus co-simulation adapters
- `gym_envs/` — RL training environments
- `data/` — ML data processing
- `third_parties/eplus/` — EnergyPlus integration
- `third_parties/salome/` — Salome integration
- `third_parties/foam/geometry/` — Legacy parametric STL generation

### third_parties/foam/ single files
- `boundary.py`, `mesh.py`, `solver.py`, `utils.py`, `__init__.py`

### utils/ (6 items)
- `config.py`, `config_builder.py`, `errors.py`, `dt_engine_pb2.py`, `json_schema/`, `protos/`

### Root-level
- `registraion.py` — dclib registration (typo in name, unused)

### Test/tutorial/run directories
- `dctwin/test/` — All tests depend on dclib/legacy
- `dctwin/run/` — Legacy run scripts
- `dctwin/tutorials/` — Legacy tutorials

## Files to MODIFY

### 1. `dctwin/dctwin/utils/__init__.py`
Rewrite from 50-line eager imports to:
```python
from .template import template_env, template_dir
from .const import rho_air, air_specific_heat

__all__ = ["template_env", "template_dir", "rho_air", "air_specific_heat"]
```

### 2. `dctwin/dctwin/__init__.py`
Remove lazy imports for deleted modules (managers, models, adapters, etc.). Keep minimal.

### 3. `dctwin/dctwin/runner/__init__.py`
Remove lazy imports for `CaseConfig`, `CaseRunner`, `MeshGenerator`. Keep only `STLGenRunner`, `FieldGenerator`.

### 4. `dctwin/dctwin/runner/fields.py`
- Remove `TYPE_CHECKING` import of `GeometryManager`
- Remove `geometry_manager` parameter and all conditional branches using it
- Remove conditional imports of `ImportedSTLEntity`, `sanitize_name`

### 5. `dctwin/pyproject.toml`
- Remove `dclib = {path = "../dclib", develop = true}`
- Remove unused dependencies (opyplus, docker, kubernetes, gpytorch, etc.)

### 6. Root `pyproject.toml`
- Remove `dclib` from workspace members

## Non-goals
- No package rename (keep `dctwin`)
- No structural reorganization (templates stay in `third_parties/foam/template/`)
- No server.py changes (CLI command stays `python -m dctwin.runner.run_cfd --from-stlgen`)

## Verification
After cleanup, verify:
1. `python -m dctwin.runner.run_cfd --from-stlgen <case>/model/cfd_export.json --mesh-only` runs successfully
2. No import errors when loading the module
3. `pip install -e ./dctwin` succeeds without dclib
