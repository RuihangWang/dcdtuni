# Liquid runtime session API

The liquid runtime advances a liquid simulation one configured sample at a time
in a server-owned session. It accepts two kinds of input:

- a strict **DAE** `.liquidrun` (schema version 3, or 4 for a transient
  timeline): the Newton DAE solver that new editor experiments use. Its HX has
  explicit primary and secondary states and the primary return temperature is
  solved, not prescribed. DAE runs reject PID controllers and add online
  inputs, independent equilibrium queries and executed-input export (below);
- a **v1/v2** `.liquidrun` with optional PID controllers (the compatibility
  path), whose HX facility side is a prescribed thermal reservoir.

Controller outputs and online commands affect only the simulation; nothing is
written to live BMS equipment. The physics of the DAE path is described in
[liquid-dae-solver-first.md](liquid-dae-solver-first.md) and the input format in
[.liquidrun v3 DAE](../reference/liquidrun-v3-dae-schema.md).

Start the application through the repository launcher:

```bash
./src/start_server.sh --app react --case-dir /absolute/path/to/case
```

Every endpoint below uses the existing server authentication. Examples assume
the launcher's port in `$PORT` (8000 by default) and an authenticated cookie in
`_tmp/dcdtuni-cookie.txt`. The example below is a v1 input with a PID
controller; a DAE run is created the same way with a v3/v4 `liquidrun` (the
editor builds it from the project).

## Create a run

```bash
curl -sS -b _tmp/dcdtuni-cookie.txt \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:$PORT/api/liquid/runs \
  --data-binary @- <<'JSON'
{
  "liquidrun": {
    "version": "1.0",
    "source": "frontend",
    "projectName": "closed-loop-demo",
    "gravity": [0, -9.81, 0],
    "fluid": {
      "rho_kg_m3": 997,
      "cp_J_kgK": 4182,
      "mu_Pa_s": 0.00089,
      "k_W_mK": 0.606
    },
    "time": {"dt_s": 1, "N_t": 60, "bdf_order": 1},
    "nodes": [
      {
        "id": "supply-node",
        "type": "pressure_bc",
        "position_m": [0, 0, 0],
        "bc": {"P_Pa": 150000, "T_K": 298.15}
      },
      {
        "id": "return-node",
        "type": "junction",
        "position_m": [1, 0, 0]
      }
    ],
    "edges": [
      {
        "id": "valve-edge",
        "type": "valve",
        "from": "supply-node",
        "to": "return-node",
        "params": {"Kv_max": 2.5, "opening": 1}
      },
      {
        "id": "pump-edge",
        "type": "pump",
        "from": "return-node",
        "to": "supply-node",
        "params": {"H0_m": 10, "a_coef": 500000, "rpm_ratio": 1}
      }
    ],
    "racks": [],
    "events": [],
    "initial_conditions": {
      "T_fluid_K": 298.15,
      "T_solid_K": 320,
      "P_uniform_Pa": 150000
    }
  },
  "controllers": [{
    "id": "dp",
    "measurement": {
      "kind": "pressure_difference",
      "target_id": "supply-node",
      "reference_id": "return-node"
    },
    "actuator": {"edge_id": "pump-edge", "field": "rpm_ratio"},
    "pid": {
      "kp": 0.00001,
      "ki": 0.000001,
      "setpoint": 30000,
      "bias": 0.8,
      "output_min": 0.2,
      "output_max": 1.0
    },
    "sample_period_s": 1
  }],
  "project_revision": 42
}
JSON
```

A valid request returns HTTP 201 with `status: "ready"`, `run_id`,
`config_hash`, metadata, and the coherent frame at sequence zero. Metadata
contains stable `node_ids`, `edge_ids`, `cp_edge_ids`,
`total_sample_count`, `dt_s`, resolved `parameter_provenance`, and the
frontend's `object_bindings` UID map when provided.

The server captures the selected global case directory while handling creation
under `CASE_DIR_LOCK`. A later `/api/set-case-dir` call cannot relocate an
existing run. Clients cannot submit a filesystem path.

## Lifecycle

The valid state transitions are:

- `ready -> running` with `POST /api/liquid/runs/{id}/start`
- `running -> paused` with `POST /api/liquid/runs/{id}/pause`
- `paused -> running` with `POST /api/liquid/runs/{id}/resume`
- `ready -> ready|completed` or `paused -> paused|completed` with
  `POST /api/liquid/runs/{id}/step`
- `ready|running|paused -> cancelled` with `POST /api/liquid/runs/{id}/cancel`
- `running -> completed|failed` when the worker reaches the horizon or a
  numerical step fails

`start` and `resume` accept `{"speed": null}` to run without pacing, or a
positive number of simulated seconds per wall-clock second. Pause and cancel
return only after the worker has stopped at a completed step boundary. An
invalid transition returns HTTP 409.

```bash
curl -sS -b _tmp/dcdtuni-cookie.txt \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:$PORT/api/liquid/runs/RUN_ID/start \
  -d '{"speed": 10}'

curl -sS -b _tmp/dcdtuni-cookie.txt \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:$PORT/api/liquid/runs/RUN_ID/pause -d '{}'
```

Polling is bounded by the configured horizon and does not own the worker:

```bash
curl -sS -b _tmp/dcdtuni-cookie.txt \
  'http://localhost:$PORT/api/liquid/runs/RUN_ID/frames?after=20'
```

This returns only frames whose `seq` is strictly greater than 20 plus the
current status. Disconnecting the polling client does not cancel the run.

## Status, result, and checkpoint

