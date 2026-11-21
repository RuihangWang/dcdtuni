/**
 * Rack-server opacity: the rack panel's batch slider and the per-server one.
 *
 * Spec 2026-09-17 §5.1. Storage is `server.meta.display.opacity` and nothing
 * else — the rack sidebar's second slider is a BATCH WRITER over that field
 * (one edit setting every server of the rack to the same value), never a
 * separate variable. The rack body's own slider (`modelOpacity.ts`) fades the
 * frame, blanking panels and doors only; its meshes skip anything tagged with
 * an `equipmentId`, and this module fades exactly that complementary set.
 *
 * The preview/commit seam mirrors `modelOpacity.ts` exactly, faults and all:
 *
 * - **One undo entry per drag.** The first preview takes ONE checkpoint and the
 *   rest mutate the live project in place; the commit ends the drag with a
 *   single `notifyLiveEdit`. Eighty pointer frames are one Ctrl+Z.
 * - **Materials are cloned before the preview writes to them.** The shared
 *   builder caches materials by `type|colour|opacity`, so the fade claims each
 *   mesh's materials through `ownMaterials`, the same helper the model fade
 *   uses — two knobs over one scene must not fight over a shared material.
 * - **The value is read back AFTER the checkpoint.** `pushUndoCheckpoint`
 *   replaces the live project with a clone, so a server record captured before
 *   it belongs to the undo snapshot, not to the screen.
 */

import type * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import {
  applyModelOpacityState,
  averageOpacityPercent,
  clampOpacityPercent,
  commitMultiOpacity,
  fadeMeshMaterials,
  opacityPreviewPending,
  percentToOpacity,
  previewMultiOpacity,
  renderRootOf,
  type MeshLike,
} from './modelOpacity'

/** A rack's server records: the uid-keyed DICT (`constructions.servers`). */
export function rackServerRecords(model: Construction | null): Record<string, ServerRecord> {
  if (!model) return {}
  return ((model.constructions as { servers?: Record<string, ServerRecord> } | undefined)
    ?.servers ?? {}) as Record<string, ServerRecord>
}

export interface ServerRecord {
  uid?: string
  meta?: { name?: string; display?: { opacity?: number } }
  [key: string]: unknown
}

/** The stored opacity of one server record, 0..1. Absent reads as opaque. */
export function serverOpacityOf(srv: ServerRecord | null | undefined): number {
  const v = srv?.meta?.display?.opacity
  return Number.isFinite(Number(v)) ? Number(v) : 1
}

/**
 * Write one opacity onto a server record IN PLACE.
 *
 * The `display` block is mutated rather than spread into a new object for the
 * same reason `applyModelOpacityState` does it: JSON.stringify writes keys in
 * insertion order and a rebuilt block would churn every saved file.
 */
export function applyServerOpacityState(srv: ServerRecord, percent: number): boolean {
  const next = percentToOpacity(percent)
  if (!Number.isFinite(next)) return false
  const meta = srv.meta ?? (srv.meta = {})
  const display = meta.display ?? (meta.display = {})
  display.opacity = next
  return true
}

/**
 * The batch slider's position: the common value when every server agrees,
 * otherwise the MEAN plus a `mixed` flag the panel turns into a hint.
 *
 * The mean reuses `averageOpacityPercent`'s definition (it reads the same
 * `meta.display.opacity` path), so a rack whose servers are 100/20 shows 60,
 * exactly as a multi-selection of models would.
 */
export function rackServersSliderPercent(model: Construction | null): {
  percent: number
  mixed: boolean
} {
  const servers = Object.values(rackServerRecords(model))
  if (!servers.length) return { percent: 100, mixed: false }
  const values = servers.map((s) => Math.round(serverOpacityOf(s) * 100))
  const first = values[0]!
  const mixed = values.some((v) => v !== first)
  // The mean is `averageOpacityPercent` itself — a server record carries its
  // opacity on the same `meta.display.opacity` path a model does, so the group
  // slider and this one answer the same question the same way.
  return {
    percent: averageOpacityPercent(servers as unknown as Construction[]),
    mixed,
  }
}

/**
 * Fade ONLY the meshes of the given servers.
 *
 * The inverse of the filter `applyOpacityToMeshes` gained: a server mesh is one
 * the builder tagged with `userData.equipmentId`, and the batch call passes
 * every uid of the rack while the per-server call passes one.
 */
export function applyServerOpacityToMeshes(
  root: THREE.Object3D | null,
  equipmentIds: ReadonlySet<string>,
  percent: number,
): void {
  const opacity = percentToOpacity(percent)
  if (!root || !Number.isFinite(opacity) || !equipmentIds.size) return
  root.traverse((child) => {
    const mesh = child as MeshLike
    const ud = (mesh.userData ?? {}) as { equipmentId?: string }
    if (!ud.equipmentId || !equipmentIds.has(ud.equipmentId)) return
    fadeMeshMaterials(mesh, opacity)
  })
}

