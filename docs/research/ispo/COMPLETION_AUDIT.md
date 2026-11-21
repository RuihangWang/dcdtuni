# ISPO requirements-to-evidence completion audit

This is the live completeness record for the **entire journal study**, not a checklist for declaring an initial draft complete. A milestone may close its own interface/gate while all broader experiment requirements remain open. Scientific completion requires the evidence below, not only a small smoke run, a working PDF, or migrated legacy code.

Audit scope: current Mac `Research/DCTwin/AGENTS.md`; the complete current `ISPO_PLAN_v2_2026-10-07.md`, including sections 13 and 14 and the 2026-10-08 user decisions. Source inspection baseline: `research/ispo-v2-20261008` at `c609730e0057b22222015dc853e2870c6f36f700`; engineering baseline `3c6760df`. Concurrent owner work is not assumed complete.

Status meanings:
- **VERIFIED / bounded scope**: the named primary receipt or source identity has been inspected, and only its stated acceptance condition is covered.
- **PARTIAL / IN PROGRESS**: a prerequisite or implementation is present, or a worker is acting; the complete requirement has no accepted receipt yet.
- **OPEN**: required study evidence is absent. “Pending” in the paper has the same meaning; it is not an experimental result.

## Existing evidence and its exact scope

| Evidence | Verified fact | What it does not establish |
|---|---|---|
| `case/agent/artifact-contract-smoke/contract_receipt.json` | `scope=artifact_contract_only`, `schema_errors=0`, `training_run=false`, `physics_run=false`, `method_or_design_benefit=null`; original SingleRack SHA-256 was independently rechecked in this audit | Any physical solve, air-energy score, post-training improvement, controlled-layout experiment, or E1–E8 result |
| `docs/manuscripts/ispo_tase/QA_REPORT.json` | A format/source/PDF verification record; it is superseded after any new authoring until refreshed | Physical validity or scientific performance |
| `src/agent/training/`, `src/agent/harness/` canonical locations and the committed setup history | Code organization and compatibility, with documented directed checks | A new ISPO implementation or new result merely because historical implementations moved |
| `case/agent/controlled2d-pilot-20261008/logger/2026-10-08_013539_SGT__controlled2d-numerical-gates-3230e637/receipt.json` plus raw evaluations/status | Root independently accepted 21/21 declared bounded gates, recomputing LF/HF FD from raw plus/minus results; this auditor read the primary receipt/status and matched current adapter SHA-256 to it. Read-only kernel identity is `367f0f6`; previous single-point kernel hashes were independently rechecked. Forward/conservation/buoyancy/adjoint/true-HF-on-LF-residual and selected perturbation checks are covered | Broader heat-source/boundary/obstacle layouts, continuum accuracy, 10-seed learning, HF cost improvement, LLM experiments or any E1–E8 comparative result; `training_run=false`, `ISPO_benefit=null` |
| Air-pipeline owner progress and new pilot case | Real React geometry/headless export and CFD are being worked on | W0/W3/W6 for the complete scene architecture menu, v1 energy, or paper-quality layout experiments |

No confirmatory E1–E8 comparison is accepted at this audit snapshot. The paper now records the bounded controlled evaluator verification; training/design-benefit results remain pending.

## E1–E8 completion matrix

| ID | Full requirement | Acceptance evidence required | Current status |
|---|---|---|---|
| E1 | Information effects: I1/component controls, I2/residual features, I3/sensitivities, correction, acquisition, and discrete integration, in defined fidelity cells | Preregistered fractional-factorial/alias matrix; same backbone and numerical head; raw runs; evaluable main/interaction contrasts; >=10 controlled and >=5 DC training seeds | OPEN; estimator implementation/pilot is in progress; no factorial learning result |
| E2 | Full cost–quality frontier from zero-HF proxy training to all-HF reference, including deployment cost | Complete HF/CPU/GPU/queue/generation/backprop/calibration/mesh/failure ledger, budget sweep, independent-run intervals, measured reference uncertainty | OPEN; a cost-logged call or short pilot is a prerequisite only |
| E3 | Black-box, surrogate-gradient, residual-only, residual+adjoint, and validated target-adjoint interfaces where defined | Physical/derivative identities, partial/full residual labels, actual measured costs, matched interface learning comparisons across required scenes | OPEN comparative study; bounded controlled primal/adjoint and true-HF-on-LF residual gates are accepted; wings/DC required interfaces remain open |
| E4 | Strong method baselines: MFPG-PPO budget variant, faithful MFPG/RLOO, prediction-powered/nonphysics acquisition, HPO/learned controls, ABC/ABC-Align adaptations; other HF uses and planned ablations | Defining equations/implementation fidelity, matched SFT and architecture, equal tuning and total cost; named adaptations; complete seeds and terminal runs | OPEN; reference estimator code is being implemented, not a completed baseline experiment |
| E5 | No-LLM and amortization: heuristics, CMA-ES/BO/adjoint optimization, equally informed structured neural policy | Same specifications/parameterization/budget; setup+training+deployment cost; break-even workload and held-out-task quality | OPEN |
| E6 | Original Qwen + harness, named frontier model, Phythesis/SGA-style refinement, LLM structure + local gradient refinement + HF validation | Pinned models and prompts, same tools/acceptance rules, equal evaluation/cost budgets, independent seed/task comparisons | OPEN; no new LLM/agent comparison accepted |
| E7 | Held-out requirements/geometries/scales/architecture combinations/wording, 4B and 27B, plus declared cross-scene experiments | Leakage-resistant split manifest, model/config hashes, 4B/27B footprint and completed runs, requisite seeds, final physical checks | OPEN; footprint is not a learning result, paraphrases cannot leak across physical task splits |
| E8 | Training-HF exploitation/model sensitivity check on representative final designs | Refined-grid/GCI and initialization/transient checks, physical closure, feasibility/ranking drift, failures/costs, raw post-training final projects | OPEN; no final trained-design audit yet |

