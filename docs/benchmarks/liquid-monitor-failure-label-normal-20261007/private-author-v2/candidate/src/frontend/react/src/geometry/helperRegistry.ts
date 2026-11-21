/**
 * Null-safe access to the shared viewport-helper scale registry (WP-S3).
 *
 * The registry itself lives in `shared/js/geometry_pure.js` (WP-S1):
 * `registerHelper(mesh, dataRadius)` hands a helper mesh to
 * `syncHelperScales(camera)`, which the Viewport render loop calls once per
 * frame to draw every registered helper at `helperDisplayRadius(dataRadius,
 * dist)` — never larger than its data radius, and shrinking as the camera
 * closes in, so a 50 mm gizmo handle does not become a beach ball at arm's
 * length. Both editors share the one implementation, as they share
 * `handleCubeSize` / `anchorDisplayRadius`.
 *
 * Read off `window.geometryPure` rather than importing
 * `../legacy/geometryBuilder`: the visual modules that register helpers (the
 * mesh annotations, the AI box, the pipe handles, the seed and orientation
 * helpers) are unit-tested in isolation, and pulling the whole shared-JS
 * import chain into their module graph just to resize spheres is out of
 * proportion. The window accessor is the precedent in `partitionWall.ts`,
 * `patchActions.ts` and `customMesh.ts`. The type comes from the bridge module
 * as a TYPE-ONLY import, so nothing is loaded at runtime.
 *
 * Every function is optional-chained: until the shared layer lands,
 * `registerHelper` reports `false` and callers keep writing their own scale.
 */

import type * as THREE from 'three'
import type { GeometryPureApi } from '../legacy/geometryBuilder'

function api(): GeometryPureApi | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: GeometryPureApi }).geometryPure ?? null
}

/**
 * Register `mesh` to be scaled per frame by the shared registry.
 *
 * Returns `false` when the registry is absent (shared layer not landed), so
 * callers can fall back to writing scale themselves instead of leaving a
 * unit-geometry mesh at scale 1.
 */
export function registerHelper(mesh: THREE.Object3D, dataRadius: number): boolean {
  const pure = api()
  if (typeof pure?.registerHelper !== 'function') return false
  pure.registerHelper(mesh, dataRadius)
  return true
}

/** Release a mesh handed to {@link registerHelper}; a no-op without the registry. */
export function unregisterHelper(mesh: THREE.Object3D): void {
  api()?.unregisterHelper?.(mesh)
}
