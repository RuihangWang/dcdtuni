/**
 * The geometry builder, shared with the legacy editor rather than reimplemented.
 *
 * `js/geometry_pure.js` (62 functions) and `js/geometry_scene.js` (51) are the
 * code that turns a .cfdproj model into Three.js meshes: rack frames, U-slots,
 * server faces and their patches, blanking panels, partition-wall CSG,
 * planar-polygon walls and floors, custom-STL regions, materials and textures.
 *
 * React cannot have its own version of this. The STL export reads the meshes the
 * scene built — `renderMeshes`, each tagged with `userData.exportName` — so the
 * exported file is a function of the RENDERED geometry, and a second builder
 * would produce a second, silently different set of OpenFOAM patches. The whole
 * point of `parity/stl_oracle.py` is that "looks the same" is not a check here.
 *
 * ## The render context
 *
 * `geometry_scene.js` needs live scene state, so it takes it as an injected
 * context instead of reaching for globals. Six of the ten required entries are
 * REASSIGNED during a project load in the legacy app (`renderMeshes = []` on
 * clear), which is why they are accessors there. React reassigns them too — a
 * project load builds fresh arrays — so the same discipline applies: hand over
 * accessors, not the arrays themselves, or the module writes into a registry
 * this app has already discarded and nothing ever appears.
 *
 * ## Globals the modules still need
 *
 * `THREE` — read off the global, not imported, because both apps must share ONE
 * Three.js instance. Two copies of Three do not interoperate: `instanceof`
 * checks fail across them and a mesh built by one cannot be added to the
 * other's scene.
 *
 * `polygonClipping` — six builders call it (face islands, planar patches, box
 * faces, server faces). Without it racks do not build at all, so it is imported
 * from the vendored copy rather than a CDN.
 *
 * `cfdprojStateV4` and `rackState` — imported here too rather than assumed. The
 * geometry modules call `getDefaultModelTransform` and `_rackServerView` out of
 * rack_state.js, and relying on some other module having imported it first is
 * the same load-order accident this file exists to remove.
 */

import * as THREE from 'three'

import type { CfdProject, Construction } from '../schema/cfdproj'

// Order matters and is enforced by the modules themselves, which throw a named
// error if a sibling is missing. Installed before the geometry modules import
// so their lazy lookups resolve on first use.
function installThree() {
  const w = window as unknown as Record<string, unknown>
  // ONE Three.js instance across both apps — see the note above.
  //
  // Published as a mutable COPY of the namespace, not the namespace itself. A
  // module namespace object is immutable, and the legacy modules ADD to the
  // global THREE — `js/liquid_mode.js` checks `THREE.TransformControls`, which
  // an app has to install because it is an addon rather than a core export.
  // Assigning onto the namespace throws "Cannot assign to property … of
  // [object Module]", and it throws inside a viewport effect, which takes the
  // whole app down to a blank page.
  //
  // A copy is safe here because three's exports are classes and constants that
  // never change after load, and every `instanceof` still compares against the
  // same class objects.
  if (!w.THREE) w.THREE = { ...THREE }
  // A FEATURE FLAG the shared geometry module reads off the global, set by the
  // legacy page at STL_generate.html:1608. With it on, a rack's 20 frame
  // sub-meshes share one `{rack}_frame` export name and become a single solid;
  // with it off (undefined, as it was here) each panel becomes its own solid.
  //
  // That is not cosmetic. Measured on Base_leakage: merged_rack_frame.stl had
  // 4 solids from the legacy app and 80 from this one, and the .cfdrun that
  // follows carried 122 boundary bindings instead of 46 — a different set of
  // OpenFOAM patches from the same project, and ~20x the snappy
  // refinementSurfaces on a rack-heavy room.
  if (w.MERGE_RACK_FRAME_SOLIDS === undefined) w.MERGE_RACK_FRAME_SOLIDS = true
}

installThree()

// The CSG library a partition wall's holes are cut with (geometry_scene.js
// reads `window.ThreeCSG.CSG`). The vendored bundle is an IIFE that assigns
// a top-level `var ThreeCSG` and asks `require('three')` for its classes; the
// legacy page runs it as a classic script with a one-line `window.require`
// shim (STL_generate.html:28). As an ES module import neither would work —
// the `var` stays module-scoped and there is no `require` — so the source is
// evaluated here with `require` bound to the THREE the scene is built with.
// Without it the first partition wall threw inside rebuild() and every model
// after it vanished from the viewport.
// eslint-disable-next-line import/first
import threeCsgSource from '../../../shared/vendor/three-csg-ts.umd.js?raw'

