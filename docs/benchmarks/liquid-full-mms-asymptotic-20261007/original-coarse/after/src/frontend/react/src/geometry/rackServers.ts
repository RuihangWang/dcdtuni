/**
 * Adding a server to a rack by typing where it goes.
 *
 * The viewport route — click the U-slot, press the kind — needs the exact slot
 * to be visible and hittable, which on a 42U rack seen from across the room it
 * is not. A datasheet says "8 nodes from U12, 2U each", and this is the panel
 * that takes that.
 *
 * The placement rules are the legacy's `addRackServerQuick`
 * (STL_generate.html:14151), and the one worth stating is the CLAMP: a span
 * running off the top of the rack is shortened to fit rather than refused,
 * because the user asked for a server starting at that unit and the rack's
 * height is the constraint they are already looking at.
 */

import type { Construction } from '../schema/cfdproj'
import { equipmentEndUnit, equipmentList, type EquipmentLike } from './rackSelection'
import {
  mergeSlotsIntoEquipment,
  nextEquipmentUid,
  resolveServerDepth,
  wouldConflictAtDepth,
  type EquipmentKind,
  type ServerDepthOffsets,
} from './rackSlots'
import * as store from '../projectStore'

export interface RackServerRow extends EquipmentLike {
  /** Inclusive last unit, so the row can print U12–U13 without recomputing. */
  endUnit: number
}

/** The rack's servers, in mounting order. */
export function rackServerRows(model: Construction | null): RackServerRow[] {
  if (!model || model.kind !== 'rack') return []
  return equipmentList(model)
    .map((e) => ({ ...e, endUnit: equipmentEndUnit(e) }))
    .sort((a, b) => a.startUnit - b.startUnit)
}

/** How many U-slots the rack has. */
export function rackSlotCount(model: Construction | null): number {
  const slot = (model?.geometry as { slot?: number } | undefined)?.slot
  return Math.max(0, Math.round(Number(slot ?? 0)))
}

export type QuickAddError = 'out-of-range' | 'blocked' | 'occupied' | 'missing' | 'depth-range'

export type QuickAddPlan =
  | { ok: true; indices: number[]; clamped: boolean }
  | { ok: false; error: QuickAddError }

/**
 * Which slots a quick-add would fill.
 *
 * `startUnit0` is 0-based — the panel shows U1 and passes 0 — matching
 * `addRackServerQuick`'s own convention, which the 2D editor already relies on.
 *
 * The `occupied` predicate answers "is a server already at this slot a reason
 * to refuse", and since the stacked-servers spec (2026-09-17 §4.4) that is
 * CONFLICT-AWARE: a covering server at a disjoint depth does not block. The
 * planner stays pure — the caller decides, and receives the candidate's
 * resolved `depth` back so a per-slot check can be made against it.
 */
export function planQuickAdd(
  slotCount: number,
  startUnit0: number,
  spanUnits: number,
  occupied: (index: number, depth?: ServerDepthOffsets) => boolean,
  depth?: ServerDepthOffsets,
): QuickAddPlan {
  const slots = Math.max(0, Math.round(slotCount))
  const start = Math.max(0, Math.trunc(Number(startUnit0) || 0))
  if (start >= slots) return { ok: false, error: 'out-of-range' }
  const asked = Math.max(1, Math.trunc(Number(spanUnits) || 0))
  // Shortened, not refused: the rack's top is a constraint the user can see.
  const span = Math.min(asked, slots - start)
  if (span < 1) return { ok: false, error: 'blocked' }
  for (let offset = 0; offset < span; offset++) {
    if (occupied(start + offset, depth)) return { ok: false, error: 'occupied' }
  }
  return {
    ok: true,
    indices: Array.from({ length: span }, (_, offset) => start + offset),
    clamped: span < asked,
  }
}

export type QuickAddResult =
  | { ok: true; clamped: boolean; uid: string }
  | { ok: false; error: QuickAddError }

/**
 * Put one server in the rack at an explicit start unit and span.
 *
 * The insert itself goes through `mergeSlotsIntoEquipment`, which is the same
 * path the viewport's multi-slot merge takes: it sets the rack's
 * `defaultSpanUnits` for the duration of the call, exactly as the legacy does
 * (STL_generate.html:14179), and re-runs `syncRackModelState`. A second insert
 * routine here would be a second answer to what a server record looks like.
 *
 * `depth` is the optional ABSOLUTE placement (spec §7.1): omitted — the two
 * empty boxes of the quick-add row — it defaults to the rack plane
 * (`defaultServerDepthOffsets`), which is today's behaviour. Occupancy is
 * judged per slot by the shared depth predicate, so a second server at a taken
 * U range is accepted exactly when it sits at a depth nothing there occupies.
 * On success the new record's uid comes back: the caller cannot diff the dict
 * to find it once stacked servers share U ranges.
 */
