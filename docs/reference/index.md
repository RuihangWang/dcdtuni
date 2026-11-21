# Reference

Field references for DCTwin's file formats, the scripted API and the meshing rules.

## File Formats

> **Current formats (as of 2026-10-21):**
>
> - **`.cfdproj` — schemaVersion 5.** The React editor saves v5: the v4 layout plus a top-level `transient` block, where every time-variable value (server power, ACU supply temperature and airflow, cold-plate heat, pump speed, valve opening, …) lives as keyframe tracks; a steady project has one keyframe at t = 0 per track. Files of every older version are migrated on load. The legacy HTML editor (`--app html`) still reads and writes v4 only.
> - **`.cfdrun` — schemaVersion 4.** The runner accepts versions 2, 3 and 4; the React editor exports 4, which carries one `simulationConfig` (the root one) and, for a transient air run, the `transient` timeline with per-binding schedules. The legacy HTML editor exports version 2.
> - **`.liquidrun` — DAE schemaVersion 3 and 4** for the production Newton DAE solver (v4 is compiled from a cfdproj v5 timeline); version 2.0 is the older runtime/PID path, still accepted.

| Format | Extension | Purpose | Used by |
|--------|-----------|---------|---------|
| [.cfdproj V5](cfdproj-v5-schema.md) | `.cfdproj` | **Current** editor project save: the v4 layout plus `transient` keyframe tracks; what changed from v4, the parameter registry and the v4 → v5 migration rules | React editor, headless compiler |
| [.cfdproj V4](cfdproj-v4-schema.md) | `.cfdproj` | Field reference for the v4 layout that v5 builds on (`geometry.*` model shape, `constructions.*` buckets, `runDefaults`) | Both editors |
| [.cfdrun V2/V3/V4](cfdrun-v2-schema.md) | `.cfdrun` | **Current** simulation manifest: boundary/porous bindings, source terms, mesh and solver configuration, the transitional `meshGeometry` blob; the v3/v4 changes are in its own section | `STLGenRunner` |
| [.liquidrun v2](liquidrun-v2-schema.md) | `.liquidrun` | Runtime/PID liquid input (compatibility path) | Liquid runtime |
| [.liquidrun v3 DAE](liquidrun-v3-dae-schema.md) | `.liquidrun` | Strict input of the Newton DAE solver (the backend also accepts v4) | Liquid DAE |
| [.cfdrun (v1)](legacy/cfdrun-schema.md) | `.cfdrun` | *Superseded* — legacy manifest; refused by the runner (re-export required). Its geometry domain is still the shape of `meshGeometry` | — |
| [.cfdproj (v3)](legacy/cfdproj-schema.md) | `.cfdproj` | *Superseded* — legacy editor save; migrated on load | — |
| [Post-processing field payload](pp-field-binary-format.md) | — | Wire format of `GET /api/pp/field/<name>` and the decode bug it prevents | React post-processing |

## Scripted API

| Topic | Doc | Summary |
|-------|-----|---------|
| [Scripted API](scripted-api.md) | `agent.scripted_api` | Headless `.cfdproj` → export → mesh → solve → metrics through a private server, and liquid DAE steady state with implicit-adjoint gradients; Python functions and the `python -m agent.scripted_api` CLI; dated `log/api/` result directories. |

## Meshing

| Topic | Doc | Summary |
|-------|-----|---------|
| [Mesh engines](mesh-engines.md) | `meshConfig.meshEngine` | `dcCart` (default, cut-cell, MPI-native) and `snappy` (body-fitted): what each runs, how the choice is resolved, the dcCart sizing keys and how to build each engine. Also records the retired three-way `meshMode` and the `meshgen` engine retired on 2026-10-26. |
| [6Sigma cell-size rule](sixsigma-cell-size-rule.md) | `meshConfig.meshBaseSize`, `firstLayerMinFraction` | What 6Sigma's grids measure (0.25 m max, 10 mm floor, about 1U at racks, aspect ≤ 5, no cut cells) and the dcCart sizing rule derived from them. |
| [STL export baseline](stl-baseline/README.md) | `stl-baseline/` | The byte-level fingerprint of one case's STL export and how to re-check it. |

## How the Formats Relate

```text
Editor (React; legacy HTML for reference)
  │
  ├─── Save Project ──────────────────► .cfdproj  (v5: models + runDefaults + transient tracks)
  │
  ├─── Mesh / Solve (air) ────────────► .cfdrun   (frozen run manifest, exported from the live
  │                                                 project at t = 0, plus schedules if transient)
  │                                          │
  │                                          ▼
  │                                     STLGenRunner
  │                                          │
  │                                          ▼
  │                                     OpenFOAM case
  │                                     (dcCartMesh | snappyHexMesh
  │                                      → createBaffles → strict checkMesh
  │                                      → buoyantBoussinesq{Simple,Pimple}Foam_new)
  │
  └─── Liquid experiment ─────────────► .liquidrun (v3/v4)
                                             │
                                             ▼
                                        Newton DAE runtime (/api/liquid/runs)
```

The `.cfdproj` stores everything the editor needs to restore a session. The runner never reads it; it consumes the `.cfdrun` manifest, which the editor regenerates from the live state each time a mesh or solve starts, so unsaved edits reach the run. The `.cfdrun` contains only what the CFD backend needs: STL references and per-patch bindings, source terms, mesh and solver settings, and the geometry blob the mesh pipeline still reads.

`config.toml` belonged to the retired `room.json` pipeline. No current code reads it, so its guide and schema pages were removed on 2026-10-21; they remain in Git history for anyone reading an old case.
