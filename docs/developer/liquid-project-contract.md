# Liquid project delivery contract

> **Status (as of 2026-10-21).** The contract below (the delivery path, the main case and the required completion checks) still holds; the "Main integration case", "Working copies" and "Shinaide browser baseline" sections are the 2026-09-22 record. Since then:
>
> - The development branch was merged into `main`; the separate liquid-platform work tree no longer exists. `case/Shinaide_Liquid/Shinaide_liquid.cfdproj` has been edited and re-saved (now cfdproj v5), so its SHA-256 differs from the one recorded below; it remains the one active project of that case (see `case/Shinaide_Liquid/README.md`).
> - Gap 1 is closed: result discovery recognises runtime `Liquid/log/run-<id>/result.json` (`src/server/liquid_result_files.py`).
> - Gap 2 is closed in code: runtime and DAE results carry `rack_summaries` (`liquidcooling/dae_runtime.py`, `runtime.py`), and server summaries count every cold plate (`209b88519`).
> - Gap 4 is closed: experiment settings persist under `runDefaults.liquid.experiment` and are compiled from the saved project.
> - Gaps 3 and 5 (object/cell bindings of the results view, checkpoint/persistence review findings) are being reworked in the 2026-10-21 campaign together with the liquid results view; this note will be updated when that lands.
> - The production solver is now the Newton DAE; full-graph, CUDA and mixed-keyframe runs of Shinaide_Liquid are recorded in `docs/benchmarks/shinaide-liquid-*-2026-10-0*`, and its hybrid air solve converged (see the [release receipt](../benchmarks/air-release-cases-2026-10-21/README.md)).

The deliverable is dcdtuni software plus a configured `.cfdproj` that can be opened, edited, saved, recompiled and rerun. The engineering path is:

`.cfdproj -> React/shared graph compiler -> .liquidrun -> backend/liquidcooling -> run results -> React postprocessing`.

The historical DDL tree (formerly `ddl/`, removed 2026-10-22, kept at the tag `agent-pre-slim-2026-10-22`) is a source of reusable research methods, not a second project format or runtime. Differentiable physics belongs under `src/backend/liquidcooling`. Agent design operations must edit validated project objects, connections, equipment parameters or saved experiment settings in `.cfdproj`; derived graph/tensor edits are internal numerical operations and must be mapped back to the project before accepting a candidate.

## Main integration case

Use `case/Shinaide_Liquid/Shinaide_liquid.cfdproj`. Its preserved source SHA-256 is `cff178754280c22f4aacae181224c6e2292a18819841e41479fbb39aa04de662`.

The source contains 14 racks, 56 enabled liquid servers/cold plates, one CDU, two main pipe segments, 14 tee fittings and 14 flow-control fittings. The active dynamic compiler produces 187 nodes and 242 edges: 170 pipes, 56 cold plates, 14 valves, one pump and one HX. Project save/reload preserves the compiled graph in the regression test.

Server IT power totals84kW while cold-plate heat inputs total21kW. Preserve both source fields and their distinct meanings; the aggregate ratio does not imply every server has the same liquid/air split. These source/demo values are not NV calibration data. The source embeds an old remote case path; resolve the runtime output directory on the actual host without overwriting the source file merely to run a test.

## Required completion checks

- Open and round-trip the actual project through React; all component UID/port references resolve.
- Compile every enabled liquid object into supported physics, with explicit failures for unsupported objects rather than silent omission.
- Exercise forward/transient solve, conservation and timestep behavior; record real wall time separately from simulated time.
- Bind postprocessing to the same project/run/physical time and expose failures.
- Save scenario, controller and equipment-pack choices in the project; replay must not depend on unsaved React component state.
- Apply an optimization candidate as a project edit, recompile, and validate through the same backend. Save the accepted project and provenance.
- Add focused companion conditions for capabilities absent from the original project, such as valve closure, faults and controller saturation. One successful baseline run is not evidence for all modules or all operating regimes.

