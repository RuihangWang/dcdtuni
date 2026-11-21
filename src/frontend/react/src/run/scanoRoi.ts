import * as THREE from 'three'
import { buildAiBox } from '../postprocess/ppRenderer'
import { attachAiBoxDrag } from '../postprocess/ppViewportDrag'
import type { AiBoxBounds, Vec3 } from '../postprocess/ppDragMath'
import { getSelection, subscribeSelection } from '../selectionBus'

/**
 * The region of an AI solve: the whole mesh, or a box. An ROI result holds values only inside its box,
 * post-processing shows only that region, and it cannot initialise a CFD solve.
 *
 * The box is drawn in the geometry viewport while the AI solve tab is open, and moved or resized there
 * (centre sphere / faces) or by its numbers.
 */

export interface ScanoRoi {
  enabled: boolean
  box: AiBoxBounds | null
}

let state: ScanoRoi = { enabled: false, box: null }
const listeners = new Set<() => void>()

export function getScanoRoi(): ScanoRoi {
  return state
}

export function subscribeScanoRoi(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function setScanoRoi(next: Partial<ScanoRoi>): void {
  const box = next.box !== undefined ? (next.box ? normaliseBox(next.box) : null) : state.box
  state = { ...state, ...next, box }
  listeners.forEach((fn) => fn())
  syncViewportBox()
}

/** The request body key of an AI solve: the box, or null for the whole mesh. */
export function scanoRoiRequest(): { min: Vec3; max: Vec3 } | null {
  return state.enabled && state.box ? { min: [...state.box.min], max: [...state.box.max] } : null
}

function normaliseBox(b: AiBoxBounds): AiBoxBounds {
  const min = [0, 1, 2].map((i) => Math.min(b.min[i]!, b.max[i]!)) as Vec3
  const max = [0, 1, 2].map((i) => Math.max(b.min[i]!, b.max[i]!)) as Vec3
  return { min, max }
}

interface Three {
  scene: THREE.Scene
}

function three(): Three | null {
  const t = (window as unknown as { __three?: Partial<Three> }).__three
  return t?.scene ? (t as Three) : null
}

// The solve tabs do not select geometry, so the box fits the object selected last (in the geometry tab).
let lastSelected: string | null = getSelection().uid
subscribeSelection(() => {
  const uid = getSelection().uid
  if (uid) {
    lastSelected = uid
    listeners.forEach((fn) => fn())
  }
})

/** The object "fit" uses: the current selection, else the one selected last; null when none. */
export function roiFitTarget(): string | null {
  return getSelection().uid ?? lastSelected
}

/** Bounds of the scene's models (all, or one model's meshes). */
export function modelBounds(uid?: string | null): AiBoxBounds | null {
  const t = three()
  if (!t) return null
  const box = new THREE.Box3()
  t.scene.traverse((o) => {
    const id = (o.userData as { modelId?: string }).modelId
    if (!id || (uid && id !== uid) || !(o as THREE.Mesh).isMesh || !o.visible) return
    box.expandByObject(o)
  })
  if (box.isEmpty()) return null
  return { min: box.min.toArray() as Vec3, max: box.max.toArray() as Vec3 }
}

/** A box over the middle half of the room's floor, full height: a first ROI the user then resizes. */
export function defaultRoiBox(): AiBoxBounds | null {
  const room = modelBounds()
  if (!room) return null
  const c = room.min.map((v, i) => (v + room.max[i]!) / 2)
  const h = room.min.map((v, i) => (room.max[i]! - v) / 4)
  return {
    min: c.map((v, i) => (i === 1 ? room.min[1]! : v - h[i]!)) as Vec3,
    max: c.map((v, i) => (i === 1 ? room.max[1]! : v + h[i]!)) as Vec3,
  }
}

let shown: { object: THREE.Object3D; dispose(): void } | null = null
let active = false
let detachDrag: (() => void) | null = null

function syncViewportBox(): void {
  const t = three()
  if (shown) {
    shown.object.parent?.remove(shown.object)
    shown.dispose()
    shown = null
  }
  if (!active || !t || !state.enabled || !state.box) return
  shown = buildAiBox(state.box.min, state.box.max)
  shown.object.userData.scanoRoi = true
  t.scene.add(shown.object)
}

/** Show the box in the viewport and make it draggable; returns the undo. */
export function showScanoRoiInViewport(): () => void {
  active = true
  syncViewportBox()
  detachDrag = attachAiBoxDrag({
    bounds: () => (state.enabled ? state.box : null),
    onCommit: (box) => setScanoRoi({ box }),
  })
  return () => {
    active = false
    detachDrag?.()
    detachDrag = null
    syncViewportBox()
  }
}
