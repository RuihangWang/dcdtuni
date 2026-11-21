# E6 inference baselines — source preparation, 2026-10-12

**PREPARED ONLY.** No new model inference/training, physics solve, mesh, GPU job or external API call. This package prepares E6 for C3; it does not establish an E6 comparison. The full C1–C5/E1–E8, 4B/27B and flagship story remains required.

## Reuse and package

Inventory found no pre-existing E6/inference-time implementation or case. The completion audit kept E6 OPEN. We reuse the controlled physical-task compiler, actual JavaScript cfdproj schema, frozen LF6/HF10 evaluator, public task registration, six-family Shinaide catalog/codecs/compilers, CaseArtifacts, RunLogger and the existing owned-process helpers.

Source:
- src/agent/harness/e6_inference_contract_v1.py
- src/agent/harness/e6_inference_adapters_v1.py
- src/agent/harness/e6_inference_prepare_v1.py

The only executable CLI is prepare. No live provider/physics dispatch CLI is shipped. The callback interfaces run with an included scripted provider and analytic fake evaluator. Actual provider and owned-process launch integration still require root review.

The controlled2d-inference-e6-20261012 case owns a real reference project and six new public-task compiled projects. The shinaide-inference-e6-20261012 case owns an adopted real Shinaide reference and verified references to all six architecture families; these are metadata, not six newly solved designs. No duplicate physics implementation is introduced.

## Primary mechanism mapping

