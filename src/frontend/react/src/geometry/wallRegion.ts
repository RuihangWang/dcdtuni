/**
 * A wall REGION: the part of a face left over once its patches are cut out.
 *
 * A wall with three vents in it exports four solids — the three vents and the
 * sheet around them — and that sheet is what the tree calls a region. It is a
 * real OpenFOAM patch with its own name and its own opacity, and until now
 * selecting one in the tree fell through to the whole model's panel, so there
 * was no way to fade one region of a floor or to see which solid it becomes.
 *
 * Ported from `renderWallRegionInspector` (STL_generate.html:10602),
 * `ensureWallName` (:4756), `ensureWallOpacity` (`js/geometry_pure.js:258`),
 * `updateWallName` (:13605) and the `previewWallOpacity`/`updateWallOpacity`
 * pair (:13581, :13592).
 */

import type { Construction } from '../schema/cfdproj'
import { stlNaming } from '../legacy/stlNaming'
import {
  applyOpacityToMeshes,
  clampOpacityPercent,
  opacityToPercent,
  percentToOpacity,
  renderRootOf,
} from './modelOpacity'
import * as store from '../projectStore'
import { wallRegionNames } from '../viewportBus'

type Bag = Record<string, unknown>

interface FaceState {
  displayName?: string
  label?: string
  opacity?: number
  prefixEnabled?: boolean
  wallNames?: (string | undefined)[]
  wallOpacities?: number[]
  patches?: { id?: string }[]
}

export function faceOf(model: Construction | null | undefined, faceId: string): FaceState | null {
  const faces = (model?.geometry as Bag | undefined)?.faces
  if (!faces || typeof faces !== 'object' || Array.isArray(faces)) return null
  return ((faces as Record<string, FaceState>)[faceId] ?? null) as FaceState | null
}

/** The face a sheet kind's regions live on: `top` for a floor, `front` for the rest. */
export function primaryFaceId(model: Construction): string {
  return model.kind === 'floor' ? 'top' : 'front'
}

/**
 * The region's editable name, defaulted the way `ensureWallName` does.
 *
 * Read-only here: materialising the default into the project on a mere READ is
 * what makes an untouched project come back from a save with `wallNames` filled
 * in, and the legacy does exactly that. The write path below stores it.
 */
export function wallRegionName(
  model: Construction | null | undefined,
  faceId: string,
  index: number,
): string {
  const stored = faceOf(model, faceId)?.wallNames?.[index]
  return stored && String(stored).length > 0 ? String(stored) : `wall_${index + 1}`
}

/** The region's opacity, 0-100, falling back to the face's and then to opaque. */
export function wallRegionOpacityPercent(
  model: Construction | null | undefined,
  faceId: string,
  index: number,
): number {
  const face = faceOf(model, faceId)
  const stored = face?.wallOpacities?.[index]
  if (typeof stored === 'number' && Number.isFinite(stored)) return opacityToPercent(stored)
  return opacityToPercent(face?.opacity)
}

/** How many regions a face is showing, the way the opacity cascade counts them. */
export function wallRegionCount(
  model: Construction | null | undefined,
  faceId: string,
): number {
  const face = faceOf(model, faceId)
  return Math.max(face?.wallNames?.length ?? 0, face?.wallOpacities?.length ?? 0, 1)
}

/**
 * The `solid <name>` this region is written into the STL as.
 *
 * Taken from the BUILT mesh where the scene has one, because that string is the
 * OpenFOAM patch name and there is nowhere else it exists; the shared namer is
 * the fallback for a region not currently on screen.
 *
 * It is NOT `wallNames[index]`, and it does not change when the name above is
 * edited. The legacy panel shows `composeFaceScopedExportName` here
 * (STL_generate.html:10606), which stopped being the exported name when the
 * builders moved to `composeModelScopedExportName(..., {isWallPart})`
 * (`js/geometry_scene.js:1862`) — so that field has been showing a string no
 * exported file contains. Showing the real one instead: a name that claims to
 * be the final solid name and is not is how a boundary condition gets set on
 * the wrong surface.
 */
export function wallRegionSolidName(
  model: Construction | null | undefined,
  faceId: string,
  index: number,
): string {
  if (!model) return ''
  const built = wallRegionNames(model.uid, faceId)[index]
  if (built) return built
  const face = faceOf(model, faceId) ?? {}
  try {
    return stlNaming().composeModelScopedExportName(model, faceId, face, {
      isWallPart: true,
      partIndex: index,
      partCount: Math.max(1, face.wallNames?.length ?? 1),
    })
  } catch {
    return `${model.meta?.name ?? model.uid}_part${index + 1}`
  }
}

