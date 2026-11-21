import { describe, expect, it } from 'vitest'
import type { Quat } from '../src/schema/cfdproj'
import { computeAnnotations, groupColumns, outletDirection } from '../src/room2d/room2dAnnotations'
import type { PlanModel } from '../src/room2d/room2dModels'

function yaw(deg: number): Quat {
  const half = ((deg * Math.PI) / 180) / 2
  return { x: 0, y: Math.sin(half), z: 0, w: Math.cos(half) }
}

function rack(uid: string, x: number, z: number, q: Quat = { x: 0, y: 0, z: 0, w: 1 }): PlanModel {
  return {
    uid,
    kind: 'rack',
    name: uid,
    enabled: true,
    isRoomBoundary: false,
    location: { x, y: 0, z },
    size: { x: 0.6, y: 2, z: 1.2 },
    quaternion: q,
    scale: { x: 1, y: 1, z: 1 },
    rotatable: false,
    movable: false,
    frontIsInlet: true,
  }
}

function baffle(x: number, z: number): PlanModel {
  return { ...rack('bf', x, z), kind: 'baffle', uid: 'bf' }
}

/**
 * Two rows of racks along X, three metres apart in Z.
 *
 * `back` (the far row) is turned 180 degrees, so both rows' outlets point INTO
 * the gap between them — the definition of a hot aisle.
 */
function twoRows(farYawDeg: number): PlanModel[] {
  const front = [0, 1, 2].map((i) => rack(`f${i}`, i * 0.6, 0))
  const back = [0, 1, 2].map((i) => rack(`b${i}`, i * 0.6, 3, yaw(farYawDeg)))
  return [...front, ...back]
}

describe('outletDirection', () => {
  it('is local +Z for an unrotated rack', () => {
    const d = outletDirection({ x: 0, y: 0, z: 0, w: 1 })
    expect(d.z).toBeCloseTo(1, 12)
    expect(d.x).toBeCloseTo(0, 12)
  })

  it('flips at 180 degrees, where w is zero', () => {
    const d = outletDirection({ x: 0, y: 1, z: 0, w: 0 })
    expect(d.z).toBeCloseTo(-1, 12)
  })
})

describe('groupColumns', () => {
  it('gathers racks within half a metre in X', () => {
    const cols = groupColumns([rack('a', 0, 0), rack('b', 0.3, 2), rack('c', 5, 0)])
    expect(cols).toHaveLength(2)
    expect(cols[0]!.racks.map((r) => r.uid)).toEqual(['a', 'b'])
    expect(cols[0]!.x).toBeCloseTo(0.15, 12)
  })
})

describe('computeAnnotations', () => {
  it('calls a gap the two rows blow into a HOT aisle', () => {
    const a = computeAnnotations(twoRows(180))
    expect(a.labels).toEqual(['HOT'])
    // Midway between the two rows' footprint CENTRES (0.6 and 2.4), not
    // between their anchors — a 180-degree rack's anchor is its far corner.
    expect(a.aisles[0]!.worldZ).toBeCloseTo(1.5, 6)
    // Rows run along X, so the aisle label is turned a quarter turn.
    expect(a.aisles[0]!.vertical).toBe(true)
  })

  it('calls the mirrored arrangement a COLD aisle', () => {
    // Front row turned away, far row facing away too: both draw FROM the gap.
    const models = [
      ...[0, 1, 2].map((i) => rack(`f${i}`, i * 0.6, 0, yaw(180))),
      ...[0, 1, 2].map((i) => rack(`b${i}`, i * 0.6, 3)),
    ]
    expect(computeAnnotations(models).labels).toEqual(['COLD'])
  })

  it('refuses to label a gap where both rows face the same way', () => {
    expect(computeAnnotations(twoRows(0)).labels).toEqual([])
  })

  it('refuses a gap wider than six metres', () => {
    const models = [
      ...[0, 1, 2].map((i) => rack(`f${i}`, i * 0.6, 0)),
      ...[0, 1, 2].map((i) => rack(`b${i}`, i * 0.6, 9, yaw(180))),
    ]
    expect(computeAnnotations(models).labels).toEqual([])
  })

  it('marks an aisle contained when a baffle sits within five metres', () => {
    const a = computeAnnotations([...twoRows(180), baffle(0.6, 1.8)])
    expect(a.labels).toEqual(['HOT_C'])
    expect(a.aisles[0]!.contained).toBe(true)
  })

  it('leaves a distant baffle out of it', () => {
    const a = computeAnnotations([...twoRows(180), baffle(40, 40)])
    expect(a.labels).toEqual(['HOT'])
  })

  it('counts racks rather than columns when deciding the facing axis', () => {
    // Two columns of racks facing along Z against ONE rack facing along X. By
    // columns it would be a tie decided by the rogue rack; by racks the Z rows
    // win and the aisle between them is still found.
    const models = [...twoRows(180), rack('rogue', 20, 20, yaw(90))]
    expect(computeAnnotations(models).labels).toEqual(['HOT'])
  })

  it('numbers the columns from the left', () => {
    const a = computeAnnotations(twoRows(180))
    expect(a.columns.map((c) => c.text)).toEqual(['C1', 'C2', 'C3'])
    expect(a.columns[0]!.worldX).toBeCloseTo(0.3, 12)
  })

  it('ignores disabled racks entirely', () => {
    const models = twoRows(180).map((m) =>
      m.uid.startsWith('b') ? { ...m, enabled: false } : m,
    )
    expect(computeAnnotations(models).labels).toEqual([])
  })

  it('reports nothing for a room with no racks', () => {
    expect(computeAnnotations([baffle(0, 0)]).labels).toEqual([])
    expect(computeAnnotations([]).dimension).toBeNull()
  })
})
