# ISPO implementation records

`WORK_LOG.md` is the running record, updated during work. Canonical agent code is `src/agent/` (since 2026-10-22 the slim layer on `backend.api`; the earlier harness and training code was removed from `main` on 2026-10-22 and is kept at the tag `agent-pre-slim-2026-10-22` and on the research branch); active research cases are `case/agent/`; the current manuscript is `docs/manuscripts/ispo_tase/`. Mac Research/DCTwin holds ideas/plans/status, not code or manuscript mirrors.

Every run uses an immutable dated ID and records the engineering commit, model/data identity, project/compiler schema, evaluator identity, seed, HF purchase probabilities, failures and all training/physics costs before claims enter the draft.

## Current status (2026-10-21)

**Where the latest records are.** The ledgers in this directory on `main` (`WORK_LOG.md`, `CLAIM_EXPERIMENT_MAP.md`, `COMPLETION_AUDIT.md`) are the 2026-10-12 state. The research continued on the branch `origin/research/ispo-validation-20261012` on the Pro 6000 host and was **paused at the user's request on 2026-10-19**. Its tip `4026e2b95` (2026-10-20) froze the runtime records; the commit message says the research moves to a separate repository (`ISPO-AgentForDesign`, not present on this host). Start from that branch:

- `docs/research/ispo/README.md` — the navigation page (latest section first);
- `docs/research/ispo/HANDOFF_2026-10-19_USER_STOP.md` and `HANDOFF_STATE_2026-10-19_USER_STOP.json` — what is accepted, what is running or pending, and the order to resume;
- `docs/manuscripts/ispo_tase/` — the newer draft (adds the E4 comparison to the HF-only and fixed-ratio PPO baselines).

Read them without switching branches, e.g. `git show origin/research/ispo-validation-20261012:docs/research/ispo/HANDOFF_2026-10-19_USER_STOP.md`. By the handoff's own rule, research results go to that branch and are not merged into `main`.

**What is already established** (accepted evidence; the 2026-10-12 part is recorded here on `main`, the rest on the branch):

- the seven-arm, ten-seed controlled cohort and the full-controller ten-seed study;
- the native heuristic / CMA-ES / MFBO / adjoint-descent optimizer cohort and the structured E5 runs;
- six Shinaide reference families through the joint liquid/air low-fidelity pipeline, normal and single-ACU outage;
- CST16 coarse full16 wing gradients; on the branch, the dedicated CST16 4B common SFT and the native force/gradient interfaces of three representative airfoils;
- on the branch, E4 all40 accepted, all ten paired seeds improving after the structured KL-scale fix, and the controlled-2D 27B formal queue at 90 of 120 runs.

**Experiments still to complete** (the full C1–C5 / E1–E8 scope stands):

- finish the controlled-2D 27B formal queue (resolve the interrupted PPO continuation and the fine3 terminal state, run controller10) and the 120-run full-cost comparison;
- E8 final design fidelity: append the remaining sources to all 480, then the target-refinement, transient and hybrid-physics audits;
- airfoil post-training: publish the B4 data acceptance with an invalid-geometry witness, connect I2 and the cal24 collector to real reference data, run the 27B common SFT, then 4B/27B RL with held-out evaluation;
- flagship Shinaide hybrid: actual 4B sampling, point-level I3 control gradients, same-source HF, training and OOD;
- E5 deployment amortization and the E6 original queue with full cost;
- the manuscript's generalization, discussion and conclusion sections, filled from the experiments above;
- on `main`, register a new I3 pump-gate version bound to the current `dae_newton.py` identity (`bb44eb40f` added the full forward Jacobian), keeping the v1 evidence.
