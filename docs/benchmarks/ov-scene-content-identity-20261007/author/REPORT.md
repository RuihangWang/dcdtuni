# Source-content scene identity and accepted selection

PRIVATE only. No normal source, service, API, browser, Kit, GPU, Git, native mesh or build operation was performed. Baseline is normal I `501b5dfe00115a6d7bdbd4fc1f08fe5e930d853b`; exact per-target source hashes are in APPLY-MAP.json.

## Product defects and narrow correction

`projectStore.emit()` increases its repaint counter for `logAction`, `markSaved`, and a non-content live notification. RendererControl used this counter as scene content identity. Its accepted edit bridge also required the captured counter to remain equal. Actual project UID/hash and accepted native scene revision remained unchanged in all three operations, yet the original controller loaded the whole scene and the original bridge rejected the authenticated selection as `stale_project`.

RendererControl keeps the real store subscription but keys the scene on `projectContentHashSync(project)` instead of the repaint counter. The hash is recomputed on each notified render rather than cached only by object reference, so legacy in-place source changes remain observable. Project UID, result sample/time/material-render revision, and independent display revision stay in the key. The edit bridge removes only the repaint-counter equality. It retains live project UID, exact canonical source hash, event hash, accepted scene generation, active interaction permission, source primitive whitelist/visible bit, and complete atomic UID validation. Motion whitelist and all source/edit permissions are unchanged. The key helper accepts the actual string identity while preserving existing numeric controlled callers.

Three and OV still share the existing React selection driver. No synthetic pending click is accepted, no native event ingress is added, and the current scene/session fences are retained.

## Actual packet evidence

The original owned Kit log prefix is 60,920,320 B (SHA725745c99b38d69349251ee0adb748ff722932182f6dbadd7b39eaf8a186e9db). Four original packet transfers were reconstructed only in RAM, never emitted as raw scene/texture/chunk data. The bounded replay is in evidence/four-packet-replay.json; AUDIT_PACKETS.py independently rechecks the immutable prefix digest and four complete packet digests. Original live log may append; only this fixed prefix is bound.

All four have the same source UID/hash and 2,348 primitives. First→second changes only 34 CDU material entries and one texture inventory. Second, third and fourth have identical primitive/texture/all-non-view digests while each transmits 1,172 chunks. They change time 20→0→20. This demonstrates real repeated content, but it does not uniquely identify the browser trigger or prove why a particular live Shift event was lost. An observed earlier update carries raster sceneRevision2 after the full scene is already at revision3; the native strict mismatch rejection is retained. No actual pixel or native Shift acceptance is claimed by CPU tests.

## Real controlled RED and GREEN

Only the transport/native window carrier is controlled. Tests use actual mounted RendererControl, actual projectStore, shared source hash implementation, real Three carrier objects/SceneSnapshot, real OvCommandClient event decoding, JSON schema and accepted selection bridge.

Before: 11 new assertions, actual rc1 / no timeout, 6 PRODUCT failures +5 passes, 20.23 s. Each log/save/non-content notification triggers one unwanted full load; each also causes `stale_project` despite unchanged UID/hash/scene identity. All six failures are assertions on actual product behavior; no collection/tooling failure.

Candidate: six focused suites /52 tests PASS, actual rc0 / no timeout, 14.66 s (CPU36, single worker). This includes the11 new tests, current16 RendererControl tests,11 edit bridge tests,5 full-selection tests,7 mounted camera ownership tests and2 display-revision tests. Real geometry and hidden-source edits invalidate the old event during transfer, create exactly one new scene ACK, and continue rejecting old revisions; an ACKed invisible source remains non-pickable. Wrong hash/revision/primitive/mixed UID events, display-only invalidation, current camera late-reply fences and result key components remain covered. Raw stdout/stderr/status are unchanged under logs/.

The existing RendererControl regex expectation changes from `revision` to `sourceHash`. This implementation-specific expectation adjustment is separate from the new actual product RED/GREEN. No production permission is relaxed to satisfy a fixture.

## Reproduction / limits

Run `/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python REPRO.py --cpu 36 --output <fresh B/.codex-tmp directory>`: before11(6 fail/5 pass), candidate52 pass; each child is capped at120s, writable TMP/cache entirely inside that fresh private directory, Vitest cache disabled, one worker, CUDA empty. Three production files and tests are copied by value; other555 bound source/runtime dependencies are readonly symlinks with full before/after hash guards. REPRO verifies the frozen manifest when present. No production build or normal types run was authorized/performed here.

For the original packet evidence only, run `AUDIT_PACKETS.py --output <new B/.codex-tmp JSON>`. It requires the original owned log's fixed prefix, so this observational part has an explicitly bound external log dependency. The source regression replay does not need that runtime log.

Unresolved separate issues: current `liquidActive=true` writer exists only in the unmounted old OV panel; selected time/result recoloring can still require full snapshots via the retained result tuple. Three highlight material changes can be included by the snapshot material reader. These are not removed or called solved by this narrow source-identity change. Native selection ingress and actual initial camera/pixel attribution require Root's bounded real-browser work.

## Apply scope

Five exact I targets:3 production, one existing expectation, one new regression. APPLY-MAP.json supplies exact before/after SHA and source paths. Normal camera patch prefix and new whole-selection source patch are retained. Parent must guard each target, review this frozen payload, then perform any normal test/build/publication/browser operation separately. Main remains outside this apply map. All9 captured source/test/config baselines and555 replay dependencies were unchanged after the actual CPU run.
