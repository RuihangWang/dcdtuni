# Mixed scoped/legacy logout correction

This supersedes only the provisional logout rule in the unchanged previous auth-cookie-port-isolation-20261006 freeze. The previous 13 payloads and their manifest were reverified byte-for-byte, without edits.

An existing browser can hold a valid scoped session S and a valid own-store legacy session L. Clearing/revoking only S leaves L eligible once the scoped cookie is absent. The distinct-token CookieJar regression covers that authentication-resurrection path; the same-token regression ensures both cookie names are removed and that validation occurs before deletion, without a duplicate revocation.

Both new cases failed on the previous green implementation (2 failures, 36 existing passes). The narrow production change affects only handle_auth_logout: it saves the validated own legacy token before any session deletion, revokes chosen and own legacy tokens using ordered deduplication, expires the own HTTP namespace and validated legacy cookie, and never revokes or clears a foreign-store legacy token. All 38 cases in the same two targeted files now pass. No assertion or old test was weakened.

Source-before/source-after, test-before/test-with-new-regression/test-final, narrow diff, baseline/final commands and raw logs are included. Test scratch/cache directories are excluded. CPU36–39, CUDA empty, all temporary paths inside this owned integration directory. No real HTTP login/logout request, browser, server restart, process control, mesh/native run, build, or Git/index operation occurred.

Existing login migration, scoped-cookie precedence and absent-only legacy fallback remain. An invalid/empty scoped cookie cannot downgrade. Cookie security flags and terminal query-token authentication are unchanged. ROOT must review and deploy with a normal owned IT restart, then verify both real UI sessions.
