# Turbulence BC Physical Grounding — Design Spec

**Date:** 2026-04-18
**Author:** Claude (via brainstorming with user)
**Status:** Draft — awaiting user approval

## Background

`buoyantBoussinesqSimpleFoam` + standard `kEpsilon` RAS with the current dcdtuni auto-computed inlet BCs drives Tri_angle's ε to `5.3e6 m²/s³` (bounded by OpenFOAM's `boundedness` limiter, not a physical solution). This is a pseudo-steady state where ν_t collapses to molecular viscosity in the affected region → turbulence model is effectively off → T field is untrustworthy near fan-server outlets.

Root-cause chain (established by subagent research):

1. `fields.py::_compute_turbulence_bc` uses `I = 0.05` (HVAC-typical) for all patches, but fan-server exhaust has blade-wake turbulence with measured `I ≈ 10–25%` (Dantec hot-wire manual; Erden & Khalifa 2019). So `k_inlet` is ~4× too low for fan-servers.
2. Same function uses `D_h = 2·√(A/π)` (circular equivalent) — systematically overestimates `D_h` by ~33% on elongated rectangular slots (e.g. 0.12m × 0.40m fan-server face). With `L = 0.07·D_h`, `ε_inlet` is correspondingly too small.
3. Standard k-ε has a well-known `1/k` singularity in the ε production source term (`C_1ε · (ε/k) · P_k`). When `k_inlet` is small and jet shear creates large `P_k` downstream, ε blows up. Shih et al. (1995) introduced `realizableKE` specifically to remove this singularity via `ε / (k + √(ν·ε))` denominator in the destruction term.

## Goals

Fix inlet turbulence BC handling so that:

1. All three canonical cases (Tri_angle, CMI-China, base_leakage) complete 500 iter with **physical** ε magnitudes:
   - ε_max in last 100 iter stays below `1e5 m²/s³` (was 5.3e6 in broken Tri_angle)
   - ε_avg in last 100 iter stays below `100 m²/s³` for small/medium cases, below `1000` for CMI-China (large dense case)
2. The pathological boundedness pattern goes away: Tri_angle's current `ε_min = -91` / `ε_max = 5.3e6` (unstable saturation) is the failure mode. After fix, bounding log values should look like base_leakage (min ≈ -0.02, max ≈ 700 — minor clipping, physical).
3. ν_t stays above molecular viscosity (ν = 1.5e-5 m²/s) everywhere — no turbulence-model collapse.
4. Refactor is covered by unit tests on `_compute_turbulence_bc` inputs/outputs.

## Non-Goals

