/**
 * Where a patch sits on a face, and what keeps it there.
 *
 * A patch is the only way a boundary condition gets onto a surface: an ACU's
 * supply and return openings, a perforated floor tile, a leakage hole in a wall,
 * an opening in a containment baffle. The record itself is small, but every
 * number in it is read by the STL exporter and then by OpenFOAM, so the maths
 * that places and clamps it is worth having on its own, testable, away from
 * React and away from Three.js.
 *
 * Ported from the patch half of the legacy editor: `addPatch`
 * (STL_generate.html:14508), `enforceSafety` (:6724), `autoFill` (:14634) and
 * `ensurePatchProjectionDefaults` / `getDefaultPatchVector`
 * (js/geometry_pure.js:602 / :585).
 */

import type { CfdProject } from '../schema/cfdproj'

export type PatchType = 'circle' | 'rect'

export interface PatchRecord {
  id: string
  type: PatchType
  name: string
  color: string
  /** Circle: radius and centre, in face coordinates. */
  r?: number
  cx?: number
  cy?: number
  /** Rectangle: bottom-left corner and extent, in face coordinates. */
  x?: number
  y?: number
  w?: number
  h?: number
  /** Custom-STL patches only: the beam that is projected through the mesh. */
  depth?: number
  regionName?: string | null
  applied?: boolean
  showVector?: boolean
  vx?: number
  vy?: number
  vz?: number
  [key: string]: unknown
}

export interface FaceRecord {
  /** Which of the model's W/H/D extents runs along the face's own x and y. */
  wType?: string
  hType?: string
  patches?: PatchRecord[]
  [key: string]: unknown
}

export interface Vec3Like {
  x: number
  y: number
  z: number
}

/**
 * The colours a new patch is given, in the legacy's order
 * (STL_generate.html:1380).
 *
 * The list and its ORDER are load-bearing for parity: a patch's colour comes
 * from its index within the face, so a different order gives a React-created
 * ACU return a different colour from a legacy-created one in the same project.
 */
export const PATCH_PALETTE = [
  '#e06c75',
  '#61afef',
  '#e5c07b',
  '#98c379',
  '#c678dd',
  '#56b6c2',
]

/** Mirrors FACE_CONFIGS (js/rack_state.js:79) — the fallback when a face record predates it. */
export const FACE_AXES: Record<string, { wType: string; hType: string }> = {
  front: { wType: 'W', hType: 'H' },
  back: { wType: 'W', hType: 'H' },
  left: { wType: 'D', hType: 'H' },
  right: { wType: 'D', hType: 'H' },
  top: { wType: 'W', hType: 'D' },
  bottom: { wType: 'W', hType: 'D' },
}

interface SizedModel {
  kind?: string
  geometry?: { size?: Partial<Vec3Like>; faces?: unknown }
  [key: string]: unknown
}

/**
 * The model's extents in the W/H/D names the face records use.
 *
 * V4 stores them as `geometry.size.{x,y,z}`; the face records still say W/H/D,
 * which is what `_baseWHD` (STL_generate.html:1531) exists to bridge.
 */
export function baseWHD(model: SizedModel): Record<string, number> {
  const size = model?.geometry?.size ?? {}
  return { W: Number(size.x) || 0, H: Number(size.y) || 0, D: Number(size.z) || 0 }
}

/** How wide and how tall the face is, in its own 2D coordinates. */
export function faceExtent(
  model: SizedModel,
  faceId: string,
  face: FaceRecord | undefined,
): { w: number; h: number } {
  const base = baseWHD(model)
  const axes = FACE_AXES[faceId] ?? { wType: 'W', hType: 'H' }
  const wType = typeof face?.wType === 'string' ? face.wType : axes.wType
  const hType = typeof face?.hType === 'string' ? face.hType : axes.hType
  return { w: base[wType] ?? 0, h: base[hType] ?? 0 }
}

/**
 * The direction a patch is projected along, per face.
 *
 * INWARD normals under the Y-up convention (js/geometry_pure.js:585): a patch
 * cuts INTO the body it sits on, so pointing outward would project the beam
 * away from the mesh and cut nothing.
 */
export const DEFAULT_PATCH_VECTORS: Record<string, Vec3Like> = {
  front: { x: 0, y: 0, z: 1 },
  back: { x: 0, y: 0, z: -1 },
  left: { x: 1, y: 0, z: 0 },
  right: { x: -1, y: 0, z: 0 },
  top: { x: 0, y: -1, z: 0 },
  bottom: { x: 0, y: 1, z: 0 },
}

export function defaultPatchVector(faceId: string): Vec3Like {
  const v = DEFAULT_PATCH_VECTORS[faceId] ?? DEFAULT_PATCH_VECTORS.front!
  return { ...v }
}

