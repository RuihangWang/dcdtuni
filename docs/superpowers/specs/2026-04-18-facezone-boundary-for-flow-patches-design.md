# faceZone-Protected Boundary for Flow-Through Patches Design

**Date:** 2026-04-18
**Scope:** `src/backend/aircooling/runner/stlgen_runner.py` — snappyHexMeshDict `refinementSurfaces` generation for flow-through `type=patch` solids
**Goal:** Force snappyHexMesh to preserve fanServer `_inlet_patch`, fanServer `_outlet_*`, ACU supply / return, and rack-level aggregated inlet / outlet solids even when they have no wall-sibling `solid` to anchor their edges against higher-priority neighboring surfaces.

---

## 1. Problem & Motivation

### 1.1 Observed failure

User ran CMI-China-fixed case (2026-04-18). snappyHexMesh produced a mesh, but the `fanServer` `_inlet_patch` survival rate was **6 / 15**. The 9 missing inlets triggered the `_write_t_field` strict gate (solve path) and the `[mesh-placeholder]` warning (mesh path, prior fix), because the fan_outlet temperature-rise BC references the inlet face via `weightAverage(T)` and cannot be built without it.

### 1.2 Why the prior fixes are not sufficient

- **2026-04-18 auto-refinement neighbor-thinness fix** (commit `4c3e15b`): correctly computed per-U level `(3, 4)` for all server buckets. That was written into `snappyHexMeshDict.refinementSurfaces.merged_server_14U.regions.B01a__5680T_inlet_patch.level (3 4)`. Verified. **Inlets still dropped.**
- **2026-04-18 mesh-path `strict=False` placeholder** (commit `a4bb864`): lets mesh pipeline complete with a `fixedValue(ambient)` placeholder for fan_outlets whose inlet is missing. Solve path keeps `strict=True` and still raises — which is correct from a physics standpoint but means the user cannot solve CMI-China at all.

### 1.3 Root-cause analysis

Comparing surviving vs dropped inlets in the same case:

| solid | `type` in snappyDict | `level` | fate |
|---|---|---|---|
| `B06_inlet_u1` (rack-aggregated 1U) | `wall` + `inGroups (wall)` | `(1, 1)` | survived |
| `B01a__5680T_inlet_patch` (fanServer integral front face) | `patch` | `(3, 4)` | **dropped** |
| `B01a__5680T_outlet_p0` (fanServer outlet port) | `patch` | `(3, 4)` | survived |

The outlet survives because fanServer outlet geometry is split into `_outlet_p0` (small `type=patch` port) **surrounded by** `_outlet_wall` (large `type=wall` frame). The wall solid gets auto-merged into the closed-shell hull, and the port's four edges share coincident geometry with wall siblings — snappy's cell-merging logic cannot drop the port without also dropping the walls that bound it, so it's preserved.

