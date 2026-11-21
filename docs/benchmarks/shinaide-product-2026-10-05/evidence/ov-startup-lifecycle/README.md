# OV startup lifecycle repair — CPU-only verification

The managed supervisor now owns a private control listener and parent-death protection before NVIDIA/Vulkan/display preflight. Each GPU query is bounded at 20 seconds within a 300-second total startup budget. The default GPU0 selectors, physical UUID mask, one NVIDIA ICD and UUID-derived Vulkan index remain intact.

The supervised shell uses the resolved selection and execs Kit. An inherited EXEC override is removed. A startup-ready receipt requires the expected port's listening socket inode to belong to that exact Kit PID; a foreign listener fails closed. The known final GPU-foundation factory failure and the signaling deadline create an explicit failure receipt and invoke normal owned cleanup. Readiness does not depend on a browser, decoded video or scene ACK.

Normal launcher shutdown sends TERM through its existing exact private fuser control port. The supervisor signals only its saved child session and current UID, with pidfd identity protection. It also handles separate child process groups in that session, escalates after a grace interval and reaps adopted descendants. No process-name cleanup or invented modal-suppression flag is used.

The original seven behavior tests failed before repair. The final three focused suites passed 32/32 in 9.45 seconds on CPU36–39. The preserved intermediate logs distinguish genuine pre-repair behavior from fixture corrections. Bash syntax checks and git diff --check passed. Fake process receipts confirm stopped test children. The older failed Kit log retained its original byte SHA.

The seven exact source/test paths are in receipt.json and source-sha256.json. source.patch includes the new test without staging it. No index mutation or commit occurred in this task. The source is ready for primary review; real GPU0 confinement, decoded video and scene ACK require the separately gated actual launch.

Run the command in receipt.json from integration, choosing a fresh repo-contained pytest basetemp for a later repeat.

## Follow-up primary review

The original final-regression.log remains byte-identical (32 PASS). The latest final-39-regression.log reports 39 PASS in 10.71 seconds. Separate before-failure logs cover cleanup exceptions, display deadline expiry and explicit display cancellation. The original 32-test source/receipt/patch snapshots are preserved with -32 names; current source hashes are in source-sha256.json.

Cleanup attempts all owned sessions and collects each failure. A cleanup failure publishes cleanup.failure and a nonzero kit.exit, preserving the original startup failure. Independent receipt write errors do not prevent the remaining exit/failure receipt attempts. Display cancellation and an expired total deadline propagate instead of creating another display child. Unexpected process metadata I/O remains an explicit error.

Startup budget and cleanup tail are separate. See independent-review.json for the exact ownership/race scope and the pending caller fallback grace recommendation. No physical GPU/Kit acceptance is claimed.

## Final shutdown grace alignment

The final focused suites pass 40/40 in 12.88 seconds (final-40-regression.log). The original 32-pass stdout is unchanged, and the 39-pass follow-up log remains available. Current seven source hashes and the precise patch are refreshed.

The launcher now allows up to 20 seconds for its exact private control-port supervisor to finish normal cleanup, returning immediately when it closes. This accommodates the approximately 15-second exceptional timed-wait tail (probe cleanup retry plus two owned sessions) separately from the 300-second startup deadline. The fallback still requires the same exact private port and same UID/PID; no signal scope expands. A real CPU-only two-session test confirms fake Kit/descendant and fake display sessions that ignore TERM are killed and reaped promptly through normal control shutdown.

## Final source freeze

The final source includes the primary-reviewed pre-Xvfb cancellation/deadline boundary check. Its targeted simulated crossing failed before the check and now passes. Final three-suite verification is 41/41 PASS in 12.72 seconds, recorded in final-41-regression.log. All earlier stdout logs, including the original 32-pass log, remain unchanged. The source manifest and patch describe the final seven source/test files; no index or commit was changed by this agent.
