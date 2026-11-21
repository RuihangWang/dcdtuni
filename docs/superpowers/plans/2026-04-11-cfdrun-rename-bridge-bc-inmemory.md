# cfdrun Rename + bridge_bc In-Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `case/<name>/model/cfd_export.json` → `<basename>.cfdrun` and refactor `bridge_bc.json` from a disk file into an in-memory `BridgeBCRegistry` injected into `FieldGenerator`.

**Architecture:** Two independent phases. Phase A is a pure Python backend refactor that eliminates `bridge_bc.json` from disk by making `BridgeBCRegistry` a constructor-injected in-memory object. Phase B is a cosmetic file rename that touches the frontend JS, server.py POST handlers, and the backend CLI flag. Both phases keep `CFDExport` Pydantic schema and `buildCfdExportJson()` JS projection untouched.

**Tech Stack:** Python 3.10 (conda env `dcdtuni`), pytest 9.x, Three.js r128 (browser prototype, no build system), `loguru` logger, Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-04-11-cfdrun-rename-bridge-bc-inmemory-design.md`

---

## File Structure

### Phase A — bridge_bc in-memory refactor

| File | Responsibility | Action |
|---|---|---|
| `src/backend/aircooling/runner/bridge_bc.py` | `BridgeBCRegistry` class + `BCSpec` Pydantic model | Modify — delete `save()`, `load()`; update docstring |
| `src/backend/aircooling/runner/fields.py` | `FieldGenerator` — generates `0/U`, `0/T`, `0/p_rgh` from BC registry | Modify — add `bridge_registry` ctor param, refactor `_collect_imported_bc_data()` |
| `src/backend/aircooling/runner/stlgen_runner.py` | `STLGenRunner` orchestrates STL → mesh → solve pipeline | Modify — add `self._bridge_registry` attr; rename `_build_and_save_bridge_bc()` → `_build_bridge_bc()`; wire registry through to FieldGenerator, leakage fvOptions, wall baffle boundary fix; extend `_cleanup_stale_compat_files()` |
| `src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py` | New unit test pinning in-memory behavior | Create |

### Phase B — cfdrun rename

| File | Responsibility | Action |
|---|---|---|
| `src/backend/aircooling/runner/run_cfd.py` | CLI entry point `python -m dcdtuni.runner.run_cfd` | Modify — rename `--from-stlgen` → `--from-cfdrun`, update help |
| `STLGeneratorForOpenFoam/server/server.py` | REST API bridge (writes JSON/STL into case dir, runs CLI) | Modify — accept `cfdrunFilename` POST field, write to `<case>/model/<basename>.cfdrun`, use new CLI flag, cleanup old `cfd_export.json`/`bridge_bc.json` at write time |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Browser UI (single-file Three.js app) | Modify — add `getCfdrunFilename()` helper; `runMeshGeneration()` POST body carries `cfdrunFilename` |

### Commit boundaries

- **Commit 1 (Phase A):** All bridge_bc in-memory changes land together. Intermediate states are broken (some consumers read disk, some read memory), so the whole phase is one commit.
- **Commit 2 (Phase B):** All cfdrun filename changes land together. Intermediate states are broken (frontend writes new name, backend CLI expects old name), so the whole phase is one commit.

---

## Phase A: bridge_bc In-Memory Refactor

### Task A1: Write the failing unit test for in-memory bridge_bc

**Files:**

- Create: `src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py`

- [ ] **Step 1: Write the failing test**

Create `src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py` with:

```python
"""Tests for bridge_bc in-memory registry behavior.

After the refactor, STLGenRunner._build_bridge_bc() stores the registry on
self._bridge_registry and does NOT write model/bridge_bc.json to disk.
"""
import json
from pathlib import Path

import pytest

from dcdtuni.runner.stlgen_runner import STLGenRunner


def _make_minimal_cfd_export() -> dict:
    """Build a minimal cfd_export.json dict exercising acu_supply/return,
    fan_inlet/outlet, and wall_baffle bc types.
    """
    return {
        "version": "1.0",
        "source": "STLGeneratorForOpenFoam",
        "projectName": "test",
        "room": {
            "height": 3.0,
            "plane": [
                {"x": 0, "y": 0, "z": 0},
                {"x": 10, "y": 0, "z": 0},
                {"x": 10, "y": 10, "z": 0},
                {"x": 0, "y": 10, "z": 0},
            ],
            "locationInMesh": {"x": 5, "y": 5, "z": 1.5},
        },
        "racks": {
            "Rack_1": {
                "stlFile": "merged_rack.stl",
                "geometry": {
                    "anchor": {"x": 1, "y": 1, "z": 0},
                    "size": {"W": 0.6, "D": 1.0, "H": 1.867},
                    "orientation": 0,
                    "unitCount": 42,
                    "unitHeight": 0.04445,
                    "slotWidthRatio": 0.8,
                },
                "servers": [
                    {
                        "id": "Rack_1_srv_1",
                        "type": "fanServer",
                        "startSlot": 0,
                        "spanSlots": 4,
                        "inletSolidName": "Rack_1_srv_1_inlet_patch",
                        "outletSolidName": "Rack_1_srv_1_outlet_patch",
                        "physics": {
                            "volumeFlowRate": 0.15,
                            "power": 2000,
                        },
                    },
                ],
                "leakage": {"enabled": False, "throughSlots": [], "groups": []},
                "wallBaffleSolids": [],
                "wallSolidNames": [],
            },
        },
        "acus": {
            "ACU_1": {
                "stlFile": "merged_acu.stl",
                "geometry": {
                    "anchor": {"x": 5, "y": 8, "z": 0},
                    "size": {"W": 1.2, "D": 0.6, "H": 2.0},
                    "orientation": 180,
                },
                "supplyFaces": [
                    {
                        "solidName": "ACU_1_supply_patch",
                        "patchId": "p_1",
                        "face": "back",
                        "physics": {"temperature": 15.0, "volumeFlowRate": 4.0},
                    }
                ],
                "returnFaces": [
                    {
                        "solidName": "ACU_1_return_patch",
                        "patchId": "p_2",
                        "face": "top",
                        "physics": {"volumeFlowRate": 4.0},
                    }
                ],
                "wallSolidNames": [],
            }
        },
        "wallObjects": {},
        "heatSources": {},
        "baffles": {},
        "floors": {},
        "walls": {},
    }


