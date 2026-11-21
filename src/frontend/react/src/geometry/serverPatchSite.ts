/**
 * A patch that belongs to a SERVER inside a rack, not to the rack.
 *
 * The rack's own `geometry.faces` carry nothing draggable: a vent is cut into
 * one of the six faces of a server sitting in a slot, and that face has its own
 * rectangle. `PATCH_DRAG_KINDS` in `faceDrag.ts` therefore leaves `rack` out,
 * which is why a server vent can be selected in the viewport but not moved —
 * the last gap in the round-4 list.
 *
 * ## Where the face rectangle comes from
 *
 * The drag needs that rectangle in RACK-LOCAL space. Two things rule out the
 * obvious routes, both checked in the browser:
 *
 * - There is no per-server group to read a transform from. A server patch
 *   mesh's parent chain is `Mesh → Group{modelId} → Group → Group → Scene`:
 *   the rack's root and nothing between.
 * - The shared builder computes the server's box inline while building it
 *   (`serverBox`, geometry_scene.js) and exports no function for it.
 *
 * So the box is recovered from the meshes the builder already produced. They
 * are what is drawn and what is exported, so a rectangle derived from them
 * cannot drift from either — where a second copy of the slot arithmetic here
 * could, silently, and would only show up as a boundary in the wrong place in
 * an OpenFOAM case.
 */

import * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import type { FaceRecord, PatchRecord } from './patchGeometry'

export interface ServerPatchSite {
  /** The server's id within the rack (`e_2`). */
  equipmentId: string
  faceId: string
  face: FaceRecord
  /** The LIVE patch object — a drag mutates it every frame. */
  patch: PatchRecord
}

interface ServerRecord {
  faces?: Record<string, FaceRecord>
}

function serversOf(model: Construction | undefined): Record<string, ServerRecord> {
  const constructions = (model as unknown as { constructions?: { servers?: unknown } } | undefined)
    ?.constructions
  const servers = constructions?.servers
  return servers && typeof servers === 'object' ? (servers as Record<string, ServerRecord>) : {}
}

/** Which server and face a patch id belongs to, or null if the rack has no such patch. */
export function findServerPatch(
  model: Construction | undefined,
  patchId: string,
): ServerPatchSite | null {
  for (const [equipmentId, server] of Object.entries(serversOf(model))) {
    for (const [faceId, face] of Object.entries(server.faces ?? {})) {
      const patch = (face.patches ?? []).find((p) => p.id === patchId)
      if (patch) return { equipmentId, faceId, face, patch }
    }
  }
  return null
}

/**
 * The server's box in rack-local space, unioned from its own built meshes.
 *
 * Only that server's: every mesh the builder makes carries `equipmentId`, and a
 * union over the whole rack would return the rack.
 */
export function serverBoxFromScene(
  rackRoot: THREE.Object3D | null | undefined,
  equipmentId: string,
): THREE.Box3 | null {
  if (!rackRoot) return null
  const box = new THREE.Box3()
  let found = false
  rackRoot.traverse((o) => {
    const data = o.userData as { isRackEquipment?: boolean; equipmentId?: string }
    if (!data.isRackEquipment || data.equipmentId !== equipmentId) return
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh || !mesh.geometry) return
    mesh.geometry.computeBoundingBox()
    const local = mesh.geometry.boundingBox
    if (!local) return
    // The mesh's own transform, relative to the rack root. The builder bakes
    // the placement into the geometry for these faces, but a mesh that carries
    // it on the object instead would otherwise contribute the wrong corner.
    const b = local.clone().applyMatrix4(mesh.matrix)
    box.union(b)
    found = true
  })
  return found ? box : null
}

/**
 * Write a server patch's geometry, as ONE undo entry.
 *
 * The sibling of `commitPatchGeometry` (faceDragActions.ts) for the server
 * case: the same contract — re-find in the DRAFT, write there — because the
 * live objects a drag mutated belong to the previous project snapshot.
 */
