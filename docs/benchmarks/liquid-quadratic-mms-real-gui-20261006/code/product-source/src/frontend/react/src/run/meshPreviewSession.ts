/**
 * The mesh preview's session: what was loaded, and whether it is drawn.
 *
 * ## Why this is a module and not the panel's state
 *
 * The preview used to live in `MeshPreviewPanel`, which only exists while the
 * mesh tab's preview sub-tab is up. Leaving the tab unmounted the panel, and its
 * cleanup disposed the mesh — so switching to 几何与属性 to look at a solid and
 * back again re-downloaded and re-decoded a multi-million-triangle payload for
 * nothing. The user's rule is that a preview ends when they CLOSE it, not when
 * they look away from it.
 *
 * So the decoded mesh and everything the panel draws from it live here, and the
 * panel is a view over it. Leaving the tab hides the group at the Three.js level
 * (`group.visible = false`) and puts the model geometry back to opaque; coming
 * back flips it and re-applies the fade. Nothing is rebuilt, so the return is a
 * flag, not a load.
 *
 * ## What does end it
 *
 * Two things, both explicit:
 *
 *   - the panel's 关闭预览 button (`closeMeshPreviewSession`), and
 *   - loading a project (`resetMeshPreviewSession`, from `workspaceReset`).
 *
 * The second is the one that must not be missed. A `.cfdproj` can arrive at any
 * moment — the file dialog, a remote path, `window._applyProjectData` — and
 * every load funnels through `resetWorkspace`. A mesh from the case being
 * closed is not a view of the case being opened: its patch names belong to
 * another room's `polyMesh/boundary`, so leaving it up would draw boundaries
 * over geometry that does not have them and offer to toggle patches that the
 * new mesh does not contain.
 */

import type { Group } from 'three'
import { t } from '../i18n'
import { stateV4 } from '../legacy/cfdprojModules'
import { decodeMesh, type DecodedMesh, type PatchMeta } from '../postprocess'
import { cfdrunExport } from '../legacy/cfdrunBuild'
import { clearResultLayerSet } from '../renderer/resultLayerRevision'
import { publishMeshPreviewEdges, type VerifiedMeshPreviewDescriptor } from '../renderer/windResultProducer'
import {assertMeshPreviewBytes} from '../renderer/meshPreviewGrant'
import { fetchVerifiedMeshPreviewSource } from '../renderer/windResultSourceClient'
import {
  groupPatchesByModelKind,
  orderedKinds,
  type ModelLike,
  type PatchLike,
} from '../postprocess/ppPatchTree'
import * as store from '../projectStore'
import { applyMeshPreviewOrder, RENDER_ORDER_DEFAULTS } from '../ui/renderOrder'
import {
  buildMeshPreview,
  DEFAULT_GEOMETRY_OPACITY,
  isEnclosurePatchName,
  DEFAULT_MESH_OPACITY,
  disposeMeshPreview,
  loadMeshPreview,
  meshPreviewSummary,
  setMeshOpacity,
  setPatchesVisible,
  type BuiltPreview,
} from './meshPreview'

export interface MeshPreviewSession {
  /** The decoded, built preview — megabytes of buffers, kept across tab switches. */
  built: BuiltPreview | null
  /** Server case/mesh stat identity, not a content hash. */
  meshSnapshotKey: string | null
  patches: PatchMeta[]
  /**
   * The models the patch list groups against, snapshotted at LOAD time.
   *
   * The grouping describes the mesh that was read off disk; re-deriving it from
   * a project the user has edited since would start moving patches between
   * racks the mesh does not know about.
   */
  models: ModelLike[]
  hidden: Set<string>
  meshOpacity: number
  geoOpacity: number
  order: string[]
  collapsed: Record<string, boolean>
  /** The post-load readout, kept on the panel after the task dialog goes. */
  summary: string
  /** The stage log the load runs behind. */
  loadLines: string[]
  busy: boolean
  error: string | null
  /**
   * Whether the mesh is drawn right now.
   *
   * False on another tab, where the group stays in the scene with `visible`
   * off — hidden, not gone.
   */
  visible: boolean
}