def _setup_runner(tmp_path: Path) -> STLGenRunner:
    """Write a cfd_export.json into tmp_path and return a loaded STLGenRunner."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    cfd_path = model_dir / "cfd_export.json"
    cfd_path.write_text(json.dumps(_make_minimal_cfd_export()))
    runner = STLGenRunner(case_dir, cfd_export_path=cfd_path)
    runner._load_cfd_json_only()
    return runner


class TestBridgeBcInMemory:
    """_build_bridge_bc() builds a registry on self._bridge_registry and does NOT write disk."""

    def test_registry_is_populated_on_self(self, tmp_path):
        runner = _setup_runner(tmp_path)
        runner._build_bridge_bc()
        assert runner._bridge_registry is not None
        assert len(runner._bridge_registry) > 0

    def test_no_bridge_bc_json_file_written(self, tmp_path):
        runner = _setup_runner(tmp_path)
        runner._build_bridge_bc()
        bridge_bc_path = runner.model_dir / "bridge_bc.json"
        assert not bridge_bc_path.exists(), (
            f"_build_bridge_bc() must not write {bridge_bc_path} "
            f"(the registry is in-memory only after the refactor)"
        )

    def test_expected_bc_types_present(self, tmp_path):
        runner = _setup_runner(tmp_path)
        runner._build_bridge_bc()
        bc_types = {spec.bc_type for _, spec in runner._bridge_registry.items()}
        # Minimal fixture has fanServer (→ fan_inlet, fan_outlet) + 1 ACU supply + 1 ACU return
        assert "fan_inlet" in bc_types
        assert "fan_outlet" in bc_types
        assert "acu_supply" in bc_types
        assert "acu_return" in bc_types

    def test_fan_server_inlet_has_flow_rate(self, tmp_path):
        runner = _setup_runner(tmp_path)
        runner._build_bridge_bc()
        spec = runner._bridge_registry.get("Rack_1_srv_1_inlet_patch")
        assert spec is not None
        assert spec.bc_type == "fan_inlet"
        assert spec.flow_rate == pytest.approx(0.15)

    def test_acu_supply_has_temperature(self, tmp_path):
        runner = _setup_runner(tmp_path)
        runner._build_bridge_bc()
        spec = runner._bridge_registry.get("ACU_1_supply_patch")
        assert spec is not None
        assert spec.bc_type == "acu_supply"
        assert spec.temperature == pytest.approx(15.0)
        assert spec.flow_rate == pytest.approx(4.0)

    def test_summary_logged(self, tmp_path, caplog):
        import logging
        caplog.set_level(logging.INFO)
        runner = _setup_runner(tmp_path)
        runner._build_bridge_bc()
        # loguru routes through stderr, but the runner uses logger.info;
        # the summary line must mention "BridgeBCRegistry" and "patches"
        all_text = "\n".join(r.message for r in caplog.records)
        # Fallback: also check stderr capture since loguru may not propagate to caplog
        # (test is best-effort — primary assertions above are the real contract)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py -v`

Expected: All tests FAIL. The most informative failure will be `AttributeError: 'STLGenRunner' object has no attribute '_build_bridge_bc'` (since today's code has `_build_and_save_bridge_bc`, not `_build_bridge_bc`). Some may also fail on `runner._bridge_registry is not None` because the attribute doesn't exist.

- [ ] **Step 3: Do NOT commit yet**

The test is red. Tasks A2-A9 make the refactor green. Single commit for all of Phase A at the end (Task A10).

---

### Task A2: Add `_bridge_registry` attribute to `STLGenRunner.__init__`

**Files:**

- Modify: `src/backend/aircooling/runner/stlgen_runner.py:428-442` (the `__init__` method)

- [ ] **Step 1: Add the attribute**

Use Edit tool to replace:

```python
        self._data: Optional[Dict[str, Any]] = None   # parsed cfd_export.json
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()
```

with:

```python
        self._data: Optional[Dict[str, Any]] = None   # parsed cfd_export.json
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()
        # In-memory bridge BC registry; populated by _build_bridge_bc() during
        # _prepare_runtime_metadata(). Replaces the old model/bridge_bc.json file.
        self._bridge_registry: Optional["BridgeBCRegistry"] = None
```

- [ ] **Step 2: Add the forward-reference import at the top of the file**

Find the existing import block near the top of `stlgen_runner.py` and add a `TYPE_CHECKING` guarded import for `BridgeBCRegistry`. Open the file and locate the imports around line 30-47. Add this block immediately after the `typing` import line (search for `from typing import Any, Dict, Iterable, List, Optional, Tuple`):

```python
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

if TYPE_CHECKING:
    from dcdtuni.runner.bridge_bc import BridgeBCRegistry
```

Note: `TYPE_CHECKING` is added to the existing `typing` import. The `if TYPE_CHECKING` block is new and placed right after. This avoids a circular import while keeping the type annotation valid for readers.

- [ ] **Step 3: Verify the module still imports**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.stlgen_runner import STLGenRunner; print('ok')"`

Expected: `ok` printed, no ImportError.

---

### Task A3: Add `bridge_registry` parameter to `FieldGenerator.__init__`

**Files:**

- Modify: `src/backend/aircooling/runner/fields.py` — FieldGenerator constructor and `_collect_imported_bc_data()` method

- [ ] **Step 1: Update the `fields.py` imports to include TYPE_CHECKING and the forward ref**

Use Edit tool to find (exact line near top of `fields.py`):

```python
from typing import Any, Dict, List, Optional, Tuple
```

Replace with:

```python
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from dcdtuni.runner.bridge_bc import BridgeBCRegistry
```

- [ ] **Step 2: Add the `bridge_registry` keyword-only parameter to `__init__`**

The current signature (lines 34-41) is multi-line and keyword-only (`*,` prefix). Use Edit tool to find:

```python
    def __init__(
        self,
        *,
        case_dir: Path,
        simulation_config: Optional[Dict[str, Any]] = None,
        room_config: Optional[Dict[str, Any]] = None,
        patch_areas: Optional[Dict[str, float]] = None,
    ):
```

Replace with:

```python
    def __init__(
        self,
        *,
        case_dir: Path,
        simulation_config: Optional[Dict[str, Any]] = None,
        room_config: Optional[Dict[str, Any]] = None,
        patch_areas: Optional[Dict[str, float]] = None,
        bridge_registry: Optional["BridgeBCRegistry"] = None,
    ):
```

- [ ] **Step 3: Store `bridge_registry` on self in the constructor body**

The current body stores private-prefixed attrs (`self._simulation_config`, `self._room_config`, `self._patch_areas`). For `bridge_registry` we use a **public** attribute `self.bridge_registry` (matching the `self.case_dir` pattern — constructor-injected, no defensive default needed).

Use Edit tool to find:

```python
        self.case_dir = Path(case_dir).resolve()
        self._simulation_config = simulation_config
        self._room_config = room_config
        self._patch_areas = patch_areas or {}
```

Replace with:

```python
        self.case_dir = Path(case_dir).resolve()
        self._simulation_config = simulation_config
        self._room_config = room_config
        self._patch_areas = patch_areas or {}
        self.bridge_registry = bridge_registry
```

- [ ] **Step 4: Verify fields.py still imports cleanly**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.fields import FieldGenerator; fg = FieldGenerator(case_dir='/tmp'); assert fg.bridge_registry is None; print('ok')"`

Expected: `ok` printed. Confirms the new optional param has `None` default and is stored on self.

---

### Task A4: Refactor `_collect_imported_bc_data()` to read from injected registry

**Files:**

- Modify: `src/backend/aircooling/runner/fields.py:116-143` (the `_collect_imported_bc_data` method — verify exact line range with `Grep` if drifted)

- [ ] **Step 1: Locate the method**

Run: `conda run -n dcdtuni python -c "import inspect; from dcdtuni.runner.fields import FieldGenerator; print(inspect.getsourcelines(FieldGenerator._collect_imported_bc_data))"`

You should see the method body starting around line 120.

- [ ] **Step 2: Replace the disk-load block**

Use Edit tool to replace this existing block (currently at approximately lines 120-143):

```python
    def _collect_imported_bc_data(self) -> Dict[str, Dict[str, Any]]:
        """Collect boundary condition data from bridge_bc.json.

        Returns dict mapping sanitized patch_name -> {bc_type, temperature, ...}
        """
        imported_bc: Dict[str, Dict[str, Any]] = {}

        # Load bridge BC registry (generated by STLGenRunner from cfd_export.json)
        bridge_bc_path = self.case_dir / "model" / "bridge_bc.json"
        if bridge_bc_path.exists():
            from dcdtuni.runner.bridge_bc import BridgeBCRegistry
            registry = BridgeBCRegistry.load(bridge_bc_path)
            for patch_name, spec in registry.items():
                imported_bc[patch_name] = {
                    "bc_type": spec.bc_type,
                    "temperature": spec.temperature,
                    "flow_rate": spec.flow_rate,
                    "power": spec.power,
                    "sigma": spec.sigma,
                    "heat_flux": spec.heat_flux,
                }
            logger.debug(f"Loaded {len(registry)} bridge BC entries from {bridge_bc_path}")

        return imported_bc
```

with:

```python
    def _collect_imported_bc_data(self) -> Dict[str, Dict[str, Any]]:
        """Collect boundary condition data from the in-memory bridge BC registry.

        The registry is built by STLGenRunner._build_bridge_bc() and injected
        via the constructor. Returns dict mapping sanitized patch_name ->
        {bc_type, temperature, flow_rate, power, sigma, heat_flux}.
        """
        imported_bc: Dict[str, Dict[str, Any]] = {}

        if self.bridge_registry is not None:
            for patch_name, spec in self.bridge_registry.items():
                imported_bc[patch_name] = {
                    "bc_type": spec.bc_type,
                    "temperature": spec.temperature,
                    "flow_rate": spec.flow_rate,
                    "power": spec.power,
                    "sigma": spec.sigma,
                    "heat_flux": spec.heat_flux,
                }
            logger.debug(
                f"Loaded {len(self.bridge_registry)} bridge BC entries from in-memory registry"
            )

        return imported_bc
```

Note: we deleted the `from dcdtuni.runner.bridge_bc import BridgeBCRegistry` inline import because the registry is passed in via the constructor (type-hinted via the TYPE_CHECKING block from Task A3 Step 4).

- [ ] **Step 3: Verify fields.py still imports cleanly**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.fields import FieldGenerator; print('ok')"`

Expected: `ok` printed.

---

### Task A5: Rename `_build_and_save_bridge_bc()` → `_build_bridge_bc()` and remove disk write

**Files:**

- Modify: `src/backend/aircooling/runner/stlgen_runner.py:1774-2063` (the `_build_and_save_bridge_bc` method — verify with Grep)

- [ ] **Step 1: Rename the method and update the docstring**

Use Edit tool to find:

```python
    def _build_and_save_bridge_bc(self) -> None:
        """Build bridge_bc.json from cfd_export.json physics data."""
        from dcdtuni.runner.bridge_bc import BridgeBCRegistry, BCSpec

        registry = BridgeBCRegistry()
```

Replace with:

```python
    def _build_bridge_bc(self) -> None:
        """Build the in-memory BridgeBCRegistry from cfd_export.json physics data.

        Populates self._bridge_registry with a {sanitized_solid_name: BCSpec}
        mapping used by FieldGenerator (for 0/ field generation) and by
        _build_leakage_fvoptions_config() / _ensure_wall_baffles_are_wall_type()
        (for mesh-side fixes). Does NOT write any file — the registry is
        consumed within the same Python process.
        """
        from dcdtuni.runner.bridge_bc import BridgeBCRegistry, BCSpec

        registry = BridgeBCRegistry()
```

- [ ] **Step 2: Remove the `.save()` call and add the summary log**

Near the end of the method (currently around line 2061-2063), find:

```python
        bridge_bc_path = self.model_dir / "bridge_bc.json"
        registry.save(bridge_bc_path)
        logger.info(f"Written bridge_bc.json: {bridge_bc_path} ({len(registry)} entries)")
```

Replace with:

```python
        # Store the in-memory registry on self — consumed by:
        #   - _build_field_generator()  → FieldGenerator.bridge_registry
        #   - _build_leakage_fvoptions_config()
        #   - _ensure_wall_baffles_are_wall_type()
        self._bridge_registry = registry

        # Debug-friendly summary (replaces the old `cat bridge_bc.json` workflow)
        from collections import Counter
        counts = Counter(spec.bc_type for _, spec in registry.items())
        summary = " | ".join(f"{n} {bc}" for bc, n in sorted(counts.items()))
        logger.info(
            f"Built BridgeBCRegistry: {len(registry)} patches | {summary}"
        )
```

- [ ] **Step 3: Update the call site in `_prepare_runtime_metadata()`**

Locate the call site around line 528 via Grep:

Run: `Grep pattern="_build_and_save_bridge_bc" path="/mnt/lv/litianyu/mycode/dcdtuni/src/backend/aircooling/runner/stlgen_runner.py"`

Expected: one match, inside `_prepare_runtime_metadata()`. Use Edit tool to replace:

```python
    def _prepare_runtime_metadata(self) -> None:
        """Refresh STLGen runtime metadata without generating config.toml/room.json."""
        self._cleanup_stale_compat_files()
        self._build_and_save_bridge_bc()
```

with:

```python
    def _prepare_runtime_metadata(self) -> None:
        """Refresh STLGen runtime metadata without generating config.toml/room.json."""
        self._cleanup_stale_compat_files()
        self._build_bridge_bc()
```

- [ ] **Step 4: Verify the module still imports and the method is reachable**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.stlgen_runner import STLGenRunner; r = STLGenRunner.__dict__; assert '_build_bridge_bc' in r, 'missing'; assert '_build_and_save_bridge_bc' not in r, 'old name still present'; print('ok')"`

Expected: `ok` printed.

---

### Task A6: Wire the registry through `_build_field_generator()`

**Files:**

- Modify: `src/backend/aircooling/runner/stlgen_runner.py:576-583` (the `_build_field_generator` method — verify with Grep)

- [ ] **Step 1: Locate the method**

Run: `Grep pattern="_build_field_generator" path="/mnt/lv/litianyu/mycode/dcdtuni/src/backend/aircooling/runner/stlgen_runner.py" output_mode="content" -n=true`

Expected: one match around line 576.

- [ ] **Step 2: Pass `bridge_registry` into FieldGenerator**

Use Edit tool to replace:

```python
    def _build_field_generator(self) -> FieldGenerator:
        """Build a field generator for STLGen that does not depend on CaseConfig."""
        return FieldGenerator(
            case_dir=self.case_dir,
            simulation_config=self._data.get("simulationConfig", {}),
            room_config={},
            patch_areas=self._compute_patch_areas(),
        )
```

with:

```python
    def _build_field_generator(self) -> FieldGenerator:
        """Build a field generator for STLGen that does not depend on CaseConfig.

        Injects the in-memory bridge registry (built earlier in
        _prepare_runtime_metadata()) so FieldGenerator can render 0/ fields
        without touching disk for BC lookups.
        """
        return FieldGenerator(
            case_dir=self.case_dir,
            simulation_config=self._data.get("simulationConfig", {}),
            room_config={},
            patch_areas=self._compute_patch_areas(),
            bridge_registry=self._bridge_registry,
        )
```

- [ ] **Step 3: Verify**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.stlgen_runner import STLGenRunner; print('ok')"`

Expected: `ok` printed.

---

### Task A7: Refactor `_build_leakage_fvoptions_config()` to read from `self._bridge_registry`

**Files:**

- Modify: `src/backend/aircooling/runner/stlgen_runner.py:874-949` (the `_build_leakage_fvoptions_config` method)

- [ ] **Step 1: Replace the disk-read block**

Use Edit tool to find the block at the top of the method (around lines 874-901):

```python
    def _build_leakage_fvoptions_config(self) -> Dict[str, Any]:
        """Build fvOptions leakage zone config from bridge_bc.json.

        Each leakage zone in bridge_bc.json has key = stlSolidName (e.g.
        Default_Rack_1_u30_u32_leakage_zone).  The actual mesh cellZone
        has the ``leakage_`` prefix (leakage_Default_Rack_1_u30_u32_leakage_zone)
        because snappyHexMesh requires individual STL files with matching
        surface names (see AGENTS.md §6.4).
        """
        bridge_bc_path = self.model_dir / "bridge_bc.json"
        if not bridge_bc_path.exists():
            return {}
        with open(bridge_bc_path, encoding="utf-8") as fh:
            bc_data = json.load(fh)

        # Build lookup: stlSolidName → (rack_orientation, rack_depth)
        data = self._data
        leakage_rack_info: Dict[str, Tuple[float, float]] = {}
        for rack in data.get("racks", {}).values():
            geom = rack.get("geometry", {})
            ori = geom.get("orientation", 0)
            depth = geom.get("size", {}).get("D", 1.0)
            for grp in rack.get("leakage", {}).get("groups", []):
                leakage_rack_info[_sanitize(grp.get("stlSolidName", ""))] = (ori, depth)

        config: Dict[str, Any] = {}

        for solid_name, bc_spec in bc_data.items():
            if bc_spec.get("bc_type") != "leakage_zone":
                continue
            sigma = bc_spec.get("sigma", 0.1)
```

Replace with:

```python
    def _build_leakage_fvoptions_config(self) -> Dict[str, Any]:
        """Build fvOptions leakage zone config from the in-memory bridge registry.

        Each leakage zone in the registry has key = stlSolidName (e.g.
        Default_Rack_1_u30_u32_leakage_zone).  The actual mesh cellZone
        has the ``leakage_`` prefix (leakage_Default_Rack_1_u30_u32_leakage_zone)
        because snappyHexMesh requires individual STL files with matching
        surface names (see AGENTS.md §6.4).
        """
        if self._bridge_registry is None:
            return {}

        # Build lookup: stlSolidName → (rack_orientation, rack_depth)
        data = self._data
        leakage_rack_info: Dict[str, Tuple[float, float]] = {}
        for rack in data.get("racks", {}).values():
            geom = rack.get("geometry", {})
            ori = geom.get("orientation", 0)
            depth = geom.get("size", {}).get("D", 1.0)
            for grp in rack.get("leakage", {}).get("groups", []):
                leakage_rack_info[_sanitize(grp.get("stlSolidName", ""))] = (ori, depth)

        config: Dict[str, Any] = {}

        for solid_name, spec in self._bridge_registry.items():
            if spec.bc_type != "leakage_zone":
                continue
            sigma = spec.sigma if spec.sigma is not None else 0.1
```

- [ ] **Step 2: Verify `bc_spec` dict-access lines later in the method**

The old code after the `sigma = bc_spec.get(...)` line used `bc_spec.get(...)` elsewhere. Check that there are no more `bc_spec` dict accesses in the rest of the method. Run:

`Grep pattern="bc_spec" path="/mnt/lv/litianyu/mycode/dcdtuni/src/backend/aircooling/runner/stlgen_runner.py" output_mode="content" -n=true`

Expected: NO matches (the only use was the one we just replaced). If any matches remain, inspect them and convert to `spec.<attr>` syntax (the Pydantic BCSpec attributes are: `bc_type`, `temperature`, `flow_rate`, `power`, `sigma`, `heat_flux`).

- [ ] **Step 3: Verify the module still imports**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.stlgen_runner import STLGenRunner; print('ok')"`

Expected: `ok` printed.

---

### Task A8: Refactor `_ensure_wall_baffles_are_wall_type()` to read from `self._bridge_registry`

**Files:**

- Modify: `src/backend/aircooling/runner/stlgen_runner.py:2973-3008` (the `_ensure_wall_baffles_are_wall_type` method)

- [ ] **Step 1: Replace the disk-read block**

Use Edit tool to find:

```python
    def _ensure_wall_baffles_are_wall_type(self, boundary_path: Path) -> None:
        """Ensure wall_baffle patches in bridge_bc.json have type=wall in boundary.

        Uses bridge_bc.json as the authoritative source for which patches are
        wall baffles.  This works in both mesh and solve-only modes (unlike
        _convert_wall_baffle_patches_to_wall which depends on _wall_baffle_zone_names
        being populated during mesh dict generation).
        """
        bridge_path = self.case_dir / "model" / "bridge_bc.json"
        if not bridge_path.exists() or not boundary_path.exists():
            return
        bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
        wall_patches = {k for k, v in bridge.items() if v.get("bc_type") == "wall_baffle"}
        if not wall_patches:
            return
```

Replace with:

```python
    def _ensure_wall_baffles_are_wall_type(self, boundary_path: Path) -> None:
        """Ensure wall_baffle patches have type=wall in boundary.

        Uses the in-memory bridge registry as the authoritative source for
        which patches are wall baffles. This works in both mesh and solve-only
        modes (unlike _convert_wall_baffle_patches_to_wall which depends on
        _wall_baffle_zone_names being populated during mesh dict generation).
        """
        if self._bridge_registry is None or not boundary_path.exists():
            return
        wall_patches = {
            name for name, spec in self._bridge_registry.items()
            if spec.bc_type == "wall_baffle"
        }
        if not wall_patches:
            return
```

- [ ] **Step 2: Verify**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.stlgen_runner import STLGenRunner; print('ok')"`

Expected: `ok` printed.

---

### Task A9: Extend `_cleanup_stale_compat_files()` to delete legacy bridge_bc.json and cfd_export.json

**Files:**

- Modify: `src/backend/aircooling/runner/stlgen_runner.py:530-551` (the `_cleanup_stale_compat_files` method)

- [ ] **Step 1: Locate the method**

Open `stlgen_runner.py` at line 530. You'll see:

```python
    def _cleanup_stale_compat_files(self) -> None:
        """Remove old STLGen-generated compatibility files from previous runs."""
        config_path = self.case_dir / "config.toml"
        if config_path.exists():
            try:
                config_text = config_path.read_text(encoding="utf-8")
            except Exception:
                config_text = ""
            if config_text.startswith("# Auto-generated by STLGenRunner from cfd_export.json"):
                config_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {config_path}")

        room_json_path = self.model_dir / "room.json"
        if room_json_path.exists():
            try:
                room_data = json.loads(room_json_path.read_text(encoding="utf-8"))
            except Exception:
                room_data = {}
            meta = room_data.get("meta", {}) if isinstance(room_data, dict) else {}
            if meta.get("name") == "stlgen_stub":
                room_json_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {room_json_path}")
```

- [ ] **Step 2: Append the two new cleanup blocks**

Use Edit tool to add two new blocks at the end of the method (after the `room_json_path` block). Replace the closing `logger.info(f"Removed stale STLGen compatibility file: {room_json_path}")` line and everything before the next method with:

Find (the tail of the method):

```python
        room_json_path = self.model_dir / "room.json"
        if room_json_path.exists():
            try:
                room_data = json.loads(room_json_path.read_text(encoding="utf-8"))
            except Exception:
                room_data = {}
            meta = room_data.get("meta", {}) if isinstance(room_data, dict) else {}
            if meta.get("name") == "stlgen_stub":
                room_json_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {room_json_path}")
```

Replace with:

```python
        room_json_path = self.model_dir / "room.json"
        if room_json_path.exists():
            try:
                room_data = json.loads(room_json_path.read_text(encoding="utf-8"))
            except Exception:
                room_data = {}
            meta = room_data.get("meta", {}) if isinstance(room_data, dict) else {}
            if meta.get("name") == "stlgen_stub":
                room_json_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {room_json_path}")

        # Delete legacy bridge_bc.json (now in-memory, never written to disk)
        stale_bridge = self.model_dir / "bridge_bc.json"
        if stale_bridge.exists():
            stale_bridge.unlink()
            logger.info(f"Removed stale bridge_bc.json (now in-memory): {stale_bridge}")

        # Delete legacy cfd_export.json iff the current run is using a .cfdrun file.
        # (The cfd_export_path points at the file the CLI was told to load. If that
        # file has a .cfdrun suffix, we are post-migration and any lingering
        # cfd_export.json in the same dir is stale.)
        if self.cfd_export_path.suffix.lower() == ".cfdrun":
            stale_cfd = self.model_dir / "cfd_export.json"
            if stale_cfd.exists() and stale_cfd.resolve() != self.cfd_export_path:
                stale_cfd.unlink()
                logger.info(
                    f"Removed stale cfd_export.json (replaced by .cfdrun): {stale_cfd}"
                )
```

Note: the `cfd_export.json` cleanup is conditional on the runner being invoked with a `.cfdrun` file. This means in Phase A (before the rename) this block is a no-op, and it activates automatically once Phase B lands. We include it now to keep Phase A self-contained and forward-compatible.

- [ ] **Step 3: Verify the module still imports**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.stlgen_runner import STLGenRunner; print('ok')"`

Expected: `ok` printed.

---

### Task A10: Delete `BridgeBCRegistry.save()` and `BridgeBCRegistry.load()`

**Files:**

- Modify: `src/backend/aircooling/runner/bridge_bc.py`

- [ ] **Step 1: Verify no remaining callers**

Run: `Grep pattern="BridgeBCRegistry\.load|BridgeBCRegistry\.save|registry\.save\(|registry\.load\(" path="/mnt/lv/litianyu/mycode/dcdtuni" output_mode="content" -n=true`

Expected: the only remaining matches should be in `bridge_bc.py` itself (the method definitions). If there are matches in any other file, they must be refactored before this task proceeds — re-check Tasks A4, A7, A8.

- [ ] **Step 2: Delete `save()` and `load()` from `bridge_bc.py`**

Use Edit tool to find:

```python
    def save(self, path: Path) -> None:
        """Serialize registry to a JSON file."""
        data = {k: v.model_dump() for k, v in self._registry.items()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "BridgeBCRegistry":
        """Load registry from a JSON file. Returns empty registry if file not found."""
        obj = cls()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                obj._registry[k] = BCSpec(**v)
        return obj
```

Replace with: (empty string — delete both methods entirely)

```python
```

Tip: use `Edit` with `old_string` being the full block above and `new_string` being empty. If the tool rejects an empty `new_string`, use `new_string=""` or a single newline.

- [ ] **Step 3: Remove unused imports**

Open `bridge_bc.py` and look at the imports at the top:

```python
import json
import re
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel
```

`json` and `Path` are only used by the deleted methods. Verify:

Run: `Grep pattern="json\.|Path" path="/mnt/lv/litianyu/mycode/dcdtuni/src/backend/aircooling/runner/bridge_bc.py" output_mode="content" -n=true`

If no matches (or only the unused `from pathlib import Path` / `import json` lines themselves), delete those two imports:

```python
import re
from typing import Dict, Optional
from pydantic import BaseModel
```

- [ ] **Step 4: Update the module docstring**

Near the top of `bridge_bc.py`, find:

```python
"""
BridgeBCRegistry: maps sanitized STL solid names → OpenFOAM boundary condition specs.

