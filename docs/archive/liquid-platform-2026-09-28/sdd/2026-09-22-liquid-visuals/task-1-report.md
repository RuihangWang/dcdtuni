# Task 1 report — realistic liquid equipment visuals

Status: implementation complete; final verification and commit evidence below.

## Preflight and design

- Worktree/branch checked before edits: `codex/liquid-control-platform` at base `1add6c61d6bc9129d517072919a58bb4cc7ae9ba`.
- Existing unrelated untracked runtime-client files were preserved and excluded from this task.
- `geometry_pure.applyModelTransformToObject` establishes the authoritative min-corner anchor plus `geometry.scale` and normalized `geometry.quaternion`; existing CDU/rack pipe helpers were axis-aligned, while React rack snapping carried a separate quaternion implementation.
- React STL export reads only `sceneBuilder.registries.renderMeshes` and their `exportName`. Decorative liquid detail is therefore attached below each structural root, marked non-selectable/export-excluded, and never registered as a render mesh.
- Port design: one common local-to-world frame; CDU connection centers retain the old unrotated pipe-renderer position; rack connections honor manifold layout and mounting face. Metal necks and collars terminate at that center rather than adding a second connection point.
- Visual direction: neutral powder-coated generic cabinet, inset service door and restrained HMI; actual-diameter metal/manifold fittings with subdued blue/red role rings. No vendor or CDU 70 claim.

## TDD evidence

RED was observed before implementation:

```text
test/legacy/geometryBuilder.test.ts: 2 failed — liquidEquipmentPortFrame is not a function
test/sceneBuilder.test.ts: 2 failed — expected two liquid detail roots, received zero; no disposable detail mesh
test/liquidEquipmentGraph.test.ts: 1 failed — compiled supply x=10.4, expected rotated x=10.6
```

Focused GREEN verification and full typecheck are recorded after the final self-review.

## Implementation

- Added canonical full-quaternion/scale port frames to shared geometry.
- Unified pipe rendering, rack snap targets and graph-node/branch coordinates on that transform.
- Replaced auto-route endpoint spheres with short metal couplers and restrained supply/return collars.
- Added React-only generic CDU and liquid-rack detail with structural/export separation and root-owned disposal.
- Added developer documentation and functional tests for rotated CDU/rack agreement, compiler/render agreement, export exclusion and cleanup.

## Commit and verification

Implementation and integration fixes included in the frontend closeout commit (see git log).

Final root closeout: 7 focused Vitest files / 49 tests passed. npm run build passed after correcting the slotHeight TypeScript annotation; existing bundle warnings remain. Actual browser load-in-air/switch-liquid showed 4 models and 2 detail roots without manual rebuild; screenshot /tmp/liquid-closeout-final.png.

## Remaining concerns

- Browser screenshot review is owned by the root task on its existing isolated port 8350 session.
- The illustration is deliberately generic until site CDU model, rear-interface layout and rack quick-connect dimensions are confirmed.
