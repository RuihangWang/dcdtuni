# src/agent — the slim agent layer on `agent.scripted_api`

`agent` is the layer a design agent (or an external research repository such as
ISPO-AgentForDesign) uses to edit a data-centre project, evaluate it at a chosen
fidelity, buy expensive evaluations under a budget, and keep a record of every
call. It reaches the platform through `agent.scripted_api` only: no private runner,
server session, JSDOM driver or liquid-solver class of its own.

Design: `docs/superpowers/specs/2026-10-22-agent-slim-layer-design.md`.
The pre-2026-10-22 tree (dated probes, gates, readbacks, the old DDL chain) was
frozen as `src/agent_legacy/` and removed on 2026-10-22 at the owner's request; it is
kept at the tag `agent-pre-slim-2026-10-22` (and the ISPO research branch). What the
trunk still compares against it -- the Shinaide codec's outcomes and the compiler's
built projects -- is frozen in `tests/fixtures/hybrid_legacy/`.

## Layout

| Package | What it holds |
|---|---|
| `agent.api` | The stable public surface, `AGENT_API_VERSION = "1.0"` (semantic versioning). Depend on these names. |
| `agent.tools` | The only platform layer. `project` (edit, query, identity, validation, liquid export, `export_air`: the air manifest and its STL batch written to a fresh directory, each file checked against the manifest's `contentHash`), `edits` (semantic edits → automation-contract build steps), `evaluate` (air admission/mesh/solve/full, hybrid, liquid steady/transient, gradients, batches), `results` (`read_result` and typed views over `result.json`), `fidelity` (`Fidelity`, `Budget`, `evaluate`). |
| `agent.records` | `RunStore` (no traversal, no symlinks, no overwrite), `RunLogger` (exclusive SGT run directories, append-only streams, physics-call ledger), `CostLedger` (wall, own CPU, reaped descendants; failures charged; C0 once), owned process groups, explicit `configure_runtime_caches(root)`. |
| `agent.optim` | HF acquisition estimators, ISPO controllers, predictable controls, four optimizers behind `optimize(oracle, space, budget, seed, logger)`, and the "register, then buy" `Acquisition` ledger. |
| `agent.policies` | Optional (`.[agent]`; the language policy also `.[agent-llm]`): numerical head, structured and language policies, joint and information losses. |
| `agent.cli` | `python -m agent {identity,edit,evaluate,optimize}`. |
| `agent.scenarios` | `air`: the air-v1 task/design types, rules, placement, energy models, metrics, objective, unit-aware tolerances, the L0 network, and the design compiler `DesignToProject` / `compile_design`. `hybrid`: the case-independent plant energy, network numerics, thermal streams, constraints and return contract, and the pump-law gate v2 (`pump_affinity`: the product's solved pump head equals N²H₀ − a·Q·\|Q\|, read back through `agent.scripted_api`; it retires the legacy v1 gates). The Shinaide hybrid task grammar (`task`: the v4 task file pinned by hash, the design codec, the named numeric head, fault contexts) and its design compiler (`compile`: `HybridDesignToProject` / `compile_design`, contract steps only; its built project equals the frozen dictionary compiler's once both pass through the editor, `test_hybrid_compile`). The three slab in-row families compile (OPEN; CAC and HAC with their leaky contained-aisle sheets; HAC with turned rows and mains re-routed from the editor's own port frames, `equipmentPortFrames`), followed by the legacy static admission on the editor's geometry (`modelBounds`). Raised-floor/perimeter designs and slot permutations stay pending dependencies of the compiler, as the task registers them. The LF oracle (`oracle`: `lf_oracle(cfdproj, action=..., out_dir=...)`, an `lf` model for `evaluate(..., Fidelity.LF)`; `lf_design` compiles first) ports the legacy candidate oracle v3: the binding of the candidate to its action, the air export (`geometry`: hash-checked solids, height slices, the server face ledger), the platform's steady liquid answer (`run_liquid_case`), the fan/air handoff (`fan_contract`: per-server air heat = registered IT − the platform's liquid heat), the zonal air network of the family (`air_network`: OPEN, CAC, HAC in one builder; `air_network_perimeter`: the raised-floor RF_PERIM_OPEN / _CAC / _HAC networks with the tile-fed supply plenum and, for HAC, the ducted ceiling return plenum; full residual ≤ 1e-10), the shared-plant energy with closed ACUs and the registered constraints. On the legacy's designs it gives the legacy's air answer (to round-off; tested at 1e-8 °C, `test_scenarios_hybrid_air_network`, and for the perimeter families on all 21 legacy perimeter runs, `test_scenarios_hybrid_perimeter_network`) and the same validity and feasibility; PUE moves only in the sixth decimal, because today's liquid solver (hydrostatics on every edge since 2026-10-22) runs the secondary pump at a slightly lower flow. `lf_oracle(..., derivatives=True)` adds oracle v2's I3 (`derivatives`): per active ACU supply temperature and flow plus the pump speed (13 controls with six ACUs; the legacy's 4-ACU point had nine), from the air network's implicit tangent and the platform's pump-speed adjoint (`solve_liquid_steady`), composed with the plant energy in PyTorch; the hard-PUE gradient is reported only where every active minimum (plant reset, common return, water-return cap) has an ordinary derivative and the flows are inside the fan catalog, otherwise the reason (e.g. six equal 20 °C set-points are a kink); the speed partial always; each block is checked against central differences (`test_scenarios_hybrid_derivatives`). On the legacy oracle v2 point (4 ACUs, `shinaide-fan-zonal-lf-20261008`) today's platform gives the pump-speed gradients 0.4 % (pump hydraulic power, 14.92 vs 14.97 W per unit speed) and 1.4 % (hottest chip, −11.69 vs −11.53 K) from the legacy's on the legacy's own `.liquidrun`, and 0.2 % / 0.03 % more on today's compile (tee law v2); the solver-side gap follows the hydrostatics law of 2026-10-22: the legacy pump rise carried no port lift, today's is ρg(H − 0.05 m), and the loop flow is 0.42 % lower, both read back from the solved states. Still pending: the perimeter design compiler (the registered v4 task leaves RF_PERIM uncompiled; the v5 task registers it, so porting `shinaide_perimeter_compiler_v3` as contract steps and moving to v5 is the next step), per-chip speed gradients (the platform's objectives give max and mean chip temperature), and layout-geometry derivatives |

## Use

From the repository root (the editable install puts `src/` on the path):

```bash
echo '[{"op": "SetServerLoad", "rack_uid": "m_7", "server_uid": "e_1", "power_w": 2500}]' > _tmp/edits.json
python -m agent identity
python -m agent edit case/SingleRack/SingleRack.cfdproj --edits _tmp/edits.json --out _tmp/X/X.cfdproj
python -m agent evaluate admission _tmp/X/X.cfdproj
python -m agent evaluate air_mesh _tmp/X/X.cfdproj --case-dir _tmp/X --mesh-processes 2 --max-processes 2
python -m agent evaluate liquid_steady case/LiquidSingle_Control/LiquidSingle_Control.cfdproj --fd-check 1 \
    --log-root _tmp/agent-runs
```

An edit list holds `{"op": "<Edit class>", ...fields}` objects. `evaluate`
prints the evaluation record and the `result.json` path and exits 0 when the
platform reported `succeeded`.

```python
from agent.api import (Budget, Fidelity, RunLogger, RunStore, SetAcuSupply, SetRackPower, edit_project,
                       evaluate, liquid_gradient)

edit_project("case/SingleRack/SingleRack.cfdproj",
             [SetRackPower("m_7", 6000.), SetAcuSupply("m_20", temperature_c=18.)],
             out="_tmp/cand-1/cand-1.cfdproj")
logger = RunLogger(RunStore("_tmp/agent-runs", create=True), "cand-1", config={},
                   identity={"model": "not used", "data": "cand-1", "seed": None, "purpose": "example"})
record = evaluate("_tmp/cand-1/cand-1.cfdproj", Fidelity.AIR_MESH, budget=Budget(max_calls=4), logger=logger,
                  case_dir="_tmp/cand-1", mesh_processes=2, max_processes=2)
record.result.check_mesh_ok, record.costs["stage_seconds"]
```

A validated air-v1 design becomes a project the same way: `DesignToProject` is an
edit whose steps duplicate the template's rack into every RACK slot, create the
in-row coolers with front supply / back return patches at their set-points,
build the contained aisle from baffles, and apply the room.

```python
from agent.scenarios.air import compile_design
from agent.scenarios.air.spec import Design, Row, Setpoint, Task

slots = ("RACK", "RACK", "IRC", "RACK", "RACK", "IRC")
design = Design("SLAB_INROW_HAC", (Row("R1", "x", 3.0, 2.0, "-", slots), Row("R2", "x", 5.4, 2.0, "+", slots)),
                containment_pairs=(("R1", "R2"),),
                setpoints=tuple(Setpoint(u, 0.7, 22.0) for u in ("R1:2", "R1:5", "R2:2", "R2:5")))
task = Task(room_x_m=12.0, room_y_m=9.0, room_h_m=3.6, n_racks=8, n_inrow_units=4)
report = compile_design(design, task, "case/agent/air-v1-inrow-s01-v2/project.cfdproj",
                        out="_tmp/hac/hac.cfdproj")   # the build report, its selfCheck, and provenance
```

## Rules the layer keeps

- Every edit is a list of automation-contract steps run by `agent.scripted_api.build_project`;
  Python never writes `.cfdproj` JSON. Switching an object off deletes it
  (`RemoveModels`); `setEnabled` only hides it in the viewport.
- `SetPumpSpeed` and `SetPipeDiameter` raise `ContractVerbMissing` until the
  contract exposes `setPumpSpeedRatio` / `setPipeDiameter`; meanwhile a pump
  speed can be run through `evaluate_liquid(inputs={"pump_rpm_ratios": ...})`.
  `SetValveOpening` uses `setModelCooling` on the valve until its own verb lands.
- Process counts are explicit; a solve is never serial; each candidate owns its
  case directory; liquid work runs on `device="cpu"` unless asked otherwise.
- Each evaluation returns the platform's own `result.json` (as a read-only
  view); nothing the platform already reports is recomputed.
- Records go to an injected root (default `case/agent/<case>/`). Importing
  `agent` or `agent.api` sets no environment variable and loads neither torch
  nor scipy.

## Tests

```bash
CUDA_VISIBLE_DEVICES= python -m pytest src/agent/tests --basetemp=_tmp/agent-tests
DCDTUNI_AGENT_SMOKE=1 python -m pytest src/agent/tests/test_smoke_platform.py   # real mesh and liquid runs, CPU
DCDTUNI_AGENT_SMOKE=1 python -m pytest src/agent/tests/test_scenarios_hybrid_lf_oracle.py   # compile + LF oracle, ~1 min
DCDTUNI_AGENT_SMOKE=1 python -m pytest src/agent/tests/test_scenarios_hybrid_derivatives.py  # platform pump-speed adjoint vs differences, oracle I3
```

The LF fixtures in `tests/fixtures/hybrid_lf/` (OPEN normal and one ACU off, CAC, HAC) are oracle runs on the
current platform, frozen as their contract, the solids the network reads and the answer (no full STL);
`DCDTUNI_AGENT_REGEN_LF_FIXTURES=<scratch dir>` re-runs and refreezes them.

`src/agent/tests/conftest.py` sets `OMP_NUM_THREADS=2` unless you set it: at one
thread per core, next to other load, the host's `user-resource-guard` sends the
run SIGTERM.

`src/agent/tests/` has no `__init__.py`: `agent.tests` is a namespace over the
frozen tests and these. Every name in `agent.api.__all__` has a contract test.
