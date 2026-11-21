import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import { modelChildren, pipeValves } from '../src/interaction/treeNodes'

/**
 * Pipe rows in the feature tree.
 *
 * The fixture is case/LiquidSingle's `Pipe_1` and `Pipe_1_copy` trimmed to the
 * fields the tree reads: a six-waypoint pipe with a blind start, an end port on
 * the CDU, and one fitting each — a flow-control valve on one, a T-junction on
 * the other.
 */

function pipe(over: Record<string, unknown> = {}, kind = 'pipe_segment'): Construction {
  const model = {
    uid: 'm_3',
    meta: { name: 'Pipe_1', enabled: true },
    geometry: {
      diameterM: 0.1,
      waypoints: [
        { x: 0, y: 2.8, z: 0.08 },
        { x: 1, y: 2.8, z: 0.08 },
        { x: 2, y: 2.8, z: 0.08 },
        { x: 2, y: 1.9, z: 0.08 },
        { x: 2, y: 1.9, z: -0.6 },
        { x: 2, y: 1.4, z: -0.6 },
      ],
    },
    cooling: {
      spec: 'pipe_100mm',
      loopRole: 'return',
      valves: [
        {
          id: 'valve_1779191286514',
          type: 'flow_control',
          connectedManifoldPortId: 'm_2.manifold_return_top',
          connectedRackId: 'm_2',
        },
      ],
      connection: {
        startPortId: '',
        endPortId: 'm_1.return',
        blindEnds: { start: true, end: false },
      },
    },
    ...over,
  }
  Object.defineProperty(model, 'kind', { value: kind, enumerable: false })
  return model as unknown as Construction
}

describe('pipe tree children', () => {
  it('lists the control-point count, the connected end, the blind end and the fitting', () => {
    const rows = modelChildren(pipe())
    expect(rows.map((r) => r.key)).toEqual([
      'pipe-cp:m_3',
      'pipe-end:m_3',
      'pipe-blind-start:m_3',
      'pipe-fitting:m_3:valve_1779191286514',
    ])
    expect(rows[0]?.badge).toBe('6')
    expect(rows[1]?.label).toContain('m_1.return')
    // No start port and no blind end means no row for either — the legacy emits
    // both conditionally, so a pipe connected at one end shows one link row.
    expect(rows.some((r) => r.key === 'pipe-start:m_3')).toBe(false)
    expect(rows.some((r) => r.key === 'pipe-blind-end:m_3')).toBe(false)
  })

  it('marks a T-junction differently from a flow-control valve', () => {
    const tee = pipe({
      cooling: {
        valves: [{ id: 'tee_1779191317140', type: 'tee_junction', connectedRackId: 'm_2' }],
        connection: { startPortId: '', endPortId: '' },
      },
    })
    const row = modelChildren(tee)[1]
    expect(row?.badge).toBe('TEE')
    expect(row?.label).toBe('T-Junction_1779191317140')

    const fcv = modelChildren(pipe())[3]
    expect(fcv?.badge).toBe('FCV')
    expect(fcv?.label).toBe('Valve_1779191286514')
  })

  it('selects the fitting, carrying the pipe it is on', () => {
    const row = modelChildren(pipe())[3]
    expect(row?.select).toEqual({
      type: 'fitting',
      uid: 'm_3',
      fittingId: 'valve_1779191286514',
    })
  })

  it('names the rack a fitting is wired to when the caller can resolve it', () => {
    const rows = modelChildren(pipe(), { modelName: (uid) => (uid === 'm_2' ? 'Rack_1' : null) })
    expect(rows[3]?.label).toBe('Valve_1779191286514 → Rack_1')
  })

  it('gives a junction no children and a face-less pipe no rows', () => {
    expect(modelChildren(pipe({}, 'pipe_junction'))).toEqual([])
    const bare = pipe({ geometry: {} })
    expect(modelChildren(bare)).toEqual([])
  })

  it('ignores a malformed valve list rather than rendering a row with no id', () => {
    expect(pipeValves(pipe({ cooling: { valves: 'nope' } }))).toEqual([])
    expect(pipeValves(pipe({ cooling: { valves: [null, { type: 'flow_control' }] } }))).toEqual([])
  })
})