```text
GET /api/liquid/runs/{id}
GET /api/liquid/runs/{id}/result
GET /api/liquid/runs/{id}/checkpoint
```

Results retain existing liquid result field names such as `time_s`,
`node_pressure_Pa`, `edge_flow_kg_s`, `cell_temp_K`, and `cp_details`.
They add `node_temperature_K`, run identity, controls, diagnostics, and an
explicit `ready|running|paused|completed|failed|cancelled` status. Cold-plate
inlet and outlet values follow the solved signed flow direction.

A DAE result also carries the network it was solved on, so a reader needs no
second source to draw flow direction or velocity: `node_ids`,
`node_positions_m`, `node_observable` (false for a node with no defined
temperature, e.g. a dry or stagnant one), `edge_ids`, `edge_from`, `edge_to`,
`edge_types`, `edge_D_m` and `edge_stagnant` (an edge isolated by a closed
valve or a dead end; its flow is exactly zero). `edge_flow_kg_s` is signed
against `edge_from → edge_to`. `warnings` / `frame_warnings` /
`hx_ideal_heating` flag an ideal-control HX that heats and coolant outside the
fluid's liquid range. `parameter_provenance.solver_laws` names the laws the run
used (`pump_law`, `friction_law`, `hydrostatics`, `hx_models` per HX edge); a
run saved before a law was recorded gets it back on restore, listed under
`solver_laws.inferred`.

Runs saved in the case survive a server restart:

```text
GET  /api/liquid/runs                  saved and loaded runs of the selected case
GET  /api/liquid/runs/{id}/saved       a saved run's header, metadata and input receipt
POST /api/liquid/runs/{id}/restore     load it back as a read-only session (body {})
```

A GET on a run that is saved but not loaded answers 404 with `"saved": true`
and the restore path.

A checkpoint is self-contained: it includes the resolved liquidrun, public
controller definitions, accepted frame history, thermal state, event position,
and PID integral/derivative/tracking state. To resume it, send the original
`liquidrun`, controllers, and checkpoint in a new create request. The server
rejects a changed input hash. A DAE checkpoint may be sent alone (its
`liquidrun` is taken from inside it, so the bytes must be sent verbatim for the
canonical hash to match); the replay becomes a NEW run whose
`metadata.resumed_from` names the checkpointed one, so resuming in the same
case never collides with the original run's directory. The React workbench's
"Continue from checkpoint…" control does exactly this. A checkpoint `run_id` is validated against a
strict safe identifier before it can contribute to the
`Liquid/log/run-<run_id>/` artifact directory.

Each accepted run atomically writes:

```text
<captured-case>/Liquid/log/run-<run_id>/input.json          # v1/v2 input
<captured-case>/Liquid/log/run-<run_id>/input.liquidrun     # DAE input, standalone
<captured-case>/Liquid/log/run-<run_id>/checkpoint.json
<captured-case>/Liquid/log/run-<run_id>/result.json
<captured-case>/Liquid/log/run-<run_id>/executed.liquidrun  # DAE: accepted steps only
<captured-case>/Liquid/log/run-<run_id>/executed-inputs.json # DAE: parent/executed hashes, accepted count
```

`GET /api/liquid/results` and the editor's liquid results panel discover these
`run-<id>/result.json` files.

## DAE runs: online inputs, equilibrium and executed input

```text
POST /api/liquid/runs/{id}/inputs        {"heat_multiplier"?, "pump_rpm_ratios"?, "valve_openings"?, "primary_inlet_K"?}
POST /api/liquid/runs/{id}/equilibrium   {"heat_multiplier"?}
GET  /api/liquid/runs/{id}/executed-input
GET  /api/liquid/runs/{id}/heat-audit?project_uid=<uid>
```

- `inputs` queues commands for the **next accepted step**; they never edit the
  past. Only the four keys shown are accepted.
- `equilibrium` solves the same residual with zero storage derivative for the
  pending inputs (optionally another heat multiplier) and returns a separately
  labelled steady result; the running trajectory, time and state are unchanged.
- `executed-input` returns the standalone executed `.liquidrun`: authored events
  and accepted online commands materialised as explicit schedules, pending
  commands and equilibrium queries excluded. It reruns through the file runner
  without a checkpoint or the browser.
- `heat-audit` returns the liquid heat ledger the air handoff uses for the
  given project UID.

Details of the workbench that drives these requests are in
[liquid-dae-workbench.md](liquid-dae-workbench.md).

## Validation and errors

HTTP 400 covers malformed JSON/configuration, unknown measurement or actuator
IDs, duplicate actuator bindings, off-grid events/controller periods, conflicting
actuator events, invalid speed, and session limits. Events and controller
periods must be positive integer multiples of `dt_s`; the runtime does not
silently floor fractional timing.

HTTP 404 means the run ID or route does not exist. HTTP 409 means the lifecycle
transition conflicts with current state or the run/artifact identity already
exists. A numerical failure sets the run status to `failed`, retains the last
accepted frame, and provides a readable error with `last_successful_seq`.

Pre-allocation limits are configurable with positive integer environment
variables:

- `DCDTUNI_LIQUID_MAX_SAMPLES` (default 10,000)
- `DCDTUNI_LIQUID_MAX_NODES` (default 5,000)
- `DCDTUNI_LIQUID_MAX_EDGES` (default 20,000)
- `DCDTUNI_LIQUID_MAX_THERMAL_CELLS` (default 200,000)
- `DCDTUNI_LIQUID_MAX_EXPANDED_VALUES` (default 5,000,000)

The runtime checks the finite derived horizon and these limits before compiling
graph tensors.
