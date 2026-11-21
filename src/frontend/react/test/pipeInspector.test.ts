import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { pipeSummary, setPipeDiameter, setPipeSegmentPoints } from '../src/liquid/pipeInspector'
import { automation } from '../src/automation/contract'
import { BUILD_VERBS } from '../src/headless/projectBuilder'
import * as store from '../src/projectStore'

function pipe(over: Record<string, unknown> = {}): Construction {
  const model = {
    uid: 'm_3',
    meta: { name: 'Pipe_1' },
    geometry: {
      diameterM: 0.1,
      waypoints: [
        { x: -0.79, y: 2.82, z: 0.086 },
        { x: 1.34, y: 2.82, z: 0.086 },
        { x: 2.31, y: 1.46, z: -0.606 },
      ],
    },
    cooling: {
      loopRole: 'return',
      connection: {
        startPortId: '',
        endPortId: 'm_1.return',
        blindEnds: { start: true, end: false },
      },
    },
    ...over,
  }
  Object.defineProperty(model, 'kind', { value: 'pipe_segment', enumerable: false })
  return model as unknown as Construction
}

describe('pipeSummary', () => {
  it('reads the two ends off the waypoint list, not a bounding box', () => {
    const s = pipeSummary(pipe())
    expect(s.start).toEqual({ x: -0.79, y: 2.82, z: 0.086 })
    expect(s.end).toEqual({ x: 2.31, y: 1.46, z: -0.606 })
    expect(s.waypointCount).toBe(3)
  })

  it('carries the loop role, its label key and the colour the pipe is drawn in', () => {
    expect(pipeSummary(pipe()).loopRoleKey).toBe('pipe.color_return')
    expect(pipeSummary(pipe()).loopRoleColor).toBe('#F44336')
    const supply = pipeSummary(pipe({ cooling: { loopRole: 'supply' } }))
    expect(supply.loopRoleKey).toBe('pipe.color_supply')
    expect(supply.loopRoleColor).toBe('#2196F3')
    // Anything else — including a missing role — is "unconnected", which is
    // what the renderer draws brown.
    const none = pipeSummary(pipe({ cooling: {} }))
    expect(none.loopRole).toBe('unconnected')
    expect(none.loopRoleColor).toBe('#8B6914')
  })

  it('reports the connections and the blind ends separately', () => {
    const s = pipeSummary(pipe())
    expect(s.startPortId).toBe('')
    expect(s.endPortId).toBe('m_1.return')
    expect(s.blindStart).toBe(true)
    expect(s.blindEnd).toBe(false)
  })

  it('falls back to 25 mm and a zero-length run for a pipe with nothing on it', () => {
    const s = pipeSummary(pipe({ geometry: {}, cooling: {} }))
    expect(s.diameterM).toBe(0.025)
    expect(s.waypointCount).toBe(0)
    expect(s.start).toEqual({ x: 0, y: 0, z: 0 })
    expect(s.end).toEqual(s.start)
  })

  it('reads a single-waypoint pipe as a run that starts and ends in one place', () => {
    const s = pipeSummary(pipe({ geometry: { waypoints: [{ x: 1, y: 2, z: 3 }] } }))
    expect(s.start).toEqual({ x: 1, y: 2, z: 3 })
    expect(s.end).toEqual({ x: 1, y: 2, z: 3 })
  })
})

