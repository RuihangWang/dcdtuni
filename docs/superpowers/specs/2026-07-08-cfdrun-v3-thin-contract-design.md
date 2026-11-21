# cfdrun V3 — Thin Frontend/Backend Contract (Design Spec)

**Status:** proposed · **Date:** 2026-07-08 · **Supersedes (direction):** the deferred "pure-V2 mesh-pipeline migration" noted in `docs/archive/schema-refactor-2026-06/2026-06-14-schema-refactor-final-report.md` §7 and `docs/reference/cfdrun-v2-schema.md` §14.

> This spec turns a stakeholder requirement (frontend = geometry + roles only; backend = derives all mesh/CFD rules; `.cfdrun` = a thin stable contract that hides OpenFOAM detail) into a concrete, staged, verifiable design. It is grounded in an empirical parity probe (2026-07-08) over 20 named cases.

---

## 1. Motivation (evidence-based)

Today the **frontend computes OpenFOAM-ready detail** and ships it inside the transitional `meshGeometry` blob of the `.cfdrun` (`buildCfdrunJson()`, `src/frontend/web/STL_generate.html:33861-33974`): nested `gravity{x,y,z}`, computed `blockMeshBounds`, `monitoring`, resolved `baseSize`/`refinement*` levels. The runner reads this derived blob as `self._data` via `CFDExport.model_validate(raw.get("meshGeometry") or {})` (`stlgen_runner.py:1159` and `:1980`).

A parity probe (`purev2_config_parity.py`) proved that across **all 20 named cases**, the derived blob is **NOT parity** with the root/raw-UI V2 config: `simulationConfig` differs in 25–43 fields, `meshConfig` in 12–15 fields (root has flat `gravityX/Y/Z`, `roomTemperatureC`, UI flags; the blob has the derived shapes). So the frontend is doing the backend's job, and the `.cfdrun` leaks OpenFOAM detail.

**Consequence:** the mesh/CFD semantics are duplicated (JS + Python), the contract is not stable, and the backend cannot be re-targeted to non-data-center scenarios without editing the frontend.

## 2. Target architecture

```
Frontend  →  geometry · STL export · solidRef · object.kind/surface.role
             · FULL mesh-refinement settings UI (defaults + per-rack/per-unit overrides)
                              │
              cfdrun V3 (stable contract — carries settings + geometry; hides OpenFOAM DICT detail)
                              │
Backend   →  CFD semantic mapping · consume settings faithfully · derive computed DICT detail · OpenFOAM case
```

> **Non-negotiable (product-owner directive, 2026-07-08):** the frontend's mesh-refinement configuration UI is a **core selling point** and must be preserved in full — not one setting removed. The frontend owns the default refinement parameters + logic AND the per-rack/per-unit override capability. `cfdrun V3` carries these settings **explicitly**, exactly as the UI produces them; the backend consumes them faithfully and **NEVER re-derives, redefines, or role-infers refinement**. This overrides the earlier sketch of a backend "role → MeshPlan" refinement rule policy.

- **Frontend responsibilities:** geometry (transforms, room polygon, STL solids), `solidRef` (solid/patch identity), `object.kind` + `surface.role` classification, **and the complete mesh-refinement configuration** (every UI knob, the default refinement logic, per-rack/per-unit overrides, refinement zones). It does NOT hand-assemble OpenFOAM *dictionary* detail (block-mesh bounds computation, gravity-vector nesting, monitoring assembly) — those move to the backend — but it owns all user-facing mesh settings.
- **Backend responsibilities:** CFD semantic mapping (BC registry — already done in V2), **faithfully consume the frontend's explicit mesh settings** (the P1 resolver), derive only the non-setting computed dict detail (block bounds from geometry, gravity shape, monitoring), and generate the OpenFOAM case. There is **no backend refinement rule engine**.
- **cfdrun V3:** a stable contract carrying geometry (incl. transforms, OQ-A) + roles + physics inputs + the FULL mesh-settings set + per-object overrides. Extending to a new scenario adds backend BC/geometry handling, not a refinement rule policy.

