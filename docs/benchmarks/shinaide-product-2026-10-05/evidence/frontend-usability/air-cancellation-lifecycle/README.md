# Air job cancellation lifecycle

The mesh interrupt action previously ignored the HTTP response and waited only for an SSE terminal event. A healthy but silent stream could therefore leave the editor locked after the server stopped the job. Conversely, a failed status request could unlock an unconfirmed active job.

session.ts now distinguishes the stop request from backend completion. It starts one fenced status monitor after an accepted stop acknowledgement, retains the lock and a visible retryable error on unsuccessful HTTP/network/status responses, and uses one terminal transition for SSE and polling. Only confirmed completion thaws the editor. The same transition refreshes current-case readiness and solve probe results; old jobs cannot overwrite a newer run or establish readiness in another project. Cancelled mesh jobs cannot grant solve readiness, even if a terminal response has a zero returncode.

The first 12 cancellation tests failed 9 and passed 3 against the original session. The final suite adds actual MeshPanel click/DOM checks and a zero-exit cancellation guard: 15 new tests plus 27 existing related tests pass. Tests use mocked HTTP/SSE responses and the real React session/component, not a live CFD process. Existing solver component tests emit the pre-existing optional-canvas JSDOM notice. Production TypeScript/Vite build succeeds. The first build's nullable display-code typecheck failure is retained alongside the corrected build.

A review follow-up adds a localized Stopping… / 正在停止… label and disables the stop control while the request is pending or accepted. The central action also blocks duplicate accepted requests. A real MeshPanel deferred-acknowledgement regression failed once before this follow-up and now confirms pending → accepted but not done → disabled stopping display → terminal thaw. HTTP refusal still permits retry; already-done success is not relabelled as cancellation. Only one new flat locale key per language was added. The earlier successful test/build records and the new failure are preserved.

The independent CUA case changes only two existing metadata string spans. Root will verify start → interrupt during native work → thaw → fresh start → interrupt again. No live cancellation, native solver, browser, backend service, source threshold or Git-index operation was performed by this frontend patch.

The exact source hashes, commands, terminal contract and scope are in receipt.json; raw baseline, test and build logs are retained. Live CUA acceptance remains pending.