export function commitServerPatchGeometry(
  uid: string,
  patchId: string,
  values: Record<string, number>,
  action = 'serverPatchDrag',
): boolean {
  const project = store.getProject()
  if (!project) return false
  if (!findServerPatch(store.findConstruction(project, uid)?.model, patchId)) return false
  const summary = Object.entries(values)
    .map(([key, value]) => `${key}=${value.toFixed(4)}`)
    .join(' ')
  store.edit(action, `${uid}/${patchId} ${summary}`, (draft) => {
    const target = findServerPatch(store.findConstruction(draft, uid)?.model, patchId)
    if (!target) return
    Object.assign(target.patch as unknown as Record<string, number>, values)
  })
  return true
}

/**
 * A server patch is stored as FRACTIONS of its face — `x, y, w, h` in 0..1 —
 * where a box patch is stored in metres. The legacy converts at the boundary
 * (`getPointOnServerFace`, STL_generate.html:6978, divides by the face's w/h;
 * `_computeQuadCornersWorld`, :6420, multiplies back), and so does this port.
 * The first wiring of the drag wrote metres into the fraction fields; on a
 * 0.6 m face that moved the vent roughly the right amount, which is the worst
 * kind of wrong.
 */
export interface ServerFacePoint {
  x: number
  y: number
}

/** A point in the face's metric frame -> fractions of the face. */
export function serverFacePoint(
  faceMetres: { x: number; y: number },
  layout: { w: number; h: number },
): ServerFacePoint | null {
  if (!(layout.w > 1e-6) || !(layout.h > 1e-6)) return null
  return { x: faceMetres.x / layout.w, y: faceMetres.y / layout.h }
}

/** A fractional rect -> metres on the face, for the handles' corners. */
export function serverRectMetres(
  rect: { x: number; y: number; w: number; h: number },
  layout: { w: number; h: number },
): { x: number; y: number; w: number; h: number } {
  return { x: rect.x * layout.w, y: rect.y * layout.h, w: rect.w * layout.w, h: rect.h * layout.h }
}

/**
 * Keep a server patch inside its face. The legacy's `enforceServerPatchSafety`
 * (STL_generate.html:6786): rect x, y in [0, 1 - w/h], w and h in [0.01, 1];
 * circle r in [0.01, 0.5] with the centre kept r away from every edge.
 * Returns true when something had to be moved.
 */
export function enforceServerPatchSafety(patch: Record<string, unknown>): boolean {
  const num = (v: unknown, fallback: number) =>
    typeof v === 'number' && Number.isFinite(v) ? v : fallback
  const before = JSON.stringify(patch)
  if (patch.type === 'circle') {
    let r = num(patch.r, 0.01)
    if (r < 0.01) r = 0.01
    if (r > 0.5) r = 0.5
    let cx = num(patch.cx, 0.5)
    let cy = num(patch.cy, 0.5)
    if (cx - r < 0) cx = r
    if (cx + r > 1) cx = 1 - r
    if (cy - r < 0) cy = r
    if (cy + r > 1) cy = 1 - r
    patch.r = r
    patch.cx = cx
    patch.cy = cy
  } else {
    let w = num(patch.w, 0.01)
    let h = num(patch.h, 0.01)
    if (w < 0.01) w = 0.01
    if (h < 0.01) h = 0.01
    if (w > 1) w = 1
    if (h > 1) h = 1
    let x = num(patch.x, 0)
    let y = num(patch.y, 0)
    if (x < 0) x = 0
    if (y < 0) y = 0
    if (x + w > 1) x = 1 - w
    if (y + h > 1) y = 1 - h
    patch.w = w
    patch.h = h
    patch.x = x
    patch.y = y
  }
  return JSON.stringify(patch) !== before
}

/** The layout shape `faceDrag` works in: origin + basis + extent. */
export interface ServerFaceLayout {
  origin: THREE.Vector3
  right: THREE.Vector3
  up: THREE.Vector3
  w: number
  h: number
}

interface GeometryPureLike {
  getServerFaceRect(
    box: { x0: number; x1: number; y0: number; y1: number; z0: number; z1: number },
    faceId: string,
  ): ServerFaceLayout | null
}

function geometryPureOrNull(): GeometryPureLike | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: GeometryPureLike }).geometryPure ?? null
}

