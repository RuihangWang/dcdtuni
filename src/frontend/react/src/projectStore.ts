/**
 * The edited project, with undo/redo.
 *
 * The legacy editor spreads this across a global `state` object and a hand-written
 * rule that every state-mutating `window.*` function must call
 * `pushUndoCheckpoint()` FIRST and `logOperation()` after. That rule is enforced
 * by review only, so a function that forgets it silently breaks undo. Here the
 * checkpoint is not something a mutation can forget: `edit()` takes the snapshot
 * itself, and there is no other way in.
 *
 * A module-level store rather than App state because three separate subtrees
 * write to it (tree, inspector, toolbar) and threading a setter through all of
 * them is how the copies drift — the same reasoning as the Omniverse demo's
 * roiStore.
 *
 * Snapshots are structured clones of plain JSON. That is only sound because the
 * project stays JSON-serialisable — no functions, no DOM nodes, no Three.js
 * objects — which is the legacy editor's rule 5 and is worth keeping.
 */

import type { CfdProject, Construction, Vec3 } from './schema/cfdproj'
import { rackState, reviveKinds, stateV4 } from './legacy/cfdprojModules'
import { liveProject, transientModel, transientOf, type EditOutcome } from './transient/model'
import { normalizeRetiredMeshEngine } from './run/meshConfig'
import { normalizeWithdrawnPreset } from './run/solverPresetTable'
import { t } from './i18n'
import { sheetDeadAxis } from './geometry/sheetSizeFields'
import { placeDuplicateWithoutOverlap, type Box } from './geometry/duplicatePlacement'
import { renumberNestedIds } from './geometry/duplicateIds'
import { withinProjectBounds, type ProjectBounds } from './geometry/faceDragMath'

export interface OperationLogEntry {
  at: number
  action: string
  detail: string
}

/** Matches the legacy adaptive depth: big projects keep fewer snapshots. */
function maxUndoFor(project: CfdProject | null): number {
  const n = project ? countConstructions(project) : 0
  if (n > 200) return 30
  if (n > 100) return 60
  if (n > 50) return 100
  return 200
}

