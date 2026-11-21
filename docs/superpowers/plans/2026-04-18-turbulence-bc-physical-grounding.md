# Turbulence BC Physical Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ε-saturating inlet turbulence BC generation with physically-grounded per-bc_type I values, rectangular hydraulic diameter, OpenFOAM native inlet BC types, and `realizableKE` turbulence model.

**Architecture:** Three coordinated changes behind clear boundaries — (T1) physical-grounding math in `FieldGenerator._compute_turbulence_bc` + new `PatchGeometry` carrying area/perimeter/bbox from `STLGenRunner`, (T2) native OpenFOAM inlet BC rendering in `_write_turbulence_field`, (T3) one-line template swap to `realizableKE`. TDD-first: pure-geometry perimeter helper lands with unit tests before `FieldGenerator` is refactored.

**Tech Stack:** Python 3.10, pytest, Jinja2 templates, OpenFOAM v2406 buoyantBoussinesqSimpleFoam.

**Spec:** `docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md`

---

## File map

| File | Responsibility | Change |
|---|---|---|
| `src/backend/aircooling/runner/stlgen_runner.py` | STL → geometry | add `PatchGeometry` NamedTuple + `_compute_boundary_perimeter` helper + rewrite `_compute_patch_areas` → `_compute_patch_geometry` (lines ~225 + ~866-887 + ~900) |
| `src/backend/aircooling/runner/fields.py` | OpenFOAM field files | replace `TURBULENCE_INTENSITY` with `TURBULENCE_INTENSITY_BY_BC_TYPE`; rewrite `_compute_turbulence_bc` to take bc_type + geometry; rewrite `_write_turbulence_field` flow-inlet branch to use native BC types (lines 62-64, 66-100, 102-128, 1142-1199) |
| `src/backend/aircooling/third_parties/foam/template/constant/turbulenceProperties.j2` | RAS model name | line 22: `kEpsilon` → `realizableKE` |
| `src/backend/aircooling/runner/tests/test_turbulence_bc.py` | Unit tests | **new** — 7 tests per spec |

---

## Task 1: Boundary perimeter helper + PatchGeometry (pure geometry, with unit tests)

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (insert near line 315, right after `_triangle_area_and_normal`)
- Create: `src/backend/aircooling/runner/tests/test_turbulence_bc.py`

### Step 1.1: Create the test file with the 2 perimeter tests (Test 5 + Test 6)

- [ ] **Write the failing tests**

Create `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
"""Unit tests for turbulence BC physical-grounding refactor.

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md
"""
import math

import pytest

from dcdtuni.runner.stlgen_runner import (
    PatchGeometry,
    _compute_boundary_perimeter,
)


# --- Test 5: perimeter extraction from 2 triangles forming a rectangle -----

def test_perimeter_extraction_from_2_triangles():
    """Two triangles sharing one diagonal → boundary = 4 rectangle edges (1.04m)."""
    # Rectangle in XY plane: 0.12m wide × 0.40m tall, origin at (0, 0, 0)
    # Triangle 1: (0,0,0) - (0.12,0,0) - (0.12,0.40,0)
    # Triangle 2: (0,0,0) - (0.12,0.40,0) - (0,0.40,0)
    # Shared edge: (0,0,0) - (0.12,0.40,0)  → NOT on boundary
    triangles = [
        ((0.0, 0.0, 0.0), (0.12, 0.0, 0.0), (0.12, 0.40, 0.0)),
        ((0.0, 0.0, 0.0), (0.12, 0.40, 0.0), (0.0, 0.40, 0.0)),
    ]
    perimeter = _compute_boundary_perimeter(triangles)
    expected = 2 * (0.12 + 0.40)  # = 1.04
    assert perimeter == pytest.approx(expected, rel=1e-4)


# --- Test 6: perimeter fallback when extraction yields zero ---------------

def test_perimeter_zero_for_closed_shell():
    """A patch where every edge is shared by 2+ triangles (closed topology)
    has no boundary → perimeter = 0. Caller falls back to circular D_h."""
    # Tetrahedron: 4 triangles, each edge shared by exactly 2 faces → closed.
    a = (0.0, 0.0, 0.0)
    b = (1.0, 0.0, 0.0)
    c = (0.5, 1.0, 0.0)
    d = (0.5, 0.5, 1.0)
    triangles = [
        (a, b, c),  # bottom
        (a, b, d),  # front
        (b, c, d),  # right
        (a, c, d),  # left
    ]
    perimeter = _compute_boundary_perimeter(triangles)
    assert perimeter == pytest.approx(0.0, abs=1e-9)


def test_perimeter_empty_input():
    """Zero triangles → perimeter 0."""
    assert _compute_boundary_perimeter([]) == 0.0
```

