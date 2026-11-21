/**
 * The per-model anchor marker: a red ball where `geometry.location` actually is.
 *
 * Ported from `createAnchorMarkerForModel` (STL_generate.html:5351), its
 * visibility rule in `updateHighlights` (:6670) and the checkbox behind
 * `toggleAnchorMarkerVisibility` (:16187).
 *
 * It exists because the anchor is NOT the same point for every kind: a rack's
 * is a bottom corner and an ACU's is its centre, so the three position boxes in
 * the inspector mean different things from one selection to the next. The ball
 * is the only thing that says which.
 *
 * Two decisions differ from a literal transcription and both are deliberate:
 *
 * - **The markers live in their own group under `globalGroup`, not inside each
 *   model's render root.** The legacy parents one to every model group as it is
 *   built, so a rebuild has to re-create them all; here they survive a rebuild
 *   and are positioned from `geometry.location` directly. `globalGroup` has an
 *   identity transform, so that location IS the world point — the same reason
 *   the move gizmo's pivot hangs there.
 *
 * - **The flag is read and written at `meta.display.showAnchorMarker`.** The
 *   legacy reads `model.showAnchorMarker`, a v3 field that does not exist on a
 *   V4 model: the migrator moved it into `meta.display` (cfdproj_migrate.js:153)
 *   and every .cfdproj in `case/` carries it there. So the legacy checkbox
 *   writes a field nothing reloads — untick it, save, reopen, and the marker is
 *   back. Writing the V4 field is what makes the checkbox persist.
 *
 * The ball is drawn like the resize handles it sits among, and sized against
 * them. A blue corner cube sits exactly on the anchor of a floor, a baffle and
 * every flat rect patch; it is drawn with depth testing off and grows with the
 * camera distance. A ball with a fixed, model-relative radius and ordinary
 * depth testing was drawn first and covered: from a room overview the user saw
 * a blue cube and no red at all. So the ball is transparent with depth testing
 * off at `ANCHOR_RENDER_ORDER` (after the green edge cubes, before the blue
 * corner cube), and never drawn smaller than 0.9 x the cube — a red ring
 * around it. The numbers are geometry_pure.js's, shared with the legacy editor.
 */

import * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'
import type { TreeSelection } from '../interaction/treeNodes'
import { geometryPure } from '../legacy/geometryBuilder'
import * as store from '../projectStore'
import { findPatchOnModel } from './faceDragActions'
import { patchAnchorWorld } from './patchAnchor'

export const ANCHOR_COLOR = 0xff4d4f
export const ANCHOR_EMISSIVE = 0x661111

/**
 * The ball's radius: 3.5% of the model's longest side, clamped to 18-90 mm.
 *
 * Scaled rather than fixed because the same marker has to be findable on a
 * 60 mm pipe junction and not swallow a 2 m rack; clamped because 3.5% of a
 * 40 m wall would be a 1.4 m sphere sitting in the room.
 *
 * The rule is the SHARED one (geometry_pure.js), so both editors agree on it.
 */
export function anchorMarkerRadius(size: { x?: number; y?: number; z?: number } | undefined): number {
  return geometryPure().anchorMarkerRadius(size)
}

/**
 * The radius a ball is DRAWN at, for a camera `dist` metres away: its own
 * radius, raised to 0.9 x the handle cube's side once the cube is larger.
 * 0.9 is past the cube's half-diagonal (0.866), so the ball always shows as a
 * ring around the cube that shares its point.
 */
export function anchorDisplayRadius(base: number, dist: number): number {
  return geometryPure().anchorDisplayRadius(base, dist)
}

/** Whether the model's own flag asks for a marker. Absent means yes. */
export function showsAnchorMarker(model: Construction | null | undefined): boolean {
  if (!model) return false
  const display = model.meta?.display
  if (typeof display?.showAnchorMarker === 'boolean') return display.showAnchorMarker
  // A v3 model still in memory keeps the flag at the top level; reading only
  // the V4 field would show a marker on a pipe that asked not to have one.
  const legacy = (model as { showAnchorMarker?: unknown }).showAnchorMarker
  return typeof legacy === 'boolean' ? legacy : true
}

