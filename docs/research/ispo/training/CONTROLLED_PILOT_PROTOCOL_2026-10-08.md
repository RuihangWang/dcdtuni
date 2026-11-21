# SFT-started controlled physical family pilot — 2026-10-08

This protocol is written before the first online post-training pilot. It validates the executed learning and cost interfaces; it does not replace E1–E8 or the required main seeds/scenes/model sizes.

Common start: completed 4.2B legal SFT run 2026-10-08_033717_SGT__qwen4b-legal-SFT-acbdd38a, sixteen train-only heuristic examples, eight supervised updates, zero HF optimization labels. All methods restore the same LoRA and numerical-head hashes and freeze the complete SFT reference. Actual prompts retain the geometry/material/BC/objective requirements.

Physical interface: frozen controlled2d task source cb30676976a6990ad2f311a71d26ebd123435a12b7bac6b7274b7e3b3cef11d2, LF6 exact dense Newton and HF10 reference Newton with unchanged residual acceptance. Four train tasks, two validation combinations and two extrapolative test tasks are declared in the task manifest. Twenty-two gates on two representative physical tasks and project-derived FD derivatives were independently accepted. This is a heated-wall/lid family; the full inlet/baffle/heat-source layout remains required.

First pilots use seed 301, four updates (one visit to each training task), four IID proposals per prompt, AdamW learning rate 2e-5, gradient norm clipping 1, and complete-SFT joint KL weight 1e-3. Initial/final held-out measurements use one paired-noise draw per task under HF, with no optimization, calibration, stopping or checkpoint selection based on their outcomes. The tiny measurement count is an interface diagnostic, not a significant quality/generalization result.

Arms: HF-RLOO, scalar-AIPW and continuous-LAX. Sparse arms use independent Bernoulli q=.5 and a fixed predictable coefficient 1. Candidate probabilities/draws are saved before current HF calls. No retrospective top-k, forced successful draw, failed-reward dropping or hidden target imputation is permitted. Additional LF-only and fully differentiable-HF references are implemented but not covered by the first three-arm pilot.

Known actuator reward is removed from both HF/LF unknown components before acquisition, then supplied identically to all methods as its categorical score plus exact continuous path gradient. This prevents the known component from acquiring artificial propensity noise. LAX uses the same thermal proxy in its score subtraction and validated physical path derivative; categorical scores are retained for shared LoRA parameters. Missing optional derivatives preserve I0 and stop that derivative arm as an implementation failure.

Cost: every fresh physical evaluation is SHA-bound to a genuine saved cfdproj. Total evaluator cost already includes first operator creation; separate preprocessing details are not added twice. Model load, worker setup, candidate generation, reference forward, optimizer, held-out verification and checkpoint costs are retained. GPU computation and residency/wait are identified separately. Setup/validation/failed attempts are never omitted from full accounting. Equal update counts are not claimed to be equal total budgets; this pilot only makes later cost-frontier experiments executable.

The joint-KL structural probability derivative was fixed by exact two-action enumeration before any online RL run. Seven joint-loss, five numerical-head, eleven estimator and two actual PEFT reference mechanics tests support the interface; the independent auditor additionally checked 75 symbolic sparse-acquisition/common-term contracts. These are correctness checks, not empirical method benefit.

Main work remains: adaptive residual/error acquisition, calibrated component controls and I1 attribution, evolving-policy covariance/cost audits, full controlled-layout tasks, >=10 controlled seeds, strong baselines, 27B main comparisons, flagship DC/airfoil and E8 simulator validation.

## Version 2 amendment, before its online runs

The v1 HF and raw-target LAX pilots are retained with exact source snapshots. They completed actual training but did not establish comparative benefit. Its LAX route is mean-correct; applying purchase weighting to the large raw thermal reward introduced unnecessary acquisition variance.

For v2 define delta=Z/q*(U_H-beta*U_L). The discrete score uses beta*U_L+delta with the full augmented peer baseline. The continuous route uses delta with its own peer baseline plus beta times the same LF path derivative. Conditional acquisition expectation retains the HF target. If U_H=U_L and beta=1, the continuous correction is identically zero and the path reference remains. Known actuator components stay outside acquisition identically across arms. No propensity or purchased outcome is changed retrospectively.

Independent algebra review supports this formulation. Twenty-five joint/head/estimator tests pass, including exact acquisition expectations and matching-proxy path recovery; complete reference tests separately pass. These do not establish lower whole-training variance/quality/cost. Sparse v2 arms keep the original seed, common SFT, four updates, q=.5, coefficient1 and held-out exclusion; they receive new dated runs and explicitly versioned losses.
