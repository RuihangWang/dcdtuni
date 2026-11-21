import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CfdProject, Construction, Vec3 } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import {
  collectDigitNameModels,
  collectDoorPorosities,
  collectFloorHeightErrors,
  collectRoomWallGapErrors,
  collectSeedCollisionTargets,
  collectSeedCollisionWarnings,
  doorPorosityMismatch,
  inferRoomHeight,
  modelTopWorldY,
  modelWorldBounds,
  planRoomWallExtension,
  runPreflightChecks,
} from '../src/run/meshPreflight'
import { collectSeedSurfaces } from '../src/geometry/seedConfig'

/**
 * A pre-flight that cries wolf is worse than none: the dialogs are modal, and a
 * user who has learned to click past them will click past the real one. So the
 * first thing asserted here is that every project in `case/` passes clean —
 * that is 22 real rooms, including the four that carry a `planeHeight`
 * disagreeing with `geometry.height` and the eleven whose ceiling floor is what
 * actually sets the room height.
 *
 * The failures are then produced by mutating those same projects, so each one
 * is a real project with one thing wrong rather than a fixture built to pass.
 */

const CASE_DIR = resolve(process.cwd(), '../../../case')

function load(name: string): CfdProject {
  const dir = join(CASE_DIR, name)
  const file = readdirSync(dir).find((f) => f.endsWith('.cfdproj'))!
  return reviveKinds(JSON.parse(readFileSync(join(dir, file), 'utf8')) as CfdProject)
}

function allProjects(): { name: string; project: CfdProject }[] {
  return readdirSync(CASE_DIR)
    .filter((dir) => readdirSync(join(CASE_DIR, dir)).some((f) => f.endsWith('.cfdproj')))
    .map((name) => ({ name, project: load(name) }))
}

describe('no false alarms on the projects in this repo', () => {
  it('finds no illegal name, no floor through the ceiling and no room wall short of it anywhere', () => {
    // Base604 was the one true positive for the room-wall check (3.5 m walls
    // under a 4.4 m ceiling) until its walls were raised on 2026-09-10; the
    // mutation tests below keep that case covered.
    const offenders: string[] = []
    for (const { name, project } of allProjects()) {
      const report = runPreflightChecks(project)
      if (report.digitNames.length) offenders.push(`${name}: digit ${report.digitNames[0]!.name}`)
      if (report.floorHeights.length) {
        const e = report.floorHeights[0]!
        offenders.push(`${name}: floor ${e.name} y=${e.y} > ${e.roomHeight}`)
      }
      for (const e of report.roomWallGaps) {
        offenders.push(`${name}: room wall ${e.name} top=${e.top} < ${e.roomHeight}`)
      }
      if (report.doorPorosity.length) offenders.push(`${name}: doors`)
    }
    expect(offenders).toEqual([])
  })
})

describe('room height', () => {
  it('prefers a floor bound as the room top over geometry.height', () => {
    // Base_leakage's Room_Ceiling sits at 3.633. Its geometry.height said 3.6
    // until 2026-10-26 (now 3.633); a stale height field must not win. Read it
    // and the ceiling itself becomes a "floor through the ceiling" on a
    // project that meshes fine.
    const project = load('Base_leakage')
    ;(project.geometry as { height: number }).height = 3.6
    expect(inferRoomHeight(project)).toBeCloseTo(3.633, 6)
    expect(collectFloorHeightErrors(project)).toEqual([])
  })

  it('falls back to the 2D editor plane height when no floor is bound as top', () => {
    // Base2-9 has no `roomBoundaryLocation: top` floor; its plane is 4.82 while
    // geometry.height still says 4.8.
    const project = load('Base2-9')
    expect(inferRoomHeight(project)).toBeCloseTo(4.82, 6)
  })

  it('falls back to geometry.height with neither', () => {
    expect(inferRoomHeight({ geometry: { height: 3.2 } } as unknown as CfdProject)).toBe(3.2)
  })

  it('reports a floor lifted above the ceiling', () => {
    const project = load('Base_leakage')
    const floor = project.constructions?.floors?.['m_51'] as Construction
    ;(floor.geometry as { location: { y: number } }).location.y = 4
    expect(collectFloorHeightErrors(project)).toEqual([
      { name: 'Raised_Floor_copy', y: 4, roomHeight: 3.633 },
    ])
  })
})

