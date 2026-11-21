# Perforated-Plate Setting Unification — Design

## Problem

Three places in the frontend today model "perforated" behaviour differently:

| Object | Current representation |
|---|---|
| Baffle model CFD | `cfd.baffleType: 'wall' \| 'perforated'` + scalar `cfd.porosity` (0–100) |
| Baffle patch (per-patch override) | `cfd.patchPhysics[id].baffleType` + `porosity` (0–100) |
| Floor patch opening | `cfd.openings[i].type: 'wall' \| 'wallBaffle' \| 'perforated'` + `porosity` (0–100) |

All three feed `bc_type="perforated_baffle"` in `bridge_bc.py`, which in
`fields.py:917` converts `σ = porosity/100` to `D = (1-σ²)/(2σ²), I = 0` and
writes a `porousBafflePressure` entry in `p_rgh`.

The current formula ignores hole geometry and hard-codes the inertial coefficient
to zero. For HVAC-style thin perforated plates the inertial term **dominates** —
so the current physics is quantitatively wrong, and the UI gives the user only
a single number (porosity %) that cannot distinguish a 64 % open sheet of 3 mm
holes from a 64 % open sheet of 20 mm holes.

## Goals

1. One unified perforated-plate settings block in the right-panel inspector,
   reused verbatim by baffle-model / baffle-patch / floor-patch call sites.
2. Physical parameters the user enters: **shape**, **hole diameter (mm)**,
   **open-area ratio (%)**. Nothing else.
3. Conversion to `porousBafflePressure` (D, I, length) lives in the backend
   Python — the cfdrun carries raw physical params only.
4. Apply a published formula (Idelchik §8.2) so the numbers are defensible.
5. Drop the existing `porosity` fields from `.cfdproj` on load — reset every
   perforated object to a fixed default.

### Non-goals

- Partition-wall patches (different physics — holes through a thick solid).
- Rack through-zone / leakage `sigma` fields.
- Reynolds-number correction (Idelchik σ-only, Re-independent form is sufficient).

## Data model (frontend state)

Every perforated site stores the same nested object:

```js
perforated: {
  shape: 'circle' | 'rect' | 'diamond',   // default 'circle'
  holeDiameterMm: number,                  // default 3
  openAreaPct: number                      // default 64
}
```

Call sites:

- `model.cfd.perforated` for baffle models where `cfd.baffleType === 'perforated'`
- `model.cfd.patchPhysics[patchId].perforated` for per-baffle-patch overrides
- `model.cfd.openings[i].perforated` for floor-patch openings where `type === 'perforated'`

Migration from old cfdproj files: `normalizeState` deletes every
`porosity`, `patchPhysics[id].porosity`, `openings[].porosity` field on load
and installs the fixed default (`circle, 3 mm, 64 %`) wherever the parent
toggle says `'perforated'`. No attempt to derive physical params from the
legacy number.

## UI (unified inspector block)

One helper:

```js
function renderPerforatedInspector(perforated, { onChange }) -> htmlString
```

Rendered whenever the parent toggle is `perforated`. Three entry points
(baffle-model inspector ~L9452, baffle-patch inspector ~L9462, floor-patch
inspector) each drop their current porosity input and call this helper.

Block layout:

```text
┌─ 🌀 穿孔板设置 ─────────────────
│ 开孔形状   [🔵 圆 ▼]  (circle / rect / diamond)
│ 孔径 (mm)  [ 3.0 ]
│ 开孔率 (%) [ 64  ]
│ 估算 K = 1.50  (Idelchik)     — read-only, live
└─────────────────────────────────
```

Each input change goes through the existing handler path (`updateModelCfd` /
`updateBafflePatchPhysics` / `updateFloorOpeningCfd`) with a dotted key
(e.g. `'perforated.shape'`). Existing undo-checkpoint and operation-log
behaviour are preserved.

## cfdrun export

In `buildCfdExportJson` everywhere the current code emits `"porosity": N`
under a baffle / patch / floor-opening object, replace with:

```json
"perforated": {
  "shape": "circle",
  "holeDiameterMm": 3.0,
  "openAreaPct": 64.0
}
```

Matching `PerforatedSpec` dataclass in `cfdproj_schema.py`:

```python
@dataclass
class PerforatedSpec:
    shape: Literal["circle", "rect", "diamond"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0
```

## Backend conversion

Single function in `bridge_bc.py`:

```python
# Idelchik, Handbook of Hydraulic Resistance (3rd ed., 2007), §8.2
# eq. 8-4: thin sharp-edged perforated plate (ℓ/d_h ≤ 0.015, high Re):
#   K = [1 + φ·0.707·(1-σ)^0.5 - σ]² / σ²
# where σ is the open-area ratio. φ is a shape correction to the
# entrance-loss coefficient 0.707 — circular sharp-edged holes are the
# reference case (φ=1); rectangular and diamond holes have slightly
# stronger vena-contracta losses at the corners (Miller, 1990; Idelchik
# §8.6 shape tables):
SHAPE_PHI = {'circle': 1.00, 'rect': 1.10, 'diamond': 1.15}

def perforated_to_porous_baffle(spec: PerforatedSpec) -> tuple[float, float, float]:
    """Convert (shape, d, σ) → (D, I, length) for OpenFOAM porousBafflePressure.
    Returns (D_viscous_per_m², I_inertial_dimensionless, length_m).
    """
    sigma = max(1e-4, min(0.9999, spec.openAreaPct / 100.0))
    phi = SHAPE_PHI.get(spec.shape, 1.0)
    K = (1.0 + phi * 0.707 * (1 - sigma) ** 0.5 - sigma) ** 2 / sigma ** 2
    # porousBafflePressure in OpenFOAM:
    #   dp = (I · 0.5 · |U| + D · mu) · length · rho · U
    # For a thin plate (ℓ/d_h < 0.5) the viscous term ≈ 0 across HVAC Re
    # range, so put the entire loss on I and set length = 1 m as the OF
    # normalization (D and I already carry the physics).
    return (0.0, K, 1.0)
```

In `fields.py` the two sites that currently hard-code `D = (1-σ²)/(2σ²), I=0`
(near lines 917 and 961) read `bc_info["perforated"]` and call the new
function. `bridge_bc.py` is updated to forward the raw `PerforatedSpec`
through the pipeline instead of the flat `sigma` scalar.

`length` stays 1.0 m — it is an OpenFOAM normalization parameter, not the
plate thickness.

Reference values (computed from the formula above, φ=1):

| σ    | K     |
|------|-------|
| 0.20 | 51.3  |
| 0.40 | 8.23  |
| 0.64 | 1.50  |
| 0.80 | 0.416 |

These are the **golden values** the unit test asserts against (±1 % to
allow for floating-point). The values are not taken from a published
table — they are analytical evaluations of the spec's formula, so the
test is a regression guard against accidental formula changes rather
than a comparison with independent data.

## Testing

1. `tests/test_perforated.py` — unit test against the golden reference
   values listed in the Backend Conversion section (circular, φ=1):
   - σ=0.20 → K ≈ 51.3
   - σ=0.40 → K ≈ 8.23
   - σ=0.64 → K ≈ 1.50
   - σ=0.80 → K ≈ 0.416
   Tolerance ±1 % for floating-point. The test is a regression guard
   on the formula, not a comparison with independent data.

2. Frontend structural test (Python grep):
   - `normalizeState` removes top-level `porosity` fields.
   - `buildCfdExportJson` emits `perforated` objects, never `porosity`
     scalars under baffle/floor/patch nodes.
   - `renderPerforatedInspector` is defined once and called from exactly
     three sites.

3. End-to-end smoke: load `Base_leakage.cfdproj`, confirm every baffle
   previously set to `perforated` now carries the default perforated
   object; run the mesh + solve pipeline with a fixture case and verify
   `0/p_rgh` contains `porousBafflePressure` with I matching the Idelchik
   K for the default σ=0.64 (≈1.50).

## Error handling

- σ clamped into (1e-4, 0.9999) so K stays finite. User-facing numbers
  are clamped to (0.1 %, 99.9 %) in the UI; the backend clamp is a
  defense-in-depth.
- Unknown `shape` string → treat as `'circle'` (φ=1) and log a warning
  in the backend.
- `holeDiameterMm ≤ 0` → reject in the UI (min=0.1); backend warns and
  uses 3 mm.

## Rollout

Single atomic change (frontend + schema + backend + test) since the
migration is destructive (old `porosity` deleted). No flag / staged
rollout.

## Files touched

- `STLGeneratorForOpenFoam/web/STL_generate.html` — state default, 3
  inspector call sites, `buildCfdExportJson`, `normalizeState` scrub.
- `STLGeneratorForOpenFoam/web/i18n/{zh-CN,en-US}.json` — new labels for
  shape / diameter / open-area / K-estimate.
- `src/backend/aircooling/runner/cfdproj_schema.py` — `PerforatedSpec` dataclass.
- `src/backend/aircooling/runner/bridge_bc.py` — `perforated_to_porous_baffle`
  function, propagate `PerforatedSpec` to `bc_info`.
- `src/backend/aircooling/runner/fields.py` — two replacement sites at ~L917 / L961.
- `tests/test_perforated.py` — new.
