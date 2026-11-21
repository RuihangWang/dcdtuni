/**
 * The two VIEW-level fades of the model geometry, and the one routine that
 * puts them back after a rebuild.
 *
 * - **The mesh preview's geometry fade** (网格预览 → 几何透明度, 30% by
 *   default). Up only while a mesh is drawn on the mesh tab; it dims every
 *   mesh of every model, servers and blanking panels included, so nothing of
 *   the geometry sits solid on the mesh patches.
 * - **Post-processing's 3D模型透明度.** The whole geometry behind a result.
 *
 * Neither is project state: both are written onto the BUILT materials
 * (`applyOpacityToMeshes`, `applyMaterialOpacity`). So a rebuild — a texture
 * landing, an edit, and above all the targeted rebuild a RACK SELECTION does
 * for its slot guides — hands back fresh materials at full opacity with depth
 * writes on, and the fade is simply gone for whatever was rebuilt. Measured on
 * Tri_angle with the preview up: one click on a mesh patch in front of
 * Default_Rack_1 rebuilt the rack with 32 solid, depth-writing materials, and
 * the rack z-fought with the patches coincident with it.
 *
 * The legacy editor does not have this problem because its mesh-preview fade
 * is WRITTEN INTO the model state (`mpSetGeoOpacity`, STL_generate.html:26544)
 * and every builder reads it back. Here the fades stay render-only — the file
 * must not change because someone looked at a mesh — so the scene builder
 * calls `reapplyViewFades` on every root it builds (`SceneHost.afterBuild`).
 */

import type * as THREE from 'three'
import { applyOpacityToMeshes,notifyRenderedOpacity } from './modelOpacity'

export interface ViewFades {
  /** Post-processing's 3D-model opacity, 0..1; 1 when off. */
  model: number
  /** The mesh preview's geometry opacity, 0..1; 1 when no preview is shown. */
  preview: number
}

export const NO_VIEW_FADES: Readonly<ViewFades> = Object.freeze({ model: 1, preview: 1 })

/** Below this a fade is "up". The sliders never leave a value in between. */
const OPAQUE = 0.999

/** Whether either fade is up, i.e. whether a rebuild has anything to restore. */
export function viewFadeActive(fades: ViewFades): boolean {
  return fades.model < OPAQUE || fades.preview < OPAQUE
}

/**
 * The opacity the fades show the geometry at: the preview's while a mesh is
 * shown, else post-processing's, else 1. The ceiling for anything painted over
 * the geometry, such as the selection highlight.
 */
export function viewFadeOpacity(fades: ViewFades): number {
  if (fades.preview < OPAQUE) return fades.preview
  if (fades.model < OPAQUE) return fades.model
  return 1
}

/**
 * Write one opacity onto every material under a root, in place.
 *
 * The post-processing fade, which has always gone through the materials
 * directly: the builders cache them by kind, so one write covers every mesh
 * sharing one — and every mesh sharing one is faded by the same pass anyway.
 * `depthWrite` follows the opacity: a transparent surface that still writes
 * depth hides whatever is behind it, which reads as the slider doing nothing.
 *
 * `layer` restricts it to one post-processing layer, for the field-opacity
 * slider that must not touch the slice or the streamlines.
 */
export function applyMaterialOpacity(
  root: THREE.Object3D | null | undefined,
  value: number,
  layer?: string,
): void {
  if (!root) return
  root.traverse((o) => {
    if (layer && (o.userData as { ppLayer?: string }).ppLayer !== layer) return
    const mesh = o as THREE.Mesh
    const mats = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []
    for (const m of mats) {
      m.opacity = value
      m.transparent = value < OPAQUE
      m.depthWrite = value >= OPAQUE
      m.needsUpdate = true
    }
  })
  if(!layer)notifyRenderedOpacity(root)
}

/**
 * Put the view fades back on freshly built model roots.
 *
 * Each fade goes back onto exactly what its own slider fades, no more:
 *
 * - the post-processing fade onto `scene` when one is given — its slider fades
 *   the whole scene group, supply streams and pipes included — and otherwise
 *   onto the roots;
 * - the mesh preview's onto the model ROOTS only, as `__setGeometryOpacity`
 *   does: the mesh tab's own helpers (the locationInMesh sphere, the
 *   refinement boxes) hang under the same scene group and must stay solid.
 *
 * The preview's goes LAST, so while a mesh is shown the preview decides the
 * geometry's look — the order the full rebuild has always used. The preview
 * group itself is skipped: it carries its own opacity, and fading it would
 * fade the mesh along with what it is drawn over. With no fade up this
 * touches nothing.
 */
