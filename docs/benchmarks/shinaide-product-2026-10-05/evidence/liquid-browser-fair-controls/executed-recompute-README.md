# Fresh solve of the real accepted-input export

This verification constructs a new `DAESimulation(executed.liquidrun)`, performs
its steady initializer and all 60 Newton DAE steps, and compares its 61 frames
(0–600 s, dt=10 s) with the untouched browser run's `result.json`. It does not use
`from_checkpoint()`, restored states, or a replayed tensor trajectory. The
checkpoint is read only as a command ledger for independent boundary checks.

The final raw receipt is [executed-recompute-v2-receipt.json](executed-recompute-v2-receipt.json),
the actual new result is [executed-recompute-v2-result.json](executed-recompute-v2-result.json),
and raw execution output is [executed-recompute-v2-output.txt](executed-recompute-v2-output.txt).
The reproducible runner is [tools/shinaide_liquid_executed_replay.py](../../../../../tools/shinaide_liquid_executed_replay.py).

All ten primary physical fields are numerically identical at every sample:
time, node pressure, node temperature, edge flow, chip temperature, edge outlet
temperature, primary return temperature, secondary supply temperature, water
cell temperature, and cold-plate water outlet temperature. The pump/valve/inlet
traces, control records, cold-plate details, diagnostics, and rack summaries are
also exactly equal. Maximum temperature, pressure, and flow differences are all
zero. Both solves report 64 Newton iterations in total and a maximum scaled
residual of 5.222391337156296e-11.

Independent JSON comparisons verify 4,392 materialized heat/pump/valve/inlet
values. Each of the 61 complete compiled parameter vectors (3,385 entries)
exactly matches an independent overlay of the accepted ledger onto the original
input's scheduled parameters. The accepted events are:

| Accepted seq | Sample time (s) | Input |
| --- | --- | --- |
| 2 | 20 | Global heat multiplier 1.2 |
| 16 | 160 | Pump m_30_pump RPM ratio 0.95 |
| 21 | 210 | Valve m_32_control_valve_1778059294098 opening 0.9 |
| 41 | 410 | Primary inlet m_30_hx 294.15 K |

Each event's command agrees with its accepted sample and previous-sample value.
Sample i supplies the parameters for the step ending at time i*dt. The original
input also contains its prescribed heat keyframe; the export preserves that
schedule and folds the accepted multiplier into it.

The materialized export intentionally represents the command history differently.
Its cold-plate Q_W/dT_set_K curves already include accepted heat multipliers, so
the fresh run's additional `heat_multipliers` trace is all ones. Its
`applied_inputs` list is empty because no new online commands are queued.
Fresh run identity, config hash, and manifest provenance identify the executed
export. These differences are recorded separately and are not called a physical
mismatch.

The manifest is unchanged CPU/float64/max_threads=1, with interop threads=1,
affinity CPUs 24–27, and CUDA_VISIBLE_DEVICES empty. Original input, executed
input, checkpoint, result, and executed-inputs hashes are checked before and
after. Liquid source hashes are also stable during the solve. Initialization
plus all steps and the parameter audit took 39.787487185094506 s in this run;
this is an observed verification duration under concurrent server load, not a
performance benchmark.

The first attempt completed the same full numerical solve but failed while
serializing a NumPy integer index into its receipt. Its raw error output is kept
in [executed-recompute-output.txt](executed-recompute-output.txt). Converting
indices to plain integers fixed only the verification runner's record-writing
code. The corrected runner was then executed from a fresh simulation for the
entire horizon. The first numerical result has the same byte SHA256 as the final
result; its redundant copy was removed after checking that identity.

This proves reproducibility of this real interactive browser run from its
accepted-input export. It is separate from checkpoint recovery, MMS accuracy,
time-step accuracy, gradient parity, and real-equipment calibration. Retaining
dt=10 s does not change the independently documented several-kelvin peak chip
time-discretization error for the separate prescribed-load verification.

To reproduce from the repository root with the project Python environment
active, use a new output prefix (the tool refuses to replace previous evidence):

```bash
mkdir -p .codex-tmp/executed-recompute/{tmp,cache,inductor,triton}
CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
TMPDIR="$PWD/.codex-tmp/executed-recompute/tmp" \
XDG_CACHE_HOME="$PWD/.codex-tmp/executed-recompute/cache" \
TORCHINDUCTOR_CACHE_DIR="$PWD/.codex-tmp/executed-recompute/inductor" \
TRITON_CACHE_DIR="$PWD/.codex-tmp/executed-recompute/triton" \
taskset -c 24-27 python -u tools/shinaide_liquid_executed_replay.py \
  --evidence docs/benchmarks/shinaide-product-2026-10-05/evidence/liquid-browser-fair-controls \
  --prefix executed-recompute-rerun
```