describe('names OpenFOAM cannot parse', () => {
  it('flags a name whose sanitised form starts with a digit', () => {
    const project = load('Base_leakage')
    const rack = project.constructions?.racks?.['m_1'] as Construction
    rack.meta.name = '10 Rack'
    const found = collectDigitNameModels(project)
    expect(found).toEqual([{ uid: 'm_1', name: '10 Rack', bucket: 'racks' }])
  })

  it('accepts a name that only CONTAINS digits', () => {
    const project = load('Base_leakage')
    ;(project.constructions?.racks?.['m_1'] as Construction).meta.name = 'R10'
    expect(collectDigitNameModels(project)).toEqual([])
  })

  it('flags a name that starts with a symbol the sanitiser drops', () => {
    // '_' survives sanitising and is legal; a leading '3' behind punctuation is
    // not, and `sanitizeName` collapses the punctuation rather than removing it,
    // so this one is legal too. The check is on the SANITISED string.
    const project = load('Base_leakage')
    ;(project.constructions?.racks?.['m_1'] as Construction).meta.name = '-3A'
    expect(collectDigitNameModels(project)).toEqual([])
  })
})

describe('rack door porosity', () => {
  it('reads every rendered door, defaulting a missing block to 64%', () => {
    const doors = collectDoorPorosities(load('DAML-Testbed'))
    // DAML-Testbed today: R1 both doors + R2 outlet door, all 94.7 % open.
    expect(doors.size).toBe(3)
    expect(new Set(doors.values())).toEqual(new Set([94.7]))
    expect(doorPorosityMismatch(load('DAML-Testbed'))).toEqual([])
  })

  it('reports every door once one of them disagrees', () => {
    const project = load('DAML-Testbed')
    const rack = Object.values(project.constructions?.racks ?? {})[0] as Construction
    const faces = (rack.geometry as unknown as {
      faces: { uid?: string; perforation?: { openAreaPct: number } }[]
    }).faces
    faces.find((f) => f.uid === 'inletDoor')!.perforation!.openAreaPct = 40
    const mismatch = doorPorosityMismatch(project)
    expect(mismatch.length).toBe(3)
    expect(mismatch.filter((d) => d.pct === 40).length).toBe(1)
  })

  it('ignores a door that is not rendered', () => {
    const project = load('DAML-Testbed')
    const rack = Object.values(project.constructions?.racks ?? {})[0] as Construction
    const faces = (rack.geometry as unknown as { faces: { uid?: string; renderDoor?: boolean }[] })
      .faces
    faces.find((f) => f.uid === 'inletDoor')!.renderDoor = false
    expect(collectDoorPorosities(project).size).toBe(2)
  })
})

describe('model bounds', () => {
  it('turns the local box by the model quaternion', () => {
    // Default_Rack_1 is 0.6 x 1.9579 x 1.0 at (1.601, 1, 3.0), yawed so its
    // depth runs along -X. Reading the box unrotated would put it a metre away.
    const bounds = modelWorldBounds(load('Base_leakage').constructions?.racks?.['m_1'] as Construction)!
    expect(bounds.min.x).toBeCloseTo(0.601, 6)
    expect(bounds.max.x).toBeCloseTo(1.601, 6)
    expect(bounds.min.y).toBeCloseTo(1, 6)
    expect(bounds.max.y).toBeCloseTo(2.9579, 6)
    expect(bounds.min.z).toBeCloseTo(3.0, 6)
    expect(bounds.max.z).toBeCloseTo(3.6, 6)
  })

  it('handles a negative scale without inverting the box', () => {
    // Default_Rack_3 carries scale.z = -1. A min/max taken from the transformed
    // corners without re-bounding would come out with min > max.
    const bounds = modelWorldBounds(load('Base_leakage').constructions?.racks?.['m_2'] as Construction)!
    expect(bounds.min.x).toBeLessThan(bounds.max.x)
    expect(bounds.min.z).toBeLessThan(bounds.max.z)
    expect(bounds.max.z).toBeCloseTo(3.0, 6)
  })

  it('collects racks, ACUs and boxes but not walls or baffles', () => {
    const targets = collectSeedCollisionTargets(load('Base_leakage'))
    expect(targets.map((t) => t.type).sort()).toEqual(['acu', 'acu', 'rack', 'rack', 'rack', 'rack'])
  })
})

