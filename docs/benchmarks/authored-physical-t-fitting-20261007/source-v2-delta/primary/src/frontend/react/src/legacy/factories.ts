import {createAuthoredTBranch} from '../liquid/tBranch'
import { type Bucket, type CfdProject, type Construction, modelsIn } from '../schema/cfdproj'
import { bucketForKind, finalizeNewModel, rackState, type V3Seed } from './cfdprojModules'

/**
 * Default-model factories, ported from the legacy editor's `create*Model`.
 *
 * These build a v3-shaped SEED and hand it to `finalizeNewModelV4`, which runs
 * the same v3->v4 migrator the load path runs. That indirection is the point:
 * a model created here is byte-identical to one created by the legacy app, and
 * to one that came out of a file — verified by diffing both apps' output rather
 * than by reading the migrator and hoping.
 *
 * Conformance was checked by creating one of each kind in both apps and diffing
 * the result: box, ACU, wall, partition wall, baffle and floor come out
 * identical, key order included. The one field that still differs is
 * `wallOpacities`, and it differs because the legacy app had RENDERED the model
 * by the time it was read — the legacy factory writes `[]` too
 * (STL_generate.html:2811) and `ensureWallOpacity` fills index 0 on first draw
 * (:5503). Which faces get a wall region is a renderer rule that varies by kind
 * (all six for a box, only `front` for a wall, only `top` for a floor, none for
 * a partition wall), so it belongs with the renderer, not here. A file saved
 * with `[]` is valid: both apps refill it on demand.
 *
 * Racks do NOT go through the table below. `createRackModel` finishes with
 * `syncRackModelState`, 190 lines that normalise slots, doors, servers and
 * partitions against each other, so it is called from the shared
 * `js/rack_state.js` rather than reimplemented — see `createRack` at the bottom.
 */

const FACE_IDS = ['front', 'back', 'left', 'right', 'top', 'bottom'] as const

/** Mirrors FACE_CONFIGS in STL_generate.html:1369. */
const FACE_CONFIGS: Record<string, { id: string; label: string; wType: string; hType: string }> = {
  front: { id: 'front', label: 'Front', wType: 'W', hType: 'H' },
  back: { id: 'back', label: 'Back', wType: 'W', hType: 'H' },
  left: { id: 'left', label: 'Left', wType: 'D', hType: 'H' },
  right: { id: 'right', label: 'Right', wType: 'D', hType: 'H' },
  top: { id: 'top', label: 'Top', wType: 'W', hType: 'D' },
  bottom: { id: 'bottom', label: 'Bottom', wType: 'W', hType: 'D' },
}

function createFacesState(labelOverrides: Record<string, string> = {}) {
  const faces: Record<string, unknown> = {}
  for (const faceId of FACE_IDS) {
    faces[faceId] = {
      ...FACE_CONFIGS[faceId],
      displayName: labelOverrides[faceId] || FACE_CONFIGS[faceId]!.label,
      prefixEnabled: true,
      opacity: 1,
      reverseSolidNormal: false,
      wallNames: [],
      wallOpacities: [],
      patches: [],
    }
  }
  return faces
}

function defaultTransform() {
  return { quaternion: { x: 0, y: 0, z: 0, w: 1 }, scale: { x: 1, y: 1, z: 1 } }
}

function defaultMoveTool() {
  return { enabled: false, axes: { x: true, y: true, z: true } }
}

/**
 * Mirrors getDefaultModelRotateTool in STL_generate.html:3286.
 *
 * The legacy FACTORY does not set this — `ensureModelRotateTool` materializes it
 * the first time the inspector asks. But 1627 of the 1630 models in this repo's
 * case files carry it, so a model created here without it would be the odd one
 * out in every saved project. It is applied after the migrator so it lands last
 * in key order, where the lazy path puts it.
 */
function defaultRotateTool() {
  return { enabled: false, axes: { yaw: true, pitch: false, roll: false } }
}

/** Mirrors getDefaultCfd in STL_generate.html:3370. */
function defaultCfd(kind: string): unknown {
  switch (kind) {
    case 'acu':
      return { role: 'acu', patchPhysics: {} }
    case 'cdu':
      return { role: 'cdu' }
    case 'baffle':
      return {
        role: 'baffle',
        baffleType: 'wall',
        perforated: { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 },
        patchPhysics: {},
      }
    case 'partitionWall':
      return { role: 'partitionWall', patchPhysics: {} }
    case 'floor':
      return { role: 'floor', roomBoundaryType: 'none', openings: [], defaultBC: { type: 'wallBaffle' } }
    case 'wall':
      return { role: 'wall' }
    default:
      return { role: 'wall' }
  }
}

export interface CreatableKind {
  kind: string
  /** Base extents in the legacy W/D/H convention: W=x, D=z, H=y. */
  base: { W: number; D: number; H: number }
  opacity?: number
  namePrefix: string
  /** Extra top-level seed fields, for kinds that carry them (CDU). */
  extra?: Record<string, unknown>
}

