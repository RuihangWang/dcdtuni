# Turbulence Seed via Physical Characteristic Velocity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix k-ε internalField seed pollution and per-patch BC errors caused by fan-server tiny-patch artificial face velocity (~100 m/s) by introducing physical characteristic velocity derivation, intensity rescaling, ACU-only seeding, and literature-backed clamp bounds.

**Architecture:** All changes in `fields.py` (turbulence computation + field writing) and `bridge_bc.py` (BCSpec gains `physical_flow_area`), plus `stlgen_runner.py` (computes `physical_flow_area` when building bridge registry). New tests in existing `test_turbulence_bc.py`. Four tasks executed sequentially: clamp constants → BCSpec extension → physical velocity + intensity rescale → ACU-only seed.

**Tech Stack:** Python 3.10, Pydantic (BCSpec), pytest, loguru

**Spec:** `docs/superpowers/specs/2026-04-19-turbulence-seed-physical-velocity-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/backend/aircooling/runner/fields.py` | Modify | Physical clamp constants, `_compute_physical_velocity`, intensity rescale in `_write_turbulence_field`, `_compute_internal_field_seed` |
| `src/backend/aircooling/runner/bridge_bc.py` | Modify | Add `physical_flow_area` field to `BCSpec` |
| `src/backend/aircooling/runner/stlgen_runner.py` | Modify | Compute `physical_flow_area` for fan_outlet/fan_inlet BCSpec entries |
| `src/backend/aircooling/runner/tests/test_turbulence_bc.py` | Modify | All new tests (extends existing file) |

---

### Task 1: Physical-range clamp constants (spec T4)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:69-74` (class constants) and `src/backend/aircooling/runner/fields.py:163-164` (clamp logic)
- Test: `src/backend/aircooling/runner/tests/test_turbulence_bc.py`

- [ ] **Step 1: Write failing tests for clamp ceiling and floor**

Add to `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
# --- Task 1: Physical-range clamps (spec T4) ---

def test_k_clamp_ceiling_at_5(tmp_path):
    """k values above K_MAX_PHYSICAL (5.0) are clamped down."""
    # U=100 m/s through tiny area → raw k = 1.5·(0.10·100)² = 150
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, _I, _L = result
    assert k <= 5.0, f"k={k} should be clamped to K_MAX_PHYSICAL=5.0"


def test_k_floor_at_0_1(tmp_path):
    """k values below K_MIN_PHYSICAL (0.1) are raised to floor."""
    # Very low flow → raw k tiny
    fg = _make_field_gen(
        tmp_path, "acu_slow", bc_type="acu_supply",
        flow_rate=0.001, area=2.0, perimeter=6.0,
    )
    result = fg._compute_turbulence_bc("acu_slow")
    assert result is not None
    k, eps, _I, _L = result
    assert k >= 0.1, f"k={k} should be raised to K_MIN_PHYSICAL=0.1"


def test_epsilon_clamp_ceiling_at_100(tmp_path):
    """epsilon values above EPS_MAX_PHYSICAL (100) are clamped down."""
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    _k, eps, _I, _L = result
    assert eps <= 100.0, f"eps={eps} should be clamped to EPS_MAX_PHYSICAL=100.0"


def test_epsilon_floor_at_0_01(tmp_path):
    """epsilon values below EPS_MIN_PHYSICAL (0.01) are raised to floor."""
    fg = _make_field_gen(
        tmp_path, "acu_slow", bc_type="acu_supply",
        flow_rate=0.001, area=2.0, perimeter=6.0,
    )
    result = fg._compute_turbulence_bc("acu_slow")
    assert result is not None
    _k, eps, _I, _L = result
    assert eps >= 0.01, f"eps={eps} should be raised to EPS_MIN_PHYSICAL=0.01"


def test_k_in_range_passes_through(tmp_path):
    """k values within [K_MIN, K_MAX] are not modified."""
    # U=3.2354 m/s → k = 1.5·(0.10·3.2354)² ≈ 0.157 — within range
    fg = _make_field_gen(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.1553, area=0.048, perimeter=1.04,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, _eps, _I, _L = result
    U = 0.1553 / 0.048
    expected_k = 1.5 * (0.10 * U) ** 2
    assert k == pytest.approx(expected_k, rel=1e-3)


def test_clamp_ceiling_logs_warning(tmp_path):
    """Hitting the k ceiling emits a WARNING log."""
    messages: list = []
    sink_id = loguru_logger.add(lambda msg: messages.append(str(msg)), level="WARNING")
    try:
        fg = _make_field_gen(
            tmp_path, "fan_p0", bc_type="fan_outlet",
            flow_rate=0.15, area=0.0015, perimeter=0.16,
        )
        fg._compute_turbulence_bc("fan_p0")
    finally:
        loguru_logger.remove(sink_id)
    assert any("clamped" in m.lower() for m in messages), (
        f"Expected 'clamped' warning in: {messages!r}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_k_clamp_ceiling_at_5 src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_k_floor_at_0_1 -v`
