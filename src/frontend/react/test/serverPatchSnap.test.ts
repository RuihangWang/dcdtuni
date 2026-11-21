import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import { serverPatchSnapDelta } from '../src/geometry/serverPatchSite'
import { applyPatchSnapRect } from '../src/geometry/faceDragMath'

/**
 * The inter-server vent snap: a server vent being dragged lines its edges up
 * with the vents of the OTHER servers in the same rack, as the legacy editor
 * did. It works in each server face's 0–1 fraction space, and its tolerance is
 * one world distance (3 % of the face width) so a 1U face — wide but short —
 * still snaps vertically.
 */

type Rect = { x: number; y: number; w: number; h: number }

function rack(servers: Record<string, Record<string, Array<Partial<Rect> & { id: string; type: string }>>>) {
  const out: Record<string, unknown> = {}
  for (const [eid, faces] of Object.entries(servers)) {
    out[eid] = {
      faces: Object.fromEntries(Object.entries(faces).map(([fid, patches]) => [fid, { patches }])),
    }
  }
  return { kind: 'rack', constructions: { servers: out } } as unknown as Construction
}

// A 1U front face: 0.48 m wide, 0.0445 m tall.
const ONE_U = { faceW: 0.48, faceH: 0.0445 }

describe('serverPatchSnapDelta', () => {
  const model = rack({
    e_1: { front: [{ id: 'p_1', type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 }] },
    e_2: {
      front: [{ id: 'p_2', type: 'rect', x: 0.5, y: 0.25, w: 0.2, h: 0.5 }],
      back: [{ id: 'p_3', type: 'circle' }],
    },
  })

  it('pulls a moved vent onto the nearest edge of another server’s vent, per axis', () => {
    const { dx, dy } = serverPatchSnapDelta(model, {
      equipmentId: 'e_1',
      rect: { x: 0.51, y: 0.21, w: 0.3, h: 0.4 },
      isResize: false,
      edge: null,
      ...ONE_U,
    })
    expect(dx).toBeCloseTo(-0.01, 12) // left edge onto x = 0.5
    // y: 0.21 → 0.25 is 0.04 away; the Y tolerance is 0.03·0.48/0.0445 ≈ 0.324.
    expect(dy).toBeCloseTo(0.04, 12)
  })

  it('scales the vertical tolerance with the face aspect, so a 1U face still snaps up/down', () => {
    const rect = { x: 0.9, y: 0.38, w: 0.05, h: 0.2 } // x far from every target
    const tall = serverPatchSnapDelta(model, {
      equipmentId: 'e_1', rect, isResize: false, edge: null, faceW: 0.48, faceH: 0.48,
    })
    expect(tall.dy).toBe(0) // 0.13 away with a 0.03 tolerance: no snap on a square face
    const short = serverPatchSnapDelta(model, {
      equipmentId: 'e_1', rect, isResize: false, edge: null, ...ONE_U,
    })
    expect(short.dy).toBeCloseTo(-0.13, 12) // bottom 0.38 onto 0.25, inside the ≈0.324 1U tolerance
    expect(short.dx).toBe(0)
  })

  it('ignores the dragged server’s own vents and circles', () => {
    const lonely = rack({
      e_1: { front: [{ id: 'p_1', type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 }] },
      e_2: { front: [{ id: 'p_9', type: 'circle', x: 0.11, y: 0.21 }] },
    })
    const { dx, dy } = serverPatchSnapDelta(lonely, {
      equipmentId: 'e_1', rect: { x: 0.105, y: 0.205, w: 0.3, h: 0.4 }, isResize: false, edge: null, ...ONE_U,
    })
    expect([dx, dy]).toEqual([0, 0])
  })

  it('moves only the dragged edge on a resize', () => {
    const rect = { x: 0.2, y: 0.1, w: 0.28, h: 0.3 }
    const right = serverPatchSnapDelta(model, {
      equipmentId: 'e_1', rect, isResize: true, edge: 'right', ...ONE_U,
    })
    expect(right.dx).toBeCloseTo(0.02, 12) // right edge 0.48 → 0.5
    expect(right.dy).toBe(0)
    const snapped = applyPatchSnapRect(rect, true, 'right', right.dx, right.dy)
    expect(snapped.x).toBe(0.2)
    expect(snapped.x + snapped.w).toBeCloseTo(0.5, 12)
  })

  it('stays put beyond the tolerance', () => {
    const { dx, dy } = serverPatchSnapDelta(model, {
      equipmentId: 'e_1', rect: { x: 0.0, y: 0.0, w: 0.05, h: 0.05 }, isResize: false, edge: null,
      faceW: 0.48, faceH: 0.48,
    })
    expect([dx, dy]).toEqual([0, 0])
  })

  it('uses the legacy defaults for a target without a size', () => {
    const sizeless = rack({
      e_1: {},
      e_2: { front: [{ id: 'p_2', type: 'rect' }] }, // reads as x 0..0.5, y 0..0.5
    })
    const { dx } = serverPatchSnapDelta(sizeless, {
      equipmentId: 'e_1', rect: { x: 0.51, y: 0.9, w: 0.1, h: 0.05 }, isResize: false, edge: null,
      faceW: 0.48, faceH: 0.48,
    })
    expect(dx).toBeCloseTo(-0.01, 12)
  })
})