E1/E3 cells with unavailable information are undefined, not failed or omitted successful evidence. A zero-HF E2 arm cannot be called unbiased target correction. A finite all-HF optimizer/training run is a reference, not a certified upper bound.

## W0–W12 implementation matrix

| ID | Required output and acceptance | Evidence snapshot | Current status |
|---|---|---|---|
| W0 | Bound-patch supply temperature/flow writes, full boundary fingerprints, balanced supply/return, actual headless exporter parity, convergence interception/failure codes, research-branch safety fixes | Air exporter pilot is being developed; original artifact receipt only covers schema/file identity | IN PROGRESS; full W0 OPEN |
| W-SEC | Explicit HTTP loopback trust default off, actual-peer authorization, private credential bootstrap, default loopback bind, traceable random browser session, opt-in token-authenticated terminal, server-free research pipeline | Security receipt `docs/research/ispo/security/receipt-2026-10-08_033935_SGT.json`: 245 server tests, zero skips, including real ephemeral HTTP/WS and actual-entrypoint sentinels; root independently repeated 245 | VERIFIED bounded Python S1–S5; wrapper/Vite changes have separate ownership; S6/full M1 pipeline not closed by security tests |
| W1 | Versioned current task specification, fixed inventory, ACU architecture menu/pairing, v1 plant+fan energy, PUE primary, exhaust margin secondary, inlet/exhaust hard bounds, sources/units | User's 2026-10-08 decisions are in the current plan; equipment curves and remaining numerical thresholds need frozen checked source records | PARTIAL specification; no complete versioned executable spec receipt |
| W2 | Tasks/prompts with hidden specifications and train/calibration/validation/test/OOD physical identities | No accepted new task-generator/split receipt | OPEN |
| W3 | New DSL -> real cfdproj compiler for rooms/rows/racks/ACUs/containment/routing/cutouts; geometry validation and safe rejection | SingleRack artifact schema smoke is genuine but not a compiler for the whole menu | OPEN beyond partial exporter work |
| W4 | Shared differentiable v1 energy module with plant reset, chiller/tower/pumps, fan heat, coil/approach limits; return-flow statistics; hand/conservation/limit tests | Pro energy/objective/metrics 51-test immutable module receipt `51a343e0`, CPU Torch/FD/guards and manufactured closure. Coefficients are mock, true CFD integration/calibration absent | VERIFIED bounded module contract; full calibrated/integrated W4 OPEN |
| W5 | Calibrated multi-layer zonal/L0 model with pressure–leakage/recirculation and design ranking/error characterization | No accepted Pro zonal calibration receipt | OPEN |
| W6 | Actual candidate->mesh->HF->metrics, systematic calibration tasks, convergence/closure/face statistics, grid/initialization/transient protocol and cost distributions | Air/CFD pilot ongoing; controlled 6/10-grid dependency case is distinct from DC W6 | IN PROGRESS; complete calibration collection OPEN |
| W7 | Face-based inlet/exhaust constraints, requirement matching, PUE/energy and auxiliary metrics, source-pinned reward definitions, signed gap to best-found references | Pro objective/metric modules in receipt `51a343e0` distinguish exact lexicographic selection from bounded scalar I0 and measured versus inferred RHI. Inputs manufactured; physical face parser/requirements/references not integrated | PARTIAL verified module contract; full W7 OPEN |
| W8 | Original LLM can use validation/compiler/preview/L0/HF tools, understand failure feedback, produce genuine candidates; valid fingerprinted cache | Artifact/harness record contract verified in its narrow scope; new task-facing tool use not yet accepted | PARTIAL contract only; research harness OPEN |
| W9 | Immutable JSONL ledger with code/model/case/evaluator/config identities, stage timings, attempt costs, propensities and terminal status; reproducible totals | Case logger/identity contract exists; no paper-budget reconcilable complete physical+training run yet | PARTIAL infrastructure only |
| W10 | Reproducible analysis of full-cost learning curves, gradient MSE, score-weighted covariances, discrepancies, feasibility vs quality, spatial thermal views | Manuscript layout QA is not study analysis | OPEN |
| W11 | Matched heuristics/optimizers/inference agents/structured-policy baselines; best-found HF-verified reference and its search cost | No accepted new reference search | OPEN; do not label optimizer output “known global optimum” |
| W12 | Heuristic-format SFT; RLOO/numerical head and its reference KL; information contract; cross-fitted controls/recorded acquisition; 4B and 27B study arms with requisite seeds | Migrated old training interfaces exist; new estimator code being implemented; no ISPO SFT/RL result yet | IN PROGRESS prerequisites; full integration OPEN |
| I2 | L1 field lifting into actual target mesh/operator; complete vector or explicitly partial residual; closure/BC consistency and lifting cost | Controlled true-HF residual-on-LF interface verified in bounded gate; no accepted DC full-vector receipt | IN PROGRESS / OPEN DC pipeline |
| I3 | LF path derivatives and HF adjoints checked against their exact forward objective/parameterization at multiple step sizes | New controlled gate independently supports the declared two-parameter adjoint/FD checks; DAFoam wing setup and DC adjoint study need receipts | VERIFIED bounded controlled gate; OPEN other required interfaces |

