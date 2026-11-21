/**
 * Rack U-slots: which ones are open, and what an opening means.
 *
 * A rack's front and back faces are divided into U-slots, and a slot's face
 * panel can be DELETED — which is how air gets in and out. Delete the same slot
 * on both faces and the two openings become a through-channel; a run of
 * adjacent through-slots is a leakage zone with its own loss coefficient.
 *
 * That is the whole reason this exists: `case_02_leakage` is authored this way,
 * and without slot editing a leakage case cannot be built at all.
 *
 * Slot state lives in `model.deletedFaces[side][index]`, and the derived
 * geometry — which panels to draw, which through-zones exist — comes from the
 * SHARED `geometry_pure.js`, so this module never re-derives it.
 */

import * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { geometryPure } from '../legacy/geometryBuilder'
import { stlNaming } from '../legacy/stlNaming'
import { rackState, type ServerDepthConflict } from '../legacy/cfdprojModules'
import * as store from '../projectStore'
import { nextEquipmentSeq } from './duplicateIds'
import {
  equipmentEndUnit,
  equipmentList,
  selectionContext,
  splitSpans,
  summarise,
  type RemovalMode,
} from './rackSelection'

export type RackSide = 'front' | 'back'

export interface SlotHit {
  type: 'slot' | 'equipment' | 'through_zone'
  slotIndex?: number
  equipmentId?: string
  startUnit?: number
  endUnit?: number
}

export interface FaceLayout {
  origin: THREE.Vector3
  right: THREE.Vector3
  up: THREE.Vector3
}

/**
 * Which slot a click on a rack face landed in.
 *
 * The hit point is taken to the rack's LOCAL space first: a rack yawed 90° in
 * the room has its slots running the same way as any other rack in its own
 * frame, and projecting in world space would read the slot index off the wrong
 * axis.
 *
 * Returns null outside the slot area — the frame, and the safe margins top and
 * bottom, are structure, not slots.
 */
export function slotAtFacePoint(
  model: Construction,
  localPoint: THREE.Vector3,
  layout: FaceLayout,
): number | null {
  const geometry = model.geometry as unknown as {
    size?: { x: number; y: number }
    slotWidthRatio?: number
    slot?: number
    slotHeight?: number
  }
  const withMargins = model as unknown as { safeMarginBottom?: number; safeMarginTop?: number }
  const sizeX = Number(geometry.size?.x ?? 0.6)
  const sizeY = Number(geometry.size?.y ?? 2)
  const ratio = Number(geometry.slotWidthRatio ?? 0.8)
  const slots = Number(geometry.slot ?? 42)
  const slotHeight = Number(geometry.slotHeight ?? 0.04445)
  const bottom = Number(withMargins.safeMarginBottom ?? 0)
  const top = Number(withMargins.safeMarginTop ?? 0)

  const offset = new THREE.Vector3().subVectors(localPoint, layout.origin)
  const u = offset.dot(layout.right)
  const v = offset.dot(layout.up)

  const slotWidth = sizeX * ratio
  const slotX = (sizeX - slotWidth) / 2
  if (u < slotX || u > slotX + slotWidth) return null
  if (v < bottom || v > sizeY - top) return null

  const index = Math.floor((v - bottom) / slotHeight)
  if (index < 0 || index >= slots) return null
  return index
}

function deletedFaces(model: Construction): Record<string, Record<number, boolean>> {
  const bag = model as unknown as { deletedFaces?: Record<string, Record<number, boolean>> }
  return bag.deletedFaces ?? (bag.deletedFaces = { front: {}, back: {} })
}

export function isSlotDeleted(model: Construction, side: RackSide, index: number): boolean {
  return deletedFaces(model)[side]?.[index] === true
}

/** A slot open on BOTH faces: air passes straight through the rack. */
export function isThroughSlot(model: Construction, index: number): boolean {
  return isSlotDeleted(model, 'front', index) && isSlotDeleted(model, 'back', index)
}