function countConstructions(project: CfdProject): number {
  let n = 0
  for (const bucket of Object.values(project.constructions ?? {})) {
    if (bucket) n += Object.keys(bucket).length
  }
  return n
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

/**
 * Deep-copy a project and put back what `structuredClone` throws away.
 *
 * The `kind` discriminator is non-enumerable on purpose — it must never
 * serialise into a saved .cfdproj — and `structuredClone` drops non-enumerable
 * properties. Without this, one undo checkpoint silently strips `kind` from
 * every model, and the next `syncRackModelState` early-returns on
 * `kind !== 'rack'` and does nothing at all.
 */
function cloneProject(project: CfdProject): CfdProject {
  return reviveKinds(structuredClone(project))
}

interface StoreState {
  project: CfdProject | null
  /** Where the project came from, so a save knows the path. */
  path: string | null
  dirty: boolean
}

/**
 * How many times the app has been told the project changed.
 *
 * `edit` swaps in a new project object, so a `useMemo(..., [project])` sees the
 * change. `notifyLiveEdit` does NOT — it exists for code that mutated in place
 * — so every such memo goes stale after one. That is not theoretical: the
 * shared liquid modules add a model with `addModel()` (a `mutateLive`) and then
 * call `updateUI()`, and the feature tree kept showing the old model count
 * until the next real edit; `setRefinementZoneVisible` works around the same
 * thing by hand.
 *
 * So this is the dependency to use ALONGSIDE `project` in anything derived from
 * it. Swapping the project reference on every live edit would fix those memos
 * too, and would also re-run every `[project]` EFFECT in the viewport — a scene
 * rebuild per opacity nudge — which is why the counter is separate.
 */
let stateRevision = 0
// A prepared run follows committed document inputs, not view/log/save repaint.
// Keep the rendering revision independent so existing subscribers still repaint.
let documentEditRevision = 0

export function documentInputRevision(): number {
  return documentEditRevision
}

/** Bumped whenever listeners are told; see `stateRevision`. */
export function revision(): number {
  return stateRevision
}

/**
 * A fresh, empty project — never null.
 *
 * The legacy editor has no "no project" state: `state` is initialised at load
 * from `getInitialStateV4()` and every panel assumes it exists. React started
 * from null, and the difference was not theoretical — the 房间 2D 平面设计
 * button called `setRoom2d(true)` and then rendered nothing, because the editor
 * was gated on `project`. Designing a room is exactly what you do BEFORE you
 * have one.
 *
 * `path` stays null: an in-memory project has no file yet, and a save has to
 * ask where to put it.
 */
function freshProject(): CfdProject {
  return liveProject(stateV4().normalizeStateV4(
    stateV4().newDocumentV4()
  ) as unknown as CfdProject, 0)
}

// ---------------------------------------------------------------------------
// Transient playhead (cfdproj v5)
// ---------------------------------------------------------------------------
//
// The live project is the document MATERIALISED at the playhead: every
// time-variable field (the parameter registry) holds its track's value at
// `playhead`, so every existing reader keeps working. The tracks in
// `project.transient` are the only truth — edits come back as keyframes
// (commitEdit), undo/redo and playhead moves re-materialise, and a save
// strips the fields again (io/projectFiles.projectForSave).

let playhead = 0
let lastRejection: Exclude<EditOutcome, { ok: true }> | null = null
let rejections = 0
const playheadListeners = new Set<() => void>()

// While the scene is frozen, the last accepted state is kept as the reference
// an in-place (legacy) mutation is judged against: `notifyLiveEdit` cannot see
// what changed, only that the scene no longer matches. Held only off t = 0, so
// t = 0 pays nothing.
let frozenRef: CfdProject | null = null
let frozenStatic: string | null = null

/** Off t = 0 in a transient project, geometry and static fields are frozen. */
export function sceneFrozen(): boolean {
  return playhead > 0 && transientOf(state.project).mode === 'transient'
}

/** Counts refused edits; a field that committed can compare it to learn it was refused. */
export function rejectionSeq(): number {
  return rejections
}

function refuse(action: string, outcome: Exclude<EditOutcome, { ok: true }>) {
  lastRejection = outcome
  rejections++
  logOperation('editRejected', `${action}: ${outcome.reason} at t=${playhead}s`)
  // Nothing changed, but the log line and whoever shows the reason repaint now.
  emit()
}

function refreshFrozenRef() {
  if (state.project && sceneFrozen()) {
    frozenRef = cloneProject(state.project)
    frozenStatic = transientModel().staticText(state.project)
  } else {
    frozenRef = null
    frozenStatic = null
  }
}

/** The time (s) the live project is materialised at. 0 = the start / steady state. */
export function playheadTime(): number {
  return playhead
}

export function subscribePlayhead(fn: () => void): () => void {
  playheadListeners.add(fn)
  return () => { playheadListeners.delete(fn) }
}

/** Why the last edit was refused (geometry/static fields are frozen off t = 0), or null. */
export function lastEditRejection() {
  return lastRejection
}

/** Move the playhead: snapped to the grid, clamped to the timeline, no undo, not dirty. */
export function setPlayhead(t: number) {
  if (!state.project) return
  const tr = transientOf(state.project)
  const g = tr.gridS > 0 ? tr.gridS : 1
  const snapped = Math.min(tr.durationS, Math.max(0, Math.round(t / g) * g))
  // A steady project has one time: the start.
  const next = tr.mode === 'transient' ? Math.round(snapped * 1e9) / 1e9 : 0
  if (next === playhead) return
  playhead = next
  state = { ...state, project: transientModel().materializeAt(cloneProject(state.project), playhead) }
  refreshFrozenRef()
  playheadListeners.forEach((fn) => fn())
  emit()
}

let state: StoreState = { project: freshProject(), path: null, dirty: false }
let undoStack: CfdProject[] = []
let redoStack: CfdProject[] = []
const log: OperationLogEntry[] = []

type Listener = () => void
const listeners = new Set<Listener>()
const projectLoadListeners = new Set<Listener>()

/** A document load is distinct from edits and log updates. */
export function subscribeProjectLoad(fn: Listener): () => void {
  projectLoadListeners.add(fn)
  return () => { projectLoadListeners.delete(fn) }
}

function emit(documentChanged = false) {
  if (documentChanged) documentEditRevision += 1
  stateRevision += 1
  listeners.forEach((fn) => fn())
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getProject(): CfdProject | null {
  return state.project
}

/** The project's name for display: its own name, else its file's, else its case directory's. */
export function projectDisplayName(): string {
  const named = String((state.project as { name?: unknown } | null)?.name ?? '').trim()
  if (named) return named
  const leaf = (p: string | null | undefined) => (p ? p.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? '' : '')
  const file = leaf(getPath()).replace(/\.(cfdproj|json)$/i, '')
  if (file) return file
  const casePath = String((state.project as { meta?: { casePath?: unknown } } | null)?.meta?.casePath ?? '')
  return leaf(casePath)
}

export function getPath(): string | null {
  return state.path
}

export function isDirty(): boolean {
  return state.dirty
}

export function getLog(): readonly OperationLogEntry[] {
  return log
}

export function canUndo(): boolean {
  return undoStack.length > 0
}

export function canRedo(): boolean {
  return redoStack.length > 0
}

/** How many undo steps are held. Shown in the log header. */
export function undoDepth(): number {
  return undoStack.length
}

/** Replace the whole project (a load). Clears history — there is nothing to undo to. */
export function loadProject(project: CfdProject, path: string | null) {
  // A project read from JSON has no `kind` at all — the V4 file derives it from
  // the bucket, so it has to be attached on the way in. It is made live first:
  // brought to v5 and materialised at t = 0, so the rack pass below sees the
  // fields it normalises.
  // Legacy empty-UID files receive identity in memory once; saving persists it.
  // Existing accepted project/run identities are never rewritten.
  const revived = reviveKinds(stateV4().withProjectUid(liveProject(project, 0)))
  // V4/V5 loaders preserve already-current documents. Bring a legacy floor
  // footprint into geometry here as well, before any render or STL export.
  for (const floor of Object.values(revived.constructions?.floors ?? {})) {
    stateV4().upgradeFloorPolygon(floor)
  }
  // A migration like the rack pass below, not an edit (E8c): a project saved
  // before transient steps converged gets the per-step set and maximum once.
  stateV4().upgradeSimConvergence((revived as { runDefaults?: { simulationConfig?: unknown } }).runDefaults?.simulationConfig)
  // Also a migration: the meshgen engine was retired on 2026-10-26, so a file
  // that still names it opens on dcCart, with a line in the operation log
  // below. The runner and the scripted API refuse meshgen instead.
  const retiredMeshEngine = normalizeRetiredMeshEngine(revived)
  // The same kind of migration (owner 2026-11-12): the 平衡 and 高精度 solver
  // presets are withdrawn, so a file saved with either opens on 快速 — schemes,
  // relaxation factors and corrector count rewritten as a press of that button
  // would — with a line in the operation log. The headless compiler loads
  // through here too, so the scripted API exports the rewritten settings.
  const withdrawnPreset = normalizeWithdrawnPreset(revived)
  // One normalizer pass per rack BEFORE the document goes live (stacked-servers
  // spec §3.2): the load-time backfill is a migration, not an edit — it turns
  // retired rack-relative server extras into absolute setbacks, backfills
  // per-server opacity from the rack, clamps to §3.1 and rebuilds
  // `constructions.serverConflicts`. Without it a document saved before the
  // absolute fields existed keeps servers the exporters read as depth 0 until
  // some unrelated rack edit happens to sync one. It must not mark the project
  // dirty and must not push undo: there is nothing to undo TO on a fresh load,
  // and "unchanged on open" is what the migration promises.
  for (const bucket of Object.values(revived.constructions ?? {})) {
    if (!bucket || typeof bucket !== 'object') continue
    for (const model of Object.values(bucket)) {
      if (!model || model.kind !== 'rack') continue
      // Guarded per rack: a document whose rack is too malformed to normalise
      // (no geometry.size, say) still LOADS — the rack then fails on its first
      // edit exactly as it did before this pass existed, and one broken model
      // cannot take the whole project down at open.
      try {
        rackState().syncRackModelState(model)
      } catch {
        /* left unsynced; the first edit of this rack will surface the defect */
      }
    }
  }
  state = { project: revived, path, dirty: false }
  playhead = 0
  lastRejection = null
  refreshFrozenRef()
  undoStack = []
  redoStack = []
  log.length = 0
  logOperation('loadProject', path ?? '(in memory)')
  if (retiredMeshEngine) logOperation('loadProject', t('log.meshgen_retired'))
  if (withdrawnPreset) logOperation('loadProject', t('log.preset_withdrawn'))
  projectLoadListeners.forEach((fn) => fn())
  emit(true)
}

/**
 * The dock's scrollback cap, newest first.
 *
 * Job streams reach the dock in batches (see run/jobLogMirror), so this is
 * minutes of a run rather than lines of it; 500 evaporated inside one snappy
 * iteration burst and left the dock showing only the verdict.
 */
const MAX_LOG_ENTRIES = 2000

/** A value for one operation-log line: scalars as they are, an object or array by its shape, never its whole body. */
function logValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.length}]`
  if (value && typeof value === 'object') {
    const keys = Object.keys(value)
    return `{${keys.slice(0, 6).join(', ')}${keys.length > 6 ? ', …' : ''}}`
  }
  const text = JSON.stringify(value) ?? String(value)
  return text.length > 120 ? `${text.slice(0, 117)}…` : text
}

export function logOperation(action: string, detail = '') {
  log.unshift({ at: Date.now(), action, detail })
  if (log.length > MAX_LOG_ENTRIES) log.length = MAX_LOG_ENTRIES
}

/**
 * Log a line AND repaint the log panel.
 *
 * `logOperation` only pushes onto the array; the repaint comes from `emit()`,
 * which every PROJECT mutation calls after logging. An action that changes no
 * project — a post-processing render, a finished mesh run — has nothing to
 * emit, so its line sits unseen until an unrelated edit happens to re-render
 * the panel. This is that pair, without the dirty flag `notifyLiveEdit` sets.
 */
export function logAction(action: string, detail = '') {
  logOperation(action, detail)
  emit()
}

/**
 * The models a committed edit changed, when its caller knows them exactly and
 * the change is local to them (a display opacity, say). The viewport then
 * re-makes only those models instead of the whole room. Bound to the project
 * object the edit produced: any later document (another edit, undo, a load)
 * no longer matches, so a stale hint can never narrow a full rebuild.
 */
let sceneHint: { project: CfdProject; uids: readonly string[] } | null = null

/** The models the edit that produced `project` named, or null for a whole-scene change. */
export function sceneUidsFor(project: CfdProject | null): readonly string[] | null {
  return project && sceneHint?.project === project ? sceneHint.uids : null
}

export interface EditOptions {
  /** Every model the edit changed, all of them local; see `sceneUidsFor`. */
  sceneUids?: readonly string[]
}

/**
 * Apply a mutation, taking the undo checkpoint first.
 *
 * `mutate` receives a DEEP COPY and edits it in place. Working on a copy is what
 * makes the checkpoint honest: the snapshot on the undo stack cannot share
 * structure with the object the caller is about to change.
 */
export function edit(action: string, detail: string, mutate: (draft: CfdProject) => void, opts: EditOptions = {}): boolean {
  if (!state.project) return false
  const before = state.project
  const draft = cloneProject(before)
  mutate(draft)
  // Time-variable fields become keyframes at the playhead; off t = 0 the
  // geometry and every static field are frozen, so such an edit is refused
  // whole (the project is left exactly as it was).
  const outcome = transientModel().commitEdit(before, draft, playhead)
  if (!outcome.ok) {
    refuse(action, outcome)
    emit()
    return false
  }
  lastRejection = null
  transientModel().materializeAt(draft, playhead)

  undoStack.push(before)
  const cap = maxUndoFor(draft)
  if (undoStack.length > cap) undoStack.splice(0, undoStack.length - cap)
  redoStack = []

  state = { ...state, project: draft, dirty: true }
  sceneHint = opts.sceneUids?.length ? { project: draft, uids: [...opts.sceneUids] } : null
  refreshFrozenRef()
  logOperation(action, detail)
  emit(true)
  return true
}

export function undo() {
  const prev = undoStack.pop()
  if (!prev || !state.project) return
  redoStack.push(state.project)
  state = { ...state, project: transientModel().materializeAt(prev, playhead), dirty: true }
  refreshFrozenRef()
  logOperation('undo', '')
  emit(true)
}

export function redo() {
  const next = redoStack.pop()
  if (!next || !state.project) return
  undoStack.push(state.project)
  state = { ...state, project: transientModel().materializeAt(next, playhead), dirty: true }
  refreshFrozenRef()
  logOperation('redo', '')
  emit(true)
}

export function markSaved(path: string) {
  state = { ...state, path, dirty: false }
  logOperation('saveProject', path)
  emit()
}

// ---------------------------------------------------------------------------
// Construction helpers — the mutations the tree and inspector need
// ---------------------------------------------------------------------------

/** Find a construction and the bucket holding it. */
export function findConstruction(
  project: CfdProject,
  uid: string,
): { bucket: string; model: Construction } | null {
  for (const [bucket, items] of Object.entries(project.constructions ?? {})) {
    if (!items) continue
    const model = items[uid]
    if (model) return { bucket, model }
  }
  return null
}

export function setEnabled(uid: string, enabled: boolean) {
  setEnabledMany([uid], enabled)
}

/**
 * Show or hide a batch of models in ONE undo step.
 *
 * Hiding is how a crowded room is worked in — select a row of racks, hide them,
 * look behind — and the legacy's checkbox toggles the whole selection when the
 * clicked model is part of one (`toggleModelEnabled`, STL_generate.html:12965).
 * Twenty models must be one entry in the undo stack, not twenty: undoing a
 * gesture the user made once has to take one press.
 */
export function setEnabledMany(uids: readonly string[], enabled: boolean) {
  if (!uids.length) return
  const label = uids.length === 1 ? `${uids[0]} -> ${enabled}` : `${uids.length} models -> ${enabled}`
  edit('toggleModelEnabled', label, (draft) => {
    for (const uid of uids) {
      const hit = findConstruction(draft, uid)
      if (hit) hit.model.meta = { ...hit.model.meta, enabled }
    }
  })
}

export function renameModel(uid: string, name: string) {
  const trimmed = name.trim()
  if (!trimmed) return
  edit('renameModel', `${uid} -> ${trimmed}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (hit) hit.model.meta = { ...hit.model.meta, name: trimmed }
  })
}

