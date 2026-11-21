# Domain object foundation: first stage, 2026-10-06

This is an integration source proposal and CPU-only verification record, not a browser or mesh/solver acceptance. ROOT owns review, build, live UI validation and Git integration.

The source view covers the exact 18 persisted construction buckets, room and two singleton slots. Named classes retain their actual JSON records; nested rack/server identity includes the parent path, so a server-local UID need not be global. Explicit child registry entries cover authored cold plates, chips, partitions/RDHX, ACU/CDU internal equipment, ports/valves, network nodes/edges, faces/patches, cutouts, instrumentation and library definitions. Legacy cpCuPairs and authored coldPlates remain distinct source records at distinct paths. PDU naming alone does not create a PduEntity or electrical model; only an explicit assetDefinition.type does so while preserving the BOX physical bucket.

A DomainProject is derived from plain JSON. No class instances, prototypes, methods or function text are attached to the store document. Routine allModels enumeration traverses only the existing top-level model buckets, preserves order and record identity, and does not deep-copy geometry or textures. Full child inventory is opt-in. Returned data references are read-only by contract, not deep-frozen: committed changes must still pass through projectStore.edit, preserving undo and notification semantics.

This first stage is not a completed OOP/SimReady architecture. Actual wiring is project load validation -> domain decode, normal allModels enumeration -> domain topLevel, project save -> safe JSON codec, and air model UID collection -> domain export admission. Unsupported geometric records remain loadable/saveable and are refused at physical export, including unknown array/singleton/nested construction containers. Legitimate flow-network topology and singleton scope do not become STL models.

The codec preserves unknown JSON root and object fields, including underscore-named extensions and own __proto__ properties, while excluding only the existing exact __runtimeOnly bag. Shared v2/v3 migration uses a safe own-assignment helper for JSON merges/root extensions and the source-proven UID, library/fluid and physics dictionaries. Unknown legacy kinds retain their declaration and are refused at physical export; matching physics rows require an own equipmentId key. A finite safe-integer schema version guard preserves failed-load return values instead of bitwise wraparound or an uncaught domain decode error.

The graph/physics methods are data descriptions in this stage. Existing numerical geometry, generated equipment-port frames, Three carriers, OV/Kit USD authoring and solver compilation remain in their existing adapters. This record does not assert a finished neutral geometry kernel, SimReady/DSX compliance, new physical equipment semantics, or whole-mesh success.

Verification:
- baseline-codec.log: real save dropped an unknown root extension (1 failed, 1 passed).
- legacy-prototype-before.log: real shared v3 root/BOX own __proto__ migration regression failed before the narrow shared fix.
- targeted-first.log: one test fixture omitted the normal allModels global; preserved without changing production behavior to accommodate it.
- targeted-nested-contract-first.log: strict known-kind refusal remained effective but its error was changed by gate ordering; the production gate order was restored, rather than weakening the expected refusal.
- uid-kind-before.log: five literal __proto__ UID dictionary records and three unknown kinds failed before their narrow shared fix (8 failed).
- additional-dictionaries-before.log: own coldPlateSpec/fluid definitions and inherited phantom server physics failed before the last three approved dictionary fixes (3 failed).
- targeted-final.log: 7 files, 108 tests passed; typecheck-final.log: tsc --noEmit exit 0.
- Actual LiquidSingle and Shinaide liquid graph nodes, edges and object bindings are equal across normal save/load with extensions. The actual Base_leakage SceneBuilder air producer's inputs, boundaryBindings and geometryExport are unchanged. Unknown root extensions do affect the established canonical project hash but are not copied into strict liquidrun/cfdrun schemas.
- No production build, browser, API, business/native solve, service change or Git mutation was performed for this proposal.

source-identity.json lists every proposed source/test path and its HEAD and current SHA. after/ contains exact proposed bytes; before/ retains the original existing sources. fixture-identity.json binds existing real inputs read by the tests. source-diff.patch is a plain read-only comparison; ROOT should review it and all live source hashes before applying/committing.