export function addRackServerQuick(
  uid: string,
  startUnit0: number,
  spanUnits: number,
  kind: EquipmentKind,
  depth?: Partial<ServerDepthOffsets>,
): QuickAddResult {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !model || model.kind !== 'rack') return { ok: false, error: 'missing' }

  const offsets = resolveServerDepth(model, depth)
  if (!offsets) return { ok: false, error: 'depth-range' }

  // Per-slot refusal, judged by a 1U dry run of the shared predicate: the same
  // question `createEquipmentAtSlot` asks over the whole span, asked early so
  // the plan can refuse before anything is set up.
  const plan = planQuickAdd(
    rackSlotCount(model),
    startUnit0,
    spanUnits,
    (index) =>
      wouldConflictAtDepth(model, {
        slotPosition: index,
        slotOccupation: 1,
        inletOffset: offsets.inletOffset,
        outletOffset: offsets.outletOffset,
      }),
    offsets,
  )
  if (!plan.ok) return plan

  // The depth is ALWAYS handed on resolved, even when the caller passed none:
  // an explicit depth tells `mergeSlotsIntoEquipment` to create a new record
  // rather than extend whatever covers the range, and at a disjoint depth that
  // is the stacked-server case this exists for.
  const newUid = nextEquipmentUid(project)
  const result = mergeSlotsIntoEquipment(uid, 'front', plan.indices, kind, offsets)
  if (result === 'ok') {
    // `createEquipmentAtSlot` mints its uid from the pre-edit project, which
    // nothing between the prediction and the insert has changed. Verify rather
    // than trust: a dropped record must not be reported as a created one.
    const after = store.getProject()
    const target = after ? store.findConstruction(after, uid)?.model : null
    const servers = ((target?.constructions as { servers?: Record<string, unknown> } | undefined)
      ?.servers ?? {}) as Record<string, unknown>
    if (!servers[newUid]) return { ok: false, error: 'occupied' }
    return { ok: true, clamped: plan.clamped, uid: newUid }
  }
  if (result === 'occupied' || result === 'wrong-type') return { ok: false, error: 'occupied' }
  if (result === 'missing') return { ok: false, error: 'missing' }
  if (result === 'depth-range') return { ok: false, error: 'depth-range' }
  return { ok: false, error: 'blocked' }
}

// ---------------------------------------------------------------------------
// Per-server CFD boundary conditions
// ---------------------------------------------------------------------------

/** A rack's servers, keyed by equipment id. */
export function rackServersOf(model: Construction): Record<string, Record<string, unknown>> {
  return ((model.constructions as { servers?: Record<string, Record<string, unknown>> })?.servers ??
    {}) as Record<string, Record<string, unknown>>
}

/**
 * Write one boundary-condition field on one server.
 *
 * Lives here rather than inside the inspector because it has a SECOND caller:
 * the automation contract (`window.updateRackServerCfd`), which is how
 * `/dc-builder` gives 200 racks their power and flow. Two copies of this would
 * be two definitions of what a server's BC seed is, and the one the pipeline
 * used would drift from the one a human clicking the panel gets.
 *
 * `cooling` is seeded on first write, as the legacy does (STL_generate.html
 * :10305-10314): a server that has never been edited carries no `cooling` at
 * all, and the runner reads its defaults from that absence — not from zeros.
 *
 * Returns false when the rack or the server is not there, so a caller driving
 * this from a script can tell a no-op from a write.
 */
export function updateRackServerCfd(
  rackUid: string,
  equipmentId: string,
  field: string,
  value: unknown,
): boolean {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return false
  if (!rackServersOf(model)[equipmentId]) return false

  store.edit('updateRackServerCfd', `${equipmentId}.${field}`, (draft) => {
    const target = store.findConstruction(draft, rackUid)?.model
    if (!target) return
    const srv = rackServersOf(target)[equipmentId]
    if (!srv) return
    const bag = (srv.cooling ?? (srv.cooling = { volumeFlowRate: 0.5, power: 1000 })) as Record<
      string,
      unknown
    >
    bag[field] = value
  })
  return true
}
