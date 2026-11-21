# Liquid Control Platform: Dynamic Hydraulic Foundation

> For agentic workers: execute with superpowers:subagent-driven-development and test-driven-development. This is the first bounded implementation package of the larger liquid platform, not the whole goal.

Goal: make the existing dcdtuni hydraulic solver respond to actual valve and pump controls over its time grid, retaining the static baseline and reporting failures honestly.

Architecture: extend the existing GGA implementation and shared component physics in place. Preserve its public array shapes while separating a single hydraulic solve from time-grid orchestration. No third-party liquid-solver code may be read, copied, imported, or depended on.

Tech stack: existing Python 3.10 dcdtuni environment, PyTorch float64, pytest. No package installation or service changes.

Spec: local Mac `Research/LiquidCooling-DC/docs/REACT_LIQUID_PLATFORM_DESIGN_2026-09-22.md`; binding implementation boundaries repeated below for this self-contained package.

## Global Constraints

- Only own dcdtuni implementation, no DiffLiquidTwin access or reuse.
- Work exclusively at `NTU-Litianyu-40901:/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform` on `codex/liquid-control-platform`. Main checkout, other repositories and services stay untouched.
- Read original main checkout `.claude/CLAUDE.md` and `CLAUDE.local.md` for environment rules. Do not reproduce credentials from them. Unit tests are allowed directly; end-to-end mesh/solve must use the launcher/server when reached by later packages.
- Test with `PYTHONPATH=$PWD/src` and `~/miniconda3/envs/dcdtuni/bin/python`, CPU, OMP/MKL 1. Confirm imported modules resolve to this worktree.
- Red first, then implementation and green evidence. Existing static tests must remain useful; never change expected values merely to hide a regression.
- Preserve user's data; no git clean/reset, no changing branches, no force push. Commit only your files locally; controller handles remote publication. No subagents.

### Task 1: Time-dependent hydraulic solve and physical valve pressure drop

Owned files:
- Modify `src/backend/liquidcooling/solver/newton_hydraulic.py`.
- Modify `src/backend/liquidcooling/handlers/valve.py` only if needed to share consistent mathematics.
- Create `src/backend/tests/test_liquid/test_dynamic_hydraulic.py`.
- Modify existing hydraulic tests only for additive coverage or legitimate diagnostics compatibility, documenting each change.
- You may add a small solver helper module if it avoids duplicating equations; do not modify frontend, server, hybrid thermal, schema, fixtures or unrelated components without reporting the need.

Consumes: `CompiledGraph` from the current compiler, including static or [component, time] pump rpm_ratio and valve opening parameters. Read its existing ValveBatchParams and valve handler before selecting equations/units.

Produces: keep `gga_solve(graph, tol=..., max_iter=..., m_init=None, iteration_callback=None)` compatible and return P `[N_nodes,N_t]`, m `[N_edges,N_t]`, info. Each time slice reflects its own controls. Existing scalar `info['converged']`, `iterations`, `max_dm` remain; aggregate converged is true only if every computed slice converges, and add per-step diagnostics with a clear key and scalar iteration information. Do not use a small update alone to excuse non-finite or gross mass/momentum residuals.

Requirements:
1. Add valve head loss and derivative to GGA assembly, using existing Kv definitions and SI conversion exactly once. Cover linear and equal-percentage at minimum, and existing other supported characteristic consistently. Do not treat the visual valve as a pipe or ignore its opening.
2. Refactor current steady solve into a testable single-step implementation, then evaluate changing pump rpm/valve settings at every time index. Warm start from the previous converged flow. Static repeated controls can reuse one solution if all hydraulic inputs are identical; reuse must never suppress a changed control.
3. Preserve flow direction and compatible reverse-flow behavior. A fully closed valve must not silently conduct a plausible normal flow. Choose a physically explicit treatment consistent with the existing graph and return clear nonconvergence if topology cannot be solved; document the boundary.
4. Leave thermal propagation and end-to-end differentiability for the next package. Do not claim this package provides those.
5. Tests use small synthetic graphs with one reference pressure and a pump-driven loop for pump-control assertions; fixed pressure endpoints would make a pump-speed response assertion physically invalid.

TDD sequence:
- [ ] Inspect current compiled types, GGA golden tests, valve handler and units; record baseline module path and test commands.
- [ ] Add behavioral tests showing a valve's reduced opening reduces branch flow at the same driving pressure; a pump rpm schedule changes loop flow at the intended time; constant controls retain repeated static results; mass balance holds; per-step failure is not hidden by aggregate success. Select tolerances from equation scales, not arbitrary loose values.
- [ ] Run new tests on unchanged production implementation and capture expected RED failures.
- [ ] Implement the smallest coherent change satisfying the above.
- [ ] Run new tests plus existing `test_newton_hydraulic.py`, `test_newton_vectorised.py`, `test_hydraulic.py`, `test_event_timing.py`, and valve-relevant existing coverage. Run the whole `src/backend/tests/test_liquid` once before commit, reporting failures rather than modifying unrelated expected results.
- [ ] Self-review the diff, capture commands/results and boundary decisions in the report, then commit with a descriptive message.

Report to `.superpowers/sdd/2026-09-22-liquid-hydraulics/task-1-report.md`. Include RED/GREEN evidence, changed files, test count, mathematical convention, per-step diagnostics interface and remaining limitations. Final message under 15 lines with status, commit, tests and report path.