/** The equipment occupying a slot, if any — an occupied slot cannot be opened. */
export function equipmentAtSlot(model: Construction, index: number): { id?: string } | null {
  try {
    const pure = geometryPure() as unknown as {
      getRackEquipmentForSlot?: (m: Construction, i: number) => { id?: string } | null
    }
    return pure.getRackEquipmentForSlot?.(model, index) ?? null
  } catch {
    return null
  }
}

/**
 * The through-zone group a slot belongs to.
 *
 * Derived by the SHARED `getRackThroughZoneGroups` (js/stl_naming.js:155),
 * which is also what the exporter uses to name the leakage cellZones — so the
 * panel and the .cfdrun can never disagree about where a zone starts and ends.
 * A zone spans the full run of gaps on EITHER face, so a 4U front gap facing a
 * 1U back gap is one 4U zone.
 */
export function throughZoneAt(
  model: Construction,
  index: number,
): { startUnit: number; endUnit: number; sigma?: number } | null {
  try {
    const naming = stlNaming() as unknown as {
      getRackThroughZoneGroups?: (
        m: Construction,
      ) => { startUnit: number; endUnit: number; sigma?: number }[]
    }
    const groups = naming.getRackThroughZoneGroups?.(model) ?? []
    return groups.find((g) => index >= g.startUnit && index <= g.endUnit) ?? null
  } catch {
    return null
  }
}

/** What a click on a rack face selects: equipment, a leakage zone, or a slot. */
export function resolveSlotHit(model: Construction, index: number): SlotHit {
  const equipment = equipmentAtSlot(model, index)
  if (equipment?.id) return { type: 'equipment', equipmentId: equipment.id }
  if (isThroughSlot(model, index)) {
    const zone = throughZoneAt(model, index)
    if (zone) return { type: 'through_zone', startUnit: zone.startUnit, endUnit: zone.endUnit }
  }
  return { type: 'slot', slotIndex: index }
}

export type SlotWriteResult = 'ok' | 'occupied' | 'missing'

/**
 * Open or close one slot's face panel.
 *
 * Refused when equipment occupies the slot: the panel is what the server is
 * mounted against, and removing it under a server would leave the server
 * floating in a hole.
 */
export function setSlotDeleted(
  uid: string,
  side: RackSide,
  index: number,
  deleted: boolean,
): SlotWriteResult {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!model || model.kind !== 'rack') return 'missing'
  if (equipmentAtSlot(model, index)) return 'occupied'

  store.edit('setRackSlotDeleted', `${uid} ${side}#${index}=${deleted}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target) return
    const faces = deletedFaces(target)
    if (!faces[side]) faces[side] = {}
    faces[side]![index] = deleted
    // Re-derives the panels, the through-zones and the partitions that a
    // newly opened slot changes.
    rackState().syncRackModelState(target)
  })
  return 'ok'
}

/** Close the opposite face of a slot, ending a half-open state. */
export function restoreOpposite(uid: string, side: RackSide, index: number): SlotWriteResult {
  return setSlotDeleted(uid, side === 'front' ? 'back' : 'front', index, false)
}

/**
 * The loss coefficient of a leakage zone.
 *
 * Stored per zone on the rack, keyed by the run it covers — the runner reads it
 * as the porous jump across the through-channel, so it is a physical
 * parameter, not a display setting.
 */
export function setThroughZoneSigma(uid: string, startUnit: number, sigma: number) {
  if (!Number.isFinite(sigma)) return
  store.edit('setThroughZoneSigma', `${uid} @${startUnit} σ=${sigma}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model as unknown as {
      throughSlotSigma?: Record<number, number>
    }
    if (!model) return
    const bag = model.throughSlotSigma ?? (model.throughSlotSigma = {})
    // Keyed by the zone's START unit and clamped to [0, 1], exactly as
    // `setRackThroughZoneSigma` does (STL_generate.html:14408) — the exporter
    // reads this key, so a different one is a silently ignored setting.
    bag[startUnit] = Math.max(0, Math.min(1, Number(sigma) || 0.05))
  })
}

