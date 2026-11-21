/**
 * Building the 3D scene from a project, using the shared geometry modules.
 *
 * This is React's counterpart to `buildGeometryData` in the legacy editor
 * (STL_generate.html): clear, then per enabled model create a group at the
 * model's anchor, apply its transform, and dispatch on `kind` to the renderer
 * that knows how to build it.
 *
 * The renderers themselves are NOT here — they are the shared modules. What is
 * here is the registry and the dispatch loop, which is the part that has to
 * know about React's lifecycle.
 *
 * ## Why the registries are held in one mutable object
 *
 * `geometry_scene.js` reads and writes `renderMeshes`, `renderRoots` and
 * `renderHelpers`, and it must see the CURRENT arrays. A rebuild replaces them
 * — that is what clearing means — so the context exposes accessors onto this
 * holder rather than the arrays themselves. Handing over an array would leave
 * the module writing into one that had already been discarded, and the meshes
 * would build while nothing appeared on screen.
 *
 * ## What is deliberately not copied
 *
 * The legacy builder MERGES every non-selected model group into a single proxy
 * mesh to cut draw calls. Merged proxies carry no `exportName`
 * (`_mergeModelGroup` sets only isMergedProxy/modelId/origColor), so a scene
 * that merges before exporting loses every OpenFOAM patch name. Until export
 * runs against an unmerged scene here, this does not merge at all: slower on
 * very large rooms, and correct.
 */

import * as THREE from 'three'
import { allModels as treeModels, type CfdProject, type Construction } from '../schema/cfdproj'
import {
  createGeometryScene,
  geometryPure,
  type GeometrySceneApi,
  type RenderContext,
} from '../legacy/geometryBuilder'
import { stateV4 } from '../legacy/cfdprojModules'
import { restoreGeometryCache } from '../io/stlAssets'
import {
  buildPreviewEntries,
  isPreviewActive,
  type PreviewExportApi,
  previewFlags,
  setPreviewCache,
} from '../io/stlPreview'
import { anyRegionVisible, isPreviewRegionVisible } from '../io/stlPreviewState'
import { isLiquidModeActive, subscribeLiquidMode } from '../liquid/liquidModeState'
import { buildLiquidEquipmentVisual } from '../geometry/liquidEquipmentVisuals'
import { buildPowerCabinetVisual } from '../geometry/pduEquipmentVisuals'

/** The mutable scene registries the shared modules read and write. */
export interface SceneRegistries {
  renderMeshes: THREE.Object3D[]
  renderRoots: THREE.Object3D[]
  renderHelpers: THREE.Object3D[]
  renderRootMap: Map<string, THREE.Object3D>
  modelMap: Map<string, Construction>
  geometryCache: Map<string, unknown>
}

export function emptyRegistries(): SceneRegistries {
  return {
    renderMeshes: [],
    renderRoots: [],
    renderHelpers: [],
    renderRootMap: new Map(),
    modelMap: new Map(),
    geometryCache: new Map(),
  }
}

export interface SceneHost {
  /** The group every model group is parented to. Identity transform. */
  globalGroup: THREE.Group
  /** Its parent. The legacy code reads its world matrix to detect mirroring. */
  wrapperGroup: THREE.Group
  registries: SceneRegistries
  /** The uid of the selected model, or null. The builders read it for guides. */
  selectedUid: () => string | null
  project: () => CfdProject | null
  /** Rack detail mode, if the host offers it. Absent = always the room view. */
  rackDetail?: () => RackDetailView
  /** Called when an async texture load finishes and the scene should rebuild. */
  onTextureLoaded?: () => void
  /**
   * Called at the end of EVERY build with the model roots it just made — all
   * of them after `rebuild()`, only the re-made ones after `rebuildModels()`.
   *
   * The seam for render-only state that lives on the built materials and is
   * lost with them: the viewport's mesh-preview and post-processing fades
   * (`geometry/viewFades.ts`). One hook rather than a re-apply at each call
   * site, because the call sites are many — a selection's slot-guide rebuild,
   * a drag, a texture landing, rack detail, the STL preview, an automation
   * verb — and the one that forgot the preview fade is exactly how a rack came
   * back solid over the mesh.
   */
  afterBuild?: (roots: readonly THREE.Object3D[]) => void
  /**
   * Whether to build cached PBR materials, procedural textures and edge
   * wireframes. Defaults to true. Set false only where a 2D canvas is
   * unavailable — jsdom returns null from `getContext('2d')`.
   */
  webglAvailable?: boolean
  /**
   * Where the `textures/` folder is served from. The two apps mount it at
   * different roots — the legacy page relative to itself, this one absolute.
   */
  texturesBasePath?: string
  /**
   * STL Preview mode, the legacy's second `displayMode`.
   *
   * Defaults to the module-level store, which is what the live viewport reads:
   * the mode is switched from the toolbar and the tree, neither of which can
   * reach into the host this viewport constructed. A test can pin it.
   */
  previewMode?: () => boolean
}

