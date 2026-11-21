/**
 * Turning the move and rotate gizmos on and off: the batch setters the
 * multi-select panel uses, and the R / V shortcut on top of them.
 *
 * The gizmos are per-model flags in the .cfdproj (`moveTool.enabled`,
 * `rotateTool.enabled`), and the viewport draws them from those flags. So
 * "activate the gizmo" is a store write, and it goes through exactly the call
 * a button would make:
 *
 * - **One model**: `store.setMoveTool` / `store.setRotateTool`, the calls
 *   behind `#model-move-toggle` and `#model-rotate-toggle`. One undo entry,
 *   and the same move/rotate exclusion.
 * - **Several**: `setGroupMoveEnabled` / `setGroupRotateEnabled`, the calls
 *   behind `#multi-move-toggle` and `#multi-rotate-toggle`, moved here from the
 *   panel unchanged so the key and the button are one function. On when no
 *   selected model has it on, off otherwise — what the batch button shows.
 *
 * A second press turns the gizmo off. A selection the transform block does not
 * offer that gizmo for does nothing, and says so in the log: a patch, a server
 * inside a rack, a liquid pipe, a T-junction's rings (`transformSupport`).
 */

import { t } from '../i18n'
import * as store from '../projectStore'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { transformSupport, transformTarget } from './transformSupport'
import type { TreeSelection } from './treeNodes'

export type GizmoKind = 'move' | 'rotate'

type ToolFlags = {
  moveTool?: { enabled: boolean; axes?: Record<string, boolean> }
  rotateTool?: { enabled: boolean }
}

/**
 * Move on or off for every model in `uids`, as ONE undo entry.
 *
 * Turning move on turns rotate off on the same models only — two gizmos on
 * one model fight for the same drag. A model with no `moveTool` object is
 * skipped, as the batch button always has.
 */
export function setGroupMoveEnabled(uids: readonly string[], on: boolean) {
  store.pushUndoCheckpoint(on ? 'enableMultiMove' : 'disableMultiMove')
  store.mutateLive((p) => {
    for (const uid of uids) {
      const model = store.findConstruction(p, uid)?.model as unknown as ToolFlags | undefined
      if (!model) continue
      if (model.moveTool) model.moveTool.enabled = on
      if (on && model.rotateTool) model.rotateTool.enabled = false
    }
  }, { scene: false })
  store.notifyLiveEdit(on ? 'enableMultiMove' : 'disableMultiMove', `${uids.length}`)
}

/**
 * Group rotate: a ring on every selected model, each turning about its OWN
 * centre (`enableMultiRotate`, STL_generate.html:10893). Not a shared pivot —
 * that is what the toolbar's 90° buttons do, and the two are different tools.
 */
export function setGroupRotateEnabled(uids: readonly string[], on: boolean) {
  store.pushUndoCheckpoint(on ? 'enableMultiRotate' : 'disableMultiRotate')
  store.mutateLive((p) => {
    for (const uid of uids) {
      const model = store.findConstruction(p, uid)?.model as unknown as ToolFlags | undefined
      if (!model) continue
      if (model.rotateTool) model.rotateTool.enabled = on
      if (on && model.moveTool) model.moveTool.enabled = false
    }
  }, { scene: false })
  store.notifyLiveEdit(on ? 'enableMultiRotate' : 'disableMultiRotate', `${uids.length}`)
}

/** Every selected model's move-axis lock, as ONE undo entry. */
export function setGroupMoveAxis(uids: readonly string[], axis: 'x' | 'y' | 'z', on: boolean) {
  store.pushUndoCheckpoint('setMultiMoveAxis')
  store.mutateLive((p) => {
    for (const uid of uids) {
      const model = store.findConstruction(p, uid)?.model as unknown as ToolFlags | undefined
      if (model?.moveTool?.axes) model.moveTool.axes[axis] = on
    }
  }, { scene: false })
  store.notifyLiveEdit('setMultiMoveAxis', `${axis}=${on}`)
}

export function gizmoEnabled(model: Construction, kind: GizmoKind): boolean {
  const tools = model as unknown as ToolFlags
  return (kind === 'move' ? tools.moveTool?.enabled : tools.rotateTool?.enabled) === true
}