/**
 * Give a patch a projection vector if it has none.
 *
 * The legacy runs this on every patch inspector RENDER, whatever the model kind
 * — which is why patches in saved projects all carry `showVector`/`vx/vy/vz`
 * even on box faces that never show the arrow. Applied at creation here instead,
 * so a React-created patch has the same fields, in the same order, as a
 * legacy-created one that has been looked at once.
 */
export function applyProjectionDefaults(faceId: string, patch: PatchRecord): void {
  if (typeof patch.showVector !== 'boolean') patch.showVector = true
  if (![patch.vx, patch.vy, patch.vz].every((v) => Number.isFinite(v))) {
    const v = defaultPatchVector(faceId)
    patch.vx = v.x
    patch.vy = v.y
    patch.vz = v.z
  }
}

/**
 * A new parametric patch, half the face wide and centred.
 *
 * Key insertion order matters: the .cfdproj is compared byte for byte against
 * the legacy app's output, and `JSON.stringify` writes keys in insertion order.
 */
export function createPatchRecord(opts: {
  model: SizedModel
  faceId: string
  face: FaceRecord | undefined
  type: PatchType
  /** The `p_<seq>` number, which also names the patch. */
  seq: number
  /** How many patches the face already holds — the colour comes from it. */
  colorIndex: number
}): PatchRecord {
  const { model, faceId, face, type, seq, colorIndex } = opts
  const patch: PatchRecord = {
    id: `p_${seq}`,
    type,
    name: type === 'circle' ? `Circle_Patch_${seq}` : `Rect_Patch_${seq}`,
    color: PATCH_PALETTE[colorIndex % PATCH_PALETTE.length] as string,
  }
  const { w, h } = faceExtent(model, faceId, face)
  if (type === 'circle') {
    patch.r = 0.15
    patch.cx = w / 2
    patch.cy = h / 2
  } else {
    patch.x = 0
    patch.y = 0
    patch.w = w / 2
    patch.h = h / 2
  }
  return patch
}

/**
 * Pull a patch back inside its face, and say whether it had to move.
 *
 * A patch that hangs over the edge is not a modelling choice, it is a broken
 * solid: the exporter would write a boundary face that is not on the surface it
 * claims to be on, and snappyHexMesh has nothing to snap it to. Circles keep a
 * 10% margin on each side because a hole touching the edge leaves a knife-edge
 * sliver of wall the mesher cannot resolve.
 *
 * Returns true when something was clamped, which is what raises the safety
 * alert — silently moving the user's number is worse than saying so.
 */
export function enforcePatchSafety(
  model: SizedModel,
  faceId: string,
  face: FaceRecord | undefined,
  patch: PatchRecord,
): boolean {
  const { w, h } = faceExtent(model, faceId, face)
  let alert = false

  if (patch.type === 'circle') {
    const mx = w * 0.1
    const my = h * 0.1
    let maxR = Math.min(w / 2 - mx, h / 2 - my)
    if (maxR < 0.01) maxR = 0.01
    if ((patch.r as number) > maxR) {
      patch.r = maxR
      alert = true
    }
    if ((patch.r as number) < 0.01) patch.r = 0.01
    const r = patch.r as number
    if ((patch.cx as number) - r < mx) {
      patch.cx = mx + r
      alert = true
    }
    if ((patch.cx as number) + r > w - mx) {
      patch.cx = w - mx - r
      alert = true
    }
    if ((patch.cy as number) - r < my) {
      patch.cy = my + r
      alert = true
    }
    if ((patch.cy as number) + r > h - my) {
      patch.cy = h - my - r
      alert = true
    }
    return alert
  }

  if ((patch.w as number) > w) {
    patch.w = w
    alert = true
  }
  if ((patch.h as number) > h) {
    patch.h = h
    alert = true
  }
  if ((patch.w as number) < 0.01) patch.w = 0.01
  if ((patch.h as number) < 0.01) patch.h = 0.01
  if ((patch.x as number) < 0) {
    patch.x = 0
    alert = true
  }
  if ((patch.x as number) + (patch.w as number) > w) {
    patch.x = w - (patch.w as number)
    alert = true
  }
  if ((patch.y as number) < 0) {
    patch.y = 0
    alert = true
  }
  if ((patch.y as number) + (patch.h as number) > h) {
    patch.y = h - (patch.h as number)
    alert = true
  }
  return alert
}

/**
 * Clamp a custom-STL patch, whose numbers mean something else.
 *
 * There is no face rectangle to stay inside — the patch is a beam projected
 * through an imported mesh — so the only rule is that none of its dimensions may
 * collapse to zero. Mirrors the `kind === 'custom'` branch of `enforceSafety`
 * (STL_generate.html:6725).
 */