/**
 * Whether the current drag has already taken its undo checkpoint.
 *
 * Module-level and SEPARATE from `modelOpacity`'s flag: the two sliders are
 * different knobs over different fields, and a drag of one must not swallow the
 * other's checkpoint.
 */
let previewCheckpointPushed = false

/** Test seam — a leaked flag would make the next drag skip its checkpoint. */
export function resetServerOpacityPreview(): void {
  previewCheckpointPushed = false
}

export function serverOpacityPreviewPending(): boolean {
  return previewCheckpointPushed
}

function serverUidsOf(rackUid: string): string[] {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return []
  return Object.entries(rackServerRecords(model)).map(
    ([key, srv]) => String(srv.uid ?? key),
  )
}

function fadeServers(rackUid: string, uids: readonly string[], percent: number): void {
  applyServerOpacityToMeshes(renderRootOf(rackUid), new Set(uids), percent)
}

// ---------------------------------------------------------------------------
// The batch knob: every server of one rack, one edit
// ---------------------------------------------------------------------------

/** `oninput`: fade now, commit later. */
export function previewRackServersOpacity(rackUid: string, percent: number): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.pushUndoCheckpoint('rackServersOpacityChange')
    previewCheckpointPushed = true
  }
  // Re-read AFTER the checkpoint: it replaced the live project with a clone.
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return
  store.mutateLive(() => {
    for (const srv of Object.values(rackServerRecords(model))) {
      applyServerOpacityState(srv, percent)
    }
  }, { scene: false })
  fadeServers(rackUid, serverUidsOf(rackUid), percent)
}

/** `onchange`: end the drag and log it as one operation. */
export function commitRackServersOpacity(rackUid: string, percent: number): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    // No preview ran — a programmatic set (the automation contract) or a
    // browser that fires `change` alone. One plain undoable edit.
    store.edit('updateRackServersOpacity', `${rackUid} servers ${clampOpacityPercent(percent)}%`, (draft) => {
      const model = store.findConstruction(draft, rackUid)?.model
      if (!model || model.kind !== 'rack') return
      for (const srv of Object.values(rackServerRecords(model))) {
        applyServerOpacityState(srv, percent)
      }
    }, { sceneUids: [rackUid] })
    return
  }
  previewCheckpointPushed = false
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (model && model.kind === 'rack') {
    store.mutateLive(() => {
      for (const srv of Object.values(rackServerRecords(model))) {
        applyServerOpacityState(srv, percent)
      }
    }, { scene: false })
  }
  // The checkpoint above the drag already holds the pre-drag project, so this
  // only marks it dirty and re-renders — the rebuild reads the committed
  // per-server values back and puts the shared materials in place.
  store.notifyLiveEdit('updateRackServersOpacity', `${rackUid} servers ${clampOpacityPercent(percent)}%`, { sceneUids: [rackUid] })
}

// ---------------------------------------------------------------------------
// The batch knob over a multi-selection: every server of every selected rack
// ---------------------------------------------------------------------------

/** The racks of a selection that hold servers (an empty rack has no knob). */
export function racksWithServers(models: readonly Construction[]): Construction[] {
  return models.filter((m) => m.kind === 'rack' && Object.keys(rackServerRecords(m)).length > 0)
}

/** The selection's server slider: `rackServersSliderPercent` over all their servers. */
export function racksServersSliderPercent(models: readonly Construction[]): { percent: number; mixed: boolean } {
  const servers = models.flatMap((m) => Object.values(rackServerRecords(m)))
  if (!servers.length) return { percent: 100, mixed: false }
  const values = servers.map((s) => Math.round(serverOpacityOf(s) * 100))
  return {
    percent: averageOpacityPercent(servers as unknown as Construction[]),
    mixed: values.some((v) => v !== values[0]),
  }
}

function writeRacksServers(project: Parameters<typeof store.findConstruction>[0], rackUids: readonly string[], percent: number) {
  for (const uid of rackUids) {
    const model = store.findConstruction(project, uid)?.model
    if (!model || model.kind !== 'rack') continue
    for (const srv of Object.values(rackServerRecords(model))) applyServerOpacityState(srv, percent)
  }
}