/** Close every slot in a leakage zone, on both faces. */
export function dissolveThroughZone(uid: string, startUnit: number, endUnit: number) {
  store.edit('dissolveThroughZone', `${uid} ${startUnit}-${endUnit}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    const faces = deletedFaces(model)
    for (let i = startUnit; i <= endUnit; i++) {
      if (faces.front) faces.front[i] = false
      if (faces.back) faces.back[i] = false
    }
    // The zone is derived from the open slots, so closing them removes it; the
    // stored sigma for that key goes with it (STL_generate.html:14364).
    const bag = model as unknown as { throughSlotSigma?: Record<number, number> }
    if (bag.throughSlotSigma) delete bag.throughSlotSigma[startUnit]
    rackState().syncRackModelState(model)
  })
}

/** The three equipment kinds a slot can be filled with. */
export const EQUIPMENT_PRESETS = {
  fanServer: { defaultName: 'fan-server', frontColor: '#d14b4b', backColor: '#9f3636' },
  nonFan: { defaultName: 'non-fan equipment', frontColor: '#4daafc', backColor: '#2f78c0' },
  liquidServer: { defaultName: 'liquid-server', frontColor: '#4dcc7a', backColor: '#36994f' },
} as const

export type EquipmentKind = keyof typeof EQUIPMENT_PRESETS

/** The next `e_<n>` id, derived from the ids already in the project. */
export function nextEquipmentUid(project: CfdProject): string {
  return `e_${nextEquipmentSeq(project)}`
}

export type CreateResult = 'ok' | 'occupied' | 'blocked' | 'missing' | 'depth-range'

/**
 * One server's ABSOLUTE depth setbacks (spec §3.1): metres back from the rack
 * inlet plane and from the rack outlet plane. The optional `depth` argument of
 * every creation path; absent means the rack's own row plane
 * (`defaultServerDepthOffsets`), which is where a server was created before the
 * fields existed.
 */
export interface ServerDepthOffsets {
  inletOffset: number
  outletOffset: number
}

/** The rack depth D in metres — the axis the absolute setbacks live on. */
export function rackDepthM(model: Construction): number {
  const z = Number((model.geometry as { size?: { z?: number } } | undefined)?.size?.z ?? 0)
  return Number.isFinite(z) && z > 0 ? z : 0
}

/**
 * Settle the depth a new server gets.
 *
 * Missing halves come from the rack plane. An EXPLICIT value that breaks §3.1
 * (negative, not a number, or the pair eating more than 90% of D) is refused
 * with null rather than clamped — the same stance the shared
 * `setServerDepthAbsolute` takes, so a panel can show the number it typed next
 * to the refusal.
 */
export function resolveServerDepth(
  model: Construction,
  depth?: Partial<ServerDepthOffsets>,
): ServerDepthOffsets | null {
  const def = rackState().defaultServerDepthOffsets(model)
  const inletOffset = depth?.inletOffset ?? def.inletOffset
  const outletOffset = depth?.outletOffset ?? def.outletOffset
  if (!Number.isFinite(inletOffset) || !Number.isFinite(outletOffset)) return null
  if (inletOffset < 0 || outletOffset < 0) return null
  const D = rackDepthM(model)
  if (D > 0 && inletOffset + outletOffset > rackState().SERVER_DEPTH_MAX_FRACTION * D) return null
  return { inletOffset, outletOffset }
}

/** The candidate record a depth dry run judges. */
export interface DepthCandidate {
  slotPosition: number
  slotOccupation: number
  inletOffset: number
  outletOffset: number
}

/**
 * Every depth conflict a candidate server would cause, as the shared predicate
 * reports them.
 *
 * A DRY RUN of `findServerDepthConflicts` over a throwaway rack carrying the
 * candidate record — the same walk the normalizer drops by, so "the UI let me"
 * and "the document kept it" cannot disagree. The candidate replaces any
 * record under `replaceUid`, which is how the extend path re-validates a
 * rewritten U range. Nothing is written; the probe only references the live
 * records, and the predicate only reads.
 */
export function findCandidateDepthConflicts(
  model: Construction,
  candidate: DepthCandidate,
  replaceUid?: string,
): ServerDepthConflict[] {
  const servers = ((model.constructions as { servers?: Record<string, unknown> } | undefined)
    ?.servers ?? {}) as Record<string, unknown>
  const probeServers: Record<string, unknown> = {}
  for (const [key, rec] of Object.entries(servers)) {
    if (replaceUid && String((rec as { uid?: string }).uid ?? key) === replaceUid) continue
    probeServers[key] = rec
  }
  const probeUid = '__depth_probe__'
  probeServers[probeUid] = {
    uid: probeUid,
    geometry: {
      slotPosition: candidate.slotPosition,
      slotOccupation: Math.max(1, Math.round(candidate.slotOccupation)),
    },
    inletOffset: candidate.inletOffset,
    outletOffset: candidate.outletOffset,
  }
  const probe = {
    kind: 'rack',
    geometry: model.geometry,
    inletOffset: (model as unknown as { inletOffset?: number }).inletOffset,
    outletOffset: (model as unknown as { outletOffset?: number }).outletOffset,
    constructions: { servers: probeServers },
  }
  return rackState().findServerDepthConflicts(probe)
}

/** Would a server of this span, at this depth, intersect one already placed? */
export function wouldConflictAtDepth(
  model: Construction,
  candidate: DepthCandidate,
  replaceUid?: string,
): boolean {
  return findCandidateDepthConflicts(model, candidate, replaceUid).length > 0
}

/**
 * Put a server in a slot.
 *
 * Without this a rack in this app can only ever be empty, and an empty rack is
 * not a heat load — the whole case is a room of furniture.
 *
 * `spanUnits` comes from the rack's `defaultSpanUnits` and a span running off
 * the top of the rack is refused rather than truncated: silently shortening it
 * would put a boundary where the user did not ask for one.
 *
 * Occupancy is CONFLICT-AWARE (spec §4.4): a slot that already holds a server
 * is only blocked when the new server's depth interval intersects the existing
 * one's — two servers may share a U range at different depths, which is the
 * whole point of the stacked-servers spec. The check is a dry run of the shared
 * `findServerDepthConflicts` over a copy carrying the new record, and the
 * normalizer's own drop remains only as a last-resort guard: a creation the
 * predicate called clean but the sync dropped anyway reports 'occupied'
 * instead of a silent nothing.
 */
export function createEquipmentAtSlot(
  uid: string,
  slotIndex: number,
  kind: EquipmentKind,
  depth?: Partial<ServerDepthOffsets>,
): CreateResult {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !model || model.kind !== 'rack') return 'missing'

  const offsets = resolveServerDepth(model, depth)
  if (!offsets) return 'depth-range'

  const geometry = model.geometry as unknown as { slot?: number }
  const slots = Number(geometry.slot ?? 42)
  const span = Math.max(1, Math.round(Number((model as unknown as { defaultSpanUnits?: number }).defaultSpanUnits) || 1))
  if (slotIndex + span > slots) return 'blocked'
  if (
    wouldConflictAtDepth(model, {
      slotPosition: slotIndex,
      slotOccupation: span,
      ...offsets,
    })
  ) {
    return 'occupied'
  }

  const preset = EQUIPMENT_PRESETS[kind]
  const newUid = nextEquipmentUid(project)
  store.edit('createRackEquipmentAtSlot', `${uid} #${slotIndex} ${kind}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target) return
    const cons = ((target as unknown as { constructions?: Record<string, unknown> }).constructions ??
      ((target as unknown as { constructions: Record<string, unknown> }).constructions = {})) as Record<
      string,
      unknown
    >
    const servers = (cons.servers ?? (cons.servers = {})) as Record<string, unknown>
    const count = Object.keys(servers).length + 1
    // The V4 server shape `_rackInsertServer` writes (STL_generate.html:5683):
    // slot position and span live under `geometry`, physics under `cooling`.
    // Depth is the ABSOLUTE pair (spec §3.1) and a new server starts opaque —
    // `meta.display.opacity = 1`, the sync leaves a stored value alone.
    servers[newUid] = {
      uid: newUid,
      type: kind,
      geometry: { slotPosition: slotIndex, slotOccupation: span },
      cooling: {},
      constructions: { chips: {}, coldPlates: {} },
      meta: { name: `${preset.defaultName}_${count}`, enabled: true, display: { opacity: 1 } },
      inletOffset: offsets.inletOffset,
      outletOffset: offsets.outletOffset,
      faces: {
        inlet: { color: preset.frontColor, patches: [] },
        outlet: { color: preset.backColor, patches: [] },
      },
    }
    // A slot holding equipment has no open face: the server IS the face (the
    // legacy clears both after `_rackInsertServer`, and `setSlotDeleted` refuses
    // to open a slot under equipment). Left open, the server's outlet or inlet
    // is never built, while the manifest still binds it.
    const faces = deletedFaces(target)
    for (let i = slotIndex; i < slotIndex + span; i += 1) {
      if (faces.front) faces.front[i] = false
      if (faces.back) faces.back[i] = false
    }
    rackState().syncRackModelState(target)
  })
  // Last-resort guard: the dry run and the normalizer are one walk, so this
  // should never fire — but a document edited outside the UI can hold a record
  // the predicate did not see, and a silent drop is a missing heat load.
  const after = store.getProject()
  const target = after ? store.findConstruction(after, uid)?.model : null
  const survived = target
    ? ((target.constructions as { servers?: Record<string, unknown> } | undefined)?.servers ??
        {})[newUid]
    : null
  return survived ? 'ok' : 'occupied'
}