export function removeModel(uid: string) {
  edit('removeModel', uid, (draft) => {
    for (const items of Object.values(draft.constructions ?? {})) {
      if (items && items[uid]) {
        delete items[uid]
        return
      }
    }
  })
}

/** Next free `<name>_copy`, `<name>_copy2`, … so duplicates never collide. */
function uniqueName(project: CfdProject, base: string): string {
  const taken = new Set<string>()
  for (const items of Object.values(project.constructions ?? {})) {
    if (!items) continue
    for (const m of Object.values(items)) if (m?.meta?.name) taken.add(m.meta.name)
  }
  let candidate = `${base}_copy`
  let n = 2
  while (taken.has(candidate)) candidate = `${base}_copy${n++}`
  return candidate
}

let uidCounter = 0
function newUid(): string {
  // `r_` so a React-created uid is distinguishable from the editor's `m_` ones
  // when reading a project someone else has to debug.
  uidCounter += 1
  return `r_${Date.now().toString(36)}_${uidCounter}`
}

/** The copy itself, applied to a draft. Shared by the single and batch paths. */
function duplicateInto(draft: CfdProject, uid: string): string | null {
  const hit = findConstruction(draft, uid)
  if (!hit) return null
  const items = draft.constructions?.[hit.bucket]
  if (!items) return null
  const copy = clone(hit.model)
  copy.uid = newUid()
  copy.meta = { ...copy.meta, name: uniqueName(draft, hit.model.meta?.name ?? 'model') }
  renumberNestedIds(draft, copy)

  // Put the copy somewhere it does not overlap anything.
  //
  // This used to be one line — shift it by its own width — which on a dense
  // room lands it inside the next rack along, and on the last rack of a row
  // lands it outside the project bounds. `placeDuplicateWithoutOverlap` walks
  // ten directions by twelve rings looking for clear space and refuses when
  // there is none; it has been in the tree since the port, tested, and imported
  // by nothing.
  //
  // A refusal has to remove the copy, because `edit()` has already taken its
  // undo snapshot and there is no way to abort a mutation from inside it. The
  // caller gets null and says so.
  const location = copy.geometry?.location
  if (location) {
    const size = copy.geometry?.size ?? { x: 0, y: 0, z: 0 }
    const others: Box[] = []
    for (const bucket of Object.values(draft.constructions ?? {})) {
      for (const model of Object.values(bucket ?? {})) {
        if (!model || model.uid === copy.uid) continue
        const g = model.geometry
        if (g?.location && g?.size) others.push({ location: g.location, size: g.size })
      }
    }
    const bounds = (draft.geometry as { bounds?: ProjectBounds } | undefined)?.bounds
    const spot = placeDuplicateWithoutOverlap({ location, size }, others, bounds)
    if (!spot) return null
    copy.geometry!.location = spot
  }

  items[copy.uid] = copy
  return copy.uid
}

export function duplicateModel(uid: string): string | null {
  let created: string | null = null
  edit('duplicateModel', uid, (draft) => {
    created = duplicateInto(draft, uid)
  })
  return created
}

/**
 * Duplicate several models as ONE undo entry.
 *
 * Calling `duplicateModel` in a loop pushes an entry per model, so undoing a
 * batch of eight takes eight presses — and the legacy's `duplicateSelectedModels`
 * pushes a single `duplicateMulti` checkpoint (STL_generate.html:13887).
 */
export function duplicateModels(uids: string[]): string[] {
  const created: string[] = []
  edit('duplicateMulti', `${uids.length} models`, (draft) => {
    for (const uid of uids) {
      const copy = duplicateInto(draft, uid)
      if (copy) created.push(copy)
    }
  })
  return created
}

/** Remove several models as ONE undo entry. */
export function removeModels(uids: string[]) {
  const wanted = new Set(uids)
  edit('deleteMulti', `${uids.length} models`, (draft) => {
    for (const items of Object.values(draft.constructions ?? {})) {
      if (!items) continue
      for (const uid of Object.keys(items)) {
        if (wanted.has(uid)) delete items[uid]
      }
    }
  })
}

/**
 * Write one geometry vector component.
 *
 * Split by field rather than taking a whole vector so a partial edit — the user
 * typed into the X box and left Y and Z alone — cannot silently write stale
 * values for the fields it did not touch.
 */
/**
 * Would this model, with this geometry, leave the project's declared bounds?
 *
 * The bounds are not decoration: `yMin` enabled at 0 is the usual one, and a
 * rack below it produces a mesh with geometry outside the domain. The DRAG
 * paths have always enforced them — the move gizmo reverts to its last valid
 * frame, drag-create clamps or cancels — and the TYPED paths did not, so the
 * same illegal state was one keystroke away in the box beside the gizmo. The
 * legacy checks both (`updateModel`, STL_generate.html:14015).
 *
 * Checked BEFORE the edit rather than reverted after it: `edit()` has taken its
 * undo snapshot by the time a mutation could be examined, so a revert inside
 * would leave an undo entry that does nothing — the legacy pops its checkpoint
 * for the same reason.
 */
function wouldLeaveBounds(
  uid: string,
  next: { location?: Vec3; size?: Vec3 },
): boolean {
  const project = state.project
  if (!project) return false
  const bounds = (project.geometry as { bounds?: ProjectBounds } | undefined)?.bounds
  if (!bounds) return false
  const model = findConstruction(project, uid)?.model
  if (!model) return false
  const geometry = model.geometry
  const location = next.location ?? (geometry?.location as Vec3 | undefined)
  const size = next.size ?? (geometry?.size as Vec3 | undefined)
  if (!location || !size) return false
  return !withinProjectBounds(location, size, bounds)
}