- [ ] **Run tests to verify they fail**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v
```

Expected: `ImportError: cannot import name 'PatchGeometry'` (or `_compute_boundary_perimeter`) — symbols don't exist yet.

### Step 1.2: Add `PatchGeometry` NamedTuple

- [ ] **Implement**

Insert after `_GEOM_EPS = 1e-9` (line 225) in `src/backend/aircooling/runner/stlgen_runner.py`:

```python
class PatchGeometry(NamedTuple):
    """Geometric summary of a flat STL patch, used by FieldGenerator
    for physically-grounded turbulence inlet BC computation.

    Fields:
        area:       total surface area in m²
        perimeter:  total boundary-polygon length in m (0.0 if extraction
                    yields no boundary edges — caller should fall back to
                    circular hydraulic-diameter approximation)
        bbox_dims:  axis-aligned bounding box (dx, dy, dz) in m
    """
    area: float
    perimeter: float
    bbox_dims: Tuple[float, float, float]
```

Also add `NamedTuple` to the typing imports at the top of the file (locate existing `from typing import ...` and add `NamedTuple`).

### Step 1.3: Implement `_compute_boundary_perimeter`

- [ ] **Implement**

Insert immediately after `_triangle_area_and_normal` in `src/backend/aircooling/runner/stlgen_runner.py` (near line 313):

```python
def _compute_boundary_perimeter(triangles: Iterable[Triangle]) -> float:
    """Length of the boundary polygon of a triangle mesh patch.

    An edge (v_i, v_j) belongs to the patch boundary iff it appears in
    exactly one triangle. Shared edges (interior) appear in two; degenerate
    edges are ignored. Vertices are quantised to 1e-6 m so floating-point
    jitter from STL generation does not break edge identity.

    Returns 0.0 for an empty input or a closed-shell topology where every
    edge is shared (e.g. sphere, tetrahedron surface) — caller interprets
    this as 'fall back to circular D_h'.
    """
    edge_counts: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], int] = {}
    edge_lengths: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], float] = {}

    def _quantise(v: Vec3) -> Tuple[int, int, int]:
        return (
            int(round(v[0] * 1_000_000)),
            int(round(v[1] * 1_000_000)),
            int(round(v[2] * 1_000_000)),
        )

    for triangle in triangles:
        qa, qb, qc = _quantise(triangle[0]), _quantise(triangle[1]), _quantise(triangle[2])
        for (va, vb), (qa_k, qb_k) in (
            ((triangle[0], triangle[1]), (qa, qb)),
            ((triangle[1], triangle[2]), (qb, qc)),
            ((triangle[2], triangle[0]), (qc, qa)),
        ):
            if qa_k == qb_k:  # degenerate edge
                continue
            key = (qa_k, qb_k) if qa_k < qb_k else (qb_k, qa_k)
            edge_counts[key] = edge_counts.get(key, 0) + 1
            if key not in edge_lengths:
                edge_lengths[key] = _vec_norm(_vec_sub(vb, va))

    total = 0.0
    for key, count in edge_counts.items():
        if count == 1:
            total += edge_lengths[key]
    return total
```

- [ ] **Run tests to verify they pass**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v
```

Expected: all 3 tests PASS.

### Step 1.4: Commit

- [ ] **Commit**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni add \
  src/backend/aircooling/runner/stlgen_runner.py \
  src/backend/aircooling/runner/tests/test_turbulence_bc.py
git -C /mnt/lv/litianyu/mycode/dcdtuni commit -m "$(cat <<'EOF'
feat(runner): add PatchGeometry + _compute_boundary_perimeter (T1 prep)

Pure-geometry helper that extracts patch boundary perimeter from STL
triangles. Edges shared by 2 triangles are interior; edges appearing
in exactly 1 triangle form the boundary. Used by the upcoming
physically-grounded turbulence BC refactor to compute rectangular
hydraulic diameter (D_h = 4A/P) instead of the circular approximation.

Vertices quantised to 1e-6 m for float-jitter-robust edge identity.

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: STLGenRunner `_compute_patch_geometry` (replaces `_compute_patch_areas`)

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:866-887` (rewrite `_compute_patch_areas`)
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:889-902` (`_build_field_generator` pass new kw)

No dedicated unit test — function is pure orchestration over the already-tested perimeter helper. Integration is covered by Task 3 tests (which consume `patch_geometry`) and by the end-to-end regression in Task 6.

### Step 2.1: Rewrite `_compute_patch_areas` → `_compute_patch_geometry`

- [ ] **Implement**

Replace the existing `_compute_patch_areas` method (lines 866-887) with:

```python
    def _compute_patch_geometry(self) -> Dict[str, PatchGeometry]:
        """Compute area, boundary perimeter, and bbox for each STL solid.

        Returns dict mapping sanitized solid name → PatchGeometry.
        Used by FieldGenerator to derive physically-grounded k/epsilon
        inlet BCs (rectangular hydraulic diameter D_h = 4A/P).
        """
        geometries: Dict[str, PatchGeometry] = {}
        for stl_file in sorted(self.tri_surface_dir.glob("*.stl")):
            try:
                solids = _read_ascii_stl_triangles(stl_file)
            except Exception as exc:
                logger.warning(
                    f"Could not read {stl_file.name} for patch geometry: {exc}"
                )
                continue
            for solid_name, triangles in solids.items():
                if not triangles:
                    continue
                total_area = 0.0
                xs: List[float] = []
                ys: List[float] = []
                zs: List[float] = []
                for tri in triangles:
                    a, _ = _triangle_area_and_normal(tri)
                    if a > _GEOM_EPS:
                        total_area += a
                    for v in tri:
                        xs.append(v[0])
                        ys.append(v[1])
                        zs.append(v[2])
                if total_area <= _GEOM_EPS:
                    continue
                perimeter = _compute_boundary_perimeter(triangles)
                bbox = (
                    max(xs) - min(xs),
                    max(ys) - min(ys),
                    max(zs) - min(zs),
                )
                geometries[_sanitize(solid_name)] = PatchGeometry(
                    area=total_area,
                    perimeter=perimeter,
                    bbox_dims=bbox,
                )
        return geometries
```

