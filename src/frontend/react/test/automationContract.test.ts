/**
 * The automation contract — `window.__dcdtuni` plus the top-level legacy aliases.
 *
 * This is the surface `/api/browser/exec-js` drives, and therefore the surface
 * the whole 3-agent pipeline stands on: `/dc-builder`, `/dc-evaluator`,
 * `/dc-orchestrate`, `/dc-dataset-reverse` and
 * `paper_experiment/batch_cfd_pipeline.py` all reach the editor through it.
 *
 * Why a test and not just the module: the legacy editor answers 465 different
 * `window.*` names and nothing records which of them are load-bearing. The
 * answer, measured, is ~25 — and if one of those 25 silently stops being a
 * function, nothing in the app breaks. The pipeline breaks, hours later, in a
 * subagent's log. So the list is pinned here.
 *
 * The names match the legacy's exactly (`window.createDefaultRackModel`, not a
 * tidier `createModel('rack')`) for the same reason `room2dApi.ts` matches
 * them: one SOP has to drive either app while both exist.
 */

import { beforeAll, describe, expect, it } from 'vitest'

import { automation } from '../src/automation/contract'
import * as store from '../src/projectStore'

/** Every verb the automation actually calls, measured from the callers. */
const REQUIRED_VERBS = [
  // creation — the seven `createDefault*Model` the legacy exposes
  'createDefaultRackModel',
  'createDefaultAcuModel',
  'createDefaultWallModel',
  'createDefaultBaffleModel',
  'createDefaultFloorModel',
  'createDefaultBaseModel',
  'createDefaultCduModel',
  // placement — what the Builder sets on every model it creates
  'newProject',
  'placeModel',
  'setRoomPolygon',
  'applyRoom',
  'setProjectMeta',
  // patches — an ACU's supply/return, floor tiles, wall leakage
  'addPatch',
  'fillFacePatch',
  'setPatchGeometry',
  'updateAcuPatchPhysics',
  // rack interior
  'createRackEquipmentAtSlot',
  'updateRackServerCfd',
  'rackServers',
  // stacked servers (spec 2026-09-17 §10)
  'setRackServersOpacity',
  'setServerOpacity',
  'addRackServerAtRange',
  'rackServerDepths',
  // selection — the Evaluator has to be able to pick a model to inspect it
  'select',
  'getSelection',
  // view
  'applyViewPreset',
  // run
  'setCaseDir',
  'refreshServerInfo',
  'runMeshGeneration',
  'runSolveOnly',
  'setSolverType',
  // transient timeline — a browser gate drives the playhead
  'setSimulationMode',
  'setPlayhead',
  'playheadTime',
  'sceneFrozen',
  'lastEditRejection',
  'setSolverConfig',
  'setMeshConfig',
  'runLiquidSolve',
  // project
  'allModels',
  'getProject',
  // export — what /api/browser/export-cfdrun and geometry-status evaluate
  'buildCfdrunJsonV2',
  'saveProjectJson',
  'buildGeometryData',
  'buildGroupedStlMap',
  'getCfdrunFilename',
  'geometryStatus',
  // observation — a script must be able to watch the run it started
  'getJob',
  'liquidRunState',
  'getServerInfo',
  'getCaseStatus',
  'isValidated',
] as const

describe('automation contract', () => {
  it('exposes every verb the 3-agent pipeline calls', () => {
    const missing = REQUIRED_VERBS.filter((v) => typeof (automation as never)[v] !== 'function')
    expect(missing).toEqual([])
  })

  it('keeps the store handle the parity harness reads', () => {
    // parity/react_probe.py and parity/liquid_drive.py both evaluate
    // `window.__dcdtuni.getProject()`. Dropping it breaks the harness silently:
    // the probe returns undefined and the diff reports "no models".
    expect(typeof automation.getProject).toBe('function')
    expect(typeof automation.edit).toBe('function')
    expect(typeof automation.undo).toBe('function')
  })

  it('publishes NOTHING at the top level', () => {
    // The contract used to install a bare alias per verb so one SOP could drive
    // either editor while both existed. Only one exists now, and a global per
    // verb is the surface this contract was built to avoid — 24 more doors into
    // the document, indistinguishable to a reader from the 465 the old app had.
    // `room2d` and `_applyProjectData` are NOT ours: room2dApi.ts and App.tsx
    // install those themselves, under names the 2D SOP has always used.
    //
    // Compared by IDENTITY, not by name. `getSelection` is a DOM builtin that
    // happens to share a name with one of our verbs, and a name check would
    // report the browser's own API as a leak of ours.
    const w = window as unknown as Record<string, unknown>
    const bag = automation as unknown as Record<string, unknown>
    const leaked = Object.keys(bag).filter((k) => w[k] !== undefined && w[k] === bag[k])
    expect(leaked).toEqual([])
  })
})