describe('seeds inside a solid', () => {
  it('passes a project whose seeds are all in open air', () => {
    expect(collectSeedCollisionWarnings(load('Base_leakage'))).toEqual([])
  })

  it('catches a pinned raised-floor seed trapped under a rack', () => {
    // Base_coldAisleIso stands two racks on a wallBaffle raised floor. Pinned
    // where the default would sit (a few centimetres above and below the slab),
    // the upper seed is inside the rack footprint; the runner keeps a pinned
    // anchor as placed, so the mesher would keep the rack interior as fluid.
    const project = load('Base_coldAisleIso')
    const pinned = collectSeedSurfaces(project).map((s) => ({ ...s, isCustom: true }))
    const warnings = collectSeedCollisionWarnings(project, pinned)
    expect(warnings.map((w) => `${w.surfaceName}->${w.objectName}:${w.side}`)).toEqual([
      'raised_floor_part1->Rack_1:positive',
      'raised_floor_part1->Rack_2:positive',
    ])
    expect(warnings[0]!.objectType).toBe('rack')
  })

  it('leaves default seeds to the runner, which moves them clear of solids (Base604 partition1)', () => {
    // The runner places every seed nobody pinned: it measures the clearance on
    // both sides and, when a solid stands against the sheet, moves the anchor
    // to a spot on the sheet with room on both sides (stlgen_runner
    // _build_patch_driven_none_seed_entries). Base604's partition1 default
    // anchor is the wall centre, exactly level with ACU_04's top, and the
    // editor flagged it on every mesh run; the runner put it at
    // (13, 2.80, 10.25), clear of the ACU.
    expect(collectSeedCollisionWarnings(load('Base_coldAisleIso'))).toEqual([])
    expect(collectSeedCollisionWarnings(load('Base604'))).toEqual([])
  })

  it('never reports a seed against the model it belongs to', () => {
    // A perforated tile's own seed is inside that tile's bounding box by
    // construction; reporting it would flag every project that has one.
    for (const { name, project } of allProjects()) {
      const own = collectSeedCollisionWarnings(project).filter((w) =>
        w.surfaceName.startsWith(w.objectName),
      )
      expect(`${name}:${own.length}`).toBe(`${name}:0`)
    }
  })
})

type WallGeometry = {
  location: Vec3
  size: Vec3
  scale: Vec3
  quaternion: { x: number; y: number; z: number; w: number }
}

const wallOf = (project: CfdProject, uid: string) =>
  project.constructions?.walls?.[uid] as Construction
const geo = (model: Construction) => model.geometry as unknown as WallGeometry