## 3. What V3 already has, and what it must add

V2 already carries the raw material for the contract:
- `geometryExport.solids[]` = `{solidName, stlFileId, objectPath, surfaceUid, role}` → **`solidRef` + `surface.role`**. `object.kind` is derivable from `objectPath` prefix (`constructions.racks`/`acus`/`baffles`/`floors`/`partitionWalls`/…).
- `boundaryBindings[]`/`porousBindings[]`/`sourceTerms[]` = the **CFD semantic mapping** (already materialised by `cfdrun_reader.resolve_run()` into the `BridgeBCRegistry`).
- `meshConfig`/`simulationConfig` at the **root** = the frontend's user-set mesh settings + defaults (kept, **frontend-owned**). The backend consumes them faithfully; it does not derive, redefine, or override them.

V3 must **remove the duplication** (the transitional `meshGeometry` blob, which re-ships the config the frontend already sets at root) and **carry the frontend's mesh settings explicitly** through the contract. The refinement knobs are NOT leakage to strip — they are the frontend's user-facing settings and stay; only the redundant blob copy is removed.

## 4. Geometry-dependent outputs the backend still computes (not settings)

Refinement/feature LEVELS are **frontend-set settings** (per the non-negotiable in §2), carried explicitly — the backend never infers them from role. Independently, three *computed* mesh outputs are **geometry-dependent** and must be produced by the backend from geometry, not from any UI setting (proven by the `self._data` inventory):

| Output | Needs | Source today |
|---|---|---|
| `blockMeshBounds` | room dimensions + polygon | frontend `_getBMBounds()` (`STL_generate.html:33920`) |
| `createBaffles` seeds / `locationsInMesh` | per-object transforms (anchor/quaternion/size) | v1 blob `racks/floors/baffles/partitionWalls` geometry |
| rack rotated refinement region | rack `quaternion` | v1 blob `racks[].geometry` |

**Therefore the thin contract MUST still carry geometric metadata** (per-object transforms + room polygon + per-unit slot geometry), OR the backend must derive it from STL AABBs (loses rotation exactness). "Frontend does geometry modeling" in the target diagram must be read to include emitting these transforms. This is **Open Question A** below.

## 5. Open Questions (require stakeholder sign-off before Stage 2+)

**These do not block Stage 1** (Stage 1 is parity-preserving and decision-neutral). They shape Stages 2–4.

- **OQ-A — How thin is the geometry contract?** **[DECIDED 2026-07-08 → A1]** The `.cfdrun` V3 contract WILL carry explicit geometry facts (per-object `transform` — anchor/quaternion/size/scale — plus room polygon and per-unit slot geometry), emitted by the frontend as a `geometryExport.meshHints` block. The backend does NOT fall back to STL-AABB for rotated racks / non-rectangular rooms. Rationale: role alone cannot produce block bounds, seeds, or rotated regions (§4), and STL-AABB loses rotation exactness. This makes P3 a "define + emit + consume `meshHints`" task rather than an STL-derivation task.
- **OQ-B — User override channel. [DECIDED 2026-07-08 → PRESERVE IN FULL]** The frontend's mesh-refinement settings + per-rack/per-unit override capability are a **core selling point** and are kept entirely. `cfdrun V3` carries the complete mesh-settings set (levels, feature levels, per-unit dicts, refinement zones, base sizes) explicitly, exactly as the frontend UI produces them. Not one setting is dropped.
- **OQ-C — Reproduce vs redefine the numbers. [DECIDED 2026-07-08 → REPRODUCE; frontend keeps the logic]** Mesh-refinement logic does NOT move to the backend. The frontend keeps its default refinement parameters + logic; the backend only **reproduces/consumes** the frontend's settings — it never redefines them or infers them from roles. P1's reproduce-only resolver is therefore the *permanent* model, not a temporary stage, so cell-parity holds by construction and no re-baseline is needed.

## 6. Fields with no source in root V2 today (Stage 1 finding)

