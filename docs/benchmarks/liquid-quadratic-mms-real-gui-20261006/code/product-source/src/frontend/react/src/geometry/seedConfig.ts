/**
 * Per-surface mesh seeds: the two points that tell snappyHexMesh which side of
 * a baffle is fluid.
 *
 * A wall-baffle or perforated surface keeps mesh on BOTH sides, so the mesher
 * needs a point in each half. The editor derives that pair automatically —
 * surface centre ± normal × offset — and when the automatic pair lands in the
 * wrong volume (inside a rack standing on a perforated tile, on the far side of
 * a containment panel) the mesh comes out with the wrong region and there is
 * nothing in the solver log that says why. So the pair is editable, and this
 * module is the maths behind that: where the default sits, what a stored
 * override looks like, and which surfaces have one at all.
 *
 * Ported from the seed half of the legacy editor:
 * `buildEditableSeedSurfaceContextFromMesh` (STL_generate.html:5085),
 * `normalizeSeedConfigForSurface` (:4980), `getStoredSeedConfigForSurface`
 * (:5012), `getSeedEndpoints` (:7278) and
 * `collectAllEditableSeedSurfaceContexts` (:5245).
 *
 * Everything here is pure — project in, numbers out. The legacy reads the
 * RENDERED mesh's centroid and normal when it has one and only falls back to
 * this arithmetic; the two agree for a patch (a flat rectangle's centroid is its
 * centre) and differ for a wall REGION with an opening cut out of it, where the
 * centroid shifts away from the face centre. That difference is why the seed
 * collision check downstream is a warning with an "ignore" button rather than a
 * hard stop.
 */

import { allModels, patchPhysicsMap, type CfdProject, type Construction, type Vec3 } from '../schema/cfdproj'
import { stlNaming } from '../legacy/stlNaming'

export interface SeedConfig {
  enabled: boolean
  anchor: Vec3
  direction: Vec3
  offset: number
}

/** Which of the four stores a surface's override is kept in. */
export type SeedOwner = 'floor-opening' | 'floor-default' | 'baffle-patch' | 'baffle-default'

export interface SeedSurface {
  owner: SeedOwner
  modelUid: string
  modelName: string
  faceId: string
  patchId: string | null
  wallIndex: number | null
  /** The STL solid this surface exports as — the key an override is filed under. */
  solidName: string
  label: string
  center: Vec3
  normal: Vec3
  localCellSize: number
  offsetMin: number
  defaultOffset: number
  defaultSeedConfig: SeedConfig
  /** The stored override, normalised — or the default when there is none. */
  seedConfig: SeedConfig
  isCustom: boolean
}

// ---------------------------------------------------------------------------
// Vector arithmetic, kept local so the collectors stay importable without three
// ---------------------------------------------------------------------------

function v3(x: number, y: number, z: number): Vec3 {
  return { x, y, z }
}

function add(a: Vec3, b: Vec3): Vec3 {
  return v3(a.x + b.x, a.y + b.y, a.z + b.z)
}

function scaled(a: Vec3, k: number): Vec3 {
  return v3(a.x * k, a.y * k, a.z * k)
}

function lengthSq(a: Vec3): number {
  return a.x * a.x + a.y * a.y + a.z * a.z
}

function normalize(a: Vec3): Vec3 {
  const len = Math.sqrt(lengthSq(a))
  return len > 1e-10 ? scaled(a, 1 / len) : v3(0, 1, 0)
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return v3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}

function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z
}

export function approxEqual(a: number | undefined, b: number | undefined, tol = 1e-6): boolean {
  return Math.abs((a || 0) - (b || 0)) <= tol
}

export function approxVecEqual(a: Vec3 | undefined, b: Vec3 | undefined, tol = 1e-6): boolean {
  return approxEqual(a?.x, b?.x, tol) && approxEqual(a?.y, b?.y, tol) && approxEqual(a?.z, b?.z, tol)
}