/**
 * Would this component write be refused?
 *
 * For editors that cannot use `setGeometryComponent` — the rack goes through
 * `editRack`, which re-derives its slot state — so they can ask the same
 * question rather than re-deriving the bounds rule.
 */
export function canSetGeometryComponent(
  uid: string,
  field: 'location' | 'size',
  axis: 'x' | 'y' | 'z',
  value: number,
): boolean {
  if (!Number.isFinite(value)) return false
  const current = state.project ? findConstruction(state.project, uid)?.model?.geometry : undefined
  const vector = { x: 0, y: 0, z: 0, ...((current?.[field] as Vec3 | undefined) ?? {}) }
  if (wouldLeaveBounds(uid, { [field]: { ...vector, [axis]: value } })) {
    return refuseOutOfBounds(uid, `${field}.${axis} = ${value}`)
  }
  return true
}

/** The refusal, logged so the operation panel says why nothing happened. */
function refuseOutOfBounds(uid: string, detail: string): false {
  logOperation('rejectOutOfBounds', `${uid} ${detail}`)
  emit()
  return false
}

export function setGeometryComponent(
  uid: string,
  field: 'location' | 'size',
  axis: 'x' | 'y' | 'z',
  value: number,
): boolean {
  if (!canSetGeometryComponent(uid, field, axis, value)) return false
  edit('setGeometry', `${uid} ${field}.${axis} = ${value}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit) return
    const geometry = hit.model.geometry ?? (hit.model.geometry = {})
    const vec = geometry[field] ?? (geometry[field] = { x: 0, y: 0, z: 0 })
    // A wall, baffle or rectangular floor is a SHEET — two in-plane dimensions
    // and no thickness. The inspector shows it only its two live boxes, so a
    // user cannot type into the third; a SCRIPT can, because this setter is on
    // the automation contract, and the value persisted into the .cfdproj
    // describing a solid that nothing draws. Held at zero here, where the value
    // lands, rather than at whichever call sites happen to exist.
    if (field === 'size' && axis === sheetDeadAxis(hit.model.kind as string | undefined)) {
      vec[axis] = 0
      return
    }
    vec[axis] = value
  })
  return true
}

/**
 * Set the rotation from Euler angles in degrees, about the model's own origin.
 *
 * Degrees, not the stored quaternion: nobody types quaternions, and the legacy
 * editor's rotate buttons are all in degrees. Order is YXZ — yaw first — because
 * yaw is the rotation these models actually use (a rack turned to face an
 * aisle), and putting it first keeps a pure yaw free of roll.
 */
/**
 * Set a model's anchor in one edit.
 *
 * Three `setGeometryComponent` calls would push three undo entries for one
 * move, so a drag would need three Ctrl+Z to put back.
 */
export function setModelLocation(
  uid: string,
  location: { x: number; y: number; z: number },
): boolean {
  if (!Number.isFinite(location.x) || !Number.isFinite(location.y) || !Number.isFinite(location.z)) {
    return false
  }
  if (wouldLeaveBounds(uid, { location })) {
    return refuseOutOfBounds(
      uid,
      `→ ${location.x.toFixed(4)}, ${location.y.toFixed(4)}, ${location.z.toFixed(4)}`,
    )
  }
  edit('moveModel', `${uid} → ${location.x.toFixed(4)}, ${location.y.toFixed(4)}, ${location.z.toFixed(4)}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit) return
    const geometry = hit.model.geometry ?? (hit.model.geometry = {})
    geometry.location = { x: location.x, y: location.y, z: location.z }
  })
  return true
}

/**
 * Snapshot the project for undo, without changing it.
 *
 * The legacy editor's `pushUndoCheckpoint`: call it BEFORE mutating in place,
 * and the mutation becomes one undoable step. React's own edits do not need it
 * — `edit()` snapshots as it goes — but the shared liquid modules mutate the
 * project directly and expect this to have happened first.
 */
export function pushUndoCheckpoint(action: string) {
  if (!state.project) return
  undoStack.push(state.project)
  const cap = maxUndoFor(state.project)
  if (undoStack.length > cap) undoStack.splice(0, undoStack.length - cap)
  redoStack = []
  // The snapshot is the project as it stands, and the mutation is about to
  // happen to that same object — so a CLONE has to become the live one, or
  // undo would restore an object the mutation had already changed.
  state = { ...state, project: cloneProject(state.project) }
  logOperation(action, 'checkpoint')
}

/**
 * Tell the app the live project changed under it.
 *
 * The counterpart to `pushUndoCheckpoint` for code that mutates in place: marks
 * the project dirty and re-renders. The legacy `updateUI()`.
 */
export function notifyLiveEdit(action = 'liveEdit', detail = '', opts: EditOptions = {}) {
  if (!state.project) return
  sceneHint = null
  // Off t = 0 the scene is frozen for in-place edits too: judged against the
  // last accepted state, a changed scene is put back whole (as `edit` refuses).
  if (frozenRef && frozenStatic !== null && transientModel().staticText(state.project) !== frozenStatic) {
    state = { ...state, project: transientModel().materializeAt(cloneProject(frozenRef), playhead) }
    refuse(action, { ok: false, reason: 'static', detail: [] })
    emit()
    return
  }
  // Legacy code mutated the live object in place: fold any time-variable field
  // it touched back into the tracks, or the next materialise would undo it.
  transientModel().syncFieldsToTracks(state.project, playhead)
  lastRejection = null
  state = { ...state, dirty: true }
  // The live object may be the clone a checkpoint made (`pushUndoCheckpoint`),
  // which the viewport sees as a new document: name what actually changed.
  if (opts.sceneUids?.length) sceneHint = { project: state.project!, uids: [...opts.sceneUids] }
  refreshFrozenRef()
  if (action !== 'liveEdit') logOperation(action, detail)
  emit(true)
}

/**
 * Mutate the live project WITHOUT an undo entry, a dirty flag or a re-render.
 *
 * For interactive drags ONLY. A gizmo drag emits a position every frame; each
 * one through `edit` would clone the project, push an undo entry and rebuild
 * the scene sixty times a second, and undoing the drag would then take sixty
 * presses. The drag mutates in place, restores the starting value on release,
 * and commits ONE `edit` with the final position — which is what makes the
 * undo entry span the whole drag. The legacy editor does the same thing with
 * `pushUndoCheckpoint` at drag start.
 *
 * Anything that is not a drag must go through `edit`.
 *
 * Off t = 0 the scene is frozen, so a scene drag is a no-op (logged once a
 * second, not per frame). A mutation that touches no scene state — project
 * settings (`meta`, `runDefaults`, `instrumentation`) or display-only fields
 * (opacity, tool toggles) — says so with `{ scene: false }` and always applies;
 * if it lied, the `notifyLiveEdit` after it puts the scene back.
 */
let frozenDragLoggedAt = 0
export function mutateLive(fn: (project: CfdProject) => void, opts: { scene?: boolean } = {}) {
  if (!state.project) return
  if (opts.scene !== false && sceneFrozen()) {
    const now = Date.now()
    if (now - frozenDragLoggedAt > 1000) {
      frozenDragLoggedAt = now
      logOperation('editRejected', `drag: static at t=${playhead}s`)
      emit()
    }
    return
  }
  fn(state.project)
}

/**
 * The move tool: whether the gizmo is shown, and which axes it offers.
 *
 * Not a view preference — it is stored per model in the .cfdproj, because
 * locking a rack to the floor plane is a property of that rack.
 */
