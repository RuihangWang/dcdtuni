# Simulated PID control review

## Verdict

**Needs fixes.** No Critical defects were found, but three Important issues remain in the scoped PID implementation: the `direct`/`reverse` labels use the opposite polarity from standard PID controller-action terminology, manual-output tracking is not bumpless when tensor gains override `Kp`, and boolean/complex tensor inputs are normalized before type validation rather than rejected.

## Scope and evidence

- Reviewed only `docs/developer/liquid-control.md`, `src/backend/liquidcooling/control.py`, and `src/backend/tests/test_liquid/test_liquid_control.py` from `f9a23a94..3b2f3137`. The intervening thermal commit was not reviewed.
- Inspected `HEAD=3b2f3137` on `codex/liquid-control-platform`; no source, index, or branch state was changed.
- Ran only the focused controller test file with bytecode and pytest cache writes disabled: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ... pytest -q -p no:cacheprovider src/backend/tests/test_liquid/test_liquid_control.py`. Result: **15 passed in 3.80 s** on this run.
- Ran four minimal read-only reproductions for controller polarity, manual tracking with overridden tensor gains, boolean measurement handling, and finite-input overflow.

## Spec compliance

| Requirement | Assessment | Evidence |
|---|---|---|
| Explicit nonmutating state | Compliant | Frozen result/state dataclasses are returned; `pid_step` creates a new `PIDState` and does not mutate the input tensors. |
| Biased, bounded direct/reverse PID | Partially compliant | Bias and hard bounds work, but the action names have nonstandard/reversed polarity; see I-1. |
| Integral antiwindup and per-second slew limit | Compliant for the reviewed finite scalar cases | The projection accepts only the admissible integral increment at hard/slew limits, and the slew distance is `rate_limit_per_s * dt_s`. The two added boundary regressions cover the former whole-update rejection and requested-command diagnostics. |
| Derivative on measurement/filter without setpoint kick | Compliant | The derivative uses measurement delta only, applies a first-order filter, and the setpoint-step test passes. |
| Manual-output tracking | Partially compliant | It is bumpless with config gains but not with an active tensor gain override; see I-2. |
| Reject invalid state/input | Not fully compliant | Non-finite and nonscalar inputs are rejected, but boolean/complex tensor values can be silently converted; see I-3. |
| Optional tensor gains preserve multi-step gradients | Compliant in the smooth tested region | State tensors retain the autograd graph and the centered finite-difference test for `Kp`/`Ki` passes. Active hard constraints remain intentionally piecewise differentiable. |
| No real equipment commands | Compliant | The module has no I/O or equipment/protocol integration; it only computes simulated tensor commands. |

## Important findings

### I-1 — `direct` and `reverse` are opposite to standard PID controller-action polarity

**Files:** `src/backend/liquidcooling/control.py:125-131`, `src/backend/tests/test_liquid/test_liquid_control.py:68-72`

Standard PID terminology defines direct action as controller output increasing when the process variable increases, and reverse action as output decreasing when the process variable increases. The implementation sets `direction=+1` for `direct`, then uses `error = setpoint - measurement` and derivative `-d(measurement)/dt`; therefore a higher measurement lowers output. `reverse` does the opposite.

Focused example with `Kp=0.05`, setpoint 30, bias 0.5, measurement 35 produced `direct=0.25` and `reverse=0.75`. For a cooling valve/pump whose command should rise when temperature rises, the test requires `action="reverse"`, although that is direct controller action under the usual instrumentation convention. A caller choosing the documented names by standard convention will select the wrong feedback sign and can create positive feedback in a closed-loop simulation.

**Action:** Either swap the direction mapping (and update the cooling test), or rename the field to an unambiguous sign/process convention and document the exact equation. If backward compatibility already matters, reject ambiguous legacy values at the binding boundary and migrate them explicitly.

### I-2 — Manual tracking is not bumpless when `pid_step` uses tensor gain overrides

**Files:** `src/backend/liquidcooling/control.py:86-92`, `src/backend/liquidcooling/control.py:118-124`

`initial_pid_state` back-calculates the integral with `config.kp`, while `pid_step` may immediately use a different `kp` from `gains`. The advertised manual-output tracking and optional tensor-gain paths therefore do not compose.

Focused example: config `Kp=0.1`, setpoint 10, bias 0.5, measurement 8, manual output 0.4. Initialization stores integral `-0.3`. The first automatic step remains approximately 0.4 with config gains, but jumps to 0.6 with `gains=[0.2, 0, 0]`, even though measurement and setpoint are unchanged.

**Action:** Let `initial_pid_state` accept and validate the active tensor gains (at least active `Kp`) and use them for back-calculation, or provide an explicit tracking/back-calculation transition in `pid_step`. Add a regression using overridden tensor gains and both action polarities.

### I-3 — Scalar normalization silently accepts invalid boolean/complex values

**Files:** `src/backend/liquidcooling/control.py:68-72`, `src/backend/liquidcooling/control.py:109-123`

`_scalar` calls `torch.as_tensor(..., dtype=torch.float64)` before inspecting the source type/dtype. Python booleans and `torch.bool` observations/state values become 0.0 or 1.0. Complex tensors are cast to real values, discarding their imaginary part with only a warning. The tensor-gain path has the same normalize-before-validate behavior. This is inconsistent with `PIDConfig` and `dt_s`, which explicitly reject booleans, and it does not meet the stated invalid-input rejection boundary.

Focused example: `measurement=True` was accepted and emitted a bounded output of 1.0 rather than raising `ValueError`.

**Action:** Validate the source before dtype conversion: reject Python `bool`, boolean tensors, complex tensors, and unsupported objects for measurement, setpoint, every state field, manual output, and `gains`. Add parameterized tests covering these paths.

## Minor findings

### M-1 — A finite-input overflow can escape through `unclamped_output`

**File:** `src/backend/liquidcooling/control.py:145-160`, `src/backend/liquidcooling/control.py:162-165`

The final finite check covers the projected `raw`, applied output, integral, and derivative, but not the original `requested` tensor returned as `unclamped_output`. With finite `Ki=1e308`, setpoint `1e308`, and `dt_s=10`, the integral request overflows; antiwindup projects the stored state back to a finite value and output 1.0, while `unclamped_output` is `inf`. Include `requested` in the finite check (preferably before comparisons/projection) so diagnostics and optimizer graphs cannot carry a hidden non-finite value.

### M-2 — The final test-duration claim requested for provenance is absent from the scoped document

**File:** `docs/developer/liquid-control.md:11-13`

The document records the original 13-test result as 3.33 s and says the final suite has 15 cases, but it does not state the reported final `15 passed in 3.18 s`. The current focused rerun confirms the outcome/count but took 3.80 s, which is expected to vary by host. If the 3.18 s figure is intended as historical RED/GREEN evidence, record the exact command/environment alongside it; otherwise omit exact wall-clock values and state only the pass count. The final diff is consistent with two regression tests being added and with the prior implementation failing their asserted behaviors, but it cannot independently prove the chronological test runs.

## Positive observations

- The antiwindup fix correctly preserves the original requested command for diagnostics while projecting only stored integral state.
- The pure-integral overshoot case now advances to the reachable boundary instead of rejecting the whole integral increment and pinning the controller.
- Derivative-on-measurement sign is internally consistent with the implementation's chosen action equation, and the filter discretization is coherent.
- The documentation appropriately limits the gradient claim to the controller path and does not claim closed-loop hydraulic/thermal differentiability or real-equipment control.

---

## Focused re-review — `ace317db..33ffe8b0`

### Verdict

**Approved for runtime integration.** All three Important findings and the finite-overflow minor finding from the first review are resolved. No new Critical or Important defect was found in the fix. The remaining documentation inconsistency below is non-blocking.

### Closure of prior findings

- **I-1 closed:** `PIDConfig.action` now defaults to `reverse`; reverse uses `SP-PV`, while direct uses `PV-SP`, consistently for proportional and measurement-derivative terms (`control.py:28`, `110`, `146`, `158`). The developer document states both equations and the cooling regression now correctly selects direct action.
- **I-2 closed:** `PIDState.tracking_setpoint` records a pending manual-to-auto transfer. On the first step, the integral is recalculated from the tracked output using the effective tensor `Kp`/`Kd` and prior derivative (`control.py:58`, `105-113`, `147-152`). The returned state omits the marker and therefore clears it after exactly one step (`control.py:190`). This keeps the transfer bumpless for unchanged measurement/setpoint while preserving normal integral evolution and natural response to subsequent changes.
- **I-3 closed:** `_real_tensor` rejects Python/tensor booleans and complex values before float64 conversion, recursively checking list/tuple gains (`control.py:70-84`). `_scalar`, state fields, observations, setpoints, manual outputs, and the gain path share that validation.
- **M-1 closed:** the original requested command is checked for finiteness before saturation or antiwindup can project it to a finite state (`control.py:172-176`).

The eight added RED paths map to the reviewed defects: polarity, tensor-gain manual tracking, four boolean/complex observation cases, mixed boolean gains, and finite-input overflow. The supplied focused evidence reports **22 controller tests passed in 3.29 s**. No broad suite was rerun during this re-review.

### Remaining minor documentation issue

`docs/developer/liquid-control.md:13` still lists a “reverse cooling action,” while the corrected regression and the newly documented industrial convention call this direct action. Line 17 also describes the eight prior failures without recording the supplied final `22 passed in 3.29 s` result. Update the test-summary wording and either record the final pass count (with timing explicitly historical) or omit all exact timings. This does not block runtime integration.
