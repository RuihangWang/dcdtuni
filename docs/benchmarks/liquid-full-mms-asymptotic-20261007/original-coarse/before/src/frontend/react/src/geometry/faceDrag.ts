/**
 * Direct manipulation on a face: drag a patch across it, drag an edge to resize.
 *
 * The legacy's `beginPatchDrag` / `updatePatchDrag` / `endPatchDrag`
 * (STL_generate.html:16266, :16562, :17216), minus the modes this port does not
 * have (the custom-STL projection vector) and plus nothing.
 *
 * Everything numeric lives in `faceDragMath.ts` and everything that writes the
 * project in `faceDragActions.ts`; what is left here is the part that can only
 * be done against a live scene — turning a pointer into a ray, deciding which
 * of the overlapping things under it the user meant, and keeping the rendered
 * geometry in step with a project that is being mutated sixty times a second.
 *
 * Two rules carried over from the legacy, both of which look like details and
 * are not:
 *
 * - **A patch must already be SELECTED before its body can be dragged.**
 *   Otherwise the first click on a rack face would move the patch it landed on
 *   instead of selecting it, and there would be no way to just look at one.
 *
 * - **One undo entry per drag.** The frames mutate the live project; the
 *   starting values go back on release and a single `store.edit` commits.
 */

import * as THREE from 'three'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { CfdProject, Construction } from '../schema/cfdproj'
import * as actions from './faceDragActions'
import {
  applyPatchSnapRect,
  applyStickyResize,
  bestEdgeTarget,
  bestPatchSnap,
  DIM_DIR,
  edgeRangeProjection,
  localAxisAlongDir,
  ownEdgeRange,
  isCircleRingGrab,
  localToFace,
  modelFaceCornersLocal,
  movedCircle,
  movedRect,
  partitionWallCornersLocal,
  rectCornersLocal,
  resizedCircle,
  resizedRect,
  STICKY_ENGAGE,
  stickyHolds,
  stickyTarget,
  wallResizeStep,
  withinProjectBounds,
  type EdgeKey,
  type FacePoint,
  type OrientedBox,
  type ProjectBounds,
  type Quat,
  type Vec3,
} from './faceDragMath'
import {
  basisOf,
  faceAxes,
  facesOf,
  layoutOf,
  locationOf,
  rectOf,
  sizeOf,
  syncPartitionWall,
  v3,
} from './faceDragModel'
import { averageModelSize, modelEdgeTargets, patchSnapTargets } from './faceDragSnap'

/** The model's rotation, for mapping world snap deltas onto local axes. */
function quatOf(model: Construction): Quat | null {
  return (model.geometry as { quaternion?: Quat } | undefined)?.quaternion ?? null
}

/**
 * The box the edge snap measures, with the model's rotation and scale but the
 * DRAG's location and size.
 *
 * Not `getModelOBB(model)`: that reads `geometry.size`/`geometry.location`,
 * which during a drag still hold the previous frame's box — snap included. The
 * raw edge would then be a function of the last snapped output and the resize
 * oscillates instead of following the pointer (see `ownEdgeRange`). A rotated
 * body still needs its orientation, because its axis-aligned world box is
 * bigger than the body; `modelEdgeTargets` applies the same rule to the OTHER
 * models.
 */
function orientedBoxOf(model: Construction, location: Vec3, size: Vec3): OrientedBox {
  const geometry = model.geometry as { quaternion?: Quat; scale?: Vec3 } | undefined
  return {
    location,
    size,
    quaternion: geometry?.quaternion ?? null,
    scale: geometry?.scale ?? null,
  }
}
import type { TransformToolsApi } from '../legacy/transformTools'
import { faceExtent, type FaceRecord, type PatchRecord } from './patchGeometry'
import { pickNearestHit } from '../interaction/picking'
import * as store from '../projectStore'
import type { SceneRegistries } from '../interaction/sceneBuilder'
import { createResizeHandles, type HandleKey } from '../ui/resizeHandles'
import { geometryPure } from '../legacy/geometryBuilder'
import {
  commitServerPatchGeometry,
  enforceServerPatchSafety,
  findServerPatch,
  serverFaceLayout,
  serverFacePoint,
  serverRectMetres,
  type ServerFaceLayout,
} from './serverPatchSite'
import type { TreeSelection } from '../interaction/treeNodes'

/**
 * The kinds whose face patches can be dragged — `BOX_PATCH_DRAG_KINDS`
 * (STL_generate.html:2439). A rack is not in it: its patches belong to the
 * servers inside, which have their own face rectangles. A custom-STL model is
 * not either: its patches are projected beams positioned by `px/py/pz`, not
 * rectangles on a face.
 */
const PATCH_DRAG_KINDS = new Set(['box', 'partitionWall', 'acu', 'wall', 'baffle', 'floor'])

/** Kinds whose FACE can be resized by its handles (`_updateResizeHandles`). */
const FACE_RESIZE_KINDS = new Set(['wall', 'baffle', 'floor', 'box', 'acu', 'partitionWall'])

/** Kinds where selecting the MODEL is enough to offer the resize handles. */
const PLANAR_KINDS = new Set(['wall', 'baffle', 'floor', 'partitionWall'])

const CIRCLE_SEGMENTS = 48
const PATCH_SNAP_THROTTLE_MS = 33

export interface FaceDragHost {
  scene: THREE.Scene
  camera: THREE.Camera
  domElement: HTMLElement
  orbit: OrbitControls | null
  registries: () => SceneRegistries
  tools: () => TransformToolsApi | null
  project: () => CfdProject | null
  /** The current narrow selection — a patch, a face, or the model itself. */
  selection: () => { uid: string | null; node: TreeSelection | null }
  snap: () => { face: boolean; edge: boolean }
  /** Rebuild the drawn geometry from the live project, at most once a frame. */
  rebuildSoon: (uids?: string[]) => void
  /** Rebuild now, after the commit that ends a drag. */
  rebuildNow: (uids?: string[]) => void
  /** False while the UI is frozen or a non-geometry tab is open. */
  enabled: () => boolean
  /**
   * Called after every redraw of the handles, shown or hidden. Whatever is
   * sized against the cubes (the red anchor balls) follows them from here: a
   * click on the ALREADY-selected patch starts a body drag rather than a
   * selection, and its no-move release rebuilds the cubes for the current
   * camera with no selection change for anything else to react to.
   */
  handlesRefreshed?: () => void
}

export interface FaceDragTools {
  /** Re-draw the handles for the current selection. */
  refresh(): void
  /**
   * Re-size the drawn handles for the camera's current distance — once a frame
   * from the render loop, so a zoom shrinks and grows them with the view
   * instead of leaving the size they were built at.
   */
  syncScale(): void
  /** Try to take the pointer. True means the drag is ours and the click is not. */
  begin(event: PointerEvent): boolean
  /** Light the handle under the pointer, so it reads as grabbable. */
  hover(event: PointerEvent): void
  /**
   * The selected patch's ANCHOR, in world space: the point its inspector
   * numbers describe — a rect's (x, y) corner before any corner offset, a
   * circle's centre — on the surface the handles are drawn on. That surface is
   * a server's face for a server patch (stored in fractions of it) and the
   * curve itself on a wall-mounted unit's front, where the flat face layout
   * would put the point in mid-air. Null when no patch with handles is
   * selected, and for a partition wall's circle, which has no ring either.
   */
  anchorPoint(): THREE.Vector3 | null
  dragging(): boolean
  dispose(): void
}