/** Remove a server from its rack. */
export function removeEquipment(uid: string, equipmentId: string) {
  store.edit('deleteRackEquipment', `${uid}/${equipmentId}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const servers = target
      ? ((target as unknown as { constructions?: { servers?: Record<string, unknown> } })
          .constructions?.servers ?? {})
      : {}
    delete (servers as Record<string, unknown>)[equipmentId]
    if (target) rackState().syncRackModelState(target)
  })
}

/**
 * Merge a contiguous slot range into ONE piece of equipment, or extend the
 * server already in it.
 *
 * Extending is only offered when the selection covers the whole server plus a
 * free slot beside it, and it must be the SAME kind: turning a fan-server into
 * a non-fan by extending it would silently change a flow boundary into a solid.
 * An explicit `depth` skips the extend path entirely — a caller stating where
 * the server sits in depth is asking for a NEW stacked record at that depth,
 * not for growing whatever covers the range (spec §7.1). The rewritten range of
 * an extend is re-validated against the depth predicate: growing a server into
 * U's where another server sits at the same depth is the interpenetration the
 * normalizer would otherwise drop.
 */
export function mergeSlotsIntoEquipment(
  uid: string,
  side: RackSide,
  slotIndices: number[],
  kind: EquipmentKind,
  depth?: Partial<ServerDepthOffsets>,
): CreateResult | 'not-contiguous' | 'wrong-type' {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !model || model.kind !== 'rack') return 'missing'

  const list = equipmentList(model)
  const summary = summarise(
    side,
    slotIndices,
    (i) => list.find((e) => i >= e.startUnit && i <= equipmentEndUnit(e)) ?? null,
    (s, i) => isSlotDeleted(model, s, i),
  )
  if (!summary) return 'missing'
  if (!summary.contiguous) return 'not-contiguous'

  const context = selectionContext(summary, (i) =>
    list.find((e) => i >= e.startUnit && i <= equipmentEndUnit(e)) ?? null,
  )
  if (!depth && context?.canExtendEquipment && context.singleEquipment) {
    if (context.singleEquipment.kind !== kind) return 'wrong-type'
    const id = context.singleEquipment.id
    const servers = ((model.constructions as { servers?: Record<string, Record<string, unknown>> })
      ?.servers ?? {}) as Record<string, Record<string, unknown>>
    const existing = servers[id]
    const def = rackState().defaultServerDepthOffsets(model)
    if (
      existing &&
      wouldConflictAtDepth(
        model,
        {
          slotPosition: summary.startUnit,
          slotOccupation: summary.slotIndices.length,
          inletOffset: Number(existing.inletOffset ?? def.inletOffset),
          outletOffset: Number(existing.outletOffset ?? def.outletOffset),
        },
        id,
      )
    ) {
      return 'occupied'
    }
    store.edit('extendRackEquipment', `${uid}/${id} -> ${summary.slotIndices.length}U`, (draft) => {
      const target = store.findConstruction(draft, uid)?.model
      const servers = target
        ? (((target as unknown as { constructions?: { servers?: Record<string, Record<string, unknown>> } })
            .constructions?.servers ?? {}) as Record<string, Record<string, unknown>>)
        : {}
      const srv = servers[id]
      if (!srv) return
      const geometry = (srv.geometry ?? (srv.geometry = {})) as Record<string, number>
      geometry.slotPosition = summary.startUnit
      geometry.slotOccupation = summary.slotIndices.length
      // A slot holding equipment has no open face: the server IS the face.
      const faces = deletedFaces(target!)
      for (const i of summary.slotIndices) {
        if (faces.front) faces.front[i] = false
        if (faces.back) faces.back[i] = false
      }
      rackState().syncRackModelState(target!)
    })
    return 'ok'
  }
  // "Occupied ⇒ refuse" is conflict-aware now: a server covering the range at a
  // DISJOINT depth does not block a new one, and `createEquipmentAtSlot` runs
  // the same dry run over the actual span (and validates an explicit depth), so
  // the decision is delegated rather than answered twice.

  // The span is the selection; `createEquipmentAtSlot` reads it from the rack's
  // default, so it is set for the duration of the call exactly as the legacy
  // does (STL_generate.html:14215).
  const previous = (model as unknown as { defaultSpanUnits?: number }).defaultSpanUnits
  store.mutateLive((p) => {
    const target = store.findConstruction(p, uid)?.model as unknown as { defaultSpanUnits?: number }
    if (target) target.defaultSpanUnits = summary.slotIndices.length
  })
  const result = createEquipmentAtSlot(uid, summary.startUnit, kind, depth)
  store.mutateLive((p) => {
    const target = store.findConstruction(p, uid)?.model as unknown as { defaultSpanUnits?: number }
    if (target) target.defaultSpanUnits = previous
  })
  return result
}

/**
 * Take a slot range out of the server that occupies it.
 *
 * The mode comes from the overlap — see `selectionContext`. A split leaves two
 * servers where there was one, so the caller confirms first.
 */
export function removeSlotsFromEquipment(
  uid: string,
  side: RackSide,
  slotIndices: number[],
): { mode: RemovalMode; ok: boolean } {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !model || model.kind !== 'rack') return { mode: null, ok: false }

  const list = equipmentList(model)
  const at = (i: number) => list.find((e) => i >= e.startUnit && i <= equipmentEndUnit(e)) ?? null
  const summary = summarise(side, slotIndices, at, (s, i) => isSlotDeleted(model, s, i))
  const context = selectionContext(summary, at)
  if (!summary || !context?.canRemoveFromEquipment || !context.singleEquipment) {
    return { mode: null, ok: false }
  }

  const equipment = context.singleEquipment
  const mode = context.removalMode
  const newUid = nextEquipmentUid(project)
  store.edit('removeRackSelectionFromEquipment', `${uid}/${equipment.id} ${mode}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target) return
    const servers = ((target as unknown as { constructions?: { servers?: Record<string, Record<string, unknown>> } })
      .constructions?.servers ?? {}) as Record<string, Record<string, unknown>>
    const srv = servers[equipment.id]
    if (!srv) return
    const geometry = (srv.geometry ?? (srv.geometry = {})) as Record<string, number>

    if (mode === 'full') {
      delete servers[equipment.id]
    } else if (mode === 'trimStart') {
      geometry.slotPosition = summary.endUnit + 1
      geometry.slotOccupation = equipmentEndUnit(equipment) - summary.endUnit
    } else if (mode === 'trimEnd') {
      geometry.slotOccupation = summary.startUnit - equipment.startUnit
    } else if (mode === 'split') {
      const parts = splitSpans(equipment, summary.startUnit, summary.endUnit)
      geometry.slotOccupation = parts.lowerSpan
      // The upper half is a NEW server carrying the same kind and a derived
      // name, as `removeRackSelectionFromEquipment` does.
      servers[newUid] = {
        uid: newUid,
        type: srv.type,
        geometry: { slotPosition: parts.upperStart, slotOccupation: parts.upperSpan },
        cooling: { ...((srv.cooling ?? {}) as object) },
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: `${equipment.name ?? equipment.id}_split`, enabled: true },
        faces: JSON.parse(JSON.stringify(srv.faces ?? {})),
      }
    }
    rackState().syncRackModelState(target)
  })
  return { mode, ok: true }
}

