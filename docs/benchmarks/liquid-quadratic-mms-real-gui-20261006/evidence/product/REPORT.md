# PRIVATE real-product verification of the new quadratic full-ring MMS fixture

This new fixture passed the actual pinned current React project reader/save/reopen/compiler, the public full DAE constructor, all four original coarse backward-Euler runs, accepted-prefix admission, and checkpoint v3 replay. It needs no production solver, tolerance, gate, catalog, or overlay code change. Browser Prepare/Start, server persistence, UI pixels and OV are still unverified here and are reserved to Root. The original sinusoidal fixture remains a separate coarse EOC failure; this packet does not change its verdict.

The independently derived new formula is `full-ring-positive-curvature-lowcycle-v1`, source reference `independent-quadratic-lowcycle-public-laws-20261006`, with graph-layout SHA `dde3685500f48cafa5fe0eccce7f2ce077217cc755b1fb4b676bdd5e31b40831`. Input sources were copied byte-for-byte from design packet `ba3ae1352ce56e390193f441758b50a3f30fe753e584b190ebe38ed0951ab025`; its mathematical report and equation-source scripts are separately preserved in independent-design-evidence. No product residual generated any forcing.

## Real chain and identity

`compile.test.ts` invokes the same public `readProjectFileText`, schema, `resolveSavedExperiment`, `compileSavedExperiment`, and `serializeProjectState` path as the application. All four public compiles passed; each is strict v4 transient/consistent, 229 nodes and 286 edges. Compilation did not mutate its project; save→reopen→compile was exact. The previous hand-authored carrier manifest was never a solver input. Actual emitted files in results/compiled-dt-*.liquidrun are the sole inputs to the numerical worker.

The actual new canonical source-project hashes are:

| dt (s) | frames over 8 s | new source_project SHA | new benchmark SHA |
|---:|---:|---|---|
| 2.0 | 5 | `6d383d2140767722a133bed0584c4f81b4186da03e4d21b6ba78d52b86be32eb` | `b8a1ebf00e4227b3ab63e5e242da021f2ed36e1733d1c3e676ea6752bed10735` |
| 1.0 | 9 | `affdac1a5c498cbb03799d16c04410a0a7eeb975e2cf7a405475bd9e0c042509` | `dbb9adf084c5abda2a8c3b785e363f934941d55ac5d7147c0f4a68bade5aae65` |
| 0.5 | 17 | `e336a7513c2f73745e3e821da3790a82b5fb7ede2fe863bc060d8c239081673d` | `efcc6b1d4af11a82e98d2fe21158ccad2742785b95ad09bca057e4c1d4126786` |
| 0.25 | 33 | `595d69fc1022aeb213354c2989272e3de4316f00e8159279e09cee814584080c` | `995a8b5749efde484acb438e5b6860192986ea1567e5ba0f0e3655b07ce97d0c` |

Raw input SHA, canonical config hash, normalized project byte SHA and prefix SHA receipts are in react-compiler-receipt.json and coarse-product-final.json. The inherited UID `proj_Shinaide` is not a sufficient fixture identity. The project name and source-project content hash are new. Relocating or editing UID/meta.casePath/provenance requires a new actual compile/config receipt rather than copying these hashes. The preserved provenance text was authored at design time and labels the assets synthetic and initially unaccepted; this new report documents the measured stages without rewriting historical provenance.

## Full coarse solver verification

The state is 1693 unknowns: 228 free node pressures, 286 edge flows, 229 node temperatures, 892 stored water temperatures, 56 solid temperatures and two HX temperatures. All original numerical options remained unchanged: max_iterations 40, atol/rtol 1e-10, max_backtracks 18, original dense linear solve settings. `DAESimulation(raw)` uses the explicit manufactured nonsteady state and derivative, not a steady rebase; every initial continuous scaled norm was 2.9103830456733705e-15 and all initial arrays exactly equalled the independent formula.

| dt (s) | final HXsec error (K) | max actual scaled residual |
|---:|---:|---:|
| 2.0 | 0.112772572464 | 2.0227162e-13 |
| 1.0 | 0.056660972984957 | 4.208232e-13 |
| 0.5 | 0.028399340804583 | 8.1490725e-13 |
| 0.25 | 0.014216877208071 | 1.4731086e-12 |

Final-time L-infinity EOCs use the original .85–1.15 band and the original coarse grids 2, 1, .5, .25 s over 8 s; no finer replacement, changed threshold, or reinterpretation of the old failed case is used.

| temperature family | EOC 2→1 | EOC 1→.5 | EOC .5→.25 | original band |
|---|---:|---:|---:|---|
| node_temperature_K | 0.999912113 | 0.999957282 | 0.999978945 | pass |
| water_temperature_K | 0.999912109 | 0.999957280 | 0.999978944 | pass |
| solid_temperature_K | 0.999709692 | 0.999875688 | 0.999943061 | pass |
| hx_secondary_K | 0.992988950 | 0.996497933 | 0.998252837 | pass |
| hx_primary_K | 0.976212904 | 0.987678422 | 0.993725111 | pass |

Every node, water cell, solid and both HX temperatures over all frames were compared to the separate continuous polynomial reference and its independent conservation-law BE error recurrence. Maximum product-versus-independent BE prediction discrepancy was 3.558854599905459e-13 K. Maximum full-node mass residual was 1.110223024625157e-16 kg/s and maximum reported absolute total energy residual 3.181003194185905e-08 W. The original energy gate `max(1e-7,256*eps)*(1+abs(heat_input_W))` was evaluated unchanged, with maximum ratio 4.157114007616673e-06. Pressure and flow errors, all array comparisons and raw frames are preserved in results.

