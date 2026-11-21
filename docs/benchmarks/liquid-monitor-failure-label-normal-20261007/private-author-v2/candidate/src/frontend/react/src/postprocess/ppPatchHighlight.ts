/**
 * The purple overlay that marks the selected patches on the post-processing
 * surface.
 *
 * There is no per-patch object to paint here — `buildSurface` draws all of them
 * as one mesh — so the selected patches are drawn a SECOND time, sharing the
 * surface's position buffer and holding only their own slice of its index
 * array. Sharing the buffer is what keeps this cheap: the overlay allocates one
 * `Uint32Array` of indices and nothing else, so a selection change costs the
 * indices of the selected patches, not a copy of the room.
 *
 * ## Reaching the scene
 *
 * The post-processing group is a Three.js object six components away, and the
 * only handle published outside the viewport is `__ppViewport`, whose API has
 * no hook for adding an object. The camera IS published, through `viewportBus`,
 * and the viewport adds the camera to the scene (Viewport.tsx:202) — so
 * `camera.parent` is that scene and the group is a named child of it. Reading
 * the graph rather than adding a viewport API keeps this feature inside the two
 * panels it belongs to.
 */

import * as THREE from 'three'
import {
  patchTriangleRanges,
  selectionIndices,
  visibleTriangleTotal,
  type TrianglePatch,
} from '../geometry/patchHighlight'
import { PATCH_HIGHLIGHT_COLOR } from '../geometry/patchListDom'
import { getViewportHandle } from '../viewportBus'

const OVERLAY_NAME = 'ppPatchHighlight'

/** The post-processing group, or null while the viewport is not up. */
export function ppGroup(): THREE.Object3D | null {
  const camera = getViewportHandle()?.camera
  const scene = camera?.parent
  return scene?.getObjectByName('postprocess') ?? null
}

/** The single merged boundary-surface mesh, tagged by `buildSurface`. */
export function ppSurfaceMesh(): THREE.Mesh | null {
  let found: THREE.Mesh | null = null
  ppGroup()?.traverse((o) => {
    if (found) return
    if ((o.userData as { ppLayer?: string }).ppLayer === 'surface' && (o as THREE.Mesh).isMesh) {
      found = o as THREE.Mesh
    }
  })
  return found
}

function removeOverlay(group: THREE.Object3D) {
  const existing = group.getObjectByName(OVERLAY_NAME) as THREE.Mesh | undefined
  if (!existing) return
  group.remove(existing)
  existing.geometry.dispose()
  ;(existing.material as THREE.Material).dispose()
}

/**
 * Redraw the overlay for `selected`.
 *
 * `hidden` must be the set the surface was BUILT with. The triangle total is
 * checked against the geometry's own index count before anything is drawn: a
 * mismatch means the surface belongs to a different hidden set, and slicing it
 * with these offsets would paint some other patch purple — a wrong picture that
 * looks right. In that case the overlay is dropped and the next render, which
 * follows the rebuild, puts it back.
 */
export function updatePpHighlight(
  selected: ReadonlySet<string>,
  patches: readonly TrianglePatch[],
  hidden: ReadonlySet<string>,
): boolean {
  const group = ppGroup()
  if (!group) return false
  removeOverlay(group)
  if (selected.size === 0) return true

  const surface = ppSurfaceMesh()
  const index = surface?.geometry.getIndex()
  const position = surface?.geometry.getAttribute('position')
  if (!surface || !index || !position) return false
  if (index.count !== visibleTriangleTotal(patches, hidden) * 3) return false

  const ranges = patchTriangleRanges(patches, hidden)
  const picked = selectionIndices(index.array as ArrayLike<number>, ranges, selected)
  if (picked.length === 0) return true

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', position)
  geometry.setIndex(new THREE.BufferAttribute(picked, 1))
  geometry.computeVertexNormals()

  const material = new THREE.MeshBasicMaterial({
    color: PATCH_HIGHLIGHT_COLOR,
    side: THREE.DoubleSide,
    // The overlay is coplanar with the surface it marks; without the offset the
    // two z-fight and the highlight renders as speckle.
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  })
  const mesh = new THREE.Mesh(geometry, material)
  mesh.name = OVERLAY_NAME
  mesh.renderOrder = surface.renderOrder + 1
  // Not a boundary patch and not a layer the render-order chips or the
  // internalMesh checkbox know about; leaving ppLayer unset keeps both from
  // treating the highlight as part of the result.
  group.add(mesh)
  return true
}

/** Drop the overlay — on unmount, or when the patch list goes away. */
export function clearPpHighlight() {
  const group = ppGroup()
  if (group) removeOverlay(group)
}
