/**
 * STL Preview: the live mode.
 *
 * A second DISPLAY MODE, not a panel. `uiState.displayMode === 'stlPreview'`
 * makes the legacy editor rebuild the viewport from the exporter's output
 * instead of the modelling geometry (`doRebuildStlPreview`,
 * STL_generate.html:7600), swap the tree for a solid-region tree and swap the
 * inspector for the three preview panels. This module is the same switch: the
 * scene builder, the feature tree and the inspector all read it.
 *
 * Held at module scope rather than in App state for the reason `viewportBus`
 * is: the three readers are in three different React subtrees, and the scene
 * builder is not a React component at all — it is constructed once by the
 * viewport and reads its inputs through accessors.
 *
 * ## The entry point is a deliberate restoration
 *
 * The legacy's own button is gone: `refreshStlPreviewToggleButton`
 * (STL_generate.html:3883) looks up `#toggle-preview-btn` and no such element
 * exists in the page any more, so over there the mode is reachable only from
 * the console. The toolbar button in this app is a RESTORATION of the entry
 * point, not a copy of the legacy's current UI. Everything below it — the
 * tree, the panels, the flags, the STL the preview is built from — is ported
 * from the legacy code, which is all still live.
 *
 * ## Why the flags are stored the way they are
 *
 * `previewVisibility` / `previewRegionVisibility` / `previewRegionReverse` are
 * UI preferences in the legacy too: `stateForSaveV4` does not write them, and
 * `UI_PREF_KEYS` (STL_generate.html:3247) persists them in
 * `localStorage['cfdproj.ui.<uid>']`. This keeps the same key and the same
 * field names, so the two editors read and write the same blob — a region
 * reversed in one app is reversed in the other.
 */

import type * as THREE from 'three'
import { uiPrefsKey } from '../interaction/groupStore'
import * as store from '../projectStore'
import { allModels, type Construction } from '../schema/cfdproj'
import { setSceneMeshes } from '../viewportBus'
import {
  allRegionsVisible,
  anyRegionVisible,
  emptyFlags,
  parseAsciiStlRegions,
  previewRegionColor,
  type PreviewEntry,
  type PreviewFlags,
  setModelVisible,
  setRegionReversed,
  setRegionVisible,
  syncFlagsFromModels,
} from './stlPreviewState'

/** The narrower selection inside the preview: one solid region of one model. */
export interface PreviewRegionSelection {
  modelId: string
  regionName: string
}

let active = false
let flags: PreviewFlags = emptyFlags()
let cache = new Map<string, PreviewEntry>()
let regionSelection: PreviewRegionSelection | null = null
let boundUid: string | null = null
let bound = false

type Listener = () => void
const listeners = new Set<Listener>()

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribePreview(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function isPreviewActive(): boolean {
  return active
}

/**
 * The flags object, by REFERENCE.
 *
 * The shared geometry module writes into it — `ensurePreviewVisibilityState`
 * fills in a default for every model and `applyModelSolidOrientationOverrides`
 * reads the reverse map back out — so handing over a copy would leave it
 * mutating an object nobody reads and every region's normal unflipped.
 */
export function previewFlags(): PreviewFlags {
  return flags
}

export function previewCache(): ReadonlyMap<string, PreviewEntry> {
  return cache
}

export function previewEntry(uid: string): PreviewEntry | null {
  return cache.get(uid) ?? null
}

export function previewRegions(uid: string): string[] {
  return cache.get(uid)?.regions.map((r) => r.name) ?? []
}

export function selectedPreviewRegion(): PreviewRegionSelection | null {
  return regionSelection
}

export function selectPreviewRegion(selection: PreviewRegionSelection | null) {
  regionSelection = selection
  if (selection) {
    store.logOperation(
      'selectPreviewRegion',
      `${selection.modelId} / ${selection.regionName}`,
    )
  }
  emit()
}

// ── persistence ───────────────────────────────────────────────────────────

interface StoredFlags {
  previewVisibility?: unknown
  previewRegionVisibility?: unknown
  previewRegionReverse?: unknown
}

function asBoolMap(value: unknown): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (typeof v === 'boolean') out[k] = v
    }
  }
  return out
}

function asNestedBoolMap(value: unknown): Record<string, Record<string, boolean>> {
  const out: Record<string, Record<string, boolean>> = {}
  if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = asBoolMap(v)
    }
  }
  return out
}

export function readFlagsFromStorage(projectUid: string | null | undefined): PreviewFlags {
  try {
    const raw = localStorage.getItem(uiPrefsKey(projectUid))
    if (!raw) return emptyFlags()
    const parsed = JSON.parse(raw) as StoredFlags | null
    if (!parsed || typeof parsed !== 'object') return emptyFlags()
    return {
      visibility: asBoolMap(parsed.previewVisibility),
      regionVisibility: asNestedBoolMap(parsed.previewRegionVisibility),
      regionReverse: asNestedBoolMap(parsed.previewRegionReverse),
    }
  } catch {
    return emptyFlags()
  }
}