## Scene, statistical, model, and acceptance requirements

| Requirement | Release evidence required | Current status |
|---|---|---|
| Controlled mechanism pilot | Source dependency identity, genuine project/decision mapping, 6/10-grid primal/adjoint and closure/FD checks, preserved failures, actual cost | VERIFIED / bounded: 21-gate receipt `3230e637`, raw FD recomputed and accepted by root; source/adapter identity checked. Full distribution/layout coverage remains open |
| Broader controlled layout suite | Heat-source/boundary/obstacle variations and held-out physical tasks; meaningful discrete–continuous decisions; full-HF reference and E1–E4 study | OPEN; lid/thermal-band pilot does not close it |
| Wind-cooled data-center flagship | Compatible architecture/containment options, fixed inventory, v1 physical energy/constraints, zonal/L1/HF, new hard projects and E1–E8 main evidence | OPEN, with headless exporter/CFD prerequisite work in progress |
| Mixed air–liquid stage | Real project coupling, calibrated heat paths, liquid gradients where used, physical closure, same main mechanisms under defined target | OPEN; old platform receipts are interface leads, not new ISPO results |
| Airfoil/2D aerodynamic scene | Valid parameterization, fine/coarse physics, verified DAFoam or equivalent adjoints, independent task splits and method results | PARTIAL: actual embedded-STL CST, differentiable Hess–Smith LF, native SA-RANS/continuous-adjoint execution, explicit wall-resolved2D geometry/BL spacing and partial residual tool exist in airfoil-cst-rans-gate-20261008; strict refined primal/adjoint convergence, shape-FD/grid gates and whole study remain OPEN. Native continuous adjoint is distinct from uninstalled DAFoam discrete AD |
| DC target adjoint scope | Plan section 9 places DC adjoint in the first article; section 13 defers it in the first air benchmark. Preserve as an explicit unresolved scope/implementation item until the user/root resolves it or the required interface is demonstrated | OPEN; do not silently remove |
| Seeds | Controlled >=10; DC >=5 independent training seeds; aerodynamic target >=5 with final protocol frozen; separate seed/task hierarchical uncertainty | OPEN; software tests, repeated solver calls, or evaluator repeats are not independent training seeds |
| Common starts and tuning | Same format-only heuristic SFT, same numerical head, same task information/grammar/compiler; equal hyperparameter trials, their cost included; common analytic mechanical/energy terms treated identically and reported separately | OPEN |
| 4B | Pinned model/head/reference/LoRA, footprint check, SFT and all claimed training/evaluation arms with full costs/seeds | PARTIAL: bounded 4B language/physics bridge receipt b05ad799 accepted by root and read in this audit; SFT/main comparison, long DSL, seeds and complete costs remain OPEN |
| 27B | Same identity and completed comparative arms, measured feasible single-GPU configuration or an explicit authorized resource/scope decision | PARTIAL: bounded bridge receipt 5c33c20e identifies actual Qwen3_5ForCausalLM in directory alias Qwen3.8-27B; root accepted it and this audit read primary receipt. Full SFT/main/long-DSL/seed evidence remains OPEN; old fit assumptions are superseded only for this measured configuration |
| All final physical scores | Same versioned target evaluator for confirmatory test designs and displayed accepted examples; E8 check retained separately | OPEN |
| PPI secondary large-population evaluation | Randomized HF labels and recorded propensity; an appropriate target-functional estimator/uncertainty; explicit statement that individual unlabeled designs were not HF-checked | OPEN; mean correction is not automatically valid IQM/quantile correction |
| Cost ledger | Training plus oracle/data acquisition, generation/backward, preprocessing/mesh/lifting/closure/adjoints, retries/failures, cache/queue, conversion sensitivity and deployment amortization | OPEN; no raw cost stage silently excluded |
| Reproducibility and manuscript | Provenance of every reported number; current source/PDF QA; full evidence-backed result/discussion/conclusion, submission format/anonymity/metadata | PARTIAL draft/format only; journal completion OPEN |

