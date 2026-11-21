# Original full-ring MMS coarse HX secondary order: independent cause audit

The original dt=2/1/0.5/0.25 s final-HX-secondary order remains below the requested 0.85–1.15 band. This audit reproduces that failure with independently assembled backward-Euler thermal equations and attributes its error. No production implementation defect was found in the investigated source/initialization/time-integration path, and no production patch, forcing, initial state, tolerance, gate or original frozen packet was changed. This is a cause audit, not an upgrade to full MMS acceptance.

## Identity and scope

Original fullMMS FROZEN-v1 manifest SHA256: `49c2e46f714605d9a60288f55334e9bfa28d76d3867edf1675fb8f93005aef45`; independently rehashed all 1170 payloads, with zero drift. Twenty-nine evidence originals were copied/decompressed losslessly and checked against their original raw SHA256. The exact original frozen backend source snapshot is in `source-frozen/src/backend`; it is not current normal Root source. All commands used NTU-litianyu-40901, CPU affinity 32–33, two numerical threads, empty CUDA. Normal IT liquid Python source has 48 before/after guards, zero drift. No browser, normal service, case, Kit, GPU or Git operations were performed.

The admitted graph is the same 229-node/286-edge/1693-state full ring: 892 water cells, 56 solid CP states, one secondary HX state and one primary HX state, or 950 differential storage states. The independent thermal system also includes all 229 algebraic node temperatures (1179 unknowns). Physical heat remains 21 kW; the declared synthetic MMS Pa/W compensation is mathematical verification data, not measured operation. Fluid coefficients and prescribed boundary/controller data remain the admitted original values. No hydraulic inertia or mechanical work-to-heat term is invented.

`reference-identity.json` separately reconstructs every prescribed hydraulic flow from the original independent cycle coefficients, `m(t)=Ccycle[a0+a sin(2*pi*t/32+phase)]`. All six source-grid flow-reference arrays are bitexact; integer `B*Ccycle=0`; all-node mass residual is at most 7.11e-15 kg/s. All six layout identities are `dde3685500f48cafa5fe0eccce7f2ce077217cc755b1fb4b676bdd5e31b40831`. Initial state digest, same across all grids, is `aea7e934b810ff52377735b1c93383d7b6dd25b7f4b1e6a5257fe12633747cb5`. Source columns retain complete 286/892/56/1/1 IDs, exact admitted times and row lengths. Source digests differ by grid as required; none were regenerated.

## Governing equations and independent discrete error

For the independently prescribed hydraulic m*(t), with fixed nonzero flow signs, every actual thermal equation is linear in temperatures. Assemble D(t) directly from conservation laws, incoming directed edges, pipe-cell order, CP effectiveness `epsilon=1-exp[-UA/(|m|cp)]`, and HX exchange. Capacities C are zero at algebraic nodes, rho*cp*V at water and HX-secondary, CP solid capacity at solids, and primary rho*cp*V at HX-primary. Physical Q is separate from declared MMS S.

The equations are `C xdot + D(t)x = Q(t)+S(t)`; units are W for storage/mixing balances (fixed-temperature boundary rows are their explicit temperature constraint). In particular:

* Water: `rho cp V dTw/dt + |m|cp(Tw-Tup)=Qwater+Swater`.
* Solid CP: `Csolid dTs/dt + |m|cp epsilon(Ts-Tup)=Qphysical+Ssolid`.
* HX secondary: `Cs dTsec/dt + |m|cp(Tsec-Tup)+UA(Tsec-Tpri)=Ssec`.
* HX primary: `Cp dTpri/dt + mp cpp(Tpri-Tin)+UA(Tpri-Tsec)=Spri`.
* Nodes: exact directed ideal mixing; no manufactured mass or junction source.

The original independent sinusoidal storage formula is reconstructed from xbase/xamplitude/xphase, with analytic derivative, using NumPy. All 950 storage reference values at all six grids are bitexact. Independently assembled continuous residual `D_i*x*_i+C*xdot*_i-Q_i-S_i` is at most 9.3133e-9 W, floating cancellation precision of the original W sources. Original initial error is exactly zero; this is the admitted nonsteady manufactured initial state, not a steady initializer.

Let `e_i=xBE_i-x*_i`, `h=dt`, `delta_i=(x*_i-x*_(i-1))/h-xdot*_i`. The exact discrete truncation propagation is:

`(D_i+C/h)e_i = (C/h)e_(i-1)-C*delta_i`, with `e_0=0`.

`independent_error_recurrence.py:17–45` hand assembles the full thermal D/C/physical Q from graph and coefficient data. Lines 49–58 read fixed original source data and audit continuous equations; lines 59–63 solve the independent sparse linear error equation. Product residual F, Newton, thermal helper, product numerical output and source regeneration are not used to construct this operator or defect. Recorded product errors are read only after the independent solve for comparison. Product admission/compiled graph data are used as the same-graph input interface; this is independent mathematics/time-error propagation, not an independently rewritten graph compiler.

## Original and independent evidence

| dt s | Original final HXsec abs error K | Independent final HXsec abs error K | Whole-thermal product vs independent max K |
|---:|---:|---:|---:|
|2|0.00339403846288633|0.00339403846295473|8.882e-13|
|1|0.00240145001441761|0.00240145001449681|8.361e-13|
|0.5|0.00150229300840010|0.00150229300841841|1.088e-12|
|0.25|0.000896719367574406|0.000896719395934204|9.734e-9|
|0.125|0.000508664862820751|see recurrence-result.json|2.449e-9|
|0.0625|0.000274718463913359|see recurrence-result.json|6.143e-10|

