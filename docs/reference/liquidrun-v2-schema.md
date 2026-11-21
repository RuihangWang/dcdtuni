# `.liquidrun` v2 contract

> **Status (as of 2026-10-21):** v2 is the runtime/PID compatibility path. New experiments in the React editor use the Newton DAE solver and compile to the strict [`.liquidrun` v3 DAE](liquidrun-v3-dae-schema.md) input (v4 when the project timeline is transient). Both paths share this document's source/derived-snapshot contract.

The editable source is `.cfdproj` v4 or v5 (`source_project.schema_version` accepts both; v5 is what the React editor saves). It owns three-dimensional geometry,
OpenFOAM air-cooling configuration, liquid topology, physical data and saved
liquid experiment settings. `.cfdrun` and `.liquidrun` are independent, derived
execution snapshots. A liquid run never becomes a second editable project.

The authoritative Python model is
[`liquidrun_schema.py`](../../src/backend/liquidcooling/liquidrun_schema.py).
The distributable [JSON Schema](../../src/backend/liquidcooling/liquidrun.schema.json)
is generated from that model. The React envelope builder and types are in
[`schema/liquidrun.ts`](../../src/frontend/react/src/schema/liquidrun.ts).
`makeLiquidrunV2` validates the exact distributed schema through AJV 2020 before
returning an exportable manifest. `assertLiquidrunV2` applies the same structural
check to an independent object and reports invalid fields by JSON path. Python
remains authoritative for profile resolution and cross-reference semantics.

## Identity and ownership

A new manifest declares `format: "liquidrun"`, `schemaVersion: 2` and
`version: "2.0"`. `source_project` records `format: "cfdproj"`,
`schema_version: 4`, the stable `project_uid`, and a display `project_name`.
`revision` and a genuine SHA-256 of the source are optional; a missing digest
must remain missing. `projectName` remains the existing runtime display field.

`object_bindings` maps stable project object UIDs to graph node and edge IDs.
Names are labels, not identity. A nested object may supply `parent_uid`;
references must exist and parent cycles are rejected. One graph element can
appear in both its server binding and its containing rack binding.

The complete `controllers` array is required, including `[]` for open-loop
runs. No separate HTTP request body is needed to reconstruct the experiment.
An explicitly supplied transport copy must equal the embedded definition after
normalizing declared PID defaults. Conflicts fail instead of choosing a winner.
Legacy v1 clients can continue sending controllers separately.

## Physics and execution

`units` explicitly declares metre, second, Kelvin, Pascal, kg/s and watt.
Physical parameter names retain their unit suffixes. Pressure can be negative
under a gauge convention; absolute temperatures, material properties and
required geometrical sizes must be positive. Valve opening lies in `[0, 1]`.
The schema rejects boolean-as-number inputs and non-finite physical values.

`execution` is explicit: `backend: "pytorch"`, `device: "cpu"`,
`dtype: "float64"`, `max_threads: 1`. The current v2 runtime uses quasi-steady
hydraulics at each step and first-order transient thermal advancement, hence
`time.bdf_order` must be `1`. The `solver` block can retain explicitly typed
legacy Adam/ODIL settings for compatibility; these do not select the v2 forward
algorithm. Unsupported settings such as a second CUDA device selector are
rejected. CPU resource limits also have to be enforced by the execution service;
the manifest alone cannot limit operating-system scheduling.

Each edge type has its own parameter model with unknown names rejected. Pump
speed, valve opening and cold-plate heat may be scalar or contain exactly
`N_t` samples. A pipe heat source may be scalar, `[n_cells]`, or
`[n_cells][N_t]`. No silent padding or truncation is allowed in v2.

Profiles are optional, embedded in `parameter_pack`, with explicit parameter
units, pack identity, revision, description and `mock`, `vendor` or `calibrated`
source. `parameter_ref` selects a same-type profile, then project edge values
override it. A missing required effective parameter fails validation. Pipe
roughness, bend-loss factor and distributed heat retain the existing documented
compiler defaults when omitted; those defaults are not evidence of calibration.
Unreferenced legacy-style free parameter dictionaries are **not** the v2 edge
contract.

After resolution, the runtime carries full numerical edge parameters and
`parameter_provenance`, including pack and effective-parameter SHA-256 digests,
profile bindings and explicit overrides. A resolved manifest has no dependency
on a file path or remote equipment library. The provenance's override object is
audit metadata; it is not a second source of solver parameters.

## Validation boundaries

JSON Schema covers structure, discriminated edge types, scalar ranges and
fixed unit/execution declarations. Python validation additionally checks:

- Unique graph, controller, rack and server IDs, and all graph/rack/binding references.
- Complete parameters after profile resolution, positive pipe route length and sample dimensions.
- Finite horizon, chronologically ordered events within the horizon, and event/control periods on the `dt_s` grid.
- Event fields appropriate to their target type, unique event keys, valid measurement/actuator references,
  one controller per actuator, and no event/controller conflict on the same actuator.
- Parameter pack unit declarations and resolved provenance hashes.

These checks happen before numerical graph compilation. They establish a
well-defined computational input, not calibration, numerical convergence,
physical realism of an unverified parameter pack, or end-to-end differentiability.

## Python entry points and artifacts

```python
from backend.liquidcooling.liquidrun_schema import (
    LiquidrunV2, load_liquidrun, parse_liquidrun, resolve_run_input,
)

validated_manifest = LiquidrunV2.model_validate(raw_v2)
compiler_config = parse_liquidrun(raw_v2)
compiler_config, controllers = resolve_run_input(raw_v2, optional_transport_controllers)
compiler_config = load_liquidrun("case/model/example.liquidrun")
```

`parse_liquidrun` returns the existing `LiquidrunConfig` compiler model while
preserving source metadata, UID bindings, execution declaration and embedded
controllers. Unknown versions cannot fall through to legacy validation.
`schema.py` is the legacy numerical adapter. Calling its permissive
`LiquidrunConfig.model_validate` directly is not validation of a v2 file.

The run manager stores the v2 `input.liquidrun` beside `input.json`,
`checkpoint.json` and `result.json` under `Liquid/log/run-<id>/`.
`input.liquidrun` contains the complete resolved input and embedded controllers;
the checkpoint also contains numerical and controller states needed for resume.

Regenerate the schema in the repository's `dcdtuni` Python environment:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 CUDA_VISIBLE_DEVICES='' \
  PYTHONPATH=src python -m backend.liquidcooling.liquidrun_schema
```

V1 is explicitly supported for old saved files and legacy clients. Its broad
`CFDBaseModel` extra-field and parameter-dictionary behavior remains confined to
that compatibility path; v1 acceptance must not be advertised as strict v2
validation.
