import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import {
  _resetLiquidUidCounter,
  manifoldEndpoint,
  nextLiquidUid,
  pipeJunctionSeed,
  pipeSegmentSeed,
} from '../../src/legacy/liquidGlobals'

/** The liquid rack from case/LiquidSingle, trimmed to what the endpoint reads. */
function liquidRack(over: Record<string, unknown> = {}): Construction {
  const rack = {
    uid: 'm_2',
    meta: { name: 'Rack_1', enabled: true },
    geometry: {
      location: { x: 0, y: 0, z: 0 },
      size: { x: 0.6, y: 1.8669000000000002, z: 1.2 },
      quaternion: { x: 0, y: 1, z: 0, w: 0 },
      slot: 42,
      slotHeight: 0.04233333333333334,
    },
    cooling: {
      type: 'liquid',
      liquidManifold: {
        layout: 'split',
        supplyPipe: { diameter: 0.025, offsetFromInlet: 0.1 },
        returnPipe: { diameter: 0.025, offsetFromInlet: 0.1 },
        pipeSpacing: 0.015,
      },
    },
    safeMarginBottom: 0.04445,
    ...over,
  }
  Object.defineProperty(rack, 'kind', { value: 'rack', enumerable: false })
  return rack as unknown as Construction
}

function emptyProject(uids: string[] = []): CfdProject {
  const pipeSegments: Record<string, unknown> = {}
  for (const uid of uids) pipeSegments[uid] = { uid, meta: { name: uid } }
  return { constructions: { pipeSegments } } as unknown as CfdProject
}

describe('manifoldEndpoint', () => {
  it('places the split layout at the two rack edges, yawed by the rack quaternion', () => {
    const rack = liquidRack()
    // 42 slots x 0.042333 m = 1.778 m of usable height; the top of the manifold
    // sits that far above the bottom safe margin.
    const top = manifoldEndpoint(rack, 'supply', 'top')
    // The rack's quaternion is a 180 degree yaw, so local +x and +z both flip.
    expect(top?.x).toBeCloseTo(-0.0225, 9)
    expect(top?.y).toBeCloseTo(1.82245, 9)
    expect(top?.z).toBeCloseTo(0.05, 9)

    const bottom = manifoldEndpoint(rack, 'supply', 'bottom')
    expect(bottom?.y).toBeCloseTo(0.04445, 9)

    const ret = manifoldEndpoint(rack, 'return', 'top')
    expect(ret?.x).toBeCloseTo(-(0.6 - 0.01 - 0.0125), 9)
  })

  it('stacks both pipes on one side for the both-left / both-right layouts', () => {
    const left = liquidRack({
      geometry: {
        location: { x: 0, y: 0, z: 0 },
        size: { x: 0.6, y: 2, z: 1.2 },
        quaternion: { x: 0, y: 0, z: 0, w: 1 },
        slot: 10,
        slotHeight: 0.1,
      },
      cooling: {
        type: 'liquid',
        liquidManifold: {
          layout: 'both-left',
          supplyPipe: { diameter: 0.025 },
          returnPipe: { diameter: 0.025 },
          pipeSpacing: 0.015,
        },
      },
    })
    expect(manifoldEndpoint(left, 'supply', 'top')?.x).toBeCloseTo(0.0225, 9)
    // supplyX + supplyR + spacing + returnR
    expect(manifoldEndpoint(left, 'return', 'top')?.x).toBeCloseTo(0.0625, 9)

    const right = liquidRack({
      geometry: {
        location: { x: 0, y: 0, z: 0 },
        size: { x: 0.6, y: 2, z: 1.2 },
        quaternion: { x: 0, y: 0, z: 0, w: 1 },
        slot: 10,
        slotHeight: 0.1,
      },
      cooling: {
        type: 'liquid',
        liquidManifold: {
          layout: 'both-right',
          supplyPipe: { diameter: 0.025 },
          returnPipe: { diameter: 0.025 },
          pipeSpacing: 0.015,
        },
      },
    })
    expect(manifoldEndpoint(right, 'return', 'top')?.x).toBeCloseTo(0.5775, 9)
    expect(manifoldEndpoint(right, 'supply', 'top')?.x).toBeCloseTo(0.5375, 9)
  })

  it('mounts on the outlet face when asked, 50 mm clear of it', () => {
    const rack = liquidRack({
      geometry: {
        location: { x: 1, y: 0, z: 2 },
        size: { x: 0.6, y: 2, z: 1.2 },
        quaternion: { x: 0, y: 0, z: 0, w: 1 },
        slot: 10,
        slotHeight: 0.1,
      },
      cooling: {
        type: 'liquid',
        liquidManifold: { layout: 'split', mountFace: 'outlet', pipeSpacing: 0.015 },
      },
    })
    expect(manifoldEndpoint(rack, 'supply', 'top')?.z).toBeCloseTo(2 + 1.2 + 0.05, 9)
  })

  it('returns null for anything that is not a liquid rack with U-slots', () => {
    expect(manifoldEndpoint(null, 'supply', 'top')).toBeNull()
    const air = liquidRack({ cooling: { type: 'air' } })
    expect(manifoldEndpoint(air, 'supply', 'top')).toBeNull()
    const noSlots = liquidRack({
      geometry: {
        location: { x: 0, y: 0, z: 0 },
        size: { x: 0.6, y: 2, z: 1.2 },
        quaternion: { x: 0, y: 0, z: 0, w: 1 },
        slot: 0,
        slotHeight: 0,
      },
    })
    expect(manifoldEndpoint(noSlots, 'supply', 'top')).toBeNull()
    // The legacy reads `rackModel.cooling.type` bare; a rack without one throws
    // there and takes every other snap target down with it.
    const noCooling = liquidRack()
    delete (noCooling as unknown as Record<string, unknown>).cooling
    expect(manifoldEndpoint(noCooling, 'supply', 'top')).toBeNull()
  })
})

