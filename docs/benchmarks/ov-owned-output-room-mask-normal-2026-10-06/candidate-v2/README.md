# PRIVATE v2: owned OV output paths and exact RootLayer enclosure mask

This is a reviewable private preview, not a production deployment or GPU/Kit acceptance. The two independent v1 blockers are repaired in private copies only. Production ten candidate paths and four unchanged dependencies remain byte-exact to the original v1 production-before capture. MAIN/IT source HEADs may advance for ROOT's unrelated docs; that does not change this source binding.

## Frozen inputs and source scope

- Original private v1: ../ov-owned-output-room-mask-private-20261006/manifest.json, SHA 4ed37a7f2bf2f8987618b18375817056d0b328524ad1643d412fd7ae5b61d906, all 89 payloads verified again, unchanged.
- Independent review: ../ov-owned-output-mask-independent-review-20261006/manifest.json, SHA 7945a575037160286fd2da23244a18c5d6304f6429229b814eb428270ce2111b, both declared payloads verified, unchanged.
- before/ contains the v1 candidate after, not the original production source. after/ is the complete ten-path v2 preview plus four unchanged dependencies.
- v2-only-two-paths.patch changes only bridge handlers.py and reference web/vite.config.ts relative to v1.
- all-ten-paths.patch compares the complete after against original v1 production before. source-v2-final.json binds all 14 current production/before/after sizes and SHAs.
- Original v1 output-path/role/supervisor/schema/render-scene/extension/scene edits remain unchanged. New frontend Panel/RendererControl/cube/CSS edits owned by ROOT are outside this packet.

## Exact parent-directory rejection

The Vite read helper now rejects relative === '..', in addition to root/ancestor descendants, absolute escape and symlink components. Four tests execute the actual transpiled helper and refuse the exact checkout parent for KIT_LOG, SCENE_TREE, DATASET_INFO and AI_LOG. This does not weaken the original bridge owned paths. Existing converter/NumPy/cache paths remain precondition validated, not claimed race-proof descriptor writers. Original CLI explicit texture_dir behavior is unchanged.

## Explicit RootLayer policy and ownership

author_scene already commits its canonical RenderScene subtree to RootLayer and restores the caller EditTarget. The temporary volume enclosure mask now writes only RootLayer and always restores caller EditTarget in finally.

Each saved entry owns an anonymous in-memory Sdf copy of the exact RootLayer visibility property spec, including default, timeSamples and metadata, or records that no Root property existed. Composed HasAuthoredValueOpinion is no longer mistaken for target-layer authorship. On clears Root default AND timeSamples before setting default invisible. The actual composed imageable visibility is checked at Default and all composed visibility sample times.

User Session and Session sublayer opinions are never changed. User hidden opinions remain hidden. A stronger visible/inherited opinion that defeats the Root mask causes a visible RuntimeError and transactional Root rollback, rather than a false successful volume acknowledgement. Root sublayer opinions are shadowed temporarily and restored by removing only the Root opinions. This is not an authority to force all layers visible or override stronger users.

Off restores the entire original Root property spec. Originally absent properties remain absent. Only mask-created, originally absent, still-empty over ancestors with no unrelated fields/properties/children are pruned. Multiple weak-only siblings restore in reverse creation order. Failed restoration attempts do not skip other entries; original snapshots plus complete shared ancestor cleanup ownership are retained for retry, the state remains masked/incomplete, and failure propagates. Caller EditTarget is restored on success and failure. Removed models are not resurrected.

Successful Root reauthor retires saved RenderScene snapshots, because that actual subtree has been replaced; fresh Root specs become the new originals. Session opinions remain intact. Failed re-mask no longer claims the retired Root mask is active. Different RootLayer stages restore the old owned stage before binding the new stage, and do not copy old visibility snapshots onto same-named new prims.

This is a cold-deployment change to private internal saved-state shape. It is not a hot-migration protocol for already-instantiated v1 scene objects. Non-mask edits to the same Root visibility property while the mask owns it are not separately merged; the captured property is restored. Independent unrelated properties and stronger Session user visibility are preserved. Anonymous backups are memory-only, not USD disk exports.

## RED and GREEN, with exclusions retained

- Valid v1 layered USD RED: **10 FAIL / 1 PASS** (logs/layered-valid-original-red.*). Includes actual caller Session -> On -> Root same-path author -> registry install -> Off, authored default/nondefault samples, exact metadata, weak-only prims, four stronger Session/sublayer default/sample refusals, transactional rollback, and the real show_volume_display function refusing before rebuild.
- Valid exact checkout-parent RED: **4 FAIL / 2 PASS** (logs/checkout-parent-valid-original-red.*).
- Intermediate private v2 retired-mask flag regression: one valid FAIL. The same run had one invalid restoration fixture (it tried to intercept CopySpec for an originally unauthored property); raw source/log and explicit clarification are retained. The corrected fixture explicitly authors the original Root property, then confirms failure visibility, other-entry attempts and retry.
- Shared ancestor closure RED: **3 FAIL** (logs/shared-ancestor-red.*) for multiple weak-only siblings and failed Off/On rollback retry; exact source before that fix retained.
- Final Python: **57 PASS** (logs/final-python-v2.*): 19 layered ownership/lifecycle/error tests + 20 original focused tests + 18 copied existing real CPU OpenUSD tests. No actual Kit or converter is run.
- Final frontend: **9 PASS** (logs/final-frontend.*): 3 snapshot role witnesses + 6 reference helper tests.
- Private copied renderer/test TypeScript: return code **0** (logs/final-types.*). Final Python-only ancestor changes do not alter those TS sources.
- First missing private dependency and escaped-newline VM harness errors are retained and explicitly excluded from valid product RED; see all three logs/*clarification.json files. Prior green logs are preserved, not overwritten.

The actual show_volume_display source is AST-loaded unchanged; only its unrelated domain helper is an explicit controlled stub, and a rebuild trap establishes that rejection happens before any rebuild. The actual author_scene and Sdf/Usd layer composition run in-memory. Dataset ingestion and extension clear/author tests retain their original controlled mocks. These prove this code boundary, not IndeX execution, field GPU validity or full runtime behavior.

## Runtime and publication limits

No production source, case, services, browser, Kit, converter, native binary, Git index or branch was changed. The original /tmp dataset-info write and missing room mask diagnosis remains real historical evidence, not corrected retroactively. No full OV postprocess/transient/material acceptance, any-case five-minute mesh guarantee, strict Shinaide mesh acceptance or air solver result is claimed.

All Python/NPM/node compiler caches and pytest basetemps are inside this packet's owned tmp/ or .vite/; generated caches and node_modules are excluded from declared payloads. Manifest lists only frozen source/tests/configs/fixtures/raw logs/receipts/diffs. Source/fixture originals are not regenerated.

Any later authorized normal IT/MAIN build must preserve full old dist assets plus all existing dist/proof outputs (including ROOT's P19 JPEG), restore missing old assets by exact bytes, keep the new index, and leave services/user browsers untouched until ROOT arranges normal deployment. That build/restart is not performed here.
