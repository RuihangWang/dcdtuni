# Perforated-Plate Setting Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three divergent "porosity %" knobs (baffle model CFD, baffle patch, floor-patch opening) with one unified `{shape, holeDiameterMm, openAreaPct}` settings block, and convert those physical params to `porousBafflePressure` (D, I, length) in the backend using Idelchik's thin perforated-plate formula.

**Architecture:** Frontend exports raw params into the cfdrun JSON (no math on the JS side). Backend `bridge_bc.BCSpec` grows a `perforated: Optional[PerforatedSpec]` field, `fields.py` generates `porousBafflePressure` from that spec, and a single new function `perforated_to_porous_baffle()` owns the conversion. Frontend inspector logic uses a single shared helper `renderPerforatedInspector` called from three sites.

**Tech Stack:** Python 3.10, pydantic v1 (BCSpec already uses `pydantic.BaseModel`), pytest, vanilla JS (no framework) in a single 28 k-line HTML file.

**Spec:** [`docs/superpowers/specs/2026-04-16-perforated-plate-unification-design.md`](../specs/2026-04-16-perforated-plate-unification-design.md)

---

## File Structure

| File | Role | Change |
|---|---|---|
| `src/backend/aircooling/runner/bridge_bc.py` | Owns `PerforatedSpec` + `perforated_to_porous_baffle()` + extends `BCSpec` | modify |
| `src/backend/aircooling/runner/fields.py` | Replace hard-coded `D=(1-σ²)/(2σ²), I=0` at line 917 with call to new function | modify |
| `src/backend/aircooling/runner/stlgen_runner.py` | Pass full `PerforatedSpec` (not just `sigma`) when registering `perforated_baffle` | modify |
| `src/backend/aircooling/runner/cfdproj_schema.py` | Document new `perforated` block in cfdrun schema | modify |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Default, migration, inspector helper, 3 call sites, cfdrun export, handlers | modify |
| `STLGeneratorForOpenFoam/web/i18n/{zh-CN,en-US}.json` | Labels for 穿孔板设置 / shape / diameter / open-area / K estimate | modify |
| `STLGeneratorForOpenFoam/projects/*.cfdproj` | Strip old `porosity` fields from saved files (14 files) | modify |
| `tests/test_perforated.py` | Golden-value + BCSpec round-trip + p_rgh snippet | new |

---

## Task 1: Backend formula + golden-value unit test (TDD)

**Files:**
- Create: `tests/test_perforated.py`
- Modify: `src/backend/aircooling/runner/bridge_bc.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_perforated.py`:

```python
"""Unit tests for the perforated-plate Idelchik formula."""
import math

import pytest

from dcdtuni.runner.bridge_bc import PerforatedSpec, perforated_to_porous_baffle


GOLDEN_CIRCLE = [
    # (open_area_pct, expected_K)  — φ=1, computed analytically from
    # K = (1 + 0.707·√(1-σ) - σ)² / σ²  (Idelchik §8.2 eq. 8-4)
    (20.0, 51.27),
    (40.0, 8.232),
    (64.0, 1.499),
    (80.0, 0.416),
]


@pytest.mark.parametrize("open_area_pct,expected_K", GOLDEN_CIRCLE)
def test_circle_golden_values(open_area_pct, expected_K):
    spec = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=open_area_pct)
    D, I, length = perforated_to_porous_baffle(spec)
    assert D == 0.0
    assert length == 1.0
    assert math.isclose(I, expected_K, rel_tol=0.01), f"I={I} vs expected {expected_K}"


def test_shape_ordering_at_fixed_sigma():
    """At σ=0.5, K(circle) < K(rect) < K(diamond) — φ rises with perimeter."""
    kwargs = dict(holeDiameterMm=3.0, openAreaPct=50.0)
    _, k_circle, _ = perforated_to_porous_baffle(PerforatedSpec(shape="circle", **kwargs))
    _, k_rect, _ = perforated_to_porous_baffle(PerforatedSpec(shape="rect", **kwargs))
    _, k_diamond, _ = perforated_to_porous_baffle(PerforatedSpec(shape="diamond", **kwargs))
    assert k_circle < k_rect < k_diamond


def test_sigma_extremes_are_clamped():
    """σ=0 would divide by zero; must clamp to avoid NaN/Inf."""
    spec = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=0.0)
    _, I, _ = perforated_to_porous_baffle(spec)
    assert math.isfinite(I) and I > 1e4  # large but finite
    spec = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=100.0)
    _, I, _ = perforated_to_porous_baffle(spec)
    assert math.isfinite(I) and I >= 0.0


def test_unknown_shape_falls_back_to_circle():
    spec = PerforatedSpec(shape="hexagon", holeDiameterMm=3.0, openAreaPct=64.0)
    _, I_hex, _ = perforated_to_porous_baffle(spec)
    _, I_circle, _ = perforated_to_porous_baffle(
        PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=64.0)
    )
    assert math.isclose(I_hex, I_circle, rel_tol=1e-9)
```

- [ ] **Step 1.2: Run to verify it fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: `ImportError: cannot import name 'PerforatedSpec' from 'dcdtuni.runner.bridge_bc'`.

- [ ] **Step 1.3: Implement the minimal formula**

Append to `src/backend/aircooling/runner/bridge_bc.py` (after the existing `BCSpec` class, before `BridgeBCRegistry`):

