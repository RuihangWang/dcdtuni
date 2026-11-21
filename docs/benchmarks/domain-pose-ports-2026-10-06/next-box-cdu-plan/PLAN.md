# BOX/CDU numerical GeometryProduct next slice

Read-only proposal bound to the source hashes in source-identity.json. The frozen pose/ports 11-source implementation is held unchanged. This document proposes implementation; it does not certify renderer/STL/OV parity or mesh acceptance.

## First implementation boundary

Implement actual structural BOX/CDU face geometry in one shared numerical producer, then replace geometry_scene._buildSingleBoxFace (2236–2318) with a thin Three BufferGeometry carrier. React SceneBuilder and classic STL_generate already both call that factory; no independent React geometry implementation is needed. Do not change racks, planar wall/floor/baffle, wall-mounted ACU, custom STL, detailed CDU HMI, physical pipe connections, or exported geometry scope.

The constructor receives explicit model UID/kind, size and ordered face/patch records, pure naming options and pose. It returns owned plain data with Float32 position/normal/UV buffers, Uint16/Uint32 indices, face/patch/wall-part metadata, material recipes and separate model pose. No THREE, DOM, window state, scene, material objects, image loading, or solver state belongs in the numerical core. GeometryEntity/BoxEntity/CduEntity.geometryProduct() delegates to this producer using registered kind; supported kinds are explicit. Legacy BOX display names never become physical CDU/PDU types.

## One dependency and loading graph

Installed Three 0.184.0 exposes the pure Earcut 3.0.2 source at src/extras/lib/earcut.js; its numerical function has no Three imports. polygon-clipping.umd.js is already shared. Bind both source SHAs, not an assumed clipper version.

For this narrow classic/React slice, use one shared pure Earcut UMD entry derived from that installed source: wrap the export only, preserve algorithm text/arithmetic, original license attribution and source hash. Both classic scripts and React import this same entry and the same numerical product module; there is no ShapeUtils fallback or second BOX triangulator. Three's installed dependency remains untouched for unrelated legacy kinds. No package installation or runtime compiler is needed.

Classic HTML has blocking scripts at 1244–1250 followed by the main inline factory and an async DOMContentLoaded callback at 4666. Load Earcut and the product module before geometry_pure/geometry_scene. React geometryBuilder imports the same files before creating its factory. CJS direct callers require these same paths. Missing dependency throws a named error at factory creation; do not defer readiness until after a synchronous consumer. This retains the existing loading graph rather than introducing an unresolved type=module race.

Proposed next source scope: shared/vendor/earcut.umd.js plus attribution; shared/js/box_geometry_product.js and declaration; domain product bridge/BaseEntity; geometry_pure face-frame delegate; geometry_scene carrier; geometryBuilder and classic HTML loader; focused tests/producer-bound fixtures. Any direct STL or OV protocol change is a separate reviewed step, not a prerequisite for removing ShapeGeometry from this branch.

## Numerical contract to preserve

1. getFaceLayouts (geometry_pure 956–988): preserve six-face order, exact origins/right/up, dimensions, normal right-cross-up, colors and extDepth. Keep wall-mounted ACU's different layout on its old branch. World pose remains the shared pose kernel; matrix arithmetic must retain existing multiply order, not substitute a quaternion formula merely because it is mathematically equivalent.
2. buildFaceIslands (569–599): preserve face/patch iteration order, circle's 32 samples and closed ring, quad offsets, intersection catch semantics, union ordering, inherited name/color and refIds. Difference against each merged island is presently uncaught; do not turn invalid boolean input into silent success.
3. Shape extraction: reproduce exact consecutive-duplicate and closing-end removal; winding uses the existing signed-area accumulation order, exterior clockwise and holes opposite. Flatten outer then holes; Earcut returned triangle order is authoritative. Never tolerance-snap, globally canonical-sort, or retessellate.
4. Quantization: ShapeGeometry writes local vertices/normals/UV to Float32 first; layout applyMatrix4 reads that Float32 and writes transformed positions/normals to Float32 again. Preserve this two-stage quantization. Three uses Uint32 whenever any index is >=65535. Base normals are +Z, layout normal-matrix conversion is separate from STL facet normals.
5. UV: preserve original XY UV, Float32 mutation order, image stretch and tile-per-u logic (geometry_scene 2562–2579). PBR scaling currently depends on the material's pbrRole and tileM (2585–2588); expose one recipe/UV-mode calculation, keep texture readiness and image IO in the carrier. Do not duplicate the material preset table.
6. Parts: patches precede remaining wall fragments on each face; original face dictionary order is preserved. Stable semantic part keys may add ownerUid/faceId/role/refIds/islandIndex/wallIndex, but preserve composeModelScopedExportName, fragment counts, renderMeshes order and the current scene child path. New IDs must not rename OpenFOAM solids or snapshot primitives.
7. Physical/display/helper: structural parts retain exportName and isBaseWall. CDU liquidEquipmentVisuals details are display-only and exportExcluded, with existing local PBR/HMI ownership. Selection/fade/edges/helpers remain adapters; no image, display opacity or helper enters physical triangles.

