/**
 * Rotate, mirror and reset — the toolbar's transform buttons.
 *
 * A port of `transformSelectedModel` (STL_generate.html:15369). Three rules
 * carry the behaviour, and each is a bug if dropped:
 *
 * - **Rotation PRE-multiplies.** `q_new = q_axis * q_old` turns the model about
 *   the WORLD axis; post-multiplying turns it about its own, already-rotated
 *   axis, so a rack yawed 90° would then pitch about the wrong one.
 * - **Mirroring is a negative SCALE**, not a rotation. It flips the geometry's
 *   winding, which is what makes an exported STL's normals point the other way
 *   — the exporter compensates, and only because the sign is in `scale`.
 * - **A multi-selection rotates about the FIRST model's anchor**, and every
 *   other model's ANCHOR orbits that pivot as well as its own orientation
 *   turning. Rotating each model in place would spin eight racks on the spot
 *   instead of turning the row.
 *
 * Pipes are a special case: their shape is `geometry.waypoints`, so a rotation
 * turns the waypoints about their own centroid and DROPS the connections —
 * a pipe that has been turned no longer meets the ports it was joined to.
 */

import * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { axisQuaternion, multiplyQuaternions } from '../projectStore'

export type TransformAction =
  | { kind: 'rotate'; axis: 'x' | 'y' | 'z'; degrees: number }
  | { kind: 'mirror'; axis: 'x' | 'y' | 'z' }
  | { kind: 'reset' }

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface Quat {
  x: number
  y: number
  z: number
  w: number
}

export function axisVector(axis: 'x' | 'y' | 'z'): THREE.Vector3 {
  return new THREE.Vector3(axis === 'x' ? 1 : 0, axis === 'y' ? 1 : 0, axis === 'z' ? 1 : 0)
}

/** The model's new orientation and scale under one action. */
export function applyToTransform(
  quaternion: Quat,
  scale: Vec3,
  action: TransformAction,
): { quaternion: Quat; scale: Vec3 } {
  if (action.kind === 'reset') {
    return { quaternion: { x: 0, y: 0, z: 0, w: 1 }, scale: { x: 1, y: 1, z: 1 } }
  }
  if (action.kind === 'mirror') {
    return {
      quaternion: { ...quaternion },
      scale: { ...scale, [action.axis]: scale[action.axis] * -1 },
    }
  }
  // The store's own math, not a second copy: the single-model toolbar path
  // uses it, and the two must agree to the bit or one rack in a selection ends
  // up oriented differently from the same rack transformed alone.
  const turn = axisQuaternion(
    action.axis.toUpperCase() as 'X' | 'Y' | 'Z',
    action.degrees,
  )
  return { quaternion: multiplyQuaternions(turn, quaternion), scale: { ...scale } }
}

/** Where a co-transformed model's anchor lands, orbiting the pivot. */
export function orbitAnchor(location: Vec3, pivot: Vec3, action: TransformAction): Vec3 {
  const offset = new THREE.Vector3(
    location.x - pivot.x,
    location.y - pivot.y,
    location.z - pivot.z,
  )
  if (action.kind === 'rotate') {
    offset.applyQuaternion(
      new THREE.Quaternion().setFromAxisAngle(
        axisVector(action.axis),
        (action.degrees * Math.PI) / 180,
      ),
    )
  } else if (action.kind === 'mirror') {
    offset[action.axis] *= -1
  }
  return { x: pivot.x + offset.x, y: pivot.y + offset.y, z: pivot.z + offset.z }
}

/** The centroid of a pipe's waypoints — the pivot it turns about. */
export function centroid(points: Vec3[]): Vec3 {
  if (!points.length) return { x: 0, y: 0, z: 0 }
  const sum = points.reduce(
    (acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y, z: acc.z + p.z }),
    { x: 0, y: 0, z: 0 },
  )
  return { x: sum.x / points.length, y: sum.y / points.length, z: sum.z / points.length }
}

function label(action: TransformAction): string {
  if (action.kind === 'reset') return 'reset'
  if (action.kind === 'mirror') return `mirror${action.axis.toUpperCase()}`
  return `rot${action.axis.toUpperCase()}:${action.degrees}`
}

type GeometryBag = {
  location?: Vec3
  quaternion?: Quat
  scale?: Vec3
  waypoints?: Vec3[]
}

/**
 * Rotate a pipe's waypoints about their centroid and drop its connections.
 *
 * Mirror and reset are no-ops on a pipe, as in the legacy: a pipe has no
 * orientation of its own to reset, and mirroring a route is not something the
 * connection model can express.
 */
function transformPipe(model: Construction, action: TransformAction) {
  if (action.kind !== 'rotate') return
  const geometry = model.geometry as unknown as GeometryBag
  const wps = geometry.waypoints
  if (!wps || wps.length < 2) return
  const pivot = centroid(wps)
  const turn = new THREE.Quaternion().setFromAxisAngle(
    axisVector(action.axis),
    (action.degrees * Math.PI) / 180,
  )
  const turnPoint = (p: Vec3) => {
    const off = new THREE.Vector3(p.x - pivot.x, p.y - pivot.y, p.z - pivot.z).applyQuaternion(turn)
    p.x = pivot.x + off.x
    p.y = pivot.y + off.y
    p.z = pivot.z + off.z
  }
  wps.forEach(turnPoint)

  const cooling = ((model as unknown as { cooling?: Record<string, unknown> }).cooling ??
    ((model as unknown as { cooling: Record<string, unknown> }).cooling = {})) as Record<
    string,
    unknown
  >
  const valves = (cooling.valves ?? []) as { position?: Vec3; connectedManifoldPortId?: string; connectedRackId?: string }[]
  valves.forEach((v) => {
    if (v.position) turnPoint(v.position)
    // The valve has moved with the pipe, so whatever it was joined to is no
    // longer where it is.
    v.connectedManifoldPortId = ''
    v.connectedRackId = ''
  })
  const connection = (cooling.connection ?? (cooling.connection = {})) as Record<string, unknown>
  if (connection.startPortId) connection.startPortId = ''
  if (connection.endPortId) connection.endPortId = ''
  cooling.loopRole = 'unconnected'
}

/**
 * Apply one action to the selection.
 *
 * `uids[0]` (or `pivotUid`) is the pivot for a multi-selection, matching the
 * legacy's anchorModelId.
 */
export function transformModels(
  uids: string[],
  action: TransformAction,
  pivotUid?: string | null,
) {
  if (!uids.length) return
  store.edit('transformModel', `${label(action)} x${uids.length}`, (draft: CfdProject) => {
    const pivotId = pivotUid && uids.includes(pivotUid) ? pivotUid : uids[0]!
    const pivotModel = store.findConstruction(draft, pivotId)?.model
    const pivot = (pivotModel?.geometry as unknown as GeometryBag)?.location ?? {
      x: 0,
      y: 0,
      z: 0,
    }

    for (const uid of uids) {
      const model = store.findConstruction(draft, uid)?.model
      if (!model) continue
      if (model.kind === 'pipe_segment') {
        transformPipe(model, action)
        continue
      }
      const geometry = model.geometry as unknown as GeometryBag
      if (!geometry) continue
      const next = applyToTransform(
        geometry.quaternion ?? { x: 0, y: 0, z: 0, w: 1 },
        geometry.scale ?? { x: 1, y: 1, z: 1 },
        action,
      )
      geometry.quaternion = next.quaternion
      geometry.scale = next.scale
      if (uids.length > 1 && uid !== pivotId && geometry.location) {
        geometry.location = orbitAnchor(geometry.location, pivot, action)
      }
    }
  })
}