export function setMoveTool(
  uid: string,
  patch: { enabled?: boolean; axes?: { x?: boolean; y?: boolean; z?: boolean } },
) {
  edit('setMoveTool', `${uid} ${JSON.stringify(patch)}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit) return
    const model = hit.model as unknown as {
      moveTool?: { enabled: boolean; axes: { x: boolean; y: boolean; z: boolean } }
    }
    const current = model.moveTool ?? { enabled: false, axes: { x: true, y: true, z: true } }
    model.moveTool = {
      enabled: patch.enabled ?? current.enabled,
      axes: { ...current.axes, ...(patch.axes ?? {}) },
    }
    // The other half of the move/rotate exclusion (`toggleModelMoveTool`,
    // STL_generate.html:15561): arrows on, rings off.
    if (model.moveTool.enabled) {
      const rotate = (hit.model as unknown as { rotateTool?: { enabled: boolean } }).rotateTool
      if (rotate) rotate.enabled = false
    }
  })
}

/**
 * The rotate tool: whether the ring gizmo is shown, and which rings.
 *
 * Rotate and move are MUTUALLY EXCLUSIVE, as in the legacy
 * (`toggleModelRotateTool`, STL_generate.html:15585): turning the rings on
 * drops every move tool in the project. Two gizmos on one model would put a
 * ring and an arrow in the same pixels, and a click could not say which was
 * meant.
 *
 * The axes are named yaw/pitch/roll, not x/y/z: this is the Y-up convention
 * the file already carries (`defaultRotateTool`), and yaw is the vertical
 * axis — the one a rack is actually turned about.
 */
export function setRotateTool(
  uid: string,
  patch: { enabled?: boolean; axes?: { yaw?: boolean; pitch?: boolean; roll?: boolean } },
) {
  edit('setRotateTool', `${uid} ${JSON.stringify(patch)}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit) return
    const model = hit.model as unknown as {
      rotateTool?: { enabled: boolean; axes: { yaw: boolean; pitch: boolean; roll: boolean } }
    }
    const current = model.rotateTool ?? {
      enabled: false,
      axes: { yaw: true, pitch: false, roll: false },
    }
    model.rotateTool = {
      enabled: patch.enabled ?? current.enabled,
      axes: { ...current.axes, ...(patch.axes ?? {}) },
    }
    if (!model.rotateTool.enabled) return
    for (const items of Object.values(draft.constructions ?? {})) {
      for (const m of Object.values(items ?? {})) {
        const move = (m as unknown as { moveTool?: { enabled: boolean } }).moveTool
        if (move) move.enabled = false
      }
    }
  })
}

export function setRotationDegrees(uid: string, deg: { x: number; y: number; z: number }) {
  if (!Number.isFinite(deg.x) || !Number.isFinite(deg.y) || !Number.isFinite(deg.z)) return
  edit('setRotation', `${uid} -> ${deg.x}, ${deg.y}, ${deg.z}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit) return
    const geometry = hit.model.geometry ?? (hit.model.geometry = {})
    geometry.quaternion = eulerDegToQuaternion(deg)
  })
}

const DEG = Math.PI / 180

/** YXZ-order Euler degrees -> quaternion, without pulling in Three.js. */
/**
 * A toolbar transform, applied to one model about its own anchor.
 *
 * Rotation PREMULTIPLIES a world-axis quaternion — `q_world * q_model`, not the
 * other way round — so 90° about Y turns the rack in the room's frame, which is
 * what the button promises. Post-multiplying would rotate about the model's own
 * already-turned axes, and a rack at 90° would then tip sideways instead of
 * turning again.
 *
 * A mirror negates a SCALE component rather than rotating: mirroring is not a
 * rotation, and the sign is what the geometry builder reads to flip its
 * outward normals.
 */
export type TransformAction =
  | { kind: 'rot'; axis: 'X' | 'Y' | 'Z'; deg: number }
  | { kind: 'mirror'; axis: 'X' | 'Y' | 'Z' }
  | { kind: 'reset' }

/** Exported so the multi-selection path reuses this math, not a second copy. */
export function axisQuaternion(axis: 'X' | 'Y' | 'Z', deg: number) {
  const half = (deg * DEG) / 2
  const s = Math.sin(half)
  return {
    x: axis === 'X' ? s : 0,
    y: axis === 'Y' ? s : 0,
    z: axis === 'Z' ? s : 0,
    w: Math.cos(half),
  }
}

/** Hamilton product, a*b — the same order THREE.Quaternion.premultiply uses. */
export function multiplyQuaternions(
  a: { x: number; y: number; z: number; w: number },
  b: { x: number; y: number; z: number; w: number },
) {
  return {
    x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
    y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
    z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
    w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
  }
}

export function applyModelTransform(uid: string, action: TransformAction) {
  const label =
    action.kind === 'rot'
      ? `rot${action.axis}:${action.deg}`
      : action.kind === 'mirror'
        ? `mirror${action.axis}`
        : 'reset'
  edit('globalTransform', `${uid} ${label}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit) return
    const geometry = hit.model.geometry ?? (hit.model.geometry = {})
    const q = geometry.quaternion ?? { x: 0, y: 0, z: 0, w: 1 }
    const scale = geometry.scale ?? { x: 1, y: 1, z: 1 }
    if (action.kind === 'rot') {
      geometry.quaternion = multiplyQuaternions(axisQuaternion(action.axis, action.deg), q)
      geometry.scale = { ...scale }
    } else if (action.kind === 'mirror') {
      const key = action.axis.toLowerCase() as 'x' | 'y' | 'z'
      geometry.scale = { ...scale, [key]: (scale[key] ?? 1) * -1 }
    } else {
      geometry.quaternion = { x: 0, y: 0, z: 0, w: 1 }
      geometry.scale = { x: 1, y: 1, z: 1 }
    }
  })
}

export function eulerDegToQuaternion(deg: { x: number; y: number; z: number }) {
  const cx = Math.cos((deg.x * DEG) / 2)
  const sx = Math.sin((deg.x * DEG) / 2)
  const cy = Math.cos((deg.y * DEG) / 2)
  const sy = Math.sin((deg.y * DEG) / 2)
  const cz = Math.cos((deg.z * DEG) / 2)
  const sz = Math.sin((deg.z * DEG) / 2)
  return {
    x: sx * cy * cz + cx * sy * sz,
    y: cx * sy * cz - sx * cy * sz,
    z: cx * cy * sz - sx * sy * cz,
    w: cx * cy * cz + sx * sy * sz,
  }
}

/** Quaternion -> YXZ Euler degrees, the inverse of the above. */
export function quaternionToEulerDeg(q: { x: number; y: number; z: number; w: number }) {
  const { x, y, z, w } = q
  // Rotation matrix elements needed for the YXZ decomposition.
  const m12 = 2 * (y * z - w * x)
  const m22 = 1 - 2 * (x * x + y * y)
  const m02 = 2 * (x * z + w * y)
  const m10 = 2 * (x * y + w * z)
  const m11 = 1 - 2 * (x * x + z * z)
  const clamped = Math.max(-1, Math.min(1, -m12))
  const ex = Math.asin(clamped)
  let ey: number
  let ez: number
  if (Math.abs(m12) < 0.9999999) {
    ey = Math.atan2(m02, m22)
    ez = Math.atan2(m10, m11)
  } else {
    // Gimbal lock: pitch is +/-90 deg, so yaw and roll are not separable.
    // Fold the whole remaining rotation into yaw rather than splitting it
    // arbitrarily, which would make the displayed numbers jump.
    ey = Math.atan2(-2 * (x * z - w * y), 1 - 2 * (y * y + z * z))
    ez = 0
  }
  const round = (r: number) => {
    const d = (r * 180) / Math.PI
    // Snap away float dust so a 90-degree rotation reads as 90, not 89.99999994.
    const snapped = Math.abs(d - Math.round(d)) < 1e-6 ? Math.round(d) : d
    // `+ 0` collapses negative zero, which would otherwise put "-0" in the box.
    return snapped === 0 ? 0 : snapped
  }
  return { x: round(ex), y: round(ey), z: round(ez) }
}

/**
 * Insert a freshly built model into its bucket.
 *
 * The bucket is passed in rather than derived here: the kind→bucket mapping
 * lives in the shared schema module, and re-deriving it in the store would be a
 * second place for it to be wrong.
 */