interface Quat {
  x: number
  y: number
  z: number
  w: number
}

/** Rotate `p` by `q`, the `applyQuaternion` expansion Three uses. */
function rotate(p: Vec3, q: Quat | undefined): Vec3 {
  if (!q) return p
  const { x, y, z } = p
  const ix = q.w * x + q.y * z - q.z * y
  const iy = q.w * y + q.z * x - q.x * z
  const iz = q.w * z + q.x * y - q.y * x
  const iw = -q.x * x - q.y * y - q.z * z
  return v3(
    ix * q.w + iw * -q.x + iy * -q.z - iz * -q.y,
    iy * q.w + iw * -q.y + iz * -q.x - ix * -q.z,
    iz * q.w + iw * -q.z + ix * -q.y - iy * -q.x,
  )
}

interface ModelGeometryBag {
  location?: Vec3
  size?: Vec3
  scale?: Vec3
  quaternion?: Quat
  faces?: Record<string, FaceBag> | FaceBag[]
  openings?: OpeningBag[]
}

interface FaceBag {
  id?: string
  patches?: PatchBag[]
  wallNames?: string[]
  displayName?: string
  label?: string
}

interface PatchBag {
  id?: string
  type?: string
  x?: number
  y?: number
  w?: number
  h?: number
  cx?: number
  cy?: number
}

interface OpeningBag {
  patchId?: string
  type?: string
  seedConfig?: SeedConfig
}

function geometryOf(model: Construction): ModelGeometryBag {
  return (model.geometry ?? {}) as ModelGeometryBag
}

function facesOf(model: Construction): Record<string, FaceBag> {
  const faces = geometryOf(model).faces
  if (!faces) return {}
  if (Array.isArray(faces)) {
    const out: Record<string, FaceBag> = {}
    for (const face of faces) if (face?.id) out[face.id] = face
    return out
  }
  return faces
}

/**
 * A face's origin corner and its two in-plane axes, in model-local space.
 *
 * Copied value for value from `getFaceLayouts` (js/geometry_pure.js:529). The
 * outward normal is `right × up`, which is what the STL writer uses — so a seed
 * pushed along `+normal` is on the side the exported solid faces.
 */
export function faceLayout(
  size: Vec3 | undefined,
  faceId: string,
): { origin: Vec3; right: Vec3; up: Vec3; w: number; h: number } | null {
  const W = Number(size?.x) || 0
  const H = Number(size?.y) || 0
  const D = Number(size?.z) || 0
  switch (faceId) {
    case 'front':
      return { origin: v3(W, 0, 0), right: v3(-1, 0, 0), up: v3(0, 1, 0), w: W, h: H }
    case 'back':
      return { origin: v3(0, 0, D), right: v3(1, 0, 0), up: v3(0, 1, 0), w: W, h: H }
    case 'left':
      return { origin: v3(0, 0, 0), right: v3(0, 0, 1), up: v3(0, 1, 0), w: D, h: H }
    case 'right':
      return { origin: v3(W, 0, D), right: v3(0, 0, -1), up: v3(0, 1, 0), w: D, h: H }
    case 'top':
      return { origin: v3(0, H, D), right: v3(1, 0, 0), up: v3(0, 0, -1), w: W, h: D }
    case 'bottom':
      return { origin: v3(0, 0, 0), right: v3(1, 0, 0), up: v3(0, 0, 1), w: W, h: D }
    default:
      return null
  }
}

/** Model-local point to world, through scale then rotation then location. */
export function modelToWorld(model: Construction, local: Vec3): Vec3 {
  const geom = geometryOf(model)
  const s = geom.scale ?? { x: 1, y: 1, z: 1 }
  const scaledLocal = v3(local.x * (s.x ?? 1), local.y * (s.y ?? 1), local.z * (s.z ?? 1))
  const loc = geom.location ?? { x: 0, y: 0, z: 0 }
  return add(rotate(scaledLocal, geom.quaternion), loc)
}

