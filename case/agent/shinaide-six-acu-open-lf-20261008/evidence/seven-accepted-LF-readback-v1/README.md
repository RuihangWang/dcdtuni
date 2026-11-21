# Seven accepted Shinaide joint-LF reference points

Normal operation and all six registered single-ACU outages completed successfully and satisfy the declared mock-task constraints. The selected operating setpoints and task thresholds are unchanged. This supplies the normal/N+1 LF reference for the flagship research pipeline; it is a reference evaluation, not an optimization or training gain.

The corrected implementation binds the registered 84 kW IT budget to each actual canonical server cooling.power field, obtains liquid removal/storage from the accepted native liquid result, and transfers the residual heat to the actual-geometry OPEN air model. In the OPEN initializer, pressure is re-closed after the linear enthalpy seed changes buoyancy. This removes the measured unbalanced all-inflow upper-node state and its singular pressure/temperature direction without changing the equations, coefficients or residual tolerances.

## Verified evidence and independent entry

- SUMMARY.json: all seven scenario results and measured successful point costs.
- INPUT_INDEX.json: byte identities of every copied raw source/state/result and the common grouped STL files.
- INDEPENDENT_READBACK_V2.json and PER_UID_RECOMPUTED_V2.json: independent normal/fault state, heat, flow, energy and constraint checks.
- src/agent/harness/shinaide_seven_point_readback_v2.py: reproducible readback entry.
- Each scenario folder includes its actual evaluated .cfdproj, native input/executed liquidrun, accepted-input provenance, native result, saved liquid state/theta, saved air state/fields, source-bound fan contract, air manifest, energy, assessment and original receipts.
- The eleven identical grouped STL files are stored once in shared_STL. The reader creates only repository-local temporary hardlinks for state residual evaluation.

The readback checks source/run identities, recomputes per-UID IT / liquid removal / storage / residual air heat and 1D enthalpy, reevaluates the original air and liquid residuals at the saved states, reconstructs pump flow/head/pressure, recomputes fan and shared-plant electricity using direct scalar equations, and checks each registered thermal/capacity/flow/zero-fault condition. It does not call any Newton/root solve. All seven independently pass. Direct energy/PUE/reward arithmetic agrees with the original summaries.

To reproduce on Pro, set PYTHONPATH=src, repository-local caches, CPU30/31 and two threads, then run:

    /home/litianyu/miniconda3/envs/dcdtuni/bin/python -B -m agent.harness.shinaide_seven_point_readback_v2

Normal design-point PUE is approximately 1.295; the six outage points are approximately 1.345. Exact values remain in the source summaries. These use the declared synthetic equipment/plant model and fixed operating points.

## Why all six outages give the same LF result

All six ACUs have identical port areas/heights and equipment curves. In a single-outage case the five survivors use the same flow and temperature, and connect to the same public OPEN cold/hot mixing nodes. Horizontal unit identity/position does not distinguish those current LF equations, so permuting the failed UID gives the same aggregate thermal and energy solution. Each fault still has its own real project, externally bound UID mask, physical solve and retained record. They are not independent random seeds or evidence that spatial HF outage effects are identical.

## Cost and repair history

The successful queue is 2026-10-09_002726_SGT__seven-joint-LF-mass-initialization-repair-pass-ddb31a7f. Seven liquid and seven air steady evaluations completed; summed per-point wall time is about one minute. Exact process-tree measurements, whole-queue GNU time and the deliberately parked pre-release interval are retained with their different boundaries.

The earlier normal liquid success followed by the missing hybridHeatBudget key remains failed and retains its fees. The subsequent normal liquid success / air linear-step failure, the one authorized air-only diagnostic, and the saved-state pressure-projection replay also remain recorded. Raw failing J/rhs/step and the repaired full-rank linear check are in the separately referenced repair package; they were not overwritten. The first independent-reader pass reached all numerical checks but failed to serialize numpy.bool_ details; its evidence and full time remain alongside the repaired native-bool writer.

## Next experiment

Connect the same projects and task-owned fault masks to the 40901 HF chain, including truly adiabatic closed inactive ports, then use the verified LF schema in the planned learning comparisons. No additional paid run was started during this readback.
