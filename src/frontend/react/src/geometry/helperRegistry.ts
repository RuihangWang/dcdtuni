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

import * as THREE from 'three'
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

/**
 * A point marker (anchor, seed, sensor, seed tip, zone centre): the shared
 * look and the shared on-screen size (`geometry_pure.js`, point markers).
 *
 * Builds the mesh, registers it so the render loop holds it at the size of
 * `kind` on screen, and returns it. Without the shared layer (unit tests) it
 * is a plain over-the-scene ball at the 8 m size, so callers need no branch.
 */
export type MarkerMaterial = THREE.Material & { color: THREE.Color }

export function createMarker(
  color: THREE.ColorRepresentation,
  kind: string,
  opts: { opacity?: number } = {},
): THREE.Mesh<THREE.BufferGeometry, MarkerMaterial> {
  const pure = api()
  const material: MarkerMaterial = pure?.createMarkerMaterial?.(color, opts)
    ?? new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: opts.opacity ?? 1, depthTest: false, depthWrite: false,
    })
  const geometry = pure?.createMarkerGeometry?.() ?? new THREE.SphereGeometry(1, 24, 18)
  const mesh = new THREE.Mesh(geometry, material)
  if (typeof pure?.registerMarker === 'function') pure.registerMarker(mesh, kind)
  else mesh.scale.setScalar(Math.max(0.0015, Math.min(0.35, (FALLBACK_SIZE[kind] ?? 0.008) * 8)))
  return mesh
}

const FALLBACK_SIZE: Record<string, number> = {
  anchor: 0.008, seed: 0.0095, extraSeed: 0.009, sensor: 0.0078,
  seedTip: 0.0072, seedHub: 0.006, zoneCentre: 0.0055,
}