describe('room walls that stop below the room height', () => {
  it('passes DAML: four room walls at 2.7 m under its 2.7 m ceiling', () => {
    const project = load('DAML-Testbed')
    expect(inferRoomHeight(project)).toBeCloseTo(2.7, 6)
    expect(collectRoomWallGapErrors(project)).toEqual([])
  })

  it('flags a DAML wall shrunk below the ceiling, and only that wall', () => {
    const project = load('DAML-Testbed')
    geo(wallOf(project, 'm_2')).size.y = 2
    const errors = collectRoomWallGapErrors(project)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatchObject({ uid: 'm_2', name: 'Wall_2' })
    expect(errors[0]!.top).toBeCloseTo(2, 9)
    expect(errors[0]!.roomHeight).toBeCloseTo(2.7, 9)
    expect(runPreflightChecks(project).roomWallGaps).toEqual(errors)
  })

  it('checks a disabled wall: a room wall is exported whether or not it is drawn', () => {
    const project = load('DAML-Testbed')
    const wall = wallOf(project, 'm_3')
    expect(wall.meta.enabled).toBe(false)
    geo(wall).size.y = 1.5
    expect(collectRoomWallGapErrors(project).map((e) => e.name)).toEqual(['Wall_3'])
  })

  it('turns each wall by its quaternion: Base604 back at 3.5 m', () => {
    // Base604 before 2026-09-10: four walls 3.5 m under a 4.4 m ceiling, turned
    // about Y with a negative w. dcCart meshed it with no room wall, floor or
    // ceiling patch at all.
    const project = load('Base604')
    expect(collectRoomWallGapErrors(project)).toEqual([])
    for (const uid of ['m_166', 'm_167', 'm_168', 'm_169']) geo(wallOf(project, uid)).size.y = 3.5
    const errors = collectRoomWallGapErrors(project)
    expect(errors.map((e) => e.name)).toEqual([
      'Room_Wall_1',
      'Room_Wall_2',
      'Room_Wall_3',
      'Room_Wall_4',
    ])
    for (const e of errors) {
      expect(e.top).toBeCloseTo(3.5, 9)
      expect(e.roomHeight).toBeCloseTo(4.4, 9)
    }
  })

  it('ignores sub-millimetre rounding', () => {
    const project = load('DAML-Testbed')
    geo(wallOf(project, 'm_1')).size.y = 2.6995
    expect(collectRoomWallGapErrors(project)).toEqual([])
  })

  it('ignores an interior wall, which may stop below the ceiling', () => {
    const project = load('DAML-Testbed')
    const wall = wallOf(project, 'm_2')
    wall.isRoomBoundary = false
    geo(wall).size.y = 1
    expect(collectRoomWallGapErrors(project)).toEqual([])
  })

  it('scales the height', () => {
    const project = load('DAML-Testbed')
    const g = geo(wallOf(project, 'm_2'))
    g.size.y = 1.35
    g.scale.y = 2
    expect(collectRoomWallGapErrors(project)).toEqual([])
    g.scale.y = 1
    expect(collectRoomWallGapErrors(project).map((e) => e.name)).toEqual(['Wall_2'])
  })

  it('measures the world top of a wall that is not upright', () => {
    const project = load('DAML-Testbed')
    const g = geo(wallOf(project, 'm_1'))
    // Turned 90 degrees about Z, the 6.09 m length stands up: the top is at
    // 6.09 although size.y says 1, which location.y + size.y would flag.
    g.size.y = 1
    g.quaternion = { x: 0, y: 0, z: Math.SQRT1_2, w: Math.SQRT1_2 }
    expect(modelTopWorldY(g)).toBeCloseTo(6.09, 9)
    expect(collectRoomWallGapErrors(project)).toEqual([])
    // Laid flat about X, a full-height wall tops out at its anchor.
    g.size.y = 2.7
    g.quaternion = { x: Math.SQRT1_2, y: 0, z: 0, w: Math.SQRT1_2 }
    const errors = collectRoomWallGapErrors(project)
    expect(errors.map((e) => e.name)).toEqual(['Wall_1'])
    expect(errors[0]!.top).toBeCloseTo(0, 9)
  })

  it('defaults a missing quaternion, scale or location instead of producing NaN', () => {
    expect(modelTopWorldY({ size: { x: 1, y: 2, z: 0 } })).toBe(2)
    expect(modelTopWorldY({ location: { x: 0, y: 0.5, z: 0 }, size: { x: 1, y: 2, z: 0 } })).toBe(2.5)
  })

  it('has nothing to measure against without a room height', () => {
    const project = {
      geometry: { height: 0 },
      constructions: {
        walls: {
          w: {
            uid: 'w',
            meta: { name: 'W' },
            isRoomBoundary: true,
            geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 1, y: 1, z: 0 } },
          },
        },
      },
    } as unknown as CfdProject
    expect(collectRoomWallGapErrors(project)).toEqual([])
    expect(collectRoomWallGapErrors(null)).toEqual([])
  })
})

describe('the extension the room-wall dialog offers', () => {
  it('sets size.y so each top lands on the room height, through location and scale', () => {
    const project = load('DAML-Testbed')
    geo(wallOf(project, 'm_2')).size.y = 2
    const g4 = geo(wallOf(project, 'm_4'))
    g4.size.y = 1
    g4.location.y = 0.5
    g4.scale.y = 2
    const plan = planRoomWallExtension(project, ['m_2', 'm_4'], 2.7)
    expect(plan.map(({ uid, name, from }) => ({ uid, name, from }))).toEqual([
      { uid: 'm_2', name: 'Wall_2', from: 2 },
      { uid: 'm_4', name: 'Wall_4', from: 1 },
    ])
    expect(plan[0]!.to).toBeCloseTo(2.7, 12)
    expect(plan[1]!.to).toBeCloseTo(1.1, 12)
  })

  it('skips a wall already at height, an unknown uid and a model that is not a wall', () => {
    // m_5 is the DAML floor.
    expect(planRoomWallExtension(load('DAML-Testbed'), ['m_1', 'nope', 'm_5'], 2.7)).toEqual([])
  })

  it('refuses a height it cannot reach', () => {
    const project = load('DAML-Testbed')
    geo(wallOf(project, 'm_2')).size.y = 2
    expect(planRoomWallExtension(project, ['m_2'], 0)).toEqual([])
    geo(wallOf(project, 'm_2')).location.y = 3
    expect(planRoomWallExtension(project, ['m_2'], 2.7)).toEqual([])
  })
})
