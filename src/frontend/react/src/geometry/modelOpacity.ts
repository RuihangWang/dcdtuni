/**
 * Per-model opacity: the inspector slider, its live preview, and the commit.
 *
 * Ported from `applyModelOpacityState` / `previewModelOpacity` /
 * `updateModelOpacity` / `_applyOpacityToMeshes` (STL_generate.html:13444,
 * 13484, 13493, 13501). The point of the feature is being able to fade ONE
 * model — a containment baffle over the racks it encloses — which the
 * post-processing panel's global geometry fade cannot do.
 *
 * Three things carry the behaviour and are easy to lose:
 *
 * - **One undo entry per drag.** The slider fires on every pointer move. Each
 *   one through `edit()` would clone the project and push an undo entry, so
 *   fading a baffle to 15% would take eighty presses of Ctrl+Z to put back.
 *   The first preview takes ONE checkpoint and the rest mutate in place —
 *   the legacy's `opacityPreviewCheckpointPushed` flag, which is why that flag
 *   is reset by the commit and not by the preview.
 *
 * - **The value cascades into the faces.** The geometry builders read
 *   `face.opacity` and `face.wallOpacities[i]` per surface, not just
 *   `meta.display.opacity`, so writing only the model-level number leaves a
 *   rack's doors opaque over a faded frame.
 *
 * - **Materials are cloned before the preview writes to them.** The shared
 *   builder caches materials by `type|colour|opacity`, so setting `.opacity`
 *   on the one a rack happens to use fades EVERY rack that shares it. The
 *   legacy has that bleed and lives with it until the next full rebuild; here
 *   the preview owns its materials and only the model being dragged fades.
 */

import type * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'

/** Display consumers may react to live materials without publishing a project edit. */
const renderedOpacityListeners=new Set<(root:THREE.Object3D)=>void>()
export function subscribeRenderedOpacity(fn:(root:THREE.Object3D)=>void){
  renderedOpacityListeners.add(fn)
  return()=>{renderedOpacityListeners.delete(fn)}
}
export function notifyRenderedOpacity(root:THREE.Object3D){
  renderedOpacityListeners.forEach(fn=>fn(root))
}

/** The legacy clamp: percent in, percent out, NaN preserved so it can be rejected. */
export function clampOpacityPercent(value: number): number {
  return Math.min(100, Math.max(0, Number(value)))
}

export function opacityToPercent(value: number | undefined): number {
  return Math.round((Number.isFinite(value) ? (value as number) : 1) * 100)
}

export function percentToOpacity(value: number): number {
  return clampOpacityPercent(value) / 100
}

/** The slider's position for a model, 0-100. */
export function modelOpacityPercent(model: Construction | null | undefined): number {
  return opacityToPercent(model?.meta?.display?.opacity)
}

/**
 * Kinds whose faces follow the model-level opacity.
 *
 * Copied from the legacy predicate rather than "all kinds": a `custom` model's
 * faces are the imported STL's solids and carry their own per-region opacity,
 * which the model slider must not overwrite.
 */
const FACE_CASCADE_KINDS = new Set([
  'box', 'partitionWall', 'rack', 'acu', 'cdu', 'wall', 'baffle', 'floor',
  'crh', 'cold_plate', 'heat_source', 'pipe_segment', 'pipe_junction', 'pipe',
])

/** The subset that also has per-wall opacities — the sheet-like kinds. */
const WALL_CASCADE_KINDS = new Set([
  'box', 'partitionWall', 'acu', 'cdu', 'wall', 'baffle', 'floor',
])

interface FaceState {
  opacity?: number
  wallNames?: unknown[]
  wallOpacities?: number[]
}

/**
 * Write an opacity onto a model IN PLACE, cascading into its faces.
 *
 * Returns false for a value that is not a number, so a half-typed box cannot
 * write NaN into the project and make every surface vanish.
 */
