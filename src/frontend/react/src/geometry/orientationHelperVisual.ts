/**
 * The surface-normal arrows on a selected wall or solid floor.
 *
 * A port of `updateWallFloorOrientationHelper` (STL_generate.html:7287) and the
 * context it draws from, `getWallFloorOrientationContext` (:6029). Without it
 * `surfaceOrientation.ts`'s flip button was a control with no readout: a wall
 * is a zero-thickness sheet, so flipping its winding changes nothing you can
 * see in the viewport, and the only way to find out which way the surface
 * faced was to mesh the case and look at which side kept its cells.
 *
 * Two arrows from the face centre, the legacy's colours: GREEN along
 * `-normal`, RED along `+normal`, and a white hub at the centre. `normal` is
 * the outward normal of the primary face (`right x up`, the winding the STL
 * writer exports), already flipped when `reverseSolidNormal` is set — so the
 * red arrow points where the exported solid faces.
 *
 * ## Why this reads the render root rather than `geometry.location`
 *
 * `anchorMarkers` can position from the model's location because `globalGroup`
 * has an identity transform. This helper cannot: the toolbar's rotate / mirror
 * buttons fall through to the WHOLE SCENE when nothing is selected
 * (`globalTransform`), which turns the wrapper group, and a normal derived from
 * the model alone would then point the wrong way in a mirrored scene. Reading
 * `matrixWorld` picks that up, which is why the group is parented to the SCENE
 * in world space, as the legacy parents it.
 */

import * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { createMarker, unregisterHelper } from './helperRegistry'
import { faceLayout } from './seedConfig'
import { orientationEligible, primaryOrientationFaceId, isPrimaryFaceReversed } from './surfaceOrientation'

/** `-normal`: the side the surface's triangles face away from. */
export const ORIENTATION_BACK_COLOR = 0x36d47a
/** `+normal`: the direction the exported solid's triangles point. */
export const ORIENTATION_FRONT_COLOR = 0xe06c75
export const ORIENTATION_HUB_COLOR = 0xf5f5f5

export interface OrientationMetrics {
  arrowLength: number
  headLength: number
  headWidth: number
}

/**
 * Arrow sizing from the face's larger side, clamped at both ends.
 *
 * Straight from the legacy: unclamped, a 40 m room wall would grow a 14 m arrow
 * and a 100 mm sheet would grow one too short to see which way it points.
 */
export function orientationMetrics(extent: number): OrientationMetrics {
  const safe = Number.isFinite(extent) ? Math.max(extent, 0.05) : 0.05
  const arrowLength = Math.max(0.18, Math.min(1.4, safe * 0.35))
  return {
    arrowLength,
    headLength: Math.min(arrowLength * 0.26, 0.18),
    headWidth: Math.min(arrowLength * 0.16, 0.09),
  }
}

export interface OrientationContext {
  uid: string
  faceId: string
  /** Face centre, in world space. */
  center: THREE.Vector3
  /** Outward normal, in world space, already flipped by `reverseSolidNormal`. */
  normal: THREE.Vector3
  /** The face's larger local side, which sets the arrow length. */
  extent: number
}

/**
 * The face centre and normal for a model, or `null` when it has neither.
 *
 * `root` is the model's render root. Its world matrix is refreshed here rather
 * than trusted: this runs from a React effect, which can land before the
 * renderer has walked the graph for the frame, and a stale matrix would put the
 * arrows where the model was one edit ago.
 */
export function orientationContext(
  model: Construction | null | undefined,
  root: THREE.Object3D | null,
): OrientationContext | null {
  if (!model || !root || !orientationEligible(model)) return null
  const faceId = primaryOrientationFaceId(model)
  if (!faceId) return null
  const faces = model.geometry?.faces
  if (!faces || Array.isArray(faces)) return null
  if (!(faces as Record<string, unknown>)[faceId]) return null
  const layout = faceLayout(model.geometry?.size, faceId)
  if (!layout) return null

  const right = new THREE.Vector3(layout.right.x, layout.right.y, layout.right.z)
  const up = new THREE.Vector3(layout.up.x, layout.up.y, layout.up.z)
  const center = new THREE.Vector3(layout.origin.x, layout.origin.y, layout.origin.z)
    .add(right.clone().multiplyScalar(layout.w * 0.5))
    .add(up.clone().multiplyScalar(layout.h * 0.5))

  root.updateWorldMatrix(true, false)
  center.applyMatrix4(root.matrixWorld)
  const normal = new THREE.Vector3()
    .crossVectors(right, up)
    .normalize()
    .transformDirection(root.matrixWorld)
  if (isPrimaryFaceReversed(model)) normal.negate()

  return {
    uid: model.uid,
    faceId,
    center,
    normal,
    // Unscaled, as in the legacy: the arrow reads the face's design size, not
    // the transform that happens to be on the model.
    extent: Math.max(layout.w, layout.h, 0.05),
  }
}

