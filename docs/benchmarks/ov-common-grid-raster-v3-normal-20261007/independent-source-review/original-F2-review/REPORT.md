# Independent joint grid + diagnostic V2 review: changes required

Reviewed exact frozen candidate `ec86e1987544c4ff736e038ce3f13a9f8ce9ed9d6f17cde1db21b45f564df400` (1511 payloads / 62,523,454 bytes). Every payload byte/hash was read and matched. F1 baseline `ff81dde6fc485f91689b164c0bb60acd24f921297b2a55220237db7dc4eac76a` also fully matched. This is a bounded source/lifecycle review, not live Kit or pixel acceptance.

## P2: rejected out-of-schema ordinals poison retirement floors

In `grid_raster.py`, `_retire_invalid_current_observation` checks Python `int` and monotonicity but omits the common schema's maximum `9007199254740991`. Actual CPU USD first authoring accepted view1/output1. A same owned source/media malformed input with viewRevision9007199254740992 is correctly rejected by `render_scene.validate_view`; the error branch nevertheless advances `_last_view` to the rejected ordinal and clears permission. A normal valid view4/output3 can no longer recover. Separately, oversized outputRevision9007199254740992 poisons `_output_revision`, blocking all later valid bounded output observations. A same experiment with ordinary invalid CSS view3/output2 correctly recovers on view4/output3.

Raw observations and a literal source-bound harness accompany this report. Both oversized ordinals are outside the producer and common schema contract; they are not primitive identities proven current. Before retiring history, require exact non-bool integers in the shared safe ordinal range (including the lower bound), alongside the existing source, media, generation and monotonic checks. Preserve the malformed-measurement retirement behavior for valid bounded identities and the late-worker refusal. Do not accept/copy invalid metric data or change source/layout permissions.

## Passed scoped checks

Read the core CSS public measurement producer, shared snapshots/controller delivery, complete coordinator, exact normal-to-joint native/client diagnostics/schema deltas, actual IExt route, source/pick ownership and narrow F1-to-V2 patch. The three merge points retain both diagnostics and grid fields/imports. The decoration carrier is outside Geometry, supplies no trusted result/source grant or primitive identity, and cannot introduce scientific pick authority. The two raster schemas match exactly; bounded ACK caps and native/client scene identity checks remain present. Actual installed ViewportAPI layoutNone stays unsupported/unresolved; no client layout proof or pixel claim was added.

One independent native group ran serially with CPU30, CUDA empty, one USD/thread worker, all caches/TMP under this review directory, total about72s and a shared120s watchdog. Exact F2 executed: independent ordinal probes; seven retirement-floor cases; joint identity and pending-worker guards; actual extension update→plain read→bounded ACK; and the complete 15-group composed USD/presentation suite. All subprocesses rc0; the new probe records the two real product failures as data, so its rc0 must not be called a passing recovery test. No normal source or runtime was written, built, started, or stopped. All25 mapped normal IT targets remained byte-identical before/after.

Frontend331/types/build evidence belongs to Root's prior F1/fresh execution and the author's later F2 run; this reviewer did not rerun it. Native actual CPU USD, controlled ready-layout fixtures and ACKs cannot prove decoded-video layout, GPU pixels, roof translucency, actual Shift picking, mesh quality or solver convergence.

Recommendation: fix the two safe-range boundary failures and freeze a new candidate before normal application. No other P1/P2 was found in the reviewed scope.