ensureWallOpacity (geometry_pure 292–295) presently materializes face.wallOpacities. The pure product must not mutate the model. Keep this existing normalization explicitly in the legacy compatibility adapter before the producer, and compare save deltas. Removing that render-time document write is a separate behavior change. BOX currently does not read geometry.cutouts; planar sheet code does. Do not silently activate BOX cutouts in this slice.

## Three, STL and OV lowerings

First migrate Three to owned buffers and preserve its existing spawnRenderable/material/highlight/disposal behavior. Existing STL then consumes those very buffers, not a second producer. This is a real shared geometry ownership change even while its legacy STL wrapper remains.

stl_writer 78–114 reads local Float32 attributes, applies world matrix in Float64, flips B/C only for mirrored world matrices, recomputes facet cross/normal, and applies existing degenerate tests (coincident squared threshold 1e-18; area2 finite and >1e-12). Actual production text is toFixed(6), not scientific .6e. Preserve order/indent/newlines and negative-zero rounding bytes: exact -0 formats as zero, small negative rounded values may print -0.000000. No epsilon cleanup or sign normalization.

A later direct plain-buffer STL lowerer can use the same product, only after exact full-string parity with the current installed writer. It must preserve disabled-model inclusion and unmerge/group insertion order; no schema or solver changes.

Current OV sceneSnapshot already consumes local buffers + root-relative matrix, normals, UV transformed through actual texture state, current visibility/material snapshot and camera. It can consume the same lowered product unchanged. Preserve UID/child-path/instance/group IDs (sceneSnapshot 497), root-relative camera and texture flip semantics. Future direct USD/OV lowering should use product parts and an explicit old-ID mapping; do not change the protocol in this slice.

## Cache and material boundaries

Product cache inputs are the actual ordered materialized size/faces/patch geometry/pose fields and naming inputs. Playhead can materialize geometry without changing document-input revision, so revision alone is not a geometry key. Image/texture readiness, opacity, selection, current camera and renderer state are separate carrier inputs. Avoid deep-copying whole projects/base64 images in allModels. Owned product buffers are not serialized into cfdproj and cannot be mutated by callers; disposed Three carriers must not destroy shared source textures or corrupt reusable product data.

Material recipes contain existing role/color/opacity/UV mode and texture references, not Texture or Canvas objects. Procedural randomness, painting, texture cache and GPU upload stay outside this geometry kernel. Existing CDU/PDU display paint and unlit HMI should remain byte-equivalent; they are not part of structural CFD export.

## Acceptance before promotion

Capture original then-live producer source/dependency/input SHA and actual SceneBuilder products before editing. Actual LiquidSingle and Shinaide inputs are bound in fixture-binding.json; the standalone Alibaba PDU BOX is a separate display fixture. Neither actual simple CDU contains boolean holes, so additional owned synthetic patches are required and must be labeled synthetic.

Baseline red tests should require the new domain method and prove original physical output unchanged: Float32 buffer bits and index order/type, rendered normals/UV, part/exportName order, full STL bytes including mirrored/nonuniform scale, signed-zero formatting and degenerate rejection. Compare actual SceneBuilder structural export before/after, display-only detail exclusion, select/restore/fade/map cleanup, and snapshot primitive arrays/matrices/materials/camera for the same current state. Do not substitute triangle counts or visual similarity for parity.

Use exact producer-bound patches/circles/quad offsets/overlap/hole/disconnected/out-of-face fixtures, identity/translated/quaternion/mirrored/nonuniform poses and patch/wall opacity/image modes. Preserve cfdproj unknown fields and unchanged physics/source hashes. Root performs actual React/classic visibility, CDU service face, save/reload and OV acknowledgement. Tests and code presence alone do not prove those gates, air full checkMesh, SimReady/DSX or complete OOP migration.
