# Positive-curvature full-ring MMS: independent PRIVATE design

A new, separately identified full-system mathematical fixture can test the same coarse final-time temperature-order band without the original sine fixture's strong time/cell cancellation. The recommended design is `full-ring-positive-curvature-lowcycle-v1`, using the complete 229/286/1693-state graph, all 950 storage states and explicit mock physical inputs. Its independent conservation-law BE experiment passes the unchanged 0.85–1.15 band at dt=2/1/.5/.25 s and final t=8 s for every temperature family. This is independent design evidence, not a production Newton/React/browser/checkpoint acceptance claim. The original sine fixture and its failed coarse final HXsec EOC remain unchanged and failed.

## What was changed, and what is held

Only the artificial mathematical target trajectory, declared initial state/rate and its explicitly derived MMS forcing are new. The original full graph, scene/geometry/models, pipe lengths/bores/cell counts, cold-plate loads/capacities, valve/pump curves, HX coefficients, fluid, gravity, primary circuit and saved controls remain identical. `structural-and-preservation.json` verifies proposed project physical sections byte-equivalent as parsed data and all manual input-carrier fields unchanged except time grid and manufactured_benchmark. `explicit-physical-inputs.json` and `lowflow-explicit-physical-inputs.json` are identical complete coefficient/profile/boundary records. No normal source, actual case, service, browser, Kit, GPU or Git operation occurred.

The data use the explicit synthetic pack `liquid-demo-equipment / mock-1-dae-v3`, with saved-project overrides. In particular, secondary fluid rho=992.2 kg/m3, cp=4180 J/(kg K), mu=.000653 Pa s, k=.632 W/(m K); gravity vector [0,-9.81,0]. The only pressure datum is m_30_return at 150000 Pa; there is no prescribed node temperature boundary in this graph. Primary HX flow is 1 kg/s at 293.15 K, with rho=992.2, cp=4180 and .02 m3 on both HX sides. All 56 physical CP heat sources, individually 100–650 W, remain present, total 21000 W. Full per-edge resolved values and explicit IDs are included; these are mock/saved-authoring values, not measured equipment calibration.

This work considered two preserved designs. The first held the original t0 cycle flow (max 26.024 kg/s, artificial momentum compensation up to approximately 143.85 MPa). It passes the independent coarse band but retains an unnecessarily large synthetic circulation. The selected lowcycle variant changes only the artificial flow target, not any physical coefficient or gate. It minimizes sum_e |m_e| in the original exact integer cycle space with constraints each CP>=.00606 kg/s and pump/HX>=56*.00606=.33936 kg/s, cycle coefficient bounds [-2,2], followed by a declared deterministic cycle jitter (seed20261006, amplitude1e-6 kg/s). These are explicit construction choices, not measured DC operating targets or parameter fitting. The objective was reducing artificial flow/source scale, not searching for a passing EOC; temperature polynomial parameters were fixed before both experiments and never tuned.

The selected graph flow is constant and satisfies all-node mass balance to 2.78e-17 kg/s; min |m|=3.3068e-7 kg/s remains above the unchanged 1e-8 sign-regime gate, max |m|=.33936019 kg/s. Maximum absolute independent head loss is 35627.82 Pa. Its initial-coarse source ranges are approximately momentum [-34594,19015] Pa, water [-348,1890] W, solid [-525,55] W, HXsec [4131,13983] W, HXpri [1175,13777] W. These artificial sources can heat or cool and are not predictions of plant actuators or real operational heat balance.

## Explicit continuous solution

Let B be the directed incidence matrix (+from/-to) and Ccycle the independently constructed 286x58 integer cycle basis. The exact fixed m*=Ccycle*c satisfies B*m*=0; no mass source exists. All edge signs are declared and fixed. The same all-active ordinary pump/valve/CP/physics-HX modes are retained; this design does not cover closed/held-flow/setpoint/rise modes.

For canonical storage index k=0..949, let the initial b_k equal the old independent sine reference at t0 (nonuniform positive Kelvin storage values), and define

`v_k=.012+.006 sin²(.073 k)` K/s,

`a_k=.005+.003 sin²(.137 k)` K/s²,

