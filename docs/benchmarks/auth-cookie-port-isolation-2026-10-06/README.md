# HTTP port session isolation

Separate backend instances on one hostname previously overwrote the same browser cookie. The cookie namespace now uses the actual bound HTTP port; Host headers cannot select it. A present invalid scoped cookie cannot fall back to legacy. Legacy migration, expiration and revocation require validation against the local session store; foreign legacy cookies are preserved. Security flags and terminal authentication remain unchanged.

The second freeze supersedes the first provisional logout rule: own legacy is validated before revocation and both own cookie names are expired. Actual CookieJar regressions show distinct and identical scoped/legacy tokens failing before this correction and passing after it. The two targeted files finish with 38 passing cases. ROOT verified all 25 payloads against their manifests; independent source review closed the logout resurrection finding.

These are source and isolated test receipts. Live UI verification and the owned IT restart are recorded separately when complete. Main OV must remain running.
