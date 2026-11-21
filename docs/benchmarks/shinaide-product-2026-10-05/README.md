# Shinaide product integration — 2026-10-05

This receipt is in progress. Its final acceptance requires real air steady/transient runs, the current-case strict mesh matrix, renderer round trips on the final source, and merging/cleaning the task worktrees. A process exit code or a written field alone is not convergence.

## Liquid physics and reproduction

- The full compiled case has 187 nodes, 242 edges, 56 cold plates, 455 water cells, 513 thermal states and 1128 DAE unknowns. The graph compiler, Newton DAE and differentiable boundary implementation were tested on the actual React export.
- Independent full-graph manufactured-solution and physical-solution verification, smooth first-order refinement and step-window refinement are in [full graph derivation](../../archive/liquid-2026-09-10/shinaide_liquid_full_graph_verification_2026-10-05.md), [full graph receipt](../shinaide-liquid-full-graph-2026-10-05/), and [step resolution](../shinaide-liquid-step-resolution-2026-10-05/).
- A 600 s real browser run accepted online heat, pump, valve and supply-temperature changes. Pause, single step, continue, checkpoint restore and a fresh 60-step re-computation of the executed input were verified. The fresh solver reproduced all 61 physical frames exactly; see [executed re-computation](evidence/liquid-browser-fair-controls/executed-recompute-v2-receipt.json).
- The final heat audit covers 56 servers × 61 accepted samples, with zero unchecked samples and maximum individual closure residual 4.28e-8 W. [Raw audit](evidence/liquid-browser-fair-controls/heat-audit-final.json).
- CPU/CUDA state and gradient comparisons used a separate 0–30 s audit, with tightened Newton tolerances; this is not a 600 s GPU performance claim. [CUDA audit](../shinaide-liquid-cuda-2026-10-05/).

Small nonlinear residuals establish equation closure, not time accuracy or field calibration. The cold-plate effective time constant is about 4.56 s; the 10 s demo step is a coarse display setting. Finer steps are required around load changes. The scene is a synthetic engineering fixture.

## Air and mesh acceptance

The guarded seam-descendant fix has a real Shinaide_Liquid basic-geometry `checkMesh -allTopology` pass: 3,755,177 cells, no failed checkMesh checks, full native API mesh flow 116.543 s. Its raw input, job status, SSE, binary identity and geometry counterexample are retained in [native quality acceptance](../air-product-presets-2026-10-05/native-quality-acceptance.json). The high non-orthogonality and two point-connected regions remain visible warnings and require physical solve validation.

The ordinary Shinaide 2000- and 5000-iteration steady diagnostics did not converge. A later large Shinaide_Liquid run genuinely converged in 600 SIMPLE iterations under its original residual thresholds, and its 112 native server thermal bindings were verified. However, the full checker on exactly the same 3,755,177-cell mesh reported four failed checks: 35 bad-tet events, 125598 concave-or-planar cells, 54 low interpolation weights and 46 low volume ratios. The product rejects this mesh. The 600-iteration solve is solver evidence and cannot authorize a transient seed. [Raw paired input, native solve and rejected full quality](evidence/target-liquid-steady/).

The product quality gate now requires both -allTopology and -allGeometry, a nonempty global mesh, source-bound immutable mesh/input identity and the whole mesh-job 300 s deadline. A native zero exit with Failed N checks is rejected. The reused-mesh path and direct check-only endpoint follow the same full scope. No current large-case full-geometry acceptance is claimed.

Independent classification of the target's 125598 flagged cells found 123924 convex cells with coplanar facets, 1623 cells with a vertex outside a face halfspace and 51 with a nonconvex polygon face. These categories describe geometry rather than override the official checker. Conservative redundant-point cleanup preserves physical and coupled outlines and reduces bad-tet events from 35 to 24; it still fails full geometry. An analytic transition-roof construction passes a buffered 54-cell, two-rank diagnostic, but has not yet established a product-case result. [Classification and diagnostic evidence](evidence/target-liquid-steady/).