## Method/theory alignment and corrected conditions

The current manuscript derives the following **conditional mathematical identities**, not empirical learning benefits. They are based on fixed policy/evaluator identity, correct action densities, dominated differentiation, finite moments, predictable/cross-fitted coefficients, and declared sampling units.

| Topic from plan section 14 | Correct treatment in manuscript | Remaining empirical acceptance |
|---|---|---|
| Score/compile identity | Sum the actual grammar-constrained token score plus numerical-head density, no action-dependent length normalization; preserve latent/compiled numerical identities and compiler/rejection semantics | Density/serialization/compiled-design replay checks |
| RLOO and partial HF | Complete-peer RLOO is action-independent; missing-peer HT denominator is original K-1, not selected count. Conditional acquisition independence yields first-moment identity. Group dependence is not discarded in covariance analysis | Unit tests and group-level estimator audit, then LLM use |
| Multi-component CV | Centered gradient covariance determines A and b. Shared-score b includes subtraction of mean-gradient inner product. Oracle nesting gives 1-R^2(1-n/N) under its explicit IID/constant-coefficient conditions | Actual covariance/coefficient fitting and matched-cost improvements |
| Estimated coefficients | Predictability prevents coefficient-selection bias; it does not guarantee lower variance. Excess variance is the quadratic deviation from the population coefficient | Independent calibration, uncertainty and safe-interface selection costs |
| STE | A biased STE vector may be a matched auxiliary CV with an estimated mean; uncorrected STE remains biased. Discrete path correctness is not assumed | Declared correction/RELAX/enumeration identity and bias diagnostic |
| Residual feature | Same fixed feature on paired and all cheap designs; its whole acquisition cost included. Enlarging an oracle CV span cannot worsen fixed-sample variance, but finite fits/cost may | Full/partial residual calibration on evolving policy and per-cost value |
| LAX | Conditional path and score terms must use the same fixed differentiable proxy; full shared-parameter gradient includes discrete score term. Consistent soft proxies/unroll are allowed; inconsistent implicit/soft/hard derivatives are not. Approximate path vectors can instead enter same-psi AIPW | Directional forward/derivative match and HF differential variance/cost |
| AIPW probability | q chosen before target outcome; q>0; actual probabilities recorded; oracle q uses square root of conditional squared gradient error divided by incremental cost | Calibrated error predictor, weighted fitting, correct missingness and cost |
| Defensive probabilities | q_D>=alpha q_uniform bounds E[||error||^2/q_D] by 1/alpha of its uniform weighted moment; it is not a universal same-factor total-variance theorem | Heavy-tail/propensity diagnostics and measured robustness |
| Two regimes | Corrected target updates can be unbiased at any positive target probability under conditions; “feasible line” is an operational variance/cost threshold. Trusted-proxy substitution is explicitly biased and uses a scoped value/score-gradient bound | Concentrated-bias exploitation test, calibrated margins/stability, reference discrepancy |
| Residual/adjoint bound | Complete same-operator/boundary/closure, local unique branch, stable inverse and smooth objective; partial residuals have only scoped/empirical trust. Value error does not by itself bound path-gradient error | New primal/adjoint/FD/grid/initialization evidence; actual bound calibration |
| Complete budget | C_remaining=N(g+cheap_specific)+n*HF_incremental. HF-only per sample also pays shared g. Setup/calibration costs use their own C0; all-failure costs are not again divided by convergence rate unless a distinct retry-until-success model is declared | Full cost reconciliation and sensitivity studies |
| “No worse” safeguard | Oracle coefficient/oracle arm choice can give a specified fixed-sample or idealized equal-cost bound. Predictable estimated coefficients alone cannot; choosing zero must also skip unnecessary costly interfaces | Monitored choice and held-out estimator/whole-training evidence |
| Statistics | Minimum seeds and preregistration are preserved. The plan's universal “3/5/10 seeds detect x sigma” numbers are not used as a theorem. Pilot variability informs sample-size/power planning | >=10/5 seed study plus hierarchical intervals, primary hypotheses and Holm correction |