Expected: FAIL — current ceiling is 100, no floor exists

- [ ] **Step 3: Implement clamp constants and updated logic**

In `src/backend/aircooling/runner/fields.py`, add constants after `C_MU = 0.09` (line 74):

```python
    C_MU = 0.09                          # k-epsilon model constant

    # Physical-range clamps (spec 2026-04-19, literature-backed):
    # Ceiling: Dogan 2022 DC internalField = 1.81 m²/s²; 5.0 = 3× safety.
    # Floor: OpenFOAM hotRoom tutorial canonical lower bound.
    K_MIN_PHYSICAL   = 0.1    # m²/s²
    K_MAX_PHYSICAL   = 5.0    # m²/s²
    EPS_MIN_PHYSICAL = 0.01   # m²/s³
    EPS_MAX_PHYSICAL = 100.0  # m²/s³
```

Replace the clamp block at the end of `_compute_turbulence_bc` (currently lines 163-164):

Old:
```python
        k = min(k, 100.0)
        epsilon = min(epsilon, 1e5)
```

New:
```python
        k_raw, eps_raw = k, epsilon
        k = max(min(k, self.K_MAX_PHYSICAL), self.K_MIN_PHYSICAL)
        epsilon = max(min(epsilon, self.EPS_MAX_PHYSICAL), self.EPS_MIN_PHYSICAL)

        if k_raw > self.K_MAX_PHYSICAL:
            logger.warning(
                f"Patch {patch_name}: computed k={k_raw:.2f} > {self.K_MAX_PHYSICAL} — "
                f"clamped; check U_phys derivation for bc_type={bc_type!r}"
            )
        elif k_raw < self.K_MIN_PHYSICAL:
            logger.info(
                f"Patch {patch_name}: computed k={k_raw:.4f} < {self.K_MIN_PHYSICAL} — "
                f"raised to floor"
            )
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v`
Expected: all PASS (including pre-existing tests — note test_compute_turbulence_bc_fan_outlet_uses_10_percent and test_compute_turbulence_bc_acu_supply_uses_5_percent may need updating if the computed k now hits the floor/ceiling; check and adjust expected values)

**Important:** Pre-existing `test_compute_turbulence_bc_acu_supply_uses_5_percent` expects `k=0.375` for U=10 m/s, I=0.05. With the new floor at 0.1 this still passes (0.375 > 0.1). Pre-existing `test_compute_turbulence_bc_fan_outlet_uses_10_percent` expects `k=0.157` — still passes (0.157 > 0.1). Good — no existing test breakage.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/aircooling/runner/tests/test_turbulence_bc.py
git commit -m "feat(turbulence): physical-range clamps k∈[0.1,5] ε∈[0.01,100] (T4)

Replace non-physical clamp ceiling (k≤100, ε≤1e5) with literature-backed
bounds: K_MAX=5 (3× Dogan 2022 DC internalField 1.81), K_MIN=0.1
(hotRoom canonical), EPS_MAX=100, EPS_MIN=0.01. Log WARNING on ceiling
hit, INFO on floor raise."
```

---

### Task 2: Add `physical_flow_area` to BCSpec and compute in stlgen_runner

**Files:**
- Modify: `src/backend/aircooling/runner/bridge_bc.py:44-52` (BCSpec)
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:2864-2942` (fan_outlet/fan_inlet BCSpec creation)
- Test: `src/backend/aircooling/runner/tests/test_turbulence_bc.py`

- [ ] **Step 1: Write failing test for BCSpec.physical_flow_area**

