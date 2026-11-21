/**
 * The post-processing view state the two panels share.
 *
 * The controls for one picture live on both sides of the screen: the left panel
 * loads the run and asks for a render, the right panel decides which patches
 * are drawn and how the geometry looks. They are separate React subtrees under
 * different parents, so the patch list has to live outside both.
 *
 * A module store rather than context, matching `projectStore`: the panels
 * subscribe, and nothing between them re-renders when a checkbox moves.
 *
 * None of this belongs to the project. It is what the legacy editor keeps in
 * `window._ppState` — view-only, dies with the page — and putting it in the
 * .cfdproj would make a visibility checkbox dirty the file and land in undo.
 */

import type { PatchMeta } from './index'

export interface PpViewState {
  /** Result source, so a different run starts with a freshly folded patch tree. */
  runDir: string | null
  /** The run's patches, as /api/pp/mesh reported them. */
  patches: PatchMeta[]
  /** Patch names NOT drawn. Absent = drawn, so a new run starts fully visible. */
  hidden: Set<string>
  /** internalMesh: the slice and streamline layers, which are not patches. */
  internalVisible: boolean
  /** Read Zones — cellZones are not fetched until this is on. */
  readZones: boolean
  /**
   * Where the seed sphere was DRAGGED to, or null if it has not been.
   *
   * The gizmo lives in the viewport and the X/Y/Z boxes live in the panel, so a
   * drag has to travel between two React subtrees. It is a separate field from
   * whatever the panel holds: writing the panel's own centre from here would
   * fight the boxes while they are being typed in.
   */
  seedDrag: [number, number, number] | null
  /**
   * Where the AI inference box was DRAGGED to, or null if it has not been.
   *
   * Same round trip as `seedDrag`, and for the same reason: the box's handles
   * are in the viewport and its six number boxes are in `AiInferencePanel`,
   * which is neither the viewport's subtree nor the one that owns the drag.
   * A new object per drag, so a listener can tell a fresh report from the one
   * it already consumed by identity.
   */
  aiBoxDrag: { min: [number, number, number]; max: [number, number, number] } | null
  /** Where the slice preview ring was dragged to, or null if it has not been. */
  ringDrag: [number, number, number] | null
}

type Listener = () => void

const listeners = new Set<Listener>()

let state: PpViewState = {
  runDir: null,
  patches: [],
  hidden: new Set(),
  internalVisible: true,
  readZones: false,
  seedDrag: null,
  aiBoxDrag: null,
  ringDrag: null,
}

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribePpView(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getPpView(): PpViewState {
  return state
}

/**
 * Replace the patch list when a run loads.
 *
 * Hidden patches are recomputed from the run's own `defaultVisible` rather than
 * carried over: patch names repeat across cases, so keeping the old set would
 * hide a patch of the new run that happens to share a name with one the user
 * hid in the previous one.
 */
export function setPpPatches(patches: PatchMeta[], runDir: string | null = null) {
  const hidden = new Set<string>()
  for (const p of patches) if (p.defaultVisible === false) hidden.add(p.name)
  state = { ...state, patches, hidden, runDir }
  emit()
}

export function setPpPatchVisible(name: string, visible: boolean) {
  const hidden = new Set(state.hidden)
  if (visible) hidden.delete(name)
  else hidden.add(name)
  state = { ...state, hidden }
  emit()
}

/** Show or hide a batch at once — a kind header, a model row, or all of them. */
export function setPpPatchesVisible(names: readonly string[], visible: boolean) {
  const hidden = new Set(state.hidden)
  for (const n of names) {
    if (visible) hidden.delete(n)
    else hidden.add(n)
  }
  state = { ...state, hidden }
  emit()
}

export function setPpInternalVisible(visible: boolean) {
  state = { ...state, internalVisible: visible }
  emit()
}

export function setPpReadZones(on: boolean) {
  state = { ...state, readZones: on }
  emit()
}

/** Report a finished seed-sphere drag, in world coordinates. */
export function setPpSeedDrag(centre: [number, number, number]) {
  state = { ...state, seedDrag: centre }
  emit()
}

/** Report a finished AI-box drag — a move or a face resize. */
export function setPpAiBoxDrag(box: {
  min: [number, number, number]
  max: [number, number, number]
}) {
  state = { ...state, aiBoxDrag: box }
  emit()
}

/** Report a finished preview-ring drag, in world coordinates. */
export function setPpRingDrag(origin: [number, number, number]) {
  state = { ...state, ringDrag: origin }
  emit()
}

/** Drop everything — used when the panel unloads a run. */
export function resetPpView() {
  state = {
    runDir: null,
    patches: [],
    hidden: new Set(),
    internalVisible: true,
    readZones: false,
    seedDrag: null,
    aiBoxDrag: null,
    ringDrag: null,
  }
  emit()
}