export function writeFlagsToStorage(
  projectUid: string | null | undefined,
  next: PreviewFlags,
): void {
  try {
    const key = uiPrefsKey(projectUid)
    const raw = localStorage.getItem(key)
    let blob: Record<string, unknown> = {}
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown> | null
      // Read-modify-write: the blob also holds the groups and the tree
      // collapse state, and replacing it wholesale would wipe them.
      if (parsed && typeof parsed === 'object') blob = parsed
    }
    blob.previewVisibility = next.visibility
    blob.previewRegionVisibility = next.regionVisibility
    blob.previewRegionReverse = next.regionReverse
    localStorage.setItem(key, JSON.stringify(blob))
  } catch {
    /* private mode / headless: the flags just do not outlive the session */
  }
}

function persist() {
  writeFlagsToStorage(boundUid, flags)
}

/** Re-read the flags when the open project changes. */
export function syncPreviewToProject(project = store.getProject()) {
  const uid = project?.uid ?? null
  if (uid === boundUid) return
  boundUid = uid
  flags = readFlagsFromStorage(uid)
  cache = new Map()
  regionSelection = null
  emit()
}

export function bindPreviewToProject() {
  if (bound) return
  bound = true
  store.subscribe(() => syncPreviewToProject())
  syncPreviewToProject()
}

/** Test seam: forget the bound project so the next sync re-reads storage. */
export function resetPreviewForTest() {
  active = false
  flags = emptyFlags()
  cache = new Map()
  regionSelection = null
  boundUid = null
}

// ── building the preview from the exporter ────────────────────────────────

/**
 * The subset of the shared scene API the preview drives.
 *
 * Named rather than reached for through `GeometrySceneApi`'s index signature,
 * so a rename in the shared module is a compile error here instead of an
 * `undefined is not a function` in the middle of a mode switch.
 */
export interface PreviewExportApi {
  exportModelToStl(model: Construction, isMirrored: boolean, meshList?: unknown): string
  exportCustomModel(model: Construction, parts: string[], isMirrored: boolean): void
  buildThroughZoneStl(model: Construction): { stl?: string } | null
  applyModelSolidOrientationOverrides(model: Construction, stlText: string): string
  withTemporaryRenderedModels(models: Construction[], cb: (meshes: unknown[]) => void): void
}

/**
 * The exact text the exporter would write for ONE model.
 *
 * Ported from `buildModelPreviewStl` (STL_generate.html:18639). The rack branch
 * matters: leakage through-zones are exported down a separate path and would
 * otherwise be missing from the preview while present in the file.
 */
export function buildModelPreviewStl(
  api: PreviewExportApi,
  wrapperGroup: THREE.Object3D,
  model: Construction,
): string {
  wrapperGroup.updateMatrixWorld(true)
  // A mirrored scene transform inverts the winding of every facet, and the
  // exporter compensates. The preview has to ask the same question or its
  // normals point the other way from the file's.
  const isMirrored = wrapperGroup.matrixWorld.determinant() < 0

  if (model.kind === 'custom') {
    const parts: string[] = []
    api.exportCustomModel(model, parts, isMirrored)
    return api.applyModelSolidOrientationOverrides(model, parts.join(''))
  }

  let stl = ''
  // The model may be disabled, or merged into a proxy that carries no export
  // names. Rendering a temporary copy is how the legacy exporter reaches a
  // model's real meshes in both cases.
  api.withTemporaryRenderedModels([model], (tempMeshes) => {
    stl = api.exportModelToStl(model, isMirrored, tempMeshes)
    if (model.kind === 'rack') {
      const leak = api.buildThroughZoneStl(model)
      if (leak?.stl) stl += leak.stl
    }
  })
  return api.applyModelSolidOrientationOverrides(model, stl)
}

/** Run the exporter over every model and split each result into its solids. */
export function buildPreviewEntries(
  api: PreviewExportApi,
  wrapperGroup: THREE.Object3D,
  models: Construction[],
): Map<string, PreviewEntry> {
  const entries = new Map<string, PreviewEntry>()
  for (const model of models) {
    const stlText = buildModelPreviewStl(api, wrapperGroup, model)
    const regions = parseAsciiStlRegions(stlText).map((region, index) => ({
      name: region.name,
      geometry: region.geometry,
      triangleCount: Math.floor((region.geometry.attributes.position?.count ?? 0) / 3),
      color: previewRegionColor(model.uid, region.name, index),
    }))
    entries.set(model.uid, {
      modelId: model.uid,
      modelName: model.meta?.name ?? model.uid,
      kind: String(model.kind ?? ''),
      stlText,
      regions,
      triangleCount: regions.reduce((sum, region) => sum + region.triangleCount, 0),
    })
  }
  return entries
}

/** Called by the scene builder after it has rebuilt the preview. */
export function setPreviewCache(entries: Map<string, PreviewEntry>) {
  cache = entries
  emit()
}

