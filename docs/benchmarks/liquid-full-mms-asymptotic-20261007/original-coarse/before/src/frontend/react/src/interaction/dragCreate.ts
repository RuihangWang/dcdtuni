/**
 * Drag-to-place creation: press a toolbar button, drag, drop the model there.
 *
 * A port of `beginDragCreate` / `updateDragCreate` / `endDragCreate`
 * (STL_generate.html:17931). Without it every wall, partition and baffle is
 * born at the origin — often inside another model — and has to be dragged into
 * place afterwards, which is the same gesture done twice.
 *
 * The module is a bridge because the gesture STARTS in the creation toolbar and
 * FINISHES over the viewport canvas: the button knows the kind, the viewport
 * knows the camera and the built scene, and neither is a parent of the other.
 * `viewportBus` is the same arrangement for the view cube.
 *
 * The undo story is the legacy's: ONE entry. `addModel` takes the checkpoint as
 * it inserts the model, every frame after that mutates the live project, and
 * the release commits nothing more — so undoing a drag-create removes the model
 * rather than leaving it at the origin.
 */

import type { CfdProject, Construction } from '../schema/cfdproj'
import {
  clampToProjectBounds,
  dragCreatePlacement,
  dragCreateStarted,
  type ProjectBounds,
  type Vec3,
} from '../geometry/faceDragMath'
import { beginDragCreatedModel, cancelDragCreatedModel } from '../geometry/faceDragActions'
import * as store from '../projectStore'
import { quantize } from '../legacy/transformTools'

/** The kinds the legacy wires the gesture to (STL_generate.html:12348). */
export const DRAG_CREATE_KINDS = new Set(['wall', 'partitionWall', 'baffle'])

export interface DragCreateBridge {
  /** Where a pointer is over the floor plane, in world coordinates. */
  groundPointAt(event: { clientX: number; clientY: number }): Vec3 | null
  /** Move the rendered group to the model's anchor, without a rebuild. */
  syncRenderTransform(uid: string): void
  /** Rebuild the scene after the drag settles. */
  rebuildNow(uids?: string[]): void
  /** The toolbar's snap toggles. */
  snap(): { face: boolean; edge: boolean; grid: boolean }
  /** The snap offset the shared transform tools compute for one model. */
  snapDelta(uid: string, grid: boolean, face: boolean, edge: boolean): Vec3
}

let bridge: DragCreateBridge | null = null

/** Registered by the viewport, which owns the camera and the built scene. */
export function setDragCreateBridge(next: DragCreateBridge | null) {
  bridge = next
}

interface DragCreateState {
  kind: string
  startX: number
  startY: number
  started: boolean
  uid: string | null
}

let state: DragCreateState | null = null
/**
 * Set when a drag actually created a model, and read by the button's own click.
 *
 * A press that travels far enough to create a model but is released back over
 * the button still fires a click, and that click would create a SECOND model.
 * The legacy guards on its drag state, which it has already cleared by then
 * (STL_generate.html:13760), so the second model appears there.
 */
let consumedClick = false

export interface DragCreateContext {
  project(): CfdProject | null
  /** Called once, when the model comes into being — the app selects it. */
  onCreated(uid: string): void
  /** The message shown when the model cannot fit inside the project bounds. */
  cannotFitMessage: string
}

function liveModel(uid: string): Construction | null {
  const project = store.getProject()
  if (!project) return null
  return store.findConstruction(project, uid)?.model ?? null
}

function boundsOf(project: CfdProject | null): ProjectBounds | undefined {
  return (project?.geometry as { bounds?: ProjectBounds } | undefined)?.bounds
}

function sizeOf(model: Construction): Vec3 {
  const size = (model.geometry as { size?: Partial<Vec3> } | undefined)?.size ?? {}
  return { x: Number(size.x) || 0, y: Number(size.y) || 0, z: Number(size.z) || 0 }
}

function writeLocation(model: Construction, location: Vec3) {
  store.mutateLive(() => {
    const geometry = (model.geometry ?? (model.geometry = {})) as Record<string, unknown>
    geometry.location = { x: location.x, y: location.y, z: location.z }
  })
}

/** True while a drag-create is in flight — the toolbar's click must stand down. */
export function dragCreateInFlight(): boolean {
  return state !== null
}

/**
 * Whether the click that follows this pointer-up was already answered by a drag.
 *
 * Reading it CLEARS it: it exists for exactly one click.
 */
export function takeDragCreateClick(): boolean {
  const value = consumedClick
  consumedClick = false
  return value
}

export function beginDragCreate(event: PointerEvent, kind: string, context: DragCreateContext) {
  if (event.button !== 0 || !DRAG_CREATE_KINDS.has(kind)) return
  if (state) return
  state = { kind, startX: event.clientX, startY: event.clientY, started: false, uid: null }

  const onMove = (move: PointerEvent) => {
    if (!state) return
    if (!state.started) {
      if (!dragCreateStarted(move.clientX - state.startX, move.clientY - state.startY)) return
      const project = context.project()
      if (!project) return
      const created = beginDragCreatedModel(project, state.kind)
      if (!created) return
      state.started = true
      state.uid = created.uid
      consumedClick = true
      context.onCreated(created.uid)
    }
    if (!state.uid) return
    const model = liveModel(state.uid)
    const point = bridge?.groundPointAt(move)
    if (!model || !point) return
    writeLocation(model, dragCreatePlacement(point, sizeOf(model), boundsOf(store.getProject())))
    bridge?.syncRenderTransform(state.uid)
  }

  const onUp = () => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    const finished = state
    state = null
    if (!finished?.started || !finished.uid) return
    const model = liveModel(finished.uid)
    if (!model) return

    const snap = bridge?.snap() ?? { face: false, edge: false, grid: false }
    const geometry = model.geometry as { location?: Vec3 }
    let location: Vec3 = { ...(geometry.location as Vec3) }
    if (snap.face || snap.edge || snap.grid) {
      const delta = bridge?.snapDelta(finished.uid, snap.grid, snap.face, snap.edge) ?? {
        x: 0,
        y: 0,
        z: 0,
      }
      location = { x: location.x + delta.x, y: location.y + delta.y, z: location.z + delta.z }
    }
    // Micrometre grid, like the move gizmo: the drop leaves the same clean
    // number a snap would, so the export never sees float dust.
    location = { x: quantize(location.x), y: quantize(location.y), z: quantize(location.z) }
    const clamped = clampToProjectBounds(location, sizeOf(model), boundsOf(store.getProject()))
    if (!clamped) {
      // The model cannot fit between an enabled Min and Max at all, so it is
      // taken back out rather than dropped somewhere it does not belong.
      cancelDragCreatedModel()
      bridge?.rebuildNow()
      window.alert(context.cannotFitMessage)
      return
    }
    writeLocation(model, clamped)
    store.notifyLiveEdit('dragCreate', `${finished.kind} → ${clamped.x.toFixed(3)}, ${clamped.z.toFixed(3)}`)
    bridge?.rebuildNow([model.uid])
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