describe('automation contract — shapes the callers depend on', () => {
  it('allModels() yields BARE models, not {bucket, model} pairs', () => {
    // server.py:2534/2602/2669 do `window.allModels().map(m => m.uid)` and
    // js/cfdrun_build.js:171/368 do `.forEach(m => ...)`. Handing those the
    // tree's pair shape does not throw — it yields undefined uids and an export
    // whose patch list is quietly empty. This is that regression, pinned.
    const project = store.getProject()
    expect(project).toBeTruthy()
    const uid = automation.createDefaultRackModel()
    expect(uid).toBeTruthy()

    const models = automation.allModels()
    expect(models.length).toBeGreaterThan(0)
    for (const m of models) {
      expect(Object.keys(m)).not.toEqual(['bucket', 'model'])
      expect(typeof (m as { uid?: string }).uid).toBe('string')
    }
    expect(models.some((m) => (m as { uid?: string }).uid === uid)).toBe(true)
  })
})

describe('automation contract — geometry read-backs', () => {
  it('modelBounds is the nominal box, and equipmentPortFrames the rack ports, in world space', async () => {
    await import('../src/legacy/geometryBuilder')  // loads transform_tools.js, as the editor and the headless build do
    const uid = automation.createDefaultRackModel()!
    automation.placeModel(uid, { location: { x: 2, y: 0, z: 3 }, size: { W: 0.6, D: 1.2, H: 2 } })
    const box = automation.modelBounds([uid])[uid]!
    expect(box.kind).toBe('rack')
    expect(box.max[0]! - box.min[0]!).toBeCloseTo(0.6, 9)
    expect(box.max[1]! - box.min[1]!).toBeCloseTo(2, 9)
    expect(box.max[2]! - box.min[2]!).toBeCloseTo(1.2, 9)
    const frames = automation.equipmentPortFrames(uid) as Record<string, { position: { x: number; y: number; z: number } }>
    expect(Object.keys(frames).sort()).toEqual(['return_bottom', 'return_top', 'supply_bottom', 'supply_top'])
    for (const frame of Object.values(frames)) {
      expect(frame.position.x).toBeGreaterThanOrEqual(box.min[0]! - 1e-9)
      expect(frame.position.x).toBeLessThanOrEqual(box.max[0]! + 1e-9)
    }
    const acu = automation.createDefaultAcuModel()!
    expect(automation.equipmentPortFrames(acu)).toBeNull()
  })
})

