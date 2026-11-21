# SDD ledger — plan: docs/superpowers/plans/2026-09-22-liquid-transient-thermal.md

Goal remains full interactive liquid platform, Three.js then OV, optimising controls/layout and agent fine-tuning; this package provides coupled thermal stepping.

Preflight:
| Tasks/interfaces | Check | Finding |
|---|---|---|
| thermal kernel -> hybrid trajectory | graph/current m/previous T -> next T | Preserve legacy trajectory output; pure kernel can retain gradients independently. |
| dynamic hydraulics -> thermal | m is [edge,time], signed | Use each current slice; rebuild upstream equations. No frozen t=0 flow. |
| thermal -> runner | convergence/diagnostics | Never label a failed hydraulic/thermal step as converged based only on cold-plate loss. |
| Task 1 internally | NTU models vs storage tests | Pipe fluid and CP solid store energy; CP coolant storage and full primary loop explicitly excluded from this package. |

Prior hydraulic package complete: commits 6964e3fe and fcd1a665, 278 full-suite pass before local gradient correction, 67 focused tests after correction, independent review PASS/APPROVE. See its scoped review report. Source now includes independently developed parameter-pack foundation (fa5e83ce plus pending validation fixes), not third-party code.

Ruling: solve affine thermal equations simultaneously in the existing NTU model rather than retain traversal order — closed circulation and changing flow make a first-time Kahn order invalid — cost if wrong: solver assembly/performance refactor, protected by conservation tests.

Task 1: ready for implementation. Root concurrently owns only parameter-pack fixes and new controller/session design, not thermal owned files.
