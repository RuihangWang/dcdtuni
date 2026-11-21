# Independent finite repair budget/wiring review

This receipt covers the frozen C/H/APP wiring and standalone budget policy only. The five source snapshots were rechecked byte-for-byte after review. No native mesh, solver, API, browser, GPU, service or Git mutation was run.

The standalone g++/pytest budget probe passed 21 tests (pytest 2.69s, invocation wall 5.898594s). Exact `1` enables the flag. Invalid finite/negative inputs yield zero; an unlimited native limit yields20s; a finite limit yields `min(20,max(0,limit-elapsed-60))`.

The APP callback reads the current native timer on every invocation. Installed clock source confirms ordinary `timeIncrement()` calls do not reset overall elapsed time. The runner supplies the remaining whole-mesh deadline before native launch, rather than a fresh full budget. Integer command rounding and native launch/setup mean the outer runner deadline remains authoritative. This timer does not by itself cover frontend export time. Sixty seconds is a policy reserve, not proof downstream writer/checkMesh will finish within that time.

Within the normal cutCells APP path, every rank runs the flag min/max consensus before deciding whether to pass the callback. Opt-in ranks then run the same missing-baseline reduction at writer entry; a missing earliest writer baseline fails before mesh writing. Default OFF skips repair, although the APP now performs two additional consensus reductions. The non-cutCells builder path does not support this option. Existing earlier experimental/literal environment options must already agree across ranks; this new late consensus cannot repair earlier divergence. Actual MPI execution was not tested.

The callback output is not the remaining total20-second repair stage. The still-WIP repair header must start its own clock before setup, count caches/census/selection/evaluation/refresh, and check all expensive operations. A post-cap rollback cannot retroactively make an overlong operation satisfy a hard wall cap. Rank-local timeout paths must preserve the same collective tail. The header was not ready/frozen and is deliberately outside this acceptance.

`source-identities.json` binds the initial snapshots; `source-post-review-binding.json` proves they remained exact; `installed-clock-identities.json` and `runner-budget-excerpts.json` bind timer semantics. `budget-tests-invocation.json` gives the exact allowed standalone test command and isolated environment.
