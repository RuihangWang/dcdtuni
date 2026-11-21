# E6 local original-Qwen pilot — SOURCE_READY, 2026-10-12

This extends the initial E6 callback package into a runnable local entry. **SOURCE_READY is an implementation status, not an accepted model/physics result.** Preparation used CPU18/19 with two threads. Model-weight binary reads, model loading/forward, PDE, mesh, GPU and external API calls were all zero.

## Concrete entry and scope

- agent.harness.e6_local_qwen_v1: original base text generation through existing ddl_agent.model.load_tokenizer/load_base; no LoRA, trained numerical head, randomly initialized numerical head or policy checkpoint.
- agent.harness.e6_local_pilot_v1: prepare, metadata preflight, exact-authorized run, owned worker and explicitly fake fixture modes. There is a live run implementation; this is no longer a callback-only handoff.
- agent.harness.e6_runtime_contract_v2: narrow live classification fixes over frozen E6 v1. Nonobject/nonfinite model JSON is actor failure; compiler infrastructure errors stop the queue. Exact provider raw text is copied into each model candidate.
- agent.harness.e6_local_pilot_fixtures_v1: CPU negative/transport fixtures.

Real case: case/agent/controlled2d-local-qwen-e6-pilot-20261012/. Each arm owns a unique dated logger, original output/token records, actual compiled cfdproj, structured action and final selected project. The existing public native LF6/HF10 evaluator is used unchanged. No sealed task or outcome is accessed.

One pilot command attempts, in this fixed order, direct prompting, iterative evaluator-tool Agent and structure-plus-LF-refinement on public train-square, seed2301. Direct has one sample and zero repairs/search. Tool Agent chooses its own LF calls and submission, at most six model turns. Structure uses one raw JSON proposal and two fixed LF gradient steps. Each selected design is committed before its own final HF; terminal HF cannot affect selection.

This is a transport/grammar/compile/physical-loop pilot, not a scientific ranking. The original formal requirements remain ten controlled seeds, five flagship seeds, 4B/27B and complete E1–E8. Original planned SGA/Phythesis task adaptations remain explicitly labeled; this pilot does not pretend to reproduce their datasets or methods. Strong frontier-provider and flagship live-wrapper work remain pending.

## Model and sampling identity

The original path is /mnt/lv902/litianyu/models/Qwen3.5-4B, using the same existing read-only asset manifest as current Shinaide preparation. The pinned installed runtime is /home/litianyu/miniconda3/envs/ddl-llm/bin/python; package versions are in the plan.

Text metadata, tokenizer, config and template SHA identities were checked. Base shards were **stat-only** during preparation. Their first byte SHA identities will be acquired inside the approved owned runtime before loading, written into model_weight_identity.json and each provider identity. This is not a comparison against unknown historical base-shard SHA values. Assets are never modified.

The installed Transformers first loader route maps the local config to Qwen3_5ForCausalLM; the loaded class is checked. The existing loader's broad fallback to ImageTextToText is blocked locally, so a first-load exception cannot cause a hidden second model load. Its traceback/stdout remain retained. Model evaluation and inference mode are enforced; every base parameter is frozen.

Generation is single-sequence autoregressive sampling: temperature0.7, top_p0.95, top_k20, one beam, maximum256 new tokens, full prompt plus output at most8192 tokens, use_cache within the call, no cross-call KV reuse and disable_compile=True. Each call has an explicit seed from job seed plus session sample index. The chat template uses enable_thinking=False and add_generation_prompt=True without tools kwargs. EOS IDs are [248046,248044], pad248044. Both complete token IDs and raw text with/without special tokens are retained; no Markdown stripping or reasoning repair is hidden in parsing. Unexpected reasoning/invalid JSON remains an outcome.

Input/output/cached-token counts, full rendered prompts, actual token IDs, truncation/termination metadata, synchronized generation wall time, CUDA event elapsed time and GPU allocation/reservation peaks are recorded before structured parsing. Per-call diagnostics are nested inside the complete owned-run bill, never added to it.

## Budgets, ownership and admission

The entire three-arm pilot has ceilings of eight model calls, seven LF evaluations including two gradient requests, three committed final-HF evaluations and zero search-HF. The plan reserves 900 wall seconds,1500 CPU core seconds,24GiB host RSS,32GiB PyTorch allocator capacity,65536 input tokens and2048 output tokens. One fixed GPU UUID is registered; CPU affinity is18/19 and thread counts are2.

The accepted mfpg_ppo_formal_supervisor_v2 supervises the worker's private process group and retains native wait4, resource samples, owner identity and terminal costs. The existing native physics child remains in that group. Child admission verifies its actual PID/starttick/PGID/session/parent/UID against the supervisor's worker_handle and the persistent attempt claim before weight access. Startup, byte hashing, model loading, token work, real compilation, LF/gradient/HF, errors, shutdown and reaping are metered. Actual CUDA kernel/event diagnostics and whole GPU reservation time have distinct labels.

