# Actual ordinary closed-ring full20s control differentiability — PRIVATE audit

The real ordinary GUI full20s trajectory supports first-order gradients of its held pump and first-valve command for the final mean temperature of all56 coldplate solids. Baseline all8 output classes/all11frames and real checkpoint v3 replay are bit-identical to the observed files; no production source fix is proposed. This is a stronger trajectory gate than the preserved local2s audit, not a hardware/calibration or all-controls gate.

J_final=329.23729347423085K. Pump AD=-1.6862274888890811K per RPMratio; first-valve AD=-0.11884330479895297K per opening.

| Control | Centered FD step | FD K/unit | AD relative error |
|---|---:|---:|---:|
| pump | 0.01 | -1.6865777360379752 | 0.0002077104965 |
| pump | 0.001 | -1.6862309908276529 | 2.076788924e-06 |
| pump | 0.0001 | -1.6862275242601754 | 2.097646641e-08 |
| valve | 0.01 | -0.1188776250586443 | 0.0002887858071 |
| valve | 0.001 | -0.11884364792535962 | 2.887216972e-06 |
| valve | 0.0001 | -0.11884330831435364 | 2.958013218e-08 |

The predeclared prior2s finest-step gate relative<=1e-4 OR absolute<=1e-8 at h1e-4 passes both controls. Coarse h1e-2 slopes are shown and are not claimed to pass that finest tolerance. Errors fall about100-fold for a10-fold step reduction. All perturbations use full public13constructors and full130dynamic BE roots, with no manual state initialization, mock solver or compensation source.

Independent NumPy across130dynamic roots: all229nodal mass max 1.66533453694e-16kg/s; all56CP energy max 9.09494701773e-12W; twoHX volume energy max 3.67799657397e-09W; physical total energy max 6.01357896812e-09W; node enthalpy max 2.91038304567e-10W. Production/independent energy difference max 3.63797880709e-12W. All original Newton/physical gates and the independent unchanged1e-6W dynamic-root balance criterion pass.

The final LOCAL t20 BE scaled Jacobian is1693x1693, finite, rank1693, singularmin5.31997608686e-05/max1466.15705987, condition2=27559467.1091; transpose solve info0 and defect1.42108547152e-14<original gate6.35714285714e-08. Full trajectory backward passed the unchanged native implicit adjoint check at each of10roots; only the final local Jacobian's rank/condition was independently enumerated here. Substantial conditioning is disclosed, not generalized to arbitrary parameters.

This audit changes no normal case/source/service/Git/browser/GPU.

The real e512006bbf0e4c6db16d918b35818867 original typed input/request/checkpoint and accepted manifest/result are preserved byte for byte in actual/. Original typed canonical config c97ab492ae9c15bc286cc9d3726fabae6bb5a394a3ab6f2e5b36169788916866; accepted config5b836ed78a51bc8c6f0d76d06d624f2e51637148a64bbe54996b0fdc24eea361; graph229nodes/286edges/214pipes/56coldplates/14valves/1pump/1HX,1693states/950differential. Original request=checkpoint full liquidrun; result and execution receipt refer to the same original config; all node/edge ordered IDs bind the admitted graph. No manufactured source, controller, event or replacement toy/mock solver was used. The ACTUAL GUI parameter pack says source=mock/revision=mock-1-dae-v3, synthetic demonstration coefficients, so these derivatives do not certify calibrated vendor/hardware prediction.

Exact accepted command and right-endpoint timing
There is exactly one human command, applied_inputs seq1/time2: heat_multiplier1.2, m_30_pump RPMratio.95, first valve m_32_control_valve_1778059294098 opening.9, primary inlet m_30_hx294.15K. The native traces keep them for samples1..10; t0 has heat1,pump1,valves1,Tin293.15K. The audit starts from the original input, full public DAEExperiment constructor, queues that same command through public set_inputs BEFORE the native seq1 backward-Euler root, then calls step10times and tensor_series. The seq1/time2 receipt names the accepted root's right endpoint, not an instruction to postpone controls to the second root. No advancement or delay was introduced to force matching. The previous2s packet instead admitted accepted schedules and kept host heat multiplier1; accepted Q already included×1.2. A separate exact comparison of all11 complete parameter vectors establishes those two admitted representations' equality; no heat double multiplication. Baseline all8 output classes/all11 samples must be bitexact to the observed GUI arrays before derivatives are accepted.

