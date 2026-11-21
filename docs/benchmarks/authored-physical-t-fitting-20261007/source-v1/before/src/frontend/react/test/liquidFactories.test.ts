import { describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import {
  buildColdPlate,
  buildCrh,
  buildHeatSource,
  coldPlateLocation,
  crhPlacement,
  findCrhForRack,
  heatSourceLocation,
  rackServer,
} from '../src/liquid/liquidFactories'
import { reviveKinds } from '../src/legacy/cfdprojModules'

function rack(): Construction {
  return {
    uid: 'm_1',
    meta: { name: 'Rack_1', enabled: true },
    geometry: {
      location: { x: 1, y: 0.2, z: 3 },
      size: { x: 0.6, y: 2, z: 1.2 },
      slotHeight: 0.04445,
    } as Construction['geometry'],
    constructions: {
      servers: {
        s_1: { uid: 's_1', meta: { name: 'Srv_A' }, geometry: { slotPosition: 10 } },
        s_2: { uid: 's_2', meta: {}, geometry: {} },
      },
    },
  } as unknown as Construction
}

function project(): CfdProject {
  return reviveKinds({
    name: 'T',
    constructions: { racks: { m_1: rack() }, boxes: {} },
  } as unknown as CfdProject)
}

describe('placement', () => {
  it('puts the CRH on the rack back face and sizes it to the rack', () => {
    // A CRH built at the factory default 0.6 x 2.0 would hang off a wide rack.
    const p = crhPlacement(rack())
    expect(p.location).toEqual({ x: 1, y: 0.2, z: 4.2 })
    expect(p.size).toEqual({ x: 0.6, y: 2, z: 0.02 })
  })

  it('places the cold plate mid-height and mid-depth', () => {
    expect(coldPlateLocation(rack())).toEqual({ x: 1.05, y: 0.7, z: 3.1 })
  })

  it('lifts the heat source to its own slot, not to the rack floor', () => {
    const at10 = heatSourceLocation(rack(), 10)
    const at0 = heatSourceLocation(rack(), 0)
    expect(at10.y - at0.y).toBeCloseTo(10 * 0.04445, 10)
    expect(at10).toEqual({ x: 1.1, y: 0.2 + 10 * 0.04445 + 0.03, z: 3.15 })
  })

  it('falls back to the 1U default when the rack carries no slot height', () => {
    const bare = { uid: 'm_9', meta: { name: 'R' }, geometry: { location: { x: 0, y: 0, z: 0 } } } as
      unknown as Construction
    expect(heatSourceLocation(bare, 2).y).toBeCloseTo(2 * 0.04445 + 0.03, 10)
  })
})

describe('rackServer', () => {
  it('reads the slot index from geometry.slotPosition', () => {
    expect(rackServer(rack(), 's_1')).toEqual({ uid: 's_1', name: 'Srv_A', startUnit: 10 })
  })

  it('names an unnamed server by its uid rather than "undefined"', () => {
    expect(rackServer(rack(), 's_2')).toEqual({ uid: 's_2', name: 's_2', startUnit: 0 })
  })

  it('returns null for a server that is not on the rack', () => {
    expect(rackServer(rack(), 'nope')).toBeNull()
  })
})

describe('buildCrh', () => {
  it('produces a V4 box-bucket model tagged crh, parented to the rack', () => {
    const p = project()
    const { bucket, model } = buildCrh(p, p.constructions!.racks!.m_1!)
    expect(bucket).toBe('boxes')
    expect(model.kind).toBe('crh')
    expect((model.meta as { legacyKind?: string }).legacyKind).toBe('crh')
    expect(model.meta.name).toBe('CRH_Rack_1')
    expect(model.parentRackId).toBe('m_1')
    expect(model.geometry?.location).toEqual({ x: 1, y: 0.2, z: 4.2 })
    expect(model.geometry?.size).toEqual({ x: 0.6, y: 2, z: 0.02 })
  })

  it('carries the four CRH performance fields the graph generator reads', () => {
    const p = project()
    const { model } = buildCrh(p, p.constructions!.racks!.m_1!)
    expect(model.crh).toEqual({
      effectiveness: 0.5,
      waterFlowRate: 20,
      waterInletTemp: 25,
      heatRejectionCapacity: 30000,
    })
  })

  it('is translucent and carries no anchor marker', () => {
    const p = project()
    const { model } = buildCrh(p, p.constructions!.racks!.m_1!)
    expect(model.meta.display).toEqual({ opacity: 0.4, showAnchorMarker: false })
  })

  it('takes the next free uid rather than restarting at m_1', () => {
    const p = project()
    const { model } = buildCrh(p, p.constructions!.racks!.m_1!)
    expect(model.uid).toBe('m_2')
  })
})

describe('findCrhForRack', () => {
  it('finds a CRH by its parent rack, and only that rack', () => {
    const p = project()
    const { bucket, model } = buildCrh(p, p.constructions!.racks!.m_1!)
    p.constructions![bucket]![model.uid] = model
    expect(findCrhForRack(p, 'm_1')?.uid).toBe(model.uid)
    expect(findCrhForRack(p, 'm_other')).toBeNull()
  })
})

describe('buildColdPlate', () => {
  it('stamps the clock into the name, so two on one rack do not collide', () => {
    const p = project()
    const { model } = buildColdPlate(p, p.constructions!.racks!.m_1!, 1234)
    expect(model.meta.name).toBe('CP_Rack_1_1234')
  })

  it('carries the cold-plate spec block and both port slots', () => {
    const p = project()
    const { model } = buildColdPlate(p, p.constructions!.racks!.m_1!, 1)
    expect(model.kind).toBe('cold_plate')
    expect(model.coldPlate).toEqual({
      specName: 'cp_default',
      chipTDP: 350,
      thermalModel: { chipArea: 0.004, chipThickness: 0.001, thermalConductivity: 148 },
      inletPortId: null,
      outletPortId: null,
    })
    expect(model.parentRackId).toBe('m_1')
    expect(model.parentServerId).toBeNull()
    expect(model.geometry?.size).toEqual({ x: 0.1, y: 0.01, z: 0.1 })
  })
})

describe('buildHeatSource', () => {
  it('splits the legacy heatSource block into cooling, as the migrator does', () => {
    // `heatMode` is absent, so the migrator picks `power`; the remaining three
    // fields are copied onto `cooling` verbatim and read from there.
    const p = project()
    const server = rackServer(p.constructions!.racks!.m_1!, 's_1')!
    const { model } = buildHeatSource(p, p.constructions!.racks!.m_1!, server)
    expect(model.kind).toBe('heat_source')
    expect(model.meta.name).toBe('Heat_Srv_A')
    expect(model.cooling).toMatchObject({
      boundaryMode: 'power',
      power: 350,
      chipArea: 0.004,
      thermalMass: 60,
    })
    expect(model.heatSource).toBeUndefined()
    expect(model.parentServerId).toBe('s_1')
  })

  it('sits at the server slot, so two servers do not get the same height', () => {
    const p = project()
    const a = buildHeatSource(p, p.constructions!.racks!.m_1!, rackServer(p.constructions!.racks!.m_1!, 's_1')!)
    const b = buildHeatSource(p, p.constructions!.racks!.m_1!, rackServer(p.constructions!.racks!.m_1!, 's_2')!)
    expect(a.model.geometry!.location!.y).toBeGreaterThan(b.model.geometry!.location!.y)
  })
})

describe('shape parity with the plain factories', () => {
  it('gives every liquid kind the lazily-materialised rotateTool', () => {
    // The legacy app adds it on the first inspector render, which for these
    // kinds is immediate — they are selected the moment they are created.
    const p = project()
    const r = p.constructions!.racks!.m_1!
    for (const built of [buildCrh(p, r), buildColdPlate(p, r, 1), buildHeatSource(p, r, rackServer(r, 's_1')!)]) {
      expect(built.model.rotateTool).toEqual({
        enabled: false,
        axes: { yaw: true, pitch: false, roll: false },
      })
    }
  })

  it('keeps `kind` off the serialised document', () => {
    // V4 derives kind from the bucket plus meta.legacyKind; a `kind` key in the
    // file would be a second, drifting source of truth.
    const p = project()
    const { model } = buildCrh(p, p.constructions!.racks!.m_1!)
    expect(Object.keys(JSON.parse(JSON.stringify(model)))).not.toContain('kind')
  })
})
