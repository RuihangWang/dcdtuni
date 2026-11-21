/**
 * Which of several overlapping hits a click actually meant.
 *
 * A rack's front face stacks four things within a millimetre of each other: the
 * panel, the slot guide over it, a server's face behind that, and a patch drawn
 * on the server. Taking the literally-nearest hit picks whichever the renderer
 * happened to offset toward the camera, which is not what the user aimed at —
 * a patch is always a more specific target than the wall it is drawn on.
 *
 * So the rule is the legacy's `pickNearestHit` (STL_generate.html:2498):
 * consider every hit within a COPLANAR WINDOW of the nearest and belonging to
 * the same model, then take the one with the highest PRIORITY, distance
 * breaking ties. The window is 0.5% of the distance with a 5 mm floor, which is
 * the scale of the offsets the builders use to avoid z-fighting.
 */

import type { TreeSelection } from './treeNodes'

export interface HitLike {
  distance: number
  object: { userData: Record<string, unknown> }
}

/**
 * How specific a hit is. Higher wins inside the coplanar window.
 *
 * Copied from `getHitPriority` (STL_generate.html:2442) — the ORDER is the
 * behaviour: a patch beats a slot guide, a slot guide beats the server behind
 * it, and everything beats the base wall.
 */
export function hitPriority(data: Record<string, unknown>): number {
  if (data.isSeedHandle) return 120
  if (data.isVectorHandle) return 100
  if (data.isCustomPatchBody) return 95
  if (Array.isArray(data.refIds) && data.refIds.length) return 90
  if (data.isRackSlot) return 85
  if (data.isRackEquipment) return 80
  if (data.isImportedRegion || data.isPreviewRegion) return 50
  if (data.isBaseWall) return 40
  if (data.isImportedBase) return 30
  if (data.isProjectionGuide) return 20
  return 0
}

/**
 * Whether a raycaster hit can become the selection at all: a visible MESH that
 * names its model.
 *
 * Mesh-only, as the legacy is — it raycasts `renderMeshes`, which holds nothing
 * else. The shared rack builder also draws U-slot boundary guides as
 * `THREE.Line`s tagged with the rack's `modelId` (geometry_scene.js), and a
 * Line is hit anywhere within `Raycaster.params.Line.threshold`, one world unit
 * by default. A scene-wide raycast therefore reported a guide IN FRONT of the
 * inlet face it sits on; `pickNearestHit` restricted the candidates to that
 * Line's model and kept the Line, so a click on a server's front selected the
 * neighbouring rack (room view) or no server face at all (rack detail).
 *
 * Every other object under the scene that carries a `modelId` is a Mesh:
 * faces, slot guides, servers, patches, vector-handle tips, seed handles. The
 * edge `LineSegments` and an ArrowHelper's shaft carry no `modelId` and were
 * already dropped. InstancedMesh counts as a mesh.
 */
interface SelectableObject {
  visible?: boolean
  isMesh?: boolean
  userData: Record<string, unknown>
  parent?: { visible?: boolean; parent?: SelectableObject['parent'] } | null
}

export function isSelectableHit(hit: { object: SelectableObject }): boolean {
  const o = hit.object
  if (o.visible !== true || o.isMesh !== true || o.userData.selectable === false ||
      !(o.userData.modelId || o.userData.uid)) return false
  // Raycaster visits children even when a group is hidden. A hidden cabinet
  // must not intercept a click on the visible equipment behind it.
  for (let parent = o.parent; parent; parent = parent.parent) {
    if (parent.visible === false) return false
  }
  return true
}

/**
 * Whether a click in the 3D view may change the GEOMETRY selection.
 *
 * Only on the geometry tab, and never while a mesh or solve has the UI
 * frozen. The legacy's ordinary click is gated exactly so
 * (`endViewportPointerState`, STL_generate.html:18145-18156), and its
 * first-person crosshair likewise (:29653). On the other tabs the panel is not
 * the geometry editor, so a selection made there is one the user cannot see —
 * and on the mesh and post-processing tabs it was worse than invisible:
 * selecting a rack rebuilds it for its slot guides, and the rebuilt rack sat
 * over the mesh patches at full opacity. The patch lists pick through their own
 * listener (`geometry/patchViewportPick.ts`) and are not affected.
 */
export function canSelectGeometryFromViewport(activeTab: string | undefined, frozen: boolean): boolean {
  return !frozen && activeTab === 'geometry'
}

/** The coplanar window around the nearest hit. */
export function coplanarWindow(distance: number): number {
  return Math.max(0.005, distance * 0.001)
}

/**
 * Pick from raycaster hits, which arrive sorted by distance.
 *
 * Candidates are restricted to the nearest hit's OWN model: a patch on a rack
 * two metres behind must never win over the wall in front of it, however
 * specific it is.
 */