/**
 * The tab the markers belong to.
 *
 * Geometry only, following the rule that a scene helper is visible on its
 * owning tab and nowhere else (CLAUDE.md, frontend rule 7). Its two siblings in
 * the same inspector group are gated the same way in the legacy —
 * `_resizeHandleGroup.visible = isGeo` and `_moveGizmo.setVisible(isGeo)`
 * (STL_generate.html:19479-19482) — and a red ball in a mesh or solver
 * screenshot is exactly what that rule exists to prevent.
 */
export function anchorMarkerTabAllows(activeTab: string): boolean {
  return activeTab === 'geometry'
}

/** The whole visibility rule, so it can be tested without a scene. */
export function anchorMarkerVisible(
  model: Construction | null | undefined,
  selectedUids: readonly string[],
  activeTab: string,
): boolean {
  if (!model || !anchorMarkerTabAllows(activeTab)) return false
  if (!showsAnchorMarker(model)) return false
  // Only the SELECTION gets a marker: one ball per model in a 238-rack room is
  // a red haze, and the anchor only matters for the model being positioned.
  return selectedUids.includes(model.uid)
}

/** The inspector checkbox. */
export function setShowAnchorMarker(uid: string, show: boolean): void {
  store.edit('toggleAnchorMarkerVisibility', `${uid} -> ${show}`, (draft) => {
    const hit = store.findConstruction(draft, uid)
    if (!hit) return
    const meta = hit.model.meta ?? (hit.model.meta = { name: uid })
    // In place, so `display`'s key order survives into the saved file.
    const display = meta.display ?? (meta.display = {})
    display.showAnchorMarker = show
  })
}

/** A face's placement in model-local space, as `getFaceLayouts` reports it. */
export interface FaceLayoutLike {
  origin: { x: number; y: number; z: number }
  right: { x: number; y: number; z: number }
  up: { x: number; y: number; z: number }
}

/**
 * Where a patch's ANCHOR is, in the model's local space.
 *
 * The anchor is the point the inspector's coordinates describe: a rectangle's
 * bottom-left corner (`x`, `y`) or a circle's centre (`cx`, `cy`), measured in
 * the face's own 2D frame. Mapping it through the face layout puts the ball on
 * the face where the numbers say it is — which is the only way to tell, from
 * the viewport, which corner "x = 2.4" refers to.
 *
 * Null for a partition wall (its patches live in corner space, not the mirrored
 * face layout) and for a patch with no usable numbers.
 */
export function patchAnchorLocal(
  patch: { type?: unknown; x?: unknown; y?: unknown; cx?: unknown; cy?: unknown; [k: string]: unknown },
  layout: FaceLayoutLike | null | undefined,
): THREE.Vector3 | null {
  if (!layout) return null
  const circle = patch.type === 'circle'
  const u = Number(circle ? patch.cx : patch.x)
  const v = Number(circle ? patch.cy : patch.y)
  if (!Number.isFinite(u) || !Number.isFinite(v)) return null
  const o = layout.origin
  const r = layout.right
  const up = layout.up
  return new THREE.Vector3(
    o.x + r.x * u + up.x * v,
    o.y + r.y * u + up.y * v,
    o.z + r.z * u + up.z * v,
  )
}

/**
 * The selected patch's anchor plus the ball's model-relative size.
 *
 * `position` is in `globalGroup`'s space, where the markers hang — the space a
 * model's `geometry.location` is in, which the app keeps equal to world space.
 */
export interface PatchAnchor {
  key: string
  position: { x: number; y: number; z: number }
  radius: number
}