```python
# ---------- Perforated-plate physics (Idelchik §8.2 eq. 8-4) ----------
# Shape factor φ scales the entrance-loss coefficient 0.707. Circle is
# the reference (sharp-edged holes); rect/diamond have slightly stronger
# vena-contracta losses at the corners.
_SHAPE_PHI = {"circle": 1.00, "rect": 1.10, "diamond": 1.15}


class PerforatedSpec(BaseModel):
    """Physical description of a perforated plate — the *only* params the
    user sets. Conversion to (D, I, length) for OpenFOAM's
    porousBafflePressure happens in perforated_to_porous_baffle().
    """
    shape: str = "circle"           # 'circle' | 'rect' | 'diamond'
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


def perforated_to_porous_baffle(spec: PerforatedSpec) -> tuple[float, float, float]:
    """Map (shape, σ) → (D, I, length) using Idelchik's thin sharp-edged
    perforated-plate formula:

        K = [1 + φ·0.707·(1-σ)^0.5 - σ]² / σ²

    For thin HVAC plates (ℓ/d_h < 0.5) the viscous term in OpenFOAM's
    porousBafflePressure is negligible, so D=0 and the entire resistance
    lives on I. `length` is the OF normalization (not plate thickness).

    Returns (D [1/m²], I [dimensionless], length [m]).
    """
    sigma = max(1e-4, min(0.9999, spec.openAreaPct / 100.0))
    phi = _SHAPE_PHI.get(spec.shape, 1.0)
    K = (1.0 + phi * 0.707 * (1.0 - sigma) ** 0.5 - sigma) ** 2 / sigma ** 2
    return (0.0, K, 1.0)
```

- [ ] **Step 1.4: Run to verify it passes**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: 8 passed (4 parametrised + 3 others).

- [ ] **Step 1.5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add src/backend/aircooling/runner/bridge_bc.py tests/test_perforated.py
git commit -m "feat(bc): add perforated-plate Idelchik formula (D=0, I=K, length=1)"
```

---

## Task 2: Extend `BCSpec` with `perforated` field + propagate through registry (TDD)

**Files:**
- Modify: `src/backend/aircooling/runner/bridge_bc.py`
- Modify: `tests/test_perforated.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_perforated.py`:

```python
from dcdtuni.runner.bridge_bc import BCSpec, BridgeBCRegistry


def test_bcspec_accepts_perforated_field():
    spec = BCSpec(
        bc_type="perforated_baffle",
        perforated=PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=64.0),
    )
    assert spec.perforated is not None
    assert spec.perforated.openAreaPct == 64.0


def test_bcspec_registry_roundtrip():
    reg = BridgeBCRegistry()
    reg.register("baffle_A_master", BCSpec(
        bc_type="perforated_baffle",
        perforated=PerforatedSpec(openAreaPct=40.0),
    ))
    got = reg.get("baffle_A_master")
    assert got is not None
    assert got.bc_type == "perforated_baffle"
    assert got.perforated.openAreaPct == 40.0
```

- [ ] **Step 2.2: Run to verify it fails**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py::test_bcspec_accepts_perforated_field -v
```

Expected: validation error — `BCSpec` has no `perforated` field.

- [ ] **Step 2.3: Add the field**

In `src/backend/aircooling/runner/bridge_bc.py`, modify `BCSpec`:

```python
class BCSpec(BaseModel):
    """Boundary condition specification for a single STL solid patch."""
    bc_type: str = "wall"
    temperature: Optional[float] = None   # Celsius; used for supply/fixedTemp patches
    flow_rate: Optional[float] = None     # m³/s; used for supply/return/fan patches
    power: Optional[float] = None         # W; used for fan_outlet temperature computation
    sigma: Optional[float] = None         # legacy porosity coefficient (rack leakage_zone cellZones only)
    perforated: Optional["PerforatedSpec"] = None  # replaces sigma for perforated baffles
    heat_flux: Optional[float] = None     # W/m²; used for heat_source_flux BC
```

Since `PerforatedSpec` is defined further down, add a forward reference fixup at the end of the file:

```python
BCSpec.update_forward_refs()
```

- [ ] **Step 2.4: Run to verify it passes**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: all tests pass (10 total now).

- [ ] **Step 2.5: Commit**

```bash
git add src/backend/aircooling/runner/bridge_bc.py tests/test_perforated.py
git commit -m "feat(bc): BCSpec gains optional perforated field for baffle plates"
```

---

## Task 3: `fields.py` consumes `PerforatedSpec` when generating `porousBafflePressure` (TDD)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py`
- Modify: `tests/test_perforated.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_perforated.py`:

```python
def test_fields_emits_porous_baffle_from_perforated_spec():
    """When BCSpec.perforated is populated, fields.py must compute I from
    the Idelchik formula (ignoring the legacy sigma field)."""
    from dcdtuni.runner.fields import _format_porous_baffle_block

    spec = BCSpec(
        bc_type="perforated_baffle",
        perforated=PerforatedSpec(shape="circle", openAreaPct=64.0),
    )
    lines = _format_porous_baffle_block("baffle_A_master", spec)
    text = "\n".join(lines)
    assert "type            porousBafflePressure" in text
    assert "patchType       cyclic" in text
    assert "length          1" in text
    assert "D               0.000000" in text
    # I ≈ 1.499 for σ=0.64
    match = [ln for ln in lines if "I " in ln]
    assert match, f"no I line in {lines}"
    import re
    m = re.search(r"I\s+([\d.]+)", match[0])
    assert m, match[0]
    assert abs(float(m.group(1)) - 1.499) < 0.01


def test_fields_falls_back_to_sigma_when_perforated_missing():
    """Legacy rack-leakage BCSpecs (sigma only, no perforated) keep
    the old viscous formula so no rack-path regression."""
    from dcdtuni.runner.fields import _format_porous_baffle_block
    spec = BCSpec(bc_type="perforated_baffle", sigma=0.5)
    lines = _format_porous_baffle_block("rack_leakage_zone", spec)
    text = "\n".join(lines)
    assert "porousBafflePressure" in text
    # Legacy: D = (1-0.25)/(2·0.25) = 1.5, I = 0
    import re
    m_d = re.search(r"D\s+([\d.]+)", text)
    m_i = re.search(r"I\s+([\d.]+)", text)
    assert m_d and m_i
    assert abs(float(m_d.group(1)) - 1.5) < 0.01
    assert abs(float(m_i.group(1))) < 1e-9
```