The fanServer **integral-face inlet** (user's "whole-face inlet mode", which base_leakage also uses) has **no wall sibling**. The inlet's four edges are adjacent to rack frame, partition wall, and blanking plate geometry — all separately meshed surfaces with their own refinement levels. When cell-count pressure hits (either `maxGlobalCells` cap or per-cell-merge heuristics in snappy), **a type=patch face that has no wall anchor can be silently merged into a higher-priority neighbor and vanish** from the boundary file.

### 1.4 Why `base_leakage` survives today

- Smaller domain (131 m³ vs 450 m³, 3.4× smaller)
- `maxGlobalCells` = **5,000,000** vs CMI-China's **500,000** (10× looser cap)
- Only 4 fanServers vs 15

Under those conditions, snappy's refinement iterations run to completion, inlet edges stay sharp as feature edges, and snappy preserves them through cell-merging heuristics. The integral-face-inlet pattern **works** — it's just fragile. Any large case with tight cell cap can trip the same failure.

### 1.5 The fix: snappy's `faceZone` mechanism

snappyHexMesh supports a `faceZone` on `refinementSurfaces.regions` that tells snappy to preserve the named face set unconditionally — it is the same mechanism the runner already uses for partition holes and wall baffles (see `stlgen_runner.py` ~L1712-1960) with `faceType internal`. For **boundary** patches (flow-through, fluid on one side), the `faceType boundary` variant applies: snappy preserves the face as an **external boundary** patch, not an internal baffle.

Adding `faceType boundary; faceZone <name>_faceZone;` to every flow-through `type=patch` solid's region entry forces snappy to preserve the face regardless of neighbor refinement levels or cell-merge pressure. BC generation is unchanged — the face is still `type patch` in `polyMesh/boundary`, `fields.py` still routes by bc_type.

---

## 2. Scope

### 2.1 Affected solid categories

In `src/backend/aircooling/runner/stlgen_runner.py`, the refinementSurfaces region-entry generator assigns `patchInfo { type patch; }` to the following categories (all semantically "flow-through boundary patches"):

| Category (Python var) | Examples | Current type | Fix |
|---|---|---|---|
| `acu_supply_solids` | `ACU1_Front`, `ACU3_Front_patch` | `patch` | add faceZone |
| `acu_return_solids` | `ACU1_Back`, `ACU3_Back_part1` | `patch` | add faceZone |
| `server_inlet_solids` | `B01a__5680T_inlet_patch`, `Default_Rack_1_fan_server_1_inlet_patch` | `patch` | add faceZone |
| `server_outlet_solids` | `_outlet_patch` (whole face mode) or `_outlet_p0` (patch mode) | `patch` | add faceZone |
| `rack_io_solids` | `A04a_inlet_u1`, `A04a_inlet_u34_u44` (rack-level aggregated) | `patch` | add faceZone |

### 2.2 NOT affected

- All `type=wall` solids (rack frame, blanking plates, partition walls, wall baffles, server top/bottom/left/right, outlet_wall, floors, ceilings, room walls): unchanged.
- Existing `faceType internal` mechanism for partition holes / wall baffles / perforated plates / cyclic baffles: unchanged.
- Any solid that is already wrapped in `createBafflesDict` as a cyclic pair: unchanged.

### 2.3 Hard no-goes

- Do not change STL generation (UI side).
- Do not change fanServer inlet/outlet geometry semantics (integral-face vs port-patch mode).
- Do not change BC generation logic in `fields.py` or `bridge_bc.py`.
- Do not remove or weaken the mesh-path `strict=False` placeholder fallback introduced in commit `a4bb864` — it stays as a diagnostic safety net that surfaces mismatches cleanly during mesh pipeline for any remaining corner-case not covered by faceZone.

---

## 3. Architecture

### 3.1 Single change site

`stlgen_runner.py` builds snappyHexMeshDict by assembling `refinementSurfaces` dict entries. Each STL surface becomes a top-level entry, and per-region (per-solid) entries sit under its `regions {}` block:

```
refinementSurfaces
{
    merged_server_14U
    {
        level (1 1);
        regions
        {
            B01a__5680T_inlet_patch
            {
                level (3 4);
                patchInfo { type patch; }
                //            ↓↓↓ NEW
                faceType        boundary;
                faceZone        B01a__5680T_inlet_patch_faceZone;
            }
            B01a__5680T_outlet_p0
            {
                level (3 4);
                patchInfo { type patch; }
                faceType        boundary;
                faceZone        B01a__5680T_outlet_p0_faceZone;
            }
            // ... other solids ...
        }
    }
}
```

### 3.2 Dispatch logic

The 5 solid-set variables (`server_inlet_solids`, `server_outlet_solids`, `acu_supply_solids`, `acu_return_solids`, `rack_io_solids`) are **local variables** built inside the same snappyDict-generating method around line 3355-3493 of `stlgen_runner.py`. They feed three existing `elif` branches (around L3914, L3939, L3950) that emit `patchInfo { type patch; }` today. The fix is purely **inline**: append two lines to each of those three emit blocks.

Concretely, the pattern changes from:

```python
solid_entries.append(f"""            {solid}
{{
    level ({_rl[0]} {_rl[1]});
    patchInfo {{ type patch; }}
}}""")
```

to:

```python
solid_entries.append(f"""            {solid}
{{
    level ({_rl[0]} {_rl[1]});
    patchInfo {{ type patch; }}
    faceType boundary;
    faceZone {sanitized};
}}""")
```

Three branches (server_inlet/outlet at L3914, rack_io at L3939, acu supply/return at L3950) get this identical two-line append. No helper function required.

### 3.3 faceZone naming convention

Use the **sanitized solid name itself** as the faceZone name: `faceZone {sanitized};`.

Rationale:

- Matches the existing convention used at `stlgen_runner.py:3966` for `rack_wall_baffle_solids` which emits `faceZone {sanitized};` (bare name, no suffix).
- Collision-safe: sanitized solid names are globally unique (enforced in STL generation) and in this patch we add faceZone to solids that currently have NO faceZone at all (they're pure `refinementSurfaces` regions today), so nothing to clash with.
- Reads clearly in generated dict and in `polyMesh/faceZones`.

### 3.4 Why `boundary` and not `internal`

| faceType | fluid adjacency | snappy cells | downstream |
|---|---|---|---|
| `internal` | fluid on both sides | cells share the face as an internal boundary | consumed by `createBaffles -overwrite` to produce cyclic master/slave pair |
| `baffle` | zero-thickness in fluid | face inserted as a zero-thickness baffle | OpenFOAM treats as wall-like |
| `boundary` | **fluid on one side, virtual on the other** | face becomes an external boundary | `type=patch` flow-through (our case) |

fanServer inlet / ACU supply are semantically single-sided: fluid enters from the side of the room, the other side is inside the equipment cavity (sealed off by wall siblings). `boundary` is the correct choice.

---

## 4. Interaction with Existing Mechanisms

### 4.1 Mesh-path `strict=False` placeholder (commit `a4bb864`)

Stays in place. Expected behavior change:

- **Before fix**: CMI-China hits the placeholder path for 9 fan_outlets → emits 9 `[mesh-placeholder]` warnings → mesh completes but solve-only raises on the same 9.
- **After fix**: faceZone preserves all 15 inlets → `_find_server_inlet_patch` finds them in `mesh_patch_names` → placeholder branch never triggers → solve-only succeeds.

Regression check: for any case where the placeholder path *would* still trigger despite faceZone (e.g. a geometry-level bug unrelated to snappy merging), the placeholder stays as the last safety net. Do not remove.

### 4.2 createBaffles

`faceType boundary` faceZones are **external boundaries**; `createBaffles -overwrite` operates on **internal** faces read from `createBafflesDict`. It will not see our new faceZones. No conflict.

### 4.3 Per-bucket refinement levels (existing auto-detect)

Unchanged. faceZone forces preservation regardless of level, but level still controls cell refinement density near the face, which still matters for resolving flow gradients (wall Y+, heat transfer). Keep the auto-detect formula output as the level input.

### 4.4 polyMesh/faceZones artifact

snappy writes `constant/polyMesh/faceZones` with one entry per faceZone. No consumer in this codebase reads faceZones; it's a debug artifact. Size cost is negligible.

### 4.5 Parallel mesh (`meshProcess > 1` + decomposePar)

faceZones are decomposed / reconstructed along with the mesh by OpenFOAM's standard tooling. No special handling needed.

---

## 5. Data Flow

```
cfdrun (unchanged)
  ↓ _load + _build_bridge_bc
bridge_bc registry (unchanged)
  ↓ _build_snappy_hex_mesh_dict (method body)
local sets already built around L3355-L3493:
  server_inlet_solids, server_outlet_solids,
  acu_supply_solids, acu_return_solids, rack_io_solids
  ↓ per-solid emit loop, three affected elif branches:
   - L3914  if solid ∈ server_inlet_solids ∪ server_outlet_solids:
              emit level + patchInfo{patch} + faceType boundary + faceZone {sanitized}
   - L3939  elif solid ∈ rack_io_solids:
              emit level + patchInfo{patch} + faceType boundary + faceZone {sanitized}
   - L3950  elif solid ∈ acu_supply_solids ∪ acu_return_solids:
              emit level + patchInfo{patch} + faceType boundary + faceZone {sanitized}
   - all other elif branches: unchanged
  ↓
constant/triSurface/*.stl (unchanged)
system/snappyHexMeshDict (two extra lines per affected solid)
  ↓ snappyHexMesh (parallel or serial, as today)
constant/polyMesh/boundary (5-category solids now guaranteed present)
constant/polyMesh/faceZones (new entries, informational only)
  ↓ fields.py (unchanged)
0/U, 0/T, 0/p_rgh, … (as today)
```

The 5 solid-set variables already exist as **local variables** inside
the dict-building method; nothing new to collect. The change is purely
appending two lines to three existing emit blocks.

---

## 6. Edge Cases

| Case | Behavior |
|---|---|
| Solid exists in cfdrun but STL has zero triangles for it | snappy emits empty faceZone; harmless |
| Same solid name registered in two categories (should not happen by design) | `_needs_faceZone_boundary` returns true on first match; idempotent |
| User swaps cfdrun mid-run and re-meshes | existing `_regenerate_fields_for_baffles` + `_setup_complete_case_in_mesh` covers, faceZone regenerates from fresh snappyDict |
| cfdrun has no ACUs and no fanServers | 5 sets are empty, `_needs_faceZone_boundary` always false, no change to snappyDict |
| Solid name has characters needing sanitization | solid names are pre-sanitized by STL generator; suffix `_faceZone` is alphanumeric+underscore, safe |
| User uses `createBafflesDict` to also wrap one of our faceZone solids | would be a pre-existing cfdrun bug; faceZone boundary + createBaffles internal for the same face is ill-defined. Out of scope to detect; document in changelog |

---

## 7. Testing Strategy

### 7.1 Unit / structural tests

`src/backend/aircooling/runner/tests/test_facezone_boundary_for_flow_patches.py` (new).

- `test_fanserver_inlet_gets_facezone_boundary`: build a minimal cfdrun with one fanServer, call the snappyDict builder, assert emitted dict text contains both `faceType        boundary` and `faceZone        <inlet_name>_faceZone` under the inlet's region entry.
- `test_fanserver_outlet_port_gets_facezone_boundary`: same for `_outlet_p0`.
- `test_acu_supply_and_return_get_facezone_boundary`: cfdrun with one ACU, 2 supply faces + 2 return faces, all 4 get the two lines.
- `test_rack_io_aggregated_inlet_gets_facezone_boundary`: rack-level `_inlet_uX` solid gets the treatment.
- `test_frame_wall_does_NOT_get_facezone_boundary`: rack frame / partition / blanking / server_top solids do NOT get the two lines (regression guard against scope creep).
- `test_integral_face_inlet_gets_facezone_boundary`: "whole-face inlet mode" (patches=[], wallSolid=None — like base_leakage's `Default_Rack_1_fan_server_1_inlet_patch`) is covered. This pins the user's design-intent statement that integral-face mode works the same as port mode for faceZone purposes.

All tests use pytest with in-memory dict mocking of `bridge_registry` + reading the runner's emit path directly; no OpenFOAM subprocess, no STL file I/O.

### 7.2 E2E regression (marked integration, skipped by default)

`src/backend/aircooling/runner/tests/test_cmi_china_inlet_survival.py` (new, `@pytest.mark.integration`).

- Runs full mesh pipeline on CMI-China case
- Asserts `constant/polyMesh/boundary` contains **all** fanServer `_inlet_patch` and `_outlet_p0` from cfdrun (count match)
- Asserts no `[mesh-placeholder]` warning fires for T BC

Requires OpenFOAM env; run manually or in CI with OpenFOAM image.

### 7.3 browser-use smoke checklist

Manual after code lands:

1. CMI-China → mesh → confirm `grep -cE "_inlet_patch$" polyMesh/boundary == 15` (was 6)
2. CMI-China → solve → no RuntimeError, solve reaches first iteration
3. base_leakage → mesh + solve → no regression, 4/4 inlets preserved

---

## 8. Out of Scope

- Changing `maxGlobalCells` defaults (orthogonal knob; user can still tune)
- Changing per-U auto-detect formula (already fixed in commit `4c3e15b`)
- Changing STL generation / UI (deliberately not touched — this is a runner-side config fix)
- Adding faceZone to `type=wall` solids (they already have closed-shell preservation via snappy's standard mechanism)
- Renaming the existing `_cellZone` convention used by partition holes
- Investigating or altering snappy's `includedAngle`, `resolveFeatureAngle`, or `mergeTolerance` settings
- Converting fanServer inlet to port-patch mode (user confirmed whole-face mode must stay supported)

---

## 9. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| snappy rejects unknown `faceZone` in boundary-type region (unlikely; grep of OpenFOAM docs confirms support since v1806) | Low | High | Test on CMI-China + base_leakage before shipping |
| faceZones ballooning mesh file size | Low | Low | Each zone adds a few hundred bytes to `polyMesh/faceZones`; 30 solids × ~200B = 6 KB, negligible |
| Solve raises on unexpected `_faceZone` patch in boundary | Near-zero | Medium | faceZones do NOT appear as patches in boundary; they appear only in `polyMesh/faceZones` |
| Edge-case cfdrun where a flow-through solid is also registered as a cyclic-baffle pair | Very low | Medium | Pre-existing cfdrun bug; not introduced by this change. Add a startup warning in follow-up if it shows up in practice |
| Over-refined cell count due to "now every flow-through patch gets its feature" | Medium | Low | No — level is unchanged; faceZone only controls preservation, not refinement density |
| Breaks parallel mesh (decomposePar splits faceZones) | Low | Medium | OpenFOAM standard, tested for partition holes today. No new decomposition path needed |

---

## 10. Acceptance Criteria

The fix is shipped when all of the following hold:

1. Unit tests in `test_facezone_boundary_for_flow_patches.py` pass (≥ 6 cases from §7.1).
2. CMI-China case produces a mesh with 15 / 15 fanServer inlets in `polyMesh/boundary` (was 6 / 15).
3. CMI-China case solve starts without the "paired inlet patch missing" RuntimeError.
4. base_leakage case still produces 4 / 4 fanServer inlets + solve runs to first iteration (regression).
5. Mesh-path `[mesh-placeholder]` warning is not triggered for any fanServer in either case (placeholder branch remains as dead-code safety net, still in source).
6. No changes outside `stlgen_runner.py` and the new test file. Plus submodule pointer bump in the parent repo if anything frontend-facing was touched (spec says no frontend changes, so the parent bump should be runner-only and live in the parent repo directly).