A real 511-cell, two-rank nonzero-flow React/API lifecycle passes both checker scopes, converges in 337 SIMPLE iterations and computes PIMPLE steps to 0.02 s. The turbulence-derived inlet/outlet diffusivity fields previously had fixed values, producing a persistent nonlinear plateau despite accurate pressure solves. Keeping those fields calculated fixes the plateau without changing case physics or residual thresholds. Source-paired re-execution on the current SIMPLE binary reproduces the accepted fields. A primary audit verifies the bound mesh/field hashes and exact whole-phi inheritance from steady into the transient seed. This small-case result does not close the large-case mesh or time-accuracy gates. [Actual lifecycle and independent artifact audit](evidence/steady-start-lifecycle/).

The first uniform air transient was deliberately stopped using its owned OpenFOAM controlDict `stopAt writeNow`. Its zero initial velocity hid the initial Courant limit: the first 0.1 s step exhausted 20 outer corrections and the subsequent Courant maximum limited dt to roughly 0.00079 s. This is a diagnostic, not a completed 5 s acceptance. [Stop rationale and exact control dictionaries](evidence/air-transient-uniform-diagnostic/).

The nonzero-inlet-flux diagnostic completed 24 PIMPLE steps before its owned `stopAt writeNow` stop. It reached 0.008666666667 s of a contradictory 5 s solver setting, so the assessment remains `TRANSIENT_INCOMPLETE` despite process return code zero and controller convergence at each step. The actual timeline requested 0.005 s. The exporter now makes timeline duration authoritative and the v4 backend rejects contradictory solver duration. The old input, solver log, stop dictionaries, job status and independent written-field mass diagnostics are preserved in [duration diagnostic](evidence/initial-flux-duration-diagnostic/). No complete transient accuracy claim follows from these short snapshots.

The product now rejects a globally empty mesh before case setup, quality authorization or either solver. Individual empty MPI ranks remain legal when the global mesh is nonempty. A real zero-cell native `Mesh OK`/SIMPLE-marker counterexample had nonfinite continuity and time diagnostics; these no longer authorize a steady seed. The accepted small nonzero-flow lifecycle is documented above. The subsequently repaired physical-time loop has genuine two-rank product verification for dt greater than duration, a nondivisible final time, a boundary keyframe, and owned writeNow termination. Early termination remains honestly incomplete; [exact time-boundary receipt](evidence/pimple-time-boundaries/README.md) preserves the original counterexample and successful follow-ups.

The owned static-mesh PIMPLE solver now uses the shared disconnected pressure-component handling, retains official v2412 momentum/temperature equations and preserves explicit physical patch values in its final written frame. An isolated 38-test MPI regression includes genuine SIMPLE and PIMPLE fields and necessary RHS compatibility checks. These kernel regressions are distinct from convergence of the full Shinaide case. [Pressure component scope and evidence](../pressure-component-gauge-2026-10-05/).

The fresh actual main browser run after the coupled-velocity startup repair keeps the original input, residual settings and all 54 Mesh seed files. At both 0.01 and 0.02 s, all 64 processor-interface phi pairs cancel exactly; final physical net volume flow is about -4.00e-14 m³/s. The earlier nonconservative product result remains archived. This closes that reproduced startup defect on the small fixture and leaves large-case acceptance separate. [Exact main source/native pairing, product log and written-field proof](evidence/pimple-startup-coupled-phi/main-product/README.md).

## React and display

The launcher now defaults to React. Toolbar grouping, actual themes, aligned tab buttons, chart legends, accessible settings and compact equal-width load presets are implemented. Real IAB checks verified pending → accepted feedback and short liquid runs. A real narrow-viewport check also found the time-settings popup was clipped by its toolbar; the panel is now anchored outside that clipping container, grid changes preserve stored keyframe times, and Escape cancels a numeric draft before returning focus. A later full frontend suite passed 3269 tests with one skip; subsequent focused quality-feed and layout regressions and production builds passed. Actual pressure editing, clearing, persistence, first-person drag fallback, aligned bounds and repair-only full-quality rejection are documented in [usability evidence](evidence/frontend-usability/). The original browser input copies are preserved as evidence; continued inputs are derived from the migrated tracked cases.