Energy balance here includes manufactured forcing. Physical cold-plate heat is 21 kW, while the endpoint artificial thermal forcing reaches about 259.26 kW; the displayed zero total equation residual must not be called an unforced plant thermal balance. Every Pa/W column, explicit boundary/initial state and coefficient is in the saved benchmark and independent data. This is a synthetic mathematical solver check with the declared mock parameter pack, not measured thermal/hydraulic performance or vendor calibration.

## Prefix and checkpoint contracts

Initial N_t=1, first-step N_t=2, and each final accepted manifest were built by the actual executed_manifest path and re-admitted by the strict public runtime input admission. Source times and all five forcing-row families were clipped exactly to that prefix and their typed canonical source digest recomputed. Explicit initial state and derivative remained intact. Final accepted source is exactly the original full source. Each raw checkpoint preserves the full original admitted input/source, rather than a prefix replacing history.

Public `DAESimulation.from_checkpoint(raw, checkpoint)` replayed all 5/9/17/33 frames, with every sample of node_pressure_Pa, edge_flow_kg_s, node_temperature_K, cell_temperature_K, solid_temperature_K, secondary_supply_K, primary_return_K and edge_outlet_K bit-exact. No input object or input file was mutated. These checks cover the constructor and public replay, not HTTP or GUI persistence; Root must retain actual browser artifacts when accepting those stages.

## Source/control dependency and negative gates

For one real t0→1 s window, all-56-solid mean J=302.0398478296469 K. Fixed sampled Pa/W sources have zero parameter derivative with respect to all four leaves (heat multiplier, pump ratio, first valve, primary inlet). Actual public research set_inputs→step→tensor_series heat AD is 3.163584369176014 K per multiplier; the independent conservation-law matrix sensitivity is exactly equal in the tested float64 result. Heat-centered FD h=.01/.001/.0001 has finest relative difference 4.476288624281955e-12, passing the preserved relative<=1e-4 OR absolute<=1e-8 gate.

Pump AD=-0.8354974021801665, first-valve AD=-0.0001925543255332052, primary-inlet AD=4.823842725893846e-34. These three are recorded finite AD values only; no FD acceptance is claimed. The first valve is at saved opening 1, so invalid >1 centered perturbations were not performed or clipped. The tiny inlet derivative is a finite one-step model result and not a useful broad physical sensitivity claim.

A regenerated source law S(theta) that holds this artificial target fixed has a different dependency tape/source identity and could cancel the physical control effect; it was not executed. The current GUI fixture carries fixed source arrays, so its heat response does not verify unforced ordinary controls. Those ordinary full20s pump/valve AD/FD results remain in the separate ordinary packet.

Actual full constructors rejected (1) a source-value change without its digest at the source SHA gate, (2) a correctly typed-normalized and re-signed incompatible initial derivative at the original continuous initialization atol gate, (3) checkpoint manifest tampering at its own config gate, and (4) a different current React source/grid at requested-input identity. Specific error messages are asserted and recorded.

The first negative-harness attempt incorrectly hashed unnormalized JSON integer data and therefore reached the SHA gate instead of the intended initial-derivative gate. Its code/log/result are preserved as attempt01 and explicitly not counted as a product RED or initial-state proof. The corrected rerun uses the real validated typed benchmark before changing only its derivative and passed all four intended rejection gates.

## Existing UI and required production delta

No new catalog entry or solver code is required for loading these explicit project assets. Existing `liquidExperiment.ts:94–105` retains structuredClone dae; `111–133` retains explicit saved DAE rather than adopting mock again; `135–197` invokes actual graph/strict compile. `152` selects v4 for an explicit manufactured benchmark, `171` chooses the required consistent initializer, `184–187` binds actual new source_project. Shared cfdproj_schema.js:471–474 validates the namespace. ManufacturedBenchmark runtime admission keeps exact source digest, units, unique coverage, graph-layout, time-grid, shape, positive Kelvin and fixed flow-regime boundaries.

LiquidExperimentPanel.tsx:141–166 compiles persisted current project and Prepare POST; 168–170 actions Start; 290–296 exports/Prepare/Start; 338–345 result/checkpoint/accepted-input buttons. Its local catalog at 130–138 is just pump/plate control targets. The old sha-bound analytic overlay in LiquidRuntimeMonitor.tsx:29–45 is a different historical fixture and stays unchanged. This new 8 s curve must never be inserted into the old historical overlay or silently shown under its identity.

GUI_RUNBOOK.md provides all four precise project files and Prepare/Start/export checks. Asset-only addition can be reviewed separately through production-delta-plan.json; this packet applies nothing.

## Preservation and evidence limits

This worker created no normal source, case, Git, service, browser, Kit or GPU changes. The tested source snapshot is pinned by source-origin-receipt.json. All caches/temp/output are within this PRIVATE root; numeric affinity was [32,33], max2 threads and CUDA_VISIBLE_DEVICES empty. Main and IT source guards are preserved before and after. One measured external IT commonRenderIntent.ts change 1ecb37df…5329B→55a2168e…5404B was confirmed by Root as their reviewed session-projection correction; the pinned liquid compiler snapshot remains before that unrelated display change. Do not describe the normal tree as globally zero-drift. Any additional concurrent changes are recorded individually in the final guard receipt. Original design/full-MMS/coarse-failure packet payloads are separately SHA checked and preserved.

Private compile setup attempts missing copied vendor or shared JSON assets are preserved as setup failures (not product REDs); the corrected actual four-test suite passed and complete private pinned React tsc --noEmit returned rc0. No browser was opened and no production service was started. Pixel/OV acceptance and all four actual browser Prepare/Start identities remain Root work.