function installThreeCsg(): void {
  const w = window as unknown as Record<string, unknown>
  if (w.ThreeCSG) return
  const requireThree = (name: string) => {
    if (name === 'three') return w.THREE
    throw new Error(`three-csg-ts: unknown module ${name}`)
  }
  // eslint-disable-next-line @typescript-eslint/no-implied-eval, no-new-func
  w.ThreeCSG = new Function('require', `${threeCsgSource}\nreturn ThreeCSG;`)(requireThree)
}

installThreeCsg()

// Side-effect imports: each attaches its API to `window`.
//
// polygon-clipping needs care, and it behaves DIFFERENTLY in the two places
// this code runs:
//
//   browser (Vite ESM) — no CommonJS `module`, so the UMD header takes its
//     global branch and assigns `globalThis.polygonClipping`. A `default`
//     import here is a hard SyntaxError that blanks the whole app.
//   vitest (jsdom + CJS interop) — there IS a `module`, so it takes the
//     `module.exports` branch and installs NOTHING on the global.
//
// A namespace import covers both: it never asks for a named export, and it
// still gives access to whatever the CJS branch produced.
// eslint-disable-next-line import/first
import * as polygonClippingModule from '../../../shared/vendor/polygon-clipping.umd.js'
import '../../../shared/vendor/earcut.umd.js'
// eslint-disable-next-line import/first
import '../../../shared/js/cfdproj_schema.js'
// eslint-disable-next-line import/first
import '../../../shared/js/cfdproj_state_v4.js'
// eslint-disable-next-line import/first
import '../../../shared/js/model_pose_ports.js'
// eslint-disable-next-line import/first
import '../../../shared/js/rack_state.js'
// eslint-disable-next-line import/first
import '../../../shared/js/stl_writer.js'
// eslint-disable-next-line import/first
import '../../../shared/js/stl_naming.js'
import '../../../shared/js/box_geometry_product.js'
// eslint-disable-next-line import/first
import '../../../shared/js/transform_tools.js'
// eslint-disable-next-line import/first
import '../../../shared/js/box_material_recipe.js'
import '../../../shared/js/pbr_materials.js'
// eslint-disable-next-line import/first
import '../../../shared/js/geometry_pure.js'
// eslint-disable-next-line import/first
import '../../../shared/js/geometry_scene.js'
import '../../../shared/js/patch_textures.js'
import '../../../shared/js/supply_flow_anim.js'

{
  const w = window as unknown as Record<string, unknown>
  if (!w.polygonClipping) {
    const ns = polygonClippingModule as unknown as { default?: unknown }
    w.polygonClipping = ns.default ?? polygonClippingModule
  }
  // Checked by the three METHODS the builders actually call, not by asking
  // whether it is a function — under vitest the namespace is a plain object
  // carrying union/intersection/xor/difference, and demanding a callable
  // rejected a perfectly good module.
  const pc = w.polygonClipping as Record<string, unknown>
  for (const op of ['union', 'intersection', 'difference']) {
    if (typeof pc?.[op] !== 'function') {
      // Not a nicety: six builders call these — face islands, planar patches,
      // box faces, server faces — so without them the first rack throws, and a
      // silent absence surfaces as an unexplained blank viewport.
      throw new Error(
        `polygonClipping.${op} is missing. Neither the UMD global branch nor the ` +
          'module namespace produced a usable polygon-clipping.'
      )
    }
  }
}