Actual main browser follow-up verified directory-only selection, ordinary folder double-click, precise small-valued colorbars and aligned range dialogs. Merely focusing and leaving a numerical input no longer commits its rounded display; the original full value is retained unless edited. The real 1280×720 view has no horizontal spill, and the loaded air result/playhead agree at 0.02 s. [Browser observations and related production builds/tests](evidence/frontend-usability/precise-colorbar-browser/README.md).

Renderer changes use a serialized streaming lease and a fresh authorization handshake, retaining display preferences only within the same project and run. GPU0 real decoded video, scene ACK and liquid frame coloring were observed before the final display-preference fixes; the final-source cold-switch and preference round trips still require re-verification.

GPU1 initially failed due to duplicate Vulkan ICD enumeration. A process-local single-ICD UUID mapping fix and launcher lifecycle regression tests are committed. The user subsequently explicitly authorized exact old owned Kit/dialog cleanup. Those residuals exited; the main launcher was restarted with --ov --gpu 0. The real in-app browser now displays the owned Shinaide project at 1600x900: the 2154-primitive scene was authored, its exact request received ok=true, and the client completed scene-ack. Video time advances, H264 statistics report approximately 60 fps with no observed loss, and browser warnings/errors are empty. Only physical GPU0 is occupied by the new Kit. [Current video and scene confirmation](evidence/frontend-usability/canonical-dae-entry/ov-current-blocker/real-iab-video-and-scene-ack.json).

Real software Chromium lifecycle verification passed: idle close removed all owned Chromium descendants, a new request created a fresh process generation, busy close left an eight-second JS command running, and close succeeded after it completed. [Actual API receipt](evidence/browser-lifecycle/actual-receipt.json). The software default is now headless and CFD startup releases its idle owned worker to remove renderer CPU interference.

## Input coverage and cleanup

The preset migration covers 52 current tracked inputs with explicit base size 0.25, dcCart and a 300 s budget: 32 product cases, 4 current templates and 16 current labels. Historical/baseline/test fixtures retain their original evidence. The separately authorized migration covers 1502 ignored generated GTC/Slab inputs, with original-byte archives and exact non-preset preservation; [generated-input migration](../air-product-presets-2026-10-05/generated-input-preset-migration.json). Its file-migration duration is unrelated to mesh timing. The 16 current schema3 paper inputs carry the presets in their existing simulation.meshConfig and do not acquire a shadow runDefaults object.

The pre-existing dirty GTC project and lock are preserved. Its user geometry must survive the final main merge; only the mesh defaults are to be applied to that user version. Native binaries are ignored build products and must be installed or rebuilt in main together with the merged source before removing the task worktrees.


### Frontend field identity, units and layout — main 0146e51c

Real main-source browser validation now covers simultaneous surface/slice/volume
temperature unit changes, unchanged full precision after focus/Tab/Apply,
three aligned non-overlapping colorbar titles, and native keyboard close.
Choosing Umag first removes the prior temperature volume and clears its limits;
Apply loads the actual speed grid with readable small bounds. Folding the
configuration retains the visible grid and legend. Four new component
regressions fail on the old source and pass after the repair; all 42 related
tests and both production builds pass. The full raw logs, retained prior
errors and ordinary-browser receipt are in
[evidence/frontend-usability/volume-field-selection-sync/](evidence/frontend-usability/volume-field-selection-sync/).
The saved PNG is canvas-only. This display receipt does not establish the full
Shinaide strict-mesh gate, which is still under repair.

### Liquid result curve window — main 449f74fd

The former chart appeared below the entire 56-server list, outside the viewport.
It now opens as a centered native dialog with the selected rack/server name,
localized chip/inlet/outlet legends and an accessible close control. The main
browser shows the complete real 600 s curves inside a 1280×720 viewport.
Escape, Enter and mouse close restore focus to the selected server; closing a
late-list server preserves its scroll position. The new focus regression fails
on the old source, then all 60 related tests and production builds pass.
[Primary browser observations, original failure and raw checks](evidence/frontend-usability/liquid-chart-dialog/README.md).
This is a display/usability check of the saved main liquid result, not another
solve or a blanket claim of time-step accuracy.

### Strict large-mesh experiments — partial state at 8175cd98

