# Normal OV5 test failure diagnosis and PRIVATE test correction

Only two test files are proposed for normal I; production OV5, startup scripts, thresholds, grants, renderer, scientific inputs and all live services remain untouched. The frozen map/patch is complete. No Git action, browser access, actual Kit, GPU, CFD/native build or mesh run was performed. Fake child processes/listeners in startup tests belong to their private test supervisor and ephemeral ports; `fuser` is used by the existing test only after its actual owner PID equals that private supervisor.

## Evidence and causes

Root's original five-suite ordinary run is preserved byte for byte: 33 failed / 36 passed / 12 subtests, rc1. All 33 failure sections and names are indexed. Root before backups provide the exact three existing production files; the two new OV5 paths are absent in the pre-change cohort. Every other cohort file is an audited current normal file outside that five-target delta. This is a precise five-target counterfactual, not an invented historical checkout.

1. All 23 startup failures are an invalid runner location. ROOT is derived from the checkout containing each actual supervisor/test file; Root supplied basetemp in B's sibling folder rather than inside I. The protected launch path rejects it either at the test assertion or the supervisor parser. Subsequent missing child/failure receipts follow that refusal. No guard is removed. Same unchanged startup sources, with basetemp under their respective private checkout, pass in BOTH pre-OV5 and current cohorts. In normal I the corrected basetemp/cache/TMP must be I/.codex-tmp/<exclusive owner>/..., not a B sibling.
2. Six frame-order cases have a fake `pxr` containing only UsdGeom while real `_clear_display` now executes `_set_geometry_hidden`, which imports Usd and uses RootLayer edit-target restoration even for an empty mask. Three further cases construct the extension without its already-installed VerifiedResultController. Both exact pre-OV5 and current cohorts reproduce the same nine original assertions/exception reasons. Production lifecycle initialization already exists in Root's before file.
3. The routing AST test forbids every context write except one startup None assignment. Actual before and current source BOTH have four assignments: initialization, the verified canonical grant branch, clear, and scene invalidation. The single nonempty assignment follows real HMAC verification and controller accept. This is proved by source-level before/current bindings and actual route tests, rather than changing the expected count to four.

## Proposed private changes

The shared `test_dataset_scene_race.py` harness now initializes the actual pure VerifiedResultController, default-OFF OwnedOutputLayout when present, current grid lifecycle fields and actual extracted grid/context methods. It executes real render-scene view validation and real hidden-grid coordinator (no raster/capture), plus real result-root and empty-mask retirement. Its controlled Stage implements RootLayer/edit-target and source UID metadata; fake pxr is deliberately limited to the declared empty-root scenario. Unexpected nonempty USD composition is not faked as successful. Genuine CPU USD composition has its separate existing tests. Three legacy fake views now satisfy the current shared view schema (`timeSeconds`, camera omitted rather than invalid null). Existing nine frame-order and all dataset assertions remain unchanged except those controlled input literals.

The outdated AST context-count assertion is replaced by actual extension router + actual HMAC verifier, canonical artifact read, real controller acceptance/final commit tests. Controlled test identities and test signing key are explicitly synthetic and cannot authorize any real result. The USD writer is a controlled carrier; these checks do not validate its full numeric-layer schema or a scientific mesh/run. They cover legitimate NOT_CONVERGED diagnostic context, unsigned create/replace refusal, signature/source/revision/expiry/kind/changed-byte rejection, and late old writers after clear, scene replacement or a newer signed frame. Production signature/lineage gates are unchanged.

## Actual runs (CPU36, one worker)

- Before production / old tests / correct basetemp: 10 failed, 49 passed, 12 subtests, rc1; 13.40s pytest.
- Current OV5 / old tests / correct basetemp: 10 failed, 59 passed, 12 subtests, rc1; 13.46s. The new ten owned-output tests pass.
- First private correction: 7 failed, 82 passed, 12 subtests. A helper extraction omitted bound `_sample_grid_raster`.
- Next coupled replay: 7 failed, 29 passed. The real validator exposed old fake views missing `timeSeconds`.
- Final focused coupled replay: 36 passed, rc0; 2.61s.
- Final complete six-suite run: 89 passed, 12 subtests, rc0; 14.02s pytest / 14.9495s outer.

The two intermediate candidate failures are preserved and classified as PRIVATE harness omissions, not newly discovered normal product RED. Read/packing tooling mistakes are separately documented; no scientific/native/normal test result is attributed to them. `SOURCE-final-guard.json` verifies all 42 captured normal files plus 3 immutable Root before backups unchanged. This is not a process-memory or whole-repository assertion.

## Review/reproduction and normal runner

`REPRO.py --owner <fresh directory under B>` verifies the entire frozen manifest and recreates all three source cohorts by value. It then runs the actual baseline/current/final comparisons, with every temp/cache file inside each private checkout. `--candidate-only` executes the final six suites only. It uses CPU36 and genuine installed Python/pytest; no node/vendor or normal source is imported. Original logs, XML, argv/environment and rc are retained.

After Root reviews and guarded-applies the TWO test targets, normal pytest can use exactly the existing five suite paths plus `test_dataset_scene_race.py`, with `--basetemp I/.codex-tmp/<normal-owner>/pytest-temp`, `cache_dir` under that owner, TMPDIR and PYTHONPYCACHEPREFIX there too. No service restart is required for these test-only corrections. Any further native display acceptance remains distinct: layout proof is unresolved and pixels unverified; this packet does not claim OV visual parity.
