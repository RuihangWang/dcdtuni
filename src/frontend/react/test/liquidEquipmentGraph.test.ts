import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { reviveKinds, stateV4 } from '../src/legacy/cfdprojModules'
import { buildLiquidGraph } from '../src/legacy/liquidGraphgen'
import '../../shared/js/liquid_pipe_renderer.js'
import type * as THREE from 'three'

const PROJECT = resolve(process.cwd(), '../../../case/Liquid/Liquid.cfdproj')

function nodePosition(graph: Record<string, unknown>, id: string): number[] {
  const nodes = graph.nodes as Array<{ id: string; position_m: number[] }>
  const node = nodes.find((candidate) => candidate.id === id)
  expect(node, `missing graph node ${id}`).toBeTruthy()
  return node!.position_m
}

function expectPoint(actual: number[], expected: number[]) {
  expect(actual).toHaveLength(3)
  expected.forEach((value, index) => expect(actual[index]).toBeCloseTo(value, 10))
}

describe('liquid graph equipment endpoints', () => {
  it('uses rotated CDU and configured rack manifold centers in compiled physics', () => {
    const project = reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject)
    const models = stateV4().allModels(project) as Construction[]
    const cdu = models.find((model) => model.kind === 'cdu')!
    const rack = models.find((model) => model.kind === 'rack')!
    const pipeTemplate = models.find((model) => model.kind === 'pipe_segment')!
    const pipe = JSON.parse(JSON.stringify(pipeTemplate)) as Construction
    Object.defineProperty(pipe, 'kind', { value: 'pipe_segment', enumerable: false })
    pipe.uid = 'test_polyline'
    pipe.meta = { ...pipe.meta, name: 'Polyline_Test' }
    ;(project.constructions!.pipeSegments as Record<string, Construction>)[pipe.uid] = pipe
    cdu.geometry!.location = { x: 10, y: 2, z: -3 }
    cdu.geometry!.quaternion = { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 }
    rack.geometry!.location = { x: 4, y: 1, z: 8 }
    rack.geometry!.quaternion = { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 }
    ;(pipe.geometry as { waypoints: Array<{ x: number; y: number; z: number }> }).waypoints = [
      { x: 0, y: 0, z: 0 },
      { x: 0, y: 4, z: 0 },
      { x: 5, y: 4, z: 0 },
      { x: 5, y: 4, z: 5 },
      { x: 6, y: 4, z: 5 },
    ]
    ;(pipe.cooling as { valves?: unknown[] }).valves = []

    const graph = buildLiquidGraph(project, {
      fluid: { rho: 997, cp: 4182, mu: 0.00089, k: 0.606, supplyTemp: 35 },
      solver: {},
      enabled: true,
    })

    expect(graph).not.toBeNull()
    expectPoint(nodePosition(graph!, `${cdu.meta.name}_supply`), [10.6, 3.53, -3.4])
    expectPoint(nodePosition(graph!, `${cdu.meta.name}_return`), [10.6, 3.48, -3.4])
    expectPoint(nodePosition(graph!, `${rack.meta.name}_mf_supply`), [3.95, 2.91135, 7.9775])
    expectPoint(nodePosition(graph!, `${rack.meta.name}_mf_return`), [3.95, 2.91135, 7.4225])

    const renderer = (window as unknown as {
      _liquidPipeRenderer: {
        getCduPort(model: Construction, port: string): THREE.Vector3
        getRackPort(model: Construction, port: string): THREE.Vector3
        resolveChainEquipmentPort(token: string): THREE.Vector3 | null
      }
    })._liquidPipeRenderer
    ;(window as unknown as { allModels: () => Construction[] }).allModels = () =>
      stateV4().allModels(project) as Construction[]
    expectPoint(renderer.getCduPort(cdu, 'supply').toArray(), [10.6, 3.53, -3.4])
    expectPoint(renderer.getRackPort(rack, 'inlet').toArray(), [3.95, 2.91135, 7.9775])
    expectPoint(renderer.resolveChainEquipmentPort(`${cdu.meta.name}_supply`)!.toArray(), [10.6, 3.53, -3.4])
    expectPoint(renderer.resolveChainEquipmentPort(`${rack.meta.name}_manifold_in`)!.toArray(), [3.95, 2.91135, 7.9775])

    const pipeEdge = (graph!.edges as Array<{
      id: string
      params: { waypoints_m: number[][] }
    }>).find((edge) => edge.id === pipe.meta.name)!
    expect(pipeEdge.params.waypoints_m).toHaveLength(5)
    expectPoint(pipeEdge.params.waypoints_m[0]!, [10.6, 3.53, -3.4])
    expectPoint(pipeEdge.params.waypoints_m[2]!, [5, 4, 0])
    expectPoint(pipeEdge.params.waypoints_m[4]!, [6, 4, 5])
  })
})
