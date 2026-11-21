# Drag & Sim — commit history

Every commit that built the demo, newest first. Kept because the *order* matters: several
conclusions in `02-inference-findings.md` overturned an earlier commit's claim, and reading
those commits without their successors would mislead.

Two commits below are corrections of the one above them in date order. They are marked.

## 2026-08-10 — the ROI investigation

| commit | what |
|---|---|
| `9cb9376` | slice-basis reuse: repairs the ROI but circular. **Rejected.** Localises the collapse to the 7 trunk pooling sites, clears the two-stream boundary design. |
| `6a6509c` | opt-in sim-ROI pivot restriction (box + 45,000 anchors), browser-verified end to end |
| `31d61f0` | AI path exports only the ROI (requirement 2, second half) |
| `04f2f57` | `box+anchor` pivot mode and the 14-arm K sweep that sizes it. **Corrects** two of my own claims from `8da9599`: K is not monotone, and stratified anchors are worse than random at every K. |
| `c4d3cbd` | `cpd_cell` cache — ROI queries without reading the result |

## 2026-08-09 — ROI as a unit, and the finding that reshaped it

| commit | what |
|---|---|
| `b5b5524` | **Corrects** two false statements in `8da9599`'s finding |
| `8da9599` | measure contiguous-ROI sampling against the scattered baseline — it FAILS (+0.405) |
| `b7c8775` | keep the ROI source grid resident across extractions |
| `49f701b` | ROI extraction — cut the cells out, don't just clip the view |
| `6b577f5` | stop burning 300 app cycles per rebuild on fixed waits |
| `dc5ea1a` | design doc: ROI as the unit of simulation and transport |
| `3f36563` | streamlines — configure before the first sync, clamp seeds per axis |
| `e436998` | post-processing takes either source; derive \|U\|; real per-scene ranges |

## 2026-08-06 → 08-08 — geometry parity with the Three.js editor

| commit | what |
|---|---|
| `106a711` | end-to-end driver for a `.cfdproj` plus its own OpenFOAM result |
| `34dcecb` | drive the native Kit viewport grid; measure why USD stays the default |
| `3e44be7` | rack internals + a model tree built from what Kit actually rendered |
| `f4f89d4` | `geometry.scale` was dropped, mirroring two racks and killing the aisle |
| `48220dc` | honour the editor's opacity in the OV render |
| `3ef8a83` | the visibility checkbox lied about objects the project disables |
| `a37b0cb` | the verify driver's hide step never matched anything |
| `6f9f39c` | per-prototype binding/normal receipt; class-prim traversal gotcha |
| `a62c9a0` | shadows do not explain the floor gap — **hypothesis refuted** |
| `4325507` | settle the `.cfdproj` light rig on measurement, not theory |
| `325495f` | bake the editor's procedural textures into the OV materials |
| `dbb1297` | bind the textures the editor actually uses, and match tone |
| `46c8564` | port the frontend's material colour rules to the OV render |
| `8e391d8` | render a `.cfdproj` in OV, geometry authored at true size |
| `2ad8810` | deployment guide + asset bundler for a second server |
| `42d0f12` | make the CFD demo runnable on another server |
| `8253d27` | AI prediction demo, SCANO bundle, project page |

## 2026-06-29 → 07-01 — the original demo

| commit | what |
|---|---|
| `d5bc601` | reset scene on fresh page loads |
| `74c88c2` | streamlines (native kit-cae, T/\|U\| colour, ROI-is-god) |
| `4f088d1` | **the name**: rename the dataset control to Drag&Sim / simulate |
| `55f8513` | volume post-processing fixes + head-browser verify harness |
| `a142b93` | tabbed left panel + runtime dataset loading + process manager |
| `83e984f` | model-tree + inspector UI |
| `e6e7d0b` | ROI volume clip + field switch via volume rebuild |
| `6d7210f` | CFD temperature volume rendering + control panel |
| `6889a6b` | render-only viewport via layer-hiding (`hideUi` breaks RTX render) |
| `7f84bc7` | web viewport + first end-to-end `loadScene` command (spike passed) |
| `4295452` | `dc.cfd_bridge` extension skeleton + launch script |
| `f6c3afc` | lean React + Vite web scaffold |
| `9e05b72` | pure command parsers for `dc.cfd_bridge` (TDD) |

Design docs from that era: `docs/superpowers/specs/2026-06-29-omniverse-cfd-web-demo-design.md`,
`docs/superpowers/plans/2026-06-29-omniverse-cfd-web-demo.md`.