The parity probe found three derived fields the backend cannot source from root V2 as-is:
- `blockMeshBounds` — root is `null` → geometry-derived (defer to Stage 3; keep reading from blob until then).
- `roomTemperature` — root `roomTemperatureC` is `null` in all 20 cases (the frontend defaults `state.geometry.temperature` → 22 only in the blob). Stage 1 defaults to 22; a small frontend change must populate root `roomTemperatureC` for non-default temps (Stage 2).
- `monitoring` — absent in root; empty (`{residuals:true, serverTemperature:[], acuReturnTemperature:[]}`) in all 20 cases. Stage 1 emits this default; non-empty monitors must map from V2 `postprocessConfig.surfaceMonitors` (Stage 2).

## 7. Staged approach

| Stage | Deliverable | Decision-neutral? | Gate |
|---|---|---|---|
| **0** (done) | §14 doc audit + parity probe evidence | yes | — |
| **1** | Backend **MeshPlan config resolver** that *reproduces* the frontend's `meshConfig`/`simulationConfig` derivation from root V2 (excluding geometry-dependent fields). Wire the runner to consume it behind a flag. | **yes** | golden-parity unit test (resolver output == blob config) + 7-case browser-use mesh/registry parity |
| **2** | **Zero-loss mesh-settings passthrough** (NOT a rule engine). Guarantee the frontend's FULL mesh-refinement settings set (every UI knob + per-rack/per-unit overrides + refinement zones) flows through `cfdrun V3` explicitly and is consumed faithfully by the backend; audit for any dropped setting vs the frontend UI; populate root `roomTemperatureC`/`monitoring` from V2 so the last blob-carried config fields become backend-sourced too. | decided (OQ-B/C) | 7-case parity + feature-completeness audit vs frontend UI (zero settings lost) |
| **3** | Geometry contract: frontend emits `geometryExport.meshHints` (transforms + room polygon + slot geometry, OQ-A → A1); backend consumes it for block bounds + seeds + rotated regions (no STL-AABB fallback). | decided (OQ-A) | 7-case mesh-cell parity |
| **4** | Freeze `cfdrun V3` schema (geometry + `meshHints` + solidRef + kind/role + physics inputs + FULL frontend mesh settings + overrides); delete only the redundant `meshGeometry` blob (root mesh settings STAY — they are frontend-owned). | decided | full 16-case batch + browser-use |

## 8. Verification strategy

- **Primary browser-use cases (stakeholder-selected):** `Base_leakage`, `Alibaba-sige`, `Base604`, `GTC`, `Tri_angle`, `Slab`, `Shinaide_Liquid`. These span leakage, very-large, dense, mid, triangular, raised-floor, and liquid-cooled scenarios.
- **Every stage gate uses the CLAUDE.md-mandated path:** load `.cfdproj` → export STL+cfdrun → mesh → (solve) via **browser-use** (GPU WebGL) or `curl` REST API. NEVER `conda run … run_cfd` for end-to-end.
- **Parity gates:** (1) golden unit test — resolver output equals the current `meshGeometry` config block on all cases; (2) `checkMesh` cell count within the established ~0.13% baseline; (3) `BridgeBCRegistry` counts unchanged.
- **Golden-file source of truth:** the current `meshGeometry.{meshConfig,simulationConfig}` blocks in each case's `.cfdrun` (what the frontend produces today). `purev2_config_parity.py` already enumerates the exact field diffs.

## 9. Non-goals

- Not touching the BC registry (V2 `boundaryBindings`/`porousBindings`/`sourceTerms` already own physics).
- Not changing OpenFOAM solvers, templates, or numerics.
- Not migrating the liquid-cooling solver contract (only air-cooling mesh/sim config).
- Stage 1 does NOT delete `meshGeometry` (geometry still flows through it until Stage 3/4).

## 10. Risks

- **Byte-exact reproduction is fiddly** (renames, `exclude_none`, default coalescing). Mitigation: golden-file test drives it field-by-field.
- **Rotated racks degrade under STL-AABB** if OQ-A resolves to A2. Mitigation: measure cell delta on `Tri_angle`/`Slab` before committing.
- **Cross-repo sync** (`/sync-dcdtuni-dctwin`) must carry every backend change.