// ── mode switching and the toggles ────────────────────────────────────────

interface SceneBuilderLike {
  rebuild(): void
  registries: { renderMeshes: unknown[] }
}

function liveBuilder(): SceneBuilderLike | null {
  return (
    (window as unknown as { __sceneBuilder?: SceneBuilderLike }).__sceneBuilder ?? null
  )
}

/**
 * Rebuild the viewport.
 *
 * The scene builder reads this module, so a flag change has to ask for a
 * rebuild — nothing else in the app knows the preview changed. `setSceneMeshes`
 * follows because the panels that describe the built scene are otherwise left
 * pointing at meshes that have been disposed.
 */
export function requestPreviewRebuild() {
  const builder = liveBuilder()
  if (!builder) return
  builder.rebuild()
  setSceneMeshes(builder.registries.renderMeshes as never[])
}

function modelList(): { uid: string; enabled: boolean }[] {
  const project = store.getProject()
  if (!project) return []
  return allModels(project).map(({ model }) => ({
    uid: model.uid,
    enabled: model.meta?.enabled !== false,
  }))
}

/**
 * Enter or leave the preview.
 *
 * Entering re-seeds the per-model flags from the modelling view, as
 * `syncPreviewVisibilityFromModelingView` (STL_generate.html:3874) does: the
 * preview opens showing what the user can currently see, and only diverges
 * once they hide something in it.
 */
export function setPreviewActive(next: boolean) {
  if (active === next) return
  active = next
  regionSelection = null
  if (next) {
    syncFlagsFromModels(flags, modelList())
    persist()
  }
  store.logOperation('toggleStlPreviewMode', next ? 'stlPreview' : 'modeling')
  emit()
  requestPreviewRebuild()
}

export function togglePreviewMode() {
  setPreviewActive(!active)
}

/** Re-run the exporter and redraw, without leaving the mode. */
export function refreshPreview() {
  if (!active) return
  store.logOperation('refreshStlPreview', 'Regenerate STL preview')
  requestPreviewRebuild()
}

function enabledOf(uid: string): boolean {
  const project = store.getProject()
  const hit = project ? store.findConstruction(project, uid) : null
  return hit ? hit.model.meta?.enabled !== false : true
}

export function togglePreviewModelVisible(uid: string, checked: boolean) {
  setModelVisible(flags, uid, previewRegions(uid), checked)
  // Logged, not `edit`ed: this is a view preference, so an undo entry would
  // restore an identical project and read as a broken undo. Same rule as the
  // model groups.
  store.logOperation('togglePreviewModelVisible', `${uid}=${checked}`)
  persist()
  emit()
  requestPreviewRebuild()
}

export function togglePreviewRegionVisible(uid: string, regionName: string, checked: boolean) {
  setRegionVisible(flags, uid, regionName, checked, previewRegions(uid), enabledOf(uid))
  if (
    !checked &&
    regionSelection?.modelId === uid &&
    regionSelection.regionName === regionName
  ) {
    regionSelection = null
  }
  store.logOperation('togglePreviewRegionVisible', `${uid}/${regionName}=${checked}`)
  persist()
  emit()
  requestPreviewRebuild()
}

/**
 * Flip one solid's facet winding.
 *
 * This one is NOT preview-only: `applyModelSolidOrientationOverrides` reads the
 * same map on every export path, so the flag changes the bytes written for
 * that solid. An outward normal is what tells snappyHexMesh which side of a
 * surface the fluid is on, so this is the remedy for a region that meshes
 * inside-out.
 */
export function togglePreviewRegionReverse(uid: string, regionName: string, checked: boolean) {
  setRegionReversed(flags, uid, regionName, checked)
  store.logOperation('togglePreviewRegionReverse', `${uid}/${regionName}=${checked}`)
  persist()
  emit()
  requestPreviewRebuild()
}

export function previewModelVisible(uid: string): boolean {
  return anyRegionVisible(flags, uid, previewRegions(uid), enabledOf(uid))
}

export function previewModelFullyVisible(uid: string): boolean {
  return allRegionsVisible(flags, uid, previewRegions(uid), enabledOf(uid))
}

if (typeof window !== 'undefined') {
  // The parity harness drives the mode from a page eval, exactly as the legacy
  // one drives `window.toggleStlPreviewMode()` — which is the ONLY way to reach
  // the mode over there, since its button is gone.
  ;(window as unknown as { __stlPreview?: unknown }).__stlPreview = {
    isActive: isPreviewActive,
    toggle: togglePreviewMode,
    setActive: setPreviewActive,
    refresh: refreshPreview,
    flags: previewFlags,
    entry: previewEntry,
    entries: () => [...cache.values()],
    toggleRegionReverse: togglePreviewRegionReverse,
    toggleRegionVisible: togglePreviewRegionVisible,
    toggleModelVisible: togglePreviewModelVisible,
  }
}