### Step 2.2: Update `_build_field_generator` to pass `patch_geometry`

- [ ] **Implement**

Locate `_build_field_generator` (around line 889) and update the `FieldGenerator(...)` call:

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
            patch_geometry=self._compute_patch_geometry(),
            bridge_registry=self._bridge_registry,
        )
```

(Note: the kw changed from `patch_areas=` to `patch_geometry=`. `FieldGenerator` will be updated to accept this new kw in Task 3.)

### Step 2.3: Verify existing tests don't break on import

- [ ] **Run the existing runner test suite**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v -x --ignore=src/backend/aircooling/runner/tests/test_turbulence_bc.py
```

Expected: all pass (the field-generator refactor is not yet wired, but `_compute_patch_geometry` should at least import cleanly). If any test imports `_compute_patch_areas`, update it to `_compute_patch_geometry` in the test file.

### Step 2.4: Commit

- [ ] **Commit**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni add src/backend/aircooling/runner/stlgen_runner.py
git -C /mnt/lv/litianyu/mycode/dcdtuni commit -m "$(cat <<'EOF'
refactor(runner): _compute_patch_areas → _compute_patch_geometry

Returns PatchGeometry(area, perimeter, bbox_dims) per STL solid, replacing
bare area dict. _build_field_generator now passes patch_geometry= kw to
FieldGenerator (ctor update lands in next commit).

Part of turbulence BC physical-grounding refactor.

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: FieldGenerator per-bc_type I + rectangular D_h (T1 core)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:62-64` (constants)
- Modify: `src/backend/aircooling/runner/fields.py:66-100` (constructor signature)
- Modify: `src/backend/aircooling/runner/fields.py:102-128` (`_compute_turbulence_bc`)
- Modify: `src/backend/aircooling/runner/fields.py:1125-1140` (`_compute_average_inlet_turbulence` — adjust for new return arity)
- Extend: `src/backend/aircooling/runner/tests/test_turbulence_bc.py` (add Tests 1-4)

### Step 3.1: Add Tests 1-4 to the test file (failing)

- [ ] **Write the failing tests**

Append to `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
# ---------------------------------------------------------------------------
# Test helpers for FieldGenerator-scoped tests
# ---------------------------------------------------------------------------

from pathlib import Path
from typing import Any, Dict, Optional

from dcdtuni.runner.fields import FieldGenerator


class _FakeBridgeRegistry:
    """Minimal stand-in for BridgeBCRegistry.items() iteration."""

    def __init__(self, entries: Dict[str, Dict[str, Any]]):
        self._entries = entries

    def items(self):
        # FieldGenerator._collect_imported_bc_data iterates as (patch, spec)
        # and reads spec.bc_type, .flow_rate, etc. We mimic with SimpleNamespace.
        from types import SimpleNamespace
        for patch, spec in self._entries.items():
            yield patch, SimpleNamespace(
                bc_type=spec.get("bc_type"),
                temperature=spec.get("temperature"),
                flow_rate=spec.get("flow_rate"),
                power=spec.get("power"),
                sigma=spec.get("sigma"),
                perforated=spec.get("perforated"),
                heat_flux=spec.get("heat_flux"),
            )

    def __iter__(self):
        return iter(self._entries.keys())


def _make_field_gen(
    tmp_path: Path,
    patch_name: str,
    bc_type: str,
    flow_rate: float,
    area: float,
    perimeter: float,
    bbox_dims=(0.12, 0.40, 0.0),
) -> FieldGenerator:
    """Construct a FieldGenerator with a single patch in its bridge registry."""
    geom = {patch_name: PatchGeometry(area=area, perimeter=perimeter, bbox_dims=bbox_dims)}
    reg = _FakeBridgeRegistry({patch_name: {"bc_type": bc_type, "flow_rate": flow_rate}})
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={},
        room_config={},
        patch_geometry=geom,
        bridge_registry=reg,
    )
    # Force lazy collect
    fg._collect_data()
    return fg


# --- Test 1: fan_outlet uses I=10% -----------------------------------------

