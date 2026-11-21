import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { resetSeedConfig, setSeedDirectionComponent, setSeedOffset } from '../src/geometry/seedActions'
import {
  collectSeedSurfaces,
  seedEndpoints,
  seedLocalCellSize,
  seedOffsetMin,
  seedSurfaceForModel,
  seedSurfaceForPatch,
  type SeedSurface,
} from '../src/geometry/seedConfig'

/**
 * Tested against a real project, not a fixture, because the thing that breaks a
 * seed is the .cfdproj's own conventions: a floor whose `size.y` is 0 and whose
 * thickness lives elsewhere, a baffle rotated -90° about X so its "front" faces
 * down, a `defaultBoundaryType` that decides whether the surface has a seed at
 * all. A hand-written fixture would agree with whatever the code does.
 *
 * case/Base_leakage carries one of each: a perforated opening in a raised floor,
 * a wallBaffle default on that same floor, three rotated wall baffles, and two
 * floors (`wall`) that must NOT produce a seed.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

/**
 * The mesh sizes these numbers are worked out for: base 0.17 m, baffle level
 * [1, 1] -> a 0.085 m cell at the surface. Pinned here, not read from the
 * case file, so a sizing change to case/Base_leakage (the historical preset,
 * 7813a336) does not rewrite what the seed arithmetic is checked against.
 */
function pinMeshSizing(project: CfdProject): CfdProject {
  const rd = ((project as { runDefaults?: Record<string, unknown> }).runDefaults ??= {})
  const mc = ((rd as { meshConfig?: Record<string, unknown> }).meshConfig ??= {})
  mc.meshBaseSize = 0.17
  mc.wallBafflesRefinementLevel = [1, 1]
  return project
}

function load(): CfdProject {
  return pinMeshSizing(reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject))
}

function floorOpeningSurface(project: CfdProject): SeedSurface {
  const surface = collectSeedSurfaces(project).find((s) => s.owner === 'floor-opening')
  expect(surface).toBeTruthy()
  return surface as SeedSurface
}

describe('cell size and the offset floor', () => {
  it('halves the base size once per baffle refinement level', () => {
    // meshBaseSize 0.17, wallBafflesRefinementLevel [1, 1] -> max level 1.
    expect(seedLocalCellSize(load())).toBeCloseTo(0.085, 10)
    expect(seedOffsetMin(0.085)).toBeCloseTo(0.02125, 10)
  })

  it('uses the 0.25 m production background with the default baffle level', () => {
    expect(seedLocalCellSize({} as CfdProject)).toBeCloseTo(0.125, 10)
  })
})

describe('which surfaces carry a seed', () => {
  it('finds the perforated opening, the wallBaffle floor and the three baffles', () => {
    const surfaces = collectSeedSurfaces(load())
    expect(surfaces.map((s) => `${s.owner}:${s.modelName}`).sort()).toEqual([
      'baffle-default:Baffle_1',
      'baffle-default:Baffle_2',
      'baffle-default:Baffle_3',
      'floor-default:Raised_Floor_copy',
      'floor-opening:Raised_Floor_copy',
    ])
  })

  it('gives a plain wall floor no seed at all', () => {
    // Room_Floor and Room_Ceiling are `defaultBoundaryType: wall`: one-sided, so
    // there is no second volume to seed and no controls to offer.
    const project = load()
    const roomFloor = project.constructions?.floors?.['m_49']
    expect(roomFloor?.meta.name).toBe('Room_Floor')
    expect(seedSurfaceForModel(project, roomFloor!, 'floors')).toBeNull()
  })

  it('places the opening seed at the patch centre with the face normal', () => {
    const surface = floorOpeningSurface(load())
    // Raised floor at y=1, top face origin (0,0,6) with right=+X and up=-Z;
    // patch p_13 is 1.199 x 1.200 at (1.601, 2.400).
    expect(surface.center.x).toBeCloseTo(2.2005, 6)
    expect(surface.center.y).toBeCloseTo(1, 6)
    expect(surface.center.z).toBeCloseTo(3.0, 6)
    expect(surface.normal).toEqual({ x: 0, y: 1, z: 0 })
    expect(surface.seedConfig.offset).toBeCloseTo(0.0425, 10)
    expect(surface.isCustom).toBe(false)
  })

  it('turns the baffle normal with the model quaternion', () => {
    // Baffle_1 is rotated -90° about X, so its front face points down. A normal
    // taken straight from the face layout would put both seeds on the wrong axis
    // and the mesher would keep the wrong side.
    const project = load()
    const baffle = project.constructions?.baffles?.['m_5']
    const surface = seedSurfaceForModel(project, baffle!, 'baffles')
    expect(surface?.normal.x).toBeCloseTo(0, 6)
    expect(surface?.normal.y).toBeCloseTo(-1, 6)
    expect(surface?.normal.z).toBeCloseTo(0, 6)
    expect(surface?.center.y).toBeCloseTo(2.958, 6)
  })

  it('puts one seed on each side of the surface', () => {
    const surface = floorOpeningSurface(load())
    const { positive, negative } = seedEndpoints(surface.seedConfig)
    expect(positive.y).toBeCloseTo(1.0425, 10)
    expect(negative.y).toBeCloseTo(0.9575, 10)
  })
})

