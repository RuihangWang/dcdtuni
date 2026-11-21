/**
 * Rack detail mode: edit one rack in isolation.
 *
 * The room view is the wrong place to set a 19" rack's internal geometry — the
 * mounting width, how far the servers sit back from each door, which doors are
 * perforated. The legacy editor answers that by REPLACING the scene with the
 * single rack at the origin, unrotated, optionally with the rest of the room
 * ghosted behind it (`doRebuildRackDetail`, STL_generate.html:7517).
 *
 * What is easy to get wrong, and what this module pins down:
 *
 * - **The rack renders unrotated.** A rack placed at 90° in the room would put
 *   its inlet face sideways, and "front" would mean something different for
 *   every rack. Detail mode always shows the default orientation.
 * - **Sliders mutate live and check-point once.** `_rdCheckpointPushed` is the
 *   legacy's one-checkpoint-per-drag flag, reset on the control's `change`.
 * - **Every write goes through `syncRackModelState`.** It re-derives the slot
 *   table, clamps the offsets against the rack depth, and rebuilds the doors;
 *   writing `inletOffset` alone leaves the servers where they were.
 */

import type { Construction } from '../schema/cfdproj'
import { rackState, type RackStateModule } from '../legacy/cfdprojModules'
import * as store from '../projectStore'

export interface RackDetailState {
  /** The rack being edited, or null when the room view is showing. */
  target: string | null
  /** Draw the rest of the room, ghosted, for context. */
  showContext: boolean
  /** Which view to restore on exit — entering from the 2D editor returns there. */
  returnMode: '2d' | '3d'
}

type Listener = () => void

