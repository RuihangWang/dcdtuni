# Drag & Sim — development log

The Omniverse CFD demo's headline interaction: drag a box in the room, get a field for it.
This folder is the working record — what is built, what was measured, what turned out to be
wrong, and what is left.

Written 2026-08-10 against `main` @ `9cb9376` + the fixes described in
`CORRECTIONS.md` §1.

> **Status check, 2026-10-21.** The research findings and the status table below still
> hold. Re-checked against the code: requirement 3.1 (ROI-only `.vtu`/`.vti`) and 3.3
> (clip semantics on the non-AI path) are done; the layout query of requirement 2 is
> wired into the bridge (`scene.py`); the box-draw retrain probe passes its acceptance
> grid with its control (`07`). Still open, as the table says: the full retrain has not
> replaced the shipped checkpoint, and `CpdCache.todo/commit` (requirement 3.2) is still
> called by no production path. What changed is how the demo is started — see "Run".
> This folder stays in place because SCANO scripts cite its files.

> **Double-blind.** The SCANO paper is under AAAI-27 review. Both repos named here are
> private on GitHub (verified: unauthenticated `GET` returns 404 for each), so recording
> repo names, host paths and the original case name `GTC` is fine *inside this repo*. None
> of it may be copied into the public project page or any published material — the fatal
> link is the chain (private repo name + host username + paper self-citation). See the
> `anon-submission-page-guard` note.

## Where the code lives

| Piece | Path | Note |
|---|---|---|
| Demo (Kit backend + React UI) | `src/frontend/omniverse/cfd_demo/` | this repo |
| SCANO **inference** subset | `.../cfd_demo/SCANO/GTC_A3_Numerical/` | a fork of the training repo's `src/`, plus inference/experiment additions. **No training entry point.** |
| SCANO **paper + training** | `/mnt/lv/home/litianyu/mycode/Gen-FVGN-refine` | separate repo. `train.py`, `run_ddp_train.sh`, training logs, and the manuscript at `docs/Manuscript/` |

The two `src/FVdomain/HomoGraph_dataset.py` differ by ~311 additive lines (all the
`SCANO_PIVOT_*` machinery); the training repo has none of it. Any retraining work happens
in `Gen-FVGN-refine`, and the resulting checkpoint is carried back by hand.

## Ports

| Port | What |
|---|---|
| **8081** | the reference React UI — open this (start it with `--port 8081`; the launcher default is 8000) |
| **8301** | WebRTC signalling / Kit streamer. **One client at a time.** |
| **8302** | resident SCANO AI worker (HTTP). A stale process here silently serves predictions from old code. See `CORRECTIONS.md` §1.3. |

Over SSH forward 8081 and 8301.

## Run