function patchCentre2D(patch: PatchBag): { u: number; v: number } {
  if (patch.type === 'circle') return { u: Number(patch.cx) || 0, v: Number(patch.cy) || 0 }
  return {
    u: (Number(patch.x) || 0) + (Number(patch.w) || 0) / 2,
    v: (Number(patch.y) || 0) + (Number(patch.h) || 0) / 2,
  }
}

/**
 * The seed refinement level, which sets how fine the local cells are.
 *
 * `wallBafflesRefinementLevel` is a `[min, max]` pair; the MAX is what the
 * baffle surface actually gets, so it is the level the offset has to clear.
 */
export function seedRefinementMaxLevel(project: CfdProject | null): number {
  const levels = (
    (project as { runDefaults?: { meshConfig?: { wallBafflesRefinementLevel?: unknown } } } | null)
      ?.runDefaults?.meshConfig ?? {}
  ).wallBafflesRefinementLevel
  if (Array.isArray(levels) && levels.length) {
    return Math.max(...levels.map((level) => parseInt(String(level), 10) || 0))
  }
  const single = parseInt(String(levels), 10)
  return Number.isFinite(single) ? single : 1
}

/** Cell size at the baffle surface: base size halved once per refinement level. */
export function seedLocalCellSize(project: CfdProject | null): number {
  const raw = (
    (project as { runDefaults?: { meshConfig?: { meshBaseSize?: unknown } } } | null)?.runDefaults
      ?.meshConfig ?? {}
  ).meshBaseSize
  const baseSize = Math.max(Number(raw) || 0.25, 1e-6)
  return baseSize / Math.pow(2, seedRefinementMaxLevel(project))
}

export function seedOffsetMin(localCellSize: number): number {
  return Math.max(0.25 * localCellSize, 1e-3)
}

function cloneSeedConfig(raw: unknown): SeedConfig | null {
  if (!raw || typeof raw !== 'object') return null
  const cfg = raw as Partial<SeedConfig>
  if (!cfg.anchor || !cfg.direction) return null
  return {
    enabled: cfg.enabled !== false,
    anchor: v3(Number(cfg.anchor.x) || 0, Number(cfg.anchor.y) || 0, Number(cfg.anchor.z) || 0),
    direction: v3(
      Number(cfg.direction.x) || 0,
      Number(cfg.direction.y) || 0,
      Number(cfg.direction.z) || 0,
    ),
    offset: Number(cfg.offset) || 0,
  }
}

/**
 * Pull a stored anchor back onto the surface it belongs to.
 *
 * An anchor that has drifted off the plane would put both seeds on the same
 * side once the offset is applied, which is the exact failure the pair exists to
 * prevent. Mirrors `projectPointToSeedSurfacePlane` (STL_generate.html:4960).
 */
function projectOntoPlane(centre: Vec3, normal: Vec3, point: Vec3): Vec3 {
  const n = normalize(normal)
  const distance = dot(v3(point.x - centre.x, point.y - centre.y, point.z - centre.z), n)
  return add(point, scaled(n, -distance))
}

/**
 * The same projection, against a surface — what a drag needs.
 *
 * Sliding the anchor means following the pointer ACROSS the surface, and a ray
 * cast at a plane comes back with a point that is on the plane only to floating
 * point. Exported so the drag and the normaliser cannot drift apart.
 */
export function projectOntoSurfacePlane(surface: SurfaceBase, point: Vec3): Vec3 {
  return projectOntoPlane(surface.center, surface.normal, point)
}

export interface SurfaceBase {
  center: Vec3
  normal: Vec3
  offsetMin: number
  defaultOffset: number
}

