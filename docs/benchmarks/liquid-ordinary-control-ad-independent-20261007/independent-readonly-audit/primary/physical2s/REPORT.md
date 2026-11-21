# Actual ordinary closed-ring control differentiability — PRIVATE audit

The current unforced production DAE supports first-order pump and one-valve control gradients for this complete saved ordinary GUI graph. No production defect was reproduced and no source fix is proposed. This packet contains no candidate patch and changes no normal case/service/Git/browser.

## Actual input identity and scope

- Real normal run `e512006bbf0e4c6db16d918b35818867`, `Shinaide_RingRear_CUA`: 20 s / dt=2 s / 11 saved frames. Completed/converged receipt, request+checkpoint original liquidrun equality and original config `c97ab492ae9c15bc286cc9d3726fabae6bb5a394a3ab6f2e5b36169788916866` all checked. Original and accepted files are preserved byte for byte; all three actual saved cfdproj files and both real steady/transient run six-artifact sets are included.
- Executed/accepted typed config `5b836ed78a51bc8c6f0d76d06d624f2e51637148a64bbe54996b0fdc24eea361` equals the receipt. Accepted manifest already contains Q×1.2 and Tin=294.15 K at index 1; the experiment heat multiplier remains 1 to avoid doubling the online heat command.
- Request source_project digest `89485bb8a9bf6a0e57a4c7043a01f0226f49ea717ab4b543f1f3776b4f905c30` names a canonical project snapshot. It is not asserted equal to the byte digest of a saved .cfdproj. Each actual file byte identity is separately in `normal-before-guards.json`.
- Actual graph: 229 nodes / 286 edges / 214 pipes / 56 coldplates / 14 valves / 1 pump / 1 HX, 892 water cells, 1693 states, 950 differential states, zero steady neutral-storage selection. Original and accepted graph ID order are checked against saved output. No `manufactured_benchmark`, no manufactured source series, no compensation source.
- Numerical scope is the single actual t0→t=2 s backward-Euler window with identical fixed initial physical state. It is not a derivative of the full 20 s trajectory, a steady-state objective, a time-refinement study, or a closed-valve/topology-change derivative.
- The actual parameter_pack explicitly says `source=mock`, revision `mock-1-dae-v3`, synthetic demonstration coefficients. No toy case or mock solver is substituted here; the real GUI input itself uses this declared pack. Mathematical sensitivity does not certify vendor calibration, experimental prediction, or hardware accuracy.

## Public API and fixed objective

The baseline AD starts with normal `admit_dae_runtime_input(executed.liquidrun)` and the complete `DAEExperiment` constructor. Tensor commands enter through `DAEExperiment.set_inputs(pump_rpm_ratios={m_30_pump:p}, valve_openings={m_32_control_valve_1778059294098:v})`, then normal `step()`. The objective is mean temperature (K) of all 56 coldplate solids obtained through public `tensor_series('solid_temperature')[:, -1].mean()`.

The FD roots use public `definition.parameters(1, pump_rpm_ratio=..., valve_opening=...)` and public `backward_euler_step`, not hand-edited parameter-vector slices. Baseline pump=.95, selected valve=.9, other 13 valves=1, prescribed heat=25200 W and primary inlet=294.15 K are identical across AD/FD. No graph/schedule mutation or pseudo-control is used. Every root uses unchanged dense Newton max_iterations40, atol=rtol=1e-10, max_backtracks18, original parameter/state validators and original physical diagnostics; all 286 flow signs remain fixed, min |flow|≥0.00846556 kg/s, every valve remains open and the pump is not flow-held.

The reconstructed t0 and host t2 outputs are **bit-identical** to the saved real GUI arrays for all 8 classes: 56 solid, 229 node temperature, 229 pressure, 286 flow, 892 cell water, 286 edge outlet, 1 primary return and 1 secondary supply. The isolated public BE baseline is also bit-identical to the host tensor-command state. This links the gradient to the actual saved physical run rather than a replacement fixture.

## Results

Baseline J=326.8804038982119 K. Pump AD=-0.4701426746761541 K per RPM ratio; first valve AD=-0.03247549205986534 K per opening. Both are finite and nonzero.

