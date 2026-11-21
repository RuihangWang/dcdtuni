# Liquid DAE, Agent design and OV integration — 2026-09-28

This record covers the integration branch through `067636d3` before it enters
`main`. The branch starts from engineering `main@80b492a7`, incorporates the
Pro 6000 `ai-design-eval@fe187de5` history, and adds a new, versioned project
design task. The dcCartMesh source tree is `b3390bb6`, byte-identical to the
tree selected by the author on 40902 `main`; no dcCart side branch was merged.

## What the new research path actually executes

The D1 engineering gate applies an Agent pipe-diameter action to a **copy** of
a saved `.cfdproj`, invokes the React `compileSavedExperiment` compiler to
produce strict v3 `.liquidrun`, loads and admits that exact input, compiles its
liquid equation graph, and solves the PyTorch Newton DAE. A separately labelled
same-graph continuous relaxation carries implicit gradients back to a trainable
decision head. The hard project score and relaxed training loss have different
identities. D1 currently scores only the **steady initialization at t=0** of a
synthetic transient fixture. It is not a full-horizon or site-calibrated score.

The Pro 6000 Qwen3.5-4B + LoRA single-step connection check passed on that
project-backed path. The preserved, byte-verified project, compiled input,
oracle record and Agent record are in
`ddl/case/benchD_v3/tracked/d1_qwen_20260928T064502Z_0a1a63d6/` (hardened
implementation identity); an earlier pre-hardening smoke is retained beside
it. These prove a gradient and one parameter update on one mock task, **not**
that 4B or 27B designs improve, transfer, or need fewer harness iterations.
Old A/B/C and `paper2`/`paper3_icml` results are historical and abandoned as
evidence for the new journal paper.

## Checks completed

- Mac: React 2969 tests, TypeScript check and production build passed after
  adding the strict project compiler. Bench D D0/D1 tests and related DAE
  runtime/schema checks passed. The compiler rejects an unsaved DAE project,
  an invalid HX configuration and mismatched deterministic output.
- 40901 isolated checkout: 128 targeted backend, DAE, USD and protocol tests
  passed on the integration branch before the final browser-load fix; another
  35 browser-load/case-lock tests passed on commit `067636d3`.
- 40901 real browser, copied small case and independent ports: both React and
  legacy editors exported four models, six nonempty STLs and a V2 `.cfdrun`
  whose returned SHA-256 matched the saved file. Corresponding STLs matched
  byte-for-byte. React loading another same-UID project cleared the old
  selection and rebuilt the new scene; legacy rejected a malformed V5 project
  with `success: false`.
- Pro 6000: the saved-project D1 hard candidate completed under the `ddl-llm`
  environment. The Qwen4B LoRA single step produced finite nonzero gradients
  and changed LoRA parameters. The selected GPU returned to idle after exit.

The production browser's shared page still requires **single-request use**:
parallel `/api/browser/*` mutation requests can interleave. D1 research uses
an independent Node compiler subprocess per candidate and does not depend on
that shared browser page.

## OV acceptance boundary

The OV result-layer producer, transport, USD author and pick bridge are wired
with CPU tests. The backend source endpoints remain 422 fail-closed until a
complete mesh/run/source lineage and field-dimension receipt exists. Kit also
rejects direct result-layer commands without an independently trusted context;
browser-supplied `expectedSource` cannot mark a result as verified.

Real Kit video and pick **did not pass**. A first isolated GPU5 episode reached
`RTX ready` only after about 442 s. A second episode reused its private cache
and reached ready after about 60 s, but two browser attempts still timed out
without decoded video, scene/view ACK or pick. The sign-in WebSocket connected,
so the remaining failure is in media/SDK/session or a later bridge stage; this
record makes no causal claim beyond the observed traces. Both episodes stopped
within their limits, released GPU5 and left the user 8360 service untouched.

Raw browser response JSON and warm Kit logs were transferred from the 40901
isolated checkout to OneDrive
`Research/DCTwin/_archive/20260928/40901/integration-qa/`. Seven transferred
files were independently SHA-256 compared against their 40901 originals;
all matched. No session cookie was copied. The 40902 uncommitted Shinaide
project was separately backed up on 40902 and OneDrive before main sync.