Runtime caches/temp files are isolated under _tmp/e6-runtime/<run-id>/ and included in the same1GiB disk guard. The guard conservatively counts the entire E6 pilot case, including earlier preparation files, plus only this owned cache; it never scans unrelated caches. Disk/RSS/CPU/wall monitoring uses the accepted polling observer, so a brief poll overrun is retained rather than hidden. At least4GiB free storage is required.

The live entry requires:
1. An exact root authorization bound to the frozen plan SHA and resources.
2. The existing exact original40 E4 terminal/reaped certificate, all corresponding native terminal/owner/wait4 hashes and empty owned groups.
3. The unique registered GPU UUID genuinely idle at entry, checked again immediately before original-model loading.

No waiter or automatic retry exists. Denied admission gets a durable rejection/CPU-cost record; an admitted attempt takes a permanent exclusive claim. All scientific failed/invalid outputs and costs remain. Bad actor JSON can consume its registered repair turn; provider/compiler/native/binding/storage infrastructure failures terminate the remaining pilot. Session and whole-run failure records retain partial outputs.

## Frozen artifacts and verification

Plan:
case/agent/controlled2d-local-qwen-e6-pilot-20261012/local_pilot_plan_v1__2026-10-12_160321_SGT__E6-local-pilot-source-prepare-b097edfc.json

SHA: 811ef88bacf5b63736a27c41c8749702720d287b73eba963d632e3c9888c57cf

Root authorization template, currently execute=false and all execution permissions false:
case/agent/controlled2d-local-qwen-e6-pilot-20261012/ROOT_AUTHORIZATION_TEMPLATE__2026-10-12_160321_SGT__E6-local-pilot-source-prepare-b097edfc.json

CPU source/transport evidence:
case/agent/controlled2d-local-qwen-e6-pilot-20261012/evidence/LIVE_PATH_CPU_FIXTURES__2026-10-12_160324_SGT__E6-live-path-CPU-contract-fixtures-2da4d090.json

SHA: 84c14e6e3e6936518c89c986d392b8bf5b29757fbfb82c2bf7db4b821c5b1d53

Nine checks passed, covering JSON null/number/list/overflow, exact raw candidate bytes, actor-design classification, compiler infrastructure stop, native transport binding/masking and sampling/message options. The fixture explicitly verifies that torch and transformers were not imported.

Final accepted-observer fake three-arm end-to-end run:
case/agent/controlled2d-local-qwen-e6-pilot-20261012/logger/2026-10-12_160347_SGT__fake-owned-E6-local-pilot-5f796d43/

All three fake arms completed, all own processes were reaped, and complete CPU/wall receipts remain. Its evaluator values are synthetic, not scientific physics. Metadata-only preflight also passes. Earlier source iterations, fixtures, raw outcomes and costs are retained.

## Single future execution command

Root must first review the bound source/plan and write the actual authorization to the fixed path below, filling the exact E4 terminal certificate and SHA and setting the explicit execution permissions. The supplied template remains false. This command then reads the exact approved file digest; it does not create an approval or wait for a GPU:

~~~sh
cd /home/litianyu/mycode/dcdtuni
taskset -c 18,19 env \
  CUDA_VISIBLE_DEVICES=GPU-330cc3e0-7cb1-f8f1-48f7-bc2e58da7cbc \
  PYTHONPATH=/home/litianyu/mycode/dcdtuni/src PYTHONDONTWRITEBYTECODE=1 \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  DCDTUNI_NODE=/home/litianyu/.local/bin/node \
  /home/litianyu/miniconda3/envs/ddl-llm/bin/python -u -B \
  -m agent.harness.e6_local_pilot_v1 run --execute \
  --plan /home/litianyu/mycode/dcdtuni/case/agent/controlled2d-local-qwen-e6-pilot-20261012/local_pilot_plan_v1__2026-10-12_160321_SGT__E6-local-pilot-source-prepare-b097edfc.json \
  --plan-sha256 811ef88bacf5b63736a27c41c8749702720d287b73eba963d632e3c9888c57cf \
  --root-approval /home/litianyu/mycode/dcdtuni/case/agent/controlled2d-local-qwen-e6-pilot-20261012/ROOT_EXECUTION_APPROVAL_2026-10-12.json \
  --root-approval-sha256 "$(sha256sum /home/litianyu/mycode/dcdtuni/case/agent/controlled2d-local-qwen-e6-pilot-20261012/ROOT_EXECUTION_APPROVAL_2026-10-12.json | cut -d ' ' -f 1)"
~~~

For safe metadata inspection use preflight with the same plan/path hash, CUDA_VISIBLE_DEVICES empty, and omit execute/approval arguments. No actual model validation is yet accepted. The runtime can now be reviewed and authorized without another provider/launcher implementation step.

Remaining scientific gaps: actual pilot validation; full fair-cost E6 cohorts including27B; real frontier provider/explicit external-call authorization; Shinaide E6-owned compile/oracle wrapping, point derivatives and40901 accepted flagship HF. Root owns Git, WORK_LOG, manuscripts and scientific release decisions.
