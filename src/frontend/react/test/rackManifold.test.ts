import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {
  isLiquidCooled,
  manifoldView,
  setManifoldLayout,
  setManifoldMountFace,
  setManifoldParam,
} from '../src/geometry/rackManifold'

function project(coolingType = 'liquid'): CfdProject {
  return reviveKinds({
    name: 'T',
    constructions: {
      racks: {
        m_1: {
          uid: 'm_1',
          meta: { name: 'Rack_1', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 0.6, y: 2, z: 1.2 },
            slot: 42,
            slotHeight: 0.04445,
            slotWidthRatio: 0.8,
            faces: [],
          },
          cooling: { type: coolingType },
        },
      },
    },
  } as unknown as CfdProject)
}

function rack(): Construction {
  return store.getProject()!.constructions!.racks!.m_1!
}

function manifold(): Record<string, unknown> {
  return (rack() as Construction & { cooling: Record<string, unknown> }).cooling
    .liquidManifold as Record<string, unknown>
}

beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

describe('isLiquidCooled', () => {
  it('is the gate the manifold section is shown behind', () => {
    expect(isLiquidCooled(rack())).toBe(true)
    store.loadProject(project('air'), '/tmp/T.cfdproj')
    expect(isLiquidCooled(rack())).toBe(false)
    expect(isLiquidCooled(null)).toBe(false)
  })
})

describe('manifoldView', () => {
  it('shows the legacy defaults for a rack that has no manifold block yet', () => {
    // `split`, not `both-left`: the legacy panel materialises the default block
    // before it renders, so that is the layout its box shows.
    expect(manifoldView(rack())).toEqual({
      layout: 'split',
      mountFace: 'inlet',
      diameterMm: 25,
      offsetMm: 100,
    })
  })

  it('keeps the legacy fallback for a block that exists without a layout', () => {
    const bare = {
      uid: 'x',
      meta: { name: 'x' },
      cooling: { type: 'liquid', liquidManifold: { mountFace: 'inlet' } },
    } as unknown as Construction
    expect(manifoldView(bare).layout).toBe('both-left')
  })

  it('reports millimetres for a block stored in metres', () => {
    setManifoldParam('m_1', 'diameter', 32)
    setManifoldParam('m_1', 'offset', 150)
    const view = manifoldView(rack())
    expect(view.diameterMm).toBeCloseTo(32, 9)
    expect(view.offsetMm).toBeCloseTo(150, 9)
  })

  it('treats any mount face other than outlet as inlet, as the writer does', () => {
    setManifoldMountFace('m_1', 'nonsense')
    expect(manifoldView(rack()).mountFace).toBe('inlet')
    setManifoldMountFace('m_1', 'outlet')
    expect(manifoldView(rack()).mountFace).toBe('outlet')
  })
})

describe('writers', () => {
  it('creates the manifold block on first write, with the shared defaults', () => {
    setManifoldLayout('m_1', 'both-right')
    expect(manifold()).toMatchObject({
      layout: 'both-right',
      mountFace: 'inlet',
      supplyPipe: { diameter: 0.025, offsetFromInlet: 0.1 },
      returnPipe: { diameter: 0.025, offsetFromInlet: 0.1 },
      pipeSpacing: 0.015,
    })
  })

  it('stores millimetres as metres', () => {
    setManifoldParam('m_1', 'diameter', 40)
    expect((manifold().supplyPipe as { diameter: number }).diameter).toBeCloseTo(0.04, 9)
  })

  it('writes diameter and offset to BOTH pipes', () => {
    // One box drives the pair; leaving the return side alone would send the
    // return run through a different bore than the supply.
    setManifoldParam('m_1', 'diameter', 32)
    setManifoldParam('m_1', 'offset', 60)
    const supply = manifold().supplyPipe as { diameter: number; offsetFromInlet: number }
    const ret = manifold().returnPipe as { diameter: number; offsetFromInlet: number }
    expect(ret.diameter).toBeCloseTo(supply.diameter, 12)
    expect(ret.offsetFromInlet).toBeCloseTo(supply.offsetFromInlet, 12)
  })

  it('ignores a non-numeric commit rather than storing NaN', () => {
    setManifoldParam('m_1', 'diameter', Number.NaN)
    expect(manifoldView(rack()).diameterMm).toBe(25)
  })

  it('is undoable, one entry per write', () => {
    setManifoldLayout('m_1', 'split')
    setManifoldLayout('m_1', 'both-right')
    expect(manifoldView(rack()).layout).toBe('both-right')
    store.undo()
    expect(manifoldView(rack()).layout).toBe('split')
  })

  it('does nothing for a uid that is not a rack', () => {
    setManifoldLayout('m_nope', 'both-right')
    expect(manifoldView(rack()).layout).toBe('split')
  })
})
