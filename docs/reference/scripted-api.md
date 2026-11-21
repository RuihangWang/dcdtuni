# Scripted API (`agent.scripted_api`)

Headless entry points for external Python callers (for example the ISPO
repository) and for agents: one call takes a saved `.cfdproj` to mesh, solve
and metrics (air), to a steady state with gradients or a transient march with
the flow in every pipe (liquid), or through a liquid session into an air solve
that carries its heat (hybrid), and returns a structured result. Air runs can
also stop after the mesh or solve an existing mesh again; any mix of runs can go
as a batch; and a `.cfdproj` can be built from a list of editor edits without a
browser. Code: `src/agent/scripted_api/`; headless export and modelling:
`src/frontend/react/src/headless/`; tests: `src/backend/tests/test_api/`.

## Why it is built this way

| Rule (`AGENTS.md`) | How the API meets it |
|---|---|
| Browser-exported STL is the geometry source of truth; no Python re-generation of geometry | The **React editor's own export modules** run headless: esbuild bundles them as a browser IIFE and evaluates the bundle in a JSDOM window (`headless_compiler.mjs` + `projectCompiler.ts`). The manifest and STL bytes are what the editor uploads for the same saved project. |
| STL/mesh/solve only through the server (debugging rules 1 and 8) | The air run is a **server client**: it posts the editor's `/api/run-mesh` bodies (mesh, then solve) to a private server and follows the jobs with `/api/job-status` and `/api/log-stream`. `run_cfd` is never called directly. |
| `start_server.sh` is the only way to start a server; one server holds one case | Each air run starts its own server through the launcher: `--app api` (the backend serves `/api` only, so neither the React build nor the archived `STL_generate.html` is needed), a port pair verified free and reserved against the process's other sessions, `--case-dir`, `--auth-dir` fresh per run, per-run administrator credentials through `DCDTUNI_ADMIN_USER`/`DCDTUNI_ADMIN_PASSWORD`. A batch starts one such server per item. |
| Only `fuser -k <port>/tcp`; never signal another user's process | The server is stopped with `fuser -k -TERM` on its own two ports (the server exits cleanly and the launcher's trap runs; `stages.server.stop` records `signal: TERM` and the launcher's return code 0); only a port still held after a 15 s grace gets `fuser -k -KILL`. Each signal is sent only after checking that every process on the port belongs to the current user. |
| Never solve serially (debugging rule 7) | A solve whose effective rank count is 1 is refused at the `input` stage unless `allow_serial_solve=True` (`--allow-serial-solve`). |
| No new liquid solver | The steady state is `DAESimulation.equilibrium` (the liquid session's query); gradients are the existing implicit adjoint of `solve_steady` (`_ImplicitRoot`); a transient run is `runtime_batch.run_dae_manifest`, the batch path the server takes for a DAE manifest; pump, valve and supply-temperature overrides go through `DAESimulation.set_inputs`, the call behind the server's `inputs` endpoint. |
| The hybrid heat handoff is the server's | A hybrid run posts the `.liquidrun` the liquid panel would submit to the SAME private server (`/api/liquid/runs`, `start`), then the editor's solve body with `hybridHeat = {project, liquidRunId, sampleIndex}` (`run/runSubmit.ts`); the server compiles the liquid heat into the air manifest and writes `model/heat-ledger.json`. The API does not compute the handoff. |
| Every edit goes through the editor's store | A headless build runs `window.__dcdtuni`, the automation contract (`automation/contract.ts`), in JSDOM; each verb goes through `projectStore.edit`, and the output is `serializeProjectState`, the save button's bytes. |
| Results never overwrite earlier runs | Every call creates `<case>/log/api/<YYYY-MM-DD_HHMMSS>_SGT__<command>[_<label>]/` with `exist_ok=False`; files inside are opened in exclusive-create mode. The name carries the command (`air`, `hybrid`, `liquid`) because the directory exists before the export: an `air` call on a hybrid project whose solve runs takes the hybrid path, so its directory reads `..._SGT__air_<label>` while `result.kind` is `hybrid`. |

The editor's run path that the export mirrors (`run/runSubmit.ts`,
`run/runPayload.ts`, `run/session.ts`): pre-flight checks
(`runPreflightChecks`), duplicate-solid refusal, liquid-transport preparation
(`prepareLiquidTransport`, here with an in-process stand-in for its module
worker that makes the same `geometryScene.buildLiquidTransportDetached` call),
`buildManifestV2`, the cached grouped STL map, `bindStlFileHashes`, and
`cfdrunSchema.validateManifest`. The mesh job carries
`meshPreparationElapsedSeconds` (the export time), as the editor's does. The
solve job sends the same manifest bytes with `stlFiles: {}`.

## Python API

Signatures (the defaults are the code's; `src/backend/tests/test_api/test_skill_docs.py` checks
these stubs against `inspect.signature`):

```python
from agent.scripted_api import (run_air_case, run_hybrid_case, export_air_case, run_batch, build_project,
                         run_liquid_case, solve_liquid_steady, solve_liquid_transient, export_liquidrun)


def run_air_case(cfdproj, *, case_dir=None, run_settings=None, solve=True, stages=None,
                 solver_type=None, require_check_mesh=False, accept_seed_collisions=False,
                 allow_serial_solve=False, max_processes=None, mesh_timeout=1800.0,
                 solve_timeout=14400.0, label=None, app="api", raise_on_failure=False,
                 sample_index=None, liquid_timeout=3600.0, liquid_device="cpu", liquid_threads=None,
                 ai_init=None, solver_engine=None) -> dict: ...


def run_hybrid_case(cfdproj, *, sample_index=None, liquid_timeout=3600.0, liquid_device="cpu",
                    liquid_threads=None, case_dir=None, run_settings=None, stages=None, solver_type=None,
                    require_check_mesh=False, accept_seed_collisions=False, allow_serial_solve=False,
                    max_processes=None, mesh_timeout=1800.0, solve_timeout=14400.0, label=None,
                    app="api", raise_on_failure=False, solver_engine=None) -> dict: ...


def export_air_case(cfdproj, *, run_settings=None, solver_type=None, timestamp=None, mesh_patches=None) -> dict: ...
    # {cfdrunFilename, cfdrunJson, stlFiles, manifestErrors, preflight, preflightStops, hybrid,
    #  simulationMode, implementation}


def run_batch(items, *, out_root, max_parallel=1, max_processes, label=None) -> dict: ...


def build_project(steps, *, out, base=None, overwrite=False, timeout=600.0) -> dict: ...  # agent.scripted_api.build


def run_liquid_case(source, *, output_root=None, label=None, mode="steady", inputs=None, duration_s=None,
                    objectives=None, gradient_parameters=DEFAULT_GRADIENT_PARAMETERS, heat_multiplier=1.0,
                    device="cpu", threads=None, fd_check=0, fd_relative_step=1e-4,
                    raise_on_failure=False) -> dict: ...


def solve_liquid_steady(liquidrun, *, objectives=None, gradient_parameters=DEFAULT_GRADIENT_PARAMETERS,
                        heat_multiplier=1.0, device="cpu", threads=None, fd_check=0,
                        fd_relative_step=1e-4, inputs=None) -> dict: ...  # in memory, writes nothing


def solve_liquid_transient(liquidrun, run_dir, *, inputs=None, heat_multiplier=1.0, device="cpu",
                           threads=None) -> dict: ...  # writes run_dir/log/run-<id>/


def export_liquidrun(cfdproj, *, mode=None, duration_s=None, solver_type=None, project=None) -> dict: ...
    # {liquidrun, controllers, simulationMode, implementation}
```

