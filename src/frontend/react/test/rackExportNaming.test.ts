import { describe, expect, it } from 'vitest'

// Side-effect import: installs window.THREE / polygonClipping and loads the
// shared js modules (rack_state, stl_naming, geometry_pure, geometry_scene) the
// two builders below resolve lazily.
import '../src/legacy/geometryBuilder'
import { createCfdrunBuild, type CfdrunContext } from '../src/legacy/cfdrunBuild'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createRack } from '../src/legacy/factories'
import { stlNaming } from '../src/legacy/stlNaming'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

/**
 * Stacked servers in the export layer — the two places a second server at an
 * occupied U range has to stay distinguishable.
 *
 * Both are naming/manifest facts, not rendering ones, and both fail silently.
 * A shared solid stem merges two servers into ONE `solid` block: the backend's
 * uniqueness validator rejects the case, or — worse — the BC registry keeps
 * whichever server wrote last and the fan blows through the wrong box. A cfdrun
 * that carries the rack-relative `extraInletOffset` instead of the absolute
 * `inletOffsetM` describes a depth nobody meshes, because the STL surfaces are
 * already at the absolute one.
 *
 * Spec: docs/superpowers/specs/2026-09-17-rack-stacked-servers-design.md §8.
 */

/** The proxy shape `getRackServerViews` hands back per server record. */
interface ServerView {
  id: string
  name: string
  kind: string
  startUnit: number
  spanUnits: number
  inletOffset?: number
  outletOffset?: number
}

type ServerType = 'fanServer' | 'nonFan'

/**
 * A server record in the V4 shape, with the ABSOLUTE depth fields of §3.1.
 *
 * Written straight into the dict rather than through a writer: `syncRackModelState`
 * still enforces one server per U slot, so a normaliser round trip would delete
 * exactly the stacked pair these tests are about.
 */
function addServer(
  rack: Construction,
  uid: string,
  name: string,
  type: ServerType,
  slotPosition: number,
  slotOccupation: number,
  inletOffset: number,
  outletOffset: number
): void {
  const servers = (rack.constructions as { servers: Record<string, unknown> }).servers
  servers[uid] = {
    uid,
    type,
    geometry: { slotPosition, slotOccupation },
    cooling: {},
    constructions: { chips: {}, coldPlates: {} },
    meta: { name, enabled: true },
    inletOffset,
    outletOffset,
    liquidCooling: null,
  }
}

/** A real 42U rack (D = 1.2 m, row offsets 0) on a real project, plus its views. */
function rackWithServers(
  servers: [uid: string, name: string, type: ServerType, startU: number, spanU: number, inletOffset: number, outletOffset: number][]
): { project: CfdProject; rack: Construction; views: ServerView[] } {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  // A footprint keeps buildCfdrunJson off its scene-bbox fallback: the room and
  // the blockMesh bounds then come from the polygon, not from rendered meshes.
  project.geometry = { height: 3, plane: [[0, 0], [6, 0], [6, 4], [0, 4]] }
  const { model: rack } = createRack(project)
  ;(stateV4() as unknown as { addConstruction(p: unknown, m: unknown): void }).addConstruction(project, rack)
  for (const [uid, name, type, startU, spanU, inletOffset, outletOffset] of servers) {
    addServer(rack, uid, name, type, startU, spanU, inletOffset, outletOffset)
  }
  return {
    project,
    rack,
    views: stlNaming().getRackServerViews(rack) as unknown as ServerView[],
  }
}

function stemOf(rack: Construction, uid: string): string {
  const views = stlNaming().getRackServerViews(rack) as unknown as ServerView[]
  const view = views.find((v) => v.id === uid)
  if (!view) throw new Error(`no server "${uid}" on the rack`)
  return stlNaming().getRackEquipmentExportStem(rack, view) ?? ''
}