> **Changed on 2026-10-04** (launcher rewrite `217a5113a`). The commands this log was
> written with (`scripts/start_demo.sh` starting Kit on GPU #7 *and* the AI worker on
> GPU #0) no longer apply: Kit is now started only through `src/start_server.sh`,
> defaults to GPU 0, logs to `.codex-tmp/ov-runtime/<signal-port>.*/kit.log`, and no
> launcher starts the AI worker any more.

```bash
./src/start_server.sh --app omniverse --port 8081 --gpu <kit card>     # Kit + UI on :8081

# the AI worker, started by hand on a card other than Kit's (Python 3.11 + torch)
DC_CFD_AI_GPUS=<ai card> DC_CFD_AI_PORT=8302 \
    python src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical/scripts/ai_worker.py

# stop — NEVER kill/pkill
fuser -k 8081/tcp 8301/tcp         # UI + Kit (or Ctrl+C in the launcher terminal)
fuser -k 8302/tcp                  # AI worker
```

If a previous worker still holds 8302, a new one dies with `Address already in use` and
**the old code keeps answering** — free 8302 before restarting the worker. Next step: an
explicit, separately-pinned AI-worker option in the launcher (see `cfd_demo/DEPLOY.md`).

## Status at a glance

| Requirement | State |
|---|---|
| 1. Only *simulate* the ROI | **Reached, by retraining — measured against its own control.** A 300-epoch probe with 50% box draws scores 24/24 PASS on the acceptance grid; its scatter-only control, identical but for `p_scatter`, still FAILs 8 of 24. So the box draws cause it, not the epoch budget. Cost: box draws hurt whole-domain accuracy at case700 (+42%/+30% beyond the budget effect). Not yet shipped — needs the full retrain. → `07` |
| 2. Only *read* the ROI | Backend: layout query wired and now actually used. AI side: ROI-only export wired and exact. → `01` |
| 3.1 ROI-only `.vtu`/`.vti` emission | Done, browser-verified |
| 3.2 Incremental fill without duplicate computation | **Machinery only.** `CpdCache.todo/commit/read/coverage` is tested (10/10) but called by **no production path**. → `01` |
| 3.3 Non-AI path keeps clip semantics | Done |

## What to do next

**Read `04` first.** The demo's −0.94 K headline is **5.7× short of its own training targets
and 6.6× short of physics**, and the cause is neither ROI nor the objective: the training set
has **zero cases** in [15.5, 18.5) °C, and the slider drags the base case straight across that
void. The model learned a step, not a sensitivity. Separately, the targets themselves are
unconverged by ~20% of server power, supply-correlated at −0.79.

1. **Stop presenting −0.94 K as a physical result** (`04`).
2. **Fill the 15.5–18.5 °C void first** — 12 cases × 2,500 iterations ≈ 2.4 h, about 1% of a
   full regeneration, and it tests the binding constraint (`05`).
3. **Regenerate the labels** — measured: they drift −1.11 K on continuation, more than the
   −0.94 K the demo renders, and realise only 66% of a supply move at `endTime 500`. But
   regeneration alone gives ≈1.4 K, not several kelvin — necessary, not sufficient (`05`).
4. **Relabel the shipping ROI arm** — its PASS is a case0, study-box result (`CORRECTIONS` §3).
5. Only then the ROI retrain (`03`).

## The one-paragraph conclusion

Restricting SCANO's inference to a contiguous box costs **+0.405 nRMSE** against a 0.077
fail threshold — *on the shipping checkpoint*. Retraining with box-shaped draws removes that
cost entirely (`07`); the paragraph below describes the problem that motivated the retrain,
not the current state of the art. It is not the boundary stream's fault — the complete boundary is injected
regardless of sampling and is load-bearing. The damage is on the query side: the pivot
cloud's spatial extent collapses the trunk's slice basis. Two engineering fixes were
measured; one fails, the other works but is circular. What ships is a distribution-matched
single draw that is *faster*, not ROI-local. True ROI-local inference needs retraining.

## Files here

| File | What |
|---|---|
| `01-requirements-status.md` | requirement-by-requirement state, with what is half-wired |
| `02-inference-findings.md` | every measurement, with provenance |
| `03-retraining-route.md` | the only remaining route, grounded in the training code |
| `04-demo-headline-sensitivity.md` | **why the headline is 5.7× short: a 3 K hole in the training data — read this first** |
| `06-retrain-plan.md` | the implementation plan for the retrain — 7 tasks, TDD, with the probe that decides viability in 4-6 h |
| `07-roi-retrain-result.md` | **the probe ran, and it works — with its control. Read this after `02`** |
| `05-label-convergence.md` | the labels are unconverged (measured); regenerating them is necessary but not sufficient |
| `CORRECTIONS.md` | claims made in commits/docs that the evidence does not support |
| `HISTORY.md` | commit timeline |

Related design docs: `docs/superpowers/specs/2026-08-09-roi-as-simulation-unit.md` (the
design), `docs/superpowers/specs/2026-08-10-roi-inference-verdict.md` (the verdict).