The later default-OFF sequential nonorthogonality proposal run completed the
authentic React/export/idle-worker-close/mesh/full-check sequence in 149.895 s.
It retains exactly the preceding first-pass decisions, topology and physical
boundary coordinates; all five saved quality sets contain zero newly failed
labels. The unchanged full checker still reports three failed checks, so this
remains a rejected diagnostic rather than a large-case solver seed.
[Raw sequence, written sets and baseline comparisons](../air-product-presets-2026-10-05/strict-stage-conforming-cones-nonorth-sequential/README.md).

The subsequent bounded thin-source-box splitter completed its failed product
sequence in 152.343 s. It was refused at base partitioning with 165 invalid
fans and zero near-coincident new points before any final checker ran. The
original project and all 12 exported STL bytes match the preceding input.
The full experimental source, patch, partition traces and failure are preserved;
the compressed and decompressed identities were independently rechecked.
[Primary rejected attempt](../air-product-presets-2026-10-05/strict-stage-conforming-cones-thin-axis-boxes/README.md).
No experimental native source is promoted by these receipts.

### Worktree cleanup — inactive liquid tree only

The completed liquid branch was already an ancestor of main and had no tracked
edits or active process working directory. Its 12,432 untracked/ignored entries
were archived within the repository and checked twice by size, SHA256 or exact
symlink target before removing that worktree and its merged branch.
[Cleanup inventory and primary receipt](evidence/worktree-cleanup/liquid-8cc1c06/README.md).
The air and integration worktrees remain active pending the outstanding mesh,
display and final merge gates.

### Liquid table alignment — main 315a25f4

The saved real liquid result now has single-line server names within the rack,
retaining full names for hover, assistive tools and chart titles. Numerical
columns use tabular digits and right alignment. All 56 rows retain full
title/accessible identity and all rack tables fit the narrow sidebar without
horizontal spill. Four before/after numerical rows, result count and final
playhead match exactly; the actual main curve still opens and restores focus.
All 60 related tests and both builds pass.
[Actual main observations and source pairing](evidence/frontend-usability/liquid-result-table/README.md).

### Latest full-star strict diagnostic and frontend refinements

The authentic full-star thin-source sequence completed in 221.568 s, including React export, idle-browser close and the complete native/full-check API. It remains rejected with D=1147, W=102, R=2; all 12 STL files, original source point prefixes, actual coupled pairs and signed boundary integrals were independently checked. [Full failure and immutable geometry proof](../air-product-presets-2026-10-05/strict-stage-conforming-cones-thin-current-star/README.md).

Three fixed-base common-apex stencils now have exact necessary-row contradiction certificates. This excludes only that fixed topology and declared external state; it does not exclude alternate subdivisions or authorize mesh acceptance. [Derivation and independently rebuilt certificates](evidence/target-liquid-steady/cone-whole-parent-apex-optimization/convex-kernel/README.md).

The actual main browser verifies aligned localized rack summaries, keyboard disclosure and heat-column overflow repair. Mesh controls now have associated labels or distinct localized accessible names, with native label focus/Tab order and unchanged values in Chinese and English. [Rack summaries](evidence/frontend-usability/liquid-rack-summary/README.md), [mesh-control labels](evidence/frontend-usability/mesh-control-labels/README.md).


### Canonical liquid default entry — source repair and baseline browser proof

The actual main browser on 89f8b48b prepared the original missing-experiment
Shinaide canonical input as v2/Adam with 181 frames while the top bar said
steady. The owned prepared session was ended without advancing it. This is a
real entry defect, separate from the already verified explicit 600 s DAE input.
[Original browser observation and backend input](evidence/frontend-usability/canonical-dae-entry/before-89f8b48b/README.md).

New canonical/default inputs now explicitly choose synthetic Newton DAE;
reading and rendering remain pure. Prepare/export saves the chosen assumptions
and parameter pack before source-project hashing. Marked inputs follow the
top-bar mode: steady v3 has one sample, transient v4 has 181 samples for the
unchanged canonical 180 s/1 s clock and no load step. Explicit legacy v2/PID and
unmarked v3 clocks remain compatible. Physical construction, heat, ports and
routes are unchanged. The old canonical 600 s DAE fixture is unchanged apart
from two provenance corrections. This is synthetic engineering data.
[Source, semantic preservation and 3526-pass suite](evidence/liquid-canonical-default-newton/README.md),
[frozen production build](evidence/frontend-usability/canonical-dae-entry/production-build/README.md).
A fresh main browser default-entry run is still required at this source checkpoint.