function writableFace(draft: Construction | undefined, faceId: string): FaceState | null {
  const faces = (draft?.geometry as Bag | undefined)?.faces
  if (!faces || typeof faces !== 'object' || Array.isArray(faces)) return null
  return ((faces as Record<string, FaceState>)[faceId] ?? null) as FaceState | null
}

/**
 * Rename a region.
 *
 * A blank name falls back to `wall_<n+1>` rather than being stored empty, as
 * the legacy does (`value || \`wall_${wallIndex + 1}\``) — an unnamed region
 * has nothing to show in the tree.
 */
export function setWallRegionName(uid: string, faceId: string, index: number, value: string) {
  const next = value.trim() || `wall_${index + 1}`
  store.edit('updateWallName', `${uid}/${faceId}[${index}] -> ${next}`, (draft) => {
    const face = writableFace(store.findConstruction(draft, uid)?.model, faceId)
    if (!face) return
    const names = face.wallNames ?? (face.wallNames = [])
    names[index] = next
  })
}

/** Write one region's opacity onto a face IN PLACE. Returns false for a non-number. */
export function applyWallRegionOpacityState(
  model: Construction,
  faceId: string,
  index: number,
  percent: number,
): boolean {
  const next = percentToOpacity(percent)
  if (!Number.isFinite(next)) return false
  const face = writableFace(model, faceId)
  if (!face) return false
  const list = face.wallOpacities ?? (face.wallOpacities = [])
  // Holes in the array would serialise as `null` and read back as "not
  // finite", so earlier regions are filled with the face's own opacity rather
  // than left undefined.
  const fill = Number.isFinite(face.opacity) ? (face.opacity as number) : 1
  for (let i = 0; i < index; i++) if (!Number.isFinite(list[i])) list[i] = fill
  list[index] = next
  return true
}

/**
 * Whether the current drag has already taken its checkpoint.
 *
 * Module-level for the same reason as `modelOpacity`'s: the slider remounts
 * when the value changes underneath it, so a flag held in the component would
 * reset mid-drag and every frame would push its own undo entry.
 */
let previewCheckpointPushed = false

/** Test seam — a leaked flag would make the next drag skip its checkpoint. */
export function resetWallOpacityPreview(): void {
  previewCheckpointPushed = false
}

export function wallOpacityPreviewPending(): boolean {
  return previewCheckpointPushed
}

/**
 * `oninput`: fade now, commit later.
 *
 * The whole model's meshes are faded during the drag, not just this region's.
 * That is what the legacy preview does too (`_applyOpacityToMeshes(modelId,…)`,
 * STL_generate.html:13592) — the per-region value only takes effect on the
 * rebuild that follows the commit.
 */
export function previewWallRegionOpacity(
  uid: string,
  faceId: string,
  index: number,
  percent: number,
): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.pushUndoCheckpoint('opacityChange')
    previewCheckpointPushed = true
  }
  // Re-read AFTER the checkpoint: it swaps the live project for a clone, so a
  // model captured before it belongs to the undo snapshot.
  const project = store.getProject()
  const hit = project ? store.findConstruction(project, uid) : null
  if (!hit) return
  store.mutateLive(() => {
    applyWallRegionOpacityState(hit.model, faceId, index, percent)
  }, { scene: false })
  applyOpacityToMeshes(renderRootOf(uid), percent)
}

/** `onchange`: end the drag and log it as one operation. */
export function commitWallRegionOpacity(
  uid: string,
  faceId: string,
  index: number,
  percent: number,
): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  const detail = `${uid}/${faceId}[${index}] ${clampOpacityPercent(percent)}%`
  if (!previewCheckpointPushed) {
    // No preview ran — a programmatic set, or a browser that fires `change`
    // alone. Nothing is written yet, so this is a plain undoable edit.
    store.edit('updateWallOpacity', detail, (draft) => {
      const model = store.findConstruction(draft, uid)?.model
      if (model) applyWallRegionOpacityState(model, faceId, index, percent)
    })
    return
  }
  previewCheckpointPushed = false
  const project = store.getProject()
  const hit = project ? store.findConstruction(project, uid) : null
  if (hit) {
    store.mutateLive(() => {
      applyWallRegionOpacityState(hit.model, faceId, index, percent)
    }, { scene: false })
  }
  store.notifyLiveEdit('updateWallOpacity', detail)
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: these are
// driven by a panel in the right-hand inspector, and reading back what a rename
// or a fade actually wrote is otherwise a matter of saving the file and
// diffing it.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __wallRegion: unknown }).__wallRegion = {
    wallRegionName,
    wallRegionOpacityPercent,
    wallRegionSolidName,
    wallRegionCount,
    setWallRegionName,
    previewWallRegionOpacity,
    commitWallRegionOpacity,
  }
}