/**
 * The tab this helper belongs to.
 *
 * Geometry only, by the rule that a scene helper is visible on its owning tab
 * and nowhere else (CLAUDE.md, frontend rule 7) — the same gate `anchorMarkers`
 * carries. The legacy leaves the arrows in the scene on every tab, which is how
 * they end up in mesh screenshots.
 */
export function orientationHelperTabAllows(activeTab: string): boolean {
  return activeTab === 'geometry'
}

/** The whole visibility rule, so it can be tested without a scene. */
export function orientationHelperVisible(
  model: Construction | null | undefined,
  selectedUids: readonly string[],
  activeTab: string,
  previewActive: boolean,
): boolean {
  if (previewActive || !orientationHelperTabAllows(activeTab)) return false
  // Exactly one: with a multi-selection there is no single surface the arrows
  // belong to, and the legacy returns no context at all
  // (STL_generate.html:6033).
  if (selectedUids.length !== 1) return false
  if (!model || selectedUids[0] !== model.uid) return false
  return orientationEligible(model)
}

export interface OrientationHelperHost {
  /** World-space parent — see the note on `matrixWorld` above. */
  scene: THREE.Object3D
  project(): CfdProject | null
  selectedUids(): readonly string[]
  activeTab(): string
  previewActive(): boolean
  renderRoot(uid: string): THREE.Object3D | null
  findModel(project: CfdProject, uid: string): Construction | null
  requestRender(): void
}

export interface OrientationHelper {
  refresh(): void
  /** What is currently drawn — for browser verification. */
  shown(): { uid: string; faceId: string; center: number[]; normal: number[] } | null
  dispose(): void
}

export function createOrientationHelper(host: OrientationHelperHost): OrientationHelper {
  const group = new THREE.Group()
  group.name = 'wallFloorOrientationHelper'
  group.visible = false
  host.scene.add(group)

  const owned: (THREE.BufferGeometry | THREE.Material)[] = []

  const backArrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), 1, ORIENTATION_BACK_COLOR)
  const frontArrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), new THREE.Vector3(), 1, ORIENTATION_FRONT_COLOR)
  for (const arrow of [backArrow, frontArrow]) {
    for (const part of [arrow.line, arrow.cone] as { renderOrder: number; material: THREE.Material }[]) {
      part.renderOrder = 60
      part.material.depthTest = false
      part.material.transparent = true
    }
    group.add(arrow)
  }

  // Point markers (geometry_pure.js): the shared ringed look, held at a
  // constant size on screen by the render loop. Drawn over the surface they
  // describe (depthTest off), which would otherwise hide them.
  const backTip = createMarker(ORIENTATION_BACK_COLOR, 'seedTip')
  const frontTip = createMarker(ORIENTATION_FRONT_COLOR, 'seedTip')
  const hub = createMarker(ORIENTATION_HUB_COLOR, 'seedHub')
  for (const mesh of [backTip, frontTip, hub]) {
    mesh.renderOrder = 60
    owned.push(mesh.material)
    group.add(mesh)
  }

  let current: OrientationContext | null = null

  function refresh() {
    const project = host.project()
    const selected = host.selectedUids()
    const only = selected.length === 1 ? selected[0] : null
    const model = project && only ? host.findModel(project, only) : null
    const context = orientationHelperVisible(model, selected, host.activeTab(), host.previewActive())
      ? orientationContext(model, host.renderRoot(model!.uid))
      : null
    current = context

    if (!context) {
      if (group.visible) {
        group.visible = false
        host.requestRender()
      }
      return
    }

    const m = orientationMetrics(context.extent)
    const back = context.normal.clone().negate()
    const front = context.normal.clone()

    backArrow.position.copy(context.center)
    backArrow.setDirection(back)
    backArrow.setLength(m.arrowLength, m.headLength, m.headWidth)
    frontArrow.position.copy(context.center)
    frontArrow.setDirection(front)
    frontArrow.setLength(m.arrowLength, m.headLength, m.headWidth)

    backTip.position.copy(context.center).addScaledVector(back, m.arrowLength)
    frontTip.position.copy(context.center).addScaledVector(front, m.arrowLength)
    hub.position.copy(context.center)
    group.userData = {
      isWallFloorOrientationHelper: true,
      modelId: context.uid,
      faceId: context.faceId,
    }
    group.visible = true
    host.requestRender()
  }

  return {
    refresh,
    shown: () =>
      current && group.visible
        ? {
            uid: current.uid,
            faceId: current.faceId,
            center: current.center.toArray(),
            normal: current.normal.toArray(),
          }
        : null,
    dispose() {
      host.scene.remove(group)
      backArrow.dispose()
      frontArrow.dispose()
      for (const mesh of [backTip, frontTip, hub]) {
        unregisterHelper(mesh)
        mesh.geometry.dispose()
      }
      for (const item of owned) item.dispose()
      group.clear()
    },
  }
}