describe('overrides', () => {
  beforeEach(() => store.loadProject(load(), '/cases/Base_leakage.cfdproj'))

  function reread(owner: string): SeedSurface {
    const surface = collectSeedSurfaces(store.getProject()).find((s) => s.owner === owner)
    return surface as SeedSurface
  }

  it('writes an opening override into geometry.openings[].seedConfig', () => {
    setSeedOffset(floorOpeningSurface(store.getProject()!), 0.2)
    const openings = (
      store.getProject()?.constructions?.floors?.['m_51']?.geometry as {
        openings?: { patchId: string; seedConfig?: { offset: number } }[]
      }
    ).openings
    expect(openings?.[0]?.patchId).toBe('p_13')
    expect(openings?.[0]?.seedConfig?.offset).toBeCloseTo(0.2, 10)

    const after = reread('floor-opening')
    expect(after.seedConfig.offset).toBeCloseTo(0.2, 10)
    expect(after.isCustom).toBe(true)
  })

  it('clamps an offset below a quarter of the local cell', () => {
    // Below that the two seeds sit inside the same cell as the surface, so the
    // mesher cannot tell them apart and keeps one region instead of two.
    setSeedOffset(floorOpeningSurface(store.getProject()!), 0.0001)
    expect(reread('floor-opening').seedConfig.offset).toBeCloseTo(0.02125, 10)
  })

  it('re-normalises the direction after one component is changed', () => {
    setSeedDirectionComponent(floorOpeningSurface(store.getProject()!), 'x', 1)
    const dir = reread('floor-opening').seedConfig.direction
    expect(Math.hypot(dir.x, dir.y, dir.z)).toBeCloseTo(1, 10)
    expect(dir.x).toBeCloseTo(Math.SQRT1_2, 6)
    expect(dir.y).toBeCloseTo(Math.SQRT1_2, 6)
  })

  it('refuses a direction of length zero', () => {
    const surface = floorOpeningSurface(store.getProject()!)
    expect(setSeedDirectionComponent(surface, 'y', 0)).toBe(false)
    expect(reread('floor-opening').seedConfig.direction).toEqual({ x: 0, y: 1, z: 0 })
  })

  it('files a baffle wall-region override under its solid name', () => {
    const surface = reread('baffle-default')
    setSeedOffset(surface, 0.15)
    const cooling = store.getProject()?.constructions?.baffles?.[surface.modelUid]?.cooling as {
      wallRegionSeedConfigs?: Record<string, { offset: number }>
      seedConfig?: { offset: number }
    }
    expect(cooling.wallRegionSeedConfigs?.[surface.solidName]?.offset).toBeCloseTo(0.15, 10)
    expect(cooling.seedConfig?.offset).toBeCloseTo(0.15, 10)
  })

  it('reset removes the stored block, not just its values', () => {
    // A leftover `seedConfig: {...}` equal to the default still exports as an
    // explicit seed, so "reset" has to delete it.
    setSeedOffset(floorOpeningSurface(store.getProject()!), 0.2)
    resetSeedConfig(reread('floor-opening'))
    const openings = (
      store.getProject()?.constructions?.floors?.['m_51']?.geometry as {
        openings?: Record<string, unknown>[]
      }
    ).openings
    expect(openings?.[0]).not.toHaveProperty('seedConfig')
    expect(reread('floor-opening').isCustom).toBe(false)
  })

  it('is one undo step per edit', () => {
    const before = store.undoDepth()
    setSeedOffset(floorOpeningSurface(store.getProject()!), 0.2)
    expect(store.undoDepth()).toBe(before + 1)
    store.undo()
    expect(reread('floor-opening').seedConfig.offset).toBeCloseTo(0.0425, 10)
  })
})

describe('patch surfaces', () => {
  it('refuses a floor opening that is a plain wall', () => {
    const project = load()
    const floor = project.constructions?.floors?.['m_51']!
    const openings = (floor.geometry as { openings: { type: string }[] }).openings
    openings[0]!.type = 'wall'
    expect(seedSurfaceForPatch(project, floor, 'floors', 'top', 'p_13')).toBeNull()
  })
})
