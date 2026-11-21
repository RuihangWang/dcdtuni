/**
 * Where a drag can snap to: the edges of everything else in the room.
 *
 * The two snaps ask different questions and are collected differently, which is
 * why they are two functions and not one:
 *
 * - A model RESIZE moves one edge along one direction, so a candidate is a
 *   single number — every other model's extent projected onto that direction.
 *   A rotated model contributes its ORIENTED box, not its axis-aligned one, or
 *   a rack turned 90 degrees would offer edges it does not have.
 * - A PATCH drag moves inside a face, so candidates come in two directions at
 *   once, and they include other patches: the ones on the same face, and the
 *   ones on models of the same KIND, which is what makes two containment
 *   baffles' openings line up (STL_generate.html:17155).
 *
 * Bounds come from the BUILT meshes through `transform_tools.js`, never from
 * `geometry.size`: a rack's real extent includes its doors and its equipment,
 * and snapping to the box the numbers describe would leave a visible gap.
 */

import * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import { projectPoints, type Vec3 } from './faceDragMath'
import { facesOf, layoutOf, rectOf, v3 } from './faceDragModel'
import { transformToolsModule, type TransformToolsApi } from '../legacy/transformTools'
import type { PatchRecord } from './patchGeometry'

/** The built scene, as the snap search needs to read it. */
export interface SnapScene {
  renderRoots(): THREE.Object3D[]
  modelOf(uid: string | null | undefined): Construction | null
  rootOf(uid: string | null | undefined): THREE.Object3D | null
  tools(): TransformToolsApi | null
}

/** The average model size the legacy derives both snap thresholds from. */
export function averageModelSize(scene: SnapScene): number {
  const tools = scene.tools()
  if (!tools) return 1
  let sum = 0
  let count = 0
  const scratch = new THREE.Vector3()
  for (const root of scene.renderRoots()) {
    const uid = (root.userData as { modelId?: string }).modelId
    if (!uid) continue
    const bounds = tools.getRenderRootWorldBounds(root)
    if (!bounds) continue
    const size = bounds.getSize(scratch)
    sum += Math.max(size.x, size.y, size.z)
    count += 1
  }
  return count ? sum / count : 1
}

/** Every other model's extent along one direction — the resize snap targets. */
export function modelEdgeTargets(
  scene: SnapScene,
  uid: string,
  dir: Vec3,
  threshold: number,
): number[] {
  const tools = scene.tools()
  if (!tools) return []
  const mod = transformToolsModule()
  const targets: number[] = []
  const own = scene.rootOf(uid)
  const ownBounds = own ? tools.getRenderRootWorldBounds(own) : null
  const near = ownBounds ? tools.querySnapCandidates(ownBounds, threshold) : null

  const add = (otherId: string | undefined, bounds: THREE.Box3 | null) => {
    if (!otherId || otherId === uid || !bounds) return
    const other = scene.modelOf(otherId)
    if (!other) return
    const points: Vec3[] = mod.hasModelRotation(other)
      ? mod.getOBBCorners(mod.getModelOBB(other)).map(v3)
      : (() => {
          const out: Vec3[] = []
          for (let ix = 0; ix < 2; ix++) {
            for (let iy = 0; iy < 2; iy++) {
              for (let iz = 0; iz < 2; iz++) {
                out.push({
                  x: ix ? bounds.max.x : bounds.min.x,
                  y: iy ? bounds.max.y : bounds.min.y,
                  z: iz ? bounds.max.z : bounds.min.z,
                })
              }
            }
          }
          return out
        })()
    const [lo, hi] = projectPoints(points, dir)
    targets.push(lo, hi)
  }

  if (near) {
    for (const entry of near) add(entry.id, entry.bounds)
  } else {
    for (const root of scene.renderRoots()) {
      add((root.userData as { modelId?: string }).modelId, tools.getRenderRootWorldBounds(root))
    }
  }
  return targets
}

export interface PatchTargets {
  r: number[]
  u: number[]
}

/** Both directions at once, projected into the face's own world frame. */
export function patchSnapTargets(
  scene: SnapScene,
  model: Construction,
  root: THREE.Object3D,
  faceId: string,
  patchId: string,
  worldRight: THREE.Vector3,
  worldUp: THREE.Vector3,
): PatchTargets {
  const tools = scene.tools()
  const out: PatchTargets = { r: [], u: [] }
  const layout = layoutOf(model, faceId)
  if (!layout) return out
  const originWorld = root.localToWorld(layout.origin.clone())
  const originR = originWorld.dot(worldRight)
  const originU = originWorld.dot(worldUp)

  const samePatches = (facesOf(model)?.[faceId]?.patches ?? []) as PatchRecord[]
  for (const other of samePatches) {
    if (other.id === patchId || other.type !== 'rect') continue
    const rect = rectOf(other)
    out.r.push(originR + rect.x, originR + rect.x + rect.w)
    out.u.push(originU + rect.y, originU + rect.y + rect.h)
  }

  if (!tools) return out
  for (const otherRoot of scene.renderRoots()) {
    const otherId = (otherRoot.userData as { modelId?: string }).modelId
    if (!otherId || otherId === model.uid) continue
    const bounds = tools.getRenderRootWorldBounds(otherRoot)
    if (bounds) {
      const points: Vec3[] = []
      for (let ix = 0; ix < 2; ix++) {
        for (let iy = 0; iy < 2; iy++) {
          for (let iz = 0; iz < 2; iz++) {
            points.push({
              x: ix ? bounds.max.x : bounds.min.x,
              y: iy ? bounds.max.y : bounds.min.y,
              z: iz ? bounds.max.z : bounds.min.z,
            })
          }
        }
      }
      const [loR, hiR] = projectPoints(points, v3(worldRight))
      const [loU, hiU] = projectPoints(points, v3(worldUp))
      out.r.push(loR, hiR)
      out.u.push(loU, hiU)
    }

    const other = scene.modelOf(otherId)
    if (!other || other.kind !== model.kind || other.kind === 'rack') continue
    otherRoot.updateMatrixWorld(true)
    const otherFaces = facesOf(other)
    if (!otherFaces) continue
    for (const [otherFaceId, face] of Object.entries(otherFaces)) {
      const otherLayout = layoutOf(other, otherFaceId)
      if (!otherLayout) continue
      for (const patch of (face.patches ?? []) as PatchRecord[]) {
        if (patch.type !== 'rect') continue
        const rect = rectOf(patch)
        const corners = [
          [rect.x, rect.y],
          [rect.x + rect.w, rect.y],
          [rect.x + rect.w, rect.y + rect.h],
          [rect.x, rect.y + rect.h],
        ].map(([u, v]) =>
          otherRoot.localToWorld(
            otherLayout.origin
              .clone()
              .addScaledVector(otherLayout.right, u!)
              .addScaledVector(otherLayout.up, v!),
          ),
        )
        const [loR, hiR] = projectPoints(corners.map(v3), v3(worldRight))
        const [loU, hiU] = projectPoints(corners.map(v3), v3(worldUp))
        out.r.push(loR, hiR)
        out.u.push(loU, hiU)
      }
    }
  }
  return out
}