export function addModel(bucket: string, model: Construction): string {
  edit('createModel', `${bucket}/${model.meta?.name ?? model.uid}`, (draft) => {
    const constructions = draft.constructions ?? (draft.constructions = {})
    const items = constructions[bucket] ?? (constructions[bucket] = {})
    items[model.uid] = model
  })
  return model.uid
}

// ---------------------------------------------------------------------------
// runDefaults — the mesh and solver configuration
// ---------------------------------------------------------------------------

type RunBucket = 'meshConfig' | 'simulationConfig' | 'liquid'

interface RunDefaults {
  meshConfig?: Record<string, unknown>
  simulationConfig?: Record<string, unknown>
  liquid?: Record<string, unknown>
}

function runDefaultsOf(draft: CfdProject): RunDefaults {
  const holder = draft as CfdProject & { runDefaults?: RunDefaults }
  return holder.runDefaults ?? (holder.runDefaults = {})
}

/**
 * Write one field of `runDefaults.<bucket>`.
 *
 * The bucket is explicit because three fields the mesh panel shows in one
 * section live in `simulationConfig` rather than `meshConfig` —
 * `partitionWallRefinementLevel`, `partitionWallFeatureLevel` and
 * `extraLocations`. Guessing the bucket from the key would put them in the
 * wrong one, and the runner would then read the default instead of the value
 * the user typed.
 */
export function setRunConfig(bucket: RunBucket, key: string, value: unknown) {
  // Refinement levels are integers wherever they are written from — the panel,
  // the contract's raw `setMeshConfig`, or a future third caller.
  let stored = value
  if (INTEGER_LEVEL_KEYS.has(key)) {
    if (typeof value === 'number') stored = Math.round(value)
    else if (Array.isArray(value)) {
      stored = value.map((v) => (typeof v === 'number' ? Math.round(v) : v))
    }
  }
  edit('setRunConfig', `${bucket}.${key} = ${logValue(stored)}`, (draft) => {
    const defaults = runDefaultsOf(draft)
    const target = defaults[bucket] ?? (defaults[bucket] = {})
    target[key] = stored
  })
}

/**
 * Write one element of a `[min, max]` refinement pair.
 *
 * Reads the current pair first so editing `max` cannot reset `min` to a
 * default — the two inputs sit side by side and are edited one at a time.
 */
/**
 * Add a refinement zone: a 1 m box at the room's centre, level 2.
 *
 * Centred on the FOOTPRINT rather than on the origin, which for a room modelled
 * in the positive octant is a corner — a zone there refines empty air outside
 * the walls.
 *
 * The id carries a timestamp because zones are addressed by id in the mesh
 * dictionaries; an index would be reused after a delete and the next zone would
 * silently inherit the old one's settings.
 */
export function addRefinementZone(): string | null {
  if (!state.project) return null
  const plane = (state.project.geometry?.plane ?? []) as [number, number][]
  let cx = 0
  let cz = 0
  if (plane.length >= 3) {
    const xs = plane.map((v) => v[0])
    const zs = plane.map((v) => v[1])
    cx = (Math.min(...xs) + Math.max(...xs)) / 2
    cz = (Math.min(...zs) + Math.max(...zs)) / 2
  }
  const runDefaults = (state.project as unknown as Record<string, unknown>).runDefaults as
    | { meshConfig?: { refinementZones?: unknown[] } }
    | undefined
  const existing = (runDefaults?.meshConfig?.refinementZones ?? []).length
  const id = `rz_${Date.now()}_${existing + 1}`
  const name = `RefineZone_${existing + 1}`
  edit('addRefinementZone', name, (draft) => {
    const bag = draft as unknown as Record<string, unknown>
    const runDefaults = (bag.runDefaults ?? (bag.runDefaults = {})) as Record<string, unknown>
    const meshConfig = (runDefaults.meshConfig ?? (runDefaults.meshConfig = {})) as Record<
      string,
      unknown
    >
    const zones = (meshConfig.refinementZones ?? (meshConfig.refinementZones = [])) as unknown[]
    zones.push({
      id,
      name,
      level: 2,
      // Y from 0 up: a zone that starts below the floor refines the plenum,
      // which is a different volume and usually not what was meant.
      min: { x: cx - 0.5, y: 0, z: cz - 0.5 },
      max: { x: cx + 0.5, y: 1, z: cz + 0.5 },
      visible: true,
    })
  })
  return id
}

// ---------------------------------------------------------------------------
// Sensors — instrumentation.probes
// ---------------------------------------------------------------------------

export interface SensorProbe {
  uid: string
  geometry: { location: { x: number; y: number; z: number } }
  meta: { name: string; enabled: boolean }
}

export function sensorProbes(project: CfdProject | null): SensorProbe[] {
  const instrumentation = (project as unknown as { instrumentation?: { probes?: Record<string, SensorProbe> } } | null)
    ?.instrumentation
  return Object.values(instrumentation?.probes ?? {})
}

/**
 * Next free `sensor_<n>`.
 *
 * Derived from the ids in the project, not from a counter: a counter restarts
 * at zero every page load and would hand out an id that overwrites a probe in
 * the file just opened — silently, since probes are stored in a dict by id.
 */
function nextSensorId(project: CfdProject): string {
  let max = 0
  for (const probe of sensorProbes(project)) {
    const m = /^sensor_(\d+)$/.exec(probe.uid ?? '')
    if (m) max = Math.max(max, Number(m[1]))
  }
  return `sensor_${max + 1}`
}

/** Add a probe at a point in METRES. */
export function addSensorProbe(x: number, y: number, z: number): string | null {
  if (!state.project) return null
  if (![x, y, z].every(Number.isFinite)) return null
  const id = nextSensorId(state.project)
  edit('addSensorPoint', `${id} (${x}, ${y}, ${z})`, (draft) => {
    const bag = draft as unknown as Record<string, unknown>
    const instrumentation = (bag.instrumentation ?? (bag.instrumentation = {})) as Record<string, unknown>
    const probes = (instrumentation.probes ?? (instrumentation.probes = {})) as Record<string, unknown>
    probes[id] = {
      uid: id,
      geometry: { location: { x, y, z } },
      meta: { name: id, enabled: true },
    }
  })
  return id
}

export function removeSensorProbe(id: string) {
  edit('deleteSensorPoint', id, (draft) => {
    const probes = (draft as unknown as { instrumentation?: { probes?: Record<string, unknown> } })
      .instrumentation?.probes
    if (probes) delete probes[id]
  })
}

export function setSensorProbe(
  id: string,
  patch: { name?: string; enabled?: boolean; location?: { x: number; y: number; z: number } },
) {
  edit('updateSensorPoint', `${id} ${JSON.stringify(patch)}`, (draft) => {
    const probes = (draft as unknown as { instrumentation?: { probes?: Record<string, SensorProbe> } })
      .instrumentation?.probes
    const probe = probes?.[id]
    if (!probe) return
    if (patch.name !== undefined) probe.meta.name = patch.name
    if (patch.enabled !== undefined) probe.meta.enabled = patch.enabled
    if (patch.location) probe.geometry.location = { ...patch.location }
  })
}

/**
 * A CDU's liquid parameters.
 *
 * Written to the V4-canonical fields — `cooling.supplyTemperatureC`,
 * `supplyPressureKpa`, `totalFlowRate` — not to the v3 names they migrated
 * from. The liquid graph compiler reads the canonical ones, so a write to the
 * old name is accepted by the editor and ignored by the solver.
 */