Add to `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
# --- Task 2: physical_flow_area in BCSpec ---

from dcdtuni.runner.bridge_bc import BCSpec


def test_bcspec_accepts_physical_flow_area():
    """BCSpec should accept an optional physical_flow_area field."""
    spec = BCSpec(bc_type="fan_outlet", flow_rate=0.15, physical_flow_area=0.019)
    assert spec.physical_flow_area == pytest.approx(0.019)


def test_bcspec_physical_flow_area_defaults_none():
    """When not set, physical_flow_area defaults to None."""
    spec = BCSpec(bc_type="fan_outlet", flow_rate=0.15)
    assert spec.physical_flow_area is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_bcspec_accepts_physical_flow_area -v`
Expected: FAIL — `physical_flow_area` field does not exist on BCSpec

- [ ] **Step 3: Add physical_flow_area to BCSpec**

In `src/backend/aircooling/runner/bridge_bc.py`, add after the `heat_flux` field (line 52):

```python
    heat_flux: Optional[float] = None     # W/m²; used for heat_source_flux BC
    physical_flow_area: Optional[float] = None  # m²; real server-face area for U_phys derivation (fan_outlet/fan_inlet only)
```

- [ ] **Step 3b: Forward physical_flow_area in FieldGenerator._collect_imported_bc_data**

In `src/backend/aircooling/runner/fields.py`, method `_collect_imported_bc_data` (line ~205-213), add `physical_flow_area` to the dict:

```python
                imported_bc[patch_name] = {
                    "bc_type": spec.bc_type,
                    "temperature": spec.temperature,
                    "flow_rate": spec.flow_rate,
                    "power": spec.power,
                    "sigma": spec.sigma,
                    "perforated": spec.perforated,
                    "heat_flux": spec.heat_flux,
                    "physical_flow_area": getattr(spec, "physical_flow_area", None),
                }
```

- [ ] **Step 3c: Update _FakeBridgeRegistry in test file to pass physical_flow_area**

In `src/backend/aircooling/runner/tests/test_turbulence_bc.py`, update the `_FakeBridgeRegistry.__init__` SimpleNamespace to include `physical_flow_area`:

```python
            yield patch, SimpleNamespace(
                bc_type=spec.get("bc_type"),
                temperature=spec.get("temperature"),
                flow_rate=spec.get("flow_rate"),
                power=spec.get("power"),
                sigma=spec.get("sigma"),
                perforated=spec.get("perforated"),
                heat_flux=spec.get("heat_flux"),
                physical_flow_area=spec.get("physical_flow_area"),
            )
```

- [ ] **Step 4: Run BCSpec tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_bcspec_accepts_physical_flow_area src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_bcspec_physical_flow_area_defaults_none -v`
Expected: PASS

- [ ] **Step 5: Compute physical_flow_area in stlgen_runner**

In `src/backend/aircooling/runner/stlgen_runner.py`, find the two places where `BCSpec(bc_type="fan_outlet", ...)` and `BCSpec(bc_type="fan_inlet", ...)` are created. There are two code paths:

**Path A — new faces format (line ~2864-2868):** inside the `if faces:` block. Before the BCSpec creation, compute the physical area from the rack+server geometry that's available in-scope:

```python
                        if srv_type == "fanServer":
                            # Compute physical server-face area for turbulence BC
                            # (Dogan 2022 rack-front convention: slot_width × span × unit_height)
                            _rack_geom = rack.get("geometry", {})
                            _rack_W = _rack_geom.get("size", {}).get("W", 0.6)
                            _slot_ratio = _rack_geom.get("slotWidthRatio", 0.8)
                            _unit_h = _rack_geom.get("unitHeight", 0.04445)
                            _span = srv.get("spanSlots", 1)
                            _phys_area = _rack_W * _slot_ratio * _span * _unit_h

                            if face_id == "inlet":
                                bc = BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P,
                                            physical_flow_area=_phys_area)
                            elif face_id == "outlet":
                                T_outlet = 25.0 + P / (1.2 * 1006 * Q) if Q > 0 else 35.0
                                bc = BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P,
                                            temperature=T_outlet, physical_flow_area=_phys_area)
