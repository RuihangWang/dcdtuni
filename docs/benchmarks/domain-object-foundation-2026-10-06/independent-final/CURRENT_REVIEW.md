# Independent review checkpoint — domain foundation

Source-bound pure data checks passed, but this checkpoint does not clear source review: two narrow shared-migration findings remain pending. No browser/mesh/solver/USD capability acceptance is inferred from these checks.

The fixed-source pure JSON probe (`final-data-probe.json`) ran in 0.974 s with CPU affinity 28–31 and an owned temporary directory. Twelve source/test hashes were equal before and after execution and agree with the author's frozen relevant source identity. Four real saved inputs were used: LiquidSingle, the actual Shinaide mixed-keyframe save, and ROOT's normal-browser compatibility source and before-save file.

LiquidSingle enumerated 94 domain records; Shinaide and each compatibility copy enumerated 1385. The independently constructed expected child path sets (85 and 1345 respectively) were present with the correct type and parent path. Every returned record referred to the exact source object at its JSON pointer; all parent UIDs matched the referenced parent. Within each individual input, enumeration changed neither source JSON nor the existing producer content hash, and its air model UID sequence matched the existing shared exporter. This is not equality between the synthetic compatibility file and its historical lossy saved copy, nor byte-identical historical schema migration.

The 1385 Shinaide records include 56 rack servers, 56 cold plate source records, 56 cpCuPairs compatibility records, 602 rack partitions, 444 model faces, 64 surface patches, 20 geometric cutouts, 29 valves (including CDU internal declaration and pipe attachments), 16 waypoints, three instrumentation probes, and three model definitions. Parent-local UID reuse is preserved; JSON pointer paths, rather than globally invented UIDs, distinguish records.

The existing probe also checked future geometric dict/array/singleton/nested-container preservation plus physical export refusal, invalid future/fractional/large/negative schema versions, and v3 root/model own __proto__ extension preservation through the actual shared v3→v5 migration. These passed on the source hashes recorded in the result. The live store remains the editing authority: domain records are cheap derived references, not immutable snapshots, and mutations must use store.edit/undo/input-revision handling.

## Additional red migration inputs, retained as evidence

`uid-dictionary-red-observation.json` binds shared migrate SHA a7494815589cfcdbaaf50dd162dda15e2f9e736fdc3ebbdb94a3a0304e1bc994. Five valid v3 dictionary identities named __proto__ (top model, server, cold plate, probe, measurement) disappeared as own serializable members and became only the respective dictionary prototype. This changed the per-dictionary prototype, not global Object.prototype. ROOT authorized the author to fix the same-module own-key assignments only.

The same small probe found that unknown v3 kind futureSolid lost its kind and became a plain BOX, bypassing the new registry's unsupported-kind refusal. Kind toString instead read the old kind map's inherited function and threw in the migrator. ROOT authorized own kind lookup and preservation in legacyKind in the same module, pending targeted verification.

The first reviewer probe draft used equipment.uid instead of the actual v3 equipment.id and failed before producing the observation. Its draft source/stderr/terminal are preserved with the prefix draft-missing-v3-equipment-id-. That draft fixture error is excluded from product findings.

Final source SHA rebinding and rerunning only these existing inputs is required after the author freezes the narrow fixes. No broad suite or business runs were invoked by this reviewer.