Sources checked: [Phythesis §4, Algorithm 1](https://arxiv.org/html/2512.10611v1) and [SGA §2, Algorithm 1](https://arxiv.org/html/2405.09783v1). No authors' code/data was copied.

Phythesis uses design proposals, differentiable asset-parameter refinement, nearest catalog-asset selection, re-simulation, reflection and ranked design/feedback history. The task adaptation retains these stages. The present fixed synthetic equipment catalog has one admissible equipment SKU per registered slot: explicit catalog projection is identity, followed by another paid LF call. This is a restricted catalog adaptation, not reproduction of their asset search/data/simulator.

SGA uses discrete outer proposals, continuous inner optimization and ranked history with low/high-temperature samples. Our adaptation searches the registered design grammar and includes a paid legal heuristic bootstrap. It does not synthesize arbitrary physics equations or reproduce their original tasks.

Proposed pre-outcome recipes:
- **Original Qwen direct:** one original checkpoint sample, one compile, zero repairs/search feedback, then commit the sole valid output before one final HF.
- **Iterative evaluator Agent:** propose/compile, request paid LF, inspect its own I0/I1/failure feedback, then submit. Repairs consume additional model turns. No hidden host optimizer.
- **Structure + LF gradient + HF:** one proposal, fixed discrete fields, four projected LF reward-ascent steps in unit coordinates with step size 0.1; retain every project; select within the paid LF lineage before HF.
- **SGA-style adaptation:** two rounds, temperatures [0.5,1.0,1.0,1.0] per round, top-K=5, four inner steps and paid heuristic bootstrap. Only inner-selected designs enter the outer heap.
- **Phythesis-style adaptation:** two rounds, five samples at 0.7 per round, top-K=5, four inner steps, catalog projection, paid re-evaluation and reflection. Only projected designs enter the heap.

Common search ceiling: 32 model calls, 64 proposals, 64 LF, 64 gradient requests, zero search-HF and one reserved final-HF. Direct/fixed-loop arms intentionally use fewer calls. These are concrete proposed settings awaiting root review; **call ceilings are not equal cost**. Whole-run limits and matched-cost checkpoints must be fixed before actual outcomes.

The original 4B and 27B models require exact checkpoint/tokenizer/template identities. Preserve the strong frontier-model arm with the same grammar, tools and evaluation budget. Its actual provider is unbound; credentials were not inspected, and external calls are not authorized. No model revision or price is invented.

## Tool schema and actor states

An actor returns one strict JSON action:
- propose + design: schema/semantic validation and deterministic compile; raw output retained even on failure.
- evaluate + candidate_id + LF/HF: evaluate an own compiled candidate; HF requires a nonzero search-HF allowance.
- submit + candidate_id: commit an existing valid candidate.

No actor file/shell/network/code/path tool exists. Duplicate keys, nonfinite JSON, extra fields and foreign/invalid candidates are rejected. propose combines validation and compilation. A live preview/rendering tool is not implemented here. Asynchronous worker dispatch belongs to the future owned launcher; the callback returns its typed completed reply.

States are SEARCH → COMMITTED → TERMINAL. Submission closes search immediately and retains the selected project under case/agent/<case>/selected/<run>/final_candidate.cfdproj. The selection record hashes the candidate and pre-final feedback. Final HF can evaluate only that candidate and never enters actor context. Reselection after final HF is blocked.

Model-visible feedback contains only status, reward, registered components, constraint status and any explicitly requested named LF derivative. Complete native fields/coordinates/iterations remain in raw receipts. Top-K and reflection explicitly include each design. Reflection is advisory. Ranking uses one fidelity, feasibility, reward and stable candidate ID; LF and HF are never silently pooled.

The refiner requires derivatives of LF reward in the exact named physical coordinates, scales them into unit coordinates, clips to registered bounds and recompiles each step. Missing I3 stays unavailable; no geometry derivative is zero-filled. No line search, HF-dependent acceptance, optimized teacher or hidden best-of-N selection is used.

## Budget, failures, retention and ownership

The host owns model/proposal/LF/search-HF/final-HF/gradient reservations before dispatch. A gradient atomically consumes LF plus gradient slots. Final HF has a separate reserved slot. Native token/cached-token/API currency receipts are retained; unknown quantities remain null.

Scientific solve failures retain their finite registered failure reward and original artifacts. Invalid model syntax/actions may get an explicit paid repair turn within the same model-call cap. Transport/identity/binding/infrastructure/resource failures stop the run; no automatic retry. Recovery needs a new identity tied to the original failed paid attempt.

Every candidate retains raw text, structured action and actual project. Invalid output has raw text plus rejection metadata and no fabricated project. Each evaluation retains request, project/task/fidelity/purpose binding, raw response and costs. The controlled adapter verifies returned native task/project/fidelity before feedback. No prior experiment's paid truth is imported into search. Any later allowed cache must bind task/design/project/fidelity/information/evaluator and retain its paid origin.

Actual execution must use the existing e5_native_observer_v1 / structured_process_groups_v2 ownership machinery, a unique dated case logger and the authorized single-resource allocation. Charge startup/model loading, inference/token work, compile, LF/gradient/HF, failure/timeout, synchronization, storage, shutdown and reaping. Nested diagnostics are alternative views of inclusive bills, never additive. Remote-provider GPU time is unknown; preserve token bill and latency. A CPU observer alone cannot establish full GPU costs.

Per-run resource/wall/RSS/output/API-spend caps and live launch lifecycle remain concrete release inputs. The preparation cost observation is explicitly taken before receipt copies/logger finalization and **is not a terminal formal-run bill**. No E6 cost/amortization claim follows from fake timings.

## Public tasks and flagship boundary

The adapter reads only the frozen public-only native E5 protocol, SHA ede8aab1de6b2d37993e737fb3bde2d8b87efe4057e5edd0b1aa00d47c14b29b. It preserves four train/two validation tasks and original seeds 1301–1310. It never calls registered_tasks() or a test constructor. Register all method/task/seed identities before dispatch; retain every seed and use the original IQM/stratified-bootstrap requirement. Public validation is not E7 unseen-task evidence.

Initial source inventory read the full controlled2d_tasks.py, including registered_tasks at lines 139–153; test constants were incidentally visible. They were not transcribed, prompted or executed; no sealed files/outcomes were read. Root was informed. Test definitions are unchanged and no new test protocol is introduced.

Flagship preserves the original minimum of five seeds. 1301–1305 are proposed before E6 outcomes, not claimed as an existing flagship seed registration. The original six-family catalog SHA is 437f6ff334fb72fed559164bc92152ed04dda8355cc80a7aad681f23f5c16321. Original TaskRefs, server/fan/liquid identities, fault contexts and synthetic provenance remain intact. Full design scope is retained alongside the catalog's explicit current compiler domain.

Point-specific conditional LF derivatives and actual source-bound flagship HF are missing release dependencies. Pro performs no mesh/CFD repair campaign; 40901 owns it. Shinaide currently has source-interface inventory, not a live E6 wrapper. Before launch, bind existing compile/oracle functions to **E6-owned** projects/loggers: original TaskRefs encode original case ownership, so blindly dispatching them is not acceptable. These missing experiments do not shrink C4.

## Verified preparation and next inputs

Current protocol:
case/agent/controlled2d-inference-e6-20261012/protocol_v1__2026-10-12_153415_SGT__E6-source-preparation-ee2ca245.json

SHA: a5e82fb9eef5989bb68d68e25d09f4c507066a97bb2e71ea4c649f518646599f

Current evidence:
case/agent/controlled2d-inference-e6-20261012/evidence/PREPARED_E6_v1__2026-10-12_153415_SGT__E6-source-preparation-ee2ca245.json

Eleven synthetic fixtures passed: direct/final isolation; paid invalid retention; refinement; absent derivative; atomic budget; stale binding; finite failed solve; SGA outer/inner/top-K; Phythesis projection/reflection; infrastructure stop; native-state masking. Six public tasks passed actual cfdproj schema compilation. Source snapshots are under the preparation logger. Earlier preparation identities/costs remain; review repaired feedback/context/catalog/exception wiring before any real inference or physics.

Exact permitted preparation command from the remote repository:

~~~sh
taskset -c 18,19 env CUDA_VISIBLE_DEVICES= \
  PYTHONPATH=/home/litianyu/mycode/dcdtuni/src PYTHONDONTWRITEBYTECODE=1 \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  DCDTUNI_NODE=/home/litianyu/.local/bin/node \
  python3 -B -m agent.harness.e6_inference_prepare_v1 prepare
~~~

Root review inputs: exact protocol/source hashes; model/tokenizer/template identities; owned provider transport with raw usage/limits; complete seed/task/arm order; complete cost/resource/storage limits and fixed matched-cost checkpoints; controlled worker native-binding/failure checks. Flagship additionally needs E6-owned compile/evaluator wrapping, point-level LF derivatives and accepted actual HF. Frontier additionally needs a named real provider and external-call authorization.

Root alone updates Git, WORK_LOG and manuscript. Scientific E6 remains pending.