- Changing the turbulence model beyond `realizableKE` (no `kOmegaSST` this pass — follow-up).
- Exposing turbulence-intensity knobs to the frontend UI/cfdrun (YAGNI — per-bc_type Python constants are sufficient).
- Changing ACU BC structure (BC for ACU isn't broken; just needs to use the new shared formula).
- Wall function tuning (y+ analysis, `nutUSpaldingWallFunction` etc.).

## Design

### Three coordinated fixes

#### T1 — Physical Grounding of Inlet Values

Replace hardcoded `I = 0.05` with a per-bc_type lookup and replace circular `D_h` with rectangular `D_h = 4A/P`.

**New constants in `fields.py`:**
```python
TURBULENCE_INTENSITY_BY_BC_TYPE = {
    "acu_outlet":  0.05,   # CRAC/CRAH supply — ASHRAE HVAC typical (Cho 2009)
    "fan_outlet":  0.10,   # server-fan exhaust — blade wake (Erden & Khalifa 2019)
    "air_leak":    0.05,   # passive leakage — background HVAC
    "leakage":     0.05,   # alias
}
TURBULENCE_INTENSITY_DEFAULT = 0.05  # unknown/unmapped bc_type falls back to HVAC value
```

**Perimeter computation:**

`_compute_patch_areas` must also return a perimeter per patch. Extract from the STL triangle data: for a flat rectangular patch (which fan-server + ACU outlets are), the perimeter is the length of the boundary polygon (edges that appear in exactly one triangle). Formally: count each edge among all triangles; edges appearing exactly once belong to the boundary.

To keep the change focused, return a new `patch_geometry` dict mapping `patch_name → {area, perimeter, bbox_dims}`. `bbox_dims` is `(dx, dy, dz)` and is available for diagnostics / as fallback when perimeter extraction is ambiguous.

**New formula in `_compute_turbulence_bc`:**

```python
geom = self._patch_geometry.get(patch_name)
if not geom or geom.area <= 1e-8: return None
U = flow_rate / geom.area
I = self.TURBULENCE_INTENSITY_BY_BC_TYPE.get(bc_type, self.TURBULENCE_INTENSITY_DEFAULT)
k = 1.5 * (I * U) ** 2
# Rectangular hydraulic diameter; fall back to circular if perimeter not extractable
if geom.perimeter > 1e-6:
    D_h = 4.0 * geom.area / geom.perimeter
else:
    D_h = 2.0 * math.sqrt(geom.area / math.pi)  # fallback
length_scale = max(0.07 * D_h, 1e-6)
epsilon = C_MU ** 0.75 * k ** 1.5 / length_scale
# Keep existing physical clamps
k = min(k, 100.0)
epsilon = min(epsilon, 1e5)
```

#### T2 — Use OpenFOAM Native Inlet BC Types

Replace the hand-written `fixedValue`/`inletOutlet` blocks for k and ε at flow inlets with OpenFOAM's native `turbulentIntensityKineticEnergyInlet` (for k) and `turbulentMixingLengthDissipationRateInlet` (for ε).

**Benefits:**

- OpenFOAM auto-recomputes k from live inlet velocity each iter (handles flow rate ramp-up / cfdrun changes without regeneration)
- Automatic reverse-flow guard (degrades to `zeroGradient` on backflow) — same semantics as current `inletOutlet` but baked in
- Declarative semantics — reader sees `intensity 0.10` rather than a computed magic number

**Rendered k BC:**

```cpp
{patch_name}
{
    type            turbulentIntensityKineticEnergyInlet;
    intensity       {I};
    value           uniform {k_initial};
}
```

**Rendered ε BC:**

```cpp
{patch_name}
{
    type            turbulentMixingLengthDissipationRateInlet;
    mixingLength    {L};
    value           uniform {eps_initial};
}
```

Domain outlets (`inletOutlet` reverse-flow guards) keep their existing structure since the value there is only active on backflow and is per-patch.

#### T3 — Switch RAS Model to realizableKE

Change the default turbulence model in the `turbulenceProperties` template from `kEpsilon` to `realizableKE`.

- BC types for k, ε, ν_t, y+ are identical to standard k-ε (realizableKE is still an ε-based model) → no BC template changes required.
- Removes `1/k` singularity → more forgiving of mild BC misspecification.
- Adds `~5%` CPU overhead per iter — acceptable.

File: `src/backend/aircooling/third_parties/foam/template/constant/turbulenceProperties.j2` — line 22 `RASModel kEpsilon;` → `realizableKE;`. No other template changes required.

### Data Flow

```text
cfdrun.json
  └── bridge_bc (bc_type per patch)
        │
        ▼
stlgen_runner._compute_patch_geometry()
  returns {patch_name → {area, perimeter, bbox_dims}}
        │
        ▼
fields.FieldGenerator
  ├── _compute_turbulence_bc(patch, bc_type):
  │     - lookup I from TURBULENCE_INTENSITY_BY_BC_TYPE[bc_type]
  │     - compute k = 1.5·(I·U)², ε = C_μ^0.75·k^1.5 / (0.07·4A/P)
  │     returns (k, ε, I, L)   ← add I, L to return
  │
  └── _write_turbulence_field(k / epsilon):
        - render turbulentIntensityKineticEnergyInlet block with I
        - render turbulentMixingLengthDissipationRateInlet block with L
        - keep value=computed as seed
```

### Error Handling

- If `bc_type` not in `TURBULENCE_INTENSITY_BY_BC_TYPE` → fall back to `TURBULENCE_INTENSITY_DEFAULT = 0.05` + log a warning.
- If perimeter extraction fails (e.g. non-planar patch, zero triangles) → fall back to circular `D_h` + log debug.
- If `flow_rate ≤ 0` or `area ≤ 1e-8` → return `None` (existing behavior, unchanged).

### Testing Strategy (TDD)

**Unit tests — `tests/runner/test_turbulence_bc.py`:**

1. `test_compute_turbulence_bc_fan_outlet_uses_10_percent`:
   Given a fan-server patch with bc_type=`fan_outlet`, U=3.24 m/s, rectangular 0.12×0.40m (area=0.048, perimeter=1.04) → assert `k ≈ 0.157` (within 1%), `ε ≈ 0.59` (within 5%).

2. `test_compute_turbulence_bc_acu_outlet_uses_5_percent`:
   Given ACU patch bc_type=`acu_outlet`, U=10 m/s, rectangular → assert `k ≈ 0.375`, `I` effectively 0.05.

3. `test_compute_turbulence_bc_unknown_bc_type_falls_back`:
   Given bc_type=`unknown_type` → `I = 0.05`, no exception, log warning.

4. `test_rectangular_Dh_vs_circular_Dh`:
   Given 0.12×0.40 rectangle → `D_h_rect = 4·0.048/(2·0.52) = 0.185`. Assert the code picks this, not circular 0.247.

5. `test_perimeter_extraction_from_2_triangles`:
   Given 2 triangles forming a 0.12×0.40 rectangle, assert computed perimeter = 1.04 (within 1e-4).

6. `test_perimeter_fallback_on_non_planar_patch`:
   Given a U-shape patch (degenerate perimeter) → falls back to circular D_h.

7. `test_write_turbulence_field_renders_native_bc_types`:
   After running `_write_turbulence_field`, assert the output contains `turbulentIntensityKineticEnergyInlet` and `turbulentMixingLengthDissipationRateInlet` (for flow inlets, not for outlets/walls).

**Integration / regression tests — manual (documented in plan, not in CI):**

- Tri_angle full 500-iter solve; assert:
  - no boundedness warnings after iter 20
  - final ε_max < 1e4 m²/s³
  - final ν_t_min > 2e-5 m²/s (> molecular viscosity)
- CMI-China full 500-iter solve; assert no regression (still converges, ε_max remains same order as last passing run)
- Base_leakage 500-iter solve; same no-regression

## Unit boundaries

- `PatchGeometry` (new NamedTuple): `(area, perimeter, bbox_dims)` — pure data.
- `_compute_patch_geometry` in `stlgen_runner.py` — STL → geometry dict. No CFD logic.
- `_compute_turbulence_bc` in `fields.py` — geometry + bc_type → (k, ε, I, L). No I/O.
- `_write_turbulence_field` in `fields.py` — values → OpenFOAM native BC text. No computation.

Clear separation lets each be tested independently.

## Files to touch

- `src/backend/aircooling/runner/stlgen_runner.py:866-887` — rewrite `_compute_patch_areas` → `_compute_patch_geometry` returning area+perimeter+bbox
- `src/backend/aircooling/runner/fields.py:63-64` — replace `TURBULENCE_INTENSITY = 0.05` with `TURBULENCE_INTENSITY_BY_BC_TYPE` dict + default
- `src/backend/aircooling/runner/fields.py:89` — accept `patch_geometry` instead of `patch_areas`
- `src/backend/aircooling/runner/fields.py:102-128` — rewrite `_compute_turbulence_bc` per new formula; extend return tuple to `(k, ε, I, L)`
- `src/backend/aircooling/runner/fields.py:1142-1200` — rewrite `_write_turbulence_field` to emit `turbulentIntensityKineticEnergyInlet` / `turbulentMixingLengthDissipationRateInlet` for flow inlets
- `src/backend/aircooling/third_parties/foam/template/constant/turbulenceProperties.j2` (or inline) — `kEpsilon` → `realizableKE`
- `src/backend/aircooling/runner/tests/test_turbulence_bc.py` (new) — 7 tests above
- Add regression-safe logging so run logs carry `I={}, L={}` for each flow inlet

## Done criteria

- [ ] All 7 unit tests pass
- [ ] Tri_angle 500-iter: no ε boundedness triggers after iter 20, ε_max < 1e4, ν_t_min > 2e-5
- [ ] CMI-China 500-iter: `End` reached; ε_max same order as pre-change
- [ ] base_leakage 500-iter: `End` reached; ε_max stays O(100)
- [ ] No regression in existing runner tests (`pytest src/backend/aircooling/runner/tests/`)
- [ ] Commit + push runner + submodule bump if any

## Open questions (for user review)

None identified. The three knobs chosen (I table / perimeter / realizableKE) have clear defaults and fallbacks.

## Verification Results — 2026-04-18

Full mesh+solve on all three canonical cases with T1+T2+T3 live (fresh mesh, 500 iter, 8-proc MPI):

| Case | End reached? | ε_max (last 100 iter) | ε_avg mean (last 100 iter) | Native BCs | realizableKE |
|------|--------------|------------------------|------------------------------|-----------|--------------|
| Tri_angle    | ✓ | 888 – 964 | 0.87 | 4 inlets | ✓ |
| CMI-China    | ✓ | 32,474 – 39,735 | 10.57 | 19 inlets | ✓ |
| base_leakage | ✓ | 504 – 570 | 5.83 | 6 inlets | ✓ |

**Baseline (pre-fix) for comparison:**

- Tri_angle: ε_max saturated at **5.3e6** (boundedness limiter) — pathological.
- CMI-China: ε_max ~76k average, with pathological transient jump of 5.8e+08 at iter 309 under `relax_k_epsilon=0.5` (pre-existing fix landed earlier as commit `b73725f`).
- base_leakage: ε_max ~670 at endTime=30 with relax=0.5 — already healthy.

**Interpretation:**

- Tri_angle ε_max reduced **5,500×** (5.3e6 → 964), ε_avg reduced **3,350×** (2918 → 0.87). Pathological `1/k` singularity eliminated.
- CMI-China ε_max stays O(1e4), unchanged order of magnitude — no regression under realizableKE.
- base_leakage ε_max O(100–600), well within physical range.

All three cases reach `End` without NaN, FOAM FATAL, or MPI_ABORT. ν_t collapse signature (ν_t ≈ molecular viscosity) no longer visible in Tri_angle logs.

All 8 unit tests pass (`conda run -n dcdtuni pytest src/backend/aircooling/runner/tests/test_turbulence_bc.py`); broader runner suite retains the 4 pre-existing unrelated failures in `test_partition_hole_fvoptions.py`.
