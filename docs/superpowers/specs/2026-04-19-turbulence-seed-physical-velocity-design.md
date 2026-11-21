# Turbulence Seed via Physical Characteristic Velocity — Design Spec

**Date:** 2026-04-19
**Author:** Claude (via brainstorming with user)
**Status:** Draft — awaiting user approval
**Supersedes aspects of:** [2026-04-18 turbulence-bc-physical-grounding](./2026-04-18-turbulence-bc-physical-grounding-design.md) (per-patch `intensity` handling and `internalField` seed)

---

## Background

**Current turbulence model:** `realizableKE` (Shih et al. 1995) — migrated from standard kEpsilon on 2026-04-18. The migration removed the `1/k` source-term singularity that was driving CMI-China NaN at iter 309 and Tri_angle ε-boundedness saturation at 5.3e6 m²/s³. The failure mode addressed by THIS spec is a different pathology that is largely model-agnostic: `internalField` seed pollution plus wall-function / initial-field feedback. Realizable alone does not fix it.

The 2026-04-18 fix addressed **per-bc_type turbulence intensity** (`acu_supply=5%`, `fan_outlet=10%`) and **rectangular hydraulic diameter** (`D_h = 4A/P`). After those changes, individual-patch k/ε computation is physically grounded for patches whose STL face equals the physical flow area — e.g. ACU supply diffusers.

Fan-server patches break this assumption. In the STL they are tiny (~cm²) proxy faces that emit the full server-fan flow to preserve mass conservation, producing an **artificial face velocity of ~100 m/s** against a real fan exit velocity of ~8 m/s. The 2026-04-18 code then uses that 100 m/s in `k = 1.5·(I·U)²`, yielding k ≈ 150 (clamped to the hard ceiling of 100 m²/s²).

Two downstream pathologies result:

1. **`internalField` seed pollution.** `_compute_average_inlet_turbulence` arithmetically averages k and ε across every flow-bearing patch, including fan-server outlets clamped at 100. In `Base_coldAisleIso` (2 racks × 1 fan-server each + 1 ACU), this yields `internalField k = 19.76, ε = 4655` — 150–500× the values recommended by the OpenFOAM `hotRoom` tutorial (k=0.1, ε=0.01) and five orders of magnitude above the CFD-Wiki / ASHRAE bulk-velocity estimate (k≈3.75e-3 for a 1 m/s room with 5% TI).
2. **Per-patch BC error at runtime.** `turbulentIntensityKineticEnergyInlet` is a *dynamic* OpenFOAM BC: it recomputes k = 1.5·(I·U)² at every iteration using the **live CFD patch velocity**. Even if we wrote a correct `value uniform X`, OpenFOAM overwrites it using U_face = 100 m/s and the current `intensity = 0.10` entry. So on every iteration, fan-server outlets inject k ≈ 150 (clamped) straight into the domain.

### Observed failure on Base_coldAisleIso

Log: `/mnt/lv/litianyu/mycode/dctwin/case/Base_coldAisleIso/log/2026-04-19-22-28-38_Base_coldAisleIso/log.mpi_solver`

| Symptom | Value | Expected |
|---------|-------|----------|
| `internalField` k | 19.76 m²/s² | < 0.1 (hotRoom reference) |
| `internalField` ε | 4655 m²/s³ | < 0.1 |
| `bounding epsilon` firings | 989/1000 iterations | transient only, < 50 |
| max ε (pinned) | 34245.9 at ACU wall across all 1000 iter | physical evolution |
| p_rgh residual @ 1000 iter | 0.26 | < 1e-4 |
| Ux final residual @ 1000 iter | 1e-3 | < 1e-5 |

### Literature summary (two-pass subagent research, 2026-04-19)