```

**Path B — legacy format (line ~2935-2942):** the `else` block with `inletSolidName`/`outletSolidName`. Same computation, same _phys_area derivation:

```python
                        _rack_geom = rack.get("geometry", {})
                        _rack_W = _rack_geom.get("size", {}).get("W", 0.6)
                        _slot_ratio = _rack_geom.get("slotWidthRatio", 0.8)
                        _unit_h = _rack_geom.get("unitHeight", 0.04445)
                        _span = srv.get("spanSlots", 1)
                        _phys_area = _rack_W * _slot_ratio * _span * _unit_h

                        registry.register(inlet, BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P,
                                                         physical_flow_area=_phys_area))
                        registry.register(outlet, BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P,
                                                         temperature=T_outlet, physical_flow_area=_phys_area))
```

- [ ] **Step 6: Run full test suite to verify no breakage**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/ -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/backend/aircooling/runner/bridge_bc.py src/backend/aircooling/runner/stlgen_runner.py src/backend/aircooling/runner/tests/test_turbulence_bc.py
git commit -m "feat(bridge_bc): add physical_flow_area to BCSpec for fan-server U_phys (T1 foundation)

BCSpec gains optional physical_flow_area field (m²). stlgen_runner
computes it as rack.W × slotWidthRatio × spanSlots × unitHeight
(Dogan 2022 rack-front convention) when creating fan_outlet/fan_inlet
entries. FieldGenerator will use this in the next commit to derive
U_phys and rescale turbulence intensity."
```

---

### Task 3: Physical velocity derivation + intensity rescaling (spec T1 + T2)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:114-171` (`_compute_turbulence_bc`) and `src/backend/aircooling/runner/fields.py:1220-1233` (`_write_turbulence_field` intensity line)
- Test: `src/backend/aircooling/runner/tests/test_turbulence_bc.py`

- [ ] **Step 1: Write failing tests for physical velocity and intensity rescaling**

Add to `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
# --- Task 3: Physical velocity + intensity rescaling (spec T1 + T2) ---

def _make_field_gen_with_phys_area(
    tmp_path: Path,
    patch_name: str,
    bc_type: str,
    flow_rate: float,
    area: float,
    perimeter: float,
    physical_flow_area: float = None,
) -> FieldGenerator:
    """Like _make_field_gen but also sets physical_flow_area on the BCSpec."""
    geom = {patch_name: PatchGeometry(area=area, perimeter=perimeter, bbox_dims=(0.12, 0.40, 0.0))}
    spec_dict = {"bc_type": bc_type, "flow_rate": flow_rate}
    if physical_flow_area is not None:
        spec_dict["physical_flow_area"] = physical_flow_area
    reg = _FakeBridgeRegistry({patch_name: spec_dict})
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={},
        room_config={},
        patch_geometry=geom,
        bridge_registry=reg,
    )
    fg._collect_data()
    return fg


def test_fan_outlet_uses_physical_velocity_for_k(tmp_path):
    """fan_outlet with physical_flow_area: k is computed from U_phys, not U_face.

    Setup: flow_rate=0.15, STL area=0.0015 (→ U_face=100), phys_area=0.019 (→ U_phys≈7.9)
    Expected k = 1.5·(0.10·7.9)² ≈ 0.94 m²/s² (NOT 150 from U_face)
    """
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=0.019,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, _I, _L = result
    U_phys = 0.15 / 0.019
    expected_k = 1.5 * (0.10 * U_phys) ** 2
    assert k == pytest.approx(expected_k, rel=1e-2), (
        f"k={k} should use U_phys={U_phys:.2f}, not U_face=100"
    )


def test_fan_outlet_without_phys_area_falls_back(tmp_path):
    """fan_outlet WITHOUT physical_flow_area: U_phys = min(U_face, 5.0)."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=None,
    )
    result = fg._compute_turbulence_bc("fan_p0")
    assert result is not None
    k, eps, _I, _L = result
    U_phys_fallback = 5.0  # min(100, 5)
    expected_k = 1.5 * (0.10 * U_phys_fallback) ** 2  # 0.375
    assert k == pytest.approx(expected_k, rel=1e-2)


def test_acu_supply_unchanged_by_physical_velocity(tmp_path):
    """acu_supply has no physical_flow_area → U_phys = U_face (unchanged)."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "acu_top", bc_type="acu_supply",
        flow_rate=4.0, area=2.4, perimeter=7.2,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    k, eps, I, _L = result
    U_face = 4.0 / 2.4
    expected_k = 1.5 * (I * U_face) ** 2
    # Must be within [K_MIN, K_MAX] range; 0.0417 → floored to 0.1
    assert k == pytest.approx(max(expected_k, 0.1), rel=1e-2)


def test_intensity_rescaled_in_k_field_output(tmp_path):
    """The k field file for fan_outlet writes rescaled intensity, not physical I."""
    fg = _make_field_gen_with_phys_area(
        tmp_path, "fan_p0", bc_type="fan_outlet",
        flow_rate=0.15, area=0.0015, perimeter=0.16,
        physical_flow_area=0.019,
    )

    patches = [("fan_p0", "patch"), ("wall_1", "wall")]
    k_path = tmp_path / "k"
    fg._write_turbulence_field(
        k_path, patches, internal_value=0.1,
        field_class="volScalarField", dimensions="[0 2 -2 0 0 0 0]",
        field_name="k",
    )
    k_text = k_path.read_text()

    # Extract the intensity value written for fan_p0
    import re
    m = re.search(r"intensity\s+([0-9.e+-]+)", k_text)
    assert m is not None, "intensity line not found in k field output"
    written_I = float(m.group(1))

    # Expected: I_eff = I_physical × U_phys / U_face = 0.10 × (0.15/0.019) / (0.15/0.0015)
    U_phys = 0.15 / 0.019
    U_face = 0.15 / 0.0015
    I_eff = 0.10 * U_phys / U_face
    assert written_I == pytest.approx(I_eff, rel=1e-2), (
        f"Written intensity {written_I} should be rescaled I_eff={I_eff:.6f}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_fan_outlet_uses_physical_velocity_for_k src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_intensity_rescaled_in_k_field_output -v`