Primary technical references checked for this alignment: RLOO `2402.14740` section 2.3; LAX/RELAX `1711.00123` equations 5/8 and their conditions; PPI++ `2311.01453` (oracle/asymptotic power tuning); active inference `2403.03208` section 7 (conditional squared error); multifidelity model management DOI `10.1137/15M1046472`; ABC `2609.36058`; ABC-Align `2609.34374`. These are targeted source checks, not a replacement for the complete experimental comparison.

## How requirements close

A row is closed only by a new immutable receipt/run whose code, physical design, model/evaluator identity, task split, seed, cost and terminal status support that exact row. Root should recheck primary evidence before changing status. Failed attempts stay in the owning logger. State correctness/evidence scope and independent acceptance, then update the paper's claims and numbers. Do not promote a marker, passing import, short update, case schema witness, or a small PDE gate into a completed full-scope experiment.

The active full-paper goal continues until its required implementation, studies, evidence-backed manuscript, and reviewable output are handled or the user explicitly changes scope. This audit currently records substantial open work; the drafting/algebra pass is not the goal's completion.

## Accepted bounded evaluator gate — 2026-10-08

Receipt: `case/agent/controlled2d-pilot-20261008/logger/2026-10-08_013539_SGT__controlled2d-numerical-gates-3230e637/receipt.json`; immutable status confirms all 21 declared gates and no training. Root independently recomputed raw directional finite differences at the recorded step sizes and verified the adapter hash; this auditor also read the primary receipt/status and checked the current adapter SHA. Its grid-sensitivity gate explicitly does not certify continuum accuracy. The case still has failed attempts preserved. Results section A now contains only this bounded evaluator verification. Every required main learning, broader-scene, model, seed, and deployment comparison remains open.

## Air-energy/objective/metrics module contract — 2026-10-08

Primary receipt: `case/agent/air-energy-contract-20261008/evidence/module-contract-2026-10-08_023349_SGT__air-module-verification-51a343e0.json`. 51 focused tests passed with actual CPU PyTorch and no skips; recorded source hashes and actual cfdproj schemas were rechecked by the owner. The case contains a genuine unchanged SingleRack geometry carrier and explicitly manufactured data. Calibration/model/vendor validation, CFD coupling, optimization and ISPO learning are not established. A failed recorder path attempt was preserved and corrected. Root independent acceptance should retain exactly this bounded scope.

## Additional bounded mechanism/interface evidence — 2026-10-08

Root accepted the 4B bridge at 015615...b05ad799 and large-checkpoint bridge at 021905...5c33c20e by reading terminal status and verifying every HF candidate SHA plus nonzero scalar/physical LoRA gradients. They close the basic external-gradient interface and short-context resource feasibility only. They do not close SFT, main post-training, long DSL, seeds, quality or E1–E8 comparisons.

Root independently recomputed all twelve DWR predictions/errors from goal_residual_summary.json at 022730...f113ddd8: predictor used no target forward or target fields, transpose/directional checks pass, and recorded scalar post-validation supports the goal-correction table. The raw residual ranking and correction/setup costs remain disclosed. The 60-design landscape is fixed-task evidence, not held-out geometry/task-family generalization.

The finite-policy response-table estimator audit uses ten calibration seeds with modeled costs and exact common mechanical gradients; it is not an online solver-time or LLM efficacy experiment. Forced multifidelity is not economic on the original coarse/fine instrument. Cost-aware interface selection, independent numerical profiling and full-budget main learning remain necessary.


## W-SEC bounded source/integration gate — 2026-10-08

Primary receipt: `docs/research/ispo/security/receipt-2026-10-08_033935_SGT.json`; 245 complete server tests pass with no skips, and root independently repeated the same complete suite with explicit Node22 PATH. Source/test hashes are frozen. Gate covers actual HTTP request/login/logout/auth-check behavior, real ephemeral WebSocket handshakes, administrator-only terminal authentication before a PTY sentinel, explicit bootstrap credentials and actual startup wiring with listener/thread sentinels. Existing credential/user/session files were not accessed. No product service, browser, PTY or shell was launched. S1–S5 are bounded Python entrypoint acceptance; S6, wrapper/UI behavior, CFD and main journal results remain distinct requirements.

Auditor also read primary `b05ad799` and `5c33c20e` bridge receipts above. Both explicitly record `SFT_started_main_study=false` and `comparative_ISPO_benefit=null`. Measured short-token stochastic-head feasibility does not settle full DSL training/evaluation cost or efficacy.

## SFT-started online and open-channel mechanisms — 2026-10-08