**General k-ε & indoor-airflow:**
- **Rumsey, Reif & Gatski (2006), AIAA J., DOI 10.2514/1.18015.** k-ε admits a continuum of self-consistent steady-state equilibria. Once seeded into a high-k attractor, the solver sees zero residual drive to leave it. Refutes the assumption "BCs dominate, seed converges out". *Note: this paper targets **standard** k-ε; realizable k-ε (Shih et al. 1995) partially mitigates by removing the 1/k source singularity. However, the wall-function / initial-field feedback mechanism is model-agnostic — our current observed failure confirms this applies to realizable as well.*
- **Kamenetskiy et al. (2013), AIAA 2013-663, DOI 10.2514/6.2013-663.** Numerical evidence of multiple converged RANS solutions with identical BC + grid, initial-condition dependent.
- **Ansys Fluent 12 UG §7.3.18 — Fan Boundary Conditions.** Commercial codes decouple turbulence BC from the CFD face velocity on fan interfaces.
- **OpenFOAM `hotRoom` tutorial.** `internalField` uses `k = 0.1 m²/s²`, `ε = 0.01 m²/s³` uniform — canonical seed for room-scale buoyant flow.
- **CFD-Wiki / CFD Direct.** Industry standard `internalField` = bulk-velocity × turbulence intensity (5–10%), never per-patch arithmetic average.

**DC-CFD-specific (primary anchor for this spec):**
- **Dogan et al. (2022), Open Research Europe 2:43, DOI 10.12688/openreseurope.14579.1.** The only publicly auditable OpenFOAM validation study for data centers. Full case files on Zenodo (10.5281/zenodo.6336674). Key data points we adopt:
  - Servers modeled as **`vectorFixedValueConstraint` cellZone momentum sources** (fvOptions), NOT tiny-patch velocity BCs. Our tiny-patch pattern is not published in DC-CFD — see "Known deviation from literature" below.
  - Rack-front convention: `U_phys = flow_rate / rack_front_area`, yielding 0.95–1.27 m/s.
  - CRAH supply uses `turbulentIntensityKineticEnergyInlet` with **intensity = 0.50** (post-fan model; very aggressive).
  - Turbulence model: **`kOmegaSST`**. We currently run `realizableKE` (Phan 2019 rack-level recommendation, migrated 2026-04-18). kOmegaSST migration is a potential future step, not required by this spec.
  - `internalField` k = 1.81 m²/s², ε ≈ 0.18 uniform.
- **Alkharabsheh et al. (2013, 2015), DOI 10.1115/IPACK2013-73214 / 10.1115/1.4029344.** IBM/Binghamton cold-aisle CFD with standard k-ε in Flovent; default intensity ~10% at ACU/rack.
- **Phan, Hu & Lin (2019), Building and Environment, DOI 10.1016/j.buildenv.2019.03.060.** Rack-level RANS model comparison; standard k-ε acceptable but realizable k-ε preferred for tile modeling.
- **Saiyad, Fulpagare & Bhargav (2022), Building Simulation, DOI 10.1007/s12273-021-0879-3.** DES vs standard k-ε at rack level.
- **Xue et al. (2021), DOI 10.1016/j.buildenv.2021.108284.** Jet-fan CFD peak TKE 2–10 m²/s²; informs clamp ceiling.

**Standards bodies (important null result):** ASHRAE TC 9.9 Thermal Guidelines (2021, 5th ed.), ASHRAE Power Equipment White Paper (2016), Green Grid White Paper #68 all recommend CFD as a verification tool but **publish no numeric turbulence-BC guidance for DC CFD**. Vendor conventions (Flovent ~10%, 6SigmaDC defaults) fill the gap.

**Known deviation from literature.** Dogan 2022's cellZone momentum source (fvOptions) is the peer-reviewed OpenFOAM DC approach; our tiny-patch + mass-flow proxy is not published. This spec keeps the tiny-patch pattern (schema stability) and fixes it via intensity rescaling, but flags cellZone migration as a follow-up spec.

---

## Goals

