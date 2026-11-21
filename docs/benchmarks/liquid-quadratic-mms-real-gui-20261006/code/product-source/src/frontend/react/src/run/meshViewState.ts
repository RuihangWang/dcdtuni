/**
 * View-only mesh-tab state the panel and the viewport both need.
 *
 * The drag-axis checkboxes live in the mesh panel and are read by the seed
 * drag in the viewport; the grid preview is toggled from the panel and owned by
 * the viewport. Neither belongs in the .cfdproj — a checkbox that constrains a
 * drag is not part of the case — so they sit here rather than in `projectStore`,
 * the same split `ppViewState` makes for post-processing.
 */

import type { MeshVisuals } from './meshVisuals'

export interface DragAxes {
  x: boolean
  y: boolean
  z: boolean
}

type Listener = () => void

const listeners = new Set<Listener>()
let axes: DragAxes = { x: true, y: true, z: true }
let visuals: MeshVisuals | null = null

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribeMeshView(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getDragAxes(): DragAxes {
  return axes
}

export function setDragAxis(axis: keyof DragAxes, on: boolean) {
  axes = { ...axes, [axis]: on }
  emit()
}

/** Published by the viewport; read by the panel's grid-preview button. */
export function setMeshVisuals(next: MeshVisuals | null) {
  visuals = next
  emit()
}

export function getMeshVisuals(): MeshVisuals | null {
  return visuals
}