- [ ] **Step 3.2: Run to verify it fails**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: `ImportError: cannot import name '_format_porous_baffle_block'`.

- [ ] **Step 3.3: Extract the porous-baffle block into a named helper and route through the perforated spec**

In `src/backend/aircooling/runner/fields.py`, replace lines 914-932 (the `else: # "porous"` branch inside `_p_rgh` generation) and lines 960-981 (the opening_* branch) with a shared helper. Add near the top of the file (after imports):

```python
from dcdtuni.runner.bridge_bc import BCSpec, perforated_to_porous_baffle


def _format_porous_baffle_block(patch_name: str, spec: BCSpec) -> list[str]:
    """Build p_rgh lines for a porousBafflePressure patch. Prefers the
    new PerforatedSpec (Idelchik formula); falls back to legacy sigma
    (D=(1-σ²)/(2σ²), I=0) for rack-leakage bridge specs that have never
    had perforated geometry."""
    if spec.perforated is not None:
        D, I, length = perforated_to_porous_baffle(spec.perforated)
    else:
        sigma = spec.sigma if spec.sigma is not None else 0.5
        D = (1.0 - sigma ** 2) / (2.0 * sigma ** 2) if sigma > 0 else 1e6
        I = 0.0
        length = 1.0
    return [
        f"    {patch_name}",
        "    {",
        "        type            porousBafflePressure;",
        "        patchType       cyclic;",
        f"        D               {D:.6f};",
        f"        I               {I:.6f};",
        f"        length          {length:.0f};",
        "        uniformJump     false;",
        "        jump            uniform 0;",
        "        value           uniform 0;",
        "    }",
    ]
```

Then at **line ~914** (inside the existing baffle branch, currently builds the dict `bc_info` and reads `bc_info.get("sigma", 0.5)`), replace the block with:

```python
                    else:
                        # "porous": 0 < porosity < 1 → porousBafflePressure
                        # bc_info is a dict view of a BCSpec; reconstruct to
                        # access the perforated field.
                        spec = BCSpec(**bc_info) if not isinstance(bc_info, BCSpec) else bc_info
                        lines.extend(_format_porous_baffle_block(patch_name, spec))
                        logger.info(f"porousBafflePressure for {patch_name}: "
                                    f"shape={getattr(spec.perforated, 'shape', None)}, "
                                    f"sigma={spec.sigma}")
```

And at **line ~960** (the `opening_*` perforated-baffle branch, currently manually computes `D` and `I` from a stored `bc_config`), delete the manual computation and replace with:

```python
            # Perforated baffle patches - use porousBafflePressure
            elif self._is_perforated_baffle_patch(patch_name):
                spec = self.bridge_registry.get(patch_name)
                if spec is None:
                    # Defensive: should not happen for perforated patches.
                    spec = BCSpec(bc_type="perforated_baffle", sigma=0.5)
                lines.extend(_format_porous_baffle_block(patch_name, spec))
                logger.info(f"Generated porousBafflePressure BC for {patch_name} via Idelchik")
```

- [ ] **Step 3.4: Run to verify it passes**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: all 12 tests pass (2 new: `test_fields_emits_porous_baffle_from_perforated_spec`, `test_fields_falls_back_to_sigma_when_perforated_missing`).

- [ ] **Step 3.5: Commit**

```bash
git add src/backend/aircooling/runner/fields.py tests/test_perforated.py
git commit -m "feat(fields): _format_porous_baffle_block uses Idelchik when perforated set"
```

---

## Task 4: `stlgen_runner` registers `BCSpec` with `perforated` from cfdrun (TDD)

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py` (doc only)
- Modify: `tests/test_perforated.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_perforated.py`:

```python
def test_stlgen_runner_builds_perforated_physics_lookup():
    """Physics dict built from cfdrun carries the perforated block for
    baffles whose physics.baffleType == 'perforated'."""
    from dcdtuni.runner.stlgen_runner import STLGenRunner

    runner = STLGenRunner.__new__(STLGenRunner)  # skip __init__
    runner._baffle_physics = {}
    runner._data = {
        "baffles": {
            "Baffle_1": {
                "physics": {
                    "baffleType": "perforated",
                    "perforated": {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0},
                },
                "solid": "Baffle_1",
            }
        }
    }
    runner._build_baffle_physics_lookup()
    phys = runner._baffle_physics["Baffle_1"]
    assert phys["baffleType"] == "perforated"
    assert phys["perforated"]["openAreaPct"] == 64.0
```

- [ ] **Step 4.2: Run to verify it fails**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py::test_stlgen_runner_builds_perforated_physics_lookup -v
```

Expected: KeyError on `"perforated"` (or similar — current lookup only stores `baffleType` + `porosity`).

- [ ] **Step 4.3: Update `_build_baffle_physics_lookup`**

In `src/backend/aircooling/runner/stlgen_runner.py`, locate `_build_baffle_physics_lookup` (line ~1567) and extend the physics dict build to pass `perforated` through. The exact current shape depends on the existing implementation, but the fix is to preserve `physics.perforated` if present:

```python
    def _build_baffle_physics_lookup(self) -> None:
        """Build lookup: sanitized solid name → {baffleType, porosity, perforated}."""
        baffle_physics: Dict[str, Dict] = {}
        for name, baffle in (self._data.get("baffles") or {}).items():
            phys = baffle.get("physics") or {}
            san = _sanitize(baffle.get("solid", name))
            entry = {
                "baffleType": phys.get("baffleType", "wall"),
                "porosity": phys.get("porosity", 0),  # legacy fallback for sigma
            }
            if phys.get("perforated"):
                entry["perforated"] = dict(phys["perforated"])
            baffle_physics[san] = entry
        # ... preserve any existing door/other physics collection below this block
        self._baffle_physics = baffle_physics
```