const listeners = new Set<Listener>()
let state: RackDetailState = { target: null, showContext: false, returnMode: '3d' }

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribeRackDetail(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getRackDetail(): RackDetailState {
  return state
}

export function isRackDetailMode(): boolean {
  return state.target !== null
}

export function enterRackDetail(uid: string, returnMode: '2d' | '3d' = '3d') {
  state = { target: uid, showContext: false, returnMode }
  store.logOperation('enterRackDetail', uid)
  emit()
}

export function exitRackDetail() {
  if (!state.target) return
  state = { ...state, target: null }
  store.logOperation('exitRackDetail', '')
  emit()
}

export function setShowContext(on: boolean) {
  state = { ...state, showContext: on }
  emit()
}

/**
 * Whether the rack-detail PANEL takes the inspector's place.
 *
 * Only while the selection is the detail rack itself (or nothing): a patch, a
 * server or a slot picked inside the rack keeps its own inspector, as it does
 * in the legacy, where the detail panel is one branch of
 * `renderRackModelInspector` (STL_generate.html:9242) and a narrower selection
 * never reaches it. Showing the detail panel for EVERY selection made a patch
 * click look like it did nothing.
 */
export function detailPanelShows(
  target: string | null,
  selectedUid: string | null,
  node: { type: string } | null,
): boolean {
  if (!target) return false
  if (node && node.type !== 'model') return false
  return selectedUid === null || selectedUid === target
}

// --- pure geometry helpers ------------------------------------------------

/** The legacy clamp on the mounting-width ratio (`syncRackModelState`:429). */
export function clampWidthRatio(ratio: number): number {
  return Math.min(0.95, Math.max(0.2, ratio))
}

/** Ratio -> percent for the slider, and back. */
export function ratioToPct(ratio: number): number {
  return Math.round(clampWidthRatio(ratio) * 100)
}

/**
 * The depth left for equipment once both doors' setbacks are taken.
 *
 * Shown under the sliders because it is the number that matters: push both
 * offsets far enough and there is no room for a server at all, which
 * `syncRackModelState` then silently rescales.
 */
export function effectiveDepth(
  depth: number,
  inlet: number,
  outlet: number,
  extraInlet = 0,
  extraOutlet = 0,
): number {
  return depth - (inlet + extraInlet) - (outlet + extraOutlet)
}

// --- project writes -------------------------------------------------------

type DoorFace = {
  uid?: string
  role?: string
  renderDoor?: boolean
  perforation?: Record<string, unknown>
}

type RackLike = Construction & {
  /** Door setbacks are TOP-LEVEL on a rack, not under geometry. */
  inletOffset?: number
  outletOffset?: number
  cooling?: Record<string, unknown>
}

/**
 * The rack with this uid.
 *
 * V4 keeps room models in `constructions.<bucket>` as a uid-keyed DICT — racks
 * under `constructions.racks` — not in the top-level `models`, which holds the
 * project's model REGISTRY. Walking `models` finds nothing and every setter
 * here becomes a silent no-op.
 */
function rackOf(project: unknown, uid: string): RackLike | null {
  const buckets = (project as { constructions?: Record<string, Record<string, Construction>> } | null)
    ?.constructions ?? {}
  for (const items of Object.values(buckets)) {
    if (!items || typeof items !== 'object') continue
    const hit = items[uid]
    if (hit) return hit as RackLike
  }
  return null
}

/**
 * The default perforation a freshly enabled door carries.
 *
 * Copied from `_rdToggleDoor` (STL_generate.html:12051). A door with no
 * perforation block is a SOLID plate to the runner, so leaving it out turns a
 * mesh door into a wall.
 */
export const DEFAULT_DOOR_PERFORATION = {
  shape: 'circle',
  holeDiameterMm: 3.0,
  openAreaPct: 64.0,
}

export function doorUid(side: 'inlet' | 'outlet'): string {
  return side === 'inlet' ? 'inletDoor' : 'outletDoor'
}

/** The door face entry for a side, or null when that door is not installed. */
export function findDoor(rack: Construction | null, side: 'inlet' | 'outlet'): DoorFace | null {
  const faces = (rack?.geometry as { faces?: unknown } | undefined)?.faces
  if (!Array.isArray(faces)) return null
  const uid = doorUid(side)
  return (
    (faces as DoorFace[]).find((f) => f && f.uid === uid && f.role === 'door') ?? null
  )
}

/** Apply a mutation to one rack and re-derive its slot state. */
function editRack(uid: string, action: string, detail: string, mutate: (rack: RackLike) => void) {
  store.edit(action, detail, (draft) => {
    const rack = rackOf(draft, uid)
    if (!rack || rack.kind !== 'rack') return
    mutate(rack)
    // Every write goes through this: it re-derives the slot table and clamps
    // the offsets against the depth. Writing a field alone leaves the servers
    // sitting where the old geometry put them.
    rackState().syncRackModelState(rack)
  })
}

/** Live variant for slider drags: no clone, no undo entry, one repaint. */
function liveRack(uid: string, mutate: (rack: RackLike) => void) {
  store.mutateLive((project) => {
    const rack = rackOf(project, uid)
    if (!rack || rack.kind !== 'rack') return
    mutate(rack)
    rackState().syncRackModelState(rack)
  })
  store.notifyLiveEdit()
}

export function setInnerWidthRatio(uid: string, ratio: number, live: boolean) {
  const apply = (rack: RackLike) => {
    const geom = rack.geometry as unknown as Record<string, unknown>
    geom.slotWidthRatio = clampWidthRatio(ratio)
  }
  if (live) liveRack(uid, apply)
  else editRack(uid, 'rackDetailInnerWidth', `${Math.round(ratio * 100)}%`, apply)
}

/** Metres, as the number box gives them. */
export function setInnerWidthMeters(uid: string, meters: number, width: number) {
  if (!Number.isFinite(meters) || width <= 0) return
  setInnerWidthRatio(uid, meters / width, false)
}

export function setDoorOffset(
  uid: string,
  side: 'inlet' | 'outlet' | 'both',
  meters: number,
  live: boolean,
) {
  if (!Number.isFinite(meters)) return
  const v = Math.max(0, meters)
  const apply = (rack: RackLike) => {
    if (side === 'inlet' || side === 'both') rack.inletOffset = v
    if (side === 'outlet' || side === 'both') rack.outletOffset = v
  }
  if (live) liveRack(uid, apply)
  else editRack(uid, 'rackDetailSlotDepth', `${side} ${v.toFixed(3)}m`, apply)
}

/** The shared writer's answer, passed through for the panels to render. */
export type ServerDepthResult = ReturnType<RackStateModule['setServerDepthAbsolute']>

/**
 * One server's ABSOLUTE setbacks, in ONE transaction (spec §6).
 *
 * Replaces the retired `setServerDepth` / `setServerExtras` pair. Those wrote
 * rack-RELATIVE extras, so a server's depth was derived as rack offset + extra
 * and a row-slider drag moved every server in the rack. The fields are now the
 * absolute `inletOffset` / `outletOffset` on the record, the write goes through
 * the SHARED `setServerDepthAbsolute` (one definition of the §3.1 range rule
 * and the depth-conflict dry run), and the row sliders no longer reach it.
 *
 * The shared writer refuses rather than clamps — `{ ok: false, error: 'range' }`
 * for a pair that breaks §3.1, `'overlap'` for a depth interval intersecting
 * another server in the same U's — and this function passes the result through
 * so the panel shows the number the user typed next to the reason it was not
 * written.
 *
 * Undo semantics are the ones the two panels already had:
 *
 * - `live: true` — one frame of a slider drag. Mutates the live project, no
 *   undo entry, no clone; a refused frame writes nothing at all.
 * - `live: false` — the release, or a number-box commit. ONE `store.edit`.
 * - `dragStart` — the release of a drag whose frames wrote the live project.
 *   `store.edit` snapshots the live project as the undo entry, so without
 *   putting the pre-drag setbacks back first, Ctrl+Z would "restore" the drag.
 *   Restore, then commit once: the rule `mutateLive` states, and what
 *   faceDragActions.ts and moveGizmo.ts do. A release whose final value is
 *   refused leaves the server at its drag start and pushes nothing.
 */
export function setServerDepthAbsolute(
  uid: string,
  serverUid: string,
  inletOffset: number,
  outletOffset: number,
  live?: boolean,
  dragStart?: { inletOffset: number; outletOffset: number },
): ServerDepthResult {
  const shared = rackState()
  if (live) {
    let result: ServerDepthResult = { ok: false, error: 'missing' }
    store.mutateLive((project) => {
      const rack = rackOf(project, uid)
      if (!rack || rack.kind !== 'rack') return
      result = shared.setServerDepthAbsolute(rack, serverUid, inletOffset, outletOffset)
      // The shared writer does not re-run the normalizer — the transaction
      // owner does, and here that is this frame.
      if (result.ok) shared.syncRackModelState(rack)
    })
    if (result.ok) store.notifyLiveEdit()
    return result
  }

  if (dragStart) {
    store.mutateLive((project) => {
      const rack = rackOf(project, uid)
      if (!rack || rack.kind !== 'rack') return
      const eq = rackServers(rack)[serverUid]
      if (!eq) return
      eq.inletOffset = dragStart.inletOffset
      eq.outletOffset = dragStart.outletOffset
      shared.syncRackModelState(rack)
    })
  }

  // Refuse BEFORE the undo stack is touched: the shared writer run against a
  // throwaway copy of the rack answers exactly what the edit below would do,
  // so a refused commit leaves no empty undo entry behind. The servers are
  // cloned because the writer writes in place on the object it is handed.
  const project = store.getProject()
  const rack = project ? rackOf(project, uid) : null
  if (!rack || rack.kind !== 'rack') return { ok: false, error: 'missing' }
  const probe: RackLike = {
    kind: 'rack',
    geometry: rack.geometry,
    inletOffset: rack.inletOffset,
    outletOffset: rack.outletOffset,
    constructions: { servers: structuredClone(rackServers(rack)) },
  } as unknown as RackLike
  const probed = shared.setServerDepthAbsolute(probe, serverUid, inletOffset, outletOffset)
  if (!probed.ok) return probed

  let result: ServerDepthResult = { ok: false, error: 'missing' }
  editRack(uid, 'rackDetailServerDepth', `${serverUid} depth`, (target) => {
    result = shared.setServerDepthAbsolute(target, serverUid, inletOffset, outletOffset)
  })
  return result
}

/** A rack's servers: a uid-keyed DICT (`constructions.servers`, rack_state.js:501). */
function rackServers(rack: RackLike): Record<string, Record<string, unknown>> {
  return ((rack.constructions as { servers?: Record<string, Record<string, unknown>> })?.servers ??
    {}) as Record<string, Record<string, unknown>>
}

export function setCoolingField(uid: string, key: string, value: unknown) {
  editRack(uid, 'setRackCooling', `${key}=${String(value)}`, (rack) => {
    const cooling = (rack.cooling ?? (rack.cooling = {})) as Record<string, unknown>
    cooling[key] = value
  })
}

/**
 * Install or remove a door.
 *
 * Enabling PUSHES a face entry; disabling SPLICES it out. The absence of the
 * entry is what "no door" means in V4 — a `renderDoor: false` entry is still a
 * door to the mesher, just an invisible one.
 */
export function setDoorEnabled(uid: string, side: 'inlet' | 'outlet', enabled: boolean) {
  editRack(uid, 'rackDetailDoor', `${side}=${enabled}`, (rack) => {
    const geom = rack.geometry as unknown as { faces?: unknown }
    if (!Array.isArray(geom.faces)) geom.faces = []
    const faces = geom.faces as DoorFace[]
    const id = doorUid(side)
    const idx = faces.findIndex((f) => f && f.uid === id && f.role === 'door')
    if (enabled) {
      const entry: DoorFace = {
        uid: id,
        role: 'door',
        perforation: { ...DEFAULT_DOOR_PERFORATION },
        renderDoor: true,
      }
      if (idx >= 0) faces[idx] = entry
      else faces.push(entry)
    } else if (idx >= 0) {
      faces.splice(idx, 1)
    }
  })
}

/** Draw the door or not. A hidden door is still meshed. */
export function setDoorRender(uid: string, side: 'inlet' | 'outlet', visible: boolean) {
  editRack(uid, 'rackDetailDoorRender', `${side}=${visible}`, (rack) => {
    const door = findDoor(rack, side)
    if (door) door.renderDoor = visible
  })
}

/** One field of a door's perforation block, by dotted path. */
export function setDoorPerforation(
  uid: string,
  side: 'inlet' | 'outlet',
  field: string,
  value: unknown,
) {
  editRack(uid, 'rackDetailDoorPerforated', `${side}.${field}`, (rack) => {
    const door = findDoor(rack, side)
    if (!door) return
    if (!door.perforation || typeof door.perforation !== 'object') {
      door.perforation = { ...DEFAULT_DOOR_PERFORATION }
    }
    const parts = field.split('.')
    let target = door.perforation as Record<string, unknown>
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts[i]!
      if (!target[key] || typeof target[key] !== 'object') target[key] = {}
      target = target[key] as Record<string, unknown>
    }
    target[parts[parts.length - 1]!] = value
  })
}