export function reapplyViewFades(
  roots: Iterable<THREE.Object3D | null | undefined>,
  fades: ViewFades,
  scene?: THREE.Object3D | null,
): void {
  if (!viewFadeActive(fades)) return
  const targets: THREE.Object3D[] = []
  for (const root of roots) {
    if (root && root.name !== 'meshPreview') targets.push(root)
  }
  if (fades.model < OPAQUE) {
    if (scene) applyMaterialOpacity(scene, fades.model)
    else for (const root of targets) applyMaterialOpacity(root, fades.model)
  }
  if (fades.preview < OPAQUE) {
    for (const root of targets) {
      applyOpacityToMeshes(root, fades.preview * 100, { includeEquipment: true })
    }
  }
}

/**
 * The fades again after a WHOLE-scene refresh — a rebuild whose `afterBuild`
 * already re-faded the model roots, followed by the supply streams and pipes
 * that are rebuilt outside the builder.
 *
 * Only what `afterBuild` cannot reach goes back on: the post-processing fade
 * onto the scene group, whose slider fades the supply streams too. The preview
 * pass is repeated only when BOTH fades are up, so the preview still has the
 * last word on the roots; with the preview alone up the roots are already
 * right, and a second pass over every root would be the same work twice.
 */
export function reapplySceneFades(
  roots: Iterable<THREE.Object3D | null | undefined>,
  fades: ViewFades,
  scene: THREE.Object3D | null | undefined,
): void {
  if (fades.model >= OPAQUE) return
  if (scene) applyMaterialOpacity(scene, fades.model)
  if (fades.preview < OPAQUE) {
    for (const root of roots) {
      if (root && root.name !== 'meshPreview') {
        applyOpacityToMeshes(root, fades.preview * 100, { includeEquipment: true })
      }
    }
  }
}

/** What the fade controller needs from the viewport. */
export interface ViewFadeHost {
  /** The model roots the preview fade goes onto (the preview group is skipped). */
  roots(): Iterable<THREE.Object3D | null | undefined>
  /** The scene group the post-processing fade covers, supply streams included. */
  scene(): THREE.Object3D | null | undefined
  /**
   * Rebuild every model from the project. The only way back to what each
   * model looked like before a fade: a fade overwrites every material with one
   * number, and a model's OWN opacity (a containment baffle at 40%, a face's
   * `wallOpacities`) is known only to the builders that read the project.
   */
  rebuild(): void
  /** Repaint the selection highlight over the fade that is now up. */
  repaint(): void
}

export interface ViewFadeController {
  /** Both fades as they stand now — a copy. */
  now(): ViewFades
  /** The mesh preview's geometry fade, 0..1 (1 = no preview drawn). */
  setPreview(value: number): void
  /** Post-processing's 3D-model fade, 0..1 (1 = off). */
  setModel(value: number): void
}

/**
 * The two fades' values, and what a change of either one does to the scene.
 *
 * Three rules, each the fix for something measured:
 *
 * - **A fade that lands repaints the highlight.** The highlight caps a solid
 *   paint at the fade (`paintMeshHighlight`), but it only ran when the
 *   SELECTION changed. Select a U-slot on the geometry tab, then open the mesh
 *   tab with a preview loaded: the fade landed, the highlight did not re-run,
 *   and the slot guide — which keeps its own opacity through a fade — stayed a
 *   0.9 blue slab 0.8 mm in front of the mesh patch.
 * - **A fade that goes back to 1 REBUILDS, it does not write 1.** Writing 1
 *   onto every material turned a model the user had faded themselves fully
 *   opaque, and put a highlight's captured material (the faded clone) back at
 *   30% on the geometry tab. The legacy saves the opacities and rebuilds on
 *   close (`mpClose` -> `doRebuildGeometry`, STL_generate.html:26518-26525).
 * - **Nothing to undo, nothing done.** Hiding a preview that was never faded
 *   (a tab switch with no mesh loaded) must not rebuild the room.
 */
export function createViewFadeController(host: ViewFadeHost): ViewFadeController {
  const fades: ViewFades = { model: 1, preview: 1 }

  function previewPass(value: number) {
    for (const root of host.roots()) {
      if (root && root.name !== 'meshPreview') {
        applyOpacityToMeshes(root, value * 100, { includeEquipment: true })
      }
    }
  }

  return {
    now: () => ({ ...fades }),
    setPreview(value: number) {
      const was = fades.preview
      fades.preview = value
      if (value >= OPAQUE) {
        if (was < OPAQUE) host.rebuild()
        return
      }
      previewPass(value)
      host.repaint()
    },
    setModel(value: number) {
      const was = fades.model
      fades.model = value
      if (value >= OPAQUE) {
        if (was < OPAQUE) host.rebuild()
        return
      }
      applyMaterialOpacity(host.scene(), value)
      // While a mesh is shown the preview decides the geometry's look.
      if (fades.preview < OPAQUE) previewPass(fades.preview)
      host.repaint()
    },
  }
}