describe('automation contract — placement writes V4 fields', () => {
  it('places a model by name, location, size and rotation', () => {
    // The old Builder SOP wrote `m.name`, `m.anchor` and `m.base` — the V2
    // spelling. V4 keeps them at `meta.name`, `geometry.location` and
    // `geometry.size`, so the old writes landed on a document nobody reads and
    // the model stayed at the origin at its default size. This asserts the
    // fields the RUNNER reads, not the ones the SOP used to write.
    const uid = automation.createDefaultRackModel()
    expect(uid).toBeTruthy()

    const ok = automation.placeModel(uid!, {
      name: 'Rack_A1',
      location: { x: 1.5, y: 0, z: 2.25 },
      size: { W: 0.6, D: 1.2, H: 2 },
      rotationDeg: { x: 0, y: 90, z: 0 },
    })
    expect(ok).toBe(true)

    const project = store.getProject()!
    const model = store.findConstruction(project, uid!)!.model as {
      meta?: { name?: string }
      geometry?: {
        location?: { x: number; y: number; z: number }
        size?: { x: number; y: number; z: number }
        quaternion?: { x: number; y: number; z: number; w: number }
      }
    }
    expect(model.meta?.name).toBe('Rack_A1')
    expect(model.geometry?.location).toEqual({ x: 1.5, y: 0, z: 2.25 })
    // W->x, H->y, D->z. Getting this backwards lies a rack on its side.
    expect(model.geometry?.size).toEqual({ x: 0.6, y: 2, z: 1.2 })
    // 90 degrees of yaw is a quaternion with y = sin(45) and w = cos(45).
    expect(model.geometry?.quaternion?.y).toBeCloseTo(Math.SQRT1_2, 6)
    expect(model.geometry?.quaternion?.w).toBeCloseTo(Math.SQRT1_2, 6)
  })

  it('refuses an unknown uid instead of writing nothing quietly', () => {
    expect(automation.placeModel('m_does_not_exist', { name: 'x' })).toBe(false)
  })

  it('sets the room footprint on the project, where the mesh reads it', () => {
    const plane: [number, number][] = [
      [0, 0],
      [6, 0],
      [6, 4],
      [0, 4],
    ]
    expect(automation.setRoomPolygon(plane, 3)).toBe(true)
    const geometry = store.getProject()!.geometry as {
      plane?: [number, number][]
      height?: number
    }
    expect(geometry.plane).toEqual(plane)
    expect(geometry.height).toBe(3)
  })

  it('refuses a degenerate footprint', () => {
    expect(automation.setRoomPolygon([[0, 0], [1, 1]], 3)).toBe(false)
  })

  it('builds the room shell the 2D editor Apply builds, in one undo step', () => {
    // setRoomPolygon stores only the footprint. A floor made by the default
    // factory is an INTERIOR sheet (a createBaffles wallBaffle with air on both
    // sides): placed at y = 0 it has no fluid below it, and the runner refuses
    // to seed it. The room's own floor and ceiling are room boundaries.
    automation.newProject()
    const plane: [number, number][] = [[0, 0], [4, 0], [4, 3], [0, 3]]
    const depth = store.undoDepth()
    const shell = automation.applyRoom(plane, 2.8)
    expect(shell).not.toBe(false)
    if (shell === false) return
    expect(store.undoDepth()).toBe(depth + 1)
    expect(shell.walls).toHaveLength(4)
    const project = store.getProject()!
    expect(project.geometry).toMatchObject({ plane, height: 2.8 })
    const model = (uid: string) => store.findConstruction(project, uid)!.model as {
      isRoomBoundary?: boolean
      cooling?: { roomBoundaryLocation?: string; defaultBoundaryType?: string }
      geometry?: { location?: { y: number }; faces?: { top?: { reverseSolidNormal?: boolean } } }
    }
    const floor = model(shell.floor)
    const ceiling = model(shell.ceiling)
    expect(floor.isRoomBoundary).toBe(true)
    expect(floor.cooling).toMatchObject({ roomBoundaryLocation: 'bottom', defaultBoundaryType: 'wall' })
    expect(floor.geometry?.faces?.top?.reverseSolidNormal).toBe(true) // the ground faces -Y, out of the room
    expect(ceiling.cooling).toMatchObject({ roomBoundaryLocation: 'top', defaultBoundaryType: 'wall' })
    expect(ceiling.geometry?.location?.y).toBe(2.8)
    for (const uid of shell.walls) expect(model(uid).isRoomBoundary).toBe(true)
    // Applying again REPLACES the shell (as the editor does), it does not stack a second one.
    const again = automation.applyRoom(plane, 3)
    expect(again).not.toBe(false)
    expect(automation.allModels().filter((m) => (m as { isRoomBoundary?: boolean }).isRoomBoundary)).toHaveLength(6)
    expect(automation.applyRoom([[0, 0], [1, 1]], 3)).toBe(false)
    expect(automation.applyRoom(plane, 0)).toBe(false)
  })
})

describe('automation contract — newProject', () => {
  it('empties the document, so a build cannot land on top of another', () => {
    // A builder that opens without this ADDS to whatever the page holds. Caught
    // by scoring a real build: 14 models built, 28 in the file, every name
    // twice, 50%.
    automation.createDefaultRackModel()
    automation.createDefaultAcuModel()
    expect(automation.allModels().length).toBeGreaterThan(0)

    expect(automation.newProject()).toBe(true)
    expect(automation.allModels()).toEqual([])

    // Fresh, not merely cleared: a document with no runDefaults is not a project.
    const project = automation.getProject() as unknown as { runDefaults?: unknown }
    expect(project).toBeTruthy()
    expect(project.runDefaults).toBeTruthy()
  })
})