type DragMode =
  | 'patchMove'
  | 'patchRectResize'
  | 'patchCircleResize'
  | 'modelResize'
  | 'cornerMove'
  | 'polygonVertex'
  | 'polygonEdge'

interface DragState {
  mode: DragMode
  uid: string
  faceId: string
  patchId: string | null
  edge: EdgeKey | 'circle' | null
  plane: THREE.Plane
  pointerId: number
  /** Patch drags: where the pointer went down, in face coordinates. */
  downFace: FacePoint
  /** Patch drags: the patch's geometry when the drag began. */
  patchStart: actions.PatchGeometryValues
  /** Corner drags: which corner, and the offsets when the drag began. */
  cornerIndex?: number
  cornerStart?: actions.CornerOffset[]
  /** Polygon drags: which vertex / edge, and the outline when the drag began. */
  polyIndex?: number
  polyStart?: number[][]
  /** The handle lit while dragging (a polygon's `pv:i` / `pe:i`). */
  handleKey?: HandleKey | null
  /** Model resize: the drag frame and the box it started from. */
  downWorld: THREE.Vector3
  worldRight: Vec3
  worldUp: Vec3
  baseStart: Record<string, number>
  anchorStart: Vec3
  wType: string
  hType: string
  /**
   * Set when the patch belongs to a SERVER inside a rack rather than to the
   * model itself. Its layout replaces `layoutOf(model, faceId)` — the rack's
   * own face is not the face this patch is cut into.
   */
  server: { equipmentId: string; layout: ServerFaceLayout } | null
  initEdgeProj: number | null
  sticky: number | null
  threshold: number | null
  lastSnapAt: number
  changed: boolean
}