def test_compute_turbulence_bc_fan_outlet_uses_10_percent(tmp_path):
    """fan_outlet bc_type → I=0.10, and the computed k matches 1.5·(I·U)²."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, I, L = result
    assert I == pytest.approx(0.10, rel=1e-9)
    # U = 0.1553/0.048 = 3.2354  →  k = 1.5·(0.10·3.2354)² = 0.15702
    assert k == pytest.approx(0.15702, rel=1e-3)


# --- Test 2: acu_outlet uses I=5% ------------------------------------------

def test_compute_turbulence_bc_acu_outlet_uses_5_percent(tmp_path):
    """acu_outlet bc_type → I=0.05, k = 1.5·(0.05·10)² = 0.375."""
    fg = _make_field_gen(
        tmp_path, "acu_top", bc_type="acu_outlet",
        flow_rate=10.0, area=1.0, perimeter=4.0,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    k, eps, I, L = result
    assert I == pytest.approx(0.05, rel=1e-9)
    assert k == pytest.approx(0.375, rel=1e-3)


# --- Test 3: unknown bc_type falls back to default I ------------------------

def test_compute_turbulence_bc_unknown_bc_type_falls_back(tmp_path, caplog):
    """Unmapped bc_type → TURBULENCE_INTENSITY_DEFAULT (0.05), warning logged."""
    fg = _make_field_gen(
        tmp_path, "mystery", bc_type="unknown_type",
        flow_rate=1.0, area=0.1, perimeter=1.3,
    )
    with caplog.at_level("WARNING"):
        result = fg._compute_turbulence_bc("mystery")
    assert result is not None
    _k, _eps, I, _L = result
    assert I == pytest.approx(0.05, rel=1e-9)
    assert any("unknown_type" in r.getMessage() for r in caplog.records)


# --- Test 4: rectangular D_h (4A/P) vs circular (2·√(A/π)) ------------------

def test_rectangular_Dh_used_when_perimeter_positive(tmp_path):
    """Given 0.12×0.40 rectangle (A=0.048, P=1.04):
       D_h_rect = 4A/P   = 0.18462
       D_h_circ = 2√(A/π) = 0.24726
       L = 0.07·D_h_rect = 0.01292
       ε = 0.09^0.75 · k^1.5 / L
    Assert ε matches rectangular-D_h derivation, not circular."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, _I, L = result
    assert L == pytest.approx(0.07 * (4 * 0.048 / 1.04), rel=1e-3)  # 0.01292
    # ε ≈ 0.1643 · k^1.5 / 0.01292
    expected_eps = (0.09 ** 0.75) * (k ** 1.5) / L
    assert eps == pytest.approx(expected_eps, rel=1e-3)
    # Sanity: rectangular ε is ~1.34× the circular ε
    # (D_h_circ/D_h_rect = 0.247/0.185 = 1.34)
    D_h_circ = 2.0 * math.sqrt(0.048 / math.pi)
    L_circ = 0.07 * D_h_circ
    eps_circ = (0.09 ** 0.75) * (k ** 1.5) / L_circ
    assert eps > eps_circ * 1.2  # strictly bigger than circular approx
```

- [ ] **Run tests to verify they fail**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v
```

Expected: Tests 1-4 FAIL with `TypeError: __init__() got an unexpected keyword argument 'patch_geometry'` (or similar).

### Step 3.2: Replace `TURBULENCE_INTENSITY` with the per-bc_type dict

- [ ] **Implement**

In `src/backend/aircooling/runner/fields.py`, replace lines 62-64:

```python
    # Turbulence inlet BC constants — grounded in literature per
    # docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md
    TURBULENCE_INTENSITY_BY_BC_TYPE = {
        "acu_outlet": 0.05,   # CRAC/CRAH supply (Cho 2009; ASHRAE HVAC typical)
        "fan_outlet": 0.10,   # server-fan exhaust — blade wake (Erden & Khalifa 2019)
        "air_leak":   0.05,   # passive leakage — background HVAC
        "leakage":    0.05,   # alias of air_leak used on some bridge_bc paths
    }
    TURBULENCE_INTENSITY_DEFAULT = 0.05  # fallback for unmapped bc_type
    C_MU = 0.09                          # k-epsilon model constant
```

### Step 3.3: Update constructor to accept `patch_geometry`

- [ ] **Implement**

In `src/backend/aircooling/runner/fields.py`, locate the `FieldGenerator.__init__` signature and body (lines 66-100). Replace `patch_areas` with `patch_geometry`:

```python
    def __init__(
        self,
        *,
        case_dir: Path,
        simulation_config: Optional[Dict[str, Any]] = None,
        room_config: Optional[Dict[str, Any]] = None,
        patch_geometry: Optional[Dict[str, "PatchGeometry"]] = None,
        bridge_registry: Optional["BridgeBCRegistry"] = None,
    ):
        """
        Initialize field generator.

        Args:
            case_dir: Case directory path
            simulation_config: Simulation configuration dict from the .cfdrun manifest
            room_config: Room configuration dict override
            patch_geometry: Dict mapping patch name → PatchGeometry(area, perimeter, bbox_dims)
                (from STLGenRunner._compute_patch_geometry()). Used for
                physically-grounded k/ε inlet BC derivation.
            bridge_registry: In-memory BridgeBCRegistry built by
                STLGenRunner._build_bridge_bc(). If None, _imported_bc_data is empty.
        """
        self.case_dir = Path(case_dir).resolve()
        self._simulation_config = simulation_config
        self._room_config = room_config
        self._patch_geometry = patch_geometry or {}
        self.bridge_registry = bridge_registry

        self._acu_data: Dict[str, Dict[str, float]] = {}
        self._air_leak_data: Dict[str, Dict[str, float]] = {}
        self._imported_bc_data: Dict[str, Dict[str, Any]] = {}

        self._data_collected = False
```

