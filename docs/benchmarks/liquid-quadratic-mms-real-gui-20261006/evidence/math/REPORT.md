# PRIVATE independent review — quadratic full-ring MMS product packet

Verdict: the frozen mathematical/compiler/constructor/coarse-grid/control/identity evidence supports the report's stated scope. No source-generation circularity through the product residual was found. The four original coarse grids and original EOC/initialization/energy/AD-FD thresholds remain intact. One actionable GUI runbook finding must be disposed of before browser Prepare/Start. The current packet contains no GUI acceptance; Root owns that stage.

Reviewed source packet: /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-product-private-20261006/FROZEN-v1.
Verified manifest SHA256: 6fb645770bbd8a2bd33a72a2c9e5a5c854922921154be8c88a593578fbdca3b3.
All 612 payload SHA/byte counts independently checked; no drift.
Review output: PRIVATE only, CPU affinity32–33, ≤2 numerical threads, CUDA_VISIBLE_DEVICES empty. No normal source/service/API/browser/Git changes; original FROZEN packet untouched.

## Finding requiring disposition: GUI runbook loads into its own frozen output directory

Priority P1 for this acceptance procedure, not a numerical solver defect.

GUI_RUNBOOK.md says to use the remote file browser to load FROZEN-v1/results/normalized-dt-*.cfdproj, then to confirm/create the different meta.casePath output directory before Prepare. That warning is correct, but the precise procedure does not establish the intended output directory. Frozen App.tsx:551–563 (and the current IT App.tsx inspected during this review) implements onRemoteLoaded with session.setCaseDir(dirname(remotePath)); it does not use project meta.casePath. liquidRuntimeClient.ts:87–99 sends the manifest/controllers/revision only. Current IT src/server/server.py:5919–5923 snapshots global CASE_DIR for POST /api/liquid/runs. Frozen run_manager.py:195–206 selects case_dir/Liquid/log/run-<id>. Therefore loading directly from FROZEN-v1/results causes Prepare persistence beneath that frozen directory, despite the saved metadata declaring a writable private casePath. Read-only files do not prevent adding new writable subdirectories because packet directory modes are writable.

Root received this finding before GUI work and explicitly adopted this disposition: create each of the four declared writable meta.casePath directories, byte-copy the corresponding normalized .cfdproj into that directory, then load the copied file using the real remote file UI. That binds dirname(remotePath) and actual server CASE_DIR to the intended private writable case, while preserving the normalized file bytes and source_project identity. This review has not executed or accepted those GUI actions. Root must record actual session caseDir, run_id and artifact owner, and verify FROZEN has no new payload/directory drift afterward. Do not edit the original runbook/FROZEN packet; preserve the finding and disposition alongside GUI receipts.

## Independent mathematical check

The design scripts use product admission to extract canonical graph layout and declared resolved physical coefficients. This means topology/parameter compilation is shared with the product and is not itself independently implemented. The forcing and reference are independently constructed from explicit NumPy hydraulic/thermal public-law formulas; no product residual F, thermal helper, Newton root or finite-difference residual manufactured the new sources.

operator_helpers.matrix explicitly assembles ideal node mixing, signed upwind pipe storage, quasi-steady CP effectiveness and two mixed HX balances. propose_lowflow_fixture.balances/headloss evaluates the literal conservation/headloss equations. The continuous polynomial reference is independent of product output. Its BE error recurrence comes from C*Tdot+D*T=Q+S, with the exact polynomial derivative defect -a*h, not product roots. Product residuals are used afterward for constructor/step verification, which is legitimate checking rather than source generation.

The reviewer imported no product Python modules. independent_audit.py rebuilt incidence, pipe spans, signed upwind topology, thermal D/C/Q, CP effectiveness and all momentum headlosses from actual compiled JSON topology and declared frozen coefficient arrays, then computed fresh SciPy sparse BE recurrences. Rebuilt D/C/Q matched the independently supplied arrays exactly. Explicit momentum equations matched their declared coefficients to roundoff.

State coverage is complete: 229 nodes,286 edges,228 free pressures,286 flows,229 node temperatures,892 water cells,56 solids,two HX temperatures =1693 states and950 storage states. Every public frame's eight scientific arrays was compared bitwise against internal frames, including solid→chip and edge_outlet→edge_outlet_temp field translation; all match. Initial full thermal states and all950 stored temperature rates exactly equal the declared reference, and initialization uses declared_manufactured_state. The independently assembled thermal initial scaled norm is2.3283e-14 under the original1e-10 atol. A literal matrix assembly rounds differently from the product's direct tensor evaluation, whose recorded2.9104e-15 scaled norm is consistent with this result; unscaled W must not be compared to scaled atol.

