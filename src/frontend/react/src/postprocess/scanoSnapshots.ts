/** AI results kept for side-by-side comparison: each keeps the mesh it was drawn on, its surface values, slices
 * and volume, so results before and after a patch update (another mesh) compare as well as real-time updates. */

import type { ColorMap } from './ppColormaps'
import type { DecodedMesh, SlicePayload } from './index'
import type { VolumePayload, VolumeThreshold } from './ppVolume'

export interface SnapshotSlice {
  label: string
  payload: SlicePayload
  colorMap: ColorMap
}

export interface SnapshotVolume {
  /** Never written after it is drawn (a new result brings a new payload), so it is kept by reference. */
  payload: VolumePayload
  colorMap: ColorMap
  clim: [number, number]
  opacity: number
  resolution: number
  threshold: VolumeThreshold | null
}

export interface ScanoSnapshot {
  id: number
  /** The boundary conditions, one short line per edited object. */
  bc: string[]
  takenAt: string
  mesh: DecodedMesh
  field: string
  values: Float32Array
  hidden: Set<string>
  colorMap: ColorMap
  /** Whether the surface field was shown; its values are kept either way. */
  surfaceVisible: boolean
  slices: SnapshotSlice[]
  volume: SnapshotVolume | null
}

export type SnapshotInput = Omit<ScanoSnapshot, 'id' | 'takenAt' | 'bc'>

export const MAX_SNAPSHOTS = 6

let snapshots: ScanoSnapshot[] = []
let nextId = 1
let viewOpen = false
const listeners = new Set<() => void>()

function emit() {
  for (const fn of listeners) fn()
}

export function getSnapshots(): ScanoSnapshot[] {
  return snapshots
}

export function isSnapshotViewOpen(): boolean {
  return viewOpen
}

export function subscribeSnapshots(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function addSnapshot(input: SnapshotInput, bc: string[]): ScanoSnapshot {
  const snap: ScanoSnapshot = {
    ...input, bc, id: nextId++,
    takenAt: new Date().toLocaleTimeString([], { hour12: false }),
    values: input.values.slice(),
    slices: input.slices.map((s) => ({ ...s, payload: { ...s.payload, values: s.payload.values?.slice() ?? null } })),
  }
  snapshots = [...snapshots, snap].slice(-MAX_SNAPSHOTS)
  emit()
  return snap
}

export function removeSnapshot(id: number): void {
  snapshots = snapshots.filter((s) => s.id !== id)
  if (!snapshots.length) viewOpen = false
  emit()
}

export function clearSnapshots(): void {
  snapshots = []
  viewOpen = false
  emit()
}

export function setSnapshotViewOpen(open: boolean): void {
  viewOpen = open && snapshots.length > 0
  emit()
}