Objective and physical history
J is t20 mean Kelvin temperature of all56 coldplate solids. Derivatives concern the two scalar seq1 command amplitudes held over all10steps; t0 state, real heat/Tin, time grid, graph and other13valves remain fixed. It is not a derivative through the initial steady root or a steady-state objective. Finite-difference trajectories repeat the full public constructor/set_inputs10step path with only the selected command changed. These12 perturbations are explicitly unobserved counterfactuals; they are not expected to match actual recorded values. In logs their actual_match is empty and the derived comparison flag false means no observed comparison, not a failed baseline gate.

DAEExperiment._theta_at220-236 uses index_copy+stack of command tensors; step245 passes the unchanged physical previous state into backward_euler_step, then263-264 retains next_state/theta without detaching. tensor_series97-98 stacks those accepted tensors. Implicit root backward552 differentiates all explicit physical-history parameters, while numerical warm-start guess has no derivative556. BE619-630 passes previous explicitly. The cross-step graph is therefore retained; the actual10-root backward and multi-step FD test whether it is numerically correct. The once_differentiable decorator limits this evidence to first-order derivatives. No detached state was manually repaired or parameter slice edited.

Every solve uses the original dense Newton options max_iterations40,atol=rtol1e-10,max_backtracks18 and all original parameter/state/thermal acceptance checks. Valve active set stays open, pump not flow-held, and all286 flow signs stay fixed. The exact saved checkpoint v3 is replayed by the genuine public DAESimulation.from_checkpoint(raw,checkpoint) API with original config/event/accepted trace gates; original file remains unchanged.

Independent balance arithmetic is pure NumPy over copied ordered incidence, state/parameter arrays and static admitted layout metadata, without calling production residual or diagnostics to calculate mass, all56 CP energy, both HX volume energy, node enthalpy and total heat/storage/primary-rejection/boundary exchange. The original local2s independent absolute1e-6W dynamic-root balance gate is retained for all130 t>0 AD/FD roots. T0 is separately reported: its saved steady scaled residual1.214757503475994e-11 passes original Newton atollimit, while energy gap1.9021463231183589e-6W exceeds1e-6 yet passes the ORIGINAL heat-scaled energy gate about.0021001W. That initializer is not relabeled as passing the dynamic absolute1e-6 gate, and its threshold was not changed.

Reproduction/evidence limits
Run only the unfrozen PRIVATE working scripts with normal source SHA guards; REPRO.sh contains exact CPU32-33/2threads/CUDAempty/tmp/cache-in-B commands. No runtime service job is created. State/theta serialization reads experiment internals solely to preserve independent conservation evidence; commands/solves/objective/AD and FD all use public APIs. SOURCE-LINES and complete guarded current liquid backend snapshot include the late initializer dependency before computation. Prior2s frozen packet manifest4034d7fb... is independently checked0drift. Harness v1 wrongly omitted raw from checkpoint API after its successful baseline/AD; exact v1 failure is retained and excluded from product RED. V2 changes only that API call plus distinct log file. All remaining source/actual guards are checked after computation and at freeze.

This does not prove second derivatives, all14valve sensitivities, new online command schedules, topology changes, refinement EOC, hardware calibration, empirical accuracy, or original coarse fullMMS HX-secondary EOC/whole-Linf acceptance. It does not upgrade any browser render or CFD convergence gate.

Final identity: 96pre/post guard records/58unique normal paths,0drift; all11 complete host parameter vectors bitexact to accepted-manifest public parameters. Source-only layout packet has separate normal deployment corrections by Root and is not covered or claimed held by these liquid-source guards.