`T*_k(t)=b_k+v_k t+a_k t²`, `dT*_k/dt=v_k+2a_k t`.

All a_k are strictly positive and nonuniform. Complete b/v/a values with IDs are frozen; temperature range over [0,8] is approximately 293.15–303.38 K. Initial temperature rates are explicit nonzero v, not replaced by a steady solve. The 229 ideal-mixing node temperatures are the exact linear mixing solution at the prescribed fixed m. Thus node b/v/a are found from the same public conservation equations, and Tnode*(t) is quadratic too; no node forcing is introduced.

For each free pressure node n,

`p*_n(t)=p0_n+beta_n t²`, where `p0_n=150000+1000*n/(N-1)+200 sin(.193 n)` Pa and `beta_n=.3+.2 sin²(.193 n)` Pa/s².

The original datum node instead has p0=150000 Pa and beta=0 exactly. All 286 edge flows remain constant. Algebraic flow/pressure/momentum accuracy needs pointwise verification; no hydraulic time-convergence order is asserted because the model has no hydraulic inertia.

## Public-law sources, without product F manufacturing

`propose_lowflow_fixture.py` evaluates explicit NumPy equations, not product residual F, a product thermal helper or a Newton result:

* Water: `Swater=rho cp V Tdot - |m|cp(Tup-Twater)-Qdistributed`.
* CP effectiveness epsilon=`1-exp[-UA/(|m|cp)]`; `Ssolid=Csolid Tdot-Qphysical+|m|cp epsilon(Tsolid-Tup)`.
* HX secondary: `Ssec=rho cp Vsec Tdotsec-|m|cp(Tup-Tsec)+UA(Tsec-Tpri)`.
* HX primary: `Spri=rhop cpp Vpri Tdotpri-mp cpp(Tin-Tpri)-UA(Tsec-Tpri)`.
* Momentum: `Smom=L(m*)-p*_from+p*_to` in Pa. The explicit smooth Darcy/blended friction, elevation, quadratic CP/HX loss, Kv characteristic and pump curve formulas are in the generator. Gravity signs follow the authored edge dh. No inertial, mass or mechanical-work-to-heat source is invented.

All four thermal sources are in W; pressure Pa; time s; temperatures/rates K and K/s; flow kg/s. Initial pressure/flow/node/storage vectors, initial 950 temperature-rate vectors, fixed flow signs and complete per-family source IDs are explicit. Samples use the original endpoint_hold contract, and source times equal each exact admitted grid. An independent SHA binds each full typed namespace; no hidden time callback or source recomputation during the BE experiment exists.

Continuous global energy uses physical Q + artificial W sources - primary rejection - total storage rate, keeping physical and artificial heat separate. Maximum sampled residual of the selected lowcycle design is 1.53e-10 W; independent non-grid checks at .37/1.23/2.71/4.19/7.83 s give max 1.05e-10 W. Original all-node mass conservation is retained.

## Why this design removes the diagnosed cancellation

At fixed m/coefficients, write `C Tdot+D T=Q+S`. For the quadratic solution, the exact local BE derivative defect is `[(T*_i-T*_(i-1))/h-Tdot*_i]=-a*h`. Therefore the error equation is

`(D+C/h)e_i=(C/h)e_(i-1)+C*a*h`, `e0=0`.

Every storage forcing in this error equation has the same positive sign. K=D+C/h has nonpositive off-diagonal entries. Each storage row is strictly diagonally dominant from C/h; algebraic node rows are weakly conservative. A dependency-path check shows all 1179 rows reach one of the 950 strict storage rows, giving a nonsingular M-matrix and nonnegative inverse. Thus e_i>=0: the original positive/negative time/cell cancellation is absent. `structural-and-preservation.json` records this check; maximum numeric row dominance deficit is 5.68e-14, ordinary coefficient-rounding precision.

This proves absence of cancellation, not a universal analytic guarantee that any finite-grid ratio lies within .85–1.15. The actual requested four original coarse grids are checked independently; no finer grids were used or substituted.

## Independent coarse experiment

