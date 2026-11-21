### Spec Compliance

- ❌ Issues found: stable physical identity is not complete for cold plates. `src/frontend/react/src/liquid/dynamicLiquidGraph.ts:62` preserves the canonical plate object but exposes its map key only as `name`; the focused unchanged-compiler check shows `src/frontend/shared/js/liquid_graphgen.js:524` still creates every cold-plate edge as `..._cp${i}`. Inserting or reordering a plate therefore changes existing edge IDs even when every plate UID is unchanged, contrary to the brief's UID-derived identity requirement. The rename-only assertion in `src/frontend/react/test/dynamicLiquidGraph.test.ts:34` does not cover this failure mode.
- ❌ Issues found: the pump and measured-cold-plate selectors are not usable for the first experiment. The catalog starts empty at `src/frontend/react/src/components/LiquidExperimentPanel.tsx:18`, is populated only inside the same `create()` call that immediately selects the first items and submits the session at `:32-36`, and the whole fieldset becomes disabled once that session is `ready` at `:56`. Thus the controls displayed at `:67-68` cannot select a non-first pump or plate until the user cancels/completes an unwanted session and creates another one.
- ⚠️ Cannot verify from diff: none. The implementer report and added real-browser evidence cover a finite 180 s/181-frame fixed-speed run and a prepared/stepped direct-PI load-step run; those checks establish the basic single-loop path but do not exercise multi-target first-use selection or out-of-order poll completion.

### Strengths

- `src/frontend/react/src/liquid/dynamicLiquidGraph.ts:49-65` compiles a revived deep snapshot and the immutability regression at `src/frontend/react/test/dynamicLiquidGraph.test.ts:25-30` verifies that the editor project is not mutated.
- `src/frontend/react/src/liquid/liquidExperiment.ts:26-36` performs the Celsius-to-Kelvin and kPa-to-Pa/gain conversions once and binds an explicit simulated pump actuator. Focused dependency checks confirm the signs: `src/backend/liquidcooling/control.py:146-153` makes direct action increase output when temperature is above setpoint and reverse action increase output when pressure is below setpoint, while `src/backend/liquidcooling/runtime.py:417` measures target pressure minus reference pressure.
- `src/frontend/react/src/liquid/liquidRuntimeClient.ts:41-53` validates run identity and vector sizes before merging, and `:54-60` deduplicates frames by sequence while preserving server physical time. `src/frontend/react/src/components/LiquidExperimentPanel.tsx:23-24,87` retains the originating project revision and surfaces a stale-project warning.
- The new case and documentation consistently label pump/HX/valve values as generic mock data and avoid NVIDIA/CDU70 or hardware-control claims (`case/LiquidSingle_Control/LiquidSingle_Control.cfdproj:7-18`, `case/LiquidSingle_Control/README.md:1-7`, `docs/developer/react-liquid-runtime-workbench.md:3-13`). The diff adds a separate case and does not modify the original cases.
- The runtime lifecycle, result/checkpoint downloads, legacy solve entry, signed kg/s chart, K-to-°C display conversion, and physical-time chart axis are all present in the requested React surface (`src/frontend/react/src/components/LiquidExperimentPanel.tsx:38-95`, `src/frontend/react/src/components/LiquidRuntimeMonitor.tsx:14-33`).

### Issues

#### Critical (Must Fix)

None.

#### Important (Should Fix)

1. `src/frontend/react/src/liquid/dynamicLiquidGraph.ts:62` and `src/frontend/shared/js/liquid_graphgen.js:510-524` — cold-plate edge and intermediate-node identity is positional rather than UID-derived. This breaks chart/event/checkpoint identity when canonical plate ordering changes. Generate the cold-plate edge/node suffix from the plate UID (namespaced by rack/server UID), preserve display labels only in bindings, and add an insert/reorder regression proving existing plate IDs stay unchanged.

2. `src/frontend/react/src/components/LiquidExperimentPanel.tsx:18,32-36,56,67-68` — control-target selection is a display-only choice on first use because options do not exist until after submission, when the controls are disabled. Compile a preview catalog from the current project/revision before session creation, or split compilation from submission, then require the chosen pump/plate when multiple candidates exist. Add a component-level test with at least two pumps/plates that asserts the selected non-first IDs reach `buildExperiment`.

3. `src/frontend/react/src/liquid/liquidRuntimeClient.ts:80-95,102-114` with `src/frontend/react/src/components/LiquidExperimentPanel.tsx:41` — lifecycle revisions protect a poll from overwriting a newer action acknowledgment, but do not order two refreshes created after the same action. `runtimeAction(start/resume)` schedules polling and the panel immediately launches another refresh; if the first request finishes after the second, both have the same lifecycle revision and line 94 can regress a terminal/newer status to the older payload status. Keep a monotonically increasing refresh request ID (or response watermark) and permit only the newest applicable response to update status/error/connection while still merging validated frames. Add a regression where an older poll returns `running` after a newer poll returned `completed`.

#### Minor (Nice to Have)

1. `src/frontend/react/src/liquid/liquidRuntimeClient.ts:44` — frame validation accepts any nonnegative `seq` and `time_s`, even when `seq >= metadata.total_sample_count` or `time_s` disagrees with `seq * metadata.dt_s`. The current backend emits the invariant, so this is defensive hardening rather than a demonstrated runtime failure. Validate metadata and the seq/time grid together, with a small floating-point tolerance.

### Assessment

**Task quality:** Needs fixes

**Reasoning:** The basic mock runtime workbench is coherent and browser evidence confirms both fixed-speed and PI paths, but UID stability is incomplete and two user-visible/state-ordering defects violate explicit task constraints. Fixing the three Important findings should make this task reviewable as a reliable foundation for later visualization and optimization slices.