The coarsest original final EOCs are 0.49909713474, 0.67673955489, 0.74443776178. Independent recurrence reproduces 0.49909713472, 0.67673955492, 0.74443771617. Thus even an exact linear BE solve of the independently built original thermal equations has the same coarse failure. Independent sparse residual is at most 3.411e-13 W. Original Newton max scaled residuals on dt2/1/.5 are 1.2151e-12/9.8754e-13/1.7782e-12. The small dt.25 discrepancy is compatible with its admitted original Newton stop (max scaled residual 1.673e-10); its HXsec discrepancy is only about 2.84e-11 K and cannot explain the millikelvin error/order failure. No tighter Newton option was used.

The two finest stored levels are read-only context from the original frozen packet, not newly run refinement and not replacement coarse acceptance. Original final EOC sequence remains `.499/.677/.744/.818/.889`. Finer evidence does not convert any failed coarse band to PASS.

## Where the coarse final observable loses simple first-order behavior

Four local truncation families (water, solid, secondary, primary) sum to the final error. At dt2, they contribute -0.001021099744, -0.000004391936, -0.002225027538, -0.000143519245 K. They have the same sign; inter-family cancellation is therefore not the explanation. Water's own final EOCs are .230/.433/.531/.680/.813; local-secondary's .578/.818/.921/.966/.985.

For a stronger attribution, `temporal_attribution.py:15–25` solves the exact discrete adjoint of the independent operator, from a unit HX-secondary final objective:

`dual_i=(D_i+C/h)^(-T) adj_i`, `localContribution_i=-dual_i*C*delta_i`, `adj_(i-1)=(C/h)*dual_i`.

The sum of every cell/time contribution reproduces the same final error. At dt2, all contributions sum positive 0.05314774963 K and negative -0.05654178809 K, net -0.003394038463 K: sum of absolute contributions / absolute net = 32.318. Water alone has positive 0.05110270603 K and negative -0.05212380577 K, net -0.001021099744 K, cancellation factor 101.094. This is within-water/cell/time cancellation, not cancellation between final family totals. The coarse dt1/dt.5/dt.25 all-cell/time factors are 22.989/18.410/15.423, so the response still changes substantially over these original grids.

At dt2, interval contributions to the final HXsec error are:

|Right endpoint s|Contribution K|
|---:|---:|
|2|+0.003905184468|
|4|+0.002101041471|
|6|-0.000862654699|
|8|-0.008537609703|

All direct adjoint weights are positive; the changing sign comes from the original smooth temperature trajectory's local time defects, not from spurious negative mixing weights. The observed HXsec error is positive early and negative at final t=8 s. Its whole-trajectory maximum is at t=2 s on the first four grids, whereas the final criterion samples t=8 s. The final error/h changes materially over the coarse grids (-.001697/-.002401/-.003005/-.003587 K/s), rather than a settled leading constant.

The local secondary capacity/diagonal ratio is about .748758 s at t0; the smallest water ratio is .0297633 s. These are local capacity/outflow ratios, not global eigenvalue time constants, so they are supporting context rather than a standalone stiffness proof. The direct recurrence/adjoint reproduction is the primary evidence: coarse BE truncation propagation and an especially cancellation-sensitive final observable explain the pre-asymptotic EOC.

## Actual source implementation witnesses and rejected explanations

Read-only exact original source witnesses:

* `source-frozen/src/backend/liquidcooling/solver/equation_graph.py:485–517`: directed mixing and four thermal balances; explicit subtraction of admitted Pa/W source families; secondary/primary signs match the independent matrix.
* `source-frozen/src/backend/liquidcooling/solver/dae_newton.py:619–629`: differential `(y-old)/dt` and right-endpoint residual; unchanged backward Euler.
* `source-frozen/src/backend/liquidcooling/manufactured_benchmark.py:163–171`: canonical-ID binding of all source rows.
* `source-frozen/src/backend/liquidcooling/dae_experiment.py:44–57`: manufactured declared initial state and derivative, original continuous residual atol gate.
* `evidence/independent_trajectory.py:77–79`: independent cycle flow and smooth storage trajectory/analytic derivative; `evidence/DERIVATION.md:45–50`: endpoint_hold contract and original warning on final coarse cancellation.

Initial mismatch is ruled out by e0 exactly zero plus same initial digest across six grids. Source-time/ID/unit mix-up is ruled out by exact times/coverage, continuous independent equation audit and full-trajectory recurrence match. Product thermal signs or BE rate error would prevent that match. Hydraulic root drift is orders below temperature error and its independent reference is bitexact. Newton precision is orders below this HXsec millikelvin error. No manufactured AD inference is used for these conclusions.

## Acceptance limits and disposition

The required coarse final-HXsec 0.85–1.15 criterion still FAILS. The original frozen REPORT:34 explicitly describes the separate whole-trajectory band as a PRIVATE criterion, not the historical one-CP unchanged final test; REPORT:35 openly preserves failed coarse final-HXsec rates. This audit keeps those distinct metrics. It does not assert a historical old fixture gate was applied unchanged to a different full-system norm.

No true production bug was identified in this narrow path, so there is no candidate source patch and no normal integration request. Changing phase, end time, source identities, metric, time integrator, tolerances, coarse grid or band just to obtain green would define another problem/criterion and is not performed. Existing ordinary control AD/FD evidence, single-source manufactured AD, accepted checkpoint replay and real 17-frame UI success remain separate evidence; none upgrade this failed coarse MMS criterion. Hardware/model-parameter calibration and physical predictive accuracy are outside this mathematical audit.

One audit harness attempt guessed `layout_sha256`; the actual typed field is `graph_layout_sha256`. The unsuccessful script and error classification are preserved in `reference_identity.attempt01.py` and `reference-identity-harness-attempt01.json`. Corrected script succeeded; this was a harness typo, not a product RED.