/** `oninput`: one checkpoint for the whole selection, fade now, commit later. */
export function previewRacksServersOpacity(rackUids: readonly string[], percent: number): void {
  if (!rackUids.length || !Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.pushUndoCheckpoint('racksServersOpacityChange')
    previewCheckpointPushed = true
  }
  const project = store.getProject()
  if (!project) return
  store.mutateLive(() => writeRacksServers(project, rackUids, percent), { scene: false })
  for (const uid of rackUids) fadeServers(uid, serverUidsOf(uid), percent)
}

/** `onchange`: end the drag and log it as one operation. */
export function commitRacksServersOpacity(rackUids: readonly string[], percent: number): void {
  if (!rackUids.length || !Number.isFinite(percentToOpacity(percent))) return
  const label = `${rackUids.length} racks servers ${clampOpacityPercent(percent)}%`
  if (!previewCheckpointPushed) {
    store.edit('updateRacksServersOpacity', label, (draft) => writeRacksServers(draft, rackUids, percent), { sceneUids: rackUids })
    return
  }
  previewCheckpointPushed = false
  const project = store.getProject()
  if (project) store.mutateLive(() => writeRacksServers(project, rackUids, percent), { scene: false })
  store.notifyLiveEdit('updateRacksServersOpacity', label, { sceneUids: rackUids })
}

/**
 * The multi-selection's GROUP slider: the models AND the servers of the
 * selected racks follow one value.
 *
 * Unlike the one-rack inspector, where the rack slider spares the servers so
 * the cabinet can be faded to show them, a group is usually a select-all that
 * should fade as a whole; the server-only slider under it still sets them apart.
 * One checkpoint for both — `previewMultiOpacity` takes it and this rides on
 * the same drag, so one undo restores the models and their servers together.
 */
export function previewGroupOpacity(uids: readonly string[], rackUids: readonly string[], percent: number): void {
  previewMultiOpacity(uids, percent)
  const project = store.getProject()
  if (!project || !rackUids.length || !opacityPreviewPending()) return
  store.mutateLive(() => writeRacksServers(project, rackUids, percent), { scene: false })
  for (const uid of rackUids) fadeServers(uid, serverUidsOf(uid), percent)
}

export function commitGroupOpacity(uids: readonly string[], rackUids: readonly string[], percent: number): void {
  if (!uids.length || !Number.isFinite(percentToOpacity(percent))) return
  if (!opacityPreviewPending()) {
    store.edit('updateMultiOpacity', `${uids.length} models ${clampOpacityPercent(percent)}%`, (draft) => {
      for (const uid of uids) {
        const hit = store.findConstruction(draft, uid)
        if (hit) applyModelOpacityState(hit.model, percent)
      }
      writeRacksServers(draft, rackUids, percent)
    }, { sceneUids: [...uids, ...rackUids] })
    return
  }
  const project = store.getProject()
  if (project && rackUids.length) store.mutateLive(() => writeRacksServers(project, rackUids, percent), { scene: false })
  commitMultiOpacity(uids, percent)
}

// ---------------------------------------------------------------------------
// The per-server knob: one record, same seam
// ---------------------------------------------------------------------------

function findServer(model: Construction | null, serverUid: string): ServerRecord | null {
  const servers = rackServerRecords(model)
  for (const [key, srv] of Object.entries(servers)) {
    if (String(srv.uid ?? key) === serverUid) return srv
  }
  return null
}

/** `oninput`: fade one server now, commit later. */
export function previewServerOpacity(rackUid: string, serverUid: string, percent: number): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.pushUndoCheckpoint('serverOpacityChange')
    previewCheckpointPushed = true
  }
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  const srv = findServer(model ?? null, serverUid)
  if (!srv) return
  store.mutateLive(() => {
    applyServerOpacityState(srv, percent)
  }, { scene: false })
  fadeServers(rackUid, [serverUid], percent)
}

/** `onchange`: end the drag and log it as one operation. */
export function commitServerOpacity(rackUid: string, serverUid: string, percent: number): void {
  if (!Number.isFinite(percentToOpacity(percent))) return
  if (!previewCheckpointPushed) {
    store.edit('updateServerOpacity', `${rackUid}/${serverUid} ${clampOpacityPercent(percent)}%`, (draft) => {
      const model = store.findConstruction(draft, rackUid)?.model
      const srv = findServer(model ?? null, serverUid)
      if (srv) applyServerOpacityState(srv, percent)
    }, { sceneUids: [rackUid] })
    return
  }
  previewCheckpointPushed = false
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  const srv = findServer(model ?? null, serverUid)
  if (srv) {
    store.mutateLive(() => {
      applyServerOpacityState(srv, percent)
    }, { scene: false })
  }
  store.notifyLiveEdit('updateServerOpacity', `${rackUid}/${serverUid} ${clampOpacityPercent(percent)}%`, { sceneUids: [rackUid] })
}