export function setCduLiquidParam(
  uid: string,
  key: 'supplyTemperature' | 'supplyPressure' | 'totalFlowRate' | 'advancedMode',
  value: number | boolean,
) {
  edit('updateCduLiquidParam', `${uid} ${key}=${String(value)}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit || hit.model.kind !== 'cdu') return
    const model = hit.model as unknown as { cooling?: Record<string, unknown> }
    const cooling = (model.cooling ?? (model.cooling = {})) as Record<string, unknown>
    if (key === 'supplyTemperature') cooling.supplyTemperatureC = value
    else if (key === 'supplyPressure') cooling.supplyPressureKpa = value
    else if (key === 'totalFlowRate') cooling.totalFlowRate = value
    else cooling.advancedMode = value
  })
}

/**
 * Which racks a CDU serves, stored by NAME.
 *
 * By name and not by uid, because that is what the liquid graph compiler
 * matches on; a uid list would look right in the file and resolve to nothing.
 */
export function setCduServedRack(uid: string, rackName: string, served: boolean) {
  edit('toggleCduServedRack', `${uid} ${rackName}=${served}`, (draft) => {
    const hit = findConstruction(draft, uid)
    if (!hit || hit.model.kind !== 'cdu') return
    const model = hit.model as unknown as { meta?: Record<string, unknown> }
    const meta = (model.meta ?? (model.meta = {})) as Record<string, unknown>
    const racks = new Set((meta.racks ?? []) as string[])
    if (served) racks.add(rackName)
    else racks.delete(rackName)
    meta.racks = [...racks]
  })
}

/** Set one field on a refinement zone, addressed by its id. */
export function setRefinementZoneField(id: string, field: string, value: unknown) {
  edit('setRefinementZone', `${id} ${field}=${String(value)}`, (draft) => {
    const bag = draft as unknown as { runDefaults?: { meshConfig?: { refinementZones?: unknown[] } } }
    const zones = (bag.runDefaults?.meshConfig?.refinementZones ?? []) as Record<string, unknown>[]
    const zone = zones.find((z) => z.id === id)
    if (zone) zone[field] = value
  })
}

export function removeRefinementZone(id: string) {
  edit('deleteRefinementZone', id, (draft) => {
    const bag = draft as unknown as { runDefaults?: { meshConfig?: Record<string, unknown> } }
    const meshConfig = bag.runDefaults?.meshConfig
    if (!meshConfig) return
    const zones = (meshConfig.refinementZones ?? []) as { id?: string }[]
    meshConfig.refinementZones = zones.filter((z) => z.id !== id)
  })
}

/**
 * Run-config keys whose values are snappyHexMesh REFINEMENT LEVELS — integers.
 *
 * The runner's Pydantic schema rejects a float outright, so a typed 1.5 does not
 * misbehave subtly: it kills the run at mesh time. The legacy editor rounded
 * inside its single write path; React rounded at the two MeshPanel call sites,
 * which held until the automation contract gained a raw `setMeshConfig` and
 * wrote 1.5 straight through. Rounding belongs where the value lands.
 *
 * Matched by name, not by rounding everything: `meshBaseSize` is the number a
 * mesh is most sensitive to, and rounding it would be a far worse bug than the
 * one being fixed.
 */
const INTEGER_LEVEL_KEYS = new Set([
  'refinementLevel',
  'featureLevel',
  'surfaceLevel',
  'rackRefinementLevel',
  'rackFeatureLevel',
  'partitionWallRefinementLevel',
  'partitionWallFeatureLevel',
  'serverRefinementLevel',
  'serverFeatureLevel',
  'baffleRefinementLevel',
  'baffleFeatureLevel',
  'floorRefinementLevel',
  'floorFeatureLevel',
  'acuRefinementLevel',
  'acuFeatureLevel',
  'wallRefinementLevel',
  'wallFeatureLevel',
])

function levelValue(key: string, value: number): number {
  return INTEGER_LEVEL_KEYS.has(key) ? Math.round(value) : value
}

export function setRefinementBound(
  bucket: RunBucket,
  key: string,
  index: 0 | 1,
  value: number,
  fallback: [number, number],
) {
  if (!Number.isFinite(value)) return
  edit('setRunConfig', `${bucket}.${key}[${index}] = ${value}`, (draft) => {
    const defaults = runDefaultsOf(draft)
    const target = defaults[bucket] ?? (defaults[bucket] = {})
    const current = target[key]
    const pair: [number, number] = Array.isArray(current) && current.length === 2
      ? [Number(current[0]), Number(current[1])]
      : [...fallback]
    pair[index] = levelValue(key, value)
    target[key] = pair
  })
}

/** Write one component of `meshConfig.locationInMesh`. */
export function setLocationInMesh(axis: 'x' | 'y' | 'z', value: number) {
  if (!Number.isFinite(value)) return
  edit('setLocationInMesh', `${axis} = ${value}`, (draft) => {
    const defaults = runDefaultsOf(draft)
    const mesh = defaults.meshConfig ?? (defaults.meshConfig = {})
    const current = mesh.locationInMesh
    const point =
      current && typeof current === 'object'
        ? { ...(current as Record<string, number>) }
        : { x: 0, y: 0, z: 0 }
    point[axis] = value
    mesh.locationInMesh = point
  })
}

/**
 * Add an extra locationInMesh seed.
 *
 * These mark ADDITIONAL connected fluid regions — a containment aisle sealed
 * off from the room is its own domain, and snappyHexMesh keeps only the region
 * around a seed, so without one the whole aisle is carved away. They live in
 * `simulationConfig`, not `meshConfig`, next to the id counter the legacy page
 * keeps there (STL_generate.html:27460).
 */
export function addExtraLocation(): number | null {
  if (!state.project) return null
  let newId: number | null = null
  edit('addExtraLoc', 'extra seed', (draft) => {
    const defaults = runDefaultsOf(draft)
    const sim = (defaults.simulationConfig ?? (defaults.simulationConfig = {})) as Record<string, unknown>
    const list = (sim.extraLocations ?? (sim.extraLocations = [])) as ExtraLocation[]
    const nextId = Number(sim._nextExtraLocId ?? 1)
    sim._nextExtraLocId = nextId + 1
    const mesh = (defaults.meshConfig ?? {}) as Record<string, unknown>
    const primary = (mesh.locationInMesh as { x: number; y: number; z: number } | undefined) ?? {
      x: 5,
      y: 4,
      z: 1.5,
    }
    // A fixed 0.75 m offset, where the legacy randomises 0.5-1.0 m. Random
    // placement makes two runs of the same project differ, which is exactly
    // what the parity sweep exists to catch.
    const r = (v: number) => Math.round(v * 100) / 100
    newId = nextId
    list.push({
      id: nextId,
      x: r(primary.x + 0.75),
      y: r(primary.y + 0.75),
      z: r(primary.z),
      color: pickExtraColor(list),
      visible: true,
    })
  })
  return newId
}

export interface ExtraLocation {
  id: number
  x: number
  y: number
  z: number
  color: string
  visible?: boolean
}

const EXTRA_COLORS = [
  '#00bfff', '#ff4081', '#69f0ae', '#ffab40',
  '#7c4dff', '#ffd740', '#18ffff', '#ff6e40',
  '#b388ff', '#64ffda', '#ff80ab', '#8c9eff',
]

function pickExtraColor(existing: ExtraLocation[]): string {
  const used = existing.map((e) => e.color)
  return EXTRA_COLORS.find((c) => !used.includes(c)) ?? EXTRA_COLORS[existing.length % EXTRA_COLORS.length]!
}

export function removeExtraLocation(id: number) {
  edit('removeExtraLoc', `#${id}`, (draft) => {
    const sim = runDefaultsOf(draft).simulationConfig as { extraLocations?: ExtraLocation[] } | undefined
    if (!sim?.extraLocations) return
    sim.extraLocations = sim.extraLocations.filter((e) => e.id !== id)
  })
}

/** Write one component of an extra seed, from the panel's number boxes. */
export function setExtraLocation(id: number, axis: 'x' | 'y' | 'z', value: number) {
  if (!Number.isFinite(value)) return
  edit('setExtraLoc', `#${id} ${axis} = ${value}`, (draft) => {
    const sim = runDefaultsOf(draft).simulationConfig as { extraLocations?: ExtraLocation[] } | undefined
    const entry = sim?.extraLocations?.find((e) => e.id === id)
    if (entry) entry[axis] = value
  })
}

/** The project's free-text description, in `meta.description`. */
export function setProjectDescription(text: string) {
  edit('setProjectDescription', `${text.length} chars`, (draft) => {
    const meta = (draft.meta ?? (draft.meta = {})) as Record<string, unknown>
    meta.description = text
  })
}

