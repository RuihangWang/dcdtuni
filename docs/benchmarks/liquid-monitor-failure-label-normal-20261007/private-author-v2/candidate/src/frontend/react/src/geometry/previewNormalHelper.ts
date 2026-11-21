/**
 * The yellow arrow that says which way a previewed solid faces.
 *
 * STL preview mode exists to answer one question — will this solid face the
 * fluid the right way — and the arrow IS the answer. The legacy draws it on the
 * selected region: an `ArrowHelper` in 0xfff27a from the region's centre along
 * its average normal, with a matching tip sphere so the far end is visible
 * against the surface it stands on (`updateStlPreviewNormalHelper`,
 * STL_generate.html:7488, cleared at :7253).
 *
 * Without it a reversed solid looks exactly like a correct one, and the reverse
 * toggle beside it has nothing to check against.
 */

import * as THREE from 'three'
import { geometryPure } from '../legacy/geometryBuilder'
import { isPreviewActive, selectedPreviewRegion } from '../io/stlPreview'
import { registerHelper, unregisterHelper } from './helperRegistry'

/** `0xfff27a` — the legacy's. */
export const PREVIEW_NORMAL_COLOR = '#fff27a'

export interface NormalHelperSpec {
  center: THREE.Vector3
  /** Unit length, Y-up when the region's own normals cancel out. */
  normal: THREE.Vector3
  arrowLength: number
  headLength: number
  headWidth: number
  tipRadius: number
  /** Where the sphere sits: centre + normal * length. */
  tip: THREE.Vector3
}

/**
 * The legacy's arithmetic, clamps included (:7499-7509).
 *
 * The clamps are what make one arrow work for a 10 m wall and a 40 mm vent:
 * 0.45 of the largest dimension, never shorter than 0.15 m (invisible) and
 * never longer than 1.2 m (swallows what it points at).
 */
export function normalHelperSpec(
  geometry: THREE.BufferGeometry,
  averageNormal: THREE.Vector3,
): NormalHelperSpec {
  const bbox =
    geometry.boundingBox ??
    new THREE.Box3().setFromBufferAttribute(
      geometry.getAttribute('position') as THREE.BufferAttribute,
    )
  const center = bbox.getCenter(new THREE.Vector3())
  const size = bbox.getSize(new THREE.Vector3())

  let normal = averageNormal.clone()
  // A normal that cancels out is not a reason to draw nothing. Y-up, as the
  // legacy switched to in 2026-04.
  if (normal.lengthSq() < 1e-8) normal = new THREE.Vector3(0, 1, 0)
  normal.normalize()

  const arrowLength = Math.max(
    0.15,
    Math.min(1.2, Math.max(size.x, size.y, size.z, 0.05) * 0.45),
  )
  return {
    center,
    normal,
    arrowLength,
    headLength: Math.min(arrowLength * 0.28, 0.18),
    headWidth: Math.min(arrowLength * 0.18, 0.09),
    tipRadius: Math.max(0.03, Math.min(0.08, arrowLength * 0.14)),
    tip: center.clone().add(normal.clone().multiplyScalar(arrowLength)),
  }
}

/** The arrow and its tip sphere, tagged so `updatePreviewNormalHelper` finds it. */
export function buildNormalHelper(spec: NormalHelperSpec): THREE.Group {
  const group = new THREE.Group()
  group.add(
    new THREE.ArrowHelper(
      spec.normal,
      spec.center,
      spec.arrowLength,
      PREVIEW_NORMAL_COLOR,
      spec.headLength,
      spec.headWidth,
    ),
  )
  // WP-S3: a UNIT sphere at the legacy tip radius, registered with the shared
  // per-frame helper scaling. The scale write is the initial condition (and
  // the fallback look while the registry is absent).
  const tip = new THREE.Mesh(
    new THREE.SphereGeometry(1, 18, 18),
    new THREE.MeshPhongMaterial({ color: PREVIEW_NORMAL_COLOR, shininess: 30, specular: 0x111111 }),
  )
  tip.scale.setScalar(spec.tipRadius)
  registerHelper(tip, spec.tipRadius)
  tip.position.copy(spec.tip)
  group.add(tip)
  group.userData = { isPreviewNormalHelper: true }
  return group
}

/** Throw the previous helper away, wherever it is in the scene. */
export function clearPreviewNormalHelper(scene: THREE.Object3D | null | undefined): void {
  if (!scene) return
  const stale: THREE.Object3D[] = []
  scene.traverse((o) => {
    if ((o.userData as { isPreviewNormalHelper?: boolean }).isPreviewNormalHelper) stale.push(o)
  })
  for (const helper of stale) {
    helper.traverse((o) => {
      const mesh = o as THREE.Mesh
      // Only the tip sphere was ever registered; unregistering the rest is a
      // no-op on the shared side and keeps this loop from having to know.
      unregisterHelper(mesh)
      mesh.geometry?.dispose()
      const mat = mesh.material as THREE.Material | THREE.Material[] | undefined
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose())
      else mat?.dispose()
    })
    helper.parent?.remove(helper)
  }
}

/**
 * Redraw the helper for whatever region is selected now.
 *
 * It is parented to the region MESH's own parent rather than to the scene root:
 * the preview groups carry a world-to-local matrix (`rebuildPreview`), so a
 * helper hung off the root would stand wherever that matrix is not. Reading the
 * geometry off the drawn mesh has the same reason — that is the geometry the
 * user is looking at.
 */
export function updatePreviewNormalHelper(scene: THREE.Object3D | null | undefined): void {
  clearPreviewNormalHelper(scene)
  if (!scene || !isPreviewActive()) return
  const selection = selectedPreviewRegion()
  if (!selection) return

  let target: THREE.Mesh | null = null
  scene.traverse((o) => {
    if (target) return
    const data = o.userData as { isPreviewRegion?: boolean; modelId?: string; regionName?: string }
    if (
      data.isPreviewRegion &&
      data.modelId === selection.modelId &&
      data.regionName === selection.regionName &&
      (o as THREE.Mesh).isMesh
    ) {
      target = o as THREE.Mesh
    }
  })
  if (!target) return

  const mesh = target as THREE.Mesh
  const helper = buildNormalHelper(normalHelperSpec(mesh.geometry, averageNormalOf(mesh.geometry)))
  ;(mesh.parent ?? scene).add(helper)
}

/**
 * The region's average normal, through the SHARED implementation.
 *
 * `getAverageNormalForGeometry` is what the exporter and the legacy helper both
 * use; computing a second one here is how the arrow and the exported winding
 * would come to disagree.
 */
function averageNormalOf(geometry: THREE.BufferGeometry): THREE.Vector3 {
  try {
    const pure = geometryPure() as unknown as {
      getAverageNormalForGeometry(g: unknown): { x?: number; y?: number; z?: number }
    }
    const raw = pure.getAverageNormalForGeometry(geometry)
    return new THREE.Vector3(Number(raw?.x ?? 0), Number(raw?.y ?? 0), Number(raw?.z ?? 0))
  } catch {
    return new THREE.Vector3(0, 0, 0)
  }
}