(Check the existing function — if it already builds a richer dict that includes door info, splice the `perforated` extraction into the existing baffle iteration without changing other behaviour.)

Then in the two registration sites (lines ~2575 and ~2595) pass `perforated`:

**Floor openings (~L2575):**

```python
                    else:
                        # porosity > 0 → cyclic + porousBafflePressure
                        perf = op["physics"].get("perforated")
                        perf_spec = PerforatedSpec(**perf) if perf else None
                        registry.register(f"{solid}_master", BCSpec(
                            bc_type="perforated_baffle", sigma=porosity, perforated=perf_spec))
                        registry.register(f"{solid}_slave", BCSpec(
                            bc_type="perforated_baffle", sigma=porosity, perforated=perf_spec))
```

**Baffle solids (~L2595):**

```python
            if porosity_pct > 0:
                porosity = porosity_pct / 100.0
                perf = phys.get("perforated")
                perf_spec = PerforatedSpec(**perf) if perf else None
                registry.register(f"{san}_master", BCSpec(
                    bc_type="perforated_baffle", sigma=porosity, perforated=perf_spec))
                registry.register(f"{san}_slave", BCSpec(
                    bc_type="perforated_baffle", sigma=porosity, perforated=perf_spec))
```

Add at the top of `stlgen_runner.py` (if not already imported):

```python
from dcdtuni.runner.bridge_bc import BCSpec, PerforatedSpec
```

- [ ] **Step 4.4: Run to verify it passes**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: 13 passed.

- [ ] **Step 4.5: Update schema docstring**

In `src/backend/aircooling/runner/cfdproj_schema.py`, find the baffle physics block documentation and add a note:

```python
# baffles[*].physics = {
#   "baffleType": "wall" | "perforated",
#   "perforated": {                # required when baffleType == "perforated"
#     "shape": "circle" | "rect" | "diamond",
#     "holeDiameterMm": float,
#     "openAreaPct": float         # 0..100
#   }
# }
```

- [ ] **Step 4.6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/cfdproj_schema.py tests/test_perforated.py
git commit -m "feat(runner): propagate PerforatedSpec from cfdrun to BCSpec registry"
```

---

## Task 5: Frontend defaults + `normalizeState` migration (scrub + install default)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Create: `STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py`

- [ ] **Step 5.1: Write the failing structural test**

Create `STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py`:

```python
"""Structural tests for the perforated-plate frontend unification."""
import re
import sys

HTML_PATH = "STLGeneratorForOpenFoam/web/STL_generate.html"
HTML = open(HTML_PATH).read()


def extract_fn_body(name):
    m = re.search(rf"function {name}\s*\([^)]*\)\s*\{{", HTML)
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


def test_default_baffle_cfd_has_perforated():
    body = extract_fn_body("getDefaultCfd")
    assert body is not None
    assert "perforated" in body, "getDefaultCfd must seed a perforated sub-object"
    assert "'circle'" in body
    assert "holeDiameterMm" in body
    assert "openAreaPct" in body


def test_normalize_state_scrubs_old_porosity():
    body = extract_fn_body("normalizeState")
    assert body is not None
    assert "delete" in body and "porosity" in body, (
        "normalizeState must delete legacy porosity fields on load"
    )


def test_render_perforated_inspector_defined_and_used_four_times():
    body = extract_fn_body("renderPerforatedInspector")
    assert body is not None, "renderPerforatedInspector helper not defined"
    # Count CALL sites (not the definition itself). Four sites exist:
    # 1. baffle model inspector (global baffleType)
    # 2. baffle model inspector's per-patch inline summary
    # 3. standalone patch inspector (baffle patch in PATCH context)
    # 4. floor opening inspector
    calls = [m for m in re.finditer(r"renderPerforatedInspector\s*\(", HTML)
             if HTML[m.start() - 10 : m.start()].strip() != "function"]
    assert len(calls) == 4, f"expected 4 call sites, found {len(calls)}"


def test_build_cfd_export_emits_perforated_not_porosity():
    body = extract_fn_body("buildCfdExportJson")
    assert body is not None
    # In the baffle / floor-opening / patch-physics emit blocks, the key
    # 'porosity' must no longer appear as an object key.
    assert "porosity:" not in body and '"porosity"' not in body, (
        "buildCfdExportJson still emits a 'porosity' key — must emit 'perforated' instead"
    )
    assert "perforated" in body, "buildCfdExportJson must emit perforated"


tests = [
    test_default_baffle_cfd_has_perforated,
    test_normalize_state_scrubs_old_porosity,
    test_render_perforated_inspector_defined_and_used_thrice,
    test_build_cfd_export_emits_perforated_not_porosity,
]
failed = 0
for t in tests:
    try:
        t()
        print(f"PASS: {t.__name__}")
    except AssertionError as e:
        print(f"FAIL: {t.__name__}: {e}")
        failed += 1
sys.exit(1 if failed else 0)
```

- [ ] **Step 5.2: Run to verify it fails**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
python3 STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
```

Expected: all 4 tests FAIL.

- [ ] **Step 5.3: Modify `getDefaultCfd` defaults**

Open `STLGeneratorForOpenFoam/web/STL_generate.html` at line ~2348. Modify the baffle and floor defaults:

```js
case 'baffle': return {
    role: 'baffle',
    baffleType: 'wall',
    perforated: { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 },
    patchPhysics: {}
};
case 'floor': return {
    role: 'floor',
    openings: [],
    defaultBC: { type: 'wall', porosity: 0.0 }  // porosity here is a FLOW BC param, unchanged
};
```