/** A stored config made safe: on the plane, unit direction, offset above the floor. */
export function normalizeSeedConfig(surface: SurfaceBase, raw: unknown): SeedConfig {
  const offsetMin = Math.max(surface.offsetMin || 0, 1e-6)
  const next = cloneSeedConfig(raw) ?? {
    enabled: true,
    anchor: surface.center,
    direction: surface.normal,
    offset: Math.max(surface.defaultOffset || 0, offsetMin),
  }
  const direction = lengthSq(next.direction) > 1e-10 ? normalize(next.direction) : normalize(surface.normal)
  return {
    enabled: next.enabled !== false,
    anchor: projectOntoPlane(surface.center, surface.normal, next.anchor),
    direction,
    offset: Math.max(Number(next.offset) || 0, offsetMin),
  }
}

export function isSeedConfigCustom(
  surface: SurfaceBase & { defaultSeedConfig: SeedConfig },
  seedConfig: SeedConfig,
): boolean {
  const current = normalizeSeedConfig(surface, seedConfig)
  const def = normalizeSeedConfig(surface, surface.defaultSeedConfig)
  return (
    !approxVecEqual(current.anchor, def.anchor) ||
    !approxVecEqual(current.direction, def.direction) ||
    !approxEqual(current.offset, def.offset)
  )
}

/** The two points the mesher is handed, one per side of the surface. */
export function seedEndpoints(cfg: SeedConfig): { anchor: Vec3; positive: Vec3; negative: Vec3 } {
  const direction = normalize(cfg.direction)
  return {
    anchor: cfg.anchor,
    positive: add(cfg.anchor, scaled(direction, cfg.offset)),
    negative: add(cfg.anchor, scaled(direction, -cfg.offset)),
  }
}

// ---------------------------------------------------------------------------
// Where an override is stored
// ---------------------------------------------------------------------------

interface CoolingBag {
  type?: string
  defaultBoundaryType?: string
  seedConfig?: unknown
  defaultSeedConfig?: unknown
  defaultSeedConfigs?: Record<string, unknown>
  wallRegionSeedConfigs?: Record<string, unknown>
}

function coolingOf(model: Construction): CoolingBag {
  return (model.cooling ?? {}) as CoolingBag
}

/**
 * The override a surface currently carries, or null when it is on its default.
 *
 * Four stores, because the legacy grew one per surface kind and the .cfdproj
 * files in this repo carry all four shapes. Reading only some of them would
 * make an override that IS in the file look like a default in the panel — and
 * the panel's reset button would then have nothing to clear.
 */
export function storedSeedConfig(model: Construction, surface: {
  owner: SeedOwner
  patchId: string | null
  solidName: string
}): SeedConfig | null {
  const cooling = coolingOf(model)
  if (surface.owner === 'floor-opening') {
    const opening = (geometryOf(model).openings ?? []).find((o) => o?.patchId === surface.patchId)
    return cloneSeedConfig(opening?.seedConfig)
  }
  if (surface.owner === 'floor-default') {
    const perRegion = cooling.defaultSeedConfigs ?? {}
    return cloneSeedConfig(perRegion[surface.solidName] ?? cooling.defaultSeedConfig)
  }
  if (surface.owner === 'baffle-patch') {
    const physics = surface.patchId ? patchPhysicsMap(model)[surface.patchId] : undefined
    return cloneSeedConfig((physics as { seedConfig?: unknown } | undefined)?.seedConfig)
  }
  const perRegion = cooling.wallRegionSeedConfigs ?? {}
  return cloneSeedConfig(perRegion[surface.solidName] ?? cooling.seedConfig)
}

// ---------------------------------------------------------------------------
// Enumerating the surfaces that have a seed at all
// ---------------------------------------------------------------------------

