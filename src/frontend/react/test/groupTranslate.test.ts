import { describe, expect, it } from 'vitest'
import {
  applyPlacement,
  capturePlacement,
  followDelta,
  hasPipeGeometry,
  placementPoints,
  translateModel,
} from '../src/interaction/groupTranslate'
import type { Construction } from '../src/schema/cfdproj'

/** `kind` is non-enumerable on real models, but only its VALUE is read here. */
function model(kind: string, extra: Record<string, unknown> = {}): Construction {
  return {
    uid: `m_${kind}`,
    kind,
    geometry: { location: { x: 1, y: 2, z: 3 } },
    ...extra,
  } as unknown as Construction
}

function pipe(): Construction {
  return model('pipe_segment', {
    geometry: {
      location: { x: 0, y: 0.8, z: 0 },
      waypoints: [
        { x: 6.4, y: 0.57, z: 3.67 },
        { x: 1.01, y: 0.57, z: 3.67 },
      ],
    },
    cooling: { valves: [{ id: 'tee_1', position: { x: 7.44, y: 0.55, z: 3.12 } }] },
  })
}

describe('placementPoints', () => {
  it('is just the anchor for a rack', () => {
    expect(placementPoints(model('rack'))).toEqual([{ x: 1, y: 2, z: 3 }])
  })

  it('is the anchor, the waypoints and the valves for a pipe segment', () => {
    // The pipe is DRAWN from its waypoints, so a translation that touched only
    // the anchor would leave it exactly where it was — the defect that made a
    // gizmo drag and the typed minimum-point box disagree.
    expect(placementPoints(pipe())).toHaveLength(4)
  })

  it('returns the LIVE objects, so writing to them moves the model', () => {
    const m = model('rack')
    placementPoints(m)[0]!.x = 9
    expect((m.geometry as { location: { x: number } }).location.x).toBe(9)
  })

  it('ignores waypoints on anything that is not a pipe segment', () => {
    // The legacy gates on `kind === 'pipe_segment'`; a model that somehow
    // carries waypoints without being one is still moved by its anchor alone.
    const odd = model('box', {
      geometry: { location: { x: 0, y: 0, z: 0 }, waypoints: [{ x: 5, y: 5, z: 5 }] },
    })
    expect(placementPoints(odd)).toHaveLength(1)
  })

  it('survives a model with no geometry at all', () => {
    expect(placementPoints({ uid: 'x' } as unknown as Construction)).toEqual([])
    expect(placementPoints(null)).toEqual([])
  })
})

describe('translateModel', () => {
  it('moves a pipe segment WHOLE — anchor, waypoints and valves', () => {
    const p = pipe()
    translateModel(p, { x: 0.5, y: 0, z: 0 })
    const g = p.geometry as unknown as {
      location: { x: number }
      waypoints: { x: number }[]
    }
    expect(g.location.x).toBeCloseTo(0.5, 9)
    expect(g.waypoints[0]!.x).toBeCloseTo(6.9, 9)
    expect(g.waypoints[1]!.x).toBeCloseTo(1.51, 9)
    const valves = (p as unknown as { cooling: { valves: { position: { x: number } }[] } }).cooling
      .valves
    expect(valves[0]!.position.x).toBeCloseTo(7.94, 9)
  })

  it('leaves the axes it was not asked to move alone', () => {
    const p = pipe()
    translateModel(p, { x: 0, y: 0.3, z: 0 })
    const g = p.geometry as unknown as { waypoints: { x: number; y: number; z: number }[] }
    expect(g.waypoints[0]!.x).toBe(6.4)
    expect(g.waypoints[0]!.z).toBe(3.67)
    expect(g.waypoints[0]!.y).toBeCloseTo(0.87, 9)
  })
})

describe('applyPlacement', () => {
  it('is measured from the START, so repeating a frame does not compound it', () => {
    // A drag emits a position every frame. Adding the frame's delta to where
    // the model IS would move it sixty times per second's worth of delta.
    const p = pipe()
    const start = capturePlacement(p)
    applyPlacement(p, start, { x: 1, y: 0, z: 0 })
    applyPlacement(p, start, { x: 1, y: 0, z: 0 })
    const g = p.geometry as unknown as { location: { x: number }; waypoints: { x: number }[] }
    expect(g.location.x).toBeCloseTo(1, 9)
    expect(g.waypoints[0]!.x).toBeCloseTo(7.4, 9)
  })

  it('a zero delta puts the model exactly back where the drag started', () => {
    const p = pipe()
    const start = capturePlacement(p)
    applyPlacement(p, start, { x: 2.5, y: -1, z: 0.25 })
    applyPlacement(p, start, { x: 0, y: 0, z: 0 })
    expect(capturePlacement(p)).toEqual(start)
  })

  it('keeps a group rigid: two models stay the same distance apart', () => {
    const a = model('rack')
    const b = model('rack', { geometry: { location: { x: 4, y: 2, z: 3 } } })
    const startA = capturePlacement(a)
    const startB = capturePlacement(b)
    applyPlacement(a, startA, { x: 2.25, y: 1, z: 0 })
    applyPlacement(b, startB, { x: 2.25, y: 1, z: 0 })
    const locA = (a.geometry as { location: { x: number } }).location
    const locB = (b.geometry as { location: { x: number } }).location
    expect(locB.x - locA.x).toBe(3)
  })

  it('leaves a point the snapshot does not cover alone', () => {
    // The legacy guards the same way (`if (wpStart[i])`): a waypoint added
    // after the drag started has no start to be measured from.
    const p = pipe()
    const start = capturePlacement(p).slice(0, 2)
    applyPlacement(p, start, { x: 1, y: 0, z: 0 })
    const g = p.geometry as unknown as { waypoints: { x: number }[] }
    expect(g.waypoints[1]!.x).toBe(1.01)
  })

  it('does not throw on a model with nothing to move', () => {
    expect(() => applyPlacement(null, [], { x: 1, y: 1, z: 1 })).not.toThrow()
  })
})

describe('followDelta', () => {
  it('shifts a point by the delta from its own start', () => {
    expect(followDelta({ x: 5, y: 0, z: 2 }, { x: 0.5, y: 0, z: -1 })).toEqual({
      x: 5.5,
      y: 0,
      z: 1,
    })
  })
})

describe('hasPipeGeometry', () => {
  it('is true only when the group holds a pipe segment', () => {
    expect(hasPipeGeometry([model('rack'), model('floor')])).toBe(false)
    expect(hasPipeGeometry([model('rack'), pipe()])).toBe(true)
  })

  it('tolerates a hole in the group', () => {
    expect(hasPipeGeometry([null, undefined, model('acu')])).toBe(false)
  })
})