export function applyModelOpacityState(model: Construction, percent: number): boolean {
  const next = percentToOpacity(percent)
  if (!Number.isFinite(next)) return false
  const meta = model.meta ?? (model.meta = { name: model.uid })
  // Mutated in place rather than spread into a new object: JSON.stringify
  // writes keys in insertion order, and rebuilding `display` would move
  // `showAnchorMarker` after `opacity` in every saved file for no reason.
  const display = meta.display ?? (meta.display = {})
  display.opacity = next

  const kind = String(model.kind ?? '')
  if (!FACE_CASCADE_KINDS.has(kind)) return true
  const faces = model.geometry?.faces as Record<string, FaceState> | undefined
  if (!faces) return true
  const walls = WALL_CASCADE_KINDS.has(kind)
  for (const face of Object.values(faces)) {
    if (!face) continue
    face.opacity = next
    if (!walls) continue
    // `wallNames` can be longer than `wallOpacities` on a face whose regions
    // were named but never faded, so the count is the longest of the two —
    // taking `wallOpacities.length` alone would leave later regions opaque.
    const count = Math.max(face.wallNames?.length ?? 0, face.wallOpacities?.length ?? 0, 1)
    const list = face.wallOpacities ?? (face.wallOpacities = [])
    for (let i = 0; i < count; i++) list[i] = next
  }
  return true
}

export type MeshLike = THREE.Mesh & { isMesh?: boolean; isLine?: boolean; isLineSegments?: boolean }

export interface OpacityUserData {
  isSeedHandle?: boolean
  isAnchorMarker?: boolean
  isRackSlotGuide?: boolean
  isProjectionGuide?: boolean
  /**
   * Set on every mesh a rack's SERVER builder spawns (geometry_scene.js's
   * `baseUserData`). Its presence marks the mesh as belonging to the server
   * opacity knob, not the model one — see the filter in `applyOpacityToMeshes`.
   */
  equipmentId?: string
  baseOpacity?: number
  _opacityOwned?: boolean
}

/**
 * Give a mesh its own materials before the preview writes to them.
 *
 * Once per mesh, flagged in userData: cloning per frame would allocate a
 * material sixty times a second for the length of the drag.
 *
 * Exported for `serverOpacity.ts`, which fades the complementary set of meshes
 * (ONLY the ones with an `equipmentId`) and must not drift on how a material
 * is claimed.
 */
export function ownMaterials(mesh: MeshLike): THREE.Material[] {
  const ud = (mesh.userData ?? (mesh.userData = {})) as OpacityUserData
  const current = mesh.material as THREE.Material | THREE.Material[] | undefined
  if (!current) return []
  if (!ud._opacityOwned) {
    mesh.material = Array.isArray(current) ? current.map((m) => m.clone()) : current.clone()
    ud._opacityOwned = true
  }
  const owned = mesh.material as THREE.Material | THREE.Material[]
  return Array.isArray(owned) ? owned : [owned]
}

/**
 * Write one opacity onto a mesh's (or line's) own materials.
 *
 * The fade NUMBERS live here so the two opacity knobs — the model's and the
 * rack servers' — cannot drift apart on what "40%" looks like:
 *
 * - a surface becomes transparent below 0.999 and stops writing depth, because
 *   a transparent surface that still writes depth hides whatever is behind it,
 *   which reads as the slider having no effect at all;
 * - edges fade FASTER than the surface (quadratic, capped at 0.3): a wall faded
 *   to 20% with full-strength black edges reads as a black grid, not as a
 *   translucent wall. The legacy curve, copied exactly.
 */
export function fadeMeshMaterials(mesh: MeshLike, opacity: number): void {
  const ud = (mesh.userData ?? {}) as OpacityUserData
  if (mesh.isMesh && mesh.material) {
    for (const m of ownMaterials(mesh)) {
      // Only `transparent` is part of the shader program (WebGLPrograms'
      // `opaque`); opacity is a uniform and depth write a draw state. Flagging
      // every material on every slider step re-derived ~1,100 programs a frame
      // after a select-all (Shinaide_Liquid: 55 ms frames while dragging).
      const transparent = opacity < 0.999
      if (m.transparent !== transparent) m.needsUpdate = true
      m.opacity = opacity
      m.transparent = transparent
      m.depthWrite = opacity >= 0.999
      // Three draws a transparent DoubleSide material in TWO passes (back
      // faces, then front) unless told not to. A room's geometry is ~2,700
      // double-sided meshes, so the fade under the mesh preview doubled their
      // draw calls: 33 → 9 FPS on an RTX 4090 with STDCT (2026-09-22). One
      // pass is enough for a dimmed model — the two-pass order only matters
      // for a surface whose own back faces must sort behind its front.
      m.forceSinglePass = true
    }
    if (typeof ud.baseOpacity === 'number') ud.baseOpacity = opacity
    return
  }
  if ((mesh.isLine || mesh.isLineSegments) && mesh.material) {
    let edge = opacity < 0.05 ? 0 : Math.min(0.3, opacity * opacity * 0.4)
    if (edge < 0.02) edge = 0
    for (const m of ownMaterials(mesh)) {
      if (!m.transparent) m.needsUpdate = true
      m.opacity = edge
      m.transparent = true
      m.depthWrite = false
    }
    // Below EDGE_VISIBLE_MIN the lines are invisible to the eye but still one
    // draw call each — and there are more line objects than meshes. At the
    // preview's default 30% fade the curve gives 0.036, and drawing those
    // 3,149 invisible lines cost the other half of the frame (23 → 60 FPS
    // once they were skipped).
    mesh.visible = edge >= EDGE_VISIBLE_MIN
  }
}

