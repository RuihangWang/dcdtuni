# Shinaide_Liquid air–liquid hybrid, full-length solve (2026-10-22)

**Result: CONVERGED by the return-air rule.** Every ACU return-air temperature
changed and then held within 0.5 K over the last window (20 % of the run); the
runner stopped the steady solve on its own at iteration 1049 of the 3000
allowed. This is the first full-length hybrid run of the case through the
scripted API; the API's earlier hybrid runs (stream B5, the same day) were
pipeline checks too short for a verdict. The editor-driven hybrid run of
2026-10-20 (`36b7af668`, [release cases](../air-release-cases-2026-10-21/README.md))
also converged.

## What ran

```bash
cd src
python -m backend.api hybrid ../_tmp/campaign_B5/cases/Shinaide_Liquid/Shinaide_liquid.cfdproj \
    --solve-only --solve-processes 16 --max-processes 16 --liquid-threads 2 \
    --solve-timeout 21600 --label long-hybrid-2026-10-22
```

- Project: a copy of `case/Shinaide_Liquid/Shinaide_liquid.cfdproj`, identical
  to the tracked file apart from `meta.casePath` (checked before the run), with
  the accepted dcCart mesh and `log/air-inputs/` the hybrid handoff measures.
- One private server (`start_server.sh --app api`). The liquid DAE session ran
  first (transient, 11 samples to t = 20 s, completed); its last accepted
  sample's heat went to the air solve as `hybridHeat`.
- Air solve: `buoyantBoussinesqSimpleFoam_new`, 16 MPI ranks, about 9 minutes.
- Monitoring: the scripted API's default, residuals plus all four ACU returns
  (`ACU_01..04_Back_patch`).
- Repository: `e00209e37` (the working tree had uncommitted changes at the time;
  `result.json` → `identity.repository` lists them).

## Evidence in this directory

| File | What it holds |
|---|---|
| `result.json` | The API's `dcdtuni.api.result/v1`: inputs and hashes, stages, the runner's convergence verdict, field metrics (rack inlets, ACU supply/return, energy and mass balance). |
| `heat-ledger.json` | The liquid-to-air heat handoff (`hybrid-air-heat-handoff/v2`), identity-verified against the liquid run and the project content hash. |

The solver and server logs stay in the case copy's run directory named in
`result.json` (`artifacts`, `stages.solve.log`).

## Readings (from `result.json`)

- **Convergence:** `metrics.convergence.verdict = CONVERGED`; the sensor check
  passed with 209 window samples over 1049 iterations. The residual check is a
  diagnostic and reports k and epsilon bounded in the window.
- **ACUs:** return air between about 23.1 and 23.7 °C for supplies at 19–21 °C.
- **Heat balance:** the heat the ACUs remove matches the air-side heat the
  servers release to within about 0.01 % (`metrics.fields.energyBalance`).
- **Rack inlets:** 14 racks, area-mean inlet temperatures between about 19 and
  22 °C.

## Next step

Rerun this from the editor's own solve button once more cases use the hybrid
workflow, so the release-case table carries a hybrid row with the same rule.
