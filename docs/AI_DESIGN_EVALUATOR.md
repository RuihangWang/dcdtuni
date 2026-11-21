# AI-Design Evaluator (platform design harness)

> **Status, 2026-10-22.** This page records the harness built on the
> `ai-design-eval` branch in September 2026; that branch has since been merged
> and removed. On 2026-10-08 the implementation moved to `src/agent/harness/`;
> on 2026-10-22 the whole pre-slim agent tree was frozen as `src/agent_legacy/`
> and then removed from the repository at the owner's request. Every file this
> page names (the harness and the DDL campaign tree `ddl/`) is kept at the tag
> `agent-pre-slim-2026-10-22`; check it out to run them. The
> `src/backend/aidesign/` forwarding shims were removed the same day, so the
> platform imports no agent code. The harness is legacy DDL infrastructure, not
> an ISPO result.
> Where each phase stands today:
>
> - **Phase 1 (liquid geometry, evaluation, 3D export): done** — the
>   phase-1b section below; `eval_suite` writes to
>   `ddl/case/benchB/E7_geom3d/loggers/<stamp>/` (Git-ignored).
> - **Phase 2 (air-side evaluator): the builder and the air oracle exist.**
>   `air_eval.py` (2026-09-16, `e744d664b`) writes a V2 `.cfdrun` and box STLs
>   from a Benchmark-B layout and drives the runner CLI (mesh-only first); the
>   fuller chain — a compiled `.cfdproj`, exported by the server with the
>   editor's geometry, then meshed and solved — is
>   `ddl/case/benchB/E8_air_eval` with three
>   fail-closed gates (equipment in the mesh, no lost probes, layouts
>   separable). `DCDesignEnv.step` still has the air stub: next step, if the
>   Gym-style environment is still wanted, is to call that oracle from `step`.
> - **Phase 3 (in-process adjoint in `DCDesignEnv`): not wired** —
>   `DCDesignEnv.adjoint()` and the topology actions still raise
>   `NotImplementedError`, a boundary kept on purpose for the legacy harness.
>   ISPO's own gradient paths live in the frozen agent code at the same tag.
> - Phase 4 (paper integration) belonged to the abandoned DDL manuscripts.

Goal: turn DCTwin into an Isaac-style data-centre design simulation environment
that the DDL agent (and any baseline) can step against, with the platform's own
liquid-cooling and air-cooling evaluators as the reward/feasibility oracle and
publication-grade 3D layout export for every designed geometry.

## Package: `agent/harness/` (at the tag `agent-pre-slim-2026-10-22`)

Before 2026-10-08 this was the package `backend.aidesign`; from then until
2026-10-22 that name was a forwarding shim, now removed.

- `spaces.py` — typed DDL action space (ASSIGN / DIAM / ADD_EDGE / BYPASS),
  slot and candidate structure identical to the paper's vocabulary.
- `env.py` — `DCDesignEnv`: `reset(task)` builds the platform geometry task
  (racks/CDUs with `position_m`, branches, diameter catalogue); `step(action)`
  mutates the typed design and solves through the platform CLI
  (`src.backend.liquidcooling.cli --from-liquidrun`), returning solved fields,
  objective, feasibility; `apply_design(assign, diam)` for one-shot policy
  decodes; `adjoint()` is the phase-3 bridge to `ddl/src/estimators.py`.
- `export3d.py` — one scene dict → three artefacts: hand-written USDA (opens in
  Omniverse/usdview and converts for the web frontend), vector isometric PDF
  render under the paper's figure rules, and an indexable scene JSON.
- `eval_suite.py` — benchmark runner: lifts frozen Benchmark-B task geometry
  from the E0c artefact, evaluates label / pathwise-final / random designs,
  logs scenes + results under `ddl/case/benchB/E7_geom3d/loggers/<stamp>/`
  (git-ignored, rsync-only).

## Phases
1. **Liquid geometry + eval + 3D export** (this branch, now): verify
   `task_from_artefact` field names against a live liquidrun config; run the
   three-design comparison on seed 900048; produce Fig. 4 candidate plates.
2. **Air-side evaluator**: wire `src/backend/aircooling/runner` (OpenFOAM) as a
   second oracle on the same placements — rack heat loads in, inlet/outlet
   temperatures and recirculation out; subset of tasks (mesh cost is real).
3. **Gym + adjoint bridge**: in-process torch solve so `step` returns the ddl
   adjoint with the reward; then the environment becomes a training backend
   (RLSF/GRPO/pathwise arms runnable against platform physics, not softlc).
4. **Paper integration**: 3-D layout comparison figure (main or supplementary),
   platform-vs-softlc agreement table, and an Evaluator card in the supplement.

## Non-goals
- No changes to frozen golden meshes or the exporter contract on `main`.
- Checkpoints/results stay git-ignored; only code, docs and case specs commit.

## Phase-1 status (first platform solve of a Benchmark-B design)

`backend.aidesign.validate` (now `agent.harness.validate`) builds a LiquidrunConfig from the frozen E0c task
(seed 900048, label design) and solves it with the platform runner.  Result:
converged (final loss 3.3e-15, 125 epochs, CPU, ~2 s).  State comparison against
the frozen twin localises the remaining mapping work:

| quantity | platform | frozen twin | rel. delta |
|---|---|---|---|
| pump power | 97.6 W | 1312.2 W | 9.3e-1 |
| max edge flow | off by 1.60 kg/s | 1.71 kg/s scale | O(1) |
| rack outlet T | off by 9.59 K | 8.71 K span | O(1) |
| recomposed J | 91.15 | 103.30 | 1.2e-1 |

Diagnosis (phase-1b work): the benchmark twin drives the loop with fixed
pressure boundaries and treats pump edges as endogenous head rises, while the
builder v1 emitted a guessed pump curve and NTU parameters; the diameter index
map (6 design slots vs per-edge pipes) also needs the topology-group audit.
The harness, not the paper, carries these numbers until they close.

## Phase-1b closed + first 3-D plate (2026-09-16)

* Twin reproduction exact: evaluate_hard on the canonical chain returns
  J = 103.295411 vs artefact 103.295411 (rel 0.0).
* Transient platform solve of the same hard config: converged, P_pump 1308.27 W
  vs twin 1312.21 W (rel 3.0e-3), recomposed J 103.256 vs 103.295 (rel 3.8e-4).
  Residual rack-outlet offset (4-7 K higher) traced to transient advection
  discretisation vs the twin's per-edge linear thermal solve; logged as
  phase-1c, does not enter J for feasible designs.
* eval_suite now evaluates label / pathwise-LLM-step-200 / uniform-random
  designs through the platform runner and exports per-design scenes
  (USDA + JSON) plus a shared-scale three-panel vector plate
  (render_plate), chip max 38.4 / 39.0 / 40.7 C; the random design shows its
  overloaded CDU in red.  Logger: case/benchB/E7_geom3d/loggers/20260916T061137Z.