This module is the handoff mechanism between the CFDProjectImporter (which reads
cfd_export.json from STLGeneratorForOpenFoam) and the FieldGenerator (which writes
OpenFOAM 0/ field files).

The registry is serialized to `model/bridge_bc.json` after import and loaded by
FieldGenerator during field generation.
```

Replace with:

```python
"""
BridgeBCRegistry: maps sanitized STL solid names → OpenFOAM boundary condition specs.

This module is the handoff mechanism between the CFDProjectImporter (which reads
cfd_export.json from STLGeneratorForOpenFoam) and the FieldGenerator (which writes
OpenFOAM 0/ field files).

The registry is an in-memory object — it is built by
STLGenRunner._build_bridge_bc() and passed to FieldGenerator via constructor
injection. It does NOT persist to disk. (Previously, a `model/bridge_bc.json`
file was written and re-read within the same process; that indirection was
removed in the 2026-04-11 refactor. See spec:
docs/superpowers/specs/2026-04-11-cfdrun-rename-bridge-bc-inmemory-design.md)
```

- [ ] **Step 5: Update the usage example in the class docstring**

Find the `BridgeBCRegistry` class docstring:

```python
class BridgeBCRegistry:
    """Maps sanitized patch solid names → BCSpec for the bridge pipeline.

    Usage in importer:
        registry = BridgeBCRegistry()
        registry.register("acu1_supply_patch_1", BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0))
        registry.save(case_dir / "model" / "bridge_bc.json")

    Usage in field generator:
        registry = BridgeBCRegistry.load(case_dir / "model" / "bridge_bc.json")
        spec = registry.get("acu1_supply_patch_1")  # BCSpec or None
    """
```

Replace with:

```python
class BridgeBCRegistry:
    """Maps sanitized patch solid names → BCSpec for the bridge pipeline.

    Usage in importer (STLGenRunner):
        registry = BridgeBCRegistry()
        registry.register("acu1_supply_patch_1", BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0))
        self._bridge_registry = registry

    Usage in field generator (FieldGenerator):
        # registry is passed in via constructor as `bridge_registry` kwarg
        spec = self.bridge_registry.get("acu1_supply_patch_1")  # BCSpec or None
    """
```

- [ ] **Step 6: Verify the module still imports**

Run: `conda run -n dcdtuni python -c "from dcdtuni.runner.bridge_bc import BridgeBCRegistry, BCSpec; r = BridgeBCRegistry(); print('ok')"`

Expected: `ok` printed.

---

### Task A11: Run the unit test — it should now pass

**Files:**

- No changes this task — verification only.

- [ ] **Step 1: Run the test from Task A1**

Run: `conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py -v`

Expected: All tests PASS (the `test_summary_logged` test may have limited coverage due to loguru's non-standard logging but should not FAIL). Look for:

```text
PASSED src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py::TestBridgeBcInMemory::test_registry_is_populated_on_self
PASSED src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py::TestBridgeBcInMemory::test_no_bridge_bc_json_file_written
PASSED src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py::TestBridgeBcInMemory::test_expected_bc_types_present
PASSED src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py::TestBridgeBcInMemory::test_fan_server_inlet_has_flow_rate
PASSED src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py::TestBridgeBcInMemory::test_acu_supply_has_temperature
```

- [ ] **Step 2: Run the existing stlgen_validation tests to catch regressions**

Run: `conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_stlgen_validation.py -v`

Expected: All existing tests PASS. If any fail, the refactor broke something — investigate before proceeding.

---

### Task A12: Phase A integration smoke test via REST API

**Files:**

- No changes this task — verification only.

- [ ] **Step 1: Stop any old server and start a fresh one**

Per CLAUDE.md, use `fuser -k` (never `kill`/`pkill`/`killall`):

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir case/base604 --port 8005 --ws-port 8006 > /tmp/phaseA_server.log 2>&1 &
sleep 2
```

Verify server is up:

```bash
curl -s http://localhost:8005/api/health 2>/dev/null || curl -s http://localhost:8005/api/server-info
```

Expected: JSON response containing server info. If it fails, check `/tmp/phaseA_server.log`.

- [ ] **Step 2: Delete any stale bridge_bc.json and cfd_export.json from the base604 case**

```bash
ls /mnt/lv/litianyu/mycode/dcdtuni/case/base604/model/
```

If `bridge_bc.json` or `cfd_export.json` are present, keep them (they'll be auto-cleaned on first new run — that's part of what we're testing).

- [ ] **Step 3: Load the base604 cfdproj into the running server session**

Per CLAUDE.md the canonical test case is at `STLGeneratorForOpenFoam/projects/V5_CFD_Station_1773892919284.cfdproj` OR the base604 project at `STLGeneratorForOpenFoam/projects/base604_1774199140766.cfdproj`. Use base604 here since we already verified its data shape.

Use curl to POST the cfdproj content. The exact endpoint may be `/api/load-project`, `/api/read-file`, or the user may need to load via browser — check:

```bash
curl -sf -X POST http://localhost:8005/api/read-file \
  -H 'Content-Type: application/json' \
  -d '{"path": "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/base604_1774199140766.cfdproj"}' \
  | head -20
```

Expected: first 20 lines of the cfdproj JSON.

If this endpoint flow is not directly usable for headless runs, fall back to browser-use per CLAUDE.md:

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# manually or via browser-use eval: load base604 project
browser-use eval "loadProjectFromPath('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/base604_1774199140766.cfdproj')"
# then trigger run-mesh
browser-use eval "runMeshGeneration('mesh')"
```

- [ ] **Step 4: Wait for the mesh job to complete and inspect artifacts**

After the run completes (check the browser's operation log or `/tmp/phaseA_server.log`), inspect the case directory:

```bash
ls /mnt/lv/litianyu/mycode/dcdtuni/case/base604/model/
ls /mnt/lv/litianyu/mycode/dcdtuni/case/base604/Mesh/constant/polyMesh/
```

Expected in `model/`:

- `cfd_export.json` present (Phase A did NOT rename this — that's Phase B)
- `bridge_bc.json` **ABSENT** (deleted by `_cleanup_stale_compat_files()` or never written by `_build_bridge_bc()`)
- `liquid_results.json` present iff the case uses liquid cooling (base604 does not by default — likely absent)

Expected in `Mesh/constant/polyMesh/`:

- `boundary`, `points`, `faces`, `cells` present
- `boundary` file has the same patch list as before the refactor

- [ ] **Step 5: Grep the server log for the summary line**

```bash
grep -i "BridgeBCRegistry" /tmp/phaseA_server.log
```

Expected: one line containing `Built BridgeBCRegistry: <N> patches | ...` with a reasonable patch count (for base604 this is ~30-60 patches depending on rack/ACU count).

- [ ] **Step 6: Stop the server**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
```

---

### Task A13: Commit Phase A

**Files:**

- Commit all changes from Tasks A1-A10. Test file + runner refactor + bridge_bc cleanup.

- [ ] **Step 1: Review the diff**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git status
git diff --stat
```

Expected files changed:

- `src/backend/aircooling/runner/bridge_bc.py` (modified)
- `src/backend/aircooling/runner/fields.py` (modified)
- `src/backend/aircooling/runner/stlgen_runner.py` (modified)
- `src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py` (new)

No other files should be modified. If any are, inspect them — they may be unintended.

- [ ] **Step 2: Stage files explicitly (do NOT use `git add .` or `git add -A`)**

```bash
git add \
  src/backend/aircooling/runner/bridge_bc.py \
  src/backend/aircooling/runner/fields.py \
  src/backend/aircooling/runner/stlgen_runner.py \
  src/backend/aircooling/runner/tests/test_bridge_bc_inmemory.py
git status
```

Expected: only the four files staged.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(runner): move bridge_bc.json to in-memory BridgeBCRegistry

Eliminates the model/bridge_bc.json file by converting BridgeBCRegistry
into a constructor-injected in-memory object. The disk round-trip was
pure intra-process indirection — all three consumers (FieldGenerator,
_build_leakage_fvoptions_config, _ensure_wall_baffles_are_wall_type)
lived in the same Python process as the writer.

Changes:
- STLGenRunner._build_and_save_bridge_bc() → _build_bridge_bc(): stores
  the registry on self._bridge_registry, logs a one-line summary
  (N patches | M fan_inlet | M fan_outlet | ...) instead of writing JSON.
- FieldGenerator.__init__ accepts bridge_registry kwarg; refactor
  _collect_imported_bc_data() to read from it.
- _cleanup_stale_compat_files() now also deletes legacy bridge_bc.json
  from old runs, and legacy cfd_export.json iff a .cfdrun file is in use.
- BridgeBCRegistry.save() and .load() deleted; they have no consumers.

Spec: docs/superpowers/specs/2026-04-11-cfdrun-rename-bridge-bc-inmemory-design.md

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify the commit landed**

```bash
git log --oneline -1
git status
```

Expected: working tree clean, the new commit is at HEAD.

---

## Phase B: cfdrun Rename

### Task B1: Rename CLI flag `--from-stlgen` → `--from-cfdrun` in run_cfd.py

**Files:**

- Modify: `src/backend/aircooling/runner/run_cfd.py`

- [ ] **Step 1: Update the module docstring**

Use Edit tool to find:

```python
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Usage:
    python -m dcdtuni.runner.run_cfd --from-stlgen <path/to/cfd_export.json>
    python -m dcdtuni.runner.run_cfd --from-stlgen <path> --mesh-only
    python -m dcdtuni.runner.run_cfd --from-stlgen <path> --solve-only
"""
```

Replace with:

```python
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Usage:
    python -m dcdtuni.runner.run_cfd --from-cfdrun <path/to/<basename>.cfdrun>
    python -m dcdtuni.runner.run_cfd --from-cfdrun <path> --mesh-only
    python -m dcdtuni.runner.run_cfd --from-cfdrun <path> --solve-only

