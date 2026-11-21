/**
 * The render-order layer table and what a chip order means to the scene.
 *
 * A port of `_renderOrderLayers` / `_renderOrderDefaults` / `_roBaseOrder` /
 * `_roApplyToScene` (STL_generate.html:22312-22372). Both patch viewers show
 * the same chips from the same table — mesh preview with two of them, post-
 * processing with five — so the table lives here rather than in either panel.
 *
 * The spacing of 50 between layers is not decoration: it leaves room for a
 * layer to offset itself INSIDE its own band. The mesh preview uses it, drawing
 * its wireframe at `ro + 1` so the edges stay on top of the faces they outline
 * without the mesh as a whole crossing into the next layer.
 */

import type * as THREE from 'three'

export interface RenderLayer {
  label: string
  color: string
  icon: string
}

export const RENDER_LAYERS: Record<string, RenderLayer> = {
  surfaceField: { label: 'Surface Field', color: '#8be9fd', icon: '🎨' },
  slice: { label: 'Slice View', color: '#ff79c6', icon: '🔪' },
  meshPreview: { label: 'Mesh Preview', color: '#50fa7b', icon: '⬡' },
  streamline: { label: 'Streamlines', color: '#ffb86c', icon: '〰' },
  geometry: { label: 'Geometry', color: '#bd93f9', icon: '📦' },
}

export const RENDER_ORDER_DEFAULTS: Record<string, string[]> = {
  meshPreview: ['meshPreview', 'geometry'],
  postprocess: ['slice', 'surfaceField', 'meshPreview', 'streamline', 'geometry'],
}

/** Top chip wins, so the first key gets the HIGHEST renderOrder. */
export function baseOrder(index: number, total: number): number {
  return (total - 1 - index) * 50
}

interface Registries {
  renderRoots: THREE.Object3D[]
}

/**
 * The model-geometry roots the scene builder produced.
 *
 * Published on `window` by the viewport for the parity harness; read here
 * because the 'geometry' chip has to reach the SAME roots the legacy
 * `renderRoots` global names, and the mesh-preview panel has no other handle on
 * them.
 */
export function geometryRoots(): THREE.Object3D[] {
  const w = window as unknown as { __viewportRegistries?: Registries }
  return w.__viewportRegistries?.renderRoots ?? []
}

/**
 * Apply a mesh-preview chip order to the scene.
 *
 * Only the two layers that panel shows are handled; the post-processing panel
 * routes its five through the viewport's own `setRenderOrder`, which owns the
 * slice and streamline objects.
 */
export function applyMeshPreviewOrder(keys: readonly string[], preview: THREE.Object3D | null) {
  keys.forEach((key, i) => {
    const ro = baseOrder(i, keys.length)
    if (key === 'meshPreview') {
      preview?.traverse((o) => {
        const obj = o as THREE.Mesh & THREE.LineSegments
        if (obj.isMesh) o.renderOrder = ro
        if (obj.isLineSegments) o.renderOrder = ro + 1
      })
      return
    }
    if (key === 'geometry') {
      for (const root of geometryRoots()) {
        root.traverse((o) => {
          const obj = o as THREE.Mesh & THREE.LineSegments
          if (obj.isMesh || obj.isLineSegments) o.renderOrder = ro
        })
      }
    }
  })
}

/**
 * Move `key` to sit before or after `target` in the chip list.
 *
 * Pulled out of the drop handler so the reorder can be tested without a
 * DragEvent: the two error cases it protects against — dropping a chip on
 * itself, and splicing at an index measured BEFORE the chip was removed — both
 * produce a list that still has every chip in it and is silently in the wrong
 * order.
 */
export function reorderChips(order: readonly string[], key: string, target: string, before: boolean): string[] {
  if (key === target || !order.includes(key) || !order.includes(target)) return [...order]
  const next = order.filter((k) => k !== key)
  const at = next.indexOf(target)
  next.splice(before ? at : at + 1, 0, key)
  return next
}