### Preset coverage is distinct from complete mesh acceptance

A real schema-loader audit on 89f8b48b checks all 1554 migrated inputs:
32 product inputs (including the GTC template), four room templates, sixteen
schema3 paper inputs and 1502 generated GTC/Slab inputs. All ten raw/effective
preset keys match; the historical paper inputs retain their original geometry,
physics, solver settings and existing simulation layout. The generated inputs
match the migration receipt byte identities. The 300 s deadline bounds work
and can reject a mesh; it does not establish successful mesh generation on
every input. Reviewed archived matrices used a weaker geometry scope for
some earlier passes, and the current large Shinaide full checker still fails.
[Actual loader audit and archived scope review](evidence/preset-audit-89f8b48b/REVIEW.md).

### Actual repaired liquid entry on main

The real main React browser prepared and completed both the single-sample v3 steady run and the 181-sample v4 transient run. The original unmarked project was loaded without an Adopt action; its existing clock was preserved, then the browser explicitly selected 180 s and steady initialization. The new explicit canonical project separately opened at 180 s/steady without that edit. All prepare/start/save/audit actions used the normal UI. The no-forcing transient finished at 180 s; all 10136 server/sample heat closures were checked, with maximum individual residual 3.3800048981902364e-8 W and zero unchecked. Independent artifact parsing confirms full graph/result identities without executing another solver, compiler or replay. This constant trajectory verifies the repaired default lifecycle and does not replace the earlier perturbed MMS evidence. [Actual browser and raw artifact audit](evidence/frontend-usability/canonical-dae-entry/main-browser-5f9bd080/README.md).

The finite shared-point and cavity work remains mathematical diagnosis. The frozen ten- and eleven-point candidates, exact conservation/guard checks, binary64 point roundtrips and rejected literal union pairs are preserved separately. None authorizes a native or complete Shinaide mesh acceptance. [Ten-point candidate](evidence/target-liquid-steady/shared-point-mathematical-7785/README.md), [eleven-point candidate and cavity design](evidence/target-liquid-steady/shared-point-ratio-mathematical-7785/README.md).

### Current OV source on main

Source 7a74dc80 fixes the screenshot button to capture the displayed OV video at its native dimensions, refuses unready OV frames instead of saving hidden Three, and confines normal texture authoring to the owned checkout-local runtime. Seven new button regressions/typecheck and an actual 32.63 s production build passed; 51 related CPU launcher/bridge tests passed. A single normal GPU0 restart then passed real main browser scene/video, Three.js to OV roundtrip, clean project state and actual PNG capture. All 36 new texture files are within the repository; 203 older outside-cache files remain unchanged. The delivered main tab is left on OV. [Final current-source browser/runtime receipt](evidence/frontend-usability/canonical-dae-entry/ov-final-main-gpu0/README.md).

### Direct finite proposal and native implementation boundary

The direct finite projection prototype now re-derives both3/4-parameter R modes from actual original topology and clears R2 in a96-core/83-outside audit, with D14/W18 unchanged. Independent replay reproduces168/8 request counts and complete parameter/history bytes, checks label/order invariance and confirms no optimizer call. The long-step endpoint that adds a D failure is explicitly rejected. [Low-dimensional proof and independent audit](evidence/target-liquid-steady/finite-native-feasibility-7785/README.md).

Separate9/11-dimensional finite proposals combine to14 moved points and373-core/271-outside scope, D47→38/W32→23/R2→0 with all original hard-role/cavity/outside and binary-copy guards passing. This is a different result from the earlier SLSQP candidate. It does not yet supply a generic higher-dimensional native selector. [Separate joint proof](evidence/target-liquid-steady/finite-dw-joint-mathematical-7785/README.md).