export function createFaceDragTools(host: FaceDragHost): FaceDragTools {
  const handles = createResizeHandles(host.scene, host.camera)
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let drag: DragState | null = null
  let hovered: HandleKey | null = null

  const registries = () => host.registries()
  const modelOf = (uid: string | null | undefined) =>
    uid ? (registries().modelMap.get(uid) ?? null) : null
  const rootOf = (uid: string | null | undefined) =>
    uid ? (registries().renderRootMap.get(uid) ?? null) : null
  const snapScene = {
    renderRoots: () => registries().renderRoots,
    modelOf,
    rootOf,
    tools: host.tools,
  }

  function setRay(event: { clientX: number; clientY: number }) {
    const rect = host.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, host.camera)
  }

  /** The quad the handles sit on, in WORLD space, or null if there is none. */
  function cornersFor(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    patch: PatchRecord | null,
    server?: ServerFaceLayout | null,
  ): THREE.Vector3[] | null {
    root.updateMatrixWorld(true)
    let local: readonly Vec3[] | null = null
    if (patch) {
      if (patch.type !== 'rect') return null
      local = server
        ? // A server patch is fractions of its face; the corners are metres.
          rectCornersLocal(basisOf(server), serverRectMetres(rectOf(patch), server))
        : model.kind === 'partitionWall'
          ? partitionWallCornersLocal(rectOf(patch), sizeOf(model).z, faceId)
          : (() => {
              const layout = layoutOf(model, faceId)
              return layout ? rectCornersLocal(basisOf(layout), rectOf(patch)) : null
            })()
    } else {
      local = modelFaceCornersLocal(sizeOf(model), faceId, faceAxes(model, faceId))
    }
    if (!local) return null
    return local.map((p) => root.localToWorld(new THREE.Vector3(p.x, p.y, p.z)))
  }

  // -------------------------------------------------------------------------
  // The wall-mounted unit's front is a curve (flat underside, quarter
  // cylinder, vertical face). Its patches are stored in the unrolled (u, v)
  // layout, so the outline, the handles and the drag all go through the
  // surface rather than the flat layout plane the other kinds use.
  // -------------------------------------------------------------------------
  type CurvedApi = {
    isWallMountedAcu(model: unknown): boolean
    wallAcuRectOutlineLocal(
      model: unknown,
      rect: { x: number; y: number; w: number; h: number },
      segs?: number,
    ): {
      corners: THREE.Vector3[]
      edges: Record<EdgeKey, THREE.Vector3>
      polyline: THREE.Vector3[]
    }
    wallAcuRayFacePoint(
      model: unknown,
      origin: THREE.Vector3,
      dir: THREE.Vector3,
    ): { u: number; v: number; point: THREE.Vector3 } | null
    wallAcuLocalToUV(model: unknown, local: THREE.Vector3): { u: number; v: number }
    wallAcuPointLocal(profile: unknown, u: number, v: number): THREE.Vector3
    wallAcuNormalLocal(profile: unknown, v: number): THREE.Vector3
    wallAcuProfile(model: unknown): unknown
  }
  const curved = () => geometryPure() as unknown as CurvedApi
  function isCurvedFace(model: Construction, faceId: string, server?: unknown): boolean {
    return !server && faceId === 'front' && curved().isWallMountedAcu(model)
  }
  /** Outline + handle points of a rect patch on the curved front, world space. */
  function curvedRectFor(
    model: Construction,
    root: THREE.Object3D,
    patch: PatchRecord,
  ): { corners: THREE.Vector3[]; outline: THREE.Vector3[]; edgeCentres: Record<EdgeKey, THREE.Vector3> } {
    root.updateMatrixWorld(true)
    const o = curved().wallAcuRectOutlineLocal(model, rectOf(patch), 12)
    const w = (p: THREE.Vector3) => root.localToWorld(p.clone())
    return {
      corners: o.corners.map(w),
      outline: o.polyline.map(w),
      edgeCentres: {
        bottom: w(o.edges.bottom),
        right: w(o.edges.right),
        top: w(o.edges.top),
        left: w(o.edges.left),
      },
    }
  }
  /** Where the current pointer ray meets the curved front, or null. */
  function curvedHit(
    model: Construction,
    root: THREE.Object3D,
  ): { world: THREE.Vector3; face: FacePoint } | null {
    root.updateMatrixWorld(true)
    const inv = root.matrixWorld.clone().invert()
    const origin = raycaster.ray.origin.clone().applyMatrix4(inv)
    const dir = raycaster.ray.direction.clone().transformDirection(inv).normalize()
    const hit = curved().wallAcuRayFacePoint(model, origin, dir)
    if (!hit) return null
    return { world: root.localToWorld(hit.point.clone()), face: { x: hit.u, y: hit.v } }
  }

  // -------------------------------------------------------------------------
  // Corner handles: a sheet (wall / baffle / floor) or a rect patch on a flat
  // face can have each corner dragged in its plane. The corners live in the
  // face LAYOUT's (u, v), which is also where the renderer builds the quad.
  // -------------------------------------------------------------------------
  const CORNER_KINDS = new Set(['wall', 'baffle', 'floor'])
  function cornerHolder(model: Construction, patch: PatchRecord | null): Record<string, unknown> | null {
    if (patch) return patch as unknown as Record<string, unknown>
    return (model.geometry ?? null) as Record<string, unknown> | null
  }
  function cornerEligible(
    model: Construction,
    faceId: string,
    patch: PatchRecord | null,
    server?: unknown,
  ): boolean {
    if (server) return false
    const kind = String(model.kind ?? '')
    if (patch) {
      if (patch.type !== 'rect' || kind === 'partitionWall') return false
      if (isCurvedFace(model, faceId)) return false
      return true
    }
    return CORNER_KINDS.has(kind)
  }
  /** World positions of the four corners (with offsets), in `corners` order. */
  function cornerHandlesFor(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    patch: PatchRecord | null,
  ): THREE.Vector3[] | null {
    const layout = layoutOf(model, faceId)
    if (!layout) return null
    root.updateMatrixWorld(true)
    const api = geometryPure() as unknown as {
      quadCornersUV(x: number, y: number, w: number, h: number, offsets: unknown): number[][]
    }
    const holder = cornerHolder(model, patch)
    const offsets = actions.readCornerOffsets(holder ?? undefined)
    const lw = layout as unknown as { w?: number; h?: number }
    const rect = patch ? rectOf(patch) : { x: 0, y: 0, w: Number(lw.w) || 0, h: Number(lw.h) || 0 }
    const uv = api.quadCornersUV(rect.x, rect.y, rect.w, rect.h, offsets)
    return uv.map(([u, v]) =>
      root.localToWorld(
        layout.origin.clone().addScaledVector(layout.right, u!).addScaledVector(layout.up, v!),
      ),
    )
  }

  /** The ring and its four grab dots, for a circular patch. */
  function circleFor(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    patch: PatchRecord,
  ): { centre: THREE.Vector3; ring: THREE.Vector3[]; dots: THREE.Vector3[] } | null {
    const layout = layoutOf(model, faceId)
    if (!layout) return null
    root.updateMatrixWorld(true)
    const cx = Number(patch.cx) || 0
    const cy = Number(patch.cy) || 0
    const r = Number(patch.r) || 0
    if (r < 1e-6) return null
    const at = (u: number, v: number) =>
      root.localToWorld(
        layout.origin.clone().addScaledVector(layout.right, u).addScaledVector(layout.up, v),
      )
    const ring: THREE.Vector3[] = []
    for (let i = 0; i <= CIRCLE_SEGMENTS; i++) {
      const t = (i / CIRCLE_SEGMENTS) * Math.PI * 2
      ring.push(at(cx + r * Math.cos(t), cy + r * Math.sin(t)))
    }
    const dots = [0, 0.25, 0.5, 0.75].map((f) => {
      const t = f * Math.PI * 2
      return at(cx + r * Math.cos(t), cy + r * Math.sin(t))
    })
    return { centre: at(cx, cy), ring, dots }
  }

  /**
   * A circular patch's centre in WORLD space — the legacy's
   * `_computeCircleWorld(...).center` (STL_generate.html:2384). A server's
   * circle is stored in fractions of the server's face, and a wall-mounted
   * unit's front bends, so the centre is found on that surface rather than on
   * the model's flat face layout.
   */
  function circleCentreFor(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    patch: PatchRecord,
    server?: ServerFaceLayout | null,
  ): THREE.Vector3 | null {
    root.updateMatrixWorld(true)
    const cx = Number(patch.cx) || 0
    const cy = Number(patch.cy) || 0
    if (server) {
      return root.localToWorld(
        server.origin
          .clone()
          .addScaledVector(server.right, cx * server.w)
          .addScaledVector(server.up, cy * server.h),
      )
    }
    if (isCurvedFace(model, faceId)) {
      const api = curved()
      return root.localToWorld(api.wallAcuPointLocal(api.wallAcuProfile(model), cx, cy).clone())
    }
    const layout = layoutOf(model, faceId)
    if (!layout) return null
    return root.localToWorld(
      layout.origin.clone().addScaledVector(layout.right, cx).addScaledVector(layout.up, cy),
    )
  }

  // -------------------------------------------------------------------------
  // A floor or ceiling drawn in the 2D room editor is an arbitrary polygon
  // (`geometry.polygon`, in the top face layout's (u, v)). Its handles are one
  // per vertex and one per edge, on the outline itself — the bounding
  // rectangle's four edges and corners describe nothing on a triangle.
  // -------------------------------------------------------------------------
  function polygonSheetOf(model: Construction, faceId: string, patch: PatchRecord | null): number[][] | null {
    if (patch || faceId !== 'top') return null
    const api = geometryPure()
    return api.isPolygonSheet(model) ? api.sheetPolygon(model) : null
  }
  function polygonHandlesFor(
    model: Construction,
    root: THREE.Object3D,
    poly: number[][],
  ): { vertices: THREE.Vector3[]; edgeMids: THREE.Vector3[] } | null {
    const layout = layoutOf(model, 'top')
    if (!layout) return null
    root.updateMatrixWorld(true)
    const at = (u: number, v: number) =>
      root.localToWorld(
        layout.origin.clone().addScaledVector(layout.right, u).addScaledVector(layout.up, v),
      )
    const points = geometryPure().polygonHandlePointsUV(poly)
    return {
      vertices: points.vertices.map(([u, v]) => at(u!, v!)),
      edgeMids: points.edges.map((e) => at(e.mid[0]!, e.mid[1]!)),
    }
  }

  /** What the current selection wants handles on, if anything. */
  function handleTarget(): {
    model: Construction
    faceId: string
    patch: PatchRecord | null
    server?: { equipmentId: string; layout: ServerFaceLayout } | null
  } | null {
    const { uid, node } = host.selection()
    const model = modelOf(node?.uid ?? uid)
    if (!model) return null
    const kind = String(model.kind ?? '')

    // A server's patch: handles on the SERVER's face rectangle, which is the
    // legacy's `_computeQuadCornersWorld` server branch (:6420). The rack's
    // own faces do not contain the patch, so the branch below would find
    // nothing and the vent could be moved but never resized.
    if (node?.type === 'serverPatch' && node.patchId && node.equipmentId) {
      const found = findServerPatch(model, node.patchId)
      const root = rootOf(model.uid)
      if (!found || !root) return null
      const layout = serverFaceLayout(root, node.equipmentId, found.faceId)
      if (!layout) return null
      return {
        model,
        faceId: found.faceId,
        patch: found.patch,
        server: { equipmentId: node.equipmentId, layout },
      }
    }

    if (node?.type === 'patch' && node.patchId) {
      if (!PATCH_DRAG_KINDS.has(kind)) return null
      const site = actions.findPatchOnModel(model, node.patchId)
      return site ? { model, faceId: site.faceId, patch: site.patch } : null
    }
    if (node?.type === 'face' && node.faceId && FACE_RESIZE_KINDS.has(kind)) {
      return { model, faceId: node.faceId, patch: null }
    }
    if ((!node || node.type === 'model') && PLANAR_KINDS.has(kind)) {
      // A floor is a horizontal sheet, so its own plane is `top`; everything
      // else stands up and shows `front` (STL_generate.html:6717).
      return { model, faceId: kind === 'floor' ? 'top' : 'front', patch: null }
    }
    return null
  }

  function refresh() {
    drawHandles()
    host.handlesRefreshed?.()
  }

  function drawHandles() {
    if (drag) return
    if (!host.enabled()) {
      handles.hide()
      return
    }
    const target = handleTarget()
    const root = rootOf(target?.model.uid)
    if (!target || !root) {
      handles.hide()
      return
    }
    const meta = {
      uid: target.model.uid,
      faceId: target.faceId,
      patchId: target.patch?.id ?? null,
      equipmentId: target.server?.equipmentId ?? null,
    }
    if (target.patch?.type === 'circle') {
      // A partition wall's holes are rectangular cutters; a circle there has no
      // ring to grab (STL_generate.html:6704).
      if (target.model.kind === 'partitionWall') {
        handles.hide()
        return
      }
      const circle = circleFor(target.model, root, target.faceId, target.patch)
      if (!circle) {
        handles.hide()
        return
      }
      handles.showCircle({ ...meta, ...circle })
      return
    }
    if (target.patch && target.patch.type === 'rect' && isCurvedFace(target.model, target.faceId, target.server)) {
      handles.showRect({ ...meta, ...curvedRectFor(target.model, root, target.patch) })
      return
    }
    const poly = polygonSheetOf(target.model, target.faceId, target.patch)
    if (poly) {
      const points = polygonHandlesFor(target.model, root, poly)
      if (points) handles.showPolygon({ ...meta, ...points })
      else handles.hide()
      return
    }
    const corners = cornersFor(
      target.model,
      root,
      target.faceId,
      target.patch,
      target.server?.layout,
    )
    if (!corners) {
      handles.hide()
      return
    }
    const cornerHandles = cornerEligible(target.model, target.faceId, target.patch, target.server)
      ? cornerHandlesFor(target.model, root, target.faceId, target.patch)
      : null
    // the outline is the (possibly trapezoid) quad the corners make; the
    // green edge cubes keep the rectangle's edge frame for resizing
    handles.showRect({
      ...meta,
      corners,
      outline: cornerHandles ?? undefined,
      cornerHandles: cornerHandles ?? undefined,
    })
  }

  function anchorPoint(): THREE.Vector3 | null {
    const target = handleTarget()
    if (!target?.patch) return null
    const root = rootOf(target.model.uid)
    if (!root) return null
    const { model, faceId, patch, server } = target
    if (patch.type === 'circle') {
      // A partition wall's holes are rectangular cutters (refresh, above).
      if (model.kind === 'partitionWall') return null
      return circleCentreFor(model, root, faceId, patch, server?.layout)
    }
    if (patch.type !== 'rect') return null
    if (isCurvedFace(model, faceId, server)) {
      return curvedRectFor(model, root, patch).corners[0] ?? null
    }
    // Corner 0 of the UN-offset rectangle: a trapezoid's blue corner cube moves
    // with its offset, while the (x, y) the inspector shows stays put.
    return cornersFor(model, root, faceId, patch, server?.layout)?.[0] ?? null
  }

  /** Redraw the handles in place, without rebuilding them, during a drag. */
  function trackHandles() {
    if (!drag) return
    const model = modelOf(drag.uid)
    const root = rootOf(drag.uid)
    if (!model || !root) return
    const patch = drag.patchId
      ? drag.server
        ? (findServerPatch(model, drag.patchId)?.patch ?? null)
        : (actions.findPatchOnModel(model, drag.patchId)?.patch ?? null)
      : null
    if (patch?.type === 'circle') {
      const circle = circleFor(model, root, drag.faceId, patch)
      if (circle) handles.moveCircle(circle.ring, circle.dots)
      return
    }
    if (patch && patch.type === 'rect' && isCurvedFace(model, drag.faceId, drag.server)) {
      const c = curvedRectFor(model, root, patch)
      handles.moveRect(c.corners, c.outline, c.edgeCentres)
      return
    }
    if (drag.mode === 'polygonVertex' || drag.mode === 'polygonEdge') {
      const poly = polygonSheetOf(model, drag.faceId, null)
      const points = poly ? polygonHandlesFor(model, root, poly) : null
      if (points) handles.movePolygon(points.vertices, points.edgeMids)
      return
    }
    const corners = cornersFor(model, root, drag.faceId, patch, drag.server?.layout)
    if (!corners) return
    const cornerHandles = cornerEligible(model, drag.faceId, patch, drag.server)
      ? cornerHandlesFor(model, root, drag.faceId, patch)
      : null
    handles.moveRect(corners, cornerHandles ?? undefined, undefined, cornerHandles ?? undefined)
  }

  /**
   * The plane a drag is projected onto: the face's own plane, in world space.
   *
   * Not a billboard plane facing the camera — a patch has to stay ON the
   * surface it cuts, so the pointer's depth is resolved against the face and
   * only its two in-face components matter.
   */
  function facePlane(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    override?: ServerFaceLayout | null,
  ): THREE.Plane | null {
    // A server's patch is cut into the SERVER's face, which is a rectangle
    // inside the rack — the rack's own face layout would put the plane on the
    // outside of the cabinet.
    const layout = override ?? layoutOf(model, faceId)
    if (!layout) return null
    root.updateMatrixWorld(true)
    if (!override && isCurvedFace(model, faceId)) {
      // the tangent plane where the pointer meets the curve (or the middle
      // of the profile when it does not) — only a fallback for the drag
      const hit = curvedHit(model, root)
      const api = curved()
      const pr = api.wallAcuProfile(model)
      const lw = (layout as unknown as { w?: number; h?: number })
      const v = hit ? hit.face.y : (Number(lw.h) || 0) / 2
      const u = hit ? hit.face.x : (Number(lw.w) || 0) / 2
      const n = api.wallAcuNormalLocal(pr, v).transformDirection(root.matrixWorld)
      return new THREE.Plane().setFromNormalAndCoplanarPoint(
        n,
        root.localToWorld(api.wallAcuPointLocal(pr, u, v)),
      )
    }
    const normal = new THREE.Vector3()
      .crossVectors(layout.right, layout.up)
      .normalize()
      .transformDirection(root.matrixWorld)
    return new THREE.Plane().setFromNormalAndCoplanarPoint(
      normal,
      root.localToWorld(layout.origin.clone()),
    )
  }

  function facePointAt(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    world: THREE.Vector3,
    override?: ServerFaceLayout | null,
  ): FacePoint | null {
    const local = root.worldToLocal(world.clone())
    // A server patch is stored as FRACTIONS of its face (legacy
    // `getPointOnServerFace`, :6992), so the metric face point is scaled.
    if (override) return serverFacePoint(localToFace(v3(local), basisOf(override)), override)
    // A partition wall's patches are stored in model-local CORNER space, not in
    // the mirrored front-face layout (STL_generate.html:6154). Mapping them
    // through the layout puts the hole on the wrong side of the wall.
    if (model.kind === 'partitionWall') return { x: local.x, y: local.y }
    if (isCurvedFace(model, faceId)) {
      const uv = curved().wallAcuLocalToUV(model, local)
      return { x: uv.u, y: uv.v }
    }
    const layout = layoutOf(model, faceId)
    if (!layout) return null
    return localToFace(v3(local), basisOf(layout))
  }
  /**
   * Pointer → a point ON the face. Flat faces intersect the drag plane; the
   * curved front intersects the surface itself, falling back to the tangent
   * plane (`drag.plane`) when the ray misses it.
   */
  function pointerOnFace(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    plane: THREE.Plane,
    server?: unknown,
  ): THREE.Vector3 | null {
    if (isCurvedFace(model, faceId, server)) {
      const hit = curvedHit(model, root)
      if (hit) return hit.world
    }
    const world = new THREE.Vector3()
    return raycaster.ray.intersectPlane(plane, world) ? world : null
  }

  // -------------------------------------------------------------------------
  // Drag lifecycle
  // -------------------------------------------------------------------------

  function beginHandleDrag(hit: THREE.Intersection): DragState | null {
    const data = hit.object.userData as {
      edgeKey?: EdgeKey | 'circle'
      cornerIndex?: number
      polygonVertex?: number
      polygonEdge?: number
      modelId?: string
      faceId?: string
      patchId?: string | null
      equipmentId?: string | null
    }
    const model = modelOf(data.modelId)
    const root = rootOf(data.modelId)
    if (!model || !root || !data.faceId) return null
    if (typeof data.cornerIndex === 'number') return beginCornerDrag(model, root, data.faceId, data.patchId ?? null, data.cornerIndex, hit)
    if (typeof data.polygonVertex === 'number') return beginPolygonDrag(model, root, data.faceId, 'polygonVertex', data.polygonVertex)
    if (typeof data.polygonEdge === 'number') return beginPolygonDrag(model, root, data.faceId, 'polygonEdge', data.polygonEdge)
    if (!data.edgeKey) return null
    // A handle on a SERVER's patch: the plane and the face frame are the
    // server's, and the drag writes fractions (the legacy's handle branch for
    // `isServerPatch`, STL_generate.html:16293-16330).
    const server = (() => {
      if (!data.equipmentId || !data.patchId) return null
      const found = findServerPatch(model, data.patchId)
      if (!found) return null
      const layout = serverFaceLayout(root, data.equipmentId, found.faceId)
      return layout ? { equipmentId: data.equipmentId, layout, found } : null
    })()
    if (data.equipmentId && !server) return null
    const plane = facePlane(model, root, data.faceId, server?.layout)
    if (!plane) return null
    const world = pointerOnFace(model, root, data.faceId, plane, server)
    if (!world) return null

    if (data.patchId) {
      const site = server
        ? { model, faceId: server.found.faceId, face: server.found.face, patch: server.found.patch }
        : actions.findPatchOnModel(model, data.patchId)
      if (!site) return null
      const face = facePointAt(model, root, data.faceId, world, server?.layout)
      if (!face) return null
      return {
        mode: data.edgeKey === 'circle' ? 'patchCircleResize' : 'patchRectResize',
        uid: model.uid,
        faceId: site.faceId,
        patchId: site.patch.id,
        edge: data.edgeKey,
        plane,
        pointerId: 0,
        downFace: face,
        patchStart: actions.readPatchGeometry(site.patch),
        downWorld: world.clone(),
        worldRight: { x: 1, y: 0, z: 0 },
        worldUp: { x: 0, y: 1, z: 0 },
        baseStart: {},
        anchorStart: locationOf(model),
        wType: 'W',
        hType: 'H',
        initEdgeProj: null,
        server: server ? { equipmentId: server.equipmentId, layout: server.layout } : null,
        sticky: null,
        threshold: null,
        lastSnapAt: 0,
        changed: false,
      }
    }

    // A model face: the drag directions are the DIMENSION axes, so the handle
    // labelled `right` grows W whichever way the face's own layout runs.
    const axes = faceAxes(model, data.faceId)
    const dimVector = (key: string) => {
      const d = DIM_DIR[key] ?? DIM_DIR.W!
      return new THREE.Vector3(d.x, d.y, d.z).transformDirection(root.matrixWorld)
    }
    const worldRight = dimVector(axes.wType)
    const worldUp = dimVector(axes.hType)
    const size = sizeOf(model)
    const baseStart: Record<string, number> = { W: size.x, H: size.y, D: size.z }
    const anchorStart = locationOf(model)
    const edge = data.edgeKey as EdgeKey
    const dragDir = edge === 'right' || edge === 'left' ? v3(worldRight) : v3(worldUp)
    const state: DragState = {
      mode: 'modelResize',
      uid: model.uid,
      faceId: data.faceId,
      patchId: null,
      edge,
      plane,
      pointerId: 0,
      downFace: { x: 0, y: 0 },
      patchStart: {},
      downWorld: world.clone(),
      worldRight: v3(worldRight),
      worldUp: v3(worldUp),
      baseStart,
      anchorStart,
      wType: axes.wType,
      hType: axes.hType,
      // Where the moving edge started, so the snap does not lock the drag to
      // the position it began at.
      initEdgeProj: edgeRangeProjection(
        ownEdgeRange(
          orientedBoxOf(model, anchorStart, {
            x: baseStart.W ?? 0,
            y: baseStart.H ?? 0,
            z: baseStart.D ?? 0,
          }),
          dragDir,
        ),
        edge,
      ),
      server: null,
      sticky: null,
      threshold: null,
      lastSnapAt: 0,
      changed: false,
    }
    host.tools()?.initSnapCache()
    return state
  }

  function beginPatchBodyDrag(): DragState | null {
    const { uid, node } = host.selection()
    const isServer = node?.type === 'serverPatch' && !!node.equipmentId
    if ((node?.type !== 'patch' && !isServer) || !node?.patchId) return null
    const model = modelOf(node.uid ?? uid)
    const root = rootOf(node.uid ?? uid)
    if (!model || !root) return null
    // A server's patch belongs to the server, so the rack's own kind is not the
    // gate — `PATCH_DRAG_KINDS` leaves `rack` out for exactly that reason.
    if (!isServer && !PATCH_DRAG_KINDS.has(String(model.kind ?? ''))) return null
    const server = isServer
      ? (() => {
          const found = findServerPatch(model, node.patchId as string)
          if (!found) return null
          const layout = serverFaceLayout(root, node.equipmentId as string, found.faceId)
          return layout ? { equipmentId: node.equipmentId as string, layout, found } : null
        })()
      : null
    if (isServer && !server) return null
    const site = server
      ? { model, faceId: server.found.faceId, face: server.found.face, patch: server.found.patch }
      : actions.findPatchOnModel(model, node.patchId)
    if (!site) return null

    const hits = raycaster
      .intersectObjects(registries().renderMeshes, false)
      .filter((h) => h.object.visible)
    const pick = pickNearestHit(hits)
    const data = (pick?.object.userData ?? {}) as { modelId?: string; refIds?: unknown }
    const refIds = Array.isArray(data.refIds) ? (data.refIds as string[]) : []
    // The body under the pointer must be THIS patch's: dragging from anywhere
    // on the model would move a patch the pointer is nowhere near.
    if (data.modelId !== model.uid || !refIds.includes(site.patch.id)) return null

    const plane = facePlane(model, root, site.faceId, server?.layout)
    if (!plane) return null
    const world = pointerOnFace(model, root, site.faceId, plane, server)
    if (!world) return null
    const face = facePointAt(model, root, site.faceId, world, server?.layout)
    if (!face) return null

    // A circle has no edge handles, so its RING is the resize affordance: a
    // press near the perimeter resizes, one inside it moves. Without this the
    // only way to change a circular opening is to type its radius.
    let mode: DragMode = 'patchMove'
    let edge: EdgeKey | 'circle' | null = null
    if (site.patch.type === 'circle' && model.kind !== 'partitionWall') {
      const extent = faceExtent(model, site.faceId, facesOf(model)?.[site.faceId] as FaceRecord)
      const circle = {
        cx: Number(site.patch.cx) || 0,
        cy: Number(site.patch.cy) || 0,
        r: Number(site.patch.r) || 0,
      }
      if (isCircleRingGrab(face, circle, extent.w, extent.h)) {
        mode = 'patchCircleResize'
        edge = 'circle'
      }
    }

    return {
      mode,
      uid: model.uid,
      faceId: site.faceId,
      patchId: site.patch.id,
      edge,
      plane,
      pointerId: 0,
      downFace: face,
      patchStart: actions.readPatchGeometry(site.patch),
      downWorld: world.clone(),
      worldRight: { x: 1, y: 0, z: 0 },
      worldUp: { x: 0, y: 1, z: 0 },
      baseStart: {},
      anchorStart: locationOf(model),
      wType: 'W',
      hType: 'H',
      initEdgeProj: null,
      server: server ? { equipmentId: server.equipmentId, layout: server.layout } : null,
      sticky: null,
      threshold: null,
      lastSnapAt: 0,
      changed: false,
    }
  }

  /** A blue corner cube went down: move that one corner in the face plane. */
  function beginCornerDrag(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    patchId: string | null,
    cornerIndex: number,
    hit: THREE.Intersection,
  ): DragState | null {
    const patch = patchId ? (actions.findPatchOnModel(model, patchId)?.patch ?? null) : null
    if (patchId && !patch) return null
    if (!cornerEligible(model, faceId, patch)) return null
    const plane = facePlane(model, root, faceId)
    if (!plane) return null
    const world = pointerOnFace(model, root, faceId, plane)
    if (!world) return null
    const face = facePointAt(model, root, faceId, world)
    if (!face) return null
    const holder = cornerHolder(model, patch)
    if (!holder) return null
    return {
      mode: 'cornerMove',
      uid: model.uid,
      faceId,
      patchId,
      edge: null,
      plane,
      pointerId: 0,
      downFace: face,
      patchStart: patch ? actions.readPatchGeometry(patch) : {},
      cornerIndex,
      cornerStart: actions.readCornerOffsets(holder),
      downWorld: hit.point.clone(),
      worldRight: { x: 0, y: 0, z: 0 },
      worldUp: { x: 0, y: 0, z: 0 },
      baseStart: {},
      anchorStart: { x: 0, y: 0, z: 0 },
      wType: '',
      hType: '',
      server: null,
      changed: false,
    } as DragState
  }

  /** A polygon vertex or edge cube went down: move it in the sheet's plane. */
  function beginPolygonDrag(
    model: Construction,
    root: THREE.Object3D,
    faceId: string,
    mode: 'polygonVertex' | 'polygonEdge',
    index: number,
  ): DragState | null {
    const poly = polygonSheetOf(model, faceId, null)
    if (!poly || index < 0 || index >= poly.length) return null
    const plane = facePlane(model, root, faceId)
    if (!plane) return null
    const world = pointerOnFace(model, root, faceId, plane)
    if (!world) return null
    const face = facePointAt(model, root, faceId, world)
    if (!face) return null
    return {
      mode,
      uid: model.uid,
      faceId,
      patchId: null,
      edge: null,
      handleKey: mode === 'polygonVertex' ? `pv:${index}` : `pe:${index}`,
      plane,
      pointerId: 0,
      downFace: face,
      patchStart: {},
      polyIndex: index,
      polyStart: poly,
      downWorld: world.clone(),
      worldRight: { x: 0, y: 0, z: 0 },
      worldUp: { x: 0, y: 0, z: 0 },
      baseStart: {},
      anchorStart: { x: 0, y: 0, z: 0 },
      wType: '',
      hType: '',
      server: null,
      initEdgeProj: null,
      sticky: null,
      threshold: null,
      lastSnapAt: 0,
      changed: false,
    }
  }

  /**
   * The outline follows the pointer in the sheet's CURRENT frame; the re-base
   * to the new bounding box happens once, on release, so the frame the
   * pointer is measured in does not shift under it mid-drag.
   */
  function updatePolygonDrag(world: THREE.Vector3) {
    if (!drag || (drag.mode !== 'polygonVertex' && drag.mode !== 'polygonEdge')) return
    const model = modelOf(drag.uid)
    const root = rootOf(drag.uid)
    const start = drag.polyStart
    const index = drag.polyIndex ?? -1
    if (!model || !root || !start) return
    const face = facePointAt(model, root, drag.faceId, world)
    if (!face) return
    const du = face.x - drag.downFace.x
    const dv = face.y - drag.downFace.y
    const api = geometryPure()
    const next =
      drag.mode === 'polygonVertex'
        ? api.movePolygonVertex(start, index, start[index]![0]! + du, start[index]![1]! + dv)
        : api.movePolygonEdge(start, index, du, dv)
    store.mutateLive(() => {
      ;(model.geometry as Record<string, unknown>).polygon = next
    })
    drag.changed = true
  }

  function updateCornerDrag(world: THREE.Vector3) {
    if (!drag || drag.mode !== 'cornerMove') return
    const model = modelOf(drag.uid)
    const root = rootOf(drag.uid)
    if (!model || !root) return
    const patch = drag.patchId ? (actions.findPatchOnModel(model, drag.patchId)?.patch ?? null) : null
    const holder = cornerHolder(model, patch)
    if (!holder) return
    const face = facePointAt(model, root, drag.faceId, world)
    if (!face) return
    const dx = face.x - drag.downFace.x
    const dy = face.y - drag.downFace.y
    const start = drag.cornerStart ?? actions.readCornerOffsets(holder)
    const idx = drag.cornerIndex ?? -1
    const next = start.map((o, i) => (i === idx ? { x: o.x + dx, y: o.y + dy } : { x: o.x, y: o.y }))
    store.mutateLive(() => actions.writeCornerOffsets(holder, next))
    drag.changed = true
  }

  function updatePatchDrag(world: THREE.Vector3) {
    if (!drag || !drag.patchId) return
    const model = modelOf(drag.uid)
    const root = rootOf(drag.uid)
    if (!model || !root) return
    const found = drag.server
      ? findServerPatch(model, drag.patchId)
      : actions.findPatchOnModel(model, drag.patchId)
    if (!found) return
    const site = drag.server
      ? { model, faceId: found.faceId, face: found.face, patch: found.patch }
      : (found as actions.PatchSite)
    const face = facePointAt(model, root, drag.faceId, world, drag.server?.layout)
    if (!face) return

    const isCircle = site.patch.type === 'circle'
    let next: actions.PatchGeometryValues
    if (drag.mode === 'patchMove') {
      next = isCircle
        ? movedCircle(
            {
              cx: Number(drag.patchStart.cx) || 0,
              cy: Number(drag.patchStart.cy) || 0,
              r: Number(drag.patchStart.r) || 0,
            },
            drag.downFace,
            face,
          )
        : movedRect(
            {
              x: Number(drag.patchStart.x) || 0,
              y: Number(drag.patchStart.y) || 0,
              w: Number(drag.patchStart.w) || 0,
              h: Number(drag.patchStart.h) || 0,
            },
            drag.downFace,
            face,
          )
    } else if (drag.mode === 'patchCircleResize') {
      next = {
        r: resizedCircle({ x: Number(site.patch.cx) || 0, y: Number(site.patch.cy) || 0 }, face),
      }
    } else {
      next = resizedRect(
        {
          x: Number(drag.patchStart.x) || 0,
          y: Number(drag.patchStart.y) || 0,
          w: Number(drag.patchStart.w) || 0,
          h: Number(drag.patchStart.h) || 0,
        },
        drag.edge as EdgeKey,
        face,
      )
    }

    store.mutateLive(() => {
      actions.writePatchGeometry(site.patch, next)

      const snapOn = host.snap()
      const wantsSnap =
        (snapOn.face || snapOn.edge) &&
        // NOT for a server patch. The legacy has its own inter-server snapping
        // (STL_generate.html:17029) that aligns one server's vent edges with
        // another's; this snapper works in the MODEL's face frame and would
        // pull a server vent onto the rack's own edges instead. Off until that
        // is ported, rather than approximately right.
        !drag!.server &&
        !isCircle &&
        (drag!.mode === 'patchMove' || drag!.mode === 'patchRectResize') &&
        performance.now() - drag!.lastSnapAt >= PATCH_SNAP_THROTTLE_MS
      if (wantsSnap) {
        drag!.lastSnapAt = performance.now()
        const layout = layoutOf(model, drag!.faceId)
        if (layout) {
          root.updateMatrixWorld(true)
          const worldRight = layout.right.clone().transformDirection(root.matrixWorld)
          const worldUp = layout.up.clone().transformDirection(root.matrixWorld)
          const originWorld = root.localToWorld(layout.origin.clone())
          const originR = originWorld.dot(worldRight)
          const originU = originWorld.dot(worldUp)
          const rect = rectOf(site.patch)
          if (drag!.threshold == null) drag!.threshold = averageModelSize(snapScene) * 0.06
          const targets = patchSnapTargets(
            snapScene,
            model,
            root,
            drag!.faceId,
            site.patch.id,
            worldRight,
            worldUp,
          )
          const { dr, du } = bestPatchSnap({
            isResize: drag!.mode === 'patchRectResize',
            edge: drag!.edge as EdgeKey | null,
            minR: originR + rect.x,
            maxR: originR + rect.x + rect.w,
            minU: originU + rect.y,
            maxU: originU + rect.y + rect.h,
            targetsR: targets.r,
            targetsU: targets.u,
            threshold: drag!.threshold,
          })
          if (Math.abs(dr) > 1e-8 || Math.abs(du) > 1e-8) {
            const snapped = applyPatchSnapRect(
              rect,
              drag!.mode === 'patchRectResize',
              drag!.edge as EdgeKey | null,
              dr,
              du,
            )
            actions.writePatchGeometry(site.patch, snapped)
          }
        }
      }

      // A server patch is clamped in its own fraction space (legacy :6786);
      // the model-level clamp measures the RACK's face in metres and would
      // let a vent run off the server.
      if (drag!.server) enforceServerPatchSafety(site.patch as unknown as Record<string, unknown>)
      else actions.enforceSiteSafety(site)
    })
    drag.changed = true
  }

  function updateModelResize(world: THREE.Vector3) {
    if (!drag) return
    const model = modelOf(drag.uid)
    const root = rootOf(drag.uid)
    if (!model || !root) return
    const previousSize = sizeOf(model)
    const previousLocation = locationOf(model)

    let step = wallResizeStep({
      baseStart: drag.baseStart,
      anchorStart: drag.anchorStart,
      worldRight: drag.worldRight,
      worldUp: drag.worldUp,
      worldDelta: v3(world.clone().sub(drag.downWorld)),
      edge: drag.edge as EdgeKey,
      wType: drag.wType,
      hType: drag.hType,
      kind: String(model.kind ?? ''),
    })

    const snapOn = host.snap()
    if (snapOn.face || snapOn.edge) {
      const edge = drag.edge as EdgeKey
      const horizontal = edge === 'right' || edge === 'left'
      const dir = horizontal ? drag.worldRight : drag.worldUp
      const dimKey = horizontal ? drag.wType : drag.hType
      if (drag.threshold == null) {
        const tools = host.tools()
        const configured = tools?.snapThreshold() ?? null
        drag.threshold = configured != null ? configured : averageModelSize(snapScene) * 0.12
      }
      const threshold = drag.threshold
      // The moving edge's TRUE position along the drag direction, and the
      // local axis the world snap delta has to be applied to. For a rotated
      // baffle (local W running vertically) the old `location·dir + size[H]`
      // put the edge a whole dimension off and resized the wrong axis, which
      // is how a containment baffle ended 0.42 m through the ceiling.
      const la = localAxisAlongDir(quatOf(model), dir)
      const raw = edgeRangeProjection(
        ownEdgeRange(orientedBoxOf(model, step.location, step.size), dir),
        edge,
      )
      // Engage only inside half the release window (Schmitt trigger), so a
      // pointer resting on the boundary cannot toggle the lock frame by frame.
      // See STICKY_ENGAGE.
      const foundRaw = bestEdgeTarget(
        raw,
        modelEdgeTargets(snapScene, drag.uid, dir, threshold),
        threshold,
        drag.initEdgeProj,
      )
      const found =
        foundRaw !== null && Math.abs(raw - foundRaw) < threshold * STICKY_ENGAGE
          ? foundRaw
          : null
      drag.sticky = stickyTarget(drag.sticky, raw, found, threshold)
      // No local axis aligns with the drag direction (a 45-degree model):
      // there is no single dimension to resize, so the snap stays out of it.
      if (la && stickyHolds(drag.sticky, raw, threshold)) {
        step = applyStickyResize({
          size: step.size,
          location: step.location,
          edge,
          dimKey,
          worldRight: drag.worldRight,
          worldUp: drag.worldUp,
          snapDelta: drag.sticky - raw,
          kind: String(model.kind ?? ''),
          axis: la.axis,
          sign: la.sign,
        })
      }
    }

    const bounds = (host.project()?.geometry as { bounds?: ProjectBounds } | undefined)?.bounds
    // Out of bounds keeps the LAST valid box rather than clamping: clamping
    // would let the pointer keep pushing and the model would slide along the
    // boundary instead of stopping at it.
    if (!withinProjectBounds(step.location, step.size, bounds)) {
      step = { size: previousSize, location: previousLocation }
    }

    store.mutateLive(() => {
      const geometry = model.geometry as Record<string, unknown>
      geometry.size = { x: step.size.x, y: step.size.y, z: step.size.z }
      geometry.location = { x: step.location.x, y: step.location.y, z: step.location.z }
      if (model.kind === 'partitionWall') {
        try {
          syncPartitionWall(model)
        } catch {
          // A partition wall refuses a box its holes cannot fit in. Keep the
          // last accepted one rather than leaving the model in a state its own
          // validator rejects.
          geometry.size = { x: previousSize.x, y: previousSize.y, z: previousSize.z }
          geometry.location = {
            x: previousLocation.x,
            y: previousLocation.y,
            z: previousLocation.z,
          }
        }
      }
    })
    root.position.set(step.location.x, step.location.y, step.location.z)
    root.updateMatrixWorld(true)
    host.tools()?.invalidateBounds(drag.uid)
    drag.changed = true
  }

  function onMove(event: PointerEvent) {
    if (!drag) return
    setRay(event)
    const dragModel = modelOf(drag.uid)
    const dragRoot = rootOf(drag.uid)
    const world =
      dragModel && dragRoot
        ? pointerOnFace(dragModel, dragRoot, drag.faceId, drag.plane, drag.server)
        : null
    if (!world) return
    if (drag.mode === 'modelResize') updateModelResize(world)
    else if (drag.mode === 'cornerMove') updateCornerDrag(world)
    else if (drag.mode === 'polygonVertex' || drag.mode === 'polygonEdge') updatePolygonDrag(world)
    else updatePatchDrag(world)
    host.rebuildSoon([drag.uid])
    trackHandles()
  }

  function onUp() {
    const state = drag
    drag = null
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    if (host.orbit) host.orbit.enabled = true
    host.domElement.style.cursor = ''
    host.tools()?.clearSnapCache()
    handles.setStates(null, null)
    if (!state) return
    try {
      host.domElement.releasePointerCapture?.(state.pointerId)
    } catch {
      // The pointer may already have been released by a lost capture.
    }
    if (!state.changed) {
      refresh()
      return
    }

    const model = modelOf(state.uid)
    if (!model) {
      host.rebuildNow()
      refresh()
      return
    }

    if (state.mode === 'polygonVertex' || state.mode === 'polygonEdge') {
      const finalPolygon = geometryPure().sheetPolygon(model)
      // back to the start first, so the undo entry is the outline the drag
      // began from
      store.mutateLive(() => {
        ;(model.geometry as Record<string, unknown>).polygon = state.polyStart
      })
      if (finalPolygon) actions.commitSheetPolygon(state.uid, finalPolygon)
    } else if (state.mode === 'cornerMove') {
      const patch = state.patchId
        ? (actions.findPatchOnModel(model, state.patchId)?.patch ?? null)
        : null
      const holder = cornerHolder(model, patch)
      if (holder) {
        const finalOffsets = actions.readCornerOffsets(holder)
        // back to the start before committing, so the undo entry is the
        // drag's starting state (as the other modes do)
        store.mutateLive(() => actions.writeCornerOffsets(holder, state.cornerStart ?? null))
        actions.commitCornerOffsets(state.uid, state.patchId, finalOffsets)
      }
    } else if (state.mode === 'modelResize') {
      const finalSize = sizeOf(model)
      const finalLocation = locationOf(model)
      // Put the box back before committing, so the undo entry the store
      // snapshots is the one the drag STARTED from.
      store.mutateLive(() => {
        const geometry = model.geometry as Record<string, unknown>
        geometry.size = {
          x: state.baseStart.W ?? 0,
          y: state.baseStart.H ?? 0,
          z: state.baseStart.D ?? 0,
        }
        geometry.location = { ...state.anchorStart }
      })
      actions.commitModelBox(state.uid, finalSize, finalLocation)
    } else if (state.patchId && state.server) {
      // The server's patch, committed through its own writer: the rack's faces
      // do not contain it, so `commitPatchGeometry` would find nothing.
      const found = findServerPatch(model, state.patchId)
      if (found) {
        const finalValues = actions.readPatchGeometry(found.patch)
        store.mutateLive(() => actions.writePatchGeometry(found.patch, state.patchStart))
        commitServerPatchGeometry(
          state.uid,
          state.patchId,
          finalValues as Record<string, number>,
        )
      }
    } else if (state.patchId) {
      const site = actions.findPatchOnModel(model, state.patchId)
      if (site) {
        const finalValues = actions.readPatchGeometry(site.patch)
        store.mutateLive(() => actions.writePatchGeometry(site.patch, state.patchStart))
        actions.commitPatchGeometry(state.uid, state.patchId, finalValues)
      }
    }
    host.rebuildNow([state.uid])
    refresh()
  }

  function begin(event: PointerEvent): boolean {
    if (drag || event.button !== 0 || !host.enabled()) return false
    setRay(event)
    const handleHits = raycaster.intersectObjects(handles.pickables(), false)
    const state = handleHits.length ? beginHandleDrag(handleHits[0]!) : beginPatchBodyDrag()
    if (!state) return false
    drag = state
    drag.pointerId = event.pointerId
    if (host.orbit) host.orbit.enabled = false
    try {
      host.domElement.setPointerCapture?.(event.pointerId)
    } catch {
      // Pointer capture is best-effort; the window listeners below carry the
      // drag even without it.
    }
    host.domElement.style.cursor = 'grabbing'
    handles.setStates(null, drag.handleKey ?? drag.edge)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return true
  }

  function hover(event: PointerEvent) {
    if (drag || !host.enabled()) return
    const pickables = handles.pickables()
    if (!pickables.length) {
      if (hovered) {
        hovered = null
        handles.setStates(null, null)
      }
      return
    }
    setRay(event)
    const hit = raycaster.intersectObjects(pickables, false)[0]
    const data = hit?.object.userData as { edgeKey?: EdgeKey | 'circle'; handleKey?: HandleKey } | undefined
    const next = data?.edgeKey ?? data?.handleKey ?? null
    if (next === hovered) return
    hovered = next
    handles.setStates(next, null)
    host.domElement.style.cursor = next ? 'grab' : ''
  }

  return {
    refresh,
    syncScale: () => handles.updateScale(),
    begin,
    hover,
    anchorPoint,
    dragging: () => drag !== null,
    dispose() {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      handles.dispose()
      drag = null
    },
  }
}