Root accepted sixteen legal train-only SFT labels/eight actual supervised updates and the common complete reference checkpoint. Four executed online pilot runs (HF-RLOO, retained v1 LAX, v2 LAX and scalar-AIPW) contain 114 new physical calls; every child response/source snapshot/candidate SHA was independently verified. Each has four updates; this closes execution/recording only, not minimum seeds, matched-budget efficacy, adaptive I2/I1 or E1–E8. The first parent recording failure stays failed with explicitly unknown old IPC-only per-call cost. Future process metering includes imports and reaped children, avoiding a full-CPU claim from stage-only sums.

The new channel's pinned-pressure/obstacle/inlet physical gate is bounded to A/D while A–D topology/engineering is represented. Root independently reran thirteen channel topology/mapping tests and reconstructed both stored 600x600 transpose equations and lambda-times-residual goal corrections. Frozen prediction hashes/timestamps precede new HF truth. Mapped mass/heat defects remain explicit; corrected design I3, four-action RL, continuous obstacle geometry and wider cases remain OPEN.

40901 engineering main was rechecked at 3c6760df, with concurrent dirty engineering source. No uncommitted source was imported to Pro; research publication stays on research/ispo-v2-20261008.

## Airfoil entry implementation checkpoint — 2026-10-08

See case/agent/airfoil-cst-rans-gate-20261008/evidence/IMPLEMENTATION_CHECKPOINT_2026-10-08_0605.json and the live case README. It pins source ownership and distinguishes executed native RANS fields from accepted physics/derivatives. Sharp-TE prototype/source and failed meshes remain preserved; the successful2D BL geometry is a new explicitly declared finite-TE CST case. The original strict allGeometry determinant failure is never labeled Mesh_OK. Refined runs that hit iteration limits are not converged. No airfoil learning or comparative benefit is accepted; formal shape-FD, grid, complete-residual and independent-run requirements remain open.

## Root independent information/parameter/channel acceptance -- 2026-10-08

The task-normalized I2-value gate and four-update residual-LAX execution are
accepted as interfaces with original LF gradients, not corrected-value I3.
Fixed-policy full-parameter moments preserve24 groups/192 fresh PDE calls,
past-only calibration, fixed-coefficient counterexamples and full paid costs.
Root independently reconstructed all16 evaluation full vectors/variance traces.

All A-D physical channel structures now have actual LF/HF forward gates, and the
four-action language/numerical policy completed a legal SFT plus4 online updates.
Root accepted30 durable replies/17 source snapshots and real candidate identities.
The initial/final same-problem channel files do not measure generalization.

These additions do not close10 controlled/5 DC seeds, information/adaptive/full
strong-baseline matrix,27B main comparison, flagship/mixed-cooling, full aero,
E8 or deployment. Main protocol is being prepared with a newly metered shared
legal SFT rather than inventing missing historical CPU/GPU cost dimensions.

## Shinaide hybrid infrastructure and scoped aerodynamic I3 -- 2026-10-08

Curated terminal manifests under case/agent/shinaide-fan-zonal-lf-20261008/evidence/
bind the new same-source network, nominal-pump DAE and pump through-solve I3.
Root proof99557b7b independently reconstructs the actual matrix/state/FD chain;
joint review121640 and ROOT_JOINT_ENERGY_ARITHMETIC_REVIEW_2026-10-08.json verify
the original245 network equations, true UID heat/flow and one shared plant.

W4/W5 now have bounded Shinaide implementation evidence at declared fixed
geometry and mock equipment. The original14.7m3/s ACU controls and explicit
new4x2.8m3/s catalog candidate are distinct physical projects. The network has
no target-field I2 and is uncalibrated. Whole-energy I3 at tied hard minima is
unavailable as a unique ordinary derivative. Next oracle development preserves
this boundary and reuses one state rather than silently re-solving it.

The pump I3 gate is one actual N coordinate with two objectives; four real
project/control perturbations passed two registered FD steps. It uses the
nominal-affinity full residual with native neutral-storage constraints.
Hydraulic work does not supply an unmeasured efficiency or electrical-power
calibration. Room CFD and all journal confirmatory learning gates remain OPEN.

Coarse airfoil proof755fea8f independently checked the398933-state full
transpose equation and16 actual forward objective traces for three shape
directions plus angle of attack. The frozen1110 terminal manifest is scoped
to those results and completed parameter mapping; live fine-grid work and
all17 independent FD coverage were excluded. Neither mapping nor four-direction
agreement establishes full-domain I3 or learning benefit.

## Matched discrete aerodynamic two-grid gate -- 2026-10-08

Root6623ba2f independently recomputes the actual two original JT equations and
all68 parameter partials from136 native signed operator outputs, including
candidate hashes. Same numerical scheme/steps and original gates pass for
the17-component grid sensitivity. This closes the baseline discrete two-grid
check only. The remaining13 independent direct-HF directions are registered
and running; DAFoam, broader candidates, E8 final-design checks and aerodynamic
training/generalization are not yet established.

