# Perimeter21 prepared actual-LF queue, 2026-10-11

Current prepared source and actual candidate registry: PERIMETER21_PREPARED_QUEUE_v2.json.
SHA256: 9d9f503f8095152442fb68b39d8191242dc248816fac6d6fd39f9f7a0d8aabd8.

The complete source-bound prepared_check and independent serialized-input readback pass for every one of the21 points. No physical call has been purchased. The source/claim/call-budget/complete-process-cost tests pass.

Root authorization is a separate exact-byte JSON record. Start from PERIMETER21_EXECUTION_AUTHORIZATION_TEMPLATE_v2.json, write a new root-owned approval file, set execute=true, retain the exact prepared/proposal SHA and point_count21, and record the root approval identity. The template itself is not permission. Compute the approval file SHA, then execute the command below on Pro6000 from the repository. This task has not created an approving record.

    env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 UV_THREADPOOL_SIZE=2 GOMAXPROCS=2 CUDA_VISIBLE_DEVICES="" DCDTUNI_NODE=/home/litianyu/.local/opt/node-v22.23.2-linux-x64/bin/node taskset -c 30,31 nice -n 10 /home/litianyu/miniconda3/envs/dcdtuni/bin/python -m agent.harness.shinaide_perimeter_lf_queue_v2 --prepared case/agent/shinaide-design-compile-20261011/PERIMETER21_PREPARED_QUEUE_v2.json --prepared-sha256 9d9f503f8095152442fb68b39d8191242dc248816fac6d6fd39f9f7a0d8aabd8 --authorization <root-approved-repo-relative-JSON> --authorization-sha256 <exact-root-approval-SHA> --execute

The command prints the unique queue run, PID, claim and dated logger path immediately after admission. A prepared SHA can acquire only one persistent exclusive claim; it is never removed or silently reused. Duplicate starts stop before creating another running logger. Each physical point is a distinct owned child, checks the exact authorization and live queue-parent claim, and records the real frozen v3 oracle logger as it is constructed. Original frozen source/project bytes are never edited.

One native liquid attempt and one air attempt are allowed per point. Branch120s, point600s and whole12600s are the dispatch/evaluation limits; cleanup/reaping and all failure costs are retained. CPU30/31, two-thread libraries, nice10 and GPU invisibility are checked at runtime. The new point meter includes its process birth/imports, preflight, actual branch setup/evaluation, output work and waited descendants. Parent wait/reap CPU is an independent full meter; stage-specific native costs remain available.

Valid but infeasible original constraints are saved and the queue continues. A failed numerical gate, code exception, runtime failure or timeout stops further dispatch. Completed, failed and partial stages all remain recorded; there is no retry. A repaired/recovery scope requires a new root-reviewed prepared identity.

Zero-solve independent readback can run before or after the queue. After execution, pass the actual queue receipt or failure file to also validate exact point/terminal/source identities, native accepted liquid heat provenance, branch calls and full process costs. Partial failures expose raw native process receipts through the actual owned oracle handle, rather than inventing an unavailable physical score.

    python -m agent.harness.shinaide_perimeter_lf_readback_v1 --prepared case/agent/shinaide-design-compile-20261011/PERIMETER21_PREPARED_QUEUE_v2.json --prepared-sha256 9d9f503f8095152442fb68b39d8191242dc248816fac6d6fd39f9f7a0d8aabd8 --queue-receipt <actual-queue-receipt-or-failure> --output <new-owning-case-readback-JSON>

The earlier v1 preparation is retained as a zero-execution checkpoint. Use the v2 prepared identity and claim-first queue above.
