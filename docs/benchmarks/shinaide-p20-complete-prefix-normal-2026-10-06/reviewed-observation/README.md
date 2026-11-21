# P19 complete-prefix replay observation — PRIVATE source packet

P19 remains stock Failed2 (D831/W82); this packet supplies no mesh or quality acceptance. Production AIR source, library, services, physical inputs and Git were not changed. The 33 production controls remain byte exact at AIR HEAD 5bf7724f18db87a2cbb6fc5811abf20a931f031b.

The private change touches only existing dcCartCutCells.C and dcCartCompleteCavityAuthority.H, and adds dcCartCompleteCavityReplayObservation.H plus dcCartCompleteCavityReplaySourceSidecar.H. Default is OFF; exact DCCART_COMPLETE_REPLAY_OBSERVATION=1 is required. Existing DCCART_LEGACY_JOURNAL_OBSERVATION=1 produces the dependency binding. Existing current predicates, all journal/Lineage layouts, W/D decisions, repair/finite kernel/thresholds, quality checks and MPI expressions are unchanged.

## Actual producer and data positions

Authority read retains a shared reference to the SAME fresh immutable post-carve Snapshot only after the original read succeeds. Failure clears it. Source identity/rank/facets/shape and all six source/material/producer/boundary fingerprints are validated without invoking an authority callback. No source or geometry permission is added.

At the existing preoperator NO_WRITE oracle, only ranks whose normal catalogue already contains Views may retain the ALREADY constructed, already cached polyMesh by move. Its real bindings transfer by move, not copy. There is no additional toPolyMesh, geometry calculation, graph, quality evaluation or collective. The oracle object and its full cache memory live longer until raw capture; this is a real full-object lifetime cost, not a sparse-cache claim.

After ALL original local current/repair/union decisions, raw capture selects the first complete bounded recorded census dependency by generic ordinal/View binding. It captures every committed journal entry (a bounded superset of every intersection), full current closure, aliases, normal source parents/facets/points and live origin sets. It extracts selected earliest cached Sf/FC/CC/V plus complete actual writer accumulation order and roles, using real earliest bindings. It then releases the retained oracle. No baseline is refreshed.

A second late tap occurs at toPolyMesh return AFTER original finite point repair and ALL original W/complete/coupled final guards. It reads the ACTUAL final compact mapping, ordered rings/world-coordinate bits, source-selected roles, patch/zone membership and cached Sf/FC/CC/V. It does not infer finite-repair results from m.points, which may still be pre-repair. It does not create any demand-driven geometry/cache or graph. Absent cache, missing/duplicate mapping or incomplete shape is explicit refusal.

Only BOTH actual files with captureComplete true and identical rank/epoch/dependency ordinal/source fingerprints are complete installed replay evidence. A raw-only controlled serializer result is not complete installed replay. Removed final cells/faces are marked removed; earliest missing required cell/face refuses. Unknown alias, source or endpoint is not fabricated.

## Bounds and cost

Each serialized output is <=64 MiB; cells<=4096, faces<=32768, alias classes<=8192, members<=32768, origins<=4096. Real earliest bindings are retained only when their four arrays total <=64 MiB. Source facet/shape and normal old caps remain enforced. Raw+earliest and final serialization share a cumulative 10-second owned steady deadline, periodically sampled during the actual full-face scan and stored-zone loops; no decision callback or MPI is invoked. Blocking filesystem writes cannot be interrupted, so this is not a wall-time guarantee. Every incomplete/cap/refusal output is labelled captureComplete:false and partialPayloadDiscarded:true; files are exclusive-create and never overwritten.

Additional peak lifetime: one existing full installed oracle and its full cached fields per rank with catalogue Views, plus its already-existing bindings; no new full mesh copy/construct. Bindings are O(1) moved, source is an existing shared_ptr reference. Raw and final writers hold bounded owned strings and request IDs. Full native RSS/wall impact requires ROOT P20 measurement; controlled ~0.3 s harness execution does not establish native impact or the 300-second deadline. Original deadline is unchanged.

## Controlled RED/GREEN and source scope

The actual OpenFOAM 2412 scalar64/label32 IFstream/types/NO_WRITE object and normal post-carve/Authority, index, View and legacy journal producer were used in a controlled twelve-cell fixture. Original Authority factory cannot expose its existing immutable source, so the baseline consumer refuses; that red and its original logs/source are preserved. Final focused harness has 47/47 PASS in both modes. Original callback trace, first refusal site903/ordinal8, Store/sidecar bytes, journal, raw writer input and normal Lineage origin sets are identical off/on; baseline normal behavior also matches. This proves controlled observation invariance, not a P20 capture or a repaired mesh.

Added coverage includes absent actual caches (no cache creation), complete cell facet omission, real compact mapping range/duplicates, unknown point alias, missing earliest lease, missing final pair, shared observation deadline, partial/ambiguous journal, missing/stale source and physical-role tamper, NaN, clock/byte limits and exclusive file IO. The fixture has identity mappings and unchanged early/final coordinates; it does not prove real postwriter mapping or finite-repair movement. Actual controlled cache fields match the independently captured View DP values. Full private normal dcCartCutCells.C syntax uses the real installed headers/compiler and passed, with original warnings retained. No normal mesher library/APP or business case was built/run.

The original first-green, intermediate installed-green, setup preparation error, baseline red, actual compiler logs and all raw stdout/stderr are retained. initial-source-bindings.json is historical, not the final identity. final-source-bindings.json, final patches and .final.gz files are authoritative for this private candidate.

## ROOT normal build / P20 plan (not executed)

See normal-build-and-P20-capture-plan.json. Apply exactly the two changed existing files and two new files after review, keep other controls, pair normal APP/library rebuild and record RPATH/ELFs. Authority uses an external library-owned TLS producer reference, so rebuild both APP and lib rather than relying on inline cross-DSO coalescing. ROOT alone handles normal Allwmake and React P20 under the original flags/deadline plus the exact OBS flag. No private callback/quality decision changes should be imported.

Normal outputs: complete-prefix-observation-rankN.json and complete-prefix-final-observation-rankN.json in Mesh. CaptureComplete:false and no packet are honest zero-coverage observations. A complete pair is to be used immediately for source-bound complete joint-prefix hard-quality replay; it never grants removal of dependency/passing/coupled/source protections.
