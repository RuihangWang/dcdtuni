/**
 * Multi-slot selection on a rack: merge, extend, split.
 *
 * One slot holds a 1U server. Real equipment is 2U, 4U, 10U — so a rack is
 * populated by selecting a RANGE of slots and merging them, and edited by
 * selecting part of an existing server and removing that part. The rules are
 * not obvious and getting them wrong writes a rack the runner cannot mesh, so
 * they are ported as pure functions with the legacy's own vocabulary
 * (`getRackSelectionSummary` / `getRackEquipmentSelectionContext`,
 * STL_generate.html:5805, 5864).
 *
 * The four removal modes are the interesting part. Taking slots out of a
 * server can mean four different things, and only the geometry of the overlap
 * says which:
 *
 * - **full** — the selection covers the whole server: delete it.
 * - **trimStart / trimEnd** — the selection is flush with one end: shorten it.
 * - **split** — the selection is in the MIDDLE: the server becomes two, and
 *   that is destructive enough that the legacy asks first.
 */

import type { Construction } from '../schema/cfdproj'

export interface SlotSummary {
  side: 'front' | 'back'
  slotIndices: number[]
  contiguous: boolean
  hasEquipment: boolean
  allDeleted: boolean
  anyDeleted: boolean
  startUnit: number
  endUnit: number
}

export interface EquipmentLike {
  id: string
  kind?: string
  startUnit: number
  spanUnits: number
  name?: string
}

/** Inclusive last unit of a server. */
export function equipmentEndUnit(equipment: EquipmentLike): number {
  return equipment.startUnit + Math.max(1, equipment.spanUnits) - 1
}

export function summarise(
  side: 'front' | 'back',
  slotIndices: number[],
  occupants: (index: number) => EquipmentLike | null,
  isDeleted: (side: 'front' | 'back', index: number) => boolean,
): SlotSummary | null {
  if (!slotIndices.length) return null
  const sorted = [...slotIndices].sort((a, b) => a - b)
  return {
    side,
    slotIndices: sorted,
    contiguous: sorted.every((v, i) => i === 0 || v === sorted[i - 1]! + 1),
    hasEquipment: sorted.some((i) => !!occupants(i)),
    allDeleted: sorted.every((i) => isDeleted(side, i)),
    anyDeleted: sorted.some((i) => isDeleted(side, i)),
    startUnit: sorted[0]!,
    endUnit: sorted[sorted.length - 1]!,
  }
}

export type RemovalMode = 'full' | 'trimStart' | 'trimEnd' | 'split' | null

export interface SelectionContext {
  equipments: EquipmentLike[]
  singleEquipment: EquipmentLike | null
  occupiedSlotIndices: number[]
  freeSlotIndices: number[]
  canRemoveFromEquipment: boolean
  removalMode: RemovalMode
  canExtendEquipment: boolean
}

export function selectionContext(
  summary: SlotSummary | null,
  occupants: (index: number) => EquipmentLike | null,
): SelectionContext | null {
  if (!summary || !summary.slotIndices.length) return null

  const byId = new Map<string, EquipmentLike>()
  const occupied: number[] = []
  const free: number[] = []
  for (const index of summary.slotIndices) {
    const equipment = occupants(index)
    if (equipment) {
      byId.set(equipment.id, equipment)
      occupied.push(index)
    } else {
      free.push(index)
    }
  }

  const equipments = [...byId.values()]
  const single = equipments.length === 1 ? equipments[0]! : null
  const context: SelectionContext = {
    equipments,
    singleEquipment: single,
    occupiedSlotIndices: occupied,
    freeSlotIndices: free,
    canRemoveFromEquipment: false,
    removalMode: null,
    canExtendEquipment: false,
  }
  // Two servers in the selection, or a gap in it, and none of the edits below
  // has a single well-defined meaning.
  if (!single || !summary.contiguous) return context

  const eqStart = single.startUnit
  const eqEnd = equipmentEndUnit(single)
  const selStart = summary.startUnit
  const selEnd = summary.endUnit

  if (occupied.length === summary.slotIndices.length) {
    context.canRemoveFromEquipment = true
    if (selStart <= eqStart && selEnd >= eqEnd) context.removalMode = 'full'
    else if (selStart === eqStart) context.removalMode = 'trimStart'
    else if (selEnd === eqEnd) context.removalMode = 'trimEnd'
    else context.removalMode = 'split'
  }

  // Extending needs the WHOLE server plus at least one free slot beside it:
  // a selection covering half a server and a free slot would move the server,
  // not lengthen it.
  const coversWhole = selStart <= eqStart && selEnd >= eqEnd
  const hasAdjacentFree = selStart < eqStart || selEnd > eqEnd
  if (coversWhole && hasAdjacentFree) context.canExtendEquipment = true

  return context
}

/** What the two ends of a split become. */
export function splitSpans(
  equipment: EquipmentLike,
  selStart: number,
  selEnd: number,
): { lowerSpan: number; upperStart: number; upperSpan: number } {
  const eqEnd = equipmentEndUnit(equipment)
  return {
    lowerSpan: selStart - equipment.startUnit,
    upperStart: selEnd + 1,
    upperSpan: eqEnd - selEnd,
  }
}

/** The servers occupying a rack, as the selection rules see them. */
export function equipmentList(model: Construction): EquipmentLike[] {
  const servers = ((model.constructions as { servers?: Record<string, Record<string, unknown>> })
    ?.servers ?? {}) as Record<string, Record<string, unknown>>
  return Object.values(servers).map((srv) => {
    const geometry = (srv.geometry ?? {}) as { slotPosition?: number; slotOccupation?: number }
    return {
      id: String(srv.uid ?? ''),
      kind: String(srv.type ?? srv.kind ?? ''),
      startUnit: Number(geometry.slotPosition ?? 0),
      spanUnits: Math.max(1, Number(geometry.slotOccupation ?? 1)),
      name: ((srv.meta ?? {}) as { name?: string }).name,
    }
  })
}