function buildSurface(
  project: CfdProject,
  model: Construction,
  owner: SeedOwner,
  faceId: string,
  opts: { patchId?: string | null; wallIndex?: number | null; localCentre: Vec3; solidName: string },
): SeedSurface {
  const layout = faceLayout(geometryOf(model).size, faceId)
  const localNormal = layout ? cross(layout.right, layout.up) : v3(0, 1, 0)
  const centre = modelToWorld(model, opts.localCentre)
  // The normal is a DIRECTION: rotated with the model but never translated, and
  // never scaled — a mirrored scale would otherwise flip which half of the
  // surface the positive seed lands in.
  const normal = normalize(rotate(localNormal, geometryOf(model).quaternion))

  const localCellSize = seedLocalCellSize(project)
  const offsetMin = seedOffsetMin(localCellSize)
  const defaultSeedConfig: SeedConfig = {
    enabled: true,
    anchor: centre,
    direction: normal,
    offset: Math.max(0.5 * localCellSize, offsetMin),
  }
  const base: SurfaceBase & { defaultSeedConfig: SeedConfig } = {
    center: centre,
    normal,
    offsetMin,
    defaultOffset: defaultSeedConfig.offset,
    defaultSeedConfig,
  }
  const stored = storedSeedConfig(model, {
    owner,
    patchId: opts.patchId ?? null,
    solidName: opts.solidName,
  })
  const seedConfig = normalizeSeedConfig(base, stored ?? defaultSeedConfig)
  return {
    owner,
    modelUid: model.uid,
    modelName: model.meta?.name ?? model.uid,
    faceId,
    patchId: opts.patchId ?? null,
    wallIndex: opts.wallIndex ?? null,
    solidName: opts.solidName,
    label: opts.solidName,
    center: centre,
    normal,
    localCellSize,
    offsetMin,
    defaultOffset: defaultSeedConfig.offset,
    defaultSeedConfig,
    seedConfig,
    isCustom: !!stored && isSeedConfigCustom(base, seedConfig),
  }
}

const FLOOR_SEED_TYPES = ['wallBaffle', 'perforated']
const BAFFLE_SEED_TYPES = ['wall', 'perforated']

function kindOf(model: Construction, bucket: string): string {
  const explicit = (model as { kind?: string }).kind
  if (explicit) return explicit
  if (bucket === 'floors') return 'floor'
  if (bucket === 'baffles') return 'baffle'
  return bucket
}

function exportName(model: Construction, faceId: string, patch: PatchBag): string {
  try {
    return stlNaming().getPatchFinalExportName(model, faceId, patch as { id: string })
  } catch {
    return `${model.meta?.name ?? model.uid}_${patch.id ?? 'patch'}`
  }
}

function wallRegionExportName(model: Construction, faceId: string, wallIndex: number): string {
  const face = facesOf(model)[faceId] ?? {}
  const partCount = Math.max(1, (face.wallNames ?? []).length)
  try {
    return stlNaming().composeModelScopedExportName(model, faceId, face, {
      isWallPart: true,
      partIndex: wallIndex,
      partCount,
    })
  } catch {
    return `${model.meta?.name ?? model.uid}_part${wallIndex + 1}`
  }
}

/**
 * The surface a patch selection can edit a seed for, or null.
 *
 * Only wall-baffle and perforated surfaces have one. A plain `wall` floor tile
 * or an ACU opening is a single-sided patch — there is no second volume to seed
 * — so offering the controls there would invite a user to set a number the
 * mesher never reads.
 */
export function seedSurfaceForPatch(
  project: CfdProject | null,
  model: Construction | null,
  bucket: string,
  faceId: string,
  patchId: string,
): SeedSurface | null {
  if (!project || !model) return null
  const kind = kindOf(model, bucket)
  const face = facesOf(model)[faceId]
  const patch = (face?.patches ?? []).find((p) => p?.id === patchId)
  if (!patch) return null
  const layout = faceLayout(geometryOf(model).size, faceId)
  if (!layout) return null
  const { u, v } = patchCentre2D(patch)
  const localCentre = add(add(layout.origin, scaled(layout.right, u)), scaled(layout.up, v))

  if (kind === 'floor') {
    const opening = (geometryOf(model).openings ?? []).find((o) => o?.patchId === patchId)
    if (!FLOOR_SEED_TYPES.includes(String(opening?.type ?? 'wall'))) return null
    return buildSurface(project, model, 'floor-opening', faceId, {
      patchId,
      localCentre,
      solidName: exportName(model, faceId, patch),
    })
  }
  if (kind === 'baffle') {
    const physics = patchPhysicsMap(model)[patchId] as { baffleType?: string } | undefined
    const baffleType = physics?.baffleType || coolingOf(model).type || 'wall'
    if (!BAFFLE_SEED_TYPES.includes(String(baffleType))) return null
    return buildSurface(project, model, 'baffle-patch', faceId, {
      patchId,
      localCentre,
      solidName: exportName(model, faceId, patch),
    })
  }
  return null
}

