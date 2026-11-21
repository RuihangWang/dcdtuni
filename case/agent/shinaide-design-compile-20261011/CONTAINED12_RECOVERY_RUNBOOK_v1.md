# Unpaid twelve-fault recovery

Two original normal points were already paid and independently verified. Keep them unchanged. The first fault stopped before any physical attempt because its display/logger label contained a path separator. The original queue, persistent claim, its complete29.900228CPU-second cost and startup failure remain preserved.

The recovery changes only candidate_ref.label from single-off/<UID> to a safe single-off-<UID> display component. fault_id and scenario identity keep their original single-off/<UID> strings. Source/project/action/control/recourse/constraints/v3 evaluator are unchanged. All twelve original fault candidates remain unpaid.

Exact proposal SHA: 16b543882ae51e7b4c509c9bb77f97789cbd5f257a339bfdaa82178295d84e30
Exact prepared path: case/agent/shinaide-design-compile-20261011/CONTAINED12_PREPARED_RECOVERY_QUEUE_v2.json
Exact prepared SHA: bb4bd20abe44239b259a714ac141de2a988918af6a96a6cffbcd4dfa74f764c1

A new root authorization must have format root-contained12-LF-execution-authorization/v1, execute=true, that proposal and prepared SHA, point_count=12. The new authorization template has execute=false and does not authorize any call. The old14 approval and claim do not authorize this recovery.

Approved command only after the new exact root record exists:

    cd /home/litianyu/mycode/dcdtuni
    env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 UV_THREADPOOL_SIZE=2 GOMAXPROCS=2 CUDA_VISIBLE_DEVICES="" DCDTUNI_NODE=/home/litianyu/.local/opt/node-v22.23.2-linux-x64/bin/node taskset -c 30,31 nice -n 10 /home/litianyu/miniconda3/envs/dcdtuni/bin/python -B -m agent.harness.shinaide_contained12_lf_queue_v1 --prepared case/agent/shinaide-design-compile-20261011/CONTAINED12_PREPARED_RECOVERY_QUEUE_v2.json --prepared-sha256 bb4bd20abe44239b259a714ac141de2a988918af6a96a6cffbcd4dfa74f764c1 --authorization <new-root-record-path> --authorization-sha256 <exact-new-root-SHA> --execute

The twelve faults keep the old order. Max1liquid+1air each,120sbranch/600spoint/7200srecovery; original2+new12=14 attempts per branch at most. The recovery cap plus prior24.4693825s queue wall stays below the original8400s scope. Unique dated logger/claim/live parent, complete process and reaped descendant costs remain mandatory. Numeric/code/runtime errors stop; valid thermal infeasibility retains and continues. Zero CFD/GPU/I3/training. No automatic retry or normal repurchase.

Readback uses shinaide_contained12_lf_readback_v2 with this prepared SHA and optionally actual queue receipt. Deep native pressure/heat/pump/energy algebra uses shinaide_contained14_paid_independent_readback_v1 --queue <new-receipt> --output <new-file>. Both perform zero new physical solves. The readback-only IT literal defect was found by actual zero-solve readback and repaired in a new v2 before authorization; source and full cause are preserved in CONTAINED12_READBACK_CONSTANT_REPAIR_v1.json.

Original stopped evidence is fully curated under evidence/contained14-stopped-actual-native-LF-v1 with154files. CONTAINED2_NORMAL_NATIVE_RECOMPUTATION_v1 independently recomputes both valid/feasible native normal states. The partial source/runtime failure does not replace the still-pending fault acceptance.
