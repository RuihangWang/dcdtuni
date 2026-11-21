# case/DCTS — a DCTS room in the current editor

`DCTS_0112.cfdproj` is the DCTS dataset room `GTC_dataset_0112` (a test room: not seen in training) rebuilt with
the recipe the dataset was generated with, so the mesh and the boundary conditions the current dcdtuni produces
match the ones SCANO was trained on. It is the reference case of the AI solve (solver tab → AI 推理).

Made by `make_dcts_project.py` from the regeneration kit's original cfdproj
(Drive `SCANO/realtime_demo_20261027/inputs/regen_kit_20261001/1_cfdproj/cfdproj_final_500.tar.gz`; the copies in
`case/GTC_dataset/case_*/` carry run defaults a later migration rewrote):

| | DCTS (JUPITER, 2026-10-03) | this project |
|---|---|---|
| mesh | snappyHexMesh (OpenFOAM v2506), base 0.34 m, 24 processes | snappyHexMesh (conda OpenFOAM v2412), same levels, 24 processes |
| cells | 916,595 | 897,361 (-2 %); same 685 patches, same types, same total volume and cell-size distribution |
| solve | 5000 SIMPLE, relaxation p_rgh 0.3 / U 0.5 / T 0.5 / k-epsilon 0.3 | the same |
| baffles | cyclic baffles plain cyclic (no pressure jump) | false ceiling 100 % open, blanking panels "fully open" |

Two boundary conditions of the current runner still differ from the dataset's (server-outlet temperature feedback
damped; ACU returns pinned to their supply flow). Both have the same converged state; the AI solve rewrites their
first-iteration values into the dataset's form. Mesh comparison and validation: `docs/developer/scano-ai.md`.

```bash
python case/DCTS/make_dcts_project.py <GTC_dataset_NNNN.cfdproj> case/<Name>   # another DCTS room
```