The default-OFF3/4-dimensional native prototype now has real React retry job495546f2 proof: R2→0, full stock Failed2 D909/W100, native135.083s. Exactly4 point rows changed; other point bits,56 topology fields and16 original D/W failure-set files remain identical. Real8-rank cancellation unlock and same-page retry also pass. Source remains isolated, and neither the full large air solve nor worktree cleanup is accepted. [Actual native/cancellation proof](evidence/target-liquid-steady/finite-native-product-495546f2/README.md).

The generic nine-dimensional DW prototype now has real React jobce178f4a proof: D909→907/W100/R0, full stock still Failed2, native143.775s. Three further point rows change, the four earlier R rows remain bit-identical, and all56 topology fields remain unchanged. Complete failure-set comparison confirms only two D labels disappear with no new failures; W100/N754 remain unchanged. The source stays experimental and large air steady/transient acceptance is pending. [Actual DW product proof](evidence/target-liquid-steady/finite-native-dw-product-ce178f4a/README.md).

The following actual React runs remain strict failures: V5 `5a55bbf9` D892/W99/R0, native143.284s; V6 `c91c0fd9` D867/W98/R0, native144.549s. Input/ELF bindings, failure-set deltas and preserved full-case archive chains are published as failure evidence. [V5 receipt](evidence/target-liquid-steady/finite-native-dw-product-5a55bbf9/README.md), [V6 receipt](evidence/target-liquid-steady/finite-native-dw-product-c91c0fd9/README.md).

A real browser reload exposed solver readiness being restored from mere polyMesh presence. The normal MeshPanel now offers check-only access, while reload readiness requires the bound strict acceptance receipt. Actual rejected check7efdbb5f and subsequent reload keep the solver disabled and permit retry; 84 React/31 backend tests, typecheck/build and independent closure pass. [Strict reload and check-entry repair](evidence/strict-reload-check-entry/README.md).

## V7 failure receipt: ed499960

Normal React 8-rank run: native 144.868 s, strict Failed2 (D844/W98, volume ratio passed). Against V6, 23 D labels removed with no additions; W unchanged. Solver remains gated. [ROOT reviewed source/runtime/quality evidence](evidence/target-liquid-steady/finite-native-dw-product-ed499960/README.md) includes the full archive chain and explicit exclusion of the stale V6 quality receipt.

## V8 failure receipt: 205177dd

Normal React native run 145.677 s, strict Failed2 (D836/W84, volume ratio passed). Against V7, fourteen W labels removed without additions and net eight D fewer (ten removed, two original-baseline failures return). [ROOT reviewed runtime/source/quality and archive evidence](evidence/target-liquid-steady/finite-native-dw-product-205177dd/README.md). Mesh/check controls unlock; solver remains gated.


## 2026-10-06 mixed liquid waveform and input-revision repair

Actual full50-frame mixed-keyframe run completed with an accepted online heat command and unchanged preceding history. Independent513-state thermal balances agree to5.25e-10K; local thermal waveform AD/FD heat and primary-return objectives pass; the original insensitive inlet-to-solid objective remains a preserved FAIL. Actual browser found and verified repairs for false stale warnings and same-UID description reload. [Full raw product/science/browser evidence](../shinaide-liquid-mixed-keyframes-2026-10-06/README.md), [source regressions and runtime-preserving builds](../frontend-ui-input-revision-2026-10-06/README.md). MAIN UI source ca3f3696 built normally; fixed live UI acceptance was isolated8396. Large P13 strict mesh still Failed2/D831/W82. [Renderer-independent object architecture stage plan](../domain-object-foundation-2026-10-06/README.md) is newly authorized and in progress.


## 2026-10-06 complete two-lobe local proof

A frozen complete-cavity template replaces32 core cells with40 two-lobe cones. Real installed OpenFOAM arithmetic clears all five local determinant failures without new hard W/R/S failures; the101-cell complete halo becomes109 cells. Exact volume/first moment,38 literal outer rings, eight physical boundary facets,69 outside cells and independent780 cone-pair separation checks pass. ROOT independently reran the three critical pure-arithmetic checks: exact conservation/coverage, pairwise nonoverlap, and the installed hard-success versus rejected Chebyshev seed. [Positive packet and ROOT replay](evidence/target-liquid-steady/two-lobe-separator-star-template-20261006/receipt.json).

