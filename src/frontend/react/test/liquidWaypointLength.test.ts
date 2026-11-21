import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'

const point = (x: number, y: number, z = 0) => ({ x, y, z })

it('keeps each original bend when tee and flow-control fittings split a drawn pipe', () => {
  // Reuse the valid CDU/rack loop, but add a wholly synthetic six-metre
  // header. Its two fittings lie exactly on the drawn centreline.
  const project = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/LiquidSingle/LiquidSingle.cfdproj'), 'utf8')) as CfdProject
  const pipes = project.constructions!.pipeSegments! as Record<string, any>
  const existing = pipes.m_4
  const bent = structuredClone(existing)
  bent.uid = 'm_bent'
  bent.meta.name = 'SyntheticBentHeader'
  bent.geometry.waypoints = [point(0, 0), point(2, 0), point(2, 2), point(4, 2)]
  bent.geometry.diameterM = 0.1
  bent.cooling.connection = { startPortId: '', endPortId: '', blindEnds: { start: true, end: true }, junctions: [] }
  bent.cooling.valves = [
    { id: 'tee', type: 'tee_junction', position: point(1, 0), connectedRackId: 'm_2', connectedManifoldPortId: 'm_2.manifold_supply_top' },
    { id: 'control', type: 'flow_control', position: point(2, 1), connectedRackId: 'm_2', connectedManifoldPortId: 'm_2.manifold_supply_top', opening: 0.7 },
  ]
  pipes.m_bent = bent

  const graph = compileDynamicLiquidGraph(project)
  const segments = [0, 1, 2].map(i => graph.edges.find(e => e.id === `m_bent_seg${i}`)!)
  expect(segments.every(Boolean)).toBe(true)
  expect(segments.map(e => e.params.waypoints_m)).toEqual([
    [[0, 0, 0], [1, 0, 0]],
    [[1, 0, 0], [2, 0, 0], [2, 1, 0]],
    [[2, 1, 0], [2, 2, 0], [4, 2, 0]],
  ])
  expect(graph.nodes.find(n => n.id === 'm_bent_tee_tee')?.position_m).toEqual([1, 0, 0])
  expect(graph.nodes.find(n => n.id === 'm_bent_tee_control')?.position_m).toEqual([2, 1, 0])
  expect(segments.map(e => e.params.n_cells)).toEqual([5, 10, 15])

  const lengths = segments.map(e => (e.params.waypoints_m as number[][]).slice(1).reduce((sum, current, i) => {
    const previous = e.params.waypoints_m[i] as number[]
    return sum + Math.hypot(...current.map((coordinate, axis) => coordinate - previous[axis]!))
  }, 0))
  expect(lengths).toEqual([1, 2, 3])
  expect(lengths.reduce((sum, length) => sum + length, 0)).toBe(6)
  expect(lengths.reduce((sum, length) => sum + Math.PI * 0.1 ** 2 * length / 4, 0))
    .toBeCloseTo(Math.PI * 0.1 ** 2 * 6 / 4, 12)
})

it('rejects a saved fitting displaced from its visible pipe centreline', () => {
  const project = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/LiquidSingle/LiquidSingle.cfdproj'), 'utf8')) as CfdProject
  const pipe = (project.constructions!.pipeSegments! as Record<string, any>).m_4
  pipe.cooling.valves[0].position.z += 0.1

  expect(() => compileDynamicLiquidGraph(project)).toThrow(/centreline/)
})