describe('server export stems', () => {
  it('separates two same-named servers stacked at the SAME U range by depth', () => {
    // The 5U fan server recessed from the outlet and the 5U block at the inlet
    // plane share U16-U20, so the U suffix is identical for both and only the
    // inlet setback can tell their solids apart.
    const { rack } = rackWithServers([
      ['srv_fan', 'Server', 'fanServer', 15, 5, 0.05, 0.6],
      ['srv_switch', 'Server', 'nonFan', 15, 5, 0.6, 0.05],
    ])
    const fan = stemOf(rack, 'srv_fan')
    const stacked = stemOf(rack, 'srv_switch')

    expect(fan).not.toBe(stacked)
    expect(fan).toMatch(/_z\d+$/)
    expect(stacked).toMatch(/_z\d+$/)
    // Millimetres of inlet setback, rounded: 0.05 m and 0.6 m.
    expect(fan).toBe(`${stlNaming().sanitizeName(rack.meta.name)}_Server_u16_u20_z50`)
    expect(stacked).toBe(`${stlNaming().sanitizeName(rack.meta.name)}_Server_u16_u20_z600`)
  })

  it('keeps the U suffix for same-named servers at DIFFERENT U ranges', () => {
    // The rule that shipped before stacked servers: two servers named alike but
    // placed apart are told apart by their U range, and adding a depth token
    // there would rename every existing case's solids for no reason.
    const { rack } = rackWithServers([
      ['srv_low', 'Server', 'fanServer', 0, 1, 0.05, 0.05],
      ['srv_high', 'Server', 'fanServer', 15, 5, 0.05, 0.6],
    ])
    const low = stemOf(rack, 'srv_low')
    const high = stemOf(rack, 'srv_high')

    expect(low).toBe(`${stlNaming().sanitizeName(rack.meta.name)}_Server_u1`)
    expect(high).toBe(`${stlNaming().sanitizeName(rack.meta.name)}_Server_u16_u20`)
    expect(low).not.toMatch(/_z/)
    expect(high).not.toMatch(/_z/)
  })

  it('leaves a uniquely named server on the bare stem', () => {
    const { rack } = rackWithServers([['srv_a', 'Fan_A', 'fanServer', 15, 5, 0.05, 0.6]])
    expect(stemOf(rack, 'srv_a')).toBe(`${stlNaming().sanitizeName(rack.meta.name)}_Fan_A`)
  })

  it('gives the stacked pair distinct face solid names, not just distinct stems', () => {
    // The stem is only useful if it reaches the solids: these strings become the
    // `solid` names in the STL and the patch names in polyMesh/boundary.
    const { rack } = rackWithServers([
      ['srv_fan', 'Server', 'fanServer', 15, 5, 0.05, 0.6],
      ['srv_switch', 'Server', 'nonFan', 15, 5, 0.6, 0.05],
    ])
    const fan = stemOf(rack, 'srv_fan')
    const stacked = stemOf(rack, 'srv_switch')
    const naming = stlNaming()
    const views = naming.getRackServerViews(rack) as unknown as ServerView[]
    const pure = (window as unknown as {
      geometryPure: {
        getServerFaceExportName(m: Construction, eq: unknown, faceId: string): string
      }
    }).geometryPure

    const fanInlet = pure.getServerFaceExportName(rack, views.find((v) => v.id === 'srv_fan'), 'inlet')
    const stackedFront = pure.getServerFaceExportName(rack, views.find((v) => v.id === 'srv_switch'), 'inlet')
    expect(fanInlet).toBe(`${fan}_inlet_patch`)
    expect(stackedFront).toBe(`${stacked}_front`)
    expect(fanInlet).not.toBe(stackedFront)
  })
})