- `run_settings` is merged into `project.runDefaults` (keys `meshConfig`,
  `simulationConfig` only) before export — exactly what the editor's mesh and
  solve panels store. Example:
  `{"meshConfig": {"meshEngine": "snappy", "meshProcess": 8}, "simulationConfig": {"parallelSolve": True, "solveProcess": 8, "endTime": 1000}}`.
- **Solve monitoring** (`simulationConfig.monitoring` of the manifest) is the
  editor's 求解监控 panel, which the export reads through
  `window.smGetMonitoringConfig`, never from the project. The headless compiler
  installs the same bridge (`run/monitoring.ts`) and sets the panel as an API
  run asks: by default residuals on and the ACU return list's select-all —
  every return the manifest binds, labelled and ordered as
  `/api/server-patches` offers them. The runner judges a steady run CONVERGED
  on exactly these return-air temperatures, and every editor export in `case/`
  since 2026-10-20 carries the same list
  (`test_api_export_is_the_editor_export_of_the_same_saved_project` compares
  the whole manifest with the editor's for seven cases). To choose, pass
  `run_settings["simulationConfig"]["monitoring"]`: `residuals` (bool),
  `acuReturnTemperature` (`"all"` or a list of patch names; `[]` for none) and
  `serverTemperature` (server inlet/outlet patch names); a key left out keeps
  the default, and a patch the panel would not offer is an `export` failure.
  The panel lists only the mesh's patches: a solve-only run passes the case
  mesh's patch names, and a full run whose new mesh lacks a monitored patch
  re-exports before the solve (`stages.monitoring.notInMesh`,
  `artifacts.solveManifest`). `stages.export.monitoring` records the patches.
- `stages` selects the work: `("mesh", "solve")` (the default; `solve=False`
  is the old spelling of `("mesh",)`), `("mesh",)`, or `("solve",)`. A
  solve-only run exports the project as usual (so the manifest carries this
  call's solver settings) and posts only the editor's solve body; before any
  server starts it checks that `<case>/mesh-quality-receipt.json` says
  `accepted` and that the STL batch the project exports now is the batch the
  mesh was built from — the content hashes bound in the manifest
  (`geometryExport.stlFiles[*].contentHash`) against the `exported_stl_hashes`
  of the mesh source receipt the runner wrote — `hybrid-mesh-source.json`
  (`hybrid-mesh-source/v2`) for a hybrid project, which is what the runner
  reads before a hybrid solve, else `source-surface-mesh-source.json`
  (`source-surface-mesh-source/v2`) — or, for a manifest without source
  surfaces, against `Mesh/constant/triSurface/`. A hybrid solve also needs
  that uploaded batch itself, which the mesh job archives under
  `log/air-inputs/<id>/` (copy it with the case). Anything else is
  `failure.stage = "input"` with the reason; `stages.meshReuse` records the
  proof. The runner repeats its own stricter check before it solves.
- `solver_type` (`steady` / `transient`; also accepted as
  `run_settings["simulationConfig"]["solverType"]`, never both with different
  values) is applied in the headless compiler through the editor's own mode
  switch, `setSimulationMode(mode, "air")` (`transient/simulationMode.ts`):
  `transient.airMode`, the derived `transient.mode`, `simulationConfig.solverType`
  and, for transient, `simulationConfig.endTimeSeconds = transient.durationS`
  change together, and a transient manifest carries the `transient` block.
  `stages.export.simulationMode` reports the result. `endTime` stays the steady
  iteration count; the transient stop time is `transient.durationS`.
- `meshConfig.meshEngine` is any engine the cfdrun schema admits
  (`dcCart`, `snappy`); `identity.tools.mesher` is the binary the job
  log names, else the engine's executable (`dcCartMesh`, `snappyHexMesh`).
- `meshgen` (MeshGenerator) was retired on 2026-10-26. Asking for it, through
  `run_settings`, a batch row or a saved project whose
  `runDefaults.meshConfig.meshEngine` still names it, fails at `input` with
  "the meshgen engine was retired on 2026-10-26; choose dcCart or snappy"
  before any server starts; `--mesh-engine` no longer offers it. (The editor
  itself opens such a project on `dcCart` and logs a warning.)
- `allow_serial_solve`: a solve on one MPI rank (`parallelSolve` false or
  `solveProcess` 1 after the overrides) is refused at `input` unless this is
  true. Mesh-only runs are never refused for it.
- `max_processes` refuses the run before anything starts when the effective
  MPI rank count of a selected stage exceeds it.
- `mesh_timeout` / `solve_timeout` are the API's own **hard** limits: past
  them the API aborts the job (`/api/abort-job`) and fails the stage. The
  runner's mesh budget (`meshConfig.meshTimeLimitSeconds`, 300 s) is only a
  soft target that is reported and never stops a mesh, so `mesh_timeout` is the
  only limit that ends a runaway mesh.
- `app` is the private server's `--app`: `api` (default) needs no editor;
  `react` / `html` also serve an editor, for debugging.
- `case_dir` defaults to the `.cfdproj`'s folder; another folder receives a copy
  of the `.cfdproj` (never overwriting a different file of the same name).
  The server writes `model/`, `Mesh/` and `log/` there, as for the editor.
- A `.liquidrun` source needs `output_root` (CLI: `--out-root`; without it
  the CLI prints `failure.stage = "input"`); a `.cfdproj` source writes under
  its folder's `log/api/`.
- A **hybrid** project (a liquid server with fan flow, or
  `runDefaults.hybridHeatBudget.version` 2) takes the hybrid path whenever its
  solve runs, from `run_air_case` as from `run_hybrid_case`; see
  [Hybrid air/liquid](#hybrid-airliquid). Its mesh alone needs no liquid session.

## CLI

Run from `src/` (or with `src` on `PYTHONPATH`) in the `dcdtuni` environment.

Mesh quality is reported, never required (owner rule 2026-10-22). A failed
final checkMesh is recorded in `stages.mesh` (`checkMeshOk`, `failedChecks`,
`qualityReceipt.status = "rejected"`) and the run goes on to the solve;
`--require-check-mesh` (`require_check_mesh=True`) makes it stop after the mesh
instead. `--solve-only` needs the case's mesh and its quality record, whatever
the verdict. `--allow-failed-checkmesh` is still accepted and does nothing.

Synopsis (`[...]` optional, `|` alternatives):

```text
python -m agent.scripted_api air X.cfdproj [--case-dir D] [--mesh-only | --solve-only]
                               [--mesh-engine dcCart|snappy]
                               [--mesh-processes N] [--solve-processes N] [--end-time T]
                               [--solver-type steady|transient] [--run-settings JSON]
                               [--max-processes N] [--allow-serial-solve]
                               [--solver-engine openfoam|gpuNewton]
                               [--mesh-timeout S] [--solve-timeout S]
                               [--require-check-mesh] [--accept-seed-collisions]
                               [--app api|react|html] [--label L] [--full]
                               [--sample-index N]   (hybrid project only, as for hybrid)
                               [--ai-init MODEL]    (steady air solve: start from that SCANO checkpoint's prediction)
python -m agent.scripted_api hybrid X.cfdproj [--case-dir D] [--solve-only] [--sample-index N]
                               [--liquid-timeout S] [--liquid-device cpu|cuda|project] [--liquid-threads N]
                               [every run-setting and run-control flag of air]
python -m agent.scripted_api liquid X.cfdproj|X.liquidrun [--out-root D] [--mode steady|transient] [--duration S]
                               [--pump-rpm EDGE=R]... [--valve-opening EDGE=O]... [--primary-inlet-k EDGE=T]...
                               [--objective NAME]... [--gradient-parameter NAME]... [--heat-multiplier M]
                               [--device cpu|cuda] [--threads N] [--fd-check K] [--fd-step H] [--label L] [--full]
python -m agent.scripted_api build spec.json --out X.cfdproj [--base Y.cfdproj] [--overwrite] [--full]
python -m agent.scripted_api export-air X.cfdproj --out-root D [--case-layout] [--mesh-engine ...]
                               [--solver-type ...] [...]
python -m agent.scripted_api export-liquid X.cfdproj --out-root D
python -m agent.scripted_api batch batch.json --out-root D [--max-parallel N] [--max-processes M] [--label L] [--full]
python -m agent.scripted_api identity
python -m agent.scripted_api.contract_verbs [--markdown | --update FILE | --check FILE]
```

stdout carries one JSON document (a summary unless `--full`), also when
`air`, `hybrid` or `liquid` refuse their input before a run directory exists
(`{"kind": ..., "status": "failed", "failure": {"stage": "input", ...}, "runDir": null}`;
a missing or unreadable `.cfdproj` / `.liquidrun` is such an input failure, and
nothing is created for it), and when `export-air` / `export-liquid` refuse:
`failure.stage` is `input` for a bad source and `export` when the editor's
export refuses the project (its own message, e.g. an unknown monitoring patch),
with `"outDir": null`. The exit code is 0 when `status` is `succeeded`, 1
otherwise.

Examples (from `src/`, the SingleRack case copied to a scratch folder first;
its saved project solves on one rank, so a solve needs `--solve-processes`):

```bash
python -m agent.scripted_api air ../_tmp/X/SingleRack/SingleRack.cfdproj --mesh-only --mesh-processes 2 --max-processes 8
python -m agent.scripted_api air ../_tmp/X/SingleRack/SingleRack.cfdproj --solve-only --solve-processes 2 --end-time 20 --max-processes 8
python -m agent.scripted_api air ../_tmp/X/SingleRack/SingleRack.cfdproj --mesh-processes 4 --solve-processes 4 --end-time 20 --max-processes 8
python -m agent.scripted_api air ../_tmp/X/SingleRack/SingleRack.cfdproj --mesh-processes 4 --solve-processes 4 --end-time 1500 --max-processes 8
```

The two 20-iteration runs only check that the pipeline goes through: they are
far shorter than the return-air verdict needs, so `metrics.convergence.verdict`
is `INCONCLUSIVE` with the reason `too-short: 20 iterations < minimum 100 ...`
(see [Convergence verdict and the early stop](#convergence-verdict-and-the-early-stop)).
The last line is a run to judge: with `--end-time 1500` as the cap the runner
stops the solve once the return air is steady (SingleRack on 4 ranks, under the
2026-10-22 rule with its 1000-iteration minimum: CONVERGED, stopped a little
after iteration 1000, at 288.37 K return air; since 2026-10-27 there is no such
minimum and a solve can stop earlier).

Liquid, hybrid and modelling examples (again on scratch copies of the cases):

```bash
python -m agent.scripted_api liquid ../_tmp/X/LiquidSingle_Control/LiquidSingle_Control.cfdproj
python -m agent.scripted_api liquid ../_tmp/X/LiquidSingle_Control/LiquidSingle_Control.cfdproj \
    --mode transient --duration 10 --pump-rpm m_1_pump=0.8
python -m agent.scripted_api hybrid ../_tmp/X/Shinaide_Liquid/Shinaide_liquid.cfdproj \
    --mesh-processes 4 --solve-processes 4 --end-time 30 --max-processes 8
python -m agent.scripted_api build ../_tmp/X/room.json --out ../_tmp/X/MiniRoom/MiniRoom.cfdproj
python -m agent.scripted_api air ../_tmp/X/MiniRoom/MiniRoom.cfdproj --mesh-only --mesh-processes 2 --max-processes 8
```

The 30-iteration hybrid run checks the liquid session, the heat handoff and the
air solve end to end; its convergence verdict is `INCONCLUSIVE` (too short).
Give it `--end-time 1500` or more for a verdict.

`export-air` writes `<name>.cfdrun` and `triSurface/` side by side, or with
`--case-layout` a case the server runs as is: `model/<name>.cfdrun` and
`Mesh/constant/triSurface/*.stl`. Its summary lists `blockers` — `manifestErrors`,
`preflight:<check>` for every pre-flight dialog the editor stops on, and
`hybrid` when the air solve needs a liquid session first — and the exit code
is 1 whenever that list is not empty (the files are written either way).

### Batch

```json
{"maxParallel": 2, "maxProcesses": 8,
 "items": [{"cfdproj": "A/A.cfdproj", "meshProcesses": 4, "solveProcesses": 4, "endTime": 20},
           {"cfdproj": "B/B.cfdproj", "stages": ["mesh"], "meshProcesses": 4, "label": "b-mesh"}]}
```

(The 20-iteration item checks the pipeline only; its verdict is `INCONCLUSIVE`.)

`batch.json` is a list of items or an object with `items` (relative paths
resolve against the file's folder). An item's `kind` is `air` (default),
`hybrid` or `liquid`. An air item is one `run_air_case` and a hybrid item one
`run_hybrid_case`, each with its own private server and port pair, so two of
them may not share a case; a liquid item is one in-process `run_liquid_case`
and may share a case with anything. At most `maxParallel` items run at once
and each gets `max_processes // max_parallel` of the budget: MPI ranks for an
air or hybrid item (one whose project asks for more is refused at its `input`
stage — set its `meshProcesses` / `solveProcesses`), torch threads for a liquid
item (its `threads` may not exceed the share). Keys, checked per kind before
anything runs:

| Kind | Keys |
|---|---|
| every kind | `cfdproj` (required; a liquid item may name a `.liquidrun`, whose result goes under the batch directory), `kind`, `label` (default `batchNN`) |
| `air`, `hybrid` | `caseDir`, `stages` (a hybrid item must solve), `solverType`, `runSettings`, `meshEngine`, `meshProcesses`, `solveProcesses`, `endTime`, `allowSerialSolve`, `acceptSeedCollisions`, `requireCheckMesh`, `meshTimeout`, `solveTimeout`, `maxProcesses` (at most the per-job share), `app` |
| `hybrid` also | `sampleIndex`, `liquidTimeout`, `liquidDevice`, `liquidThreads` |
| `liquid` | `mode`, `inputs`, `durationS`, `objectives`, `gradientParameters`, `heatMultiplier`, `device` (default `cpu`), `threads`, `fdCheck` |

The batch
writes `<out-root>/<YYYY-MM-DD_HHMMSS>_SGT__batch[_label]/` with the
normalised `batch.json` and `batch-result.json` (format
`dcdtuni.api.batch-result/v1`: status, per-item kind, status, failure and the
path of the item's own `result.json`); it never deletes or overwrites an earlier
directory. One failed item fails the batch status but not the other items.

## Liquid: modes, inputs and the network

`mode="steady"` (default) answers the liquid session's equilibrium query and
differentiates it (see [Liquid result](#liquid-result-metrics-backendapiliquidpy)).
`mode="transient"` marches the run through `runtime_batch.run_dae_manifest`,
the batch path `/api/liquid/solve` takes for a DAE manifest, and writes
`<run dir>/log/run-<id>/` with `input.liquidrun`, `executed.liquidrun`,
`result.json`, `executed-inputs.json` and `checkpoint.json` — the directory
`hybrid_air_case_cli --liquid-run` takes. For a `.cfdproj` the liquid side is
compiled in the requested mode through the editor's
`setSimulationMode(mode, "liquid")`, only when it differs from the saved mode,
and `duration_s` sets the timeline length through the timeline strip's own
duration edit (a keyframe past the new end is refused, not trimmed). The steps
are the experiment's `dt_s`. A `.liquidrun` source keeps its own time grid;
`mode="transient"` needs `analysis.mode = transient` and `N_t ≥ 2`.

`inputs = {"pump_rpm_ratios": {edge: r}, "valve_openings": {edge: o},
"primary_inlet_K": {hx edge: T}}` (CLI `--pump-rpm`, `--valve-opening`,
`--primary-inlet-k`, each `EDGE=VALUE` and repeatable) is ONE command given to
`DAESimulation.set_inputs` — the call behind the server's
`POST /api/liquid/runs/<id>/inputs`, with the same validation (unknown edge,
flow-held pump, a valve whose opening would change the active set). The
command is queued for the next accepted step:

- transient: it applies from sample 1 (t = dt) on and is recorded in the run's
  `applied_inputs` and executed manifest; a `heat_multiplier` other than 1 is
  queued with it;
- steady: the answer is the equilibrium with the command pending, the server's
  `inputs` then `equilibrium`; the gradients are taken at those inputs. That
  needs a run with a next step, so for a `.cfdproj` the API compiles the liquid
  side as transient for it (the equilibrium does not depend on the horizon),
  and a steady-only `.liquidrun` with inputs is refused with that reason.

Both modes report the network, built from the result topology fields
(`postprocess/topology.py`: `edge_from`, `edge_to`, `edge_types`, `edge_D_m`,
`node_positions_m`, plus `node_observable` and `edge_stagnant`):

| `edges[]` field | Meaning |
|---|---|
| `id`, `kind`, `from`, `to` | the liquidrun edge and its declared endpoints (node ids) |
| `massFlowKgS` | signed: positive from `from` to `to` |
| `direction`, `upstream`, `downstream` | the actual flow, `upstream->downstream` in node ids; `none` on a stagnant edge (a dead end or a branch closed off by a valve, whose flow is a round-off residue) or an exactly zero flow |
| `velocityMS`, `diameterM` | speed \|ṁ\|/(ρ·πD²/4) along the direction, for pipes and valves; `null` elsewhere |
| `outletTemperatureK` | temperature of the coolant leaving the edge in its flow direction |
| `observable` | false on a stagnant edge |

`nodes[]` holds `id`, `pressurePa`, `temperatureK`, `positionM` and
`observable` (false where the node has no inflow and no prescribed temperature,
so its temperature is only a numerical gauge). A steady result has them under
`metrics.metrics`; a transient result has them at the final sample under
`metrics`, next to `final` (`seq`, `timeS`), `samples`, `timeS`, `appliedInputs`,
final chip, primary-return and secondary-supply temperatures,
`peakChipTemperatureK` over the run, the final energy balance and
`maxAbsEnergyBalanceW` over the run. The full series stay in the run's own
`result.json`.

## Hybrid air/liquid

`run_hybrid_case` (CLI `hybrid`), and `run_air_case` for a hybrid project's
solve, do in ONE private server what the editor does in one session:

1. Export the air case, the `.liquidrun` and the hybrid project headless — the
   last is `cfdrunExport().canonicalProjectForHash(project)` (operation
   `hybrid-project`), the `project` of the editor's `hybridHeat`. All three come
   from the same document (the same run settings and air mode), because the
   server checks one content hash against the air manifest's `sourceProject`
   and the liquid run's `source_project`.
2. Refuse at `input` what the editor refuses (`run/runSubmit.ts`): liquid
   servers with fan flow but no v2 fan budget ("Declare a fan hybrid IT
   budget…"), and a v2 budget whose `runDefaults.liquid.inheritResults` is not
   true.
3. `POST /api/liquid/runs {liquidrun, controllers}`, `start`, poll until
   `completed` (`liquid_timeout`, default 3600 s; cancelled past it). The posted
   run executes on `liquid_device` (default `cpu`; `None` / `--liquid-device
   project` keeps the saved device) with `liquid_threads`; only the run's
   `execution` block changes. A refusal or a failed run fails the `liquid`
   stage before any air job.
4. Mesh without `hybridHeat`, unless `stages=("solve",)` (`--solve-only`) reuses
   the case's mesh: the runner records a hybrid project's mesh source in
   `hybrid-mesh-source.json`, and its STL hashes must be the batch exported now.
5. Solve with `hybridHeat = {project, liquidRunId, sampleIndex}`;
   `sampleIndex` defaults to the run's latest accepted sample, as the editor
   sends, and an index outside the run fails the `liquid` stage. The server
   compiles the heat into the air manifest and writes `model/heat-ledger.json`.
6. Record the ledger (`metrics.heatLedger`, copied to the run directory), the
   liquid network at the handed-over sample (`metrics.liquid`, the same
   `edges[]` / `nodes[]` as a liquid result), and read the air fields against
   the manifest the solver ran (the server's hybrid compile).

`result.kind` is `hybrid`; `stages.hybridExport` and `stages.liquid` (run id,
run directory `<case>/Liquid/log/run-<id>/`, status, samples, sample index)
are added to the air stages.

## Headless modelling (`build`)

`build_project(steps, out=..., base=None)` (CLI `build spec.json --out X.cfdproj
[--base Y.cfdproj]`) runs the editor's automation contract in JSDOM
(operation `build-project`, `projectBuilder.ts`) on `base` — loaded as the
editor opens a file, so a v4 file is migrated on the way in — or on a fresh
`newProject()`, and writes the saved bytes. A spec is a list of steps or
`{"steps": [...], "base": "Y.cfdproj"}`:

```json
[{"verb": "setProjectMeta", "args": [{"name": "MiniRoom", "inputUnit": "m", "roomTemperatureC": 22}]},
 {"verb": "applyRoom", "args": [[[0, 0], [4, 0], [4, 4], [0, 4]], 3]},
 {"verb": "createDefaultRackModel"},
 {"verb": "placeModel", "args": ["$2", {"name": "Rack_1", "location": {"x": 1.7, "y": 0, "z": 1.4}}]},
 {"verb": "addRackServerAtRange", "args": ["$2", 10, 4, "fanServer"]},
 {"verb": "updateRackServerCfd", "args": ["$2", "$4", "power", 3000]},
 {"verb": "updateRackServerCfd", "args": ["$2", "$4", "volumeFlowRate", 0.25]},
 {"verb": "createDefaultAcuModel"},
 {"verb": "placeModel", "args": ["$7", {"name": "ACU_1", "location": {"x": 0.4, "y": 0, "z": 3.1}}]},
 {"verb": "addPatch", "args": ["$7", "front", "rect"]},
 {"verb": "fillFacePatch", "args": ["$7", "$9"]},
 {"verb": "updateAcuPatchPhysics", "args": ["$7", "$9", "role", "supply"]},
 {"verb": "updateAcuPatchPhysics", "args": ["$7", "$9", "temperature", 18]},
 {"verb": "updateAcuPatchPhysics", "args": ["$7", "$9", "volumeFlowRate", 1.0]},
 {"verb": "addPatch", "args": ["$7", "top", "rect"]},
 {"verb": "fillFacePatch", "args": ["$7", "$14"]},
 {"verb": "updateAcuPatchPhysics", "args": ["$7", "$14", "role", "return"]},
 {"verb": "setMeshConfig", "args": ["locationInMesh", {"x": 3.2, "y": 1.5, "z": 0.6}]},
 {"verb": "setSolverConfig", "args": ["parallelSolve", true]},
 {"verb": "setSolverConfig", "args": ["solveProcess", 2]}]
```

- A string argument that is exactly `"$N"` is what step N returned (a create's
  uid, `addPatch`'s patch id, `addRackServerAtRange`'s server uid, the object
  `applyRoom` returns); `"$$…"` is a literal string with one `$` removed.
- Only the verbs that edit or read the document are open: creation
  (`createDefault*Model`, `addModel`, `duplicateModel(s)`, `removeModel(s)`),
  air pipes, placement and the room (`placeModel`, `renameModel`,
  `setGeometryComponent`, `setModelLocation`, `setRotationDegrees`,
  `setRoomPolygon`, `applyRoom`, `replaceRoom`, `setProjectMeta`, …), patches
  (`addPatch`, `fillFacePatch`, `setPatchGeometry`, `updateAcuPatchPhysics`),
  rack servers and cooling (`createRackEquipmentAtSlot`, `addRackServerAtRange`,
  `updateRackServerCfd`, `setModelCooling`, `setCduLiquidParam`, …), liquid
  routes, bores, valve openings and pump speed (`setPipeSegmentPoints`,
  `setPipeDiameter`, `setValveOpening`, `setCduPumpSpeed`), run
  configuration (`setSolverType`, `setSolverConfig`, `setMeshConfig`,
  `setRunConfig`, `setNestedRunConfig`, `setSimulationMode`, refinement zones,
  sensors) and read-backs (`allModels`, `getProject`, `rackServers`, `modelBounds`,
  `equipmentPortFrames`, …). The
  scene, view, selection, server and history verbs are closed
  (`buildGeometryData`, `applyViewPreset`, `select`, `runMeshGeneration`, `undo`,
  …). The full list is `BUILD_VERBS` in `projectBuilder.ts`; every verb of the
  contract with its signature, whether `build` may call it and one line from its
  source comment: `python -m agent.scripted_api.contract_verbs --markdown` (generated
  from `contract.ts`, `projectStore.ts` and `BUILD_VERBS`; the same table is the
  appendix of `.claude/commands/dc-builder.md`). A step list can also be
  generated, e.g. from a Builder's `dc-params.json`:
  `params_to_steps` in `docs/manuscript/paper_experiment/batch_cfd_pipeline.py`.
- `applyRoom(plane, height)` is the 2D room editor's Apply: walls, a floor and
  a ceiling that are ROOM BOUNDARIES, in one undo step, replacing any earlier
  shell. Floors from `createDefaultFloorModel` are interior sheets (createBaffles
  wall baffles with air on both sides); placed at y = 0 they have no fluid
  below and the runner cannot seed them, so the room does not mesh.
- A step fails when the verb throws, returns `false` / `null` / `{error}`
  (`createRackEquipmentAtSlot`: anything but `"ok"`), or the store refuses its
  edit; the build stops there, writes nothing and reports `failure = {stage:
  "build", step, verb, reason}`. An existing `out` is replaced only with
  `overwrite=True` (`--overwrite`).
- After the steps the saved bytes go through the editor's air export as a
  self-check: `selfCheck = {ok, manifestErrors, preflightStops, seedCollisions,
  stlFileCount, boundaryRoles, cfdrunFilename, error}`; `ok` is false on a
  schema error, a pre-flight stop or an export refusal (the file is still
  written). The CLI exits 0 only when the build succeeded and `selfCheck.ok`.
- The report (format `dcdtuni.api.build/v1`) lists every step with its
  resolved arguments and result, the output's SHA-256, and the bundled frontend
  sources with their SHA-256.

## Environment

| Need | Located by |
|---|---|
| Node.js | `$DCDTUNI_NODE`, else `node` on `PATH`, else the newest `~/.local/opt/node-v*/bin/node`. The editor's `engines` (`src/frontend/react/package.json`) require `"node": "^24.12.0"` and npm ≥ 10 — use that for `npm ci` and the editor build. The headless compiler only bundles and evaluates over the installed `node_modules` (esbuild, JSDOM); it has also run on Node v20.19.4 on this host (`python -m agent.scripted_api identity` prints the Node it finds) |
| esbuild, jsdom, three (the editor's `node_modules`) | `$DCDTUNI_HEADLESS_NODE_MODULES`, else `src/frontend/react/node_modules` |
| dcCartMesh / `buoyantBoussinesqSimpleFoam_new` | the runner's own rule: the repo build under `third_parties/*/platforms/` (`deploy.sh`), else `PATH`. A fresh worktree has no build; run `WM_NCOMPPROCS=<n> ./deploy.sh` in both `third_parties/dcCartMesh` (`--cart-only` is enough) and `third_parties/foam` |
| pyvista / torch | the `dcdtuni` environment (`torch` from the `liquid` extra) |
| the `gpuNewton` solver engine (optional) | `src/backend/aircooling/gpu_solver` and the `dcdtuni` environment with the `gpu-solver` extra (warp-lang, pyamg, h5py next to torch, scipy and torch-geometric). `$DCDTUNI_GPU_SOLVER_GPU` names the CUDA devices it may use (default: any idle card), `$DCDTUNI_GPU_SOLVER_THREADS` its CPU threads (default 4), `$DCDTUNI_GPU_SOLVER_STANDBY` the stand-by seconds of its service (default 600). See "Solver engine" below |

## Solver engine (`solver_engine`, `--solver-engine`)

The steady air solve has two engines. `openfoam` is the default and what every
run without the option uses: `mpirun ... buoyantBoussinesqSimpleFoam_new`.
`gpuNewton` (`src/backend/aircooling/runner/gpu_newton.py`) hands the same
prepared run directory to this project's GPU Newton-Krylov solver
(`src/backend/aircooling/gpu_solver`, a differentiable finite-volume solver),
which runs on one GPU in the same conda environment:

```bash
python -m agent.scripted_api air X.cfdproj --solve-processes 12 --max-processes 12 --solver-engine gpuNewton
DCDTUNI_GPU_SOLVER_GPU=1,2 python -m agent.scripted_api air X.cfdproj --solve-processes 12 --max-processes 12 --solver-engine gpuNewton   # on GPU 1 or 2
```

- **Offered by a `--gpu` launch only.** A server accepts `solverEngine:
  "gpuNewton"` only when `start_server.sh` was given `--gpu` (all cards) or
  `--gpu <list>`; `/api/server-info` reports it as `gpuSolver: {enabled,
  devices}` and the editor's solve page then shows the engine choice. The
  scripted API starts its private server with `--gpu` (the cards of
  `DCDTUNI_GPU_SOLVER_GPU`, else all) whenever a run asks for the engine.
  With several cards a solve takes the idle one with the most free memory.

- **Same chain.** Export, mesh, run directory, dictionaries and boundary
  conditions are generated as for OpenFOAM, and the manifest bytes are the
  same, so a mesh one engine accepted is reused by the other (`--solve-only`).
  The solver writes what the OpenFOAM solver writes: its log lines (in the job
  log), the `postProcessing` monitors of the ACU patches and the probes, and
  the final time directory of every partition with explicit boundary values.
  The execution receipt, the sensor harvest, `convergence.json` and
  `metrics.fields` are produced by the same code from those files.
- **Same stop rule.** The runner writes the project's rule into
  `<run>/gpuNewton/request.json` (`stopToleranceK`, `energyBalanceTolerance`,
  the 100-iteration window, the declared heat); the solver applies it every
  iteration and also honours the steady-stop watcher's `stopAt writeNow`. The
  verdict is `assess_run` on the written files, as for OpenFOAM. An iteration
  is a pseudo-time step of the coupled system, not a SIMPLE iteration, so
  iteration counts of the two engines are not comparable.
- **Scope.** Steady solves of a partitioned dcCart mesh with more than one
  solve partition (the partitions are only read, the solve itself runs on the
  GPU); refused before the solve for a transient case, a serial solve or
  `ai_init`. A boundary condition the solver does not implement ends the solve
  stage with that reason in the job log.
- **Memory.** One solve runs on one card and needs about 0.8 GiB + 18 GiB
  per million solved cells there, 25 GiB per million for meshes up to 0.5
  million cells (measured on halls of 0.10-1.16 million cells; a 24 GB card
  holds about 1.2 million). The solver compares that with
  the free memory of its card right after it has read the mesh and refuses a
  mesh that does not fit; an allocation failure inside the solve is reported
  the same way: `errorCode: "gpu_out_of_memory"` in
  `<run>/gpuNewton/result.json`, and the solve stage fails with a line that
  asks for a restart of `start_server.sh` with more cards, or cards with more
  free memory, after `--gpu` (for the scripted API: other cards in
  `DCDTUNI_GPU_SOLVER_GPU`). `result.gpu` of a finished solve records the peak
  device memory. `GET /api/gpu-solver/status` is the live reading of every
  card of the launch that the editor's solve page shows.
- **Result.** `inputs.solverEngine` and `stages.solve.solverEngine` name the
  engine; `identity.tools.solverEngine` is the vendored solver's source commit
  (`gpu_solver/VENDORED.json`) and its interpreter. `stages.solve.solver` (the
  OpenFOAM binary) is `null` for `gpuNewton`. The solver's own timing is in
  `<run>/gpuNewton/result.json`, everything it printed in
  `<run>/gpuNewton/engine.log`.
- **Solver service.** Starting the solver process costs more than a solve
  on a loaded host, so a loaded process stands by: as soon as the solve is
  bound to its mesh (its `meshSha256`) the runner announces that mesh, which
  starts a service on one of the allowed cards when none stands by; it loads
  and reads the mesh while the run directory is prepared, takes the solve,
  and its successor stands by for the next one for
  `DCDTUNI_GPU_SOLVER_STANDBY` seconds (default 600; `0`: no service, every
  solve loads the solver afresh). The request written to
  `<run>/gpuNewton/request.json` names the mesh identity, and a solve whose
  identity differs reads the run directory's mesh itself. Sockets and the
  service log are in `.codex-tmp/gpu-solver/`; a service ends on its own
  after the stand-by time (`python
  src/backend/aircooling/gpu_solver/entries/dc_foam_solve.py --stop-service`
  with the same `LPCNS_DC_SERVICE_DIR` and `CUDA_VISIBLE_DEVICES` ends it at once).
- **Solver development.** `DCDTUNI_GPU_SOLVER_ROOT` (a solver checkout) runs
  another copy of the solver in place of the vendored one, with the same
  interpreter (one `dcdtuni` environment per machine); `tools/vendor_gpu_solver.py`
  refreshes the vendored copy from such a checkout.
- **L0 meshes.** A manifest with `meshConfig.coarsenLevel = "L0"` is solved by
  the zonal pressure-enthalpy network of that mesh (`entries/dc_zonal_solve.py`
  of the vendored solver), not by the RANS solver: `convergence.json` is its
  Newton certificate (`gpuNewton/zonal/result.json`), the fields `T`, `p_rgh`
  and `U` (the flux-to-cell velocity of the network's flows) are at time 1.

## Result (`result.json`, format `dcdtuni.api.result/v1`)

| Key | Content |
|---|---|
| `status`, `failure` | `succeeded` / `failed`; `failure = {stage, reason}` with stage `input`, `export`, `preflight`, `liquid` (hybrid), `mesh`, `solve` (a server refusal of that job included), `metrics` or `internal` (with traceback) |
| `format`, `runDir`, `caseDir`, `startedAt`, `finishedAt`, `elapsedSeconds` | `dcdtuni.api.result/v1`; this result's directory and the case; start and finish (ISO, SGT) and wall time of the whole call |
| `kind` | `air`, `hybrid` or `liquid`: what ran (the directory name carries the command, see above) |
| `inputs` | source path and SHA-256, effective-project SHA-256, run settings, `stages`, `solverType`, `monitoring` (the panel request), `allowSerialSolve`, limits and the server `app` |
| `identity.repository` | dcdtuni commit, branch, `dirty`, changed tracked files, untracked source files (and untracked links, e.g. a `node_modules` symlink, which do not count as dirty) |
| `identity.frontend` | Node version, `node_modules` path and package versions, SHA-256 of every bundled frontend source file |
| `identity.tools` | mesher and solver binaries actually used (from the job log) with SHA-256; OpenFOAM version and build line |
| `identity.python`, `identity.host` | interpreter and library versions; host |
| `stages.*.seconds` | wall time per stage that ran (`export`, `meshReuse`, `hybridExport`, `liquid`, `mesh`, `solve`, `metrics`); the server's is `stages.server.startupSeconds` |
| `stages.export` | manifest SHA-256, schema errors, pre-flight findings, `processes` (engine, ranks per stage), `simulationMode` (steady/transient as exported), `monitoring` (the panel request and the residual, server and ACU return patches the manifest monitors) |
| `stages.monitoring` | full run only, when the new mesh lacks a monitored patch: the patches dropped (`notInMesh`) and the re-exported solve manifest |
| `stages.server` | `port`, `wsPort`, `app`, `startupSeconds` (from the launcher's start until the server answered and the login succeeded), `command` (the private auth directory masked); `stop` (ports, `signal` per freed port, refused ports, launcher return code) |
| `stages.mesh` | job id, return code, `checkMeshOk`, `failedChecks`, `meshWarnings`, cell count, engine and mesher, mesh-quality receipt |
| `stages.meshReuse` | solve-only: the mesh's quality receipt (its verdict is reported, never required) and the STL-batch comparison it passed |
| `stages.hybridExport`, `stages.liquid` | hybrid: the project content hash, budget and liquid run compiled, the execution overrides; the liquid session's run id, run directory, status, samples and the sample handed over |
| `metrics.heatLedger`, `metrics.liquid` | hybrid: the server's `heat-ledger.json`; the liquid network at the handed-over sample |
| `stages.solve` | job id, return code, ranks, the server's `solveAssessment`, the solve run directory |
| `metrics.convergence` | the runner's verdict from `convergence.json`: `verdict`, `reason` (why it is not `CONVERGED`, e.g. `too-short: ...`; `null` when it is, and for reports written before 2026-10-22), `rule`, the residual leg (diagnostic: `passed`, `reasons`, `iterations`, final initial residuals) and the return-air leg `sensors` (`passed`, `status`, `reasons`, `iterations`, `minimumIterations`, `samples`, `windowSamples`); see below |
| `metrics.sensors` | every `sensorResults/*.json` the runner wrote |
| `metrics.fields` | see below |
| `artifacts` | `resultJson`; `manifest`, the exact manifest sent (and `solveManifest` when the solve's differs, see `stages.monitoring`); `stlIndex` (`stl-index.json`: name, bytes, SHA-256); `meshJobLog`, `solveJobLog`, `serverLog` (`mesh-job.log`, `solve-job.log`, `server.log`, each when its stage ran); `meshDir`, the case `Mesh/`, and `solveRunDir`; hybrid: `liquidrun`, `liquidRunDir`, `heatLedger` |

### Convergence verdict and the early stop

The runner judges every steady solve by the owner's rule (2026-10-20,
`backend/aircooling/runner/convergence.py`): it is `CONVERGED` once every ACU
return-air temperature (the area average of each monitored return patch, one
sample per iteration) has moved at least 0.05 K over the run and then stays
within the stop tolerance (`simulationConfig.stopToleranceK`, default 0.5 K;
or 1 % of its excursion, whichever is larger) over the last window, peak to
peak and in drift. Residuals are reported as diagnostics and do not decide.
The window is a fixed 100 iterations (200 until 2026-10-30) (`MIN_WINDOW_SAMPLES`) and, since
2026-10-27, there is no other minimum run length (it was 20 % of the run and at
least 1000 iterations from 2026-10-22). A run of fewer than 100 iterations is
`INCONCLUSIVE`, never `CONVERGED`: `metrics.convergence.reason` (the report's
`verdictReason`) then reads `too-short: N iterations < minimum 100 (...)`,
`sensors.status` is `TOO_SHORT`, and each return's window values are still
listed as a read-out. A run with no return-air evidence is `INCONCLUSIVE` too.
Use these short runs to check the pipeline, not the physics.

**Energy balance (owner rule 2026-10-30).** A run that passes the return-air
rule is `CONVERGED` only when the energy balance closes too
(`convergence.energy_balance_verdict`, report key `energy`). The solver writes,
every iteration, sum(phi*T) and sum(phi) on every ACU supply and return patch
(function objects `heatT_<patch>` and `phi_<patch>`); the heat the ACUs remove
is rho*cp*sum(phi*T - T_ref*phi) with T_ref the flux-weighted supply
temperature. Over the last 100 iterations it must stay within
`simulationConfig.energyBalanceTolerance` (default 0.01, 1 %) of the declared
heat: server `powerW` (in a hybrid run, the air share) plus heat-flux surfaces
times their area. When the declared heat is incomplete (fixed-temperature
surfaces, volumetric sources) the ACU heat must instead vary within the
tolerance of its mean over the window, and `energy.closureUnavailable` says
why. An open balance makes the run `NOT_CONVERGED` with
`energy balance open: ...` as its reason; the early stop applies the same
verdict. Why: Alibaba-sige read `CONVERGED` at iteration 204 with every return
0.2 K above supply and the ACUs removing 2 % of the 995 kW the servers put in.
The 1 % follows the Eolios data-centre CFD white paper (mass and energy
conserved over the domain within 1 %; a balance that does not close
invalidates the computation even with fine residuals) and the Ansys Fluent
User's Guide (net imbalance under 1 % of the smallest boundary flux). Runs
solved before 2026-10-30 have no such monitors and no `energy` leg.

A steady solve whose return air is monitored (the API's default) does not
have to run to `--end-time`: while it runs, the runner's steady-stop watcher
re-reads the log and the monitors every 30 s of wall clock, applies the same
verdict over the same 100-iteration window, and on the first poll that sees
`CONVERGED` writes `stopAt writeNow;`. The solver keeps iterating between
polls, so the iteration a solve stops at depends on the host's speed and load,
not only on the case: three runs of the same deterministic SingleRack mesh on
4 ranks stopped at iterations 1044, 1067 and 1204, with return-air series
identical over the iterations they share. Their verdicts agree, but the final numbers read
at the last iteration (temperatures, energy balance) differ slightly. To
compare runs at a fixed iteration, run them with `DCDTUNI_RUNTIME_MODIFIABLE=false`
in the environment of the API call (the private server and the runner inherit
it): the watcher is then off and the solve runs to `--end-time`, or to the
project's own `residualControl`, which is counted in iterations.

### Air field metrics (`metrics.fields`, `agent/scripted_api/air_metrics.py`)

Read from the solver's written latest time, without reconstruction and
without running any OpenFOAM utility. Face fluxes are the solver's
conservative face flux `phi` and face `T`, read together from the native ASCII
time directory (per processor) so each flux is paired with its own face. `U·S`
is reported next to it (`velocityAreaFlowM3s`): at a pressure outlet such as an
ACU return (`pressureInletOutletVelocity`) the face velocity is the adjacent
cell's and `U·S` is not the conserved flux.

- `rackInlet.racks[]`: per rack and per fan-server inlet patch — area-mean,
  flux-mean, min and max face temperature; `rackInlet.summary`: hottest rack,
  max/min/mean of the rack area-means, max face value (°C).
- `acus[]`: supply and return patch statistics (temperature, outward flow vs
  declared flow) and `heatRemovedW = ρ·cp·Σ φ·(T − T_ref)` over both patches,
  `T_ref` the flux-weighted ACU supply temperature (`energyBalance.referenceTemperatureK`):
  returns are pressure outlets sharing one room, so one ACU's return can draw
  more or less air than it supplies, and absolute `φ·T` would charge it
  `ρ·cp·Δφ·T`; the total over all ACUs is the same either way.
- `energyBalance`: declared IT power (`Σ powerW` of fan-server outlets, heat-flux
  servers as `q''·A`), ACU heat removal, heat the server patches add to the air,
  and their relative differences; `ρ = 1.19`, `cp = 1005` are the runner's
  constants (`backend.aircooling.utils.const`), the ones the fan-outlet
  temperature rise uses. Heat paths not covered (fixed-temperature patches,
  volumetric source terms) are listed in `notCovered`, never estimated.
- `massBalance`: net outward flux over every physical patch.
- `fieldSource`: time read, decomposed or not, and which flux source was used.

### Liquid result (`metrics`, `agent/scripted_api/liquid.py`)

`DAESimulation(liquidrun)` admits and initializes the run exactly as a liquid
session does; `equilibrium(heat_multiplier)` gives the steady state at the
sample-0 boundary values (for a transient manifest this is the equilibrium the
session's query returns, not a time step). The API then repeats that steady
solve once with a `requires_grad` parameter vector — same guess, same
conserved-storage selection — and differentiates scalar objectives with
`torch.autograd.grad` through the implicit adjoint.
`primalConsistencyMaxAbsK` records the largest chip-temperature difference
between the two solves.

| Objective | Definition |
|---|---|
| `max_chip_temperature_K` | max cold-plate solid temperature (gradient at the active argument) |
| `mean_chip_temperature_K` | mean cold-plate solid temperature |
| `max_coolant_outlet_temperature_K` | max coolant temperature leaving a cold plate |
| `mean_primary_return_temperature_K` | mean facility-side return temperature at the CDU heat exchangers |
| `pump_hydraulic_power_W` | Σ over pumps of (p_out − p_in)·ṁ/ρ |

Gradient parameters are named slices of `EquationGraph.parameter_layout`
(default `heat_W`, `pump_rpm_ratio`, `valve_opening`, `primary_inlet_K`,
`primary_flow_kg_s`; any other layout name is accepted), each component
labelled by its liquidrun edge or node id. `fd_check=k` re-solves the steady
state at θ ± h·e_i (h = `fd_relative_step`·max(1, |θ_i|), default 1e-4; a
second-order one-sided difference from below where θ + h leaves the admissible
set, e.g. a fully open valve) for the k largest nonzero components per
objective and records adjoint, difference, absolute and relative error. The
difference solves run to a Newton tolerance of 1e-13 (rtol 0): at the product
tolerance a small perturbation is accepted at iteration 0 because the base
state already satisfies it, which once left valve-opening differences 5e-5
away from the adjoint at any step size (Shinaide, 2026-10-20).

Also returned: diagnostics (`heat_input_W`, `primary_rejection_W`,
`energy_balance_W`, residual norms), Newton reports of both solves, chip
temperature per cold plate, primary return and secondary supply per HX, rack
summaries (`build_rack_summaries`), graph sizes, execution device and threads,
timings, `configHash` and the liquidrun SHA-256, object bindings.

## Known limitations

- **Hybrid sample.** One liquid sample's heat goes to one steady air solve, as
  in the editor; a transient air run driven by the liquid time series is not
  offered.
- **Pre-flight questions.** Seed-point collisions are a question in the editor;
  the API refuses unless `accept_seed_collisions=True`. Digit names, floor
  heights, room-wall gaps and door porosity stop the run as in the editor.
- **One server per call.** The server start costs about a second per air run;
  there is no server reuse across calls (by design: one server holds one case).
- **Liquid inputs.** One command per run, queued before the first step; a
  schedule of several commands is the project's own transient tracks.
- **Headless modelling.** Only the document verbs of the contract are open, so
  anything that needs the 3D scene (a topview screenshot, geometry status) still
  goes through the editor; liquid pipe networks have no contract verb beyond
  `addModel` / `setModelCooling` / the CDU setters.
- **Air gradients.** There are none: the air side returns objectives and
  receipts only.
- **Liquid steady state.** Evaluated at the sample-0 boundary values; a valve
  opening of exactly 0 is a topology change and cannot be differentiated
  through (the active set is fixed per `EquationGraph`).
- **snappy on small cases.** With `--mesh-engine snappy` the meshes of
  `SingleRack` (2 cells with a small determinant) and `Tri_angle` (2 highly
  skewed faces) do not pass the runner's strict final checkMesh, so their mesh
  stage fails with that reason (re-measured 2026-10-22 SGT with `air --mesh-only
  --mesh-engine snappy --mesh-processes 4`). The default engine, dcCart, meshes
  both and passes the gate (`Tri_angle` is one of the release cases in
  `docs/benchmarks/air-release-cases-2026-10-21/`). The gate is the runner's,
  the same for an editor-started run.
- **CUDA on Blackwell GPUs.** Since 2026-11-16 the `dcdtuni` environment is torch
  2.7.1+cu128, whose kernels include sm_120 (RTX Pro 6000); the earlier
  2.5.1+cu121 had none there ("no kernel image" at admission of a `cuda`
  liquidrun). An environment still on 2.5.1 is updated with `setup_env.sh`.
- **Node/npm.** The headless export needs the editor's `node_modules`; a host
  where `npm ci` cannot reach the private Omniverse package can point
  `DCDTUNI_HEADLESS_NODE_MODULES` at a minimal install of esbuild, jsdom,
  three, three-mesh-bvh and ajv.
