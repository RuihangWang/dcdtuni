# Mesh cancellation worker lifecycle

The backend now keeps an accepted cancellation nonterminal until its worker drains output, joins process-tree cleanup and leaves its own case context. A queued cancellation cannot start a later CFD process. Only worker finalization emits done=true, returncode=-15, cancelled=true and checkMeshOk=false; the request handler emits aborting while cleanup is pending. Natural completion remains already_done and is not retrospectively cancelled.

The first seven regressions failed 6/7 against the original source. The final 16 controlled cancellation regressions and existing check-only/browser lifecycle, frame-budget, command-budget and project-loader tests passed together (75 tests). Tests cover queued/browsing admission, process-publication serialization, descendant stdout draining, case-lock release, late success/receipt invalidation, repeated requests, another job, HTTP failure/retry, lock ordering, separately-sessioned descendants and zombie-only groups. Temporary files stay under the integration checkout.

This is a source and controlled-worker receipt. The actual browser stop/restart cycle remains pending. No service, native job, C++ prototype, git index or GPU was changed for this verification.