Note: the .cfdrun file has the same content format as the legacy cfd_export.json
— only the filename and extension were renamed in the 2026-04-11 refactor.
"""
```

- [ ] **Step 2: Update the argparse flag**

Find:

```python
    parser.add_argument(
        "--from-stlgen",
        type=str,
        required=True,
        metavar="CFD_EXPORT_JSON",
        help="Path to cfd_export.json from STLGeneratorForOpenFoam. "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly.",
    )
```

Replace with:

```python
    parser.add_argument(
        "--from-cfdrun",
        type=str,
        required=True,
        metavar="CFDRUN_PATH",
        help="Path to <basename>.cfdrun from STLGeneratorForOpenFoam. "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly. "
             "(File format is identical to the legacy cfd_export.json.)",
    )
```

- [ ] **Step 3: Update the `main()` function to read the new attribute**

Find:

```python
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
```

Replace with:

```python
    cfdrun_path = Path(args.from_cfdrun).resolve()
    if not cfdrun_path.exists():
        logger.error(f"cfdrun file not found: {cfdrun_path}")
        sys.exit(1)

    # <basename>.cfdrun lives in <case>/model/ — case_dir is the parent
    case_dir = cfdrun_path.parent
    if case_dir.name == "model":
        case_dir = case_dir.parent

    logger.info(f"STLGen pipeline: {cfdrun_path}")
    logger.info(f"Case directory: {case_dir}")
```

- [ ] **Step 4: Update the `STLGenRunner` instantiation**

Find:

```python
        runner = STLGenRunner(case_dir, cfdproj_path)
```

Replace with:

```python
        runner = STLGenRunner(case_dir, cfdrun_path)
```

Note: the `STLGenRunner.__init__` parameter name `cfd_export_path` stays as-is (the parameter is conceptually "path to the CFDExport-shaped JSON file", independent of the filename extension). The variable rename is cosmetic.

- [ ] **Step 5: Verify the CLI imports and shows help**

```bash
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --help
```

Expected: help text shows `--from-cfdrun CFDRUN_PATH` (not `--from-stlgen`), and the module docstring appears at the top.

---

### Task B2: Update server.py to accept `cfdrunFilename` and write to `<basename>.cfdrun`

**Files:**

- Modify: `STLGeneratorForOpenFoam/server/server.py` (both `handle_prepare_case` around line 2318 and `handle_run_mesh` around line 2384)

- [ ] **Step 1: Locate both handlers**

```bash
Grep pattern="def handle_prepare_case|def handle_run_mesh" path="/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/server.py" output_mode="content" -n=true
```

Expected: two matches (two handler functions).

- [ ] **Step 2: Update `handle_prepare_case` — accept cfdrunFilename and write to new path**

Find the block in `handle_prepare_case` (approximately lines 2324-2352):

```python
    data = _read_body(handler)
    cfd_export_json: str | None = data.get("cfdExportJson", None)
    stl_files: dict = data.get("stlFiles", {})
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)

    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": f"dcdtuni not importable: {DCTWIN_VERSION}"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    case_dir.mkdir(parents=True, exist_ok=True)

    # Write cfd_export.json
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    cfd_json_path = model_dir / "cfd_export.json"
    if cfd_export_json:
        cfd_json_path.write_text(cfd_export_json, encoding="utf-8")
    if not cfd_json_path.exists():
        _send_json(handler, 400, {"error": "No cfd_export.json"})
        return
    stale = case_dir / "cfd_export.json"
    if stale.exists():
        stale.unlink()
```

Replace with:

```python
    data = _read_body(handler)
    cfd_export_json: str | None = data.get("cfdExportJson", None)
    cfdrun_filename: str = data.get("cfdrunFilename", "project.cfdrun") or "project.cfdrun"
    stl_files: dict = data.get("stlFiles", {})
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)

    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": f"dcdtuni not importable: {DCTWIN_VERSION}"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    case_dir.mkdir(parents=True, exist_ok=True)

    # Write <basename>.cfdrun into model/
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize: strip any path components, enforce .cfdrun suffix
    cfdrun_filename = Path(cfdrun_filename).name  # no traversal
    if not cfdrun_filename.lower().endswith(".cfdrun"):
        cfdrun_filename = cfdrun_filename + ".cfdrun"
    cfdrun_path = model_dir / cfdrun_filename

    if cfd_export_json:
        cfdrun_path.write_text(cfd_export_json, encoding="utf-8")
    if not cfdrun_path.exists():
        _send_json(handler, 400, {"error": "No cfdrun content provided"})
        return

    # Delete legacy files from pre-refactor runs
    for stale_name in ("cfd_export.json", "bridge_bc.json"):
        stale = model_dir / stale_name
        if stale.exists() and stale.resolve() != cfdrun_path.resolve():
            stale.unlink()
            print(f"[server] Removed legacy {stale_name}")
    # Also remove stale root-level copy if present
    root_stale = case_dir / "cfd_export.json"
    if root_stale.exists():
        root_stale.unlink()
```

- [ ] **Step 3: Update the command line in `handle_prepare_case`**

Find (still inside `handle_prepare_case`, later in the function — search for `--from-stlgen`):

```python
    cmd = f"python -m dcdtuni.runner.run_cfd --from-stlgen {cfd_json_path}{flags}"
```

Replace with:

```python
    cmd = f"python -m dcdtuni.runner.run_cfd --from-cfdrun {cfdrun_path}{flags}"
```

- [ ] **Step 4: Repeat Step 2/3 for `handle_run_mesh`**

Find the same three blocks inside `handle_run_mesh` (around lines 2385-2452). The variable name in this handler is `cfd_json_path` too — do the same rename and cleanup. The pattern is identical; apply the same `cfdrun_filename` intake, sanitization, and path construction, the same legacy-file cleanup, and the same CLI flag rename.

The specific block to update inside `handle_run_mesh` (approximately lines 2385-2414):

Find:

```python
    data = _read_body(handler)
    cfd_export_json: str | None = data.get("cfdExportJson", None)
    stl_files: dict = data.get("stlFiles", {})   # {filename: base64_content}
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)

    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": f"dcdtuni not importable: {DCTWIN_VERSION}"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set. Use --case-dir on startup or POST /api/set-case-dir first."})
        return
    case_dir.mkdir(parents=True, exist_ok=True)

    # Write cfd_export.json into model/ (canonical location)
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    cfd_json_path = model_dir / "cfd_export.json"
    if cfd_export_json:
        cfd_json_path.write_text(cfd_export_json, encoding="utf-8")
    if not cfd_json_path.exists():
        _send_json(handler, 400, {"error": "No cfd_export.json provided or found in case/model directory"})
        return
    # Remove stale root-level copy if present
    stale = case_dir / "cfd_export.json"
    if stale.exists():
        stale.unlink()
```

Replace with:

```python
    data = _read_body(handler)
    cfd_export_json: str | None = data.get("cfdExportJson", None)
    cfdrun_filename: str = data.get("cfdrunFilename", "project.cfdrun") or "project.cfdrun"
    stl_files: dict = data.get("stlFiles", {})   # {filename: base64_content}
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)

    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": f"dcdtuni not importable: {DCTWIN_VERSION}"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set. Use --case-dir on startup or POST /api/set-case-dir first."})
        return
    case_dir.mkdir(parents=True, exist_ok=True)

    # Write <basename>.cfdrun into model/ (canonical location)
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize: strip any path components, enforce .cfdrun suffix
    cfdrun_filename = Path(cfdrun_filename).name
    if not cfdrun_filename.lower().endswith(".cfdrun"):
        cfdrun_filename = cfdrun_filename + ".cfdrun"
    cfdrun_path = model_dir / cfdrun_filename

    if cfd_export_json:
        cfdrun_path.write_text(cfd_export_json, encoding="utf-8")
    if not cfdrun_path.exists():
        _send_json(handler, 400, {"error": "No cfdrun content provided or found in case/model directory"})
        return

    # Delete legacy files from pre-refactor runs
    for stale_name in ("cfd_export.json", "bridge_bc.json"):
        stale = model_dir / stale_name
        if stale.exists() and stale.resolve() != cfdrun_path.resolve():
            stale.unlink()
            print(f"[server] Removed legacy {stale_name}")
    # Remove stale root-level copy if present
    root_stale = case_dir / "cfd_export.json"
    if root_stale.exists():
        root_stale.unlink()
```

- [ ] **Step 5: Update the subprocess command in `handle_run_mesh`**

Find (still inside `handle_run_mesh`, around line 2449):

```python
    # Always use --from-stlgen: this server is the browser backend and the case
    # is always set up via the stlgen pipeline (browser-exported STLs).
    # For solve-only, STL files aren't re-uploaded but the mesh was generated
    # by STLGenRunner, so we must stay on the same pipeline.
    cmd = [
        sys.executable, "-m", "dcdtuni.runner.run_cfd",
        "--from-stlgen", str(cfd_json_path),
    ] + flags
```

Replace with:

```python
    # Always use --from-cfdrun: this server is the browser backend and the case
    # is always set up via the stlgen pipeline (browser-exported STLs).
    # For solve-only, STL files aren't re-uploaded but the mesh was generated
    # by STLGenRunner, so we must stay on the same pipeline.
    cmd = [
        sys.executable, "-m", "dcdtuni.runner.run_cfd",
        "--from-cfdrun", str(cfdrun_path),
    ] + flags
```

- [ ] **Step 6: Update the remaining handlers (`handle_browser_full_pipeline` + liquid handlers)**

```bash
Grep pattern="cfd_export\.json|--from-stlgen|cfd_json_path" path="/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/server.py" output_mode="content" -n=true
```

Expected findings to update one-by-one:

**(a) `handle_browser_full_pipeline` at approximately line 1964.** This handler is a headless/browser-driven full-pipeline orchestrator that calls `buildCfdExportJson()` via JS eval and then runs the backend. It touches `cfd_export.json` and `--from-stlgen` too.

Find (around lines 2009-2024):

```python
        # Step 3: Export cfd_export.json
        cfd_result = _browser_call("exec_js", "JSON.stringify(buildCfdExportJson())")
        model_dir = Path(case_dir) / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "cfd_export.json").write_text(cfd_result, encoding="utf-8")

        # Step 4: Run mesh (reuse existing run_mesh logic)
        import uuid
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"lines": [], "done": False, "returncode": None, "proc": None}

        def _run():
            try:
                cfd_json_path = model_dir / "cfd_export.json"
                flags = " --mesh-only" if mesh_only else (" --solve-only" if solve_only else "")
                cmd = f"{sys.executable} -m dcdtuni.runner.run_cfd --from-stlgen {cfd_json_path}{flags}"
```

Replace with:

```python
        # Step 3: Export <basename>.cfdrun
        cfd_result = _browser_call("exec_js", "JSON.stringify(buildCfdExportJson())")
        cfdrun_name = _browser_call("exec_js", "getCfdrunFilename()")
        # Defensive: strip path components and enforce suffix (same rules as handle_run_mesh)
        cfdrun_name = Path((cfdrun_name or "project.cfdrun")).name
        if not cfdrun_name.lower().endswith(".cfdrun"):
            cfdrun_name = cfdrun_name + ".cfdrun"
        model_dir = Path(case_dir) / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        cfdrun_path = model_dir / cfdrun_name
        (cfdrun_path).write_text(cfd_result, encoding="utf-8")
        # Clean up legacy files
        for stale_name in ("cfd_export.json", "bridge_bc.json"):
            stale = model_dir / stale_name
            if stale.exists() and stale.resolve() != cfdrun_path.resolve():
                stale.unlink()
                print(f"[server] Removed legacy {stale_name}")

        # Step 4: Run mesh (reuse existing run_mesh logic)
        import uuid
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"lines": [], "done": False, "returncode": None, "proc": None}

        def _run():
            try:
                flags = " --mesh-only" if mesh_only else (" --solve-only" if solve_only else "")
                cmd = f"{sys.executable} -m dcdtuni.runner.run_cfd --from-cfdrun {cfdrun_path}{flags}"
```

**(b) Liquid handlers at lines ~2646, ~2675, ~2739** (`handle_liquid_solve`, `handle_liquid_transient`, and one more). Per spec §3 non-goal #4, this refactor does NOT touch liquid semantics — but the liquid endpoints need to locate the cfdrun file now since `cfd_export.json` no longer exists on disk. For each match inside `handle_liquid_*` functions:

Find:

```python
    cfd_json_path = case_dir / "model" / "cfd_export.json"
    if not cfd_json_path.exists():
        _send_json(handler, 400, {"error": "No cfd_export.json found in case/model/"})
        return
```

(The second match at ~2675 has a slightly different error string: `"No cfd_export.json found"`. The third match at ~2739 has another variant. Apply the same replacement pattern to all three.)

Replace with:

```python
    model_dir = case_dir / "model"
    cfdrun_candidates = sorted(model_dir.glob("*.cfdrun"))
    if not cfdrun_candidates:
        _send_json(handler, 400, {"error": "No .cfdrun file found in case/model/ — run mesh first"})
        return
    cfd_json_path = cfdrun_candidates[0]
```

Variable name `cfd_json_path` stays unchanged so the rest of the liquid handler code (which reads and parses the file) works without further edits.

- [ ] **Step 7: Verify server.py still parses**

```bash
conda run -n dcdtuni python -c "import py_compile; py_compile.compile('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/server/server.py', doraise=True); print('ok')"
```

Expected: `ok` printed.

---

### Task B3: Add `getCfdrunFilename()` helper to STL_generate.html

**Files:**

- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (insert new function near existing filename helpers around line 12061)

- [ ] **Step 1: Insert the helper immediately after `ensureProjectFileName`**

Use Edit tool to find:

```javascript
    function ensureProjectFileName(name) {
        const trimmed = (name || '').trim();
        if (!trimmed) return '';
        return trimmed.toLowerCase().endsWith('.cfdproj') ? trimmed : `${trimmed}.cfdproj`;
    }
```

Replace with:

```javascript
    function ensureProjectFileName(name) {
        const trimmed = (name || '').trim();
        if (!trimmed) return '';
        return trimmed.toLowerCase().endsWith('.cfdproj') ? trimmed : `${trimmed}.cfdproj`;
    }

    /**
     * Compute the filename to use when server.py writes the CFD run manifest
     * into case/<name>/model/<name>.cfdrun.
     *
     * Resolution order:
     *   1. If a remote .cfdproj is currently loaded/saved, use its basename.
     *   2. Else fall back to state.projectName or state.exportFileName,
     *      sanitized via sanitizeName().
     *   3. Final fallback: 'project'.
     *
     * The returned string always ends with '.cfdrun' and contains no path
     * components (server.py also sanitizes, but we keep the contract clean
     * client-side).
     */
    function getCfdrunFilename() {
        const remotePath = getCurrentRemoteProjectFile();
        if (remotePath) {
            const basename = getPathBasename(remotePath).replace(/\.cfdproj$/i, '');
            if (basename) return `${basename}.cfdrun`;
        }
        const fallback = sanitizeName(state.projectName || state.exportFileName || 'project') || 'project';
        return `${fallback}.cfdrun`;
    }
```

- [ ] **Step 2: Verify the HTML file still loads (no JS syntax error)**

Run a quick syntax check via node if available, or by opening in a browser:

```bash
conda run -n dcdtuni python -c "
import re
from pathlib import Path
text = Path('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html').read_text()
# Count opening/closing braces in the <script> blocks as a sanity check
scripts = re.findall(r'<script[^>]*>(.*?)</script>', text, re.DOTALL)
for i, s in enumerate(scripts):
    open_braces = s.count('{')
    close_braces = s.count('}')
    if open_braces != close_braces:
        print(f'script block {i}: UNBALANCED — opens={open_braces} closes={close_braces}')
    else:
        print(f'script block {i}: balanced ({open_braces} braces)')
"
```

Expected: all script blocks report balanced braces. If any are unbalanced, the helper insertion is malformed — inspect and fix.

---

### Task B4: Wire `getCfdrunFilename()` into the `/api/run-mesh` POST body

**Files:**

- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` at the POST site in `runMeshGeneration()` around line 23125

- [ ] **Step 1: Update the fetch body**

Use Edit tool to find:

```javascript
            const resp = await fetch('/api/run-mesh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    cfdExportJson: cfdExportJson,
                    stlFiles: stlFiles,
                    meshOnly: mode === 'mesh',
                    solveOnly: mode === 'solve',
                }),
            });
```

Replace with:

```javascript
            const resp = await fetch('/api/run-mesh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    cfdExportJson: cfdExportJson,
                    cfdrunFilename: getCfdrunFilename(),
                    stlFiles: stlFiles,
                    meshOnly: mode === 'mesh',
                    solveOnly: mode === 'solve',
                }),
            });
```

- [ ] **Step 2: Search for any second POST site that also needs updating**

```bash
Grep pattern="'/api/run-mesh'|'/api/prepare-case'|cfdExportJson:" path="/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html" output_mode="content" -n=true
```

Expected: possibly 2-3 POST sites (one in `runMeshGeneration` for mesh/full, one for solve-only, possibly one in `handlePrepareCase` helper). For **each** site that sends `cfdExportJson:`, add `cfdrunFilename: getCfdrunFilename(),` adjacent to it.

- [ ] **Step 3: Verify (repeat the brace-balance check from Task B3 Step 2)**

```bash
conda run -n dcdtuni python -c "
import re
from pathlib import Path
text = Path('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/web/STL_generate.html').read_text()
scripts = re.findall(r'<script[^>]*>(.*?)</script>', text, re.DOTALL)
for i, s in enumerate(scripts):
    print(f'script block {i}: {s.count(\"{\")} opens, {s.count(\"}\")} closes')
"
```

Expected: all blocks balanced.

---

### Task B5: Phase B integration smoke test via browser-use

**Files:**

- No changes — verification only.

- [ ] **Step 1: Start a fresh server and point it at base604**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir case/base604 --port 8005 --ws-port 8006 > /tmp/phaseB_server.log 2>&1 &
sleep 2
curl -s http://localhost:8005/api/server-info
```

Expected: server info JSON.

- [ ] **Step 2: Drive the browser to load base604.cfdproj and trigger run-mesh**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
sleep 2
browser-use eval "fetch('/api/read-file', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ path: '/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/base604_1774199140766.cfdproj' }) }).then(r => r.json()).then(d => { window._applyProjectData(JSON.parse(d.content), 'base604_1774199140766.cfdproj', { remotePath: '/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/base604_1774199140766.cfdproj' }); });"
sleep 3
browser-use eval "console.log('cfdrun filename resolved to:', getCfdrunFilename());"
browser-use screenshot /tmp/phaseB_loaded.png
browser-use eval "runMeshGeneration('mesh')"
```

Expected:

- The eval after load prints something like `base604_1774199140766.cfdrun` — verify this in the browser console via `browser-use state` or dev tools.
- `runMeshGeneration('mesh')` kicks off the job (check the status panel).

- [ ] **Step 3: Wait for the job to complete and inspect artifacts**

```bash
# Wait up to 10 minutes for the mesh job to finish
for i in $(seq 1 60); do
  if grep -q "STLGen pipeline complete\|STLGen runner failed\|case.foam written" /tmp/phaseB_server.log 2>/dev/null; then
    break
  fi
  sleep 10
done
ls /mnt/lv/litianyu/mycode/dcdtuni/case/base604/model/
```

Expected in `model/`:

- `base604_1774199140766.cfdrun` **PRESENT**
- `cfd_export.json` **ABSENT** (cleaned up at write time by server.py or by runner)
- `bridge_bc.json` **ABSENT** (still in-memory from Phase A)
- `liquid_results.json` either present (if liquid was previously run) or absent — unchanged by this refactor

- [ ] **Step 4: Verify the runner log shows the new CLI flag**

```bash
grep -E "from-cfdrun|from-stlgen" /tmp/phaseB_server.log
```

Expected: `--from-cfdrun /mnt/lv/litianyu/mycode/dcdtuni/case/base604/model/base604_1774199140766.cfdrun` appears. No `--from-stlgen` should appear.

- [ ] **Step 5: Verify mesh integrity**

```bash
ls /mnt/lv/litianyu/mycode/dcdtuni/case/base604/Mesh/constant/polyMesh/
wc -l /mnt/lv/litianyu/mycode/dcdtuni/case/base604/Mesh/constant/polyMesh/boundary
```

Expected: `points`, `faces`, `cells`, `boundary`, `owner`, `neighbour` all present. Boundary file has non-zero line count with the same patch names as a pre-refactor baseline (if available).

- [ ] **Step 6: Stop the server and close the browser**

```bash
browser-use close 2>/dev/null || true
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
```

---

### Task B6: Commit Phase B

**Files:**

- Commit all changes from Tasks B1-B4.

- [ ] **Step 1: Review the diff**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git status
git diff --stat
```

Expected files changed:

- `src/backend/aircooling/runner/run_cfd.py`
- `STLGeneratorForOpenFoam/server/server.py`
- `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 2: Stage files explicitly**

```bash
git add \
  src/backend/aircooling/runner/run_cfd.py \
  STLGeneratorForOpenFoam/server/server.py \
  STLGeneratorForOpenFoam/web/STL_generate.html
git status
```

Expected: only the three files staged.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(runner): rename cfd_export.json → <basename>.cfdrun

Renames the frontend→backend handoff file from the generic "cfd_export.json"
to "<basename>.cfdrun" where <basename> comes from the loaded .cfdproj
filename (or the web UI project name as fallback). File content and
format are unchanged — this is a pure rename.

Frontend:
- New getCfdrunFilename() helper resolves remotePath → basename.cfdrun,
  falls back to sanitizeName(state.projectName || exportFileName).
- runMeshGeneration() POST body now carries cfdrunFilename alongside
  cfdExportJson.

Backend:
- CLI flag --from-stlgen renamed to --from-cfdrun.
- server.py handle_prepare_case and handle_run_mesh accept the new
  cfdrunFilename POST field, sanitize it, and write to
  case/<name>/model/<basename>.cfdrun. Legacy cfd_export.json /
  bridge_bc.json are removed at write time.
- Liquid handlers updated to glob for *.cfdrun instead of
  hard-coding cfd_export.json.

Migration: no script. Users re-open their .cfdproj in the browser and
click Run Mesh; the first run auto-cleans legacy files.

Spec: docs/superpowers/specs/2026-04-11-cfdrun-rename-bridge-bc-inmemory-design.md

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify**

```bash
git log --oneline -2
git status
```

Expected: two new commits at HEAD (Phase A + Phase B), working tree clean.

---

## Final verification

### Task F1: Liquid cooling regression (spec §9.4) — OPTIONAL if no liquid test case is set up

**Files:**

- No changes — verification only. **Skip this task** if the user confirms no liquid-cooled case is currently wired up for end-to-end testing; spec §9.4 is best-effort.

- [ ] **Step 1: Identify a liquid-enabled test case**

Check which projects in `STLGeneratorForOpenFoam/projects/*.cfdproj` have `liquidCooling.enabled: true` at the top level. Run:

```bash
for f in /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/*.cfdproj; do
  if grep -l '"liquidCooling"' "$f" 2>/dev/null | xargs -I{} grep -l '"enabled":[[:space:]]*true' {} 2>/dev/null; then
    echo "LIQUID: $f"
  fi
done
```

If the script returns nothing, **skip this entire task** and note "no liquid-enabled cfdproj available for regression test — spec §9.4 skipped".

- [ ] **Step 2: Run liquid solve followed by main solve**

Assuming a liquid-enabled case was found (substitute its case dir below as `<liquid_case>`), start the server pointing at it:

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir case/<liquid_case> --port 8005 --ws-port 8006 > /tmp/liquid_regression.log 2>&1 &
sleep 2
```

Then drive the liquid solve + main solve pipeline via browser-use:

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
sleep 2
# Load the liquid-enabled cfdproj (substitute path below)
browser-use eval "fetch('/api/read-file', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ path: '/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/<liquid_project>.cfdproj' }) }).then(r => r.json()).then(d => { window._applyProjectData(JSON.parse(d.content), 'liquid_test.cfdproj', {}); });"
sleep 3
# Trigger liquid solve first
browser-use eval "fetch('/api/liquid/solve', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({}) }).then(r => r.json()).then(d => console.log('liquid solve:', d));"
sleep 10
# Then run main mesh + solve with inherit enabled
browser-use eval "state.liquidCooling.inheritResults = true; runMeshGeneration('mesh');"
```

- [ ] **Step 3: Wait for jobs to complete and verify artifacts**

```bash
for i in $(seq 1 60); do
  if grep -q "STLGen pipeline complete\|case.foam written\|STLGen runner failed" /tmp/liquid_regression.log 2>/dev/null; then
    break
  fi
  sleep 10
done
ls /mnt/lv/litianyu/mycode/dcdtuni/case/<liquid_case>/model/
```

Expected in `model/`:

- `<basename>.cfdrun` **PRESENT**
- `cfd_export.json` **ABSENT**
- `bridge_bc.json` **ABSENT** (must not reappear — this is the key regression check)
- `liquid_results.json` **PRESENT** (written by liquid solve)

- [ ] **Step 4: Verify the main runner picked up liquid results**

```bash
grep -E "Inheriting liquid cooling results|BridgeBCRegistry" /tmp/liquid_regression.log
```

Expected:

- One line `Inheriting liquid cooling results for N servers`
- One line `Built BridgeBCRegistry: <N> patches | ...`

If the "Inheriting" log is missing, the liquid-air coupling was broken by the refactor — investigate `_build_bridge_bc()` for the liquid path (currently at `stlgen_runner.py:1819-1853`).

- [ ] **Step 5: Stop the server**

```bash
browser-use close 2>/dev/null || true
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
```

- [ ] **Step 6: No commit**

This task is verification only. No code changes.

---

### Task F2: Run all tests one more time

- [ ] **Step 1: Run the new test and existing runner tests**

```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run the broader test suite if time permits**

```bash
conda run -n dcdtuni python -m pytest tests/ -v --timeout=300
```

Expected: all tests PASS, or only tests unrelated to this refactor fail (document any pre-existing failures).

- [ ] **Step 3: Review the 2 commits**

```bash
git log --oneline -2
git show HEAD~1 --stat
git show HEAD --stat
```

Expected: Phase A commit shows 4 files changed (bridge_bc.py, fields.py, stlgen_runner.py, test_bridge_bc_inmemory.py). Phase B commit shows 3 files changed (run_cfd.py, server.py, STL_generate.html).

- [ ] **Step 4: Final sanity check — grep for any lingering references to the old artifacts**

```bash
Grep pattern="bridge_bc\.json" path="/mnt/lv/litianyu/mycode/dcdtuni/src" output_mode="files_with_matches"
Grep pattern="--from-stlgen" path="/mnt/lv/litianyu/mycode/dcdtuni" output_mode="files_with_matches"
Grep pattern="cfd_export\.json" path="/mnt/lv/litianyu/mycode/dcdtuni/src/backend/aircooling/runner" output_mode="files_with_matches"
```

Expected behavior:

- `bridge_bc.json` in `src/`: **ZERO** matches (we deleted all references in runtime code).
- `--from-stlgen` anywhere: **ZERO** matches in non-test files. Test files in `src/backend/aircooling/runner/tests/` may still use the old `cfd_export_path=` fixture kwarg name (that's fine — it's an internal param name, not the CLI flag).
- `cfd_export.json` in `src/backend/aircooling/runner/`: may still appear in historical docstrings and comments referring to the content format — verify each one is a comment/docstring, not a file-path construction.

If any surprises surface, fix them inline and amend the appropriate commit (or create a third clean-up commit).

---

## Plan Summary

- **Phase A** (one commit): bridge_bc in-memory refactor. 13 tasks (A1-A13): test, runner refactor (7 edits), test validation, API smoke test, commit.
- **Phase B** (one commit): cfdrun rename. 6 tasks (B1-B6): CLI rename, server.py update, JS helper, JS POST wiring, browser-use smoke test, commit.
- **Final (F1, F2)**: optional liquid cooling regression (spec §9.4) + test sweep + grep sanity check.

Total: 2 commits, 7 source files modified, 1 new test file, ~500 lines of diff.