The exact public-law D/C matrices and analytic quadratic derivatives are assembled independently over all 1179 thermal unknowns; SciPy sparse LU advances only the direct BE error equation. Product F/Newton did not create sources or errors. Typed backend admission is separately exercised as a schema/layout/ID/unit/SHA resource gate; it does not count as initialization, Newton, replay, React compile or browser acceptance.

|Family|Final error dt2 K|Final error dt1 K|Final error dt.5 K|Final error dt.25 K|Three final EOCs|
|---|---:|---:|---:|---:|---|
|229 node temperatures|.12792705|.06396742|.03198466|.01599256|.999912/.999957/.999979|
|892 water temperatures|.12792709|.06396744|.03198467|.01599257|.999912/.999957/.999979|
|56 CP solid temperatures|.12787081|.06394827|.03197689|.01598908|.999710/.999876/.999943|
|HX secondary|.11277257|.05666097|.02839934|.01421688|.992989/.996498/.998253|
|HX primary|.09768385|.04965390|.02503990|.01257452|.976213/.987678/.993725|

All remain within the unchanged [.85,1.15] band. All noninitial temperatures have positive error, and final max equals whole-trajectory max as a consequence of this design's observed monotone response; the criterion is still final-time, not silently replaced by whole L-infinity. Sparse error solve residual <=9.10e-13 W. Independently evaluating the literal thermal equations at BE states against the fixed right-endpoint sources gives local residual <=1.72e-8 W and global energy residual <=2.84e-8 W. Finite-difference evaluation of the quadratic derivative defect suffers expected subtraction rounding up to 2.19e-8 W after multiplying large capacities; no tolerance/gate was changed.

Selected source SHA identities:

* dt2: b8a1ebf00e4227b3ab63e5e242da021f2ed36e1733d1c3e676ea6752bed10735
* dt1: dbb9adf084c5abda2a8c3b785e363f934941d55ac5d7147c0f4a68bade5aae65
* dt.5: efcc6b1d4af11a82e98d2fe21158ccad2742785b95ad09bca057e4c1d4126786
* dt.25: 995a8b5749efde484acb438e5b6860192986ea1567e5ba0f0e3655b07ce97d0c

Graph/storage layout SHA stays dde3685500f48cafa5fe0eccce7f2ce077217cc755b1fb4b676bdd5e31b40831. New source/initial/formula identities intentionally differ from the old sine fixture; its manifest49c2e46... and failed .499/.677/... EOC are never rewritten.

## Reviewable proposed files and remaining acceptance

`proposed-lowflow-fixtures/quadratic-lowflow-dt-*.cfdproj` are new PRIVATE proposal files, preserving physical model fields and explicitly adding this different benchmark namespace, matching grid/duration and a PRIVATE case-path suggestion. They were not exported by actual React during this task. `*.design-input.json` are clearly wrapped manual admission carriers: their inherited source_project metadata refers to the original geometry/source, and is NOT claimed to be the compiler identity of the proposed project. They should not be routed as accepted production liquidrun artifacts. Their only role is typed admission of the new namespace against the original graph.

Before calling this a new full product-chain MMS acceptance, a separate authorized PRIVATE pass must compile each proposed .cfdproj through the real current React compiler, verify new input/source-project identities and unchanged graph/physical fields, exercise actual constructor/initial continuous gate, run the actual coarse Newton trajectory with original nonlinear/mass/energy gates, compare every component to these independent formulas, and verify accepted-prefix/checkpoint replay/source identity. An actual browser Prepare/Start/render check and suitable declared source/control AD test remain separate follow-up acceptance. No such product-chain PASS is claimed here.

The first highcycle trial is preserved in `quadratic-*`, `proposed-fixtures/` and `proposed-fixture-result.json`; lowcycle files are separate and selected explicitly. It is not suppressed as if only a single numerical trial existed.

No additional measured data are required to verify a declared mathematical manufactured solution. To claim physical plant predictive accuracy, actual/as-built pipe routing/bores/roughness and fitting losses, rack heat transfer/capacitance, pump and valve characteristics, HX/circuit conditions, load histories and measured temperatures/flows/pressure drops would be needed instead of this mock pack and artificial sources. This task does not ask the user to supply them or block independent mathematical verification on unavailable measurements.