/** Edge alpha under which a faded model's outline is not drawn at all. */
export const EDGE_VISIBLE_MIN = 0.05

/**
 * The live half of the slider: fade a model's meshes without rebuilding it.
 *
 * A geometry rebuild per pointer move is what this avoids — on a 200-rack room
 * that is seconds of work for one drag. The committed value is read back by the
 * builders on the next rebuild, so this only has to look right until then.
 */
export function applyOpacityToMeshes(
  root: THREE.Object3D | null,
  percent: number,
  opts: { includeEquipment?: boolean } = {},
): void {
  const opacity = percentToOpacity(percent)
  if (!root || !Number.isFinite(opacity)) return
  root.traverse((child) => {
    const mesh = child as MeshLike
    const ud = (mesh.userData ?? {}) as OpacityUserData
    // The seed handle and the anchor marker are HELPERS parented under the
    // model; fading them with it would hide the very thing the user is
    // dragging.
    if (ud.isSeedHandle || ud.isAnchorMarker) return
    // A rack's servers belong to the SERVER opacity knob (spec §5.1), not to
    // this one: every model-root fade — one model or a multi-select group —
    // skips meshes the server builder tagged with an `equipmentId`, so fading
    // the rack body no longer drags the servers with it. `serverOpacity.ts`
    // fades exactly the complementary set. The MESH PREVIEW fade is the one
    // exception (`includeEquipment`): it dims the whole scene under the mesh,
    // servers and blanking panels included — at 0% nothing of the geometry
    // may remain, or the server fronts sit on the mesh patches and blur them.
    // The legacy's mpSetGeoOpacity fades every mesh of every model the same way.
    if (ud.equipmentId && !opts.includeEquipment) return
    if (mesh.isMesh && mesh.material) {
      if (ud.isRackSlotGuide || ud.isProjectionGuide) {
        // Guides keep their own opacity and simply disappear once the model is
        // effectively invisible — a guide floating over nothing is worse than
        // no guide.
        const mats = ownMaterials(mesh)
        for (const m of mats) {
          if (opacity < 0.01) m.opacity = 0
        }
        mesh.visible = opacity >= 0.01
        return
      }
      fadeMeshMaterials(mesh, opacity)
      return
    }
    fadeMeshMaterials(mesh, opacity)
  })
  notifyRenderedOpacity(root)
}

/**
 * How the preview reaches the meshes it has to fade.
 *
 * The slider is in the right panel and the scene graph is inside the viewport,
 * two React subtrees that share no ancestor holding either. Registered by the
 * viewport rather than passed down, the same seam `viewportBus` uses for the
 * camera.
 */
export type RenderRootLookup = (uid: string) => THREE.Object3D | null

let rootLookup: RenderRootLookup | null = null

export function setRenderRootLookup(fn: RenderRootLookup | null): void {
  rootLookup = fn
}

export function renderRootOf(uid: string): THREE.Object3D | null {
  return rootLookup?.(uid) ?? null
}

/**
 * Whether the current drag has already taken its undo checkpoint.
 *
 * Module-level, like the legacy's `opacityPreviewCheckpointPushed`: the slider
 * is remounted when the value changes underneath it, so a flag held in the
 * component would reset mid-drag and every frame would push its own entry.
 */
let previewCheckpointPushed = false