export interface GizmoSelection {
  project: CfdProject | null
  /** Every selected model, active one first. */
  uids: readonly string[]
  /** The narrower selection under the active model, if any. */
  node: TreeSelection | null
}

export type GizmoToggleResult =
  | { ok: true; enabled: boolean; uids: string[] }
  | { ok: false; reason: 'no-selection' | 'unsupported' }

function logUnsupported(kind: GizmoKind, name: string) {
  store.logAction(
    'shortcut',
    t(kind === 'move' ? 'log.move_gizmo_unsupported' : 'log.rotate_gizmo_unsupported', { name }),
  )
}

/** What R (rotate) or V (move) does to the current selection. */
export function toggleSelectionGizmo(kind: GizmoKind, sel: GizmoSelection): GizmoToggleResult {
  const project = sel.project
  if (!project) return { ok: false, reason: 'no-selection' }
  const hits = sel.uids
    .map((uid) => store.findConstruction(project, uid)?.model)
    .filter((m): m is Construction => !!m)
  if (hits.length === 0) return { ok: false, reason: 'no-selection' }

  if (hits.length === 1) {
    const model = hits[0]!
    // The node narrows the ACTIVE model; one left over from another model
    // says nothing about this one.
    const node = sel.node && sel.node.uid === model.uid ? sel.node : null
    const target = transformTarget(model, node)
    if (!target || !target.support[kind]) {
      logUnsupported(kind, model.meta?.name ?? model.uid)
      return { ok: false, reason: 'unsupported' }
    }
    const enabled = !gizmoEnabled(model, kind)
    if (kind === 'move') store.setMoveTool(model.uid, { enabled })
    else store.setRotateTool(model.uid, { enabled })
    return { ok: true, enabled, uids: [model.uid] }
  }

  // Several: the multi-select panel is what is on screen, and its buttons
  // act on the whole selection.
  if (!hits.some((m) => transformSupport(m)[kind])) {
    logUnsupported(kind, hits.map((m) => m.meta?.name ?? m.uid).join(', '))
    return { ok: false, reason: 'unsupported' }
  }
  const uids = hits.map((m) => m.uid)
  const enabled = !hits.some((m) => gizmoEnabled(m, kind))
  if (kind === 'move') setGroupMoveEnabled(uids, enabled)
  else setGroupRotateEnabled(uids, enabled)
  return { ok: true, enabled, uids }
}

export interface GizmoShortcutContext {
  /** First-person mode is on (look or cursor mode). */
  firstPerson: boolean
  /** The 2D room editor owns the right column and its own keys. */
  room2d: boolean
  /** STL preview replaces the inspector. */
  preview: boolean
  /** Rack detail mode: the room's models are not what is on screen. */
  rackDetail: boolean
  /**
   * A viewport drag is live: the move arrows, the rings, a face handle, the
   * seed balls or a pipe waypoint.
   */
  dragging: boolean
  /** The open left tab. */
  tab: string
}

/**
 * Whether the gizmo keys are off.
 *
 * Only on the geometry tab, which is where models are placed: the other tabs'
 * right columns are mesh, solver and results panels, and a stray V there would
 * put arrows on a model nobody is editing. Off in first-person mode (its own
 * keys pass R and V straight through), in the 2D room editor (it borrows the
 * right column and owns the keyboard), in STL preview and in rack detail (the
 * inspector is replaced in both).
 *
 * Off while a drag is live, too. The toggle is a `store.edit`: it would push
 * the model's uncommitted mid-drag position onto the undo stack and replace
 * the project, and the drag — which holds the models it captured at drag
 * start — would go on writing into the snapshot the edit just replaced. The
 * model stops following the pointer, and the first Ctrl+Z after the drop
 * lands on a position that was never committed.
 */
export function gizmoShortcutBlocked(ctx: GizmoShortcutContext): boolean {
  return (
    ctx.dragging ||
    ctx.firstPerson ||
    ctx.room2d ||
    ctx.preview ||
    ctx.rackDetail ||
    ctx.tab !== 'geometry'
  )
}