## Same-source joint energy, complete local direction set and official AD gate -- 2026-10-08

Root proofs eb7adf65 and ca047b76 accept the v1 source reconciliation and new
v2 same-saved-project compound LF interface. The new v2 point has unique
active hard minima and interior q. One predeclared nine-nonzero joint
direction passed two fresh FD steps; direct algebra, air IFT and liquid IFT
are separately reconstructed. The objective is PUE-only and equipment is
mock: the complete constrained objective, calibration, uncertainty and
comparative learning remain OPEN. The scalar cost gradient has the opposite
sign to a minus-PUE reward. No reduction between the old and new operating
points is treated as an optimization result.

Root b229cb6d supersedes the earlier pending coverage statement for the
baseline native airfoil: all 17 independent spanning directions now have
68 actual side evaluations. The old continuous-adjoint failures and costs
remain, and a sampled-point numerical gradient cannot be reused on an
unpaid design. DAFoam root36578d4b separately accepts the official lower-Re
CD/alpha gate from raw residual/adjoint arrays and actual boundary files.
This does not close same-case shape AD, cross-condition validation, training,
E8, or aerodynamic generalization. Applicable 2D quality evidence is not
relabeled as the failed original 3D determinant check.

Fan export, same-source heat and explicitly paired ACU field contracts are
verified. The MPI-v3 total-enthalpy library actually loads, but Shinaide
constructors, real-mesh component flux/gauge, nonorthogonal heat transport
and all-56 CFD heat closure remain OPEN. No new Shinaide CFD was run on Pro.

The controlled study and E4 guarded pilot queue remain active. Statistical
import/plot tools are prepared with synthetic data, with actual outcomes
sealed until all 70 trials are terminal. The seven-arm study does not by
itself close every I1/I2/adaptive-information/strong-baseline and model-size
requirement. Full manuscript completion remains OPEN.

## Typed reward consumption and guarded analysis -- 2026-10-08

The paid Shinaide energy consumer passes20 tests, including backward agreement
with the actual two-h reward differences. Peer source review agrees that
d_reward=-d_PUE, versioned branch availability wins over legacy flags, and
receipt/project/order/unit binding rejects unpaid new-point use. This closes
a typed energy-only consumption contract, not a fresh oracle, constrained
objective, actual model training or measured benefit.

The analysis importer/plot v2 resolves the independently reproduced v1
first-read, weak-proof and cumulative-cost defects. Twenty-two synthetic
tests and root's real live-process read-order audit72f7c5d9 pass. The70/1400
positive test mocks row import; a separate20-row synthetic trial exercises
raw source/reply/candidate/cost checks. Actual70-trial import and final-test
release remain OPEN while the formal dispatcher is live.

The supported CST16 benchmark decision follows the original plan's DAFoam
aerodynamic scope. Native-map28checks, static constructor audit and new
reference-surface15checks are accepted at their geometry/source scope.
Actual IDWarp, new primal/shape adjoints, full16FD/grid gates, aerodynamic
post-training and E1--E8 remain OPEN. Prior native high-Re CL and official
CD/alpha evidence is not transferred into the new benchmark.

E5 structured2arm/10seed protocol and CPU SFT preparation exist, but no new
SFT/model/PDE run has started because resource admission has not passed.
Per-task optimization and the rest of E5 remain OPEN. G6 original-task MFPG
curve reproduction now has a reviewed-resource bounded warm preparation;
actual warm and full public curve are still OPEN. CPU resource wait and
preparation are not scientific negative results or completed experiments.

## Terminal implementation increments -- 2026-10-08 evening

Supersedes the preceding pending status only at the following bounded scopes.
Actual IDWarp CST16 with paired span/fixed farfield passes geometry checks;
the first flow/shape gate v4 failed after CD due to diagnostic AD-tape lifetime,
and v5 is prepared. Flow shape FD, all16 directions across designs and full
aerodynamic training/E8 remain OPEN. I1 paid numerical components agree, but
the initial area/volume declaration failed; typed zero-PDE replay is accepted,
new live v2 and learning benefit remain OPEN.

G6 warm and one fresh1M HF-only seed3 run are terminal and independently audited;
the corresponding MFPG seed3 is live. Both-curve comparison and20-seed public
reproduction remain OPEN. E5 fresh common CPU SFT exists with a valid checkpoint;
exact final C0 was lost by the outer recorder. Its bound proposal, formal runner
and online pilots still need review; no speed or full E5 claim is supported.