// ---------------------------------------------------------------------------
// Slot partitions
// ---------------------------------------------------------------------------

export interface RackPartition {
  index: number
  name: string
  deleted?: boolean
}

/**
 * The dividers between slots, as `syncRackModelState` normalises them.
 *
 * A rack of N slots always carries N-1 partition entries, all `deleted: true`
 * until someone asks for one — the list is the full set of places a divider
 * COULD go, not the set that exists. That is why adding and removing one is a
 * flag flip rather than an insert, and why a partition index the saved project
 * never mentioned is still addressable.
 */
export function rackPartitions(model: Construction): RackPartition[] {
  const cons = (model as unknown as { constructions?: { partitions?: RackPartition[] } })
    .constructions
  return Array.isArray(cons?.partitions) ? cons.partitions : []
}

export function partitionAt(model: Construction, index: number): RackPartition | null {
  return rackPartitions(model).find((p) => p.index === index) ?? null
}

/**
 * The two dividers that bound one slot.
 *
 * The partition at index `i` sits between slot `i` and slot `i+1`, so a slot's
 * TOP divider is its own index and its BOTTOM divider is one below. The ends of
 * the rack have only one: there is nothing above the top slot to divide it
 * from. Returns null for the missing side rather than an out-of-range index,
 * which is what stops the panel offering a button that writes nowhere.
 */