(Note: `floor.defaultBC.porosity` is the *remaining-area* BC param for the floor area that has no openings — unrelated to the perforated-plate hole geometry. Keep it untouched.)

- [ ] **Step 5.4: Add `normalizeState` migration block**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, locate `normalizeState` (around line 3070) and add the perforated-migration block inside the per-model loop, immediately after the existing `cfd` field is populated. Insert:

```js
            // --- Perforated-plate unification (2026-04-16) ---
            // Drop legacy top-level `porosity` scalars from baffle/floor/patch
            // physics; install the fixed default perforated sub-object wherever
            // the parent toggle is still 'perforated'. Spec:
            // docs/superpowers/specs/2026-04-16-perforated-plate-unification-design.md
            const DEFAULT_PERFORATED = { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };
            if (model.cfd && model.kind === 'baffle') {
                delete model.cfd.porosity;
                if (model.cfd.baffleType === 'perforated' && !model.cfd.perforated) {
                    model.cfd.perforated = { ...DEFAULT_PERFORATED };
                }
                // Per-patch override
                if (model.cfd.patchPhysics) {
                    Object.values(model.cfd.patchPhysics).forEach(pp => {
                        delete pp.porosity;
                        if (pp.baffleType === 'perforated' && !pp.perforated) {
                            pp.perforated = { ...DEFAULT_PERFORATED };
                        }
                    });
                }
            }
            if (model.cfd && model.kind === 'floor') {
                (model.cfd.openings || []).forEach(op => {
                    delete op.porosity;  // was hole-level porosity, not BC-level
                    if (op.type === 'perforated' && !op.perforated) {
                        op.perforated = { ...DEFAULT_PERFORATED };
                    }
                });
            }
```

- [ ] **Step 5.5: Run tests — 2 of 4 should now pass**

```bash
python3 STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
```

Expected: `test_default_baffle_cfd_has_perforated` and `test_normalize_state_scrubs_old_porosity` pass, the other two still fail (no helper yet, no export emit).

- [ ] **Step 5.6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
git commit -m "feat(frontend): getDefaultCfd + normalizeState seed/scrub perforated block"
```

---

## Task 6: `renderPerforatedInspector` helper + i18n keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 6.1: Add i18n keys**

In `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`, next to the existing `label.porosity` entry, add:

```json
  "inspector.perforated_title": "🌀 穿孔板设置",
  "label.hole_shape": "开孔形状",
  "label.hole_shape_circle": "🔵 圆",
  "label.hole_shape_rect": "🟥 矩形",
  "label.hole_shape_diamond": "🔶 菱形",
  "label.hole_diameter_mm": "孔径 (mm)",
  "label.open_area_pct": "开孔率 (%)",
  "label.k_estimate": "估算 K",
  "hint.perforated_formula": "Idelchik 薄板公式 · K = [1+φ·0.707·√(1-σ)-σ]² / σ²",
```

In `STLGeneratorForOpenFoam/web/i18n/en-US.json`:

```json
  "inspector.perforated_title": "🌀 Perforated plate",
  "label.hole_shape": "Hole shape",
  "label.hole_shape_circle": "🔵 Circle",
  "label.hole_shape_rect": "🟥 Rectangle",
  "label.hole_shape_diamond": "🔶 Diamond",
  "label.hole_diameter_mm": "Hole diameter (mm)",
  "label.open_area_pct": "Open area (%)",
  "label.k_estimate": "K estimate",
  "hint.perforated_formula": "Idelchik thin-plate · K = [1+φ·0.707·√(1-σ)-σ]² / σ²",
```

- [ ] **Step 6.2: Add helper function**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, insert just above the baffle inspector block (line ~9450):

```js
    // ---------- Shared perforated-plate inspector block (2026-04-16) ----------
    // Used by baffle-model CFD, baffle-patch physics, and floor-patch openings.
    // `perforated` must be a {shape, holeDiameterMm, openAreaPct} object;
    // `onChangeCall` is a string of JS invoking the site-specific handler
    // with three placeholders: {FIELD} and {VALUE}. The caller is responsible
    // for wiring undo/log via the handler.
    const PERFORATED_SHAPE_PHI = { circle: 1.00, rect: 1.10, diamond: 1.15 };

    function _perforatedEstimateK(perf) {
        const sigma = Math.max(1e-4, Math.min(0.9999, (perf.openAreaPct || 64) / 100));
        const phi = PERFORATED_SHAPE_PHI[perf.shape] || 1.0;
        return Math.pow(1 + phi * 0.707 * Math.sqrt(1 - sigma) - sigma, 2) / (sigma * sigma);
    }

    function renderPerforatedInspector(perforated, onChangeCall) {
        const perf = perforated || { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };
        const shape = perf.shape || 'circle';
        const diameter = Number.isFinite(perf.holeDiameterMm) ? perf.holeDiameterMm : 3.0;
        const pct = Number.isFinite(perf.openAreaPct) ? perf.openAreaPct : 64.0;
        const k = _perforatedEstimateK(perf).toFixed(2);
        const opt = (v, label) => `<option value="${v}" ${shape === v ? 'selected' : ''}>${t(label)}</option>`;
        return `<div class="prop-group" style="border-color:#16825d">
            <div class="prop-title" style="color:#16825d">${t('inspector.perforated_title')}</div>
            <div class="prop-row"><label>${t('label.hole_shape')}</label>
                <select onchange="${onChangeCall.replace('{FIELD}', 'perforated.shape').replace('{VALUE}', 'this.value')}">
                    ${opt('circle', 'label.hole_shape_circle')}
                    ${opt('rect', 'label.hole_shape_rect')}
                    ${opt('diamond', 'label.hole_shape_diamond')}
                </select>
            </div>
            <div class="prop-row"><label>${t('label.hole_diameter_mm')}</label>
                <input type="number" step="0.1" min="0.1" value="${diameter}"
                    onchange="${onChangeCall.replace('{FIELD}', 'perforated.holeDiameterMm').replace('{VALUE}', 'parseFloat(this.value)')}">
            </div>
            <div class="prop-row"><label>${t('label.open_area_pct')}</label>
                <input type="number" step="1" min="0.1" max="99.9" value="${pct}"
                    onchange="${onChangeCall.replace('{FIELD}', 'perforated.openAreaPct').replace('{VALUE}', 'parseFloat(this.value)')}">
            </div>
            <div class="prop-row"><label>${t('label.k_estimate')}</label>
                <span style="font-family:monospace;color:var(--hint-color);">${k}</span>
            </div>
            <div style="font-size:0.72rem; color:var(--text-dim); line-height:1.5;">${t('hint.perforated_formula')}</div>
        </div>`;
    }