1. `Base_coldAisleIso` converges in 1000 iterations: `bounding epsilon` fires < 50 times, `p_rgh` residual < 1e-3, `Ux` residual < 1e-4.
2. Regression protection — `Tri_angle` and `base_leakage` continue to solve at least as well as before this change.
3. `Alibaba-hongchou` (200 racks, ≥1000 fan-server patches, 8 ACUs) runs 200 iterations without ν_t collapse or bounding epsilon firing every iteration.
4. `internalField` k and ε stay within one order of magnitude of the OpenFOAM `hotRoom` reference (0.1, 0.01) across all above cases, or are computed defensibly from literature-backed room-bulk formulas.
5. All changes covered by unit tests on `_compute_turbulence_bc` / `_compute_physical_velocity` inputs/outputs.

## Non-Goals

- **Changing the turbulence model.** The pipeline already runs `realizableKE` (migrated 2026-04-18; Shih et al. 1995 variant). This spec retains it. Migrating to `kOmegaSST` (Dogan 2022) is a potential future step but not required — Phan et al. 2019 explicitly recommend realizable k-ε for rack-level DC CFD, which is exactly our current model.
- **Changing the fan-server modeling paradigm** (no switch to `fanPressureJump` + momentum source — that is a larger schema change, see `Option C` below).
- **Exposing turbulence intensity knobs to the frontend** (per-bc_type Python constants remain the single source of truth).
- **Wall-function tuning.** The wall-function feedback unwinds once the internalField and fan-server BCs stop injecting k≈100 energy; no `nutUSpaldingWallFunction` switch needed.
- **cfdrun schema changes.** The `U_phys` derivation is purely in-runner (geometry from existing rack/server/ACU fields).

---

## Design

Four coordinated sub-tasks, all local to `src/backend/aircooling/runner/fields.py` plus one new helper module for physical-velocity inference.

### T1 — Physical characteristic velocity per bc_type

Introduce `_compute_physical_velocity(patch_name, bc_type) -> float` returning `U_phys` in m/s.

