# Editor render scene → OpenUSD

The editable source is `.cfdproj`. Air execution derives `.cfdrun`; liquid execution derives `.liquidrun`. Neither run file is a rendering backend format. `dcdtuni.render-scene/v1` is a disposable visual snapshot derived from the **actual built** editor geometry, including liquid equipment detail and the separate pipe group. It must never be loaded as a project or solver input.

## Contract

`src/frontend/shared/schema/render-scene.schema.json` is the independent strict wire schema. React validates it with AJV; the Python bridge validates the same document and topology/texture references before touching a live USD stage. Unsupported/deformed geometry, missing object identity, malformed topology, unresolved textures and unsupported material maps fail explicitly.

- Coordinates: right-handed, Y up, metres. The editor's room-centring transform is removed from geometry **and** camera, leaving project coordinates. Negative scale is preserved in the full transform.
- Identity: each primitive carries the construction UID and optional equipment UID. USD prim names are hashes of primitive identities; display names never serve as keys. Renaming and equal display names do not merge assets.
- Geometry: indexed/non-indexed triangle meshes, material groups and draw ranges, per-instance transforms/colours, lines and points. Registered editing helpers and explicit gizmo tags are excluded; `exportExcluded` equipment detail remains visible because STL export rules are not display rules.
- Materials: embedded PNG textures with UV transforms and wrapping; linear base colour, emissive, opacity/cutout, double-sided state, roughness and metalness. Colour/normal/roughness/metalness maps are supported. Roughness uses the green channel and metalness the blue channel, multiplied by their scalar values as in Three.js. Normal scale and texture colour space are retained. Conflicting per-map UV transforms are rejected. Phong shininess maps to approximate PBR roughness and produces a warning. Custom shaders, physical material extensions and unsupported maps are rejected. Screen-space line width uses a documented 1 mm USD curve representation.
- View state: project-space camera pose/projection, selected UIDs and **physical seconds**, with `null` representing no result time. Updating only view metadata does not recolour geometry; when a new result frame changes mesh colours the caller must send a fresh snapshot.

## React integration

`buildSceneSnapshot({root, project, camera, target, selectedUids, timeSeconds, helpers})` receives the live `globalGroup`, not just `renderMeshes` (which intentionally excludes detail and independent pipes). `captureViewState` captures just the view. `OvViewport` is an overlay: keep Three mounted, and return to it on any stream/schema/ACK failure without resetting the project or undo history. Its `onReady` exposes `OvCommandClient` for `loadScene`, `updateView` and `readView`.

The NVIDIA stream library is lazy-loaded, pinned to the existing demo's 5.18.2 version and uses its public NVIDIA npm registry. Mounting the component connects to a configured running Kit host; it never starts a Kit process. The previous standalone OV demo remains supported.

The sender splits JSON into ordered bounded chunks. The bridge assembles and validates the packet, authors a temporary USD layer, then installs it, applies view state and sends a request-ID-correlated ACK. Transport delivery alone is not success. ACK means USD authoring succeeded; it is not proof of rendered pixels or simulation correctness. The ACK channel uses NVIDIA's [livestream messaging extension](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.livestream.messaging/latest/Overview.html).

The observed Kit extension `omni.kit.livestream.messaging-1.3.3` emits a small ACK as `{event_type,id,payload}`. Larger ACKs use `{event_type,id,part,numParts,payload}`, with one-based part numbers and JSON-string fragments in `payload`. The installed browser SDK 5.18.2 forwards custom application envelopes to `onCustomEvent`; the editor's own `OvCommandClient` reassembles these ACK fragments. Transport `id` is only an assembly key: after parsing the complete payload, **payload.requestId** must match an outstanding logical request. Ordinary small ACKs keep the same behavior.

ACK reassembly accepts out-of-order delivery and identical duplicates. It admits at most 128 parts per group, 64 KiB of UTF-8 per fragment, 1 MiB per complete ACK, 2 MiB of retained UTF-8 fragment content across groups, and eight simultaneous groups (including invalid-group tombstones); transport IDs must be nonempty strings no longer than 128 characters. These bounds apply to view/diagnostic ACKs, not the much larger outgoing mesh/texture scene. Conflicting duplicates/counts and memory-limit violations invalidate that group; malformed or unsolicited fragments are ignored and cannot signify successful authoring. The caller's existing timeout remains responsible for reporting an unacknowledged request.

Fragments do not extend the existing 45 s request deadline. Buffers and timers are released on completion, timeout, send failure or disposal; a group that began before a newer logical request cannot complete that newer request. Equal duplicate fragments do not consume additional storage. The bounded string/message-wrapper path also avoids unbounded recursion. CPU contract tests cover official small envelopes, large view/error payloads, ordering, duplication, malicious metadata, memory limits and lifecycle cleanup. This verifies protocol handling without replacing the still-required live-stream acceptance.

## CPU verification

React targeted tests (one worker):

```sh
cd src/frontend/react
npm test -- --maxWorkers=1 --minWorkers=1 test/sceneSnapshot.test.ts test/sceneSnapshotShinaide.test.ts test/ovProtocol.test.ts
```

OpenUSD tests run with the `dcdtuni` Python environment, using `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=''`:

```sh
python -m pytest -q src/frontend/omniverse/cfd_demo/tests/test_render_scene.py
```

A browser-exported snapshot can be checked and converted without Kit or RTX:

```sh
PYTHONPATH=src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge \
python -m dc.cfd_bridge.render_scene snapshot.json output/scene.usda
```

The CLI writes `scene_textures/` beside `scene.usda` and authors relative texture references; move both together between hosts. Kit's in-memory bridge keeps using absolute cache paths by default.

CPU tests cover actual USD points/transforms/material connections, camera, UID lookup, physical time, invalid-input preservation and chunk ordering. They do **not** establish live WebRTC/RTX correctness. This development pass intentionally does not start GPU rendering. The neutral USD lighting rig and Phong-to-PBR conversion do not imply pixel-identical Three/OV lighting.