/**
 * The server face's rectangle, in RACK-LOCAL space.
 *
 * The box comes off the built meshes (above); the rectangle itself comes from
 * the SHARED `getServerFaceRect`, which is the same function the builder and
 * the exporter use. A second copy of that arithmetic here is exactly what would
 * drift from the drawn and exported geometry — and the only symptom would be a
 * boundary in the wrong place in an OpenFOAM case.
 */
export function serverFaceLayout(
  rackRoot: THREE.Object3D | null | undefined,
  equipmentId: string,
  faceId: string,
): ServerFaceLayout | null {
  const box = serverBoxFromScene(rackRoot, equipmentId)
  if (!box) return null
  const pure = geometryPureOrNull()
  if (!pure) return null
  try {
    return (
      pure.getServerFaceRect(
        { x0: box.min.x, x1: box.max.x, y0: box.min.y, y1: box.max.y, z0: box.min.z, z1: box.max.z },
        faceId,
      ) ?? null
    )
  } catch {
    return null
  }
}

/** What a server-vent drag is doing when the inter-server snap is asked for. */
export interface ServerPatchSnapInput {
  /** The dragged server's id; its own vents are never snap targets. */
  equipmentId: string
  /** The dragged rectangle, in the server face's 0–1 fraction space. */
  rect: { x: number; y: number; w: number; h: number }
  isResize: boolean
  edge: 'left' | 'right' | 'top' | 'bottom' | null
  /** The face's width and height in metres (`ServerFaceLayout.w` / `.h`). */
  faceW: number
  faceH: number
}

/**
 * The shift that lines a server vent up with the vents of the OTHER servers in
 * the same rack — the legacy's inter-server snap (STL_generate.html, the block
 * after the box-patch snap in `updatePatchDrag`), with the same rules:
 *
 * - targets are every rect patch on every face of every other server, with the
 *   legacy's missing-size defaults (`x||0`, `w||0.5`);
 * - each axis independently takes the nearest of the four min/max pairings;
 * - the tolerance is one WORLD distance (3 % of the face width) converted into
 *   each axis's fraction space, so a 1U face — wide but short — still snaps
 *   vertically instead of needing a sub-millimetre approach;
 * - shifts at or below 1e-6 are ignored.
 *
 * It does not snap to the rack's edges or to the dragged server's own vents.
 * The returned shift is in fraction space; fold it in with `applyPatchSnapRect`.
 */
export function serverPatchSnapDelta(
  model: Construction | undefined,
  input: ServerPatchSnapInput,
): { dx: number; dy: number } {
  const thrX = 0.03
  const thrY = input.faceH > 1e-6 ? (0.03 * input.faceW) / input.faceH : 0.03
  const { rect } = input
  const left = rect.x, right = rect.x + rect.w, bottom = rect.y, top = rect.y + rect.h
  // A resize moves one edge only, so only that edge may be pulled.
  const usedX = input.isResize ? (input.edge === 'right' ? [right] : input.edge === 'left' ? [left] : []) : [left, right]
  const usedY = input.isResize ? (input.edge === 'top' ? [top] : input.edge === 'bottom' ? [bottom] : []) : [bottom, top]
  let bestX = thrX
  let bestY = thrY
  let dx = 0
  let dy = 0
  for (const [equipmentId, server] of Object.entries(serversOf(model))) {
    if (equipmentId === input.equipmentId) continue
    for (const face of Object.values(server.faces ?? {})) {
      for (const p of face.patches ?? []) {
        if (p.type !== 'rect') continue
        const ox = Number(p.x) || 0
        const oy = Number(p.y) || 0
        const theirX = [ox, ox + (Number(p.w) || 0.5)]
        const theirY = [oy, oy + (Number(p.h) || 0.5)]
        for (const me of usedX) {
          for (const t of theirX) {
            const d = Math.abs(me - t)
            if (d < bestX) {
              bestX = d
              dx = t - me
            }
          }
        }
        for (const me of usedY) {
          for (const t of theirY) {
            const d = Math.abs(me - t)
            if (d < bestY) {
              bestY = d
              dy = t - me
            }
          }
        }
      }
    }
  }
  return { dx: Math.abs(dx) > 1e-6 ? dx : 0, dy: Math.abs(dy) > 1e-6 ? dy : 0 }
}