/**
 * The context the shared modules receive.
 *
 * `uiState` is shaped to exactly the six fields the geometry builders read —
 * enumerated from the source, not guessed:
 *
 *   selection.modelId          which model gets slot guides and stays unmerged
 *   rackDetail.target          rack-detail mode; null means normal mode
 *   displayMode                'liquid' hides air-only decoration
 *   previewVisibility          per-model preview overrides
 *   previewRegionVisibility    per-region overrides for custom STL
 *   previewRegionReverse       per-region normal flips
 *
 * Passing React's own UI state would couple the shared module to this app's
 * shape, and passing the legacy `uiState` is impossible — React has no such
 * object. An incomplete shape is not harmless either: `isRackDetailMode` reads
 * `rackDetail.target` unguarded, so omitting it threw mid-rack.
 */
function makeContext(host: SceneHost): RenderContext {
  const r = host.registries

  // ONE object, not a fresh one per read. `ensurePreviewVisibilityState`
  // (geometry_scene.js:806) WRITES into `uiState.preview*` and
  // `applyModelSolidOrientationOverrides` reads the reverse map back out when
  // it writes the STL, so a throwaway object meant every write was discarded
  // and no region's normal was ever flipped in an exported file.
  const uiState = {
    get selection() {
      return { modelId: host.selectedUid(), type: 'model' }
    },
    // The real target: the builder only spawns a rack's slot guides for the
    // SELECTED rack or the one being edited in detail mode, and hardcoding
    // null here meant detail mode had no slots to click.
    get rackDetail() {
      return { target: host.rackDetail?.().target ?? null }
    },
    get displayMode() {
      return isLiquidModeActive() ? 'liquid' : 'air'
    },
    get previewVisibility() {
      return previewFlags().visibility
    },
    set previewVisibility(v: Record<string, boolean>) {
      previewFlags().visibility = v
    },
    get previewRegionVisibility() {
      return previewFlags().regionVisibility
    },
    set previewRegionVisibility(v: Record<string, Record<string, boolean>>) {
      previewFlags().regionVisibility = v
    },
    get previewRegionReverse() {
      return previewFlags().regionReverse
    },
    set previewRegionReverse(v: Record<string, Record<string, boolean>>) {
      previewFlags().regionReverse = v
    },
  }

  return {
    get state() {
      return host.project()
    },
    get uiState() {
      return uiState
    },
    // Defaults to true, matching the legacy editor: it gates the cached PBR
    // materials, the procedural textures (brushed metal, perforated panels,
    // floor tiles) and the edge wireframes, and with it off the room renders
    // flat white.
    //
    // Overridable because those textures are drawn on a 2D canvas, which jsdom
    // does not provide — `getContext('2d')` returns null there and the first
    // `fillStyle` assignment throws. A headless test can turn it off and still
    // exercise every geometry path; that is an environment limit, not a reason
    // to ship the flat-white configuration.
    get webglAvailable() {
      return host.webglAvailable ?? true
    },
    get renderMeshes() {
      return r.renderMeshes
    },
    set renderMeshes(v: THREE.Object3D[]) {
      r.renderMeshes = v
    },
    get renderRoots() {
      return r.renderRoots
    },
    set renderRoots(v: THREE.Object3D[]) {
      r.renderRoots = v
    },
    get renderHelpers() {
      return r.renderHelpers
    },
    set renderHelpers(v: THREE.Object3D[]) {
      r.renderHelpers = v
    },
    globalGroup: host.globalGroup,
    wrapperGroup: host.wrapperGroup,
    geometryCache: r.geometryCache,
    _renderRootMap: r.renderRootMap,
    _modelMap: r.modelMap,
    // The SHARED module's flat ordering — the .cfdrun/.liquidrun consumers
    // index into this list, so a re-derived order rewires the network.
    allModels: () => {
      const p = host.project()
      return p ? (stateV4().allModels(p) as Construction[]) : []
    },
    ...(host.onTextureLoaded ? { onTextureLoaded: host.onTextureLoaded } : {}),
  }
}