describe('automation contract — stacked-server verbs', () => {
  function freshRack(): string {
    automation.newProject()
    return automation.createDefaultRackModel()!
  }

  it('addRackServerAtRange returns the uid or { error }, and rackServerDepths reads it back', () => {
    const rack = freshRack()
    // A 5U fan server in the front half (z 0 -> 0.5), recessed from the outlet.
    const deep = automation.addRackServerAtRange(rack, 16, 5, 'fanServer', 0, 0.7)
    expect(typeof deep).toBe('string')
    // A 1U switch at the SAME U's, near the outlet: the stacked case.
    const near = automation.addRackServerAtRange(rack, 16, 1, 'nonFan', 0.6, 0.05)
    expect(typeof near).toBe('string')
    // The same U range only blocks when the depth intervals intersect.
    expect(automation.addRackServerAtRange(rack, 16, 1, 'nonFan', 0.1, 0)).toEqual({
      error: 'occupied',
    })
    // And the §3.1 budget is refused, not clamped.
    expect(automation.addRackServerAtRange(rack, 16, 1, 'nonFan', 0.9, 0.9)).toEqual({
      error: 'depth-range',
    })
    const depths = automation.rackServerDepths(rack)
    expect(depths.map((d) => d.uid).sort()).toEqual([deep as string, near as string].sort())
    const row = depths.find((d) => d.uid === near)!
    expect(row.startU).toBe(16)
    expect(row.endU).toBe(16)
    expect(row.inletOffset).toBeCloseTo(0.6, 12)
    expect(row.outletOffset).toBeCloseTo(0.05, 12)
    expect(row.opacity).toBe(1)
    expect(typeof row.name).toBe('string')
  })

  it('the opacity verbs write the per-server fields in one edit each', () => {
    const rack = freshRack()
    const a = automation.addRackServerAtRange(rack, 0, 2, 'fanServer') as string
    const b = automation.addRackServerAtRange(rack, 8, 1, 'nonFan') as string
    const before = store.undoDepth()
    expect(automation.setRackServersOpacity(rack, 40)).toBe(true)
    expect(store.undoDepth() - before).toBe(1)
    expect(automation.rackServerDepths(rack).every((d) => Math.abs(d.opacity - 0.4) < 1e-12)).toBe(
      true,
    )
    // The per-server verb leaves the sibling alone.
    expect(automation.setServerOpacity(rack, a, 75)).toBe(true)
    const depths = automation.rackServerDepths(rack)
    expect(depths.find((d) => d.uid === a)!.opacity).toBeCloseTo(0.75, 12)
    expect(depths.find((d) => d.uid === b)!.opacity).toBeCloseTo(0.4, 12)
    // One undo puts the batch value back on both.
    store.undo()
    expect(automation.rackServerDepths(rack).every((d) => Math.abs(d.opacity - 0.4) < 1e-12)).toBe(
      true,
    )
    // Unknown targets refuse rather than write nothing quietly.
    expect(automation.setRackServersOpacity('m_nope', 10)).toBe(false)
    expect(automation.setServerOpacity(rack, 'e_nope', 10)).toBe(false)
    expect(automation.rackServerDepths('m_nope')).toEqual([])
  })
})

describe('automation contract — installed on window', () => {
  beforeAll(async () => {
    await import('../src/automation/contract')
  })

  it('installs __dcdtuni, and only __dcdtuni', () => {
    const w = window as unknown as Record<string, unknown>
    expect(w.__dcdtuni).toBeTruthy()
    expect(typeof (w.__dcdtuni as Record<string, unknown>).createDefaultRackModel).toBe('function')
    // and no bare copy of it
    expect(w.createDefaultRackModel).toBeUndefined()
  })
})

describe('automation contract — transient timeline', () => {
  it('setSolverType goes through the one mode writer, and the playhead is scriptable', () => {
    automation.newProject()
    automation.setSolverType('transient')
    const tr = () => (store.getProject() as any).transient
    expect(tr().mode).toBe('transient')
    expect((store.getProject() as any).runDefaults.simulationConfig.solverType).toBe('transient')
    automation.setPlayhead(60)
    expect(automation.playheadTime()).toBe(60)
    expect(automation.sceneFrozen()).toBe(true)
    automation.setSimulationMode('steady')
    expect(automation.playheadTime()).toBe(0)
    expect(automation.lastEditRejection()).toBeNull()
  })
})