Shinaide closed-zone full56 demand exceeds the current4x2.8 catalog. A new
explicit architecture/zone/fault/catalog/sizing TaskSpec must establish a
nonempty domain without weakening the original requirements. Original case,
fan flows, liquid topology and historical PUE-only evidence remain unchanged.
The full journal E1--E8/model-size/baselines/generalization scope is OPEN.

## First complete seeded cohort and CST16 first physical direction -- evening update

The70registered controlled trials are now terminal and all1400fixed-validation
rows have been source/cost audited. This completes execution of that seven-arm,
ten-seed,64-update submatrix only. The fixed common-wall frontier is unsupported
(lower4334.359s > upper300.722s); the entire delayed model-load trial remains.
A separately labeled fixed-update analysis and descriptive paired intervals
are accepted, with zero final-test access. Full I1/I2/adaptive information,
strong baselines, model scale, generalization, E8 and the matched-cost claim
remain OPEN. The four E4 pilots are terminal at lifecycle/artifact-binding scope,
with full numerical readback and the actual E4 study still OPEN.

CST16 v5 has accepted native CD and CL adjoints plus two-step actual FD for
one joint direction. It repairs a diagnostic tape-lifetime interference,
preserving the v4 failure and costs; no vendor changes or tolerance relaxation.
The full16 fluid directions, grid/wall-regime/design-domain checks and training
remain OPEN. The13-page manuscript includes only these bounded conclusions.
E5's exact SFT terminal cost is unknown;60s is not a verified terminal bound.
Flagship TaskSpec/catalog/zone/fault feasibility and genuine fan-assisted CFD
remain OPEN; the current pure-energy bridge is not the full objective.

## Complete reference and baseline preparation -- 2026-10-08 late evening

Effective components now include the real six-ACU route-coherent Shinaide
reference, full architecture/zone/fault task contracts, two paid structured
feedback arms, complete new initialization measurement, and actual E4 pilot
mechanisms. Original-task reference execution has three accepted trajectories.
All original summaries, adverse outcomes and failed setup costs remain.

Next experiments are the normal/single-fault coupled LF reference, the now-live
full E5 twenty-trial study, the now-live remaining original-task MFPG cohort,
and the predeclared zero-training GPU equivalence/cost check for efficient
MFPG-PPO context reuse. Full adaptive information experiments, flagship HF,
27B study, E5 task optimizers, E6/E7/E8 and the complete journal conclusions
remain part of the objective, with gaps listed as experiments to complete.


## 2026-10-11 evening terminal increments
Supersedes earlier dated pending entries only at the stated scopes. OriginalMFPG/HF-only full40 and structured2arm×10 are accepted; original70 quality and rawprefixes remain immutable. Newv6 includes all61 paidfailedouter attempts and completeBterminal cost; derivedcheckpoint availability changes only prepaidfailed gaps. Original70 common support remains empty, while all40 registeredarchitecture/recipe common support passes unchanged latestaffordable/pairedseed bootstrap. Rootprimaryreceipt is case/agent/controlled2d-deployment-e5-20261011/evidence/ROOT_COST_V6_AND_HF680_PRIMARY_ACCEPTED_2026-10-11.json. All20 terminalstructuredmodels actual680 candidateexports and exact680 originalHF10/Newton45/x0None I0/I1 re-evaluations are source/native-state accepted. Inference andHF commands have disjoint inclusive complete bills; internalsetup/warm/solver costs are nested. Readyservice workloadwall diagnostics are conditional; languageGPUdeployment, nativeoptimizer240, CPU perqueryamortization andsame-quality break-even remainOPEN.

Allsix realShinaide architecture references now pass registered jointLF normal/all6single-off gates with originalinventory/fanheat/liquidtopology/syntheticcatalog. Geometrycompiler/numerichead/conditionalpartialI3/source-languagehook are interfaces, not flagshiptraining orHF benefits. 40901 samefrozen engineering mesh/air convergence remainsOPEN anditsworker ownsrepair.

FulloriginalISPO10 are terminal64, with37training and4recovery sourceidentities unchanged andoriginalfailed1302 retained. Independentreadback/statistics are inprogress under analysisprotocol frozenbeforeaggregatequality access. ConditionalE4 release was valid; firstformalreplylogging O_EXCL collision stoppedat0trainiteration. Minimal loggingonlynewversion/reconciliation is prepared beforeanyretry;39unstartedoriginaltrial identities remain. Bothstudies' scientificbenefits remain subject toindependentnative/parameter/cost verification.

Aerodynamiccoarsecenter full16 andop2joint are accepted. FinefullR plateau has a nativeSA2cycle cause; one .35 nuTilda iterationdampingcoldgate is sourcefrozen/rootreleasedat originalgates, pendingactualresult. Verification/full16 atnewfidelity/design-domain/lifting/training remainOPEN. FulljournalC1–C5/E1–E8, actual4B+27B, flagshipdesign andOOD completion stillrequired.