export function enforceCustomPatchSafety(patch: PatchRecord): void {
  if (patch.type === 'circle' && (!Number.isFinite(patch.r) || (patch.r as number) < 0.01)) {
    patch.r = 0.01
  }
  if (patch.type === 'rect') {
    if (!Number.isFinite(patch.w) || (patch.w as number) < 0.01) patch.w = 0.01
    if (!Number.isFinite(patch.h) || (patch.h as number) < 0.01) patch.h = 0.01
  }
  if (!Number.isFinite(patch.depth) || (patch.depth as number) < 0.01) patch.depth = 0.01
}

/**
 * Stretch a rectangular patch across the whole face in one direction.
 *
 * This is how a raised-floor tile row or a full-width ACU return gets built:
 * the interesting dimension is set by hand and the other one is made to reach
 * both edges exactly, which typing it never does.
 */
export function autoFillRect(
  model: SizedModel,
  faceId: string,
  face: FaceRecord | undefined,
  patch: PatchRecord,
  direction: 'H' | 'V',
): void {
  const { w, h } = faceExtent(model, faceId, face)
  if (direction === 'H') {
    patch.x = 0
    patch.w = w
  }
  if (direction === 'V') {
    patch.y = 0
    patch.h = h
  }
}

/** A 32-segment ring, as a polygon-clipping MultiPolygon (js/geometry_pure.js:336). */
export function circleToPoly(cx: number, cy: number, r: number): number[][][][] {
  const ring: number[][] = []
  for (let i = 0; i < 32; i++) {
    ring.push([cx + r * Math.cos(((i / 32) * Math.PI) * 2), cy + r * Math.sin(((i / 32) * Math.PI) * 2)])
  }
  ring.push([ring[0]![0] as number, ring[0]![1] as number])
  return [[ring]]
}

/** A closed rectangle ring, as a polygon-clipping MultiPolygon (js/geometry_pure.js:344). */
export function rectToPoly(x: number, y: number, w: number, h: number): number[][][][] {
  return [
    [
      [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h],
        [x, y],
      ],
    ],
  ]
}

/**
 * Next free `p_<n>`, scanned across every patch already in the project.
 *
 * Derived rather than counted: a counter starts at zero on every page load and
 * would hand out ids that collide with the ones in the file just opened, which
 * shows up as two patches sharing a boundary condition.
 */
export function nextPatchSeq(project: CfdProject | null): number {
  let max = 0
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit)
      return
    }
    if (!value || typeof value !== 'object') return
    const record = value as Record<string, unknown>
    if (typeof record.id === 'string') {
      const m = /^p_(\d+)$/.exec(record.id)
      if (m) max = Math.max(max, Number(m[1]))
    }
    Object.values(record).forEach(visit)
  }
  visit(project?.constructions ?? {})
  return max + 1
}

/**
 * Which face of a partition wall a patch may actually be added to.
 *
 * A partition wall is a THIN plate and its holes go right through it, so both
 * visible faces describe the same hole. The legacy keeps one store — the front
 * face — and mirrors it (STL_generate.html:5610); returning null for the edge
 * faces is what stops a hole being punched into a 50 mm edge.
 */
export function partitionWallEditableFaceId(faceId: string): string | null {
  return faceId === 'front' || faceId === 'back' ? 'front' : null
}

/**
 * The face an imported region's patch should hang on, from the region's normal.
 *
 * Mirrors `getDominantFaceFromNormal` (STL_generate.html:6162). The axis with
 * the largest component wins, and Z is tested first so a 45-degree face lands on
 * top/bottom rather than flapping between two answers.
 */
export function dominantFaceFromNormal(normal: Vec3Like): string {
  const ax = Math.abs(normal.x)
  const ay = Math.abs(normal.y)
  const az = Math.abs(normal.z)
  if (az >= ax && az >= ay) return normal.z >= 0 ? 'top' : 'bottom'
  if (ax >= ay) return normal.x >= 0 ? 'right' : 'left'
  return normal.y >= 0 ? 'back' : 'front'
}

/**
 * How big a custom-STL patch starts out, from the region it is mounted on.
 *
 * Proportional to the region rather than fixed, because an imported model can be
 * a 40 mm connector or a 40 m hall — a 0.15 m default would be invisible on one
 * and would swallow the other. Mirrors `getCustomPatchDefaultSize`
 * (STL_generate.html:6120).
 */
export function customPatchDefaultSize(size: Vec3Like): {
  radius: number
  width: number
  height: number
  depth: number
} {
  const maxDim = Math.max(size.x, size.y, size.z, 0.1)
  return {
    radius: Math.max(Math.min(maxDim * 0.12, maxDim * 0.4), 0.04),
    width: Math.max(size.x * 0.28, 0.08),
    height: Math.max(size.z * 0.28, 0.08),
    depth: Math.max(maxDim * 0.35, 0.08),
  }
}