export function slotPartitionIndices(
  slotIndex: number,
  slotCount: number,
): { top: number | null; bottom: number | null } {
  return {
    top: slotIndex < slotCount - 1 ? slotIndex : null,
    bottom: slotIndex - 1 >= 0 ? slotIndex - 1 : null,
  }
}

/**
 * Add or remove a divider.
 *
 * A partition splits the through-channel between two adjacent slots into two
 * closed cavities, so this is the control that decides whether air moving up
 * inside the rack can pass from one slot to the next — not a cosmetic line.
 * Ports `_rackSlotAddPartition` / `_rackSlotRemovePartition`
 * (STL_generate.html:14294, 14320).
 */
export function setRackPartitionDeleted(uid: string, index: number, deleted: boolean) {
  const action = deleted ? 'rackSlotRemovePartition' : 'rackSlotAddPartition'
  store.edit(action, `${uid} #${index}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target || target.kind !== 'rack') return
    const cons = ((target as unknown as { constructions?: Record<string, unknown> })
      .constructions ??
      ((target as unknown as { constructions: Record<string, unknown> }).constructions =
        {})) as Record<string, unknown>
    if (!Array.isArray(cons.partitions)) cons.partitions = []
    const list = cons.partitions as RackPartition[]
    const existing = list.find((p) => p.index === index)
    if (existing) existing.deleted = deleted
    else list.push({ index, name: `partition_${index + 1}`, deleted })
    rackState().syncRackModelState(target)
  })
}

// ---------------------------------------------------------------------------
// Batch slot actions
// ---------------------------------------------------------------------------

/**
 * Open or close every slot in a range on ONE face.
 *
 * Opening a 20U blanking range is twenty checkbox clicks otherwise. Refused
 * outright when any slot in the range holds equipment, exactly as
 * `setRackSelectionDeleted` does (STL_generate.html:14336): a partial apply
 * would silently skip the occupied slots and leave a range half open.
 */
export function setRackSelectionDeleted(
  uid: string,
  side: RackSide,
  slotIndices: number[],
  deleted: boolean,
): SlotWriteResult {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!model || model.kind !== 'rack' || !slotIndices.length) return 'missing'
  if (slotIndices.some((i) => equipmentAtSlot(model, i))) return 'occupied'

  store.edit('setRackSelectionDeleted', `${uid} ${side} x${slotIndices.length}=${deleted}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target) return
    const faces = deletedFaces(target)
    if (!faces[side]) faces[side] = {}
    for (const index of slotIndices) faces[side]![index] = deleted
    rackState().syncRackModelState(target)
  })
  return 'ok'
}