/**
 * The selected patch's ball, or null when the selection is not a patch of
 * `model`.
 *
 * `surfacePoint` is the anchor the face handles are drawn from (faceDrag's
 * `anchorPoint`, in globalGroup space). It is the only placement that is right
 * on a server's face and on a wall-mounted unit's curved front, so it comes
 * first. A MODEL patch the handles do not cover falls back to its face layout;
 * a SERVER patch has no such fallback, because the rack's own faces do not
 * hold it. Keyed by model, server and patch, so the same patch id on two
 * servers is never mistaken for one ball.
 */
export function selectedPatchAnchor(
  node: TreeSelection | null | undefined,
  model: Construction | null | undefined,
  surfacePoint: () => { x: number; y: number; z: number } | null,
): PatchAnchor | null {
  if (!node || !model || !node.patchId || node.uid !== model.uid) return null
  if (node.type !== 'patch' && node.type !== 'serverPatch') return null
  let at = surfacePoint()
  if (!at && node.type === 'patch') {
    const site = findPatchOnModel(model, node.patchId)
    at = site ? patchAnchorWorld(model, site.faceId, site.patch as unknown as Record<string, unknown>) : null
  }
  if (!at) return null
  return {
    key: `${model.uid}/${node.equipmentId ?? ''}/${node.patchId}`,
    position: { x: at.x, y: at.y, z: at.z },
    radius: anchorMarkerRadius(model.geometry?.size),
  }
}

export interface AnchorMarkerHost {
  /** Identity-transform parent, so a model's location IS the marker's position. */
  globalGroup: THREE.Group
  project(): CfdProject | null
  selectedUids(): readonly string[]
  activeTab(): string
  requestRender(): void
  /**
   * The camera the balls are sized for, as the handle cubes are. Optional:
   * without one a ball keeps its model-relative radius.
   */
  camera?(): THREE.Camera | null
  /**
   * The selected PATCH's anchor, when a patch is selected. Optional: the
   * markers predate patch anchors, and a host without patches has none.
   */
  patchAnchor?(): PatchAnchor | null
}

export interface AnchorMarkers {
  refresh(): void
  /**
   * Re-size every drawn ball for where the camera is NOW.
   *
   * `refresh` sizes a ball from the camera distance at the moment it is placed,
   * and a zoom does not re-run it — so a ball placed from across the room stays
   * room-scale while the user rolls in and buries the model it marks. Called
   * once per frame from the render loop; with no markers it returns at once, and
   * with some it is one `distanceTo` per ball plus a compare, so an idle camera
   * writes nothing.
   */
  updateScale(): void
  /** The uids that currently have a visible marker — for browser verification. */
  shown(): string[]
  dispose(): void
}

