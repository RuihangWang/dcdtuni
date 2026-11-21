# Domain object foundation: read-only inventory

Source identities are frozen in source-identity.json. No tests, business runs, services, Git changes or NVIDIA capability qualification were performed for this inventory.

Current editable frontend architecture is plain JSON Construction records plus factories (schema/cfdproj.ts:52-58, legacy/factories.ts), not a common behavior-rich BaseEntity class. Pydantic CFDBaseModel and Kit parser classes are separate validation/renderer structures.

Shared cfdproj_schema.js:12-27 owns 18 buckets and legacy aliases. raisedFloor/falseCeiling are singleton slots (:66-69), room geometry is separate. Nested rack servers, CP-CU pairs, cooling ports/valves, networks, faces/patches and instrumentation have additional identities. allModels in shared cfdproj_state_v4.js:152-162 excludes networks; React schema/cfdproj.ts:193-202 uses 17 tree categories. Registry enumeration must distinguish these views and preserve ordering.

cfdprojModules.ts:190-225 restores non-enumerable kind after structuredClone. Store prototypes must remain plain; DomainProject is a derived view. projectFiles.ts:262-306 accepts historical v2/v3/v4/v5 through shared migration, refuses >5. projectForSave :212-223 strips materialized transient fields but whitelists root SAVE_KEYS, so unknown top-level extensions are currently lost; nested Construction extensions are plain JSON. Lossless codec must deliberately preserve document extensions without serializing private runtime state or methods.

geometry_pure.js explicitly requires THREE; geometry_scene.js injects scene/cache context. sceneBuilder.ts:338-382 delegates shape dispatch while PDU/CDU details stay outside physical renderMeshes/STL registry. A facade alone does not remove Three coupling.

sceneSnapshot.ts:59-67 already defines a neutral dcdtuni.render-scene/v1 packet, but :416-539 derives it from Three objects. Kit render_scene.py:402-461 creates an in-memory USD stage and authors projectUid/modelUid/primitiveId/equipmentUid/liquidPart customData. This is derived display data, never project or solver authority.

runPayload.ts:132-155 generates fresh cfdrun+STL in RAM; run/session.ts:423 submits /api/run-mesh. Normal execution stores/re-reads these files. stlgen_runner.py:1867-1922 pins raw/source STL identities, :3415-3441 validates cfdrun2/3/4 and transitional meshGeometry. Existing memory snapshot production does not imply diskless meshing.

Air contracts include sourceProject.contentHash, geometryExport solid/sourceSurface identities, objectPath, patch/boundary binding names, exact STL hash/transform/winding. ResolvedRun (cfdrun_reader.py:89-115) supplies patch BC/porous region/source terms and solver configuration. Liquid dynamicLiquidGraph.ts:52-86 binds exact UID and rack/server hierarchy; pair UID encoding (:73), valve paths (:153-169) and liquidExperiment.ts:135-197 canonical start/timeline hashes must survive.

Smallest coherent first stage: BaseEntity + versioned registry + derived DomainProject + lossless plain-document codec, covering all buckets/singletons/room/known nested families. Wire actual migrated load/save and enumeration/export; store remains raw JSON, no methods/prototypes are serialized, unknown entities are preserved with explicit unsupported capabilities rather than silently boxed. Methods return data-only geometry/render/physics descriptions; retain existing numeric geometry/solver delegates until a later adapter extraction stage. Do not invent missing physics.

Next stage: extract numeric geometry kernels from THREE, provide common neutral geometry/material descriptions consumed by Three and USD adapters, preserving physical vs decorative carriers. ROOT's NVIDIA reviewer owns SimReady/DSX research; no compliance claim follows here.

Meaningful first-stage gates: complete registry coverage; parent-local nested UIDs; root/model/child extensions; future-version refusal and historical migration; transient canonical save/hash; JSON without methods; ordinary real store undo/redo/document revision; unchanged STL regions/face names and graph binding IDs. Existing targeted suites: schema/cfdproj, cfdproj, projectFiles, projectStore, sceneBuilder/Preview, cfdrunStlIdentity, rackExportNaming, dynamicLiquidGraph, liquidEquipmentGraph, liquidSchedules, sceneSnapshot/Pbr/Shinaide and Kit test_render_scene.py. This inventory did not rerun them.

Risks: clone/prototype loss, unknown-field loss, parent-local ambiguity, decorative geometry leaking into STL, transform/units/winding drift, shared material lifetime, transient hash drift, role omission and historical results binding. Domain classes cannot imply large-mesh acceptance; current native failures and solver qualification retain their existing scope.