const INITIAL: MeshPreviewSession = {
  built: null,
  meshSnapshotKey: null,
  patches: [],
  models: [],
  hidden: new Set<string>(),
  meshOpacity: DEFAULT_MESH_OPACITY,
  geoOpacity: DEFAULT_GEOMETRY_OPACITY,
  order: RENDER_ORDER_DEFAULTS.meshPreview as string[],
  collapsed: {},
  summary: '',
  loadLines: [],
  busy: false,
  error: null,
  visible: true,
}

let state: MeshPreviewSession = INITIAL
const listeners = new Set<() => void>()
let loadAbort: AbortController | null = null
let verifiedPreview: VerifiedMeshPreviewDescriptor | null = null
let decodedPreview: DecodedMesh | null = null

function clearVerifiedPreview() {
  verifiedPreview = null
  decodedPreview = null
  clearResultLayerSet('mesh-preview')
}

function publishVerifiedPreview() {
  const project = store.getProject()
  if (!state.visible || !state.built || !verifiedPreview || !decodedPreview || !project?.uid) {
    clearResultLayerSet('mesh-preview')
    return
  }
  try {
    publishMeshPreviewEdges({
      descriptor: verifiedPreview, meshSnapshotKey: verifiedPreview.meshSnapshotKey,
      currentProject: { uid: project.uid, contentSha256: cfdrunExport().projectContentHashSync(project) },
      mesh: decodedPreview, hidden: new Set([...state.hidden,...(state.built.merged?state.patches.filter(p=>p.name.endsWith('_slave')).map(p=>p.name):[])]), opacity: state.meshOpacity,
    })
  } catch {
    // A missing certificate leaves the ordinary Three.js preview usable.
    clearResultLayerSet('mesh-preview')
  }
}

function emit() {
  for (const fn of [...listeners]) fn()
}

function patch(next: Partial<MeshPreviewSession>) {
  state = { ...state, ...next }
  emit()
}

