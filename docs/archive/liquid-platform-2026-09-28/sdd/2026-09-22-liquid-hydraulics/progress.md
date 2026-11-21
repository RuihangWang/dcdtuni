# SDD ledger — plan: docs/superpowers/plans/2026-09-22-liquid-hydraulics.md

Goal scope remains full Three.js then OV modelling, coupled simulation, postprocess, controls/layout optimisation and agent fine-tuning. This package only establishes dynamic hydraulics.

Preflight inspection:

| Tasks | Shared file or interface | Finding |
|---|---|---|
| Task 1 and existing hybrid | gga_solve returns P/m [node-or-edge,time], scalar diagnostics | Shapes preserved; varying flow thermal consumption belongs to next package. |
| Task 1 and valve handler | Kv characteristic and SI equations | Single consistent definition required, no duplicated conversion. |
| Task 1 internally | time-index tests versus static compatibility | Both explicitly required. Failure aggregation must cover every time index. |

Ruling: use an external sibling SSH worktree because native app tools operate only on the Mac document workspace and source main is shared — preserves main without a housekeeping-only ignore commit — cost if wrong: move/re-register the worktree before delivery.
Baseline main 4aa0621c: 20 frontend tests and 44 targeted backend tests passed. Worker must verify imported source points to its worktree.
Task 1: ready for dispatch; implementation owns solver/newton_hydraulic.py, valve handler only if necessary, and focused new tests. No third-party solver reuse.