/**
 * Which slots a sync-delete would act on.
 *
 * Only the ones already open on this face and still closed on the other: those
 * are the half-open slots, and opening their opposite is what turns a one-sided
 * opening into a through-channel. Pure, because the button is hidden when the
 * list is empty and that decision has to be testable.
 */
export function syncDeleteTargets(
  model: Construction,
  side: RackSide,
  slotIndices: number[],
): number[] {
  const opposite: RackSide = side === 'front' ? 'back' : 'front'
  return slotIndices.filter(
    (i) => isSlotDeleted(model, side, i) && !isSlotDeleted(model, opposite, i),
  )
}

/**
 * Open the opposite face of every half-open slot in the selection.
 *
 * A through-channel is how `case_02_leakage` is authored, and there is no other
 * control that makes one across a range. Ports `syncDeleteOppositeForSelected`
 * (STL_generate.html:14386).
 */
export function syncDeleteOpposite(
  uid: string,
  side: RackSide,
  slotIndices: number[],
): SlotWriteResult {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!model || model.kind !== 'rack') return 'missing'
  const targets = syncDeleteTargets(model, side, slotIndices)
  if (!targets.length) return 'missing'
  const opposite: RackSide = side === 'front' ? 'back' : 'front'

  store.edit('syncDeleteOppositeForSelected', `${uid} ${opposite} x${targets.length}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target) return
    const faces = deletedFaces(target)
    if (!faces[opposite]) faces[opposite] = {}
    for (const index of targets) faces[opposite]![index] = true
    rackState().syncRackModelState(target)
  })
  return 'ok'
}

// ---------------------------------------------------------------------------
// Leakage zones and void-edge partitions across a range
// ---------------------------------------------------------------------------

export interface ThroughZone {
  startUnit: number
  endUnit: number
  sigma?: number
}

/** Every through-zone on the rack, from the SHARED derivation the exporter uses. */
export function throughZoneGroups(model: Construction): ThroughZone[] {
  try {
    const naming = stlNaming() as unknown as {
      getRackThroughZoneGroups?: (m: Construction) => ThroughZone[]
    }
    return naming.getRackThroughZoneGroups?.(model) ?? []
  } catch {
    return []
  }
}

/**
 * A contiguous run of open slots within a selection, on one face.
 *
 * A void run is what a partition can bound: the divider below the first slot
 * and the one above the last close the cavity off from the slots either side.
 * Runs are found within the SELECTION, not the whole rack, so a range that
 * covers half a longer gap offers the partitions at the ends of the half the
 * user actually selected.
 */
export function voidRuns(
  slotIndices: number[],
  isDeleted: (index: number) => boolean,
): { start: number; end: number }[] {
  const runs: { start: number; end: number }[] = []
  let start = -1
  for (let i = 0; i < slotIndices.length; i++) {
    const slot = slotIndices[i] as number
    if (isDeleted(slot)) {
      if (start < 0) start = slot
    } else if (start >= 0) {
      runs.push({ start, end: slotIndices[i - 1] as number })
      start = -1
    }
  }
  if (start >= 0) runs.push({ start, end: slotIndices[slotIndices.length - 1] as number })
  return runs
}

/**
 * The two dividers that close a void run off.
 *
 * `bottom` is the divider below the run's first slot, `top` the one above its
 * last. A run that reaches an end of the rack has no divider there — the rack's
 * own top or bottom panel already closes it — so that side is null.
 */
export function voidRunPartitions(
  run: { start: number; end: number },
  slotCount: number,
): { top: number | null; bottom: number | null } {
  return {
    top: run.end < slotCount - 1 ? run.end : null,
    bottom: run.start > 0 ? run.start - 1 : null,
  }
}