All four source grids are exactly2/1/.5/.25 s over8 s with5/9/17/33 frames. The reviewer recomputed final-time L-infinity temperature EOCs, preserving the original [.85,1.15] band:

| family |2→1|1→.5|.5→.25|
|---|---:|---:|---:|
|node|.999912113|.999957282|.999978945|
|water|.999912109|.999957280|.999978944|
|solid|.999709692|.999875688|.999943061|
|HX secondary|.992988950|.996497933|.998252837|
|HX primary|.976212904|.987678422|.993725111|

Maximum product thermal error minus fresh independent BE prediction is3.9947e-13 K. Fresh literal BE equation residual max is1.4906e-8 W; fresh full energy residual max3.1839e-8 W. The actual mass residual remains roundoff, and the original energy scale max(1e-7,256*eps)*(1+abs(total_heat_input_W)) passes. No coarse-grid substitution, final-versus-whole error substitution, smaller problem, rebased steady initialization or relaxed tolerance is used.

The source is an explicitly new synthetic fixture; changing polynomial and flow targets to remove cancellation does not reverse the original sine fixture's coarse failure. Highcycle and selected lowcycle designs were separately retained and identified. Their physical coefficients remained the original saved mock pack. Artificial endpoint heat can exceed physical21kW substantially; equation closure is the forced mathematical model closure, not measured/unforced DC thermal performance.

## Source/control and identity checks

The fixed-source1s all56solidMean heat AD3.1635843691760144 equals the reviewer’s fresh independent linear sensitivity solve exactly in this float64 calculation. Three centered heat FD step sizes and the original relative<=1e-4 OR absolute<=1e-8 gate are explicitly present. Pump/first-valve/primary-inlet numbers are finite AD-only results; the packet does not claim FD acceptance for them. Saved first valve opening1 is the upper bound, so the lack of invalid centered FD is correctly disclosed. Tiny1-step primary-inlet AD is not promoted to physical sensitivity quality.

The numeric source-dependency harness differentiates each source-family sum, not a full per-element Jacobian. Sum derivatives alone would permit cancellation, but inspected schema/runtime source constructs each family from a constant sampled tensor and concatenates it into theta without any dependence on the four control leaves. Thus the stronger per-entry fixed-source claim is supported structurally as well as by the family-sum smoke check. An S(theta) regenerated to retain a fixed artificial trajectory would be a different source identity/tape; it was not executed in this packet. The independent ordinary20s control packet is neither replaced nor absorbed by this fixed-forcing test.

The four intended negative gates are visible in corrected harness source and actual JSON/logs: unsigned source-value change→source SHA gate; typed-normalized re-signed wrong initial derivative→unchanged continuous initialization atol; checkpoint manifest tamper→its config identity; different current React source/grid→requested-input checkpoint identity. The initial harness's unnormalized integer digest mistake is explicitly retained and excluded. No fresh expensive product negative/replay run was executed by the reviewer; that evidence was source/log reviewed, while identity hashes and arrays were independently checked.

identity_audit.py independently reproduces the typed benchmark source SHA for all four cases, verifies real emitted input and normalized project raw SHA, recomputes checkpoint canonical config SHA, and confirms checkpointv3/accepted-final retain the full original benchmark and source_project. These results match all four reported identities. Inherited UIDproj_Shinaide alone remains insufficient identity. Moving identical file bytes to an output directory does not change source content; editing metadata/UID/provenance would require a fresh real compile/config receipt.

## Acceptance limits and reproducibility

The private Vitest harness invokes actual reader/schema/revive/public saved-experiment compiler/serialize/reopen and checks all four v4 manifests with exact benchmark retention and source-project binding. It is a valid pinned public-function compiler test, not evidence of the current running React browser after Root's subsequent normal IT changes. The reports correctly record external normal source drift and do not claim globally zero normal drift. The browser stage must retain exact current source/service receipt plus real UI/HTTP/persisted/download artifacts.

Except for the output-directory finding above, inspected Prepare/Start/export controls exist and the runbook maps to current public UI behavior. A no-historical-overlay result for the new8s fixture is expected under the unchanged old overlay identity gate. GUI pixels, service persistence and OV remain unverified here and must not be accepted from private numeric PASS.

The reviewer's own reproducible low-cost scripts and raw findings are frozen beside this report. They use only frozen JSON/NPZ data, NumPy and SciPy; they perform no API calls or source writes. Set PYTHONDONTWRITEBYTECODE=1, CUDA_VISIBLE_DEVICES empty, OMP/OPENBLAS/MKL threads2 and taskset-c32-33. Run script copies from a new writable directory inside dcdtuni; never execute them in this frozen review directory because output writes are directed to its original private parent. Use their explicit output-path constants as declared rather than claiming a relocatable harness.
