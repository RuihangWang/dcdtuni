# SDD ledger — plan: docs/superpowers/plans/2026-09-22-liquid-runtime.md

Base 33ffe8b0. Full goal remains Three.js then OV modelling, coupled simulation/postprocess, control/layout optimisation and agent fine-tuning.

| Interface | Preflight check | Finding |
|---|---|---|
| runtime -> GGA | time-indexed controls versus single-step graph | Slice immutable graph each tick; no full-horizon recompute. |
| runtime -> thermal_step | current flow and prior accepted state | Signature fixed; numerical and current-monitor reviews approved. |
| runtime -> PID | explicit state and industry action polarity | Default reverse; preserve optional tracking_setpoint in checkpoints. Latest 22 tests pass; scoped re-review pending at dispatch. |
| manager -> runtime | pause/cancel/checkpoint boundary | Only commit complete solved frames; no concurrent mutation of a session. |
| API -> manager | immutable config and captured case directory | Ignore later CASE_DIR changes; no client-selected filesystem path. |
| Task internally | same wire units and identity in every output | Kelvin/Pa/kg/s/s; stable run_id/hash/frame seq. |

Task 1: ready for implementation. Root owns frontend/compiler work separately; backend worker must not edit frontend or numerical/control modules.

Review 1: four Important findings; not complete. See task-1-review.md. Fix same-case restore identity, pre-allocation checkpoint validation, history/state coherence, and persistence failures. Original implementer liquid_runtime will resume after current visual implementation task, preserving one implementation subagent at a time. No findings dismissed.
