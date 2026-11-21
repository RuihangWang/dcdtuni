# Current input preset and acceptance audit (read-only)

Bound to MAIN and IT89f8b48b7af94ad5f772e4a7cd2fe900486b6bdf. CPU36–39, CUDA hidden. No API, browser, mesh, solver, source, user input, Git index or process mutation.

The original current-input-preset-manifest.json and generated migration receipt supply the scope. The actual current shared migrateCfdprojToV5 loader evaluated every input, rather than treating raw JSON layout alone as proof. All ten raw/effective preset keys pass for52 listed tracked inputs and1502 generated inputs (501 GTC,1001 Slab). All1502 resulting byte hashes match the original migration receipt. MAIN case/ contains1534 inputs; none falls outside these lists. The52 contain32 case/ inputs,4 room templates and16 paper labels. All16 labels exactly preserve9559648bec051a20e22fdec7592c8459df9f010f blobs, use simulation, and have no runDefaults shadow. The other45 tracked .cfdproj files are frozen evidence/baselines/test fixtures, not added migration targets.

Reproduce the effective field check without product execution, from MAIN:

    CUDA_VISIBLE_DEVICES= GIT_OPTIONAL_LOCKS=0 taskset -c36-39 node .codex-tmp/goal-20261005/integration/.codex-tmp/active-preset-audit-89f8b48b/audit-effective-presets.cjs

This overwrites only this audit's own effective report. effective-preset-audit.json records every source hash and mismatch; coverage-and-paper-preservation.json records the exact list and label guards.

Acceptance remains incomplete. Stored primary matrices and additional historical AIR matrices were read with their original SSE hashes and flag scope. Old accepted:true product rows executed -allTopology without -allGeometry; they cannot satisfy today's full-scope gate. Tri_angle's recorded full-scope diagnostic fails five checks. The latest Shinaide root CUA7785c33f status is Failed3 (D909/W100/R2). Full-scope matrix successes here are isolated tiny fixtures; no global room/template/label/dataset acceptance is claimed. The generated migration explicitly ran no mesh or solve.

Source defaults are src/frontend/shared/js/cfdproj_state_v4.js:27–29 and mesh_sizing.js:252–253. Runner ceiling is stlgen_runner.py:210–223,8869–8901; final checker is :8253,8290–8301. tools/air_product_matrix.py:146–150 polls without its own hard deadline and :168–180 classifies complete sequence <=300s. A300s watchdog is a termination bound, not proof of successful five-minute meshing; shutdown/polling/export time must be reported honestly.

No historical input bytes were changed. Additional AIR matrix snapshots remain historical and do not certify current installed native artifacts.