/**
 * A MEASURED sensor record: what a probe in the real room read.
 *
 * Not the same thing as a solver monitoring point — those are places the run
 * reports from (`instrumentation.probes`). These are measurements from the
 * physical room, kept beside the model so a solved case can be compared with
 * what the building actually did.
 */
export function addSensorRecord(): string {
  const id = `rs_${Math.random().toString(36).slice(2, 9)}`
  edit('addSensorRecord', id, (draft) => {
    const instrumentation = (draft as unknown as { instrumentation?: Record<string, unknown> })
      .instrumentation ?? ((draft as unknown as { instrumentation: Record<string, unknown> }).instrumentation = {})
    const measurements = (instrumentation.measurements ?? (instrumentation.measurements = {})) as
      Record<string, unknown>
    measurements[id] = {
      id,
      name: `sensor_${Object.keys(measurements).length + 1}`,
      quantity: 'temperature',
      unit: 'C',
      location: { x: 0, y: 0, z: 0 },
      bind: { instanceId: null },
      series: [],
      source: '',
    }
  })
  return id
}

export function updateSensorRecord(id: string, field: string, value: unknown) {
  edit('updateSensorRecord', `${id}.${field}`, (draft) => {
    const measurements = ((draft as unknown as { instrumentation?: { measurements?: Record<string, Record<string, unknown>> } })
      .instrumentation?.measurements ?? {}) as Record<string, Record<string, unknown>>
    const record = measurements[id]
    if (!record) return
    if (field === 'x' || field === 'y' || field === 'z') {
      const location = (record.location ?? (record.location = { x: 0, y: 0, z: 0 })) as Record<string, number>
      location[field] = Number(value) || 0
    } else {
      record[field] = value
    }
  })
}

export function deleteSensorRecord(id: string) {
  edit('deleteSensorRecord', id, (draft) => {
    const measurements = ((draft as unknown as { instrumentation?: { measurements?: Record<string, unknown> } })
      .instrumentation?.measurements ?? {}) as Record<string, unknown>
    delete measurements[id]
  })
}

/**
 * A project boundary constraint: a wall a model may not be dragged past.
 *
 * `yMin` enabled at 0 is the common one — it stops a rack being dragged below
 * the floor, which is easy to do while looking at the room from above and
 * produces a mesh with geometry outside the domain. The move gizmo enforces
 * these through `areModelsWithinProjectBounds`, so they are not decoration.
 */
export function setProjectBound(
  key: string,
  field: 'enabled' | 'value',
  value: boolean | number,
) {
  if (field === 'value' && !Number.isFinite(value as number)) return
  edit('setProjectBounds', `${key}.${field}=${String(value)}`, (draft) => {
    const geometry = draft.geometry as unknown as Record<string, unknown> | undefined
    if (!geometry) return
    const bounds = (geometry.bounds ?? (geometry.bounds = {})) as Record<
      string,
      { enabled?: boolean; value?: number }
    >
    const entry = bounds[key] ?? (bounds[key] = { enabled: false, value: 0 })
    if (field === 'enabled') entry.enabled = !!value
    else entry.value = value as number
  })
}

/**
 * The unit geometry boxes are typed in.
 *
 * A display setting, stored WITH the project (`meta.inputUnit`): the unit a
 * room was authored in belongs to that work, not to the browser session.
 */
export function setInputUnit(unit: 'mm' | 'cm' | 'm') {
  edit('setInputUnit', unit, (draft) => {
    const meta = (draft.meta ?? (draft.meta = {})) as Record<string, unknown>
    meta.inputUnit = unit
  })
}

/**
 * Write one field of a model's own `cooling` block, by dotted path.
 *
 * This is the boundary condition for the part of a surface no patch covers —
 * most of a raised floor, most of a containment baffle. The runner reads it as
 * the DEFAULT for the region, so leaving it unreachable meant a perforated
 * floor could only be described tile by tile.
 */
export function setModelCooling(uid: string, path: string, value: unknown) {
  edit('updateModelCfd', `${uid} ${path}=${String(value)}`, (draft) => {
    const model = findConstruction(draft, uid)?.model as unknown as {
      cooling?: Record<string, unknown>
    }
    if (!model) return
    const cooling = (model.cooling ?? (model.cooling = {})) as Record<string, unknown>
    const parts = path.split('.')
    let target = cooling
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts[i]!
      if (!target[key] || typeof target[key] !== 'object') target[key] = {}
      target = target[key] as Record<string, unknown>
    }
    target[parts[parts.length - 1]!] = value
  })
}

/**
 * Write one field of a nested object under `runDefaults.<bucket>`.
 *
 * `schemes`, `residualControl` and `relaxationFactors` are objects, and the
 * shallow setter would replace the whole object with a single key — losing
 * every other scheme or threshold. Reading the current object first is what
 * keeps editing `U`'s residual from clearing `T`'s.
 */
export function setNestedRunConfig(
  bucket: RunBucket,
  group: string,
  key: string,
  value: unknown,
) {
  edit('setRunConfig', `${bucket}.${group}.${key} = ${logValue(value)}`, (draft) => {
    const defaults = runDefaultsOf(draft)
    const target = defaults[bucket] ?? (defaults[bucket] = {})
    const current = target[group]
    const next: Record<string, unknown> =
      current && typeof current === 'object' ? { ...(current as Record<string, unknown>) } : {}
    next[key] = value
    target[group] = next
  })
}

// ---------------------------------------------------------------------------
// Coolant — models.materialModels.fluids
// ---------------------------------------------------------------------------

/**
 * Replace the coolant.
 *
 * The fluids dict is keyed by `fluid.type` and holds exactly ONE entry, so this
 * clears it before inserting rather than assigning a key. That mirrors
 * `_liqSetFluid` (STL_generate.html:1483), and it matters: editing a property
 * re-types the fluid to 'custom', so a plain assignment would leave the old
 * key behind and `_liqFluid`, which returns `Object.values(...)[0]`, could then
 * pick either one.
 */
export function setFluid(fluid: { type: string } & Record<string, unknown>) {
  edit('setLiquidCoolingFluid', `coolant = ${fluid.type}`, (draft) => {
    const holder = draft as CfdProject & {
      models?: { materialModels?: { fluids?: Record<string, unknown> } }
    }
    const models = holder.models ?? (holder.models = {})
    const material = models.materialModels ?? (models.materialModels = {})
    const fluids = material.fluids ?? (material.fluids = {})
    for (const key of Object.keys(fluids)) delete fluids[key]
    fluids[fluid.type || 'fluid'] = { ...fluid }
  })
}

// ---------------------------------------------------------------------------
// Room shell — walls + floor + ceiling generated from a 2D footprint
// ---------------------------------------------------------------------------

/**
 * Replace the room shell in ONE checkpoint.
 *
 * Removing the old boundary models and adding the new ones as separate edits
 * would put 2n+2 entries on the undo stack for what a user thinks of as one
 * action, and an undo halfway through would leave a room with three walls.
 */
export function replaceRoom(
  plane: [number, number][],
  height: number,
  removeUids: string[],
  added: { bucket: string; model: Construction }[],
) {
  edit('applyRoom2D', `${plane.length} 顶点, 高 ${height}m, ${added.length} 个模型`, (draft) => {
    for (const items of Object.values(draft.constructions ?? {})) {
      if (!items) continue
      for (const uid of removeUids) delete items[uid]
    }
    const constructions = draft.constructions ?? (draft.constructions = {})
    for (const { bucket, model } of added) {
      const items = constructions[bucket] ?? (constructions[bucket] = {})
      items[model.uid] = model
    }
    // The footprint itself lives on the project, not on the models: the mesh
    // pipeline reads `geometry.plane` to build the blockMesh box and to rebuild
    // the watertight room shell, so it has to move with the walls.
    const geometry = draft.geometry ?? (draft.geometry = { height, plane })
    geometry.plane = plane
    geometry.height = height
    ;(geometry as { planeHeight?: number }).planeHeight = height
  })
}