Expected: FAIL — current code uses U_face for k computation and writes I=0.10 directly

- [ ] **Step 3: Implement physical velocity in _compute_turbulence_bc**

In `src/backend/aircooling/runner/fields.py`, modify `_compute_turbulence_bc`. After computing `U = flow_rate / geom.area` (line 137), add physical velocity derivation:

```python
        U = flow_rate / geom.area  # CFD face velocity (may be artificial for fan-server)

        # Physical characteristic velocity: use real server-face area if available
        # (Dogan 2022 rack-front convention). For ACU / unmapped, U_phys = U_face.
        bc_type = bc_info.get("bc_type", "")
        phys_area = bc_info.get("physical_flow_area")
        if bc_type in ("fan_outlet", "fan_inlet") and phys_area and phys_area > 1e-8:
            U_phys = flow_rate / phys_area
        elif bc_type in ("fan_outlet", "fan_inlet"):
            U_phys = min(U, 5.0)
            logger.warning(
                f"Patch {patch_name!r}: fan bc_type={bc_type!r} missing physical_flow_area; "
                f"fallback U_phys={U_phys:.2f} m/s (capped at 5.0)"
            )
        else:
            U_phys = U  # ACU, leakage, etc.: STL face = physical face
```

Then change the k computation to use `U_phys` instead of `U`:

Old:
```python
        k = 1.5 * (I * U) ** 2
```

New:
```python
        k = 1.5 * (I * U_phys) ** 2
```

Also store both velocities in the return tuple for T2 intensity rescaling. Change the return signature from `(k, epsilon, I, L)` to `(k, epsilon, I, L, U_phys, U)`:

```python
        return (k, epsilon, I, length_scale, U_phys, U)
```

Update the docstring return type accordingly:
```python
    ) -> Optional[Tuple[float, float, float, float, float, float]]:
        """Compute (k, ε, I, L, U_phys, U_face) for an inlet patch.
```

- [ ] **Step 4: Update _write_turbulence_field to use rescaled intensity**

In `_write_turbulence_field`, the current code at line 1222 unpacks `result`:

Old:
```python
                if result:
                    k_val, eps_val, I, L = result
```

New:
```python
                if result:
                    k_val, eps_val, I_phys, L, U_phys, U_face = result
                    # Rescale intensity so OpenFOAM's dynamic BC produces physical k:
                    # k_runtime = 1.5·(I_eff·U_face)² = 1.5·(I_phys·U_phys)²
                    I_eff = I_phys * U_phys / U_face if U_face > 1e-6 else I_phys
```

Then change the intensity line in the k BC block:

Old:
```python
                                      f"        intensity       {I:.6g};",
```

New:
```python
                                      f"        intensity       {I_eff:.6g};",
```

- [ ] **Step 5: Fix all callers that unpack _compute_turbulence_bc's return tuple**

Search for all `result[0]`, `result[1]`, `k_val, eps_val, I, L = result` patterns. The following locations need updating:

1. `_write_turbulence_field` line ~1211 (domain outlet branch):
   Old: `val = result[0] if field_name == "k" else result[1]`
   New: same — `result[0]` and `result[1]` are still k and eps at indices 0, 1. No change needed.

2. `_compute_average_inlet_turbulence` line ~1175 (will be replaced in Task 4):
   Old: `k_values.append(result[0])` / `eps_values.append(result[1])`
   New: same indices. No change needed.

- [ ] **Step 6: Run all tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v`
Expected: all PASS

**Note:** Pre-existing `test_write_turbulence_field_renders_native_bc_types` checks for `"intensity       0.1"` in the k output. After this change, the fan_outlet intensity will be rescaled. The test uses `area=0.048` (same as physical face in test fixture) → `U_phys = U_face` → `I_eff = I_phys = 0.10`. So the assertion `"intensity       0.1"` still matches. No change needed.

- [ ] **Step 7: Commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/aircooling/runner/tests/test_turbulence_bc.py
git commit -m "feat(turbulence): physical velocity derivation + intensity rescaling (T1+T2)

_compute_turbulence_bc now uses U_phys (from BCSpec.physical_flow_area)
instead of U_face for k computation. _write_turbulence_field rescales
the intensity parameter: I_eff = I_phys × U_phys/U_face, so OpenFOAM's
dynamic turbulentIntensityKineticEnergyInlet BC produces physical k at
runtime despite the artificial face velocity on tiny fan-server patches.
Fan-server k drops from ~150 (clamped to 5) to ~0.96 m²/s²."
```

---

### Task 4: ACU intensity bump + ACU-only internalField seed (spec T3)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:69-72` (TURBULENCE_INTENSITY_BY_BC_TYPE), `src/backend/aircooling/runner/fields.py:1168-1183` (rename + rewrite), `src/backend/aircooling/runner/fields.py:373` (call site)
- Test: `src/backend/aircooling/runner/tests/test_turbulence_bc.py`

- [ ] **Step 1: Write failing tests for ACU intensity bump and ACU-only seed**

Add to `src/backend/aircooling/runner/tests/test_turbulence_bc.py`:

```python
# --- Task 4: ACU intensity bump + ACU-only seed (spec T3) ---

def test_acu_supply_uses_10_percent_intensity(tmp_path):
    """acu_supply bc_type should now use I=0.10 (was 0.05)."""
    fg = _make_field_gen(
        tmp_path, "acu_top", bc_type="acu_supply",
        flow_rate=4.0, area=2.4, perimeter=7.2,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    _k, _eps, I, _L = result[:4]
    assert I == pytest.approx(0.10, rel=1e-9), f"acu_supply I should be 0.10, got {I}"


def _make_multi_patch_field_gen(tmp_path, patches_config):
    """Build a FieldGenerator with multiple patches in its bridge registry."""
    geom = {}
    reg_entries = {}
    for cfg in patches_config:
        name = cfg["name"]
        geom[name] = PatchGeometry(
            area=cfg["area"], perimeter=cfg.get("perimeter", 4.0),
            bbox_dims=cfg.get("bbox_dims", (0.12, 0.40, 0.0)),
        )
        entry = {"bc_type": cfg["bc_type"], "flow_rate": cfg["flow_rate"]}
        if "physical_flow_area" in cfg:
            entry["physical_flow_area"] = cfg["physical_flow_area"]
        reg_entries[name] = entry
    reg = _FakeBridgeRegistry(reg_entries)
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={},
        room_config={},
        patch_geometry=geom,
        bridge_registry=reg,
    )
    fg._collect_data()
    return fg


def test_internal_field_seed_excludes_fan_server(tmp_path):
    """Seed should come from ACU patches only, NOT fan-server."""
    fg = _make_multi_patch_field_gen(tmp_path, [
        {"name": "acu_supply_1", "bc_type": "acu_supply", "flow_rate": 4.0, "area": 2.4, "perimeter": 7.2},
        {"name": "fan_p0", "bc_type": "fan_outlet", "flow_rate": 0.15, "area": 0.0015, "perimeter": 0.16,
         "physical_flow_area": 0.019},
    ])
    patches = [("acu_supply_1", "patch"), ("fan_p0", "patch")]
    k_seed, eps_seed = fg._compute_internal_field_seed(patches)
    # If fan_server is excluded, seed should be ACU-based (k ≈ 0.04 → floored to 0.1)
    # If fan_server is included, seed would be avg of ACU's ~0.1 and fan's ~0.94 → ~0.5
    assert k_seed == pytest.approx(0.1, rel=0.5), (
        f"k_seed={k_seed} should be ACU-only (≈0.1, not polluted by fan k≈0.94)"
    )


def test_internal_field_seed_no_acu_uses_hotroom_fallback(tmp_path):
    """No ACU patches → fall back to hotRoom canonical (k=0.1, eps=0.01)."""
    fg = _make_multi_patch_field_gen(tmp_path, [
        {"name": "fan_p0", "bc_type": "fan_outlet", "flow_rate": 0.15, "area": 0.0015, "perimeter": 0.16,
         "physical_flow_area": 0.019},
    ])
    patches = [("fan_p0", "patch")]
    k_seed, eps_seed = fg._compute_internal_field_seed(patches)
    assert k_seed == pytest.approx(0.1, rel=1e-6)
    assert eps_seed == pytest.approx(0.01, rel=1e-6)


def test_internal_field_seed_respects_floor(tmp_path):
    """Seed never drops below K_MIN_PHYSICAL / EPS_MIN_PHYSICAL."""
    fg = _make_multi_patch_field_gen(tmp_path, [
        {"name": "acu_slow", "bc_type": "acu_supply", "flow_rate": 0.001, "area": 2.0, "perimeter": 6.0},
    ])
    patches = [("acu_slow", "patch")]
    k_seed, eps_seed = fg._compute_internal_field_seed(patches)
    assert k_seed >= 0.1
    assert eps_seed >= 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_acu_supply_uses_10_percent_intensity src/backend/aircooling/runner/tests/test_turbulence_bc.py::test_internal_field_seed_excludes_fan_server -v`
Expected: FAIL — acu_supply currently uses I=0.05; `_compute_internal_field_seed` doesn't exist yet

- [ ] **Step 3: Bump ACU intensity from 0.05 to 0.10**

In `src/backend/aircooling/runner/fields.py`, change `TURBULENCE_INTENSITY_BY_BC_TYPE`:

```python
    TURBULENCE_INTENSITY_BY_BC_TYPE = {
        "acu_supply": 0.10,   # post-fan CRAH supply (Alkharabsheh Flovent default; Dogan 2022 uses 0.50)
        "fan_outlet": 0.10,   # server-fan exhaust — convention import (no DC measurement; Erden & Khalifa 2019)
    }
```

- [ ] **Step 4: Replace _compute_average_inlet_turbulence with _compute_internal_field_seed**

In `src/backend/aircooling/runner/fields.py`, replace the method at line 1168:

Old:
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

New:
```python
    def _compute_internal_field_seed(self, patches: List[Tuple[str, str]]) -> Tuple[float, float]:
        """Compute internalField k and epsilon from ACU supply patches only.

        Fan-server patches are domain-internal circulation and must not
        set the domain-average bulk seed (CFD-Wiki "free-stream BC",
        Dogan 2022 uniform internalField, every commercial tool surveyed).
        Fallback: OpenFOAM hotRoom canonical (k=0.1, eps=0.01).
        """
        k_values = []
        eps_values = []
        for patch_name, _ in patches:
            bc_type = self._imported_bc_data.get(patch_name, {}).get("bc_type", "")
            if bc_type != "acu_supply":
                continue
            result = self._compute_turbulence_bc(patch_name)
            if result:
                k_values.append(result[0])
                eps_values.append(result[1])
        if k_values:
            k_avg = max(sum(k_values) / len(k_values), self.K_MIN_PHYSICAL)
            eps_avg = max(sum(eps_values) / len(eps_values), self.EPS_MIN_PHYSICAL)
            logger.info(f"InternalField seed: k={k_avg:.4f}, eps={eps_avg:.4f} "
                        f"from {len(k_values)} ACU supply patches")
            return k_avg, eps_avg
        logger.info("No ACU supply patches — using hotRoom canonical seed (k=0.1, eps=0.01)")
        return 0.1, 0.01
```