Also, in `fields.py`, add `PatchGeometry` to the existing `TYPE_CHECKING` guard to avoid the circular import (stlgen_runner already imports FieldGenerator from fields.py at module load):

```python
if TYPE_CHECKING:
    from dcdtuni.runner.bridge_bc import BridgeBCRegistry
    from dcdtuni.runner.stlgen_runner import PatchGeometry
```

The annotation `Optional[Dict[str, "PatchGeometry"]]` in the constructor already uses the string form, so runtime doesn't need the symbol.

### Step 3.4: Rewrite `_compute_turbulence_bc`

- [ ] **Implement**

In `src/backend/aircooling/runner/fields.py`, replace `_compute_turbulence_bc` (lines 102-128) with:

```python
    def _compute_turbulence_bc(
        self, patch_name: str
    ) -> Optional[Tuple[float, float, float, float]]:
        """Compute (k, ε, I, L) for an inlet patch.

        Formula (inlet turbulence estimation; CFD standard):
            k  = 1.5·(I·U)²
            L  = 0.07·D_h  where D_h = 4A/P (rectangular) or 2√(A/π) (fallback)
            ε  = C_μ^0.75 · k^1.5 / L

        I is selected per bc_type from TURBULENCE_INTENSITY_BY_BC_TYPE; unknown
        bc_types fall back to TURBULENCE_INTENSITY_DEFAULT with a WARNING log.

        Returns None when flow_rate ≤ 0, area ≤ 1e-8, or the patch has no
        bridge-registry entry.
        """
        bc_info = self._imported_bc_data.get(patch_name, {})
        flow_rate = bc_info.get("flow_rate") or 0.0
        if flow_rate <= 0:
            return None
        geom = self._patch_geometry.get(patch_name)
        if geom is None or geom.area <= 1e-8:
            return None
        U = flow_rate / geom.area

        bc_type = bc_info.get("bc_type", "")
        if bc_type in self.TURBULENCE_INTENSITY_BY_BC_TYPE:
            I = self.TURBULENCE_INTENSITY_BY_BC_TYPE[bc_type]
        else:
            I = self.TURBULENCE_INTENSITY_DEFAULT
            logger.warning(
                f"Unknown bc_type {bc_type!r} for patch {patch_name!r}; "
                f"falling back to default turbulence intensity I={I}"
            )

        k = 1.5 * (I * U) ** 2

        if geom.perimeter > 1e-6:
            D_h = 4.0 * geom.area / geom.perimeter
        else:
            D_h = 2.0 * math.sqrt(geom.area / math.pi)
        length_scale = max(0.07 * D_h, 1e-6)

        epsilon = self.C_MU ** 0.75 * k ** 1.5 / length_scale

        # Physical clamps to prevent extreme values from degenerate inputs
        k = min(k, 100.0)
        epsilon = min(epsilon, 1e5)

        logger.debug(
            f"Turbulence BC {patch_name}: U={U:.2f} m/s, I={I}, L={length_scale:.4f} m, "
            f"k={k:.4f}, eps={epsilon:.4f} (A={geom.area:.4f} m², P={geom.perimeter:.4f} m, "
            f"Q={flow_rate:.4f} m³/s)"
        )
        return (k, epsilon, I, length_scale)
```

### Step 3.5: Fix `_compute_average_inlet_turbulence` for 4-tuple return

- [ ] **Implement**

In `src/backend/aircooling/runner/fields.py`, locate `_compute_average_inlet_turbulence` (around line 1125-1140). Update the unpacking:

```python
    def _compute_average_inlet_turbulence(self, patches: List[Tuple[str, str]]) -> Tuple[float, float]:
        """Compute average k and epsilon across all flow-rate inlets for internal field init."""
        k_values = []
        eps_values = []
        for patch_name, _ in patches:
            result = self._compute_turbulence_bc(patch_name)
            if result:
                k_values.append(result[0])
                eps_values.append(result[1])
        if k_values:
            k_avg = sum(k_values) / len(k_values)
            eps_avg = sum(eps_values) / len(eps_values)
            logger.info(f"Auto-computed turbulence: k_avg={k_avg:.4f}, eps_avg={eps_avg:.4f} "
                        f"from {len(k_values)} flow patches")
            return k_avg, eps_avg
        return 0.001, 0.001  # fallback
```

(Unpack index 0 and 1 — already correct as the first two elements remain k and ε.)

### Step 3.6: Also patch the `_write_turbulence_field` call-site that reads `result[0]`/`result[1]`

- [ ] **Implement**

In `_write_turbulence_field` (line 1156-1178) the current code does:

```python
result = self._compute_turbulence_bc(patch_name)
if result:
    val = result[0] if field_name == "k" else result[1]
```

This still works — `result[0]` is k, `result[1]` is ε. No change needed in Task 3; the native-BC-rendering rewrite happens in Task 4.

