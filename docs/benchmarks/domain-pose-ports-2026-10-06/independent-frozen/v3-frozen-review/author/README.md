# Renderer-free model pose and equipment-port ownership

The same numeric kernel now serves actual domain methods and the existing Three compatibility helpers. No second graph compiler, rendering engine, store mutation or serialized function text was added.

## Scope and validation

Eleven source paths are listed in source-final.json. GeometryEntity owns pose()/localPointToWorld()/localDirectionToWorld(); RackEntity and CduEntity own portFrame() using registry kind rather than a missing raw record.kind. rack_state defaults, geometryPure clone/world/port helpers, React geometryBuilder, the independent format/factory loader, and legacy HTML all resolve the same shared kernel. Standalone CommonJS callers require that same module.

The original copied producers were captured before implementation. Their hashes, actual LiquidSingle/Shinaide input hashes and golden fixture binding are in golden-producer-binding.json. The baseline had ten missing-method failures and one passing legacy control. Final targeted validation passed 125 tests across ten files; thirteen are new pose/port tests. TypeScript passed. Logs and validation commands remain unedited, including the extra nonfinite NaN sign/payload failure.

All ordinary corpus Float64 values remain bit-exact, including mirrored/nonuniform scale, quaternion normalization, numeric string defaults, zero quaternion, both equipment kinds, mount faces and slot-height threshold. The added Infinity edge case preserves NaN classification; IEEE NaN sign/payload is not promised. Finite and signed-zero assertions remain bit-exact. Actual structural SceneBuilder/STL, equipment ports, graph compilation, codec/save/UID, rack naming and display regressions remain green.

## Deliberate compatibility

- Transform components use Number.isFinite without coercing numeric strings. Coordinates/dimensions retain Number(value) || 0, including Infinity.
- Quaternion sqrt/reciprocal/component order and Vector3 cross-product expansion retain the installed Three arithmetic. Zero quaternion becomes identity; zero direction remains zero. Nonuniform direction scale is preserved.
- Rack manifold_in selects supply; CDU manifold_in retains the old return branch. Unknown roles retain return, and only exact bottom selects the lower endpoint.
- Rack slotHeight uses > 1e-6; external graph thresholds and visual radius clamps were not changed.
- Defaults, clones, poses and port frame numeric objects are freshly allocated. Kernel exports are frozen, with no mutable shared defaults.
- Values are persisted project coordinates. SI conversion remains at existing physical adapters; this slice does not add unit reinterpretation.
- Legacy HTML cloneModelTransform currently forwards rawTransform = {}. That original independent behavior is recorded and left unchanged.
- Missing Three still yields null from the old point/direction/port carrier helpers. The numeric core and domain methods do not require Three.
- The UMD shell only installs the compatibility API; its numeric factory reads no window, scene, project store or external state.

## Acceptance boundary

No production build, browser validation, business meshing/solver run, Git/index mutation or service change was performed. This closes a real renderer-free pose/port slice. Complete geometry/material/physics ownership and renderer adapter migration remain subsequent stages.