export function subscribeMeshPreview(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/**
 * The session snapshot.
 *
 * Replaced, never mutated: `useSyncExternalStore` compares it by identity, so a
 * panel that got the same object back would not re-render and a change made in
 * place would be invisible.
 */
export function getMeshPreviewSession(): MeshPreviewSession {
  return state
}

// ── the viewport's two handles ───────────────────────────────────────────────
//
// Published by `Viewport` on `window` rather than threaded down: the group is a
// Three.js object and the panel that owns it sits six components away from the
// scene. Same route the panel used before this existed.

interface PreviewWindow {
  __attachMeshPreview?(group: Group | null): void
  __setGeometryOpacity?(value: number): void
}

function previewWindow(): PreviewWindow {
  return typeof window === 'undefined' ? {} : (window as unknown as PreviewWindow)
}

/**
 * Put the group in the scene, or take it out, and match the geometry's fade to
 * whether the mesh is actually being looked at.
 *
 * The group is ATTACHED either way once a preview exists — only `.visible`
 * changes — so a tab switch cannot lose the buffers. Detaching happens on close
 * and on reset, which are the two things that really end the preview.
 */
function applyPresence() {
  const win = previewWindow()
  const built = state.built
  if (!built) {
    win.__attachMeshPreview?.(null)
    win.__setGeometryOpacity?.(1)
    return
  }
  built.group.visible = state.visible
  win.__attachMeshPreview?.(built.group)
  win.__setGeometryOpacity?.(state.visible ? state.geoOpacity : 1)
  applyMeshPreviewOrder(state.order, built.group)
}

/** The fold state a freshly loaded patch list starts in: everything closed. */
function foldedByDefault(
  patches: readonly PatchMeta[],
  models: readonly ModelLike[],
): Record<string, boolean> {
  const groups = groupPatchesByModelKind(patches as unknown as PatchLike[], [...models])
  const collapsed: Record<string, boolean> = {}
  for (const kind of orderedKinds(groups)) {
    collapsed[kind] = true
    for (const modelName of Object.keys(groups[kind] ?? {})) collapsed[`${kind}/${modelName}`] = true
  }
  return collapsed
}

// ── loading ─────────────────────────────────────────────────────────────────

/**
 * Fetch and build the preview.
 *
 * Lives here rather than in the panel so that switching tabs mid-load does not
 * cancel it: the request outlives the component that started it, and the mesh is
 * there when the user comes back.
 */
export async function loadMeshPreviewSession(): Promise<void> {
  const controller = new AbortController()
  loadAbort?.abort()
  loadAbort = controller
  clearVerifiedPreview()
  patch({ busy: true, error: null, loadLines: [], summary: '' })
  const stage = (line: string) => patch({ loadLines: [...state.loadLines, line] })
  try {
    const { meta, buffer } = await loadMeshPreview({ signal: controller.signal, onStage: stage })
    // The room shell starts unchecked — see isEnclosurePatchName. Normalising
    // defaultVisible here, before anything reads it, means the mesh visibility,
    // the merged visible set and the list checkbox all follow from one place.
    for (const patch of meta.patches ?? []) {
      if (patch.defaultVisible !== false && isEnclosurePatchName(patch.name)) {
        patch.defaultVisible = false
      }
    }
    // Aborting between the two fetches leaves both resolved and nothing to
    // reject — the legacy checks `task.isCancelled()` at the same points
    // (STL_generate.html:24352/24357) rather than swapping in a mesh nobody is
    // waiting for any more.
    if (controller.signal.aborted) throw new DOMException('aborted', 'AbortError')
    disposeMeshPreview(state.built)
    const built = buildMeshPreview(meta, buffer, state.meshOpacity)
    const project = store.getProject()
    // A mesh with no patches in it has nothing to list, and a list of every
    // declared patch would then be a list of things that are not on screen.
    const patches = built.byPatch.size ? (meta.patches ?? []) : []
    const models = project ? (stateV4().allModels(project) as ModelLike[]) : []
    patch({
      built,
      meshSnapshotKey: meta.meshSnapshotKey ?? null,
      patches,
      models,
      hidden: new Set(
        (meta.patches ?? []).filter((p) => p.defaultVisible === false).map((p) => p.name),
      ),
      // Every group starts folded: a mesh of a few hundred patches is read from
      // its kinds down, and a list that opens fully is a wall of names. Done
      // HERE rather than in the panel, because the panel now remounts on every
      // tab switch and a fold-on-mount would undo the groups the user had
      // opened. A patch picked in the viewport opens its own kind and model.
      collapsed: foldedByDefault(patches, models),
      visible: true,
    })
    applyPresence()
    const line = meshPreviewSummary(meta)
    patch({ summary: line })
    store.logAction('meshPreview', line)
    if (meta.meshSnapshotKey) {
      // Certificate lookup is independent of the already loaded Three.js mesh.
      void fetchVerifiedMeshPreviewSource(meta.meshSnapshotKey, controller.signal).then(async (descriptor) => {
        if (controller.signal.aborted || state.built !== built) return
        if(descriptor)await assertMeshPreviewBytes(buffer,descriptor)
        if(controller.signal.aborted||state.built!==built)return
        verifiedPreview = descriptor
        decodedPreview = descriptor ? decodeMesh(buffer, meta) : null
        publishVerifiedPreview()
      }).catch(() => {
        if (controller.signal.aborted || state.built !== built) return
        clearVerifiedPreview()
      })
    }
  } catch (err) {
    // A cancelled load is a choice, not a failure. The legacy logs it and leaves
    // the panel as it was (STL_generate.html:24484). `instanceof Error` is not
    // the test: a browser's abort rejects with a DOMException, which is not an
    // Error subclass everywhere.
    if ((err as { name?: string } | null)?.name === 'AbortError') {
      store.logAction('meshPreview', t('mp.load_cancelled'))
    } else {
      patch({ error: err instanceof Error ? err.message : String(err) })
    }
  } finally {
    if (loadAbort === controller) loadAbort = null
    patch({ busy: false })
  }
}

/** Cancel a load in flight. */
export function abortMeshPreviewLoad(): void {
  loadAbort?.abort()
}

export function isMeshPreviewLoading(): boolean {
  return loadAbort !== null
}

// ── the two ways it ends ────────────────────────────────────────────────────

/** Forget the built mesh but keep the panel's settings — used by neither caller
 *  today, and kept out of the public surface until one needs it. */
function dropBuilt() {
  clearVerifiedPreview()
  disposeMeshPreview(state.built)
  patch({ built: null, meshSnapshotKey: null, patches: [], models: [], summary: '', hidden: new Set<string>() })
  applyPresence()
}

/**
 * The 关闭预览 button: dispose the mesh and put the geometry back to solid.
 *
 * The fade only makes sense while a mesh is drawn over it, so it goes with it.
 */
export function closeMeshPreviewSession(): void {
  loadAbort?.abort()
  loadAbort = null
  dropBuilt()
  patch({ busy: false, error: null, loadLines: [] })
}

/**
 * A project is being loaded: everything here belongs to the one being closed.
 *
 * Called from `workspaceReset`, which runs BEFORE the new document reaches the
 * store. The settings go too — the opacity a user picked to read one mesh is not
 * a session default, and the collapsed groups are keyed on patch names that the
 * next case will mean something else by.
 */
export function resetMeshPreviewSession(): void {
  loadAbort?.abort()
  loadAbort = null
  clearVerifiedPreview()
  disposeMeshPreview(state.built)
  state = { ...INITIAL, hidden: new Set<string>(), order: [...INITIAL.order], collapsed: {} }
  emit()
  applyPresence()
}

// ── the panel's own gestures ────────────────────────────────────────────────

/**
 * Show or hide patches.
 *
 * ONE call for the whole batch: merged mode rebuilds the index buffer on every
 * visibility change, so a loop over 4,336 patches was 4,336 rebuilds of a
 * three-million-triangle buffer and 全选 hung the tab.
 */
export function setMeshPreviewPatchesVisible(
  names: readonly string[],
  visible: boolean,
): void {
  const hidden = new Set(state.hidden)
  for (const name of names) {
    if (visible) hidden.delete(name)
    else hidden.add(name)
  }
  patch({ hidden })
  if (state.built) setPatchesVisible(state.built, names, visible)
  publishVerifiedPreview()
}

export function setMeshPreviewOpacity(value: number): void {
  patch({ meshOpacity: value })
  if (state.built) setMeshOpacity(state.built, value)
  publishVerifiedPreview()
}

export function setMeshPreviewGeoOpacity(value: number): void {
  patch({ geoOpacity: value })
  previewWindow().__setGeometryOpacity?.(value)
}

export function setMeshPreviewOrder(order: string[]): void {
  patch({ order })
  if (state.built) applyMeshPreviewOrder(order, state.built.group)
}

export function setMeshPreviewCollapsed(
  next: Record<string, boolean> | ((prev: Record<string, boolean>) => Record<string, boolean>),
): void {
  const collapsed = typeof next === 'function' ? next(state.collapsed) : next
  // A no-op update must NOT emit. The patch list re-highlights its selection on
  // every render, and highlighting unfolds the groups the selection sits in
  // through this setter; with an unconditional emit that is a render → highlight
  // → emit → render loop, which is React error #185 (maximum update depth) the
  // moment any patch is selected. React's own setState bails out on an
  // identical object; a hand-rolled store has to bail out too.
  if (collapsed === state.collapsed) return
  patch({ collapsed })
}

/**
 * Draw the mesh, or stop drawing it — WITHOUT dropping anything.
 *
 * This is the tab switch. The group stays in the scene and keeps its buffers;
 * only `.visible` and the geometry's fade change, so coming back is instant.
 */
export function setMeshPreviewVisible(visible: boolean): void {
  if (state.visible === visible) return
  patch({ visible })
  applyPresence()
  publishVerifiedPreview()
}