describe('cfdrun server objects', () => {
  /** The v1 meshGeometry blob, built from a project holding one rack. */
  function buildBlob(project: CfdProject): Record<string, unknown> {
    const w = window as unknown as { allModels?: () => Construction[] }
    w.allModels = () => stateV4().allModels(project)
    const ctx = {
      state: project,
      globalGroup: { children: [] },
      LENGTH_UNITS: {
        mm: { scale: 0.001, label: 'mm', step: 0.1, precision: 4 },
        cm: { scale: 0.01, label: 'cm', step: 0.01, precision: 4 },
        m: { scale: 1, label: 'm', step: 0.0001, precision: 4 },
      },
      _MESH_MODES: ['bodyFitted', 'cartesianLocalized', 'structured'],
    } as unknown as CfdrunContext
    return createCfdrunBuild(ctx).buildCfdrunJson()
  }

  function exportedServers(blob: Record<string, unknown>, rackName: string): Record<string, unknown>[] {
    const racks = blob.racks as Record<string, { servers?: Record<string, unknown>[] }>
    const entry = racks[rackName]
    if (!entry) throw new Error(`no rack "${rackName}" in the exported blob`)
    return entry.servers ?? []
  }

  it('carries the absolute depth on BOTH branches and drops the relative extras', () => {
    const { project, rack } = rackWithServers([
      ['srv_fan', 'Server', 'fanServer', 15, 5, 0.05, 0.6],
      ['srv_switch', 'Server', 'nonFan', 15, 5, 0.6, 0.05],
    ])
    const blob = buildBlob(project)
    const servers = exportedServers(blob, stlNaming().sanitizeName(rack.meta.name))
    expect(servers.length).toBe(2)

    const fan = servers.find((s) => s.type === 'fanServer')
    const nonFan = servers.find((s) => s.type === 'nonFan')
    if (!fan || !nonFan) throw new Error('the rack exported without one of its two servers')

    // fanServer branch
    expect(fan.inletOffsetM).toBe(0.05)
    expect(fan.outletOffsetM).toBe(0.6)
    expect('extraInletOffset' in fan).toBe(false)
    expect('extraOutletOffset' in fan).toBe(false)

    // nonFan branch — the same pair, so the runner reads one shape either way
    expect(nonFan.inletOffsetM).toBe(0.6)
    expect(nonFan.outletOffsetM).toBe(0.05)
    expect('extraInletOffset' in nonFan).toBe(false)
    expect('extraOutletOffset' in nonFan).toBe(false)
  })

  it('exports the stacked pair under distinct ids', () => {
    // `id` IS the export stem, and the runner keys solid_to_span and the BC
    // registry by it: two servers sharing one id is a silent overwrite.
    const { project, rack } = rackWithServers([
      ['srv_fan', 'Server', 'fanServer', 15, 5, 0.05, 0.6],
      ['srv_switch', 'Server', 'nonFan', 15, 5, 0.6, 0.05],
    ])
    const servers = exportedServers(buildBlob(project), stlNaming().sanitizeName(rack.meta.name))
    const ids = servers.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toContain(`${stlNaming().sanitizeName(rack.meta.name)}_Server_u16_u20_z50`)
    expect(ids).toContain(`${stlNaming().sanitizeName(rack.meta.name)}_Server_u16_u20_z600`)
  })

  it('defaults a server with no depth fields to the rack planes', () => {
    // Records written before the absolute fields existed carry neither; the
    // manifest must still hold a number, not undefined, or the runner's schema
    // reads a hole where the depth should be.
    const { project, rack } = rackWithServers([['srv_a', 'Fan_A', 'fanServer', 0, 1, 0, 0]])
    const servers = (rack.constructions as { servers: Record<string, Record<string, unknown>> }).servers
    delete servers['srv_a']!.inletOffset
    delete servers['srv_a']!.outletOffset

    const exported = exportedServers(buildBlob(project), stlNaming().sanitizeName(rack.meta.name))
    expect(exported.length).toBe(1)
    expect(exported[0]!.inletOffsetM).toBe(0)
    expect(exported[0]!.outletOffsetM).toBe(0)
  })
})