```

- [ ] **Step 6.3: Run the structural test again (helper existence)**

```bash
python3 STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
```

Expected: `test_render_perforated_inspector_defined_and_used_thrice` now reports "expected 3 call sites, found 0" (definition alone counts as 0 via the `function` filter).

- [ ] **Step 6.4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(frontend): add renderPerforatedInspector shared helper + i18n"
```

---

## Task 7: Wire the helper into all three call sites

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 7.1: Replace the baffle model inspector porosity block (~L9452)**

Find the existing block around line 9452:

```js
        if (model.kind === 'baffle') {
            html += `<div class="prop-group" style="border-color:#16825d"><div class="prop-title" style="color:#16825d">${t('inspector.baffle_cfd')}</div>
            <div class="prop-row"><label>${t('label.baffle_type')}</label><select onchange="updateModelCfd('${model.id}', 'baffleType', this.value)">
                <option value="wall" ${cfd.baffleType === 'wall' ? 'selected' : ''}>${t('label.baffle_wall')}</option>
                <option value="perforated" ${cfd.baffleType === 'perforated' ? 'selected' : ''}>${t('label.baffle_perforated')}</option>
            </select></div>
            ${cfd.baffleType === 'perforated' ? `<div class="prop-row"><label>${t('label.porosity')}</label><input type="number" step="1" min="0" max="100" value="${cfd.porosity || 0}" onchange="updateModelCfd('${model.id}', 'porosity', parseFloat(this.value))"></div>
            <div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55;">${t('hint.perforated_desc')}</div>` : ''}
```

Replace the `${cfd.baffleType === 'perforated' ? ...}` expression with a call to the shared helper:

```js
            ${cfd.baffleType === 'perforated' ? renderPerforatedInspector(
                cfd.perforated,
                `updateModelCfd('${model.id}', '{FIELD}', {VALUE})`
            ) : ''}
```

- [ ] **Step 7.2: Replace the baffle patch inspector porosity block (~L9462)**

Find the patch-physics block:

```js
                <div class="prop-row"><label>${t('label.baffle_type')}</label><select onchange="updateBafflePatchPhysics('${model.id}', '${patch.id}', 'baffleType', this.value)">
                    <option value="wall" ${pp.baffleType === 'wall' ? 'selected' : ''}>${t('label.baffle_wall')}</option>
                    <option value="perforated" ${pp.baffleType === 'perforated' ? 'selected' : ''}>${t('label.baffle_perforated')}</option>
                </select></div>
                ${pp.baffleType === 'perforated' ? `<div class="prop-row"><label>${t('label.porosity')}</label><input type="number" step="1" min="0" max="100" value="${pp.porosity || 0}" onchange="updateBafflePatchPhysics('${model.id}', '${patch.id}', 'porosity', parseFloat(this.value))"></div>` : ''}
```

Replace the `${pp.baffleType === 'perforated' ? ...}` ternary with:

```js
                ${pp.baffleType === 'perforated' ? renderPerforatedInspector(
                    pp.perforated,
                    `updateBafflePatchPhysics('${model.id}', '${patch.id}', '{FIELD}', {VALUE})`
                ) : ''}
```

- [ ] **Step 7.3: Replace the standalone baffle patch inspector (~L10948)**

When a baffle patch is selected directly (not via the baffle-model inspector), a separate porosity block renders around line 10948. Find:

```js
                ${pp.baffleType === 'perforated' ? `<div class="prop-row"><label>${t('label.porosity')}</label><input type="number" step="1" min="0" max="100" value="${pp.porosity || 0}" onchange="updateBafflePatchPhysics('${model.id}', '${patch.id}', 'porosity', parseFloat(this.value))"></div>` : ''}
            </div>`;
        }

        // Floor patch: set opening type
```

Replace the inner ternary with the helper call:

```js
                ${pp.baffleType === 'perforated' ? renderPerforatedInspector(
                    pp.perforated,
                    `updateBafflePatchPhysics('${model.id}', '${patch.id}', '{FIELD}', {VALUE})`
                ) : ''}
```

- [ ] **Step 7.4: Replace the floor patch opening inspector (~L10962)**

Find the floor opening block (around line 10962, inside the `if (model.kind === 'floor')` patch inspector branch):

```js
                ${opening.type === 'perforated' ? `<div class="prop-row"><label>${t('label.porosity')}</label><input type="number" step="1" min="0" max="100" value="${opening.porosity}" onchange="updateFloorOpeningCfd('${model.id}', '${patch.id}', 'porosity', parseFloat(this.value))"></div>
                <div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55;">${t('hint.perforated_cyclic')}</div>` : ''}