describe('setPipeDiameter', () => {
  beforeEach(() => {
    const project = {
      constructions: {
        pipeSegments: {
          m_3: {
            uid: 'm_3',
            meta: { name: 'Pipe_1' },
            geometry: { diameterM: 0.1, waypoints: [] },
            cooling: {},
          },
        },
        racks: { m_2: { uid: 'm_2', meta: { name: 'Rack_1' } } },
      },
    } as unknown as CfdProject
    store.loadProject(project, '/tmp/liquid.cfdproj')
  })

  const bore = () =>
    (store.getProject()?.constructions?.pipeSegments?.m_3?.geometry as { diameterM?: number })
      ?.diameterM

  it('writes metres and is undoable in one press', () => {
    setPipeDiameter('m_3', 0.032)
    expect(bore()).toBe(0.032)
    store.undo()
    expect(bore()).toBe(0.1)
  })

  it('refuses a zero or negative bore, which the solver would divide by', () => {
    setPipeDiameter('m_3', 0)
    setPipeDiameter('m_3', -0.05)
    setPipeDiameter('m_3', Number.NaN)
    expect(bore()).toBe(0.1)
  })

  it('does not touch a model that is not a pipe', () => {
    setPipeDiameter('m_2', 0.05)
    expect(
      (store.getProject()?.constructions?.racks?.m_2?.geometry as { diameterM?: number })
        ?.diameterM,
    ).toBeUndefined()
  })
})

describe('setPipeSegmentPoints', () => {
  const route = [{ x: 0, y: 0.5, z: 1 }, { x: 4, y: 0.5, z: 1 }, { x: 4, y: 2, z: 1 }]
  beforeEach(() => {
    const project = {
      constructions: {
        pipeSegments: {
          m_3: {
            uid: 'm_3',
            meta: { name: 'Pipe_1' },
            geometry: { diameterM: 0.05, waypoints: route },
            cooling: {
              loopRole: 'supply',
              valves: [
                { id: 'v1', type: 'tee', connectedRackId: 'm_2', position: { x: 1, y: 0.5, z: 1 } },
                { id: 'v2', type: 'tee', connectedRackId: 'm_4', position: { x: 3, y: 0.5, z: 1 } },
              ],
            },
          },
        },
        racks: { m_2: { uid: 'm_2', meta: { name: 'Rack_1' } } },
      },
    } as unknown as CfdProject
    store.loadProject(project, '/tmp/liquid.cfdproj')
  })
  const model = () => store.getProject()!.constructions!.pipeSegments!.m_3 as unknown as {
    geometry: { waypoints: unknown; diameterM: number }
    cooling: { loopRole: string; valves: { id: string; connectedRackId: string; position: unknown }[] }
  }
  const moved = route.map((p) => ({ ...p, z: p.z + 0.3 }))

  it('moves the route and the named valves, keeps the rest, and undoes in one step', () => {
    expect(setPipeSegmentPoints('m_3', moved, { v2: { x: 3, y: 0.5, z: 1.3 } })).toBe(true)
    expect(model().geometry.waypoints).toEqual(moved)
    expect(model().cooling.valves[1]!.position).toEqual({ x: 3, y: 0.5, z: 1.3 })
    expect(model().cooling.valves[0]!.position).toEqual({ x: 1, y: 0.5, z: 1 })
    expect(model().cooling.valves.map((v) => v.connectedRackId)).toEqual(['m_2', 'm_4'])
    expect(model().geometry.diameterM).toBe(0.05)
    store.undo()
    expect(model().geometry.waypoints).toEqual(route)
    expect(model().cooling.valves[1]!.position).toEqual({ x: 3, y: 0.5, z: 1 })
  })

  it('refuses a short route, a bad coordinate, an unknown valve and a non-pipe', () => {
    expect(setPipeSegmentPoints('m_3', moved.slice(0, 1))).toBe(false)
    expect(setPipeSegmentPoints('m_3', [...moved.slice(0, 2), { x: Number.NaN, y: 0, z: 0 }])).toBe(false)
    expect(setPipeSegmentPoints('m_3', moved, { nope: { x: 0, y: 0, z: 0 } })).toBe(false)
    expect(setPipeSegmentPoints('m_2', moved)).toBe(false)
    expect(model().geometry.waypoints).toEqual(route)
  })

  it('is a headless build verb, beside the pump speed', () => {
    expect(automation.setPipeSegmentPoints).toBe(setPipeSegmentPoints)
    expect(BUILD_VERBS).toContain('setPipeSegmentPoints')
    expect(BUILD_VERBS).toContain('setCduPumpSpeed')
  })
})
