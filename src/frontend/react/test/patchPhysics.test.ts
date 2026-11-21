import { describe, expect, it } from 'vitest'
import { PERFORATED_SHAPE_PHI, perforatedK , syncAcuReturnFlow, syncAcuSupplyReturnFaces } from '../src/geometry/patchPhysics'

/**
 * The K estimate is what a perforated baffle becomes in the case, so these
 * check the arithmetic against the legacy formula rather than against a
 * screenshot of it.
 */
describe('perforatedK', () => {
  it('matches the legacy value for the default 64% circular perforation', () => {
    // (1 + 1.00·0.707·√0.36 - 0.64)² / 0.64² — the number the legacy inspector
    // prints for a fresh perforated patch.
    const sigma = 0.64
    const expected = Math.pow(1 + 0.707 * Math.sqrt(1 - sigma) - sigma, 2) / (sigma * sigma)
    expect(perforatedK({ shape: 'circle', openAreaPct: 64 })).toBeCloseTo(expected, 12)
  })

  it('rises as the open area falls', () => {
    // A tighter plate resists more. If this inverted, a user tightening a floor
    // tile would see the pressure drop go DOWN.
    const k80 = perforatedK({ openAreaPct: 80 })
    const k40 = perforatedK({ openAreaPct: 40 })
    const k10 = perforatedK({ openAreaPct: 10 })
    expect(k40).toBeGreaterThan(k80)
    expect(k10).toBeGreaterThan(k40)
  })

  it('penalises sharper hole shapes', () => {
    const circle = perforatedK({ shape: 'circle', openAreaPct: 50 })
    const rect = perforatedK({ shape: 'rect', openAreaPct: 50 })
    const diamond = perforatedK({ shape: 'diamond', openAreaPct: 50 })
    expect(rect).toBeGreaterThan(circle)
    expect(diamond).toBeGreaterThan(rect)
    expect(PERFORATED_SHAPE_PHI).toEqual({ circle: 1.0, rect: 1.1, diamond: 1.15, hexagon: 1.05 })
  })

  it('stays finite at both singular ends', () => {
    // sigma is clamped: 0% would divide by zero and 100% is not a plate. Both
    // are reachable by typing in the box.
    expect(Number.isFinite(perforatedK({ openAreaPct: 0 }))).toBe(true)
    expect(Number.isFinite(perforatedK({ openAreaPct: 100 }))).toBe(true)
    expect(perforatedK({ openAreaPct: 0 })).toBeGreaterThan(perforatedK({ openAreaPct: 99 }))
  })

  it('falls back to the 64% default when the field is missing', () => {
    expect(perforatedK(undefined)).toBeCloseTo(perforatedK({ openAreaPct: 64 }), 12)
    expect(perforatedK({})).toBeCloseTo(perforatedK({ shape: 'circle', openAreaPct: 64 }), 12)
  })

  it('treats an unknown shape as circular rather than as zero', () => {
    // phi 0 would make K a different formula entirely.
    expect(perforatedK({ shape: 'octagon' as never, openAreaPct: 50 })).toBeCloseTo(
      perforatedK({ shape: 'circle', openAreaPct: 50 }),
      12,
    )
  })
})

describe('syncAcuReturnFlow', () => {
  const acu = (patches: { role: string; volumeFlowRate?: number }[]) => ({
    geometry: {
      faces: {
        front: {
          patches: patches.map((p, i) => ({ id: `p${i}`, physics: { ...p } })),
        },
      },
    },
  })

  it('splits the supply total evenly among the return patches', () => {
    // An ACU pushes what it takes; the return flow is derived, not independent.
    const model = acu([
      { role: 'supply', volumeFlowRate: 4 },
      { role: 'supply', volumeFlowRate: 2 },
      { role: 'return', volumeFlowRate: 0 },
      { role: 'return', volumeFlowRate: 99 },
    ])
    expect(syncAcuReturnFlow(model)).toBe(3)
    const patches = model.geometry.faces.front.patches
    expect(patches[2]!.physics.volumeFlowRate).toBe(3)
    // The stale 99 is overwritten: a stored number the exporter reads cannot be
    // left disagreeing with the supply it mirrors.
    expect(patches[3]!.physics.volumeFlowRate).toBe(3)
  })

  it('leaves supply patches alone', () => {
    const model = acu([{ role: 'supply', volumeFlowRate: 5 }, { role: 'return' }])
    syncAcuReturnFlow(model)
    expect(model.geometry.faces.front.patches[0]!.physics.volumeFlowRate).toBe(5)
  })

  it('does nothing when there are no return patches', () => {
    const model = acu([{ role: 'supply', volumeFlowRate: 5 }])
    expect(syncAcuReturnFlow(model)).toBe(0)
  })

  it('reads an ARRAY of faces as well as the keyed object', () => {
    // Racks carry `geometry.faces` as an array; walking only the object shape
    // would silently skip them.
    const model = {
      geometry: {
        faces: [
          { patches: [{ id: 'a', physics: { role: 'supply', volumeFlowRate: 8 } }] },
          { patches: [{ id: 'b', physics: { role: 'return', volumeFlowRate: 0 } }] },
        ],
      },
    }
    expect(syncAcuReturnFlow(model)).toBe(8)
  })

  it('treats a wall patch as neither', () => {
    const model = acu([
      { role: 'supply', volumeFlowRate: 6 },
      { role: 'wall', volumeFlowRate: 100 },
      { role: 'return' },
    ])
    expect(syncAcuReturnFlow(model)).toBe(6)
  })

  it('leaves a RETURN-ONLY unit\'s flow alone: there is no supply to mirror', () => {
    // A wall-mounted return brick or a ceiling grille has returns and no
    // supply; dividing a supply total of 0 would zero the number the user typed.
    const model = acu([{ role: 'return', volumeFlowRate: 0.6 }, { role: 'return', volumeFlowRate: 0.3 }])
    expect(syncAcuReturnFlow(model)).toBe(0)
    const patches = model.geometry.faces.front.patches
    expect(patches[0]!.physics.volumeFlowRate).toBe(0.6)
    expect(patches[1]!.physics.volumeFlowRate).toBe(0.3)
  })
})

describe('syncAcuSupplyReturnFaces on a floor', () => {
  it('mirrors return patches of a FLOOR (a ceiling grille) into returnFaces', () => {
    const model = {
      kind: 'floor',
      geometry: {
        faces: {
          top: {
            patches: [
              { id: 'p_15', physics: { role: 'return', volumeFlowRate: 0.3 } },
              { id: 'p_16' },
            ],
          },
        },
      },
    }
    syncAcuSupplyReturnFaces(model)
    const g = model.geometry as { supplyFaces?: unknown; returnFaces?: unknown }
    expect(g.returnFaces).toEqual([{ faceId: 'top', patchId: 'p_15' }])
    expect(g.supplyFaces).toEqual([])
  })

  it('still ignores every other kind', () => {
    const model = { kind: 'box', geometry: { faces: { top: { patches: [{ id: 'p', physics: { role: 'return' } }] } } } }
    syncAcuSupplyReturnFaces(model)
    expect((model.geometry as { returnFaces?: unknown }).returnFaces).toBeUndefined()
  })
})