export function pickNearestHit<T extends HitLike>(hits: T[]): T | null {
  if (!hits.length) return null
  const nearest = hits[0]!
  const limit = nearest.distance + coplanarWindow(nearest.distance)
  const modelId = nearest.object.userData.modelId
  const candidates = hits.filter(
    (h) => h.distance <= limit && h.object.userData.modelId === modelId,
  )
  let best: T | null = null
  for (const current of candidates) {
    if (!best) {
      best = current
      continue
    }
    const bp = hitPriority(best.object.userData)
    const cp = hitPriority(current.object.userData)
    if (cp > bp || (cp === bp && current.distance < best.distance)) best = current
  }
  return best
}

/**
 * What a hit on a NON-RACK model means, beyond "this model".
 *
 * The rack branches stay in the viewport: resolving a U-slot needs the hit
 * POINT and the rack's face layout, neither of which is a property of the
 * userData. Everything else is, and it is the same table the legacy viewport
 * walks (STL_generate.html:17803-17893):
 *
 *   refIds            a patch — the boundary condition itself
 *   isImportedRegion  one solid of an imported STL
 *   isImportedBase    the imported mesh with no named solid: the whole model
 *   wallIndex         a piece of a face that patches have SPLIT, but only once
 *                     the face actually carries a patch — an unsplit face has
 *                     exactly one region, and calling it "region 1" would offer
 *                     a rename for something that is just the face
 *   otherwise         the face
 *
 * Returning `null` means "select the whole model", which the viewport does at
 * the end of its own chain. A planar body (wall, baffle, floor, partition
 * wall) resolves to its FACE, so the inspector can name it; the legacy
 * selected the model there and left the user guessing which side was hit.
 */
export interface ViewportPickContext {
  /** The model's kind, for the planar-body rule. */
  kind: string | null
  /** How many patches the given face carries. */
  facePatchCount: (faceId: string) => number
}

const PLANAR_BODY_KINDS = new Set(['baffle', 'wall', 'floor', 'partitionWall'])

export function resolveViewportSelection(
  uid: string,
  data: Record<string, unknown>,
  ctx: ViewportPickContext,
): TreeSelection | null {
  const faceId = typeof data.faceId === 'string' ? data.faceId : undefined
  const regionName = typeof data.regionName === 'string' ? data.regionName : undefined

  const refIds = Array.isArray(data.refIds) ? (data.refIds as unknown[]) : null
  if (refIds && refIds.length && typeof refIds[0] === 'string' && faceId) {
    return {
      type: 'patch',
      uid,
      faceId,
      patchId: refIds[0],
      ...(regionName ? { regionName } : {}),
    }
  }

  if (data.isImportedRegion && regionName) return { type: 'region', uid, regionName }

  if (data.isBaseWall || data.isProjectionGuide) {
    const wallIndex = Number.isInteger(data.wallIndex) ? (data.wallIndex as number) : null
    if (data.isBaseWall && wallIndex !== null && faceId && ctx.facePatchCount(faceId) > 0) {
      return { type: 'wallRegion', uid, faceId, index: wallIndex }
    }
    // A wall, baffle, floor or partition wall body used to resolve to the
    // model, on the grounds that its patches hang straight off it in the tree.
    // But then a click on the sheet said nothing about WHICH face was hit, and
    // the inspector had no face to name. The face is selected now; the
    // inspector shows the model's panel under a banner naming the face.
    if (!faceId) return null
    // A pipe is one swept solid: its body is the model, not a face of it.
    if (ctx.kind === 'pipe') return null
    if (ctx.kind && PLANAR_BODY_KINDS.has(ctx.kind)) return { type: 'face', uid, faceId }
    if (wallIndex !== null && ctx.facePatchCount(faceId) > 0) {
      return { type: 'wallRegion', uid, faceId, index: wallIndex }
    }
    return { type: 'face', uid, faceId }
  }

  // The imported mesh drawn as one piece: the legacy selects the WHOLE-model
  // region, which is the row its patches are added from.
  if (data.isImportedBase) return { type: 'region', uid }

  return null
}

/**
 * The same resolution, from the MODEL rather than a hand-built context.
 *
 * The viewport has the model in its registry and nothing else, so deriving the
 * kind and the face's patch count belongs here rather than being spelled out at
 * the call site — the `faces` map is an ARRAY on a rack and an object on
 * everything else, and getting that wrong yields a patch count of zero and a
 * face selection where a wall region was meant.
 */
export function viewportSelectionFor(
  uid: string,
  userData: Record<string, unknown>,
  model: { kind?: unknown; geometry?: unknown } | undefined,
): TreeSelection | null {
  const geometry = model?.geometry as { faces?: unknown } | undefined
  const faces = geometry?.faces
  const byName =
    faces && typeof faces === 'object' && !Array.isArray(faces)
      ? (faces as Record<string, { patches?: unknown }>)
      : null
  return resolveViewportSelection(uid, userData, {
    kind: model?.kind == null ? null : String(model.kind),
    facePatchCount: (faceId) => {
      const patches = byName?.[faceId]?.patches
      return Array.isArray(patches) ? patches.length : 0
    },
  })
}