### Step 3.7: Run tests

- [ ] **Run tests**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v
```

Expected: **all 7 tests (3 from Task 1 + 4 from Task 3) PASS**. (Test 7 is still pending from Task 4 but is not yet written.)

### Step 3.8: Run the broader runner test suite

- [ ] **Regression-check**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v
```

Expected: no new failures. If a test references `TURBULENCE_INTENSITY` (old constant) or `patch_areas` (old kw), update it.

### Step 3.9: Commit

- [ ] **Commit**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni add \
  src/backend/aircooling/runner/fields.py \
  src/backend/aircooling/runner/tests/test_turbulence_bc.py
git -C /mnt/lv/litianyu/mycode/dcdtuni commit -m "$(cat <<'EOF'
feat(runner): per-bc_type I + rectangular D_h in turbulence BC (T1)

- TURBULENCE_INTENSITY_BY_BC_TYPE dict: fan_outlet=10%, acu_outlet=5%,
  air_leak/leakage=5%; default 5% with WARNING on unknown bc_type.
- _compute_turbulence_bc now returns (k, ε, I, L) 4-tuple.
- D_h = 4A/P when perimeter>0 (rectangular), else circular 2√(A/π).
- FieldGenerator constructor now takes patch_geometry= (PatchGeometry dict).

Addresses root cause of Tri_angle fan-server ε boundedness saturation
(current I=5% + circular D_h undersizes k, destabilises ε equation).

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Emit OpenFOAM native inlet BC types (T2)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:1142-1199` (`_write_turbulence_field`)
- Extend: `src/backend/aircooling/runner/tests/test_turbulence_bc.py` (add Test 7)

### Step 4.1: Add Test 7

- [ ] **Write the failing test**

Append to `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
# --- Test 7: _write_turbulence_field emits native BC types -----------------