/** Kinds built from a plain seed. Racks are not here — see `createRack`. */
export const CREATABLE: Record<string, CreatableKind> = {
  box: { kind: 'box', base: { W: 0.6, D: 0.4, H: 1.8 }, namePrefix: 'Default_Box' },
  wall: { kind: 'wall', base: { W: 1.0, D: 0, H: 1.0 }, namePrefix: 'Default_Wall' },
  partitionWall: {
    kind: 'partitionWall',
    base: { W: 1.0, D: 0.05, H: 3.0 },
    namePrefix: 'Partition_Wall',
  },
  baffle: { kind: 'baffle', base: { W: 1.0, D: 0, H: 1.0 }, namePrefix: 'Default_Baffle' },
  floor: { kind: 'floor', base: { W: 5.0, D: 5.0, H: 0 }, opacity: 0.6, namePrefix: 'Floor' },
  acu: { kind: 'acu', base: { W: 1.2, D: 0.6, H: 2.0 }, namePrefix: 'ACU' },
  cdu: {
    kind: 'cdu',
    base: { W: 0.8, D: 0.6, H: 1.8 },
    namePrefix: 'CDU',
    extra: {
      liquidCooling: {
        supplyTemperature: 35,
        supplyPressure: 250,
        totalFlowRate: 60,
        servedRacks: [],
        advancedMode: false,
        advanced: {
          pump: { specName: 'pump_default', rpm: 3000 },
          heatExchanger: { specName: 'hx_default' },
          valve: { specName: 'valve_default', opening: 1.0 },
        },
      },
    },
  },
}

/**
 * Next free `m_<n>` uid.
 *
 * Derived from what the project already holds, not from a counter: a counter
 * starts at zero every page load and would hand out uids that collide with the
 * ones in the file the user just opened.
 */
export function nextModelUid(project: CfdProject): string {
  let max = 0
  for (const items of Object.values(project.constructions ?? {})) {
    if (!items) continue
    for (const uid of Object.keys(items)) {
      const m = /^m_(\d+)$/.exec(uid)
      if (m) max = Math.max(max, Number(m[1]))
    }
  }
  return `m_${max + 1}`
}

/** Next free `<prefix>_<n>` name within the kind's bucket. */
export function nextModelName(project: CfdProject, kind: string, prefix: string): string {
  const bucket = bucketForKind(kind)
  const taken = new Set(modelsIn(project, bucket).map((m) => m.meta?.name))
  let n = modelsIn(project, bucket).length + 1
  while (taken.has(`${prefix}_${n}`)) n += 1
  return `${prefix}_${n}`
}

export interface CreatedModel {
  bucket: Bucket
  model: Construction
}

/** Build a default model of `kind`, ready to drop into `constructions`. */
/**
 * A free pipe segment — three waypoints, connected to nothing.
 *
 * Not in CREATABLE because a pipe has no box: its `base` is zero on every axis
 * and its shape lives entirely in `waypoints`. The legacy `createFreePipe`
 * builds exactly this, then overrides the spec to 100 mm — done here in one
 * place instead, so there is no window where the model exists at 25 mm.
 */
export function createPipeSegment(project: CfdProject): CreatedModel {
  const seed: V3Seed = {
    id: nextModelUid(project),
    kind: 'pipe_segment',
    name: nextModelName(project, 'pipe_segment', 'Pipe'),
    enabled: true,
    // A pipe has no anchor cube to mark; the waypoint spheres ARE its handles.
    showAnchorMarker: false,
    opacity: 1,
    anchor: { x: 0, y: 0, z: 0 },
    base: { W: 0, D: 0, H: 0 },
    transform: defaultTransform(),
    moveTool: defaultMoveTool(),
    faces: createFacesState(),
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: defaultCfd('pipe_segment'),
    pipe: {
      spec: 'pipe_100mm',
      diameter: 0.1,
      startPortId: '',
      endPortId: '',
      // Three points, not two: a straight two-point pipe gives the middle
      // control handle nothing to grab, and bending it is the first thing
      // anyone does.
      waypoints: [
        { x: 0, y: 0.5, z: 0 },
        { x: 0, y: 0.5, z: 0.5 },
        { x: 0, y: 0.5, z: 1.0 },
      ],
      color: 'supply',
      junctions: [],
    },
  }
  const model = withRotateTool(finalizeNewModel(seed))
  const cooling = (model as Record<string, unknown>).cooling as Record<string, unknown>
  // `loopRole` is what the pipe renderer colours by: an unconnected pipe draws
  // brown rather than in supply blue, so a pipe that goes nowhere looks like
  // one.
  if (cooling) cooling.loopRole = 'unconnected'
  return { bucket: bucketForKind('pipe_segment'), model }
}