/** Test seam — a leaked flag would make the next drag skip its checkpoint. */
export function resetOpacityPreview(): void {
  previewCheckpointPushed = false
}

export function opacityPreviewPending(): boolean {
  return previewCheckpointPushed
}

/**
 * The slider's position for a multi-selection: the MEAN of its members.
 *
 * The legacy `avgOpacity` (STL_generate.html:11011). A mean rather than the
 * first model's value, so a group where one baffle is already faded does not
 * read as if the whole group were.
 */
export function averageOpacityPercent(models: readonly Construction[]): number {
  if (!models.length) return 100
  const total = models.reduce((sum, m) => sum + (m.meta?.display?.opacity ?? 1), 0)
  return Math.round((total / models.length) * 100)
}

/** `oninput`: fade now, commit later. */
export function previewModelOpacity(uid: string, percent: number): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.pushUndoCheckpoint('opacityChange')
    previewCheckpointPushed = true
  }
  // Re-read AFTER the checkpoint: it replaces the live project with a clone,
  // so a model captured before it belongs to the undo snapshot and writing to
  // it would change what undo restores instead of what is on screen.
  const project = store.getProject()
  if (!project) return
  const hit = store.findConstruction(project, uid)
  if (!hit) return
  store.mutateLive(() => {
    applyModelOpacityState(hit.model, percent)
  }, { scene: false })
  applyOpacityToMeshes(renderRootOf(uid), percent)
}

/** `onchange`: end the drag and log it as one operation. */
export function commitModelOpacity(uid: string, percent: number): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    // No preview ran — a programmatic set, or a browser that fires `change`
    // alone. Nothing has been written yet, so this is a plain undoable edit.
    store.edit('updateModelOpacity', `${uid} ${clampOpacityPercent(percent)}%`, (draft) => {
      const hit = store.findConstruction(draft, uid)
      if (hit) applyModelOpacityState(hit.model, percent)
    }, { sceneUids: [uid] })
    return
  }
  previewCheckpointPushed = false
  const project = store.getProject()
  const hit = project ? store.findConstruction(project, uid) : null
  if (hit) {
    store.mutateLive(() => {
      applyModelOpacityState(hit.model, percent)
    }, { scene: false })
  }
  // The checkpoint above the drag already holds the pre-drag project, so this
  // only marks it dirty and re-renders — which is what rebuilds the scene from
  // the committed value and puts the shared materials back.
  store.notifyLiveEdit('updateModelOpacity', `${uid} ${clampOpacityPercent(percent)}%`, { sceneUids: [uid] })
}

/**
 * The same two calls for a multi-selection — the legacy's group slider
 * (`previewMultiOpacity` / `updateMultiOpacity`, STL_generate.html:13538).
 *
 * One checkpoint for the WHOLE group, not one per model: eight `edit()` calls
 * would be eight undo entries for one drag of one slider.
 */
export function previewMultiOpacity(uids: readonly string[], percent: number): void {
  if (!uids.length || !Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.pushUndoCheckpoint('multiOpacityChange')
    previewCheckpointPushed = true
  }
  const project = store.getProject()
  if (!project) return
  for (const uid of uids) {
    const hit = store.findConstruction(project, uid)
    if (!hit) continue
    store.mutateLive(() => {
      applyModelOpacityState(hit.model, percent)
    }, { scene: false })
    applyOpacityToMeshes(renderRootOf(uid), percent)
  }
}

export function commitMultiOpacity(uids: readonly string[], percent: number): void {
  if (!uids.length || !Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.edit('updateMultiOpacity', `${uids.length} models ${clampOpacityPercent(percent)}%`, (draft) => {
      for (const uid of uids) {
        const hit = store.findConstruction(draft, uid)
        if (hit) applyModelOpacityState(hit.model, percent)
      }
    }, { sceneUids: uids })
    return
  }
  previewCheckpointPushed = false
  const project = store.getProject()
  if (project) {
    for (const uid of uids) {
      const hit = store.findConstruction(project, uid)
      if (!hit) continue
      store.mutateLive(() => {
        applyModelOpacityState(hit.model, percent)
      }, { scene: false })
    }
  }
  store.notifyLiveEdit('updateMultiOpacity', `${uids.length} models ${clampOpacityPercent(percent)}%`, { sceneUids: uids })
}