/**
 * Rack detail mode: render ONE rack, unrotated, at the origin.
 *
 * Not a filter over the normal build. The rack has to sit at the origin with
 * identity rotation so "front" means the same thing for every rack no matter
 * how it is placed in the room, which means the other models — when shown for
 * context — are positioned RELATIVE to it and ghosted so they cannot be
 * mistaken for the thing being edited. Mirrors `doRebuildRackDetail`
 * (STL_generate.html:7517).
 */
export interface RackDetailView {
  target: string | null
  showContext: boolean
}

/** The ghost the legacy applies to context models (STL_generate.html:7574). */
function ghost(group: THREE.Object3D) {
  group.traverse((child) => {
    const mat = (child as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined
    if (!mat) return
    const clone = (m: THREE.Material) => {
      const c = m.clone()
      c.opacity = 0.08
      c.transparent = true
      c.depthWrite = false
      return c
    }
    ;(child as THREE.Mesh).material = Array.isArray(mat) ? mat.map(clone) : clone(mat)
    // Layer 2 keeps them out of the raycaster's way: a ghost must not be
    // pickable, or clicking through it selects a model that is not on screen.
    child.layers.set(2)
    child.renderOrder = -1
  })
}

export interface SceneBuilder {
  /** Rebuild every model from scratch. */
  rebuild(): void
  /**
   * Re-render ONLY these models, where they already sit.
   *
   * Falls back to a full `rebuild()` for the two scene shapes that cannot be
   * patched model by model — the STL preview, and rack-detail mode.
   */
  rebuildModels(uids: readonly string[]): void
  /** Rebuild the structural roots when air/liquid display mode changes. */
  watchLiquidMode(afterRebuild?: () => void): () => void
  /** Drop everything and dispose the GPU resources. */
  clear(): void
  /** The scene API, for callers that need a specific renderer or the exporter. */
  api: GeometrySceneApi
  /**
   * The live scene registries.
   *
   * Exposed because the export checks read what was BUILT — a solid's OpenFOAM
   * name lives in its mesh's `userData.exportName` and nowhere else.
   */
  registries: SceneRegistries
}

export function createSceneBuilder(host: SceneHost): SceneBuilder {
  const api = createGeometryScene(makeContext(host))
  const pure = geometryPure()
  const r = host.registries

  // The real PBR surfaces, from the same table the legacy editor uses. They
  // arrive asynchronously and the scene rebuilds when they land — without them
  // only the procedural fallbacks remain and the rack tops, floor vents and ACU
  // grills render flat white.
  if (host.webglAvailable !== false) {
    const base = host.texturesBasePath ?? '/textures'
    const load = api.loadPbrTextureSet as ((base: string) => void) | undefined
    load?.(base)
    // The photographs — the server's rear socket panel, the perforated outlet
    // mesh its patches are drawn with, the power strips, the grilles. They
    // have NO procedural fallback, so without this call a server's outlet is a
    // flat coloured rectangle. The legacy page requests them beside the PBR
    // set (`_preloadImageTextures`); this port requested only the PBR set.
    const loadPhotos = api.loadPhotoTextureSet as ((base: string) => void) | undefined
    loadPhotos?.(base)
  }

  function clear() {
    // Materials are cached by kind, and a cached one holds whatever texture
    // existed when it was first built. PBR images arrive asynchronously AFTER
    // the first build, so without this the rebuild that the texture-load
    // callback triggers hands back the same materials carrying the 256x256
    // procedural fallback — the images load, and nothing on screen changes.
    // `clearRender` does the same thing in the legacy editor.
    const clearMaterials = api.clearMaterialCache as (() => void) | undefined
    clearMaterials?.()

    for (const root of r.renderRoots) {
      root.parent?.remove(root)
      pure.disposeRenderObject(root)
    }
    // REPLACED, not emptied: the shared modules read these through accessors
    // onto this holder, so a fresh array is what they will see next.
    r.renderRoots = []
    r.renderMeshes = []
    r.renderHelpers = []
    r.renderRootMap.clear()
  }

  /** Dispatch one model into the group prepared for it. */
  function renderInto(model: Construction, group: THREE.Group) {
      // Liquid tubes and fittings are drawn by the shared liquid scene. Their
      // OO roots retain ownership and transforms, but have no box-face topology.
      if (model.kind === 'pipe_segment' || model.kind === 'pipe_junction') return
      const faceLayouts = pure.getFaceLayouts(model)
      const islands = pure.buildFaceIslands(model)
      const kind = model.kind

      // Same dispatch as buildGeometryData. `custom` needs a parsed STL in the
      // geometry cache; without one the legacy builder falls through to the box
      // renderer rather than throwing, and so does this.
      if (kind === 'custom' && r.geometryCache.has(model.uid)) {
        api.renderCustomModel(model, group, faceLayouts)
      } else if (kind === 'rack') {
        api.renderRackModel(model, group, faceLayouts)
      } else if (kind === 'wall') {
        api.renderWallModel(model, group, faceLayouts)
      } else if (kind === 'baffle') {
        api.renderBaffleModel(model, group, faceLayouts)
      } else if (kind === 'floor') {
        api.renderFloorModel(model, group, faceLayouts)
      } else if (kind === 'partitionWall') {
        api.renderPartitionWallModel(model, group, faceLayouts)
      } else if (kind === 'pipe') {
        api.renderPipeModel(model, group)
      } else if (kind === 'acu' && (model as { cooling?: { unitType?: unknown } }).cooling?.unitType === 'wallMounted') {
        api.renderWallAcuModel(model, group, faceLayouts, islands)
      } else {
        api.renderBoxModel(model, group, faceLayouts, islands)
      }

      // Explicit power-cabinet names get display-only detail in either mode.
      // The physical kind remains BOX and the original export registry is
      // untouched. The role only makes whole-cabinet selection readable.
      const powerVisual = buildPowerCabinetVisual(model)
      if (powerVisual) {
        group.userData.equipmentVisualRole = powerVisual.userData.equipmentVisualRole
        group.add(powerVisual)
      }

      // The CDU cabinet is the same physical asset in either simulation mode.
      // Rack liquid detail follows the liquid view. These children belong to
      // their structural root and remain outside the CFD/STL registry.
      if (kind === 'cdu' || isLiquidModeActive()) {
        const visual = buildLiquidEquipmentVisual(model)
        if (visual) group.add(visual)
      }
  }

  /**
   * The STL Preview build: one mesh per exported `solid`, not per model face.
   *
   * Mirrors `doRebuildStlPreview` (STL_generate.html:7600). Three details are
   * load-bearing:
   *
   * - The parsed geometry is in WORLD coordinates, because that is what the
   *   exporter writes. Each preview root therefore carries the INVERSE of
   *   `globalGroup`'s world matrix, so those coordinates land where the file
   *   puts them however the scene above is transformed. Parenting them under a
   *   transformed group without that would apply the transform twice.
   * - They hang off `globalGroup` — the same group the modelling roots use —
   *   and not off the wrapper's parent. The viewport re-parents `globalGroup`
   *   into a fresh scene when its setup effect re-runs but keeps ONE builder,
   *   so a root attached relative to `wrapperGroup` ends up in a scene that is
   *   no longer rendered: 52 meshes built, an empty viewport, no error.
   * - The cache is rebuilt every time rather than kept: it IS the answer to
   *   "what would be exported right now", and a stale one answers for a
   *   project that has since been edited.
   */
  function rebuildPreview(models: Construction[]) {
    // `wrapperGroup`, because that is the object the shared exporter reads for
    // the mirror test (`ctx.wrapperGroup`). Asking a different one would let
    // the preview and the file disagree about the winding.
    const entries = buildPreviewEntries(
      api as unknown as PreviewExportApi,
      host.wrapperGroup,
      models,
    )
    setPreviewCache(entries)
    const flags = previewFlags()
    host.globalGroup.updateMatrixWorld(true)
    const worldToLocal = new THREE.Matrix4().copy(host.globalGroup.matrixWorld).invert()

    for (const model of models) {
      const entry = entries.get(model.uid)
      if (!entry?.regions.length) continue
      const names = entry.regions.map((region) => region.name)
      const enabled = model.meta?.enabled !== false
      if (!anyRegionVisible(flags, model.uid, names, enabled)) continue

      const group = new THREE.Group()
      // Fixed matrix, not position/quaternion: this is the inverse of whatever
      // sits above, and letting three recompose it from a decomposition would
      // lose a mirror.
      group.matrixAutoUpdate = false
      group.matrix.copy(worldToLocal)
      group.userData = { modelId: model.uid, isStlPreviewRoot: true }
      host.globalGroup.add(group)
      r.renderRoots.push(group)
      r.renderRootMap.set(model.uid, group)

      // A floor of the room at 15% would be invisible against its own regions,
      // so the preview never goes below 0.22 however faded the model is.
      const opacity = Math.max(
        0.22,
        (model.meta as { display?: { opacity?: number } } | undefined)?.display?.opacity ?? 1,
      )
      for (const region of entry.regions) {
        if (!isPreviewRegionVisible(flags, model.uid, region.name)) continue
        const spawn = api.spawnRenderable as (
          group: THREE.Object3D,
          geom: THREE.BufferGeometry,
          options: Record<string, unknown>,
        ) => unknown
        spawn(group, region.geometry.clone(), {
          color: region.color,
          opacity,
          doubleSide: true,
          depthWrite: opacity >= 0.999,
          userData: {
            modelId: model.uid,
            regionName: region.name,
            // The solid name IS the OpenFOAM patch name, and this view exists
            // to show it — so it travels on the mesh the same way it does on a
            // modelling mesh.
            exportName: region.name,
            isPreviewRegion: true,
            isBaseWall: false,
          },
        })
      }
    }
    host.globalGroup.updateMatrixWorld(true)
  }

  function rebuild() {
    buildAll()
    // Every path above ends here: the room, rack detail and the STL preview.
    // An early return for "no project" hands over an empty list, which is a
    // no-op for any hook.
    host.afterBuild?.(r.renderRoots)
  }

  function buildAll() {
    clear()
    const project = host.project()
    if (!project) return

    r.modelMap.clear()
    for (const { model } of treeModels(project)) r.modelMap.set(model.uid, model)

    const detail = host.rackDetail?.()
    const models = stateV4().allModels(project) as Construction[]

    if (host.previewMode?.() ?? isPreviewActive()) {
      // The custom-STL branch of the exporter reads the parsed asset out of the
      // geometry cache, so it has to be filled before the preview runs too.
      restoreGeometryCache(project, r.geometryCache, models)
      rebuildPreview(models)
      return
    }
    // Custom models draw from the geometry cache and fall back to a BOX
    // without it, so this has to run before the dispatch below — not once at
    // load: undo, redo and a project swap all change which meshes are needed.
    restoreGeometryCache(project, r.geometryCache, models)
    const target = detail?.target ? models.find((m) => m.uid === detail.target) : undefined

    if (target && target.kind === 'rack') {
      const group = new THREE.Group()
      // At the origin, unrotated — see the RackDetailView note.
      group.userData = { modelId: target.uid }
      host.globalGroup.add(group)
      r.renderRoots.push(group)
      r.renderRootMap.set(target.uid, group)
      renderInto(target, group)

      if (detail?.showContext) {
        const base = target.geometry?.location
        for (const model of models) {
          if (model.uid === target.uid || !api.isModelEnabled(model)) continue
          const ctx = new THREE.Group()
          const loc = model.geometry?.location
          ctx.position.set(
            (loc?.x ?? 0) - (base?.x ?? 0),
            (loc?.y ?? 0) - (base?.y ?? 0),
            (loc?.z ?? 0) - (base?.z ?? 0),
          )
          ctx.userData = { modelId: model.uid, isRackDetailContext: true }
          pure.applyModelTransformToObject(ctx, model)
          host.globalGroup.add(ctx)
          r.renderRoots.push(ctx)
          renderInto(model, ctx)
          ghost(ctx)
        }
      }
      host.wrapperGroup.updateMatrixWorld(true)
      return
    }

    for (const model of models) {
      if (!api.isModelEnabled(model)) continue

      const group = new THREE.Group()
      const loc = model.geometry?.location
      group.position.set(loc?.x ?? 0, loc?.y ?? 0, loc?.z ?? 0)
      group.userData = { modelId: model.uid }
      pure.applyModelTransformToObject(group, model)
      host.globalGroup.add(group)
      r.renderRoots.push(group)
      r.renderRootMap.set(model.uid, group)
      renderInto(model, group)
    }

    host.wrapperGroup.updateMatrixWorld(true)
  }

  function watchLiquidMode(afterRebuild?: () => void) {
    return subscribeLiquidMode(() => {
      rebuild()
      afterRebuild?.()
    })
  }

  /**
   * Re-render a handful of models instead of the room.
   *
   * ## Why this exists
   *
   * A rack's U-slot guides are built only for the SELECTED rack — 200 racks x 42
   * slots x 2 faces would be 16,800 transparent meshes otherwise — so picking a
   * different rack has to rebuild SOMETHING. That something used to be
   * everything: on `case/Alibaba-hongchou` one click in the 3D view rebuilt all
   * 287 models and 15,153 meshes, which measured at 1.3 s in jsdom with textures
   * switched off and several times that in the browser. The user's report was
   * "I click anything and it hangs for a very long time before the tree
   * navigates"; this is the hang, and it had nothing to do with the tree.
   *
   * Two racks are re-rendered for one selection change — the one that just lost
   * its guides and the one that just gained them — so the cost is now what one
   * rack costs.
   *
   * ## Order is preserved
   *
   * The shared builder APPENDS to `renderMeshes` / `renderRoots`, and
   * `io/stlExport` walks `renderMeshes` in that order to group solids. Splicing
   * the new meshes back into the slot the old ones held keeps a targeted rebuild
   * byte-equivalent, for the exporter, to the full rebuild it replaces.
   */
  function rebuildModels(uids: readonly string[]) {
    const project = host.project()
    if (!project) return
    // The preview draws one mesh per exported SOLID and rack detail draws one
    // rack at the origin with the rest ghosted; neither has a per-model slot to
    // patch, and both are rare enough not to care.
    if (host.previewMode?.() ?? isPreviewActive()) {
      rebuild()
      return
    }
    if (host.rackDetail?.().target) {
      rebuild()
      return
    }
    const wanted = new Set(uids.filter(Boolean))
    if (!wanted.size) return

    r.modelMap.clear()
    for (const { model } of treeModels(project)) r.modelMap.set(model.uid, model)

    const models = stateV4().allModels(project) as Construction[]
    restoreGeometryCache(project, r.geometryCache, models)

    const rebuilt: THREE.Object3D[] = []
    for (const uid of wanted) {
      const old = r.renderRootMap.get(uid)
      let meshAt = -1
      let rootAt = -1
      if (old) {
        const doomed = new Set<THREE.Object3D>()
        old.traverse((o) => doomed.add(o))
        old.parent?.remove(old)
        pure.disposeRenderObject(old)
        rootAt = r.renderRoots.indexOf(old)
        r.renderRootMap.delete(uid)
        r.renderRoots = r.renderRoots.filter((o) => o !== old)
        // Everything before the first removed mesh survives the filter at the
        // same index, so this stays a valid insertion point.
        meshAt = r.renderMeshes.findIndex((o) => doomed.has(o))
        r.renderMeshes = r.renderMeshes.filter((o) => !doomed.has(o))
        r.renderHelpers = r.renderHelpers.filter((o) => !doomed.has(o))
      }

      const model = r.modelMap.get(uid)
      if (!model || !api.isModelEnabled(model)) continue

      const meshFrom = r.renderMeshes.length
      const group = new THREE.Group()
      const loc = model.geometry?.location
      group.position.set(loc?.x ?? 0, loc?.y ?? 0, loc?.z ?? 0)
      group.userData = { modelId: model.uid }
      pure.applyModelTransformToObject(group, model)
      host.globalGroup.add(group)
      r.renderRoots.push(group)
      r.renderRootMap.set(model.uid, group)
      renderInto(model, group)

      if (meshAt >= 0 && r.renderMeshes.length > meshFrom) {
        r.renderMeshes.splice(meshAt, 0, ...r.renderMeshes.splice(meshFrom))
      }
      if (rootAt >= 0 && rootAt < r.renderRoots.length - 1) {
        r.renderRoots.splice(rootAt, 0, r.renderRoots.pop()!)
      }
      rebuilt.push(group)
    }

    host.wrapperGroup.updateMatrixWorld(true)
    // Only the roots re-made here: the rest of the room kept its materials,
    // and whatever was written onto them is still there.
    if (rebuilt.length) host.afterBuild?.(rebuilt)
  }

  return { rebuild, rebuildModels, watchLiquidMode, clear, api, registries: r }
}