- [ ] **Step 5: Update the call site**

In `src/backend/aircooling/runner/fields.py`, line ~373:

Old:
```python
        k_avg, eps_avg = self._compute_average_inlet_turbulence(patches)
```

New:
```python
        k_avg, eps_avg = self._compute_internal_field_seed(patches)
```

- [ ] **Step 6: Fix pre-existing test for ACU intensity**

The existing `test_compute_turbulence_bc_acu_supply_uses_5_percent` needs updating since ACU is now 10%:

Old:
```python
def test_compute_turbulence_bc_acu_supply_uses_5_percent(tmp_path):
    """acu_supply bc_type → I=0.05, k = 1.5·(0.05·10)² = 0.375."""
    ...
    assert I == pytest.approx(0.05, rel=1e-9)
    assert k == pytest.approx(0.375, rel=1e-3)
```

New:
```python
def test_compute_turbulence_bc_acu_supply_uses_10_percent(tmp_path):
    """acu_supply bc_type → I=0.10, k = 1.5·(0.10·10)² = 1.5."""
    fg = _make_field_gen(
        tmp_path, "acu_top", bc_type="acu_supply",
        flow_rate=10.0, area=1.0, perimeter=4.0,
    )
    result = fg._compute_turbulence_bc("acu_top")
    assert result is not None
    k, eps, I, L = result[:4]
    assert I == pytest.approx(0.10, rel=1e-9)
    assert k == pytest.approx(1.5, rel=1e-3)
```

Also update `test_write_turbulence_field_renders_native_bc_types` which checks `"intensity       0.1"` — this test passes a fan_outlet with area=0.048 (no physical_flow_area), so U_phys will be min(U_face, 5). U_face = 0.1553/0.048 = 3.235 which is < 5, so U_phys = U_face = 3.235. I_eff = 0.10 × 3.235/3.235 = 0.10. The assertion `"intensity       0.1"` still holds. No change needed for this test.

- [ ] **Step 7: Run all tests**

Run: `conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/aircooling/runner/tests/test_turbulence_bc.py
git commit -m "feat(turbulence): ACU-only internalField seed + I_acu 5→10% (T3)

Replace _compute_average_inlet_turbulence (averaged ALL flow patches
including fan-server k=100 clamp) with _compute_internal_field_seed
(ACU supply patches only). Fan-server patches are domain-internal
circulation, not free-stream; excluding them matches CFD-Wiki,
Dogan 2022, and every commercial tool surveyed.

Also bump acu_supply intensity 0.05→0.10: our ACU is post-fan CRAH
(Alkharabsheh Flovent default 10%; Dogan 2022 uses 50%).

Base_coldAisleIso internalField k: 19.76 → ~0.1 (within hotRoom range)."
```

---

## Self-Review Checklist

**1. Spec coverage:**
- T1 (physical velocity): Task 3 step 3 ✓
- T2 (intensity rescaling): Task 3 step 4 ✓
- T3 (ACU-only seed + floor): Task 4 ✓
- T4 (clamp ceiling + floor): Task 1 ✓
- ACU intensity 5→10%: Task 4 step 3 ✓
- BCSpec.physical_flow_area: Task 2 ✓
- Warning/info logs: Task 1 step 3 + Task 3 step 3 ✓
- Unit tests (all 4 sub-tasks): ✓
- Integration smoke tests: Not in plan scope (manual verification per spec) ✓

**2. Placeholder scan:** No TBD/TODO/placeholders found.

**3. Type consistency:**
- `_compute_turbulence_bc` return type: `(k, eps, I, L)` → `(k, eps, I, L, U_phys, U_face)` — 6-tuple, consistent across Task 3 steps 3-5
- `_compute_internal_field_seed` replaces `_compute_average_inlet_turbulence` — call site updated in Task 4 step 5
- `BCSpec.physical_flow_area: Optional[float]` — used in Task 3 step 3 via `bc_info.get("physical_flow_area")`
- `_FakeBridgeRegistry` in tests passes `physical_flow_area` through `SimpleNamespace` — added in Task 3 step 1 via `_make_field_gen_with_phys_area`