def test_write_turbulence_field_renders_native_bc_types(tmp_path):
    """Flow inlets should render as turbulentIntensityKineticEnergyInlet (k) /
    turbulentMixingLengthDissipationRateInlet (ε). Wall patches remain
    kqRWallFunction / epsilonWallFunction."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )

    patches = [("fan_p0", "patch"), ("wall_1", "wall")]

    k_path = tmp_path / "k"
    fg._write_turbulence_field(
        k_path, patches, internal_value=0.1,
        field_class="volScalarField", dimensions="[0 2 -2 0 0 0 0]",
        field_name="k",
    )
    k_text = k_path.read_text()
    assert "turbulentIntensityKineticEnergyInlet" in k_text
    # Assert the fan patch block carries the intensity value
    assert "intensity       0.1" in k_text
    # Wall patch still uses the wall function
    assert "kqRWallFunction" in k_text

    eps_path = tmp_path / "epsilon"
    fg._write_turbulence_field(
        eps_path, patches, internal_value=0.01,
        field_class="volScalarField", dimensions="[0 2 -3 0 0 0 0]",
        field_name="epsilon",
    )
    eps_text = eps_path.read_text()
    assert "turbulentMixingLengthDissipationRateInlet" in eps_text
    # mixingLength is the L computed above (~0.01292 m for the 0.12×0.40 rect)
    assert "mixingLength" in eps_text
    # epsilonWallFunction still applied to wall patch
    assert "epsilonWallFunction" in eps_text
```

- [ ] **Run tests to verify it fails**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_write_turbulence_field_renders_native_bc_types -v
```

Expected: FAIL with `turbulentIntensityKineticEnergyInlet not in k_text` (native BCs not yet rendered).

### Step 4.2: Rewrite the flow-inlet branch of `_write_turbulence_field`

- [ ] **Implement**

In `src/backend/aircooling/runner/fields.py`, replace lines 1156-1178 (the `if self._is_flow_patch(patch_name):` block) with:

```python
            # Flow patches: use OpenFOAM native turbulent-intensity /
            # mixing-length BCs for inlets. They auto-recompute k and ε
            # from live inlet velocity each iter and fall back to
            # zeroGradient on reverse flow.
            if self._is_flow_patch(patch_name):
                result = self._compute_turbulence_bc(patch_name)
                bc_type = self._imported_bc_data.get(patch_name, {}).get("bc_type", "")

                # Domain outlets: keep existing inletOutlet guard — value is
                # only used on backflow, per-patch, no native BC needed.
                if bc_type in self._DOMAIN_OUTLET_BC_TYPES:
                    if result:
                        val = result[0] if field_name == "k" else result[1]
                    else:
                        val = internal_value
                    lines.extend([f"    {patch_name}", "    {",
                                  "        type            inletOutlet;",
                                  f"        inletValue      uniform {val:.6g};",
                                  f"        value           uniform {val:.6g};", "    }"])
                    continue

                # Domain inlets: render native turbulentIntensity / mixingLength BCs
                if result:
                    k_val, eps_val, I, L = result
                    seed = k_val if field_name == "k" else eps_val
                    if field_name == "k":
                        lines.extend([f"    {patch_name}", "    {",
                                      "        type            turbulentIntensityKineticEnergyInlet;",
                                      f"        intensity       {I:.6g};",
                                      f"        value           uniform {seed:.6g};", "    }"])
                    else:  # epsilon
                        lines.extend([f"    {patch_name}", "    {",
                                      "        type            turbulentMixingLengthDissipationRateInlet;",
                                      f"        mixingLength    {L:.6g};",
                                      f"        value           uniform {seed:.6g};", "    }"])
                else:
                    # No flow data → fall back to fixedValue with internal field
                    lines.extend([f"    {patch_name}", "    {",
                                  "        type            fixedValue;",
                                  f"        value           uniform {internal_value:.6g};", "    }"])
                continue
```

### Step 4.3: Run tests

- [ ] **Run tests**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v
```

Expected: all 7 tests PASS.

### Step 4.4: Run the broader runner test suite

- [ ] **Regression-check**

```bash
conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v
```

Expected: no new failures.

### Step 4.5: Commit

- [ ] **Commit**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni add \
  src/backend/aircooling/runner/fields.py \
  src/backend/aircooling/runner/tests/test_turbulence_bc.py
git -C /mnt/lv/litianyu/mycode/dcdtuni commit -m "$(cat <<'EOF'
feat(runner): emit native turbulentIntensity/mixingLength inlet BCs (T2)

Flow inlets now render as OpenFOAM native:
  - k: turbulentIntensityKineticEnergyInlet (intensity= per-bc_type I)
  - ε: turbulentMixingLengthDissipationRateInlet (mixingLength= 0.07·D_h)

Benefits:
- OpenFOAM auto-recomputes k/ε from live U each iter (no regen on BC change)
- Automatic reverse-flow guard (degrades to zeroGradient on backflow)
- Declarative semantics (reader sees intensity 0.10, not a magic number)

Domain outlets retain the inletOutlet guard (value only active on backflow).

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Swap RAS model to realizableKE (T3)

**Files:**
- Modify: `src/backend/aircooling/third_parties/foam/template/constant/turbulenceProperties.j2:22`

No unit test — a one-line template change. Covered by integration run in Task 6.

### Step 5.1: Edit template

- [ ] **Implement**

In `src/backend/aircooling/third_parties/foam/template/constant/turbulenceProperties.j2`, replace:

```
    RASModel        kEpsilon;
```

with:

```
    RASModel        realizableKE;
```

Also add a comment block right above the `simulationType RAS;` line to document the rationale:

```
// 2026-04-18: realizableKE replaces standard kEpsilon.
// Standard k-ε has a 1/k singularity in the ε source term
// (C_1ε · (ε/k) · P_k) that destabilises under jet/shear inlets —
// CMI-China NaN at iter ~309 and Tri_angle ε-boundedness saturation
// at 5.3e6 m²/s³ both traced to this. Shih et al. (1995) realizable
// variant replaces the denominator with (k + √(νε)) — no singularity.
// Identical k/ε BC types → no downstream template changes required.
```

### Step 5.2: Verify renderer still produces valid OpenFOAM syntax

- [ ] **Smoke-test the template**

```bash
conda run -n dcdtuni python -c "
from dcdtuni.runner.openfoam_case import template_env
t = template_env.get_template('foam/template/constant/turbulenceProperties.j2')
print(t.render(turbulence='on'))
"
```

Expected output: valid `simulationType RAS;` block with `RASModel realizableKE;`. No template rendering errors.

### Step 5.3: Commit

- [ ] **Commit**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni add src/backend/aircooling/third_parties/foam/template/constant/turbulenceProperties.j2
git -C /mnt/lv/litianyu/mycode/dcdtuni commit -m "$(cat <<'EOF'
fix(foam): RASModel kEpsilon → realizableKE (T3)

Shih et al. (1995) realizable k-ε replaces ε destruction denominator
from k to (k + √(νε)), eliminating the 1/k singularity that destabilises
jet/shear inlets. Same k/ε/ν_t BC types → no downstream template work.

Expected runtime overhead: ~5%/iter. Benefit: forgiving of mild BC
misspecification (defence-in-depth alongside the physically-grounded
I and D_h from previous commits).

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Manual integration — 500 iter on 3 canonical cases

**Files:** None (manual run + verification).

**Prerequisite:** `STLGeneratorForOpenFoam/server/server.py` running in dev mode with `--case-dir` pointing at one of the cases. Login flow per `.claude/CLAUDE.md`:

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case/Tri_angle &
sleep 3
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}'
```

### Step 6.1: Full-pipeline run for Tri_angle (mesh + solve)

- [ ] **Run**

```bash
# Clear stale mesh (forces new snappy with realizableKE-compatible fields)
rm -rf case/Tri_angle/Mesh/constant/polyMesh
rm -rf case/Tri_angle/Mesh/0
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/set-case-dir \
  -H "Content-Type: application/json" \
  -d '{"caseDir":"/mnt/lv/litianyu/mycode/dcdtuni/case/Tri_angle"}'
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/run-mesh \
  -H "Content-Type: application/json" -d '{}'
```

Wait ~4-5 min for mesh+solve to complete (check `ps -ef | grep buoyant` until 0).

- [ ] **Verify BCs were rendered correctly**

```bash
grep -c "turbulentIntensityKineticEnergyInlet" case/Tri_angle/Mesh/0/k
# Expected: ≥ 3 (3 fan-server outlets at minimum)
grep -c "turbulentMixingLengthDissipationRateInlet" case/Tri_angle/Mesh/0/epsilon
# Expected: ≥ 3
grep -c "realizableKE" case/Tri_angle/Mesh/constant/turbulenceProperties
# Expected: 1
grep -c "intensity       0.1" case/Tri_angle/Mesh/0/k
# Expected: ≥ 3 (all fan_outlet patches get 10%)
grep -c "intensity       0.05" case/Tri_angle/Mesh/0/k
# Expected: ≥ 1 (ACU_Top_patch)
```

- [ ] **Verify ε magnitude is physical**

```bash
LATEST_T=$(ls -t case/Tri_angle/log/ | head -1)
# ε_max in last 100 iter (expect all below 1e5, was 5.3e6 before)
awk '/^Time =/ {t=$NF} /bounding epsilon/ && t>400 {
  n=split($0,a," ")
  for(i=1;i<=n;i++) if(a[i]=="max:") print a[i+1]
}' case/Tri_angle/log/$LATEST_T/log.mpi_solver | sort -g | tail -3
# Expected: all < 1e5 (was 5.3e6 before fix)

# End marker present
tail -5 case/Tri_angle/log/$LATEST_T/log.mpi_solver | grep -c "^End"
# Expected: 1
```

### Step 6.2: Full-pipeline run for CMI-China

- [ ] **Run + verify**

Same commands as 6.1 but targeting `case/CMI-China`. Expected:
- `End` reached
- ε_max in last 100 iter in O(10⁴-10⁵) range (was 76k pre-change, may shift a bit with realizableKE; stays below 1e5)
- No NaN
- No pathological jumps (e.g. Time=308→309 epsilon max factor >100 — was the pre-relax=0.3 symptom)

### Step 6.3: Full-pipeline run for base_leakage

- [ ] **Run + verify**

Same commands as 6.1 but targeting `case/base_leakage`. Expected:
- `End` reached at endTime=500
- ε_max in last 100 iter stays O(10²-10³)
- No regression

### Step 6.4: Record results in a brief regression note

- [ ] **Document**

Append to `docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md` (at the end, under a new `## Verification Results` section):

```markdown
## Verification Results — 2026-04-18

| Case | End? | ε_max (last 100 iter) | ε_avg (last 100 iter) | ν_t_min |
|---|---|---|---|---|
| Tri_angle | ✓ | {fill in} | {fill in} | {fill in} |
| CMI-China | ✓ | {fill in} | {fill in} | {fill in} |
| base_leakage | ✓ | {fill in} | {fill in} | {fill in} |
```

Replace `{fill in}` with actual values harvested from each run's `log.mpi_solver`. Use this snippet to extract values:

```bash
CASE=Tri_angle
LATEST=$(ls -t case/$CASE/log/ | head -1)
awk '/^Time =/ {t=$NF} /bounding epsilon/ && t>400 {
  n=split($0,a," ")
  for(i=1;i<=n;i++) {
    if(a[i]=="max:") mx=a[i+1]
    if(a[i]=="average:") av=a[i+1]
  }
  mxs[NR]=mx; avs[NR]=av
}
END {
  n=asort(mxs); print "ε_max (top 3): " mxs[n] " " mxs[n-1] " " mxs[n-2]
  s=0; c=0; for(i=1;i<=n;i++) {s+=avs[i]; c++}
  printf("ε_avg mean: %.4f\n", s/c)
}' case/$CASE/log/$LATEST/log.mpi_solver
```

### Step 6.5: Commit the verification note

- [ ] **Commit**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni add docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md
git -C /mnt/lv/litianyu/mycode/dcdtuni commit -m "$(cat <<'EOF'
docs(spec): turbulence BC verification results for 3 canonical cases

All three cases reach `End` with physical ε magnitudes — Tri_angle no
longer sits on the boundedness limiter (ε_max from 5.3e6 to O(1e3-1e4)),
CMI-China and base_leakage show no regression.

Spec: docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final push

**Files:** None — push only.

### Step 7.1: Push to origin

- [ ] **Push**

```bash
git -C /mnt/lv/litianyu/mycode/dcdtuni push origin main
git -C /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam log --oneline -3
# Verify submodule pointer is clean (no stray edits in STLGeneratorForOpenFoam)
git -C /mnt/lv/litianyu/mycode/dcdtuni status
# Expected: no modified, no untracked files pending from this feature.
```

---

## Done criteria checklist (mirror of spec §Done criteria)

- [ ] All 7 unit tests pass (`pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py`)
- [ ] Tri_angle 500-iter: ε_max (last 100 iter) < 1e5, pathological boundedness pattern gone
- [ ] CMI-China 500-iter: `End`, ε_max stays O(1e4-1e5), no regression
- [ ] base_leakage 500-iter: `End`, ε_max stays O(10²-10³)
- [ ] Other runner tests (`pytest src/backend/aircooling/runner/tests/`) still pass
- [ ] Pushed to origin