/** The live scene state `geometry_scene.js` reads and writes. */
export interface RenderContext {
  /** The project. Reassigned on load, so pass an accessor. */
  readonly state: CfdProject | null
  /** UI state — the builder reads `selection.modelId` to decide what to merge. */
  readonly uiState: unknown
  readonly webglAvailable?: boolean
  /** Every mesh that should be pickable and exportable. Reassigned on clear. */
  renderMeshes: THREE.Object3D[]
  /** One THREE.Group per model, in creation order. Reassigned on clear. */
  renderRoots: THREE.Object3D[]
  /** Non-exported helpers (anchor markers, guides). Reassigned on clear. */
  renderHelpers: THREE.Object3D[]
  readonly globalGroup: THREE.Group
  readonly wrapperGroup: THREE.Group
  readonly geometryCache: Map<string, unknown>
  readonly _renderRootMap: Map<string, THREE.Object3D>
  readonly _modelMap: Map<string, Construction>
  /**
   * Called ~100 ms after an external texture finishes loading.
   *
   * Optional. The module does not know how this app rebuilds, so it asks; an
   * app with no rebuild loop (a headless export) can leave it out.
   */
  onTextureLoaded?: () => void
  /**
   * The flat construction list, in the SHARED module's order.
   *
   * Optional: without it the module derives the list from `state` through
   * `cfdprojStateV4.allModels`, which is the same ordering. Supply it only to
   * avoid re-deriving it on every mesh — `spawnRenderable` asks for the count
   * once per mesh to decide whether to skip edge wireframes.
   *
   * The ORDER is not cosmetic. The .liquidrun and .cfdrun consumers index into
   * this list, so a different order rewires the network.
   */
  allModels?: () => Construction[]
}

export interface GeometrySceneApi {
  buildGroupedStlMap(modelIds: string[], includeLeakage: boolean): Record<string, string>
  renderRackModel(model: Construction, group: THREE.Group, faceLayouts: unknown): void
  renderBoxModel(model: Construction, group: THREE.Group, faceLayouts: unknown, islands: unknown): void
  renderWallModel(model: Construction, group: THREE.Group, faceLayouts: unknown): void
  renderBaffleModel(model: Construction, group: THREE.Group, faceLayouts: unknown): void
  renderFloorModel(model: Construction, group: THREE.Group, faceLayouts: unknown): void
  renderPartitionWallModel(model: Construction, group: THREE.Group, faceLayouts: unknown): void
  renderPipeModel(model: Construction, group: THREE.Group): void
  renderWallAcuModel(model: Construction, group: THREE.Group, faceLayouts: unknown, islands: unknown): void
  renderCustomModel(model: Construction, group: THREE.Group, faceLayouts: unknown): void
  isModelEnabled(model: Construction): boolean
  getModelById(uid: string): Construction | null
  [key: string]: unknown
}

