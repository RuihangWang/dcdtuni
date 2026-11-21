### Task 1: Closed-loop runtime and session API

Owned files:
- Create `src/backend/liquidcooling/runtime.py`, `run_manager.py` and optionally a focused `runtime_results.py` helper.
- Create `src/server/liquid_runtime_api.py` and minimally route its endpoints in `src/server/server.py`.
- Create `src/backend/tests/test_liquid/test_runtime.py`, `test_run_manager.py`, and a focused API test file.
- Create tracked developer documentation with actual API requests and error semantics.

Numerical interfaces available:
- `gga_solve(graph, ...) -> P[N_nodes,N_t], m[N_edges,N_t], info` with `per_step`. Build one-time-slice graph per step; don't recompute the full horizon each tick.
- `thermal_step(graph, m, T_node_prev, T_w_prev, T_s_prev, dt=None, time_index=...) -> ThermalStepResult(T_node,T_w,T_s,edge_outlet,diagnostics)`; pure tensor kernel, no mutation. Current facility side is a prescribed thermal reservoir; do not claim full primary-loop dynamics.
- `PIDConfig`, `PIDState`, `initial_pid_state`, `pid_step` in `backend.liquidcooling.control`. Output Tensor and explicit next state, with output/derivative/integral diagnostics.
- `LiquidrunConfig` resolves versioned `parameter_pack`/refs and retains `parameter_provenance`. Hash complete resolved input + controller definitions for run identity; physical parameter hash alone is insufficient.

Controller definition on API:
`{"id": "dp", "measurement": {"kind": "pressure_difference", "target_id": "supply-node", "reference_id": "return-node"}, "actuator": {"edge_id": "pump-edge", "field": "rpm_ratio"}, "pid": {"kp": 0.00001, "ki": 0.000001, "setpoint": 30000, "bias": 0.8, "output_min": 0.2, "output_max": 1.0}, "sample_period_s": 1.0}`.
Allowed measurements: `node_temperature` (target node), `solid_temperature` (target CP edge), `pressure_difference` (two nodes), `mass_flow` (target edge, signed). Actuator field is only `rpm_ratio` on a pump or `opening` on a valve. Validate IDs/kinds, finite coefficients and duplicate actuator bindings; do not silently pick the last writer.

Public runtime class: `LiquidSimulation(config: LiquidrunConfig, controllers: list[dict] = ..., run_id: str | None = None)` with `step() -> dict`, `checkpoint() -> dict`, `from_checkpoint(checkpoint)`, `result() -> dict`, immutable `config_hash`, `run_id`, `done` and coherent latest frame. Refine constructor defaults safely, preserve these names for frontend/service integration.

Frame contract:
`{kind:"frame", run_id, seq, time_s, node_pressure_Pa:[...], node_temperature_K:[...], edge_flow_kg_s:[...], chip_temp_K:[...], edge_outlet_temp_K:[...], controls:{loop_id:{measurement,setpoint,output,saturated,rate_limited}}, diagnostics:{...}}`.
Metadata returned with creation/status includes node_ids, edge_ids, cp_edge_ids, total sample count/dt, config_hash and parameter_provenance; ordering is stable. Diagnostic arrays may remain arrays but must serialize as finite JSON. Do not alias Newton iteration count to physical time.

Runtime behavior:
1. Copy/freeze configuration, initialize hydraulic state and temperatures from actual config (fixed T boundaries respected), and create frame at t=0. Heat/flows in each later frame correspond to the same index.
2. At a control tick read previous accepted observations, evaluate PID, apply output to current cloned parameter tensors, solve current hydraulics, advance thermal state, then commit the new frame/controller state atomically. Invalid solve cannot commit a successful-looking frame.
3. Consume configured load/pump/valve schedules using actual current time index. For this initial API, require events and controller periods to lie on the configured dt grid (positive integer multiple); reject fractional off-grid events clearly rather than flooring silently. Do not claim arbitrary event timing. Reject ambiguous direct actuator events plus an enabled loop targeting the same actuator unless an explicit precedence is designed and documented.
4. Preserve initial conditions, controller states and event index in checkpoints; restore checks input/hash and reproduces uninterrupted future frames. Restoring does not reset integral/derivative state. Include sufficient history or history reference for a self-contained checkpoint to retain earlier result frames.
5. Result uses the existing `LiquidResult` JSON naming where possible, including time_s, node/edge arrays and cp_details, so existing liquid post-processing can consume it; add node_temperature_K, run identity, controls and diagnostics rather than dropping them. Derive inlet/outlet from signed flow, not geometric direction alone. Do not rewrite history by rerunning with final control values.

HTTP endpoints (all under existing auth handling):
- `POST /api/liquid/runs` body `{liquidrun: object, controllers?: [...], project_revision?: integer, checkpoint?: object}` -> 201 with `run_id`, `status:"ready"`, `config_hash`, `metadata`, latest `frame`. Capture the selected case path at creation under existing CASE_DIR_LOCK; do not accept arbitrary client file paths or later use a changed global case dir.
- `GET /api/liquid/runs/{id}` -> status, metadata, latest frame, error if any.
- `POST /api/liquid/runs/{id}/{start|pause|resume|cancel|step}` -> updated status. Start/resume may take `{speed: number|null}` where null runs as fast as possible and a positive number paces simulation seconds/wall second. Step advances one sample when ready/paused; invalid lifecycle transitions return 409.
- `GET /api/liquid/runs/{id}/frames?after=<seq>` -> frames with seq strictly greater than supplied value plus current status (bounded by finite configured horizon; don't poll busy loops).
- `GET /api/liquid/runs/{id}/result` -> current/final result with completed/failed/cancelled status explicit; `GET .../checkpoint` -> resumable snapshot.
- Unknown ID 404, malformed config 400, operation conflict 409, numerical failure recorded as failed with clear diagnostics. No stack trace as the only user message.

Manager behavior: server-side ownership independent of a browser request. Disconnecting polling does not cancel simulation. Pause/cancel accepted at step boundaries, deterministic and no background worker leak. Capture per-run input and write atomic checkpoint/result files in the selected case's `Liquid/log/run-<run_id>/`. Store unique IDs, never pick a manifest alphabetically. Single active worker is acceptable initially if busy requests are explicit; do not silently overwrite an active run.

TDD/acceptance:
- [ ] Small own pump/pipe/CP/HX fixture with load and pump schedule produces causal temperature/flow changes through real kernels (not fake plant outputs).
- [ ] A pressure PID changes pump command and tracks a reachable target; constraints/limits are respected and commands differ from open-loop baseline.
- [ ] Checkpoint/restore at mid-run reproduces remaining frames and controller state against uninterrupted run within floating tolerance; changing input hash is rejected.
- [ ] Duplicate actuator/unknown sensor/off-grid schedule/invalid inputs fail before background solve. Nonconvergence yields failed status, never a converged banner.
- [ ] Session start/pause/step/resume/cancel state transitions and concurrent requests are covered; no worker continues after cancellation acknowledgment at a completed step boundary.
- [ ] Actual HTTP JSON routing/error/status tests plus run artifact path capture: changing CASE_DIR after create cannot move that run's files.
- [ ] Tests prove no eager torch import is added to ordinary air-only server startup.
- [ ] Focused tests while iterating, affected liquid/API regressions before commit; source path verification. Do not launch legacy long suites after every edit.

Report exact interfaces, RED/GREEN, lifecycle rules, file path ownership, limitations and commit to `.superpowers/sdd/2026-09-22-liquid-runtime/task-1-report.md`. No subagents/push. Controller handles independent review and React wiring.