describe('nextLiquidUid', () => {
  beforeEach(() => _resetLiquidUidCounter())

  it('does not repeat itself while the project is unchanged', () => {
    const project = emptyProject(['m_3', 'm_4'])
    // _upgradeToTBranch builds three models before adding any of them.
    expect([nextLiquidUid(project), nextLiquidUid(project), nextLiquidUid(project)]).toEqual([
      'm_5',
      'm_6',
      'm_7',
    ])
  })

  it('still clears a project that grew past the counter', () => {
    const project = emptyProject(['m_3'])
    expect(nextLiquidUid(project)).toBe('m_4')
    expect(nextLiquidUid(emptyProject(['m_3', 'm_40']))).toBe('m_41')
  })
})

describe('the pipe factories', () => {
  beforeEach(() => _resetLiquidUidCounter())

  it('seeds a 25 mm pipe with no waypoints, as createPipeSegmentModel does', () => {
    const seed = pipeSegmentSeed(emptyProject(), 'Pipe_A', 'm_1.supply', 'm_9.port_1', 'return')
    expect(seed.kind).toBe('pipe_segment')
    expect(seed.showAnchorMarker).toBe(false)
    const pipe = seed.pipe as Record<string, unknown>
    expect(pipe.spec).toBe('pipe_25mm')
    expect(pipe.diameter).toBe(0.025)
    expect(pipe.waypoints).toEqual([])
    expect(pipe.startPortId).toBe('m_1.supply')
    expect(pipe.endPortId).toBe('m_9.port_1')
    expect(pipe.color).toBe('return')
  })

  it('seeds a 60 mm T-branch at the waypoint it was split from', () => {
    const seed = pipeJunctionSeed(emptyProject(), 'TBranch_Pipe_1_2', 'm_3', {
      x: 1,
      y: 2,
      z: 3,
    })
    expect(seed.kind).toBe('pipe_junction')
    expect(seed.anchor).toEqual({ x: 1, y: 2, z: 3 })
    expect(seed.base).toEqual({ W: 0.06, D: 0.06, H: 0.06 })
    expect(seed.parentPipeId).toBe('m_3')
    expect((seed.junction as Record<string, unknown>).type).toBe('T_branch')
  })
})
