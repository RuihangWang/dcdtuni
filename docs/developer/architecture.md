# Architecture

DCTwin is a Python layer on top of OpenFOAM and a PyTorch liquid-cooling solver, driven from a browser editor through one REST server. This page describes the layers, the data flow of an air-cooling run, where the code lives, and how authentication and ports work. It reflects the code as of 2026-10-21.

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser: React editor (src/frontend/react, Three.js r184)           │
│  model tree · inspector · 2D floor plan · mesh/solve · post-process  │
│  liquid workbench · transient timeline · automation (window.__dcdtuni)│
│  shared model logic: src/frontend/shared/js (also used by the        │
│  legacy single-file editor, src/frontend/web/STL_generate.html)      │
│  renderer switch: Three.js │ Omniverse (Kit WebRTC stream, optional) │
└───────────────┬──────────────────────────────────────────────────────┘
                │ REST / JSON / SSE on the launcher's port (default 8000)
                │ optional admin terminal: WebSocket on port + 1
┌───────────────▼──────────────────────────────────────────────────────┐
│  src/server/server.py (ThreadingHTTPServer)                          │
│  auth · sessions · /api/* routing · job management · SSE logs        │
│  serves the React build (or the legacy page, or /api only)           │
└───────┬──────────────────────────┬─────────────────────┬─────────────┘
        │ air jobs                 │ liquid sessions      │ results
┌───────▼───────────────┐  ┌───────▼──────────────┐  ┌───▼─────────────┐
│ STLGenRunner          │  │ LiquidRunManager     │  │ postprocess.py  │
│ (aircooling/runner)   │  │ Newton DAE runtime   │  │ PyVista native  │
│ manifest · BCs ·      │  │ (liquidcooling)      │  │ OpenFOAM reads  │
│ mesh · solve ·        │  └──────────────────────┘  └─────────────────┘
│ receipts · verdict    │
└───────┬───────────────┘
        │ subprocess / mpirun
┌───────▼──────────────────────────────────────────────────────────────┐
│  dcCartMesh (default) │ blockMesh + snappyHexMesh                    │
│  redistributePar · createBaffles · checkMesh · decomposePar          │
│  buoyantBoussinesqSimpleFoam_new / buoyantBoussinesqPimpleFoam_new   │
└──────────────────────────────────────────────────────────────────────┘
```

The browser talks only to the REST API; it never calls OpenFOAM. The scripted API (`python -m agent.scripted_api`) starts a private server through the launcher and drives the same endpoints, using the editor's export modules headless (`src/frontend/react/src/headless/`), so scripted runs and clicks in the editor produce the same manifests.

---

## 2. Data Flow of an Air-Cooling Run

```
Editor: live project (.cfdproj v5 in memory)
   │  materialise at t = 0, export STL files + .cfdrun v4
   ▼
POST /api/run-mesh {meshOnly | solveOnly | full, files…}
   │  server writes case/<name>/model/<name>.cfdrun and Mesh/constant/triSurface/*.stl
   ▼
STLGenRunner.run_mesh_only()
   │  _load()                       validate the manifest (CfdrunV2), read the geometry
   │                                blob through execution_blob(), discover STL solids
   │  _prepare_runtime_metadata()   resolve_run(): bindings → in-memory BC registry
   │  validate_geometry(), preflights, watertight room enclosure
   │  _generate_mesh_dicts()        snappyHexMeshDict, createBafflesDict, … (Python)
   │  engine dispatch:  dcCart → _run_dcCart_mesh_pipeline
   │                    snappy → _run_{parallel,serial}_mesh_pipeline
   │  _setup_complete_case_in_mesh()   0/ fields, fvSolution, fvSchemes, controlDict
   │  _record_air_mesh_quality()       strict checkMesh → mesh-quality-receipt.json
   ▼
STLGenRunner.run_solve_only()
   │  re-check the mesh receipt, regenerate the case files
   │  _run_solve_from_mesh()   copy Mesh/ → log/<timestamp>_<name>/
   │     decompose (or reuse dcCart partitions) → bc-audit.json
   │     mpirun -np N buoyantBoussinesq*Foam_new -parallel   (steady-stop watcher)
   │     convergence.json · sensorResults/ · air-run-execution-receipt.json
   ▼
POST /api/pp/init → /api/pp/mesh, /field, /slice, /streamlines, /volume (X-PP-Revision)
```

### Key file locations after a run

```
case/<CaseName>/
├── <CaseName>.cfdproj            # the editor's saved project
├── mesh-quality-receipt.json     # strict checkMesh verdict bound to the manifest and mesh
├── model/<name>.cfdrun           # the live manifest of the latest export
├── Mesh/                         # working OpenFOAM case, reset on every mesh
│   ├── constant/triSurface/      # STL files exported by the editor
│   ├── constant/polyMesh/        # serial mesh (snappy), or
│   ├── processor*/               # partitioned mesh (dcCart keeps it)
│   ├── system/  0/
├── log/<timestamp>_<CaseName>/   # one directory per solve: results live here, not in Mesh/
│   ├── <timestamp>_<CaseName>.log, convergence.json, bc-audit.json,
│   ├── air-run-execution-receipt.json, model/<name>.cfdrun (as executed)
│   ├── processor*/ or <time>/    # results
│   └── sensorResults/, postProcessing/probes/
├── log/api/<date>_SGT__<kind>/   # scripted-API runs: result.json and logs
└── Liquid/
    ├── model/*.liquidrun         # exported liquid inputs (older one-shot path)
    └── log/run-<id>/             # liquid runtime sessions: input, checkpoint, result
```

The `.cfdproj` is the user's saved file; the run inputs that matter for debugging are the archived manifest and receipts in the run directory.

---

## 3. Package Structure

The project name in `pyproject.toml` is `dcdtuni` (version 4.0.1); the importable package is `backend`, installed editable from `src/` into the repository environment (`.codex-tmp/envs/dcdtuni`, see [Installation](../user-guide/installation.md)). `import dcdtuni` fails; use `import backend`. `torch` and `networkx` are the `liquid` extra.

```text
src/backend/
├── aircooling/
│   ├── runner/                      # the air pipeline (see Runner Internals)
│   │   ├── stlgen_runner.py         # STLGenRunner: orchestration, mesh engines, solve
│   │   ├── cfdrun_schema.py         # Pydantic models: CfdrunV2 (strict) + CFDExport (geometry blob)
│   │   ├── cfdrun_reader.py         # resolve_run(): manifest → ResolvedRun / BC registry
│   │   ├── derived_config.py        # execution_blob(), backend-derived mesh/solver config
│   │   ├── bridge_bc.py             # BridgeBCRegistry, BCSpec, perforated-plate physics
│   │   ├── fields.py                # 0/ field files (FieldGenerator)
│   │   ├── openfoam_case.py         # controlDict/fvSolution/fvSchemes/decomposeParDict
│   │   ├── bc_audit.py              # pre-solve boundary-condition audit
│   │   ├── convergence.py           # convergence verdict (return-air rule)
│   │   ├── mesh_acceptance.py, mesh_quality.py, air_mesh_quality_receipt.py
│   │   ├── mesh_budget.py           # soft mesh-time target
│   │   ├── air_run_execution_receipt.py, air_run_result_receipt.py
│   │   ├── hybrid_air_heat_handoff.py   # liquid → air heat handoff
│   │   ├── transient_start.py, transient_time.py
│   │   ├── sensor_harvest.py, postprocess.py
│   │   └── tools/                   # regen_all_meshes and other batch tools
│   └── third_parties/
│       ├── dcCartMesh/              # dcCartMesh source (+ editable snappyHexMesh copy)
│       └── foam/                    # *_new solvers, writeExplicitBC, Jinja2 templates
├── liquidcooling/
│   ├── solver/dae_newton.py, equation_graph.py   # production Newton DAE
│   ├── dae_runtime.py, dae_experiment.py         # DAESimulation, DAEExperiment
│   ├── run_manager.py, runtime.py                # runtime sessions (/api/liquid/runs)
│   ├── liquidrun_dae_schema.py, liquidrun_schema.py
│   ├── graphcompiling/, handlers/, postprocess/, verification/
│   └── solver/hybrid.py, adam_odil.py, rollout.py  # older paths kept for v1/v2 and comparison
├── api/                             # scripted API (python -m agent.scripted_api)
└── tests/
```

The liquid solver has three layers: the **Newton DAE** (production default for new experiments; dense linear solves, CPU float64 by default), the **v2 runtime** (`LiquidSimulation` with PID controllers; compatibility) and the **v1 direct/Adam** paths (legacy and comparison).

---

## 4. Key Modules

| Module | Role |
|--------|------|
| `backend.aircooling.runner.stlgen_runner.STLGenRunner` | Air orchestrator. Public methods: `run_mesh_only()`, `run_solve_only()`, `run_simulation()`, `validate_geometry()`. |
| `backend.aircooling.runner.cfdrun_schema` | `CfdrunV2` validates the whole manifest strictly (schemaVersion 2/3/4); `CFDExport` validates the `meshGeometry` blob the mesh pipeline reads. |
| `backend.aircooling.runner.cfdrun_reader` | `resolve_run()` turns `boundaryBindings` / `porousBindings` / `sourceTerms` into the in-memory BC registry. |
| `backend.aircooling.runner.bridge_bc` | `BridgeBCRegistry` — sanitised patch name → `BCSpec` (bc type, temperature, flow, power, porosity, …). In memory only. |
| `backend.aircooling.runner.fields` | `FieldGenerator` writes the `0/` field files (Python string builders; Jinja2 templates are used only for `system/` dictionaries). |
| `backend.aircooling.runner.convergence` | `assess_run()` writes `convergence.json`; the steady stop watcher uses the same verdict. |
| `backend.aircooling.runner.postprocess` | Reads native OpenFOAM results (partitioned or reconstructed) with PyVista for the browser views; probes and sensor harvest. |
| `backend.liquidcooling.run_manager.LiquidRunManager` | Server-owned liquid sessions: create, start/pause/resume/step/cancel, frames, online inputs, independent equilibrium, checkpoints, executed-input export. |
| `agent.scripted_api` | Scripted API: `run_air_case`, `run_liquid_case`, `solve_liquid_steady`, batch runs. |
| `src/server/server.py` | HTTP handler: routes `/api/*`, enforces auth, streams logs via SSE, runs one mesh/solve job at a time for its case directory. |
| `src/server/security.py`, `auth_manager.py` | Explicit administrator credentials, loopback policy, PBKDF2 password hashing, sessions, IP lockout. |
| `src/server/liquid_runtime_api.py` | JSON adapter for `/api/liquid/runs*`. |
| `src/start_server.sh` | The only launcher for the web frontends (React, legacy HTML, Omniverse demo, API-only). |

---

## 5. Authentication

Authentication is always on and fails closed.

### Credentials

The server starts only with explicit administrator credentials: the environment pair `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`, or `--admin-credentials <owned 0600 two-line file>`. There is no built-in password. When neither is given, `start_server.sh` uses **local mode**: it creates (once) and reuses `src/server/auth-local/admin_credentials` and passes that directory as `--auth-dir`. The scripted API creates its own credentials for each private server.

### Mechanism

- Passwords hashed with **PBKDF2-SHA256**, random per-user salt.
- Users, sessions and bans live in the auth directory (`src/server/auth/` by default, `src/server/auth-local/` in local mode) as `users.json`, `sessions.json`, `ip_bans.json`; sessions survive restarts.
- The session cookie is named per port, `stlgen_session_<port>`, so two servers on one host do not share sessions (the old unscoped name is still accepted for this server's own sessions).
- Loopback clients are **not** trusted automatically; `--trust-loopback` opts in for HTTP only.
- The server's built-in headless browser (`/api/browser/*`) logs in with a random worker session.
- Auth events are logged to `<auth-dir>/server_auth.log`.

### Roles

| Role | Permissions |
|------|-------------|
| `admin` | Everything: mesh/solve, file writes, liquid sessions, user management, terminal. |
| `viewer` | GET endpoints, plus `POST /api/pp/init`, `/api/pp/render-grant`, `/api/mesh-preview/render-grant` and `/api/auth/logout`. |

### Escalating IP lockout

| Consecutive failures | Lockout duration |
|----------------------|-----------------|
| 5 | 5 minutes |
| 10 | 1 hour |
| 15 | 24 hours |
| 20 | Permanent ban |

---

## 6. Ports and Launch

| Service | Through the launcher | `server.py` run directly |
|---------|---------------------|--------------------------|
| HTTP (UI + REST + SSE) | `--port`, default **8000** | `--port`, default 8005 |
| WebSocket terminal (only with `--enable-terminal`) | `--port + 1` (or `--ws-port`) | `--ws-port`, default 8006 |
| React dev server (`--dev`) | UI on `--port`, backend on `--api-port` (default `--port + 2`) | — |
| Omniverse Kit signalling (`--ov`) | `--ov-port`, default 8301; UDP video `--ov-stream-port`, default 47998 | — |

Always start through the launcher (`./src/start_server.sh`); it selects the environment, cleans OpenFOAM paths, handles credentials, refuses ports owned by other users and stops only its own processes. The backend listens on 127.0.0.1 unless `--host 0.0.0.0` is passed. To stop a server from another terminal use `fuser -k <port>/tcp` (never `kill`/`pkill`).

**One case per server.** A server holds one global case directory (`POST /api/set-case-dir` is process-wide) and runs mesh/solve jobs one at a time. Run one launcher per user or parallel task, on distinct ports.

---

## 7. Key Directories

| Path | Purpose |
|------|---------|
| `case/<CaseName>/` | A case: the saved project, the live manifest, `Mesh/`, `log/`, `Liquid/`. |
| `src/frontend/react/` | The editor (React + TypeScript + Three.js); `dist/` is its production build. |
| `src/frontend/shared/` | Code and assets both editors load: `js/` (project format, geometry builders, export), `i18n/`, `vendor/`, `transient/`, and `textures/` — the ONE material and texture pool: `materials.json` (`dcdtuni.material-library/v1`) names every rendered material, texture set, photo, patch preset and the shared lighting; both editors, the Omniverse bridge and Kit read it, so a change there shows in Three.js and OV alike. |
| `src/frontend/web/` | The legacy single-file editor, kept as a reference implementation. |
| `src/frontend/omniverse/cfd_demo/` | Omniverse display bridge (Kit extension `dc.cfd_bridge`) and the standalone demo. |
| `src/backend/kit-cae/` | Git-ignored NVIDIA Kit-CAE checkout and build used by the Omniverse display. |
| `src/server/` | Server, security, liquid and render adapters, server tests. |
| `src/agent/` | The agent layer, which reaches the platform only through `agent.scripted_api`; `backend` never imports it (the `backend.aidesign` shims were removed on 2026-10-22). |
| `src/backend/aircooling/third_parties/` | Native code built from this repository: `dcCartMesh`, `foam` solvers. (The vendored `meshgenerator` tree was removed on 2026-10-26.) |
| `docs/benchmarks/` | Dated evidence receipts referenced by code, tests and docs. |

---

## 8. Parallel Execution

Mesh and solve both run as `mpirun -np N <tool> -parallel`; the runner never runs a solver serially in production.

**Mesh (dcCart, default):** `mpirun -np N dcCartMesh -overwrite -parallel` writes `processor*/` partitions with matched processor patches; if the mesher did not already split the baffles, `redistributePar` (with `preserveFaceZones`) and `mpirun createBaffles -overwrite -parallel` follow. The partitions are kept.

**Mesh (snappy):** `blockMesh` → `decomposePar` → `mpirun snappyHexMesh -overwrite -parallel` → `redistributePar` (`preserveFaceZones`) + `mpirun createBaffles -parallel` → `reconstructParMesh` → serial clean-up.

**Solve:** `_decompose_for_solve` reuses the mesh partitions when the solve uses the same rank count, otherwise reconstructs and decomposes again; then `mpirun -np N buoyantBoussinesq*Foam_new -parallel`. Runs that keep their partitions (dcCart) are post-processed directly from `processor*/`; the others are reconstructed (`reconstructPar -latestTime`) and their explicit boundary values rewritten.

Each solve is archived in its own `log/<timestamp>_<name>/` directory. Test with the same parallel settings as production: decomposition problems do not show up in serial runs.

---

## 9. Concepts Glossary

| Term | Definition |
|------|-----------|
| **multi-solid ASCII STL** | STL file with several `solid`/`endsolid` blocks; each block becomes one OpenFOAM boundary patch. Binary STL cannot carry region names. |
| **watertight topology** | Adjacent STL regions share exact vertex coordinates — no gaps or overlaps. |
| **outward normals** | All triangle normals point toward the fluid domain (right-hand rule). |
| **cut cell** | A Cartesian cell cut by a surface instead of snapped to it; how `dcCartMesh` resolves geometry. |
| **BridgeBCRegistry** | The in-memory registry mapping each patch name to its physical type and values; built from the manifest's bindings every run, never written to disk. |
| **`.cfdrun`** | The run manifest exported from the live project for every mesh/solve; supersedes the saved `.cfdproj` during a run. |
| **`meshGeometry`** | The transitional v1-shaped geometry block inside a `.cfdrun` that the mesh pipeline still reads. |
| **mesh-quality receipt** | `mesh-quality-receipt.json`: strict `checkMesh` verdict bound to the manifest and the mesh; reported to the user and recorded with every solve, never required (owner 2026-10-22). |
| **convergence verdict** | `convergence.json`: `CONVERGED` when every ACU return-air temperature has changed and then stays within 0.5 K over the final window; residuals are diagnostics. |
| **SSE** | Server-Sent Events — how `server.py` streams job logs to the editor's Operation Log. |