export function createAnchorMarkers(host: AnchorMarkerHost): AnchorMarkers {
  const group = new THREE.Group()
  group.name = 'anchorMarkers'
  host.globalGroup.add(group)

  // One material for every marker, as the legacy does: they are all the same
  // red, and a material per selected model is a GPU program per model.
  //
  // transparent + depthTest off, like the handles: renderOrder only sorts
  // within one render list, and an opaque ball lands in the opaque pass, which
  // is drawn before (and so under) every transparent handle cube.
  const material = new THREE.MeshPhongMaterial({
    color: ANCHOR_COLOR,
    emissive: ANCHOR_EMISSIVE,
    shininess: 30,
    specular: 0x111111,
    transparent: true,
    opacity: 1,
    depthTest: false,
    depthWrite: false,
  })
  // One unit sphere, scaled per ball: the drawn radius follows the camera, and
  // a geometry per radius would be rebuilt on every select after a zoom.
  const sphere = new THREE.SphereGeometry(1, 22, 22)

  const markers = new Map<string, THREE.Mesh<THREE.SphereGeometry, THREE.MeshPhongMaterial>>()
  const world = new THREE.Vector3()
  const groupScale = new THREE.Vector3()

  function drop(key: string) {
    const mesh = markers.get(key)
    if (!mesh) return
    group.remove(mesh)
    markers.delete(key)
  }

  function ball(key: string, userData: Record<string, unknown>) {
    let mesh = markers.get(key)
    if (!mesh) {
      mesh = new THREE.Mesh(sphere, material)
      mesh.renderOrder = geometryPure().ANCHOR_RENDER_ORDER
      group.add(mesh)
      markers.set(key, mesh)
    }
    mesh.userData = userData
    return mesh
  }

  /**
   * Size one ball for the camera's CURRENT distance: its base radius, raised to
   * 0.9 x the handle cube's side. The distance is measured in WORLD space, as
   * the handle cubes measure it, and the group's world scale is divided out so
   * `radius` is world metres. False when nothing changed, so a per-frame caller
   * can leave the mesh (and its matrix) alone.
   */
  function sizeBall(
    mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshPhongMaterial>,
    baseRadius: number,
    camera: THREE.Camera | null,
  ): boolean {
    const radius = camera
      ? anchorDisplayRadius(
          baseRadius,
          camera.position.distanceTo(world.copy(mesh.position).applyMatrix4(group.matrixWorld)),
        )
      : baseRadius
    if (Number.isFinite(radius) && Math.abs(radius - Number(mesh.userData.displayRadius)) < 1e-9) {
      return false
    }
    group.getWorldScale(groupScale)
    mesh.scale.set(
      radius / (Math.abs(groupScale.x) || 1),
      radius / (Math.abs(groupScale.y) || 1),
      radius / (Math.abs(groupScale.z) || 1),
    )
    mesh.userData.displayRadius = radius
    return true
  }

  /** Put a ball at `position` (globalGroup space) and size it for the camera. */
  function place(
    mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshPhongMaterial>,
    position: { x: number; y: number; z: number },
    baseRadius: number,
  ) {
    mesh.position.set(position.x, position.y, position.z)
    group.updateWorldMatrix(true, false)
    sizeBall(mesh, baseRadius, host.camera?.() ?? null)
    mesh.visible = true
  }

  /**
   * No `requestRender` here, unlike `refresh`: this runs INSIDE the render loop,
   * so asking for another frame would draw every zoom frame twice — a full
   * extra scene render for a scale write.
   */
  function updateScale() {
    if (!markers.size) return
    const camera = host.camera?.() ?? null
    group.updateWorldMatrix(true, false)
    for (const mesh of markers.values()) sizeBall(mesh, Number(mesh.userData.radius) || 0, camera)
  }

  function refresh() {
    const project = host.project()
    const selected = host.selectedUids()
    const tab = host.activeTab()
    const wanted = new Set<string>()

    if (project) {
      for (const uid of selected) {
        const hit = store.findConstruction(project, uid)
        if (!hit || !anchorMarkerVisible(hit.model, selected, tab)) continue
        wanted.add(uid)
        const radius = anchorMarkerRadius(hit.model.geometry?.size)
        const mesh = ball(uid, {
          modelId: uid,
          isAnchorMarker: true,
          anchorMarkerEnabled: true,
          radius,
        })
        const loc = hit.model.geometry?.location
        place(mesh, { x: Number(loc?.x) || 0, y: Number(loc?.y) || 0, z: Number(loc?.z) || 0 }, radius)
      }
    }

    // The selected patch's anchor ball, keyed apart from the models' so a
    // model and its patch can both show one. Same tab rule as the models.
    const patch = anchorMarkerTabAllows(tab) ? host.patchAnchor?.() : null
    if (patch) {
      const key = `patch:${patch.key}`
      wanted.add(key)
      const mesh = ball(key, {
        patchKey: patch.key,
        isAnchorMarker: true,
        isPatchAnchor: true,
        radius: patch.radius,
      })
      place(mesh, patch.position, patch.radius)
    }

    for (const key of [...markers.keys()]) if (!wanted.has(key)) drop(key)
    host.requestRender()
  }

  return {
    refresh,
    updateScale,
    shown: () => [...markers.keys()],
    dispose() {
      for (const key of [...markers.keys()]) drop(key)
      sphere.dispose()
      material.dispose()
      host.globalGroup.remove(group)
    },
  }
}