export interface GeometryPureApi {
  /** The same rack blanking ranges and solid names used by the scene STL exporter. */
  getRackMergedEmptySlotRanges(model: Construction, side: 'front' | 'back'): Array<{
    startUnit: number; endUnit: number; isWallBaffle: boolean
  }>
  getRackMergedSlotExportName(model: Construction, side: 'front' | 'back', startUnit: number, endUnit: number): string
  getFaceLayouts(model: Construction): unknown
  buildFaceIslands(model: Construction): unknown
  applyModelTransformToObject(object: THREE.Object3D, model: Construction): void
  modelLocalPointToWorld(model: Construction, point: { x: number; y: number; z: number }): THREE.Vector3 | null
  modelLocalDirectionToWorld(model: Construction, direction: { x: number; y: number; z: number }): THREE.Vector3 | null
  liquidEquipmentPortFrame(
    model: Construction,
    port: string,
    end?: 'top' | 'bottom',
  ): {
    position: THREE.Vector3
    direction: THREE.Vector3
    localPosition: THREE.Vector3
    localDirection: THREE.Vector3
  } | null
  disposeRenderObject(object: THREE.Object3D): void
  /**
   * The viewport marker sizes both editors share, so one selection draws the
   * same red anchor ball beside the same blue handle cubes in either app.
   */
  /** Side of a resize-handle cube, in metres, for a camera `dist` metres away. */
  handleCubeSize(dist: number): number
  /** The anchor ball's model-relative radius: 3.5% of the longest side, 18-90 mm. */
  anchorMarkerRadius(size?: { x?: number; y?: number; z?: number } | null): number
  /** The radius the ball is drawn at: its own, or 0.9 x the handle cube once that is larger. */
  anchorDisplayRadius(base: number, dist: number): number
  /** Between the edge cubes (11) and the corner cubes (12). */
  readonly ANCHOR_RENDER_ORDER: number
  /**
   * Viewport-helper camera scaling (WP-S1/WP-S3), shared with the legacy
   * editor. A registered helper is drawn at `helperDisplayRadius(dataRadius,
   * dist)` — never larger than its data radius, shrinking as the camera
   * closes in. OPTIONAL until the shared layer lands in `geometry_pure.js`;
   * every caller optional-chains these, and `geometry/helperRegistry.ts`
   * reports absence so callers can fall back to writing scale themselves.
   */
  /** The radius a helper of `dataRadius` is drawn at, `dist` from the camera. */
  helperDisplayRadius?(dataRadius: number, dist: number): number
  /** Hand a helper mesh to the per-frame `syncHelperScales`. */
  registerHelper?(mesh: THREE.Object3D, dataRadius: number): void
  /** Release a mesh handed to `registerHelper`. */
  unregisterHelper?(mesh: THREE.Object3D): void
  /** Resize every registered helper for this camera; once per rendered frame. */
  syncHelperScales?(camera: THREE.Camera): void
  /**
   * A 2D-designed floor/ceiling's outline, in its TOP face layout's (u, v):
   * the selection draws one handle per vertex and per edge from these.
   */
  sheetPolygon(model: Construction): number[][] | null
  isPolygonSheet(model: Construction): boolean
  polygonHandlePointsUV(poly: number[][]): {
    vertices: number[][]
    edges: { index: number; mid: number[]; normal: number[]; length: number }[]
  }
  movePolygonVertex(poly: number[][], index: number, u: number, v: number): number[][]
  movePolygonEdge(poly: number[][], index: number, du: number, dv: number): number[][]
  polygonSelfIntersects(poly: number[][]): boolean
  /** Re-base to the polygon's own box without moving it in the world. */
  rebasePolygonSheet(
    model: Construction,
    rotate?: (v: { x: number; y: number; z: number }) => { x: number; y: number; z: number },
  ): boolean
  [key: string]: unknown
}

function required<T>(value: T | undefined, name: string): T {
  if (!value) {
    throw new Error(
      `${name} is missing — the shared geometry modules did not load. They attach ` +
        'to window on import; check the import order in legacy/geometryBuilder.ts.'
    )
  }
  return value
}

/** The shared liquid-equipment PBR library (`shared/js/pbr_materials.js`). */
export interface PbrMaterialsApi {
  readonly SETS: Record<string, { meanRoughness: number; tintable: boolean }>
  readonly ROLES: Record<string, {
    set: string; roughness: number; metalness: number; normalScale: number; tileM: number; color: number
  }>
  readonly MAP_FILES: Record<'diffuse' | 'normal' | 'roughness', string>
  loadTextureSets(basePath: string): void
  registerTexture(set: string, mapType: 'diffuse' | 'normal' | 'roughness', texture: THREE.Texture): void
  isSetReady(set: string): boolean
  material(role: string, options?: THREE.MeshStandardMaterialParameters & { normalScale?: number }): THREE.MeshStandardMaterial
  scaleUv(geometry: THREE.BufferGeometry, su: number, sv: number, swap?: boolean): THREE.BufferGeometry
  tubeUv(geometry: THREE.BufferGeometry, lengthM: number, radius: number, role: string, tileM?: number): THREE.BufferGeometry
  cylinderUv(geometry: THREE.BufferGeometry, radius: number, height: number, role: string, tileM?: number): THREE.BufferGeometry
  boxUv(geometry: THREE.BufferGeometry, role: string, tileM?: number): THREE.BufferGeometry
  _resetForTests(): void
}

export function pbrMaterials(): PbrMaterialsApi {
  return required(
    (window as unknown as { pbrMaterials?: PbrMaterialsApi }).pbrMaterials,
    'window.pbrMaterials'
  )
}

export function geometryPure(): GeometryPureApi {
  return required(
    (window as unknown as { geometryPure?: GeometryPureApi }).geometryPure,
    'window.geometryPure'
  )
}

export function createGeometryScene(ctx: RenderContext): GeometrySceneApi {
  const mod = required(
    (window as unknown as { geometryScene?: { create(c: RenderContext): GeometrySceneApi } })
      .geometryScene,
    'window.geometryScene'
  )
  return mod.create(ctx)
}
