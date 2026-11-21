# Shared OV postprocess previews — private CPU review freeze

This freeze adds the two display-only interactions to the active shared React driver: the slice preview ring and streamline seed sphere. The common PostprocessPanel remains the only UI. No cut, trace, field, run selection or CFD input is computed or mutated by the native preview producer. Twelve targets: six existing files modified and six new files. No normal source or service was changed by this worker.

## Binding and lifecycle

The packet/ACK/event schema is strict and bounded. It carries the complete ResultSource, canonical numeric packet SHA, physical time, acknowledged scene revision, common display generation, common scope key, adapter owner nonce and exact set ID (`draft` or the selected list entry). Native leases and increasing sequences isolate repeat installations. The native controller binds only after the existing signed grant verifier and actual numeric authoring transaction succeed; mesh grants cannot authorize these postprocess interactions.

Clear, scene replacement, a newer valid numerical grant, legacy display replacement, renderer disposal and shutdown retire old handles. Exact owner tombstones reject retire-before-install races and survive same-frame numeric replays; an old adapter's retirement cannot clear a new owner's lease. The frontend aborts pending preview ACK waits and requires an explicit native retirement ACK. Late ACKs/events cannot unlock the current frame. Active accepted context is checked again at readback, and the adapter owns an immutable scene acknowledgement snapshot.

The native carrier uses the installed public ViewportWindow.get_frame, ViewportAPI.add_scene_view/remove_scene_view, omni.ui.scene.SceneView, Curve, PolygonMesh and DragGesture APIs. This pinned SDK has no Sphere shape, so a closed UV sphere is built with PolygonMesh. SceneView receives the active viewport camera matrices through the public API. Drag gestures consume native world rays, use a camera-facing plane, project ring displacement along the normalized plane normal, and emit only on END. Camera/view/projection changes during a drag cancel and restore it. API/window/app/widget/delegate/stage/camera/time/scene ownership is rechecked before END publication. Destroyed native APIs do not interrupt owned-widget/controller cleanup.

Preview readiness is separate from appearance capability. A same-generation, exact preview ACK permits only the two form readbacks while unsupported AO/light/model-opacity conversion remains honestly labelled. Native events never use the editable source-object selection channel. Existing numerical multi-set carriers remain unchanged, with the selected preview set ID forwarded to the existing common listener through the mounted production hook.

## Actual evidence

- Public clone source: 6622527782893e68bd3885667ac40c6f7db1df7a; actual initial clone was 2541de9e and earlier private bootstrap repairs were preserved before the authorized public source sync. This is a new private candidate on the updated public source, not an assertion that initial 2541 installed or tested unchanged.
- Final frontend controls: **139 PASS**, six files. Includes 26 new controls, real OvCommandClient request/ACK/event handling, actual adapter/common driver, mounted active hook, nonempty decoded two-slice/two-trace lists, wrong source/time/scene/generation/lease/ID, replay, malformed payload, clear/pending-ACK cancellation, engine replacement, retirement rejection and missing concrete retirement ACK.
- Final native controls: **38 PASS**. Real controller, ray/geometry arithmetic and actual bridge routing; the public SDK construction/gestures are explicitly controlled CPU stand-ins. They do not run a Kit application or prove native pixels.
- Production React build: RC 0, 51.82 seconds; final all-source/test TypeScript check RC 0. The final build uses the same production frontend bytes; only the additional retirement-ACK test was added after that build and is covered by the final TypeScript check.
- Broader existing native run is preserved **RED**: candidate 79 PASS / 8 FAIL before the last two cleanup controls; unmodified public D baseline has 43 PASS / the identical 8 FAIL. Seven failures come from existing AST harnesses missing `_sample_native_grid`; one expects an obsolete error string instead of the already-present signed-only rejection. No old test was skipped or mechanically rewritten. This comparison shows no new failure in that selected legacy subset; it does not make the full backend suite green.
- First frontend candidate attempt had a parse error and an added await that disturbed existing scene-idle sequencing. The next caught mutable acknowledged-scene aliasing; it was fixed with an owned snapshot and current accepted-context gate. A later multi-set control used `id` instead of the actual CommonRenderIntent `setId`; primary source justified that assertion correction. Raw RED logs remain.
- Initial private source copy lacked backend/tools imports needed by the full production build. The missing **821 tracked files / 15,281,953 bytes** were copied from the same clean public D source; the closure receipt is included. No production source was changed for that copy deficiency.
- One initial `npm run build` omitted the explicit npm cache override and wrote one debug file into the already-existing user `.npm/_logs` directory. Its exact log and deviation receipt are preserved, and no user path was deleted. All later commands and the replay entry explicitly put npm cache and TMPDIR inside the private repository. No global npm dependency/config/install change was made.
- CPU affinity 36–37, threads 2, CUDA empty, own actual Node 24.12.0 / npm 11.6.2 / CPython 3.10.22 / node_modules. No existing Kit/runtime/cache/GPU/service was borrowed or started. D tracked source remains clean; the twelve normal I before guards held when freezing (recorded HEAD in TARGETS.json).

## Independent replay

Use the fresh clone's owned toolchain and modules, and a new output path inside dcdtuni. The script validates every before/after guard, copies the provided repository's tracked frontend/backend/tools import closure without vendor trees, overlays exactly the twelve frozen targets, runs the 139 frontend and 38 native controls and production build with bounded owned process groups, and records actual versions/source hashes/logs. It never edits the source repository. For the original fresh-source reproduction, pass the clean D repository as `--source`; I can also be supplied for a current-source independent review when its twelve guards match.

```
F=/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/fresh-clone-actual-20261007/dcdtuni/.codex-tmp/ov-shared-previews-private/FROZEN-v1
D=/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/fresh-clone-actual-20261007/dcdtuni
/mnt/lv/home/litianyu/miniconda3/bin/python -B "$F/RUN_REPRO.py" \
  --source "$D" \
  --node-bin "$D/.codex-tmp/bootstrap-toolchain/node-v24.12.0-linux-x64/bin" \
  --python "$D/.codex-tmp/envs/dcdtuni/bin/python" \
  --node-modules "$D/src/frontend/react/node_modules" \
  --output "$D/.codex-tmp/ov-shared-preview-root-replay-1"
```

## Still unverified

Actual default factory construction in a running Kit, encoded-video visibility, user point hit/drag/END delivery, viewport black band and fullframe/encoder mapping need coordinated real-browser/GPU0 acceptance. The CPU shim and official source signatures prove API-contract implementation only. No positive encoder/crop/scale transform was invented; `output_layout.capture_current` and its unresolved layout gate are untouched. Background-grid pixel verification, full wind CFD, liquid numerical validation/MMS and whole-product fresh service launch are outside this candidate receipt. Root still holds final fresh-source sync/full frontend suite and GPU launch.

Official source snapshots and package licenses are included under `primary/`; the previous separate owned-stream API report remains the authority for unresolved encoder/layout observations. Nothing here revives the removed independent OV UI or its old singleton numerical commands.