| Control | centered FD h | FD K/unit | AD relative error |
|---|---:|---:|---:|
| pump | 1e-02 | -0.47023309045926 | 0.00019231563 |
| pump | 1e-03 | -0.47014357869557 | 1.9228619e-06 |
| pump | 1e-04 | -0.470142683752783 | 1.9306116e-08 |
| valve | 1e-02 | -0.0324837396220801 | 0.00025396266 |
| valve | 1e-03 | -0.0324755745282346 | 2.5394032e-06 |
| valve | 1e-04 | -0.0324754930147719 | 2.9403914e-08 |

Errors drop by approximately 100× per 10× step reduction, consistent with centered-FD truncation. The gate declared before the numerical worker was relative≤1e-4 OR absolute≤1e-8 K/unit at h=1e-4; both controls pass. The h=1e-2 slopes are shown transparently and are not claimed to pass that finest-step threshold.

All 13 isolated roots require 3 Newton iterations and zero backtracks. Maximum original scaled residual is 3.67799657397e-13, nodal mass 5.55111512313e-17 kg/s, momentum 8.73114913702e-11 Pa, |energy gap| 2.63025867753e-09 W. The baseline actual heat decomposition is 25200 W input = 17186.50070232424 W primary rejection + 8013.499297677784 W storage (boundary exchange0), within 2.03e-9 W.

`independent_balances.py` independently recomputes NumPy incidence mass on all 229 nodes, all 56 individual coldplate solid balances, both HX volume balances, nodal enthalpy and whole physical heat/storage/rejection from copied raw graph IDs, output cell IDs and public scheduled parameters. It calls no production residual/diagnostics to calculate these balances. Across all 13 roots: mass max 5.55111512313e-17 kg/s; coldplate balance max 9.09494701773e-12 W; HX balance max 3.67799657397e-09 W; all-node enthalpy max 2.32830643654e-10 W; total physical energy max 2.63025867753e-09 W. This confirms internal conservation for the admitted mathematical model, not empirical calibration.

Complete scaled BE Jacobian 1693×1693 is finite, numerical rank1693, singular min 5.31997609146e-05/max 1466.15708659, 2-norm condition 27559467.5876. The condition number is substantial: this is evidence only at this state and scaling, not a guarantee over arbitrary controls. Independent transpose solve info0, defect 1.55431223448e-14 versus unchanged production adjoint gate 6.35714285714e-08. AD remains first-order; production `once_differentiable` does not support a second derivative here.

## Evidence and reproduction

- `physical_control_ad.py`, `worker-v1.log`, `RESULT.json`: real host tensor-control AD, all full-graph FD roots, original guards and Jacobian. No failed product run or implementation workaround occurred.
- `independent_balances.py`, `independent-numpy-balances.json`: independent arithmetic conservation checks.
- `actual/`: all 15 actual original files (three projects, six steady files, six transient files). `preflight.json` records raw/accepted identities and graph/layout.
- `states.npz`: actual reconstructed initial state, baseline/12 FD roots and full scaled Jacobian, NumPy float64.
- `SOURCE-LINES.txt` and `source-snapshot/`: inspected implementation and exact source byte snapshots. Recorded pre-compute guards cover 38 imported backend source files and 21 actual/source guard records, with overlap; all 59 guard records / 53 unique normal paths are unchanged after computation and at freeze. One dynamically imported hydraulic initializer helper was identified after the worker and separately snapshotted in `dynamic-initializer-dependency.json`; it has a current SHA receipt, not a pre-compute guard. Do not upgrade that helper to before/after identity evidence.
- `REPRO.sh`: run copied scripts only from an **unfrozen private working copy**. They import the current normal IT backend and require its frozen guard SHA match. They emit outputs under their own script directory. Do not execute directly inside frozen-v1.

No normal files were written by this audit. CPU32–33 / two threads / CUDA empty; no browser, normal runtime job, service, Kit, build or Git operation was started. This local control gate does not upgrade the earlier full-MMS coarse HX-secondary EOC or whole L∞ criteria to a pass.