```

Replace the inner ternary with:

```js
                ${opening.type === 'perforated' ? renderPerforatedInspector(
                    opening.perforated,
                    `updateFloorOpeningCfd('${model.id}', '${patch.id}', '{FIELD}', {VALUE})`
                ) : ''}
```

- [ ] **Step 7.5: Run structural test — 4-call-sites check should pass**

```bash
python3 STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
```

Expected: `test_render_perforated_inspector_defined_and_used_four_times` passes. `test_build_cfd_export_emits_perforated_not_porosity` still fails (Task 8).

- [ ] **Step 7.6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(frontend): wire renderPerforatedInspector into baffle/patch/floor sites"
```

---

## Task 8: Handler dotted-key support + `buildCfdExportJson` emits `perforated`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 8.1: Extend `updateBafflePatchPhysics` to support dotted keys**

At line ~26732:

```js
    window.updateBafflePatchPhysics = function(modelId, patchId, field, value) {
        pushUndoCheckpoint('updateBafflePatchPhysics');
        const model = getModelById(modelId);
        if (!model) return;
        if (!model.cfd) model.cfd = getDefaultCfd('baffle');
        if (!model.cfd.patchPhysics) model.cfd.patchPhysics = {};
        if (!model.cfd.patchPhysics[patchId]) model.cfd.patchPhysics[patchId] = { baffleType: 'wall' };
        const parts = field.split('.');
        let target = model.cfd.patchPhysics[patchId];
        for (let i = 0; i < parts.length - 1; i++) {
            if (!target[parts[i]]) target[parts[i]] = {};
            target = target[parts[i]];
        }
        target[parts[parts.length - 1]] = value;
        updateUI();
        logOperation('updateBafflePatchPhysics', `${field}=${value}`, { modelId, patchId, field, value }, 'modify');
    };
```

- [ ] **Step 8.2: Extend `updateFloorOpeningCfd` to support dotted keys**

At line ~26676:

```js
    window.updateFloorOpeningCfd = function(modelId, patchId, field, value) {
        pushUndoCheckpoint('updateFloorOpeningCfd');
        const model = getModelById(modelId);
        if (!model) return;
        if (!model.cfd) model.cfd = getDefaultCfd('floor');
        if (!model.cfd.openings) model.cfd.openings = [];
        let opening = model.cfd.openings.find(o => o.patchId === patchId);
        if (!opening) {
            opening = { patchId, type: 'wall' };
            model.cfd.openings.push(opening);
        }
        const parts = field.split('.');
        let target = opening;
        for (let i = 0; i < parts.length - 1; i++) {
            if (!target[parts[i]]) target[parts[i]] = {};
            target = target[parts[i]];
        }
        target[parts[parts.length - 1]] = value;
        updateUI();
        logOperation('updateFloorOpeningCfd', `${field}=${value}`, { modelId, patchId, field, value }, 'modify');
    };
```

- [ ] **Step 8.3: Replace `porosity` with `perforated` in `buildCfdExportJson`**

Three concrete emit sites inside the baffle/floor branches of the model loop (around lines 27345, 27360, 27383). Each drops `porosity` in favour of a `perforated` sub-object. Leave `rack.inletDoor.porosity` / `rack.outletDoor.porosity` (rack-door porosity, different concept) and `floor.defaultBCPorosity` (remaining-area flow BC) untouched.

Add this small helper once near the top of `buildCfdExportJson`:

```js
        const DEFAULT_PERF = { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };
        const toPerf = (src) => ({
            shape: (src && src.shape) || DEFAULT_PERF.shape,
            holeDiameterMm: Number.isFinite(src && src.holeDiameterMm) ? src.holeDiameterMm : DEFAULT_PERF.holeDiameterMm,
            openAreaPct: Number.isFinite(src && src.openAreaPct) ? src.openAreaPct : DEFAULT_PERF.openAreaPct,
        });
```

**Site 1 — baffle per-patch map (~L27345 – L27354).** Current:

```js
                    const pp = (cfd.patchPhysics || {})[patch.id] || { baffleType: cfd.baffleType || 'wall', porosity: cfd.porosity || 0 };
                    ...
                    bafflePatchPhysicsMap[solidName] = {
                        baffleType: pp.baffleType || 'wall',
                        porosity: pp.porosity || 0,
                        seedConfig: cloneSeedConfig(pp.seedConfig)
                    };
```

Replace with:

```js
                    const pp = (cfd.patchPhysics || {})[patch.id] || { baffleType: cfd.baffleType || 'wall' };
                    ...
                    const entry = {
                        baffleType: pp.baffleType || 'wall',
                        seedConfig: cloneSeedConfig(pp.seedConfig)
                    };
                    if (entry.baffleType === 'perforated') {
                        entry.perforated = toPerf(pp.perforated || cfd.perforated);
                    }
                    bafflePatchPhysicsMap[solidName] = entry;
```

**Site 2 — baffle model physics (~L27359 – L27362).** Current:

```js
                    physics: {
                        baffleType: cfd.baffleType || 'wall',
                        porosity: cfd.porosity || 0
                    },
```

Replace with:

```js
                    physics: (() => {
                        const phys = { baffleType: cfd.baffleType || 'wall' };
                        if (phys.baffleType === 'perforated') phys.perforated = toPerf(cfd.perforated);
                        return phys;
                    })(),
```

**Site 3 — floor opening (~L27383).** Current:

```js
                        physics: { type: op.type || 'wall', porosity: op.porosity || 0.0 },
```

Replace with:

```js
                        physics: (() => {
                            const phys = { type: op.type || 'wall' };
                            if (phys.type === 'perforated') phys.perforated = toPerf(op.perforated);
                            return phys;
                        })(),
```

- [ ] **Step 8.4: Run structural test — final one should pass**

```bash
python3 STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
```

Expected: all 4 tests PASS.

