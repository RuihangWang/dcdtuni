# Large-case convergence, 2026-10-30

Alibaba_hangchou (10.4 M cells) and Alibaba-sige (11.2 M cells) meshed and solved to a converged verdict with the
energy balance closed. Host 40901: 24 physical cores (48 threads), meshing on 8 ranks. Raw runs are under
`_tmp/Hong_ui/log/` and `_tmp/Sige_ui/log/` on 40901 (not in git); the `result.json` of each run holds the stage times.

## Results

Settings: the `fast` preset as it stands (`limited 0.5`, no non-orthogonal correctors, upwind), porous jump relaxation
0.05, uniform initial fields, T relaxation 0.95 (the preset's 0.7 is not changed yet).

| case | solve ranks | iterations | ACU heat / declared | rack inlet mean | hottest rack mean | mesh + solve + rest |
|---|---|---|---|---|---|---|
| Alibaba_hangchou | 32 | 368 | 1137.6 / 1136.3 kW (+0.11 %) | 32.4 °C | 43.9 °C | 6.7 + 34.8 + 1 min |
| Alibaba-sige | 40 | 401 | 994.90 / 994.92 kW (−0.002 %) | 38.2 °C | 45.0 °C | 7.2 + 41.8 + 1 min |

Alibaba_hangchou has 26 fixed-temperature surfaces, so its declared heat is incomplete and the verdict used the
steadiness rule (ACU heat within 1 % over the window, 0.82 %).

Alibaba-sige from the AI initial field (428 iterations) ends at the same solution: rack inlet mean 38.19 against
38.22 °C, hottest rack 44.95 against 45.04 °C.

## Findings

| question | evidence | outcome |
|---|---|---|
| False convergence | Alibaba-sige read CONVERGED at iteration 204 with the ACUs removing 21.5 of 995 kW | energy leg in the verdict |
| Divergence at iteration 24-27 on Alibaba_hangchou | the same mesh ran clean with the doors' I near 0, or with the jump relaxed at 0.05 | porous jump relaxation 0.3 → 0.05 |
| Does 0.05 slow convergence | Shinaide 691 iterations at 0.05, 694 at 0.3, both closed | no |
| T relaxation | Alibaba_hangchou ACU heat at iteration 201: −73 kW at 0.7, 1133 kW at 0.95 | see the table below |
| `uncorrected` laplacian | Alibaba-sige diverged twice of the two runs that reached iteration 150, at one 1U gap between servers, with a 1760 m/s velocity normal to its walls; `limited 0.5` ran clean through the same iterations | keep `limited 0.5` |
| AI initial field | Alibaba_hangchou 447 iterations from it, 368 from uniform; the current model was trained on DCTS only | uniform by default on these rooms |
| Rank count | Alibaba-sige s/iteration: 24 bound 4.5, 40 5.2; Alibaba_hangchou 32: 4.6 | 24 ranks (the physical cores) |
| Solve held to the mesher's ranks | 24 `processorCyclic` patches on Alibaba-sige at 8 ranks | none after the ownership passes; global mesh unchanged |

Repartitioning from 8 to 32 ranks takes about 4 minutes (reconstructParMesh 64 s, decomposePar 2 min 48 s).

## T relaxation 0.95 on smaller cases

Uniform initial fields, 8 ranks, each case's saved settings otherwise.

| case | iterations at 0.7 | iterations at 0.95 | energy balance |
|---|---|---|---|
| Shinaide | 691 | 348 | closed, +0.50 % |
| Base_leakage | 758 | 320 | closed, −0.01 % |
| Slab | not measured | 379 | closed, +0.03 % |

## Mesh identity of the ownership passes

Global face-centre multiset and patch face counts equal the mesh before them.

| case, ranks | faces | patches | `processorCyclic` patches |
|---|---|---|---|
| Alibaba-sige, 8 | 36,735,281 | 7,094 | 24 → 0 |
| Base604, 8 | 22,854,403 | 4,336 | 0 |

## After these runs

The owner then decided: `fast` and the new-project defaults take T relaxation 0.95, the verdict window goes from 200 to
100 iterations, and non-orthogonal correction is on by default (one corrector, `limited corrected 0.5`). The runs above
used `limited 0.5` with no correctors and the 200-iteration window.

## Acceptance of the final settings

The projects as saved in `case/`, nothing overridden but the rank counts: mesh on 8 ranks, solve on 24, uniform initial
fields, whole pipeline from export to metrics. Run directories `_tmp/E2E_sige/`, `_tmp/E2E_hong2/` on 40901.

| case | cells | iterations | ACU heat / declared | window | rack inlet mean | hottest rack mean | export + mesh + solve + metrics | total |
|---|---|---|---|---|---|---|---|---|
| Alibaba_hangchou | 10.4 M | 267 | 1137.0 / 1136.3 kW (+0.06 %) | steady, 0.77 % | 32.37 °C | 43.90 °C | 20 + 401 + 1646 + 40 s | **35.2 min** |
| Alibaba-sige | 11.2 M | 297 | 993.3 / 994.9 kW (−0.16 %) | closed, worst 0.82 % | 38.16 °C | 44.89 °C | 5 + 454 + 1892 + 39 s | **39.9 min** |

Both are inside the 40-minute target. Alibaba-sige has no margin: the backend test suite shared the machine at the start
of its run, and another user's load would push it over.

The 100-iteration window ended at the same solution as the 200-iteration runs above: Alibaba_hangchou rack inlet mean
32.37 against 32.36 °C and hottest rack 43.90 against 43.92 °C; Alibaba-sige 38.16 against 38.22 °C and 44.89 against
45.04 °C.

The corrector cost about 14 % per iteration on Alibaba-sige (24 ranks: 5.1 s with, 4.5 s without, the latter bound to
physical cores) and took the local continuity error from about 1e-4 to 1e-5.