At the time this contract is written, the project round-trip/compiler test passes. The actual Shinaide runtime, complete postprocessing, project experiment persistence and optimization round-trip are still being verified/implemented. The tensor solver's small-network gradient tests do not replace these integration checks.

## Working copies

The user-selected Mac repository is `/Users/litianyu/Desktop/AgentWorkspac/dcdtuni`, development branch `codex/liquid-control-platform`. Linux GPU/browser integration may run from the matching commit on40901. Record commit identity when syncing, preserve the original project and existing macOS setup files, and do not treat copies with different commits as equivalent test evidence. Air cooling remains OpenFOAM; the user's Newton/JFNK integration is future work.

## Shinaide browser baseline, 2026-09-22

Mac and40901 were synchronized to `bc798177` before this check. The isolated Linux service was restarted through `src/start_server.sh --app react --dev --port8350` with the Shinaide case and one CPU thread. The original project was read through the browser file API, loaded by React, switched to liquid mode, compiled and run using the visible experiment controls. Only the in-memory case path was normalized; the source `.cfdproj` was not overwritten.

Accepted run: `30817deb6a51402b9b23f5bb8074f7e6`, status completed,10s /11frames,187nodes /242edges /56cold plates. Final chip temperatures span43.328–64.922°C. Maximum thermal energy residual was0.000355493W. These are short synthetic/demo results, not timestep convergence, steady state, site calibration or a speed benchmark. The earlier zero-step run was cancelled after login/case-path normalization; it is not counted as a successful simulation.

The actual postprocess-tab click then reproduced **“暂无液冷结果”**. The workbench monitor has results, but the ordinary engineering postprocess path does not. Concrete gaps for the next fixes:

1. `server.handle_liquid_results` and `handle_scan_case_status` recognize only `liquid_result.json` and timestamp-named directories; runtime writes `Liquid/log/run-<id>/result.json`. Unify discovery/read compatibility and sort UUID runs by actual time rather than UUID text.
2. `LiquidSimulation.result()` lacks `rack_summaries` and stable object/cell binding metadata needed by the existing results UI. Preserve partial-run lengths. The existing runner aggregation also sums serial cold-plate flows; do not copy that error into the runtime: a server's series chain carries one branch flow.
3. Postprocess tree/temperature lookup still relies on displayed rack/server names, whereas dynamic graphs correctly use UIDs. Resolve against actual result IDs. The shared pipe temperature overlay currently assigns cell blocks by mesh order; it must get physical object/cell mappings before it can be considered validated.
4. Dynamic experiment settings currently reside only in React component state. Persist time/scenario/controller and selected equipment-pack settings under the project's existing `runDefaults.liquid` structure and recompile from the saved project. Agent candidates must follow that path.
5. Existing runtime checkpoint/persistence review findings remain open and must be repaired before delivery. The Shinaide baseline does not exercise them.

Status of these gaps (checked against the code on 2026-10-22):

- **1 closed.** `GET /api/liquid/results` and the saved-run list read `Liquid/log/run-<id>/result.json` (`selected_liquid_result`, `liquid_run_catalog`); a saved run restores as a session after a restart.
- **2 closed.** A DAE result carries `rack_summaries` (one branch flow per server) and `object_bindings`, plus the network topology.
- **3 closed for the 3D display.** Pipe colouring and flow arrows map result cells to edges through `cell_edge_ids` and `object_bindings` (`liquidColourRules.ts`, `liquidFlowDisplay.ts`), not mesh order; picking resolves by uid.
- **4 closed.** The experiment is saved under `runDefaults.liquid.experiment` and recompiled from the saved project (`liquidExperiment.ts`); the scripted API compiles the same path.
- **5 open.** The checkpoint/persistence review has not been redone since; next step: re-run that review against `run_manager.py` and the restore path.

Runtime artifacts remain on40901 under `/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform/case/Shinaide_Liquid/Liquid/log/run-30817deb6a51402b9b23f5bb8074f7e6/`. Test service8350/8352/8353 and browser-use session `liquid-platform-8350` are owned by this task. Next debugging should continue from this evidence, not recreate an independent solver or claim all modules passed.