/** A T-branch fitting, 60 mm cubed. */
export function createPipeJunction(
  project: CfdProject,
  position: { x: number; y: number; z: number } = { x: 0, y: 0, z: 0 },
  parentPipeId: string | null = null,
): CreatedModel {
  const seed: V3Seed = {
    id: nextModelUid(project),
    kind: 'pipe_junction',
    name: nextModelName(project, 'pipe_junction', 'T-Branch'),
    enabled: true,
    showAnchorMarker: true,
    opacity: 1,
    anchor: { ...position },
    base: { W: 0.06, D: 0.06, H: 0.06 },
    transform: defaultTransform(),
    moveTool: defaultMoveTool(),
    faces: createFacesState(),
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: defaultCfd('pipe_junction'),
    junction: createAuthoredTBranch(),
    parentPipeId,
  }
  return { bucket: bucketForKind('pipe_junction'), model: withRotateTool(finalizeNewModel(seed)) }
}

/**
 * An air-side pipe: a 150 mm round duct, one straight vertical metre.
 *
 * Waypoints are LOCAL to the anchor, so the first one is the origin; the pipe
 * stands on its anchor and the move tool carries the whole run. No anchor cube:
 * the waypoint balls are the pipe's handles, as for a liquid pipe.
 */
export function createAirPipe(
  project: CfdProject,
  opts: {
    shape?: 'round' | 'rect'
    diameterM?: number
    widthM?: number
    heightM?: number
    location?: { x: number; y: number; z: number }
    /** Local waypoints; two or more. */
    waypoints?: { x: number; y: number; z: number }[]
  } = {},
): CreatedModel {
  const seed: V3Seed = {
    id: nextModelUid(project),
    kind: 'pipe',
    name: nextModelName(project, 'pipe', 'Pipe'),
    enabled: true,
    showAnchorMarker: false,
    opacity: 1,
    anchor: { ...(opts.location ?? { x: 0, y: 0, z: 0 }) },
    base: { W: 0, D: 0, H: 0 },
    transform: defaultTransform(),
    moveTool: defaultMoveTool(),
    faces: {},
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: { role: 'wall' },
    pipe: {
      shape: opts.shape ?? 'round',
      diameterM: opts.diameterM ?? 0.15,
      widthM: opts.widthM ?? 0.2,
      heightM: opts.heightM ?? 0.2,
      waypoints:
        opts.waypoints && opts.waypoints.length >= 2
          ? opts.waypoints.map((p) => ({ ...p }))
          : [
              { x: 0, y: 0, z: 0 },
              { x: 0, y: 1.0, z: 0 },
            ],
      joins: [],
    },
  }
  return { bucket: bucketForKind('pipe'), model: withRotateTool(finalizeNewModel(seed)) }
}

export function createModel(project: CfdProject, kind: string): CreatedModel {
  if (kind === 'rack') return createRack(project)
  if (kind === 'pipe') return createAirPipe(project)
  const spec = CREATABLE[kind]
  if (!spec) throw new Error(`no factory for kind "${kind}"`)
  const seed: V3Seed = {
    id: nextModelUid(project),
    kind: spec.kind,
    name: nextModelName(project, kind, spec.namePrefix),
    enabled: true,
    showAnchorMarker: true,
    opacity: spec.opacity ?? 1,
    anchor: { x: 0, y: 0, z: 0 },
    base: { ...spec.base },
    transform: defaultTransform(),
    moveTool: defaultMoveTool(),
    faces: createFacesState(),
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: defaultCfd(spec.kind),
    ...(spec.extra ?? {}),
  }
  return { bucket: bucketForKind(spec.kind), model: withRotateTool(finalizeNewModel(seed)) }
}

/**
 * Attach `rotateTool` AFTER the migrator, so it lands last in key order.
 *
 * The legacy app materialises it lazily on first inspector render, which is why
 * it ends up last there. JSON.stringify writes keys in insertion order, so
 * putting it in the seed instead would make every saved file differ in byte
 * order from a legacy-saved one for no reason. Applied to every kind here,
 * racks included — a rack that skipped it was the one field separating the
 * React rack from the HTML one.
 */
function withRotateTool(model: Construction): Construction {
  ;(model as Record<string, unknown>).rotateTool = defaultRotateTool()
  return model
}

/**
 * A default 42U rack, built by the shared `js/rack_state.js`.
 *
 * Ids are injected per call rather than configured once: that module cannot own
 * the counters (the HTML editor's load path assigns them, and two patch-naming
 * sites read one directly), and here they have to be derived from the project
 * anyway — a counter starting at zero each page load would collide with the uids
 * in the file just opened.
 *
 * Nothing sets a rotation. The legacy `createDefaultRackModel` looked like it
 * gave new racks a 180 degree yaw, but that write went to a read-only derived
 * view and never landed; every rack it produced had the identity quaternion.
 * Matching the behaviour, not the apparent intent.
 */
export function createRack(project: CfdProject): CreatedModel {
  const rack = rackState()
  let patchSeq = nextPatchSeq(project)
  rack.configureIds({
    nextModelId: () => nextModelUid(project),
    nextPatchId: () => `p_${patchSeq++}`,
  })
  const model = rack.createRackModel(nextModelName(project, 'rack', 'Default_Rack'))
  return { bucket: bucketForKind('rack'), model: withRotateTool(model) }
}

/** Next free `p_<n>`, scanned across every patch already in the project. */
function nextPatchSeq(project: CfdProject): number {
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
  visit(project.constructions ?? {})
  return max + 1
}