This positive used a49-request six-parameter SciPy diagnostic. It is not a dependency-free normal native selector or a final writer certificate. [Neighbor-coordinated refusal](evidence/target-liquid-steady/neighbor-coordinated-apex-star-20261006/manifest.json) and [complete single-star refusals](evidence/target-liquid-steady/complete-two-point-star-template-20261006/manifest.json) are retained separately.

All81 points,101 cells and288 faces bind uniquely and geometrically exactly to actual P13 through normal native handles; boundary and all three zone file payloads are SHA/size identical to P12. Five actual parent origin callback bindings and a bounded multi-origin producer/consumer authority remain required. The existing contract accepts only two origins. [Source/material binding and finite replay plan](evidence/target-liquid-steady/two-lobe-native-transplant-plan-20261006/README.md). No production source or mesh was changed by these packets. Actual P13 remains strict Failed2/D831/W82; large air steady/transient solving remains gated.

## 2026-10-06 finite geometry seeds — frozen local qualification

The fixed two-lobe cavity now has a dependency-free finite geometry seed candidate: four-active-plane enumeration derives both seed coordinates from the bound shell; no optimizer call or copied successful coordinate is needed. Installed OpenFOAM DP/Int32 checks on all109halo cells give local D5→0 and weight/ratio/skew0→0, retain all27previously passing replacement roles, and exact rational V/M1 plus780pair separation checks prove local coverage without overlap. N warnings4→2 still introduce two different warning labels;10ordinary outside cells change their DP accumulation by at most65ULP. Neither is hidden or claimed bit-identical.

[Original42-file packet](evidence/target-liquid-steady/two-lobe-finite-native-replay-20261006/README.md), manifest0835cba98ea72835abf38da121707fd51cdf046926b7f38fd04e1334ed53c923, retains the first12PASS/1FAIL wrapper tamper test before its13PASS fix. ROOT replayed the exact shell/conservation, all780pair separations and physical-patch tamper refusal:3PASS. The11.649s bounded diagnostic includes seed generation and a typed harness build/run; later audits are separate, and this is not product meshing timing.

A separate normal-source CompleteCavityAuthority and complete origin lineage are under development. The old pair authorization remains intact. No production topology consumer, final native writer, coupled-byte certificate or new browser mesh run has accepted this candidate. Latest production P13 remains strict Failed2/D831/W82, native144.802s; large-case air steady/transient stays blocked by that strict mesh gate.

## 2026-10-06 bounded normal source authority — source/API checkpoint

The seven-path normal-source CompleteCavityAuthority and lineage checkpoint is committed on AIR at afbafdcd and verified pushed to origin/shared. ROOT and independent read-only reviewers checked source/provenance/capacity and old pair-body preservation; the late128 admission defect was fixed with preserved rejection fixtures. [Full original packets and independent closure](evidence/target-liquid-steady/complete-cavity-source-authority-20261006/README.md) explicitly distinguish synthetic API/type tests from actual P13 provenance.

This source authority is not a topology repair grant or a built production binary. Generic discovery, exact native coverage, current installed oracle, atomic replacement, packet protection, final writer and full normal checkMesh remain. P13 remains Failed2/D831/W82; no new large-air run is accepted.


## 2026-10-06 numerical pose/ports source and browser acceptance

The renderer-free pose/ports kernel is source-accepted IT57d7a3a2→MAIN05f5f845 and pushed to origin/shared. Actual32model/800mesh Shinaide-derived normal liquid Prepare/Start completed50frames with exact prior physical output bits,2800heat samples≤3.38e-8W, no stale banner/errors, and byte-identical normal Save As. [Raw proofs and both normal builds](../domain-pose-ports-2026-10-06/README.md). Main OV video remains live1600×900 without reload/restart; isolated8396 keeps completedrun846357c1. BOX/CDU structural product is in progress. [Normal complete-origin source checkpoint and consumer design](evidence/target-liquid-steady/complete-cavity-source-authority-20261006/README.md) is source-only; exact/prepared API review is ongoing, no new native library/mesh run. Strict P13 remains Failed2/D831/W82/native144.802s and large-air steady/transient stays unaccepted.