/**
 * The surface a wall-REGION selection edits: the part of the face left over
 * once the patches are cut out of it.
 *
 * The centre used here is the face's, not the remaining region's centroid — see
 * the module comment. `getSingleEditableDefaultSeedSurface` (:5228) picks the
 * FIRST region for a whole-model selection, which is what `wallIndex = 0` is.
 */
export function seedSurfaceForWallRegion(
  project: CfdProject | null,
  model: Construction | null,
  bucket: string,
  faceId: string,
  wallIndex: number,
): SeedSurface | null {
  if (!project || !model) return null
  const kind = kindOf(model, bucket)
  const layout = faceLayout(geometryOf(model).size, faceId)
  if (!layout) return null
  const cooling = coolingOf(model)
  if (kind === 'floor') {
    if (!FLOOR_SEED_TYPES.includes(String(cooling.defaultBoundaryType || 'wall'))) return null
  } else if (kind === 'baffle') {
    if (!BAFFLE_SEED_TYPES.includes(String(cooling.type || 'wall'))) return null
  } else {
    return null
  }
  const localCentre = add(
    add(layout.origin, scaled(layout.right, layout.w / 2)),
    scaled(layout.up, layout.h / 2),
  )
  return buildSurface(project, model, kind === 'floor' ? 'floor-default' : 'baffle-default', faceId, {
    wallIndex,
    localCentre,
    solidName: wallRegionExportName(model, faceId, wallIndex),
  })
}

/** The default surface a whole-model selection edits, for a floor or a baffle. */
export function seedSurfaceForModel(
  project: CfdProject | null,
  model: Construction | null,
  bucket: string,
): SeedSurface | null {
  const kind = model ? kindOf(model, bucket) : ''
  if (kind !== 'floor' && kind !== 'baffle') return null
  return seedSurfaceForWallRegion(project, model, bucket, kind === 'floor' ? 'top' : 'front', 0)
}

/**
 * Every surface in the project that carries a seed.
 *
 * The order is bucket order then insertion order, and duplicates are dropped on
 * `owner:model:solid` exactly as the legacy does — one solid gets one seed pair
 * however many render meshes happen to reference it.
 */
export function collectSeedSurfaces(project: CfdProject | null): SeedSurface[] {
  if (!project) return []
  const out: SeedSurface[] = []
  const seen = new Set<string>()
  const push = (surface: SeedSurface | null) => {
    if (!surface) return
    const key = `${surface.owner}:${surface.modelUid}:${surface.solidName}`
    if (seen.has(key)) return
    seen.add(key)
    out.push(surface)
  }

  for (const { bucket, model } of allModels(project)) {
    const kind = kindOf(model, bucket)
    if (kind === 'floor') {
      for (const opening of geometryOf(model).openings ?? []) {
        if (opening?.patchId) push(seedSurfaceForPatch(project, model, bucket, 'top', opening.patchId))
      }
      push(seedSurfaceForWallRegion(project, model, bucket, 'top', 0))
    } else if (kind === 'baffle') {
      for (const [faceId, face] of Object.entries(facesOf(model))) {
        for (const patch of face.patches ?? []) {
          if (patch?.id) push(seedSurfaceForPatch(project, model, bucket, faceId, patch.id))
        }
      }
      push(seedSurfaceForWallRegion(project, model, bucket, 'front', 0))
    }
  }
  return out
}
