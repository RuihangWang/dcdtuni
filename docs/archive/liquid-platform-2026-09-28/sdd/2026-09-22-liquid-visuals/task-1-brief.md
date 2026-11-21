# Realistic liquid equipment: implementation brief

User-approved ongoing goal: own dcdtuni React liquid editor with Three.js first then OV, coupled simulation and optimization. User now explicitly requires more realistic drag/display equipment. This bounded task improves procedural equipment visuals and their port transforms. Existing architectural spec is in the local research docs; this brief is the implementation authority for this slice.

## Scope and constraints
- Own code only. Never read or import DiffLiquidTwin. Do not use externally downloaded model assets.
- Work only NTU-Litianyu-40901:/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform, branch codex/liquid-control-platform. Read repo instructions (credentials must never appear in output). No main edits, pushes, service launches or kills.
- Root owns App/components/dynamic runtime client. You own new procedural visual module, shared geometry/pipe/port integration and sceneBuilder if required. Avoid editing React App or runtime client. One implementation agent is running (you).
- HTML remains legacy air; enable new detailed visuals in React liquid mode. Preserve legacy public signatures and air export semantics.
- All equipment is generic engineering illustration; no unsupported NV model/geometry claim and no CDU70 liquid-to-air facade pretending to be liquid-to-liquid CDU.
- Structural sizes/location/rotation remain project authority. Decorative meshes must not pollute CFD/STL/mesh export, physical graph edges, selection identity, or bounds used for layout placement.
- Endpoint position helpers must share the actual equipment transform. Audit existing shared geometry rotations before implementation, don't assume radians/degrees/pivot. CDN-like port spheres should become short metal stub/coupler with subtle supply blue and return red marker. Retain endpoint center compatibility when unrotated.

## Deliverable
1. Reusable procedural detailed visuals: generic CDU neutral powder-coated cabinet with inset service door/panel, handles/plinth/feet, readable subtle HMI, visible rear paired ports; rack detailing with side manifolds/quick-connect collars and short server branch hoses where project structure supports it. Keep pipe diameter engineering scale; don't enlarge for visual drama. Clamp small features to device size. Avoid detailed unreal internal CDU hydraulics not present in topology.
2. Port transform correction for rotated CDU/rack, used by render/edit snapping and graph compiler endpoint logic where those paths currently duplicate coordinates. If rack manifold config governs endpoint, honor it consistently. No silent hydraulic length changes independent of displayed path.
3. Dedicated tests for 90-degree rotated+translated CDU/rack agreement, selection/export exclusion and resource cleanup when applicable; use existing Node/vitest/Three testing patterns. Do not add tests asserting aesthetic constants alone.
4. Document approximation/geometry authority and evidence in docs/developer/liquid-equipment-visuals.md. Write report to .superpowers/sdd/2026-09-22-liquid-visuals/task-1-report.md with commit hash, exact tests/output, remaining concerns.

## Starting observations (verify)
geometry_scene.js _buildSingleBoxFace around2208 uses bright cyan cduFaceColors. liquid_pipe_renderer.js getCduPort/getRackPort around84 ignore rotation. React interaction/sceneBuilder.ts delegates shared scene and owns context/helper lifecycle. Existing pipe renderer already has TubeGeometry fillets, tees/caps and helper registry; reuse rather than replace. geometry/rackManifold.ts defines configurable ports in metres.

## Sequence and verification
- Inspect narrow scene/port/export contracts; settle transform and helper lifecycle. Record design and preflight in report before edits.
- Implement failing functional transform/export tests then minimal common transform helper/integration.
- Add procedural detail module with shared materials/geometries/disposal; integrate only React liquid mode.
- Run affected tests + npx tsc --noEmit. Root runs real UI screenshots on isolated server port8350; coordinate any screenshot need by message rather than launch server.
- git diff --check, self-review, commit only owned files. No worker-spawned review agents. Return DONE/DONE_WITH_CONCERNS or precise blocker.