**Scope note.** Per the existing comment in `fields.py:66-68`, only `acu_supply` and `fan_outlet` bc_types reach `_compute_turbulence_bc` today (`acu_return` and `fan_inlet` are handled as domain outlets with `inletOutlet` style BC and don't need turbulence-intensity computation). So T1 only needs two branches:

| bc_type | Formula | Source fields |
|---------|---------|---------------|
| `fan_outlet` | `U_phys = flow_rate / (rack.slotWidth × server.spanSlots × rack.unitHeight)` | rack geometry + server entry in cfdrun |
| `acu_supply` | `U_phys = flow_rate / geom.area` (unchanged; STL face = physical face) | existing |
| unmapped | `U_phys = flow_rate / geom.area` + WARNING log | existing; forward-compatible |

**Literature provenance.** `U_phys` via rack-front-area is the convention used by Dogan et al. 2022 (OpenFOAM, 0.95–1.27 m/s), Alkharabsheh et al. 2013/2015 (Flovent), VanGilder 2024 (ITherm). Rotor-disc area is NOT used in DC-CFD.

**Convention import note.** `TURBULENCE_INTENSITY_BY_BC_TYPE["fan_outlet"] = 0.10` is borrowed from generic axial-fan / indoor-airflow CFD (Erden & Khalifa 2019). **DC-CFD literature has no measured server-fan exit TI** — the second-pass subagent research confirmed zero PIV/hot-wire DC server-fan wake studies exist. Dogan 2022 avoids the question entirely by using cellZone momentum sources. We retain I=0.10 as a defensible convention with this caveat documented.

**Fallback ladder** for `fan_outlet` when rack/server metadata is unavailable:

```
1. Look up server entry in bridge_registry for patch_name's rack
2. Compute A_server = slotWidth × spanSlots × unitHeight
3. U_phys = flow_rate / A_server
4. If any dependency missing:
       U_phys = min(U_face, 5.0 m/s)   # industry fan exit typical
       log.warning("fan-server %s missing rack geometry; fallback U_phys=%.2f", ...)
```

The 5 m/s fallback ceiling reflects industry 1U/2U server-fan exit velocities (4–8 m/s; Sapounas et al. 2008; Xue et al. 2021).

### T2 — Effective-intensity rescaling for dynamic BCs

Keep `turbulentIntensityKineticEnergyInlet` and `turbulentMixingLengthDissipationRateInlet` (OpenFOAM-idiomatic, handles flow reversal). Rescale the `intensity` parameter so that OpenFOAM's runtime recompute produces physical k/ε despite the artificial U_face:

```
I_physical  = TURBULENCE_INTENSITY_BY_BC_TYPE[bc_type]  # e.g., 0.10 for fan_outlet
U_phys      = _compute_physical_velocity(patch_name, bc_type)
U_face      = flow_rate / geom.area
I_effective = I_physical * U_phys / U_face
```

Written to the dict as:

```
type        turbulentIntensityKineticEnergyInlet;
intensity   {I_effective:.6g};
value       uniform {k_seed:.6g};
```

Where `k_seed = 1.5·(I_physical·U_phys)²` is the physical value (used only if OpenFOAM cold-starts without evaluating the dynamic formula).

**Worked example** — Base_coldAisleIso fan_server_1_outlet_p0:
- U_face = 100 m/s, U_phys = 8 m/s, I_physical = 0.10
- I_effective = 0.10 × 8/100 = **0.008**
- At runtime OpenFOAM computes: k = 1.5·(0.008·100)² = 0.96 m²/s² ✓ physical

For ACU (U_phys = U_face): I_effective = I_physical unchanged.

Same treatment for ε via `turbulentMixingLengthDissipationRateInlet`: `mixingLength` stays as `0.07·D_h`; the recomputed ε = C_μ^0.75·k^1.5/L follows the corrected k.

**Literature note.** This rescaling achieves the same final k as a `fixedValue` BC with pre-computed k = 1.5·(I_phys·U_phys)². Dogan 2022 avoids the issue entirely via cellZone momentum sources — see "Known deviation from literature" in Background and "Future work" below.

### T3 — internalField seed: ACU-only average + physical safety floor

Replace `_compute_average_inlet_turbulence` logic. Rename to `_compute_internal_field_seed` for clarity.

**Also bump ACU intensity from 0.05 → 0.10.** Rationale: `TURBULENCE_INTENSITY_BY_BC_TYPE["acu_supply"] = 0.05` assumed a post-diffuser flow (generic HVAC). In our pipeline the ACU box encapsulates the CRAH unit (internal fan + diffuser), so the supply face is **post-fan**. Dogan 2022 uses I=0.50 at this location (OpenFOAM DC canonical); Alkharabsheh Flovent defaults to ~10%. We adopt **I_acu = 0.10** as a conservative midpoint — 2× the generic HVAC value (ASHRAE handbook range 4–10%) and 5× below Dogan's aggressive post-fan setting.

```
# In TURBULENCE_INTENSITY_BY_BC_TYPE:
"acu_supply": 0.10,   # was 0.05; post-fan CRAH supply (Alkharabsheh Flovent default)
"fan_outlet": 0.10,   # unchanged; convention import (no DC measurement)

# New _compute_internal_field_seed:
seed_patches = [p for p in flow_patches if bc_type(p) == "acu_supply"]
if not seed_patches:
    # No ACU supply patches: use hotRoom canonical values
    k_internal, eps_internal = 0.1, 0.01
else:
    k_vals   = [_compute_turbulence_bc(p)[0] for p in seed_patches]
    eps_vals = [_compute_turbulence_bc(p)[1] for p in seed_patches]
    k_internal   = max(mean(k_vals),   K_MIN_PHYSICAL)   # see T4 floor
    eps_internal = max(mean(eps_vals), EPS_MIN_PHYSICAL)
```

**Rationale — seed from ACU only:** ACU supplies represent the domain-entering free-stream turbulence. Fan-server patches, leakage patches, and rack-internal flows are domain-internal circulation; they dominate LOCAL turbulence near themselves (via their own per-patch BC) but should not set the DOMAIN-AVERAGE bulk seed. This matches CFD-Wiki ("Turbulence free-stream boundary conditions"), Dogan 2022 (uniform internalField, not patch-averaged), and every commercial tool surveyed.

**Expected seed range after this change** (per subagent research Q4): k in 0.1–2 m²/s² across our case portfolio. Base_coldAisleIso: k ≈ 0.04 from ACU-derived, floored to 0.1 by K_MIN_PHYSICAL; matches Dogan 1.81 order of magnitude.

### T4 — Physical-range clamps (ceiling + floor)

New module-level constants in `fields.py`:

```
K_MIN_PHYSICAL   = 0.1    # m²/s²; hotRoom canonical lower bound
K_MAX_PHYSICAL   = 5.0    # m²/s²; 3× Dogan 2022 DC internalField (1.81)
EPS_MIN_PHYSICAL = 0.01   # m²/s³; hotRoom canonical lower bound
EPS_MAX_PHYSICAL = 100.0  # m²/s³; gives nu_t/nu ≈ 1500× at k=5
```

Applied in `_compute_turbulence_bc`:

```
k       = max(min(k,       K_MAX_PHYSICAL),   K_MIN_PHYSICAL)
epsilon = max(min(epsilon, EPS_MAX_PHYSICAL), EPS_MIN_PHYSICAL)
```

**Ceiling justification:**
- Dogan 2022 DC internalField = 1.81 m²/s² (highest DC-CFD published). Ceiling at 5 gives 3× safety headroom.
- Xue 2021 jet-fan peak TKE = 2–10 m²/s² — applies to jet cores, not DC rooms.
- Current ceiling 100 m²/s² is ~55× Dogan, clearly masking bugs.

**Floor justification:**
- OpenFOAM `kMin_` / `epsilonMin_` defaults are `SMALL` (≈1e-15) — numerical safety only, NOT a physical floor. See cpp.openfoam.org kEpsilon source.
- OpenFOAM `hotRoom` tutorial uses (k=0.1, ε=0.01) — the canonical physical lower bound for room-scale buoyant flow.
- Below these values: ν_t = C_μ·k²/ε collapses toward molecular viscosity → effective laminarization → unphysical T/U fields.
- Floor enforces minimum physically plausible turbulence regardless of BC geometry quirks.

**Warning logs** (both directions):

```
if k_unclamped > K_MAX_PHYSICAL:
    logger.warning("patch %s: computed k=%.2f > %.2f — clamped; check U_phys for bc_type=%s",
                   patch_name, k_unclamped, K_MAX_PHYSICAL, bc_type)
if k_unclamped < K_MIN_PHYSICAL:
    logger.info("patch %s: computed k=%.4f < %.2f — raised to floor",
                patch_name, k_unclamped, K_MIN_PHYSICAL)
```

---

## Testing

### Unit tests (new file `tests/runner/test_turbulence_physical_velocity.py`)

- **T1 mapping:**
  - `fan_outlet` with given rack/server geometry → U_phys matches hand calc
  - `acu_supply` → U_phys = flow_rate / geom.area
  - `fan_outlet` missing rack metadata → fallback to min(U_face, 5)
- **T2 rescaling:**
  - I_effective = I_physical · U_phys / U_face for fan-server
  - I_effective = I_physical for ACU (no rescale)
- **T3 seed:**
  - Mixed scene (ACU + fan-server): seed = ACU mean only
  - ACU-only scene: seed uses ACU values
  - Empty flow patches: seed = hotRoom canonical (0.1, 0.01)
  - Seed never below floor (1e-4, 1e-5)
- **T4 clamp:**
  - k > 5 gets clamped AND logs warning
  - k ≤ 5 passes through and logs nothing

### Integration smoke tests

All run on the existing case fixtures, `--mesh-only` already cached:

| Case | Run | Success criterion |
|------|-----|-------------------|
| `Base_coldAisleIso` | 1000 iter `buoyantSimpleFoam` | bounding epsilon < 50 firings; p_rgh residual < 1e-3 |
| `Tri_angle` | 500 iter | bounding epsilon count does not increase vs pre-change baseline |
| `base_leakage` | 500 iter | bounding epsilon count does not increase vs pre-change baseline |
| `Alibaba-hongchou` | 200 iter | ν_t stays ≥ ν (no collapse); ε_max never pinned at clamp value |

Record max-k / max-ε / bounding counts in `log.mpi_solver` grep one-liner; compare against baseline before committing.

---

## Future Work (explicitly out of scope here)

1. **Migrate fan-server from tiny-patch velocity-inlet to `fvOptions` cellZone momentum source** — the published DC-OpenFOAM pattern (Dogan et al. 2022). Requires cfdrun schema change (per-server cellZone + semiImplicitSource terms) and rewrite of `_build_snappyHexMesh` region definitions. Separate spec; estimated scope 2–3× this one.
2. **Consider switching turbulence model to `kOmegaSST`** — we already run `realizableKE` (migrated 2026-04-18; matches Phan et al. 2019 rack-level recommendation). Dogan 2022's OpenFOAM DC validation uses kOmegaSST — potential next step if realizable proves insufficient after this spec. Requires fvSchemes/fvSolution updates, BC file regeneration (omega field), and validation cycle.
3. **Measure rather than convention-import fan-server exit TI** — the DC-CFD community has no published measurements. Could commission a targeted PIV / hot-wire study, or extract intensity from rack-level DES simulation (Saiyad 2022 path).

## Considered Alternatives (rejected)

### Alt 1 — Option C: fanPressureJump + momentum source
Rewrite fan-server as an OpenFOAM `fanPressureJump` BC backed by a `momentumSource` fvOption. Semantically identical to Fluent/STAR-CCM+ fan interfaces.
**Rejected:** requires cfdrun schema change (per-server pressure-jump polynomial), breaks backward compatibility with 40+ existing cfdproj files, and is strictly larger scope than the current pathology warrants. Revisit as separate spec if T2 rescaling proves insufficient under further load.

### Alt 2 — Hardcode internalField to hotRoom values (k=0.1, ε=0.01)
Drop all patch-derived computation for internalField.
**Rejected:** loses case-specific information (a 10× higher supply velocity is a genuine signal the bulk TKE should be higher). Our ACU-averaged approach scales with case while bounded by physics.

### Alt 3 — Switch to `fixedValue` BC on all flow patches
Replace dynamic BCs entirely with pre-computed fixedValue k/ε.
**Rejected:** loses flow-reversal adaptation; OpenFOAM-idiomatic pattern is dynamic BC + rescaled intensity, which is what Variation A in T2 does.

### Alt 4 — Lower `intensity` uniformly across all patches
Simply halve I everywhere.
**Rejected:** not principled — the problem is fan-server specifically. Uniform reduction would under-estimate ACU and real inlet turbulence.

---

## Rollout

1. T1 → T2 → T3 → T4 in order, each landing a TDD commit (failing test → implementation → green).
2. Integration smoke on all 4 cases before merge.
3. Update prior 2026-04-18 spec status to "Superseded in part by 2026-04-19" (in-file note).
4. Update project memory entry (if any) tracking turbulence-BC evolution.

---

## Success Criteria Summary

| Check | Pass threshold |
|-------|---------------|
| `Base_coldAisleIso` p_rgh residual at iter 1000 | < 1e-3 |
| `Base_coldAisleIso` bounding epsilon firings | < 50 |
| `Base_coldAisleIso` max ε ever pinned at clamp | no |
| `Tri_angle` regression — bounding count | ≤ pre-change baseline |
| `base_leakage` regression | ≤ pre-change baseline |
| `Alibaba-hongchou` 200 iter ν_t minimum | ≥ molecular ν (1.5e-5) |
| Unit tests (T1-T4) | all green |