- [ ] **Step 8.5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(frontend): dotted-key handlers + cfdrun export emits perforated"
```

---

## Task 9: Scrub old `porosity` from saved `.cfdproj` files

**Files:**
- Create: `STLGeneratorForOpenFoam/scripts/scrub_perforated_porosity.py`
- Modify: 14 files under `STLGeneratorForOpenFoam/projects/*.cfdproj`

- [ ] **Step 9.1: Write the scrub script**

Create `STLGeneratorForOpenFoam/scripts/scrub_perforated_porosity.py`:

```python
"""Scrub legacy top-level 'porosity' fields from baffle/floor/patch
cfdproj entries and replace with the default perforated block where the
parent toggle says 'perforated'. Idempotent."""
import json
import pathlib
import sys

DEFAULT = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}


def scrub(root: dict) -> int:
    """Mutate root in place, return number of fields changed."""
    changes = 0
    for model in root.get("models", []):
        cfd = model.get("cfd")
        if not isinstance(cfd, dict):
            continue
        if model.get("kind") == "baffle":
            if "porosity" in cfd:
                cfd.pop("porosity")
                changes += 1
            if cfd.get("baffleType") == "perforated" and "perforated" not in cfd:
                cfd["perforated"] = dict(DEFAULT)
                changes += 1
            for pp in (cfd.get("patchPhysics") or {}).values():
                if "porosity" in pp:
                    pp.pop("porosity")
                    changes += 1
                if pp.get("baffleType") == "perforated" and "perforated" not in pp:
                    pp["perforated"] = dict(DEFAULT)
                    changes += 1
        if model.get("kind") == "floor":
            for op in cfd.get("openings", []) or []:
                if "porosity" in op:
                    op.pop("porosity")
                    changes += 1
                if op.get("type") == "perforated" and "perforated" not in op:
                    op["perforated"] = dict(DEFAULT)
                    changes += 1
    return changes


def main() -> int:
    proj_dir = pathlib.Path("STLGeneratorForOpenFoam/projects")
    total = 0
    for fp in sorted(proj_dir.glob("*.cfdproj")):
        doc = json.loads(fp.read_text())
        root = doc.get("state", doc)
        n = scrub(root)
        if n:
            fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
            print(f"{fp.name}: {n} fields changed")
            total += n
    print(f"total fields changed: {total}")
    return 0 if total >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9.2: Dry-run the scrub on a single file to verify**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
# Pick Base_leakage as a sanity check.
python3 -c "
import json
doc = json.load(open('STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj'))
root = doc.get('state', doc)
bcount = sum(1 for m in root.get('models', []) if m.get('kind') == 'baffle')
with_por = sum(1 for m in root.get('models', [])
               if m.get('kind') == 'baffle' and 'porosity' in (m.get('cfd') or {}))
print(f'baffles={bcount}, with legacy porosity={with_por}')
"
```

- [ ] **Step 9.3: Run the scrub**

```bash
python3 STLGeneratorForOpenFoam/scripts/scrub_perforated_porosity.py
```

Expected output lists each cfdproj file touched with a count > 0 for files that had old porosity.

- [ ] **Step 9.4: Verify the scrub is idempotent**

```bash
python3 STLGeneratorForOpenFoam/scripts/scrub_perforated_porosity.py
```

Expected: second run prints `total fields changed: 0`.

- [ ] **Step 9.5: Commit**

```bash
git add STLGeneratorForOpenFoam/scripts/scrub_perforated_porosity.py STLGeneratorForOpenFoam/projects/*.cfdproj
git commit -m "chore(projects): scrub legacy porosity, seed default perforated block"
```

---

## Task 10: Full-file syntax check + submodule bump + push

**Files:**
- Modify: root `dcdtuni` (submodule pointer bump only)

- [ ] **Step 10.1: Verify the HTML is still syntactically valid JS**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
sed -n '848,28094p' STLGeneratorForOpenFoam/web/STL_generate.html > /tmp/stlgen.js
node --check /tmp/stlgen.js && echo OK
```

Expected: `OK`. If not, fix errors before continuing.

- [ ] **Step 10.2: Run the full backend test suite**

```bash
conda run -n dcdtuni pytest tests/test_perforated.py -v
```

Expected: all tests pass.

- [ ] **Step 10.3: Run the full frontend structural test**

```bash
python3 STLGeneratorForOpenFoam/scripts/test_perforated_frontend.py
```

Expected: all 4 tests pass.

- [ ] **Step 10.4: Push the submodule**

```bash
cd STLGeneratorForOpenFoam
git push origin main
```

- [ ] **Step 10.5: Bump the submodule pointer in the parent repo**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam
git commit -m "chore(submodule): bump STLGen — unified perforated-plate inspector"
git push origin main
```

---

## Smoke checklist (manual, post-push)

Only items that can't be covered by the automated tests above:

- [ ] Open `Base_leakage.cfdproj` in a real browser. Select a baffle model with `baffleType=perforated`. Confirm the right panel shows the 🌀 穿孔板设置 block with three inputs + the K estimate updating live when any input changes.
- [ ] Select a baffle patch with `baffleType=perforated`. Same block should appear (same three inputs, same live K).
- [ ] Open `STDCT.cfdproj` (floor openings). Select a floor patch with `type=perforated`. Same block.
- [ ] Change the shape dropdown circle → rect → diamond on any one of the above. Confirm K rises each step (1.00 → 1.10 → 1.15 factor in φ).
- [ ] Save the project, reopen, confirm the three values round-trip without resetting.
- [ ] Run a small mesh + solve end-to-end; check `<case>/0/p_rgh` for a `porousBafflePressure` entry whose `I` matches the Idelchik K for whichever baffle you inspected (σ=0.64 → I≈1.499).
