import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { readProjectFileText } from '../src/io/projectFiles'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import { captureCompilerAlerts, edgeBlocks, elevationMismatch, liquidReadiness } from '../src/liquid/liquidReadiness'

const root = resolve(process.cwd(), '../../..')
function load(path: string): CfdProject {
  const result = readProjectFileText(readFileSync(resolve(root, path), 'utf8'))
  if (!result.ok) throw new Error(result.error)
  store.loadProject(result.project, null)
  return structuredClone(store.getProject()!)
}
const status = (project: CfdProject | null) =>
  Object.fromEntries(liquidReadiness(project).items.map(i => [i.id, i.status]))
type Bag = Record<string, any>

describe('edgeBlocks', () => {
  // supply -pump-> a -plate1-> b -pipe-> supply ; plus a dead-end b -x-> c -plate2-> d -y-> c
  const nodes = ['s', 'a', 'b', 'c', 'd']
  const edges = [
    { from: 's', to: 'a' }, // 0 pump
    { from: 'a', to: 'b' }, // 1 plate on the loop
    { from: 'b', to: 's' }, // 2 return pipe
    { from: 'b', to: 'c' }, // 3 dead-end branch
    { from: 'c', to: 'd' }, // 4 plate in a little loop off the branch
    { from: 'd', to: 'c' }, // 5
  ]
  it('puts a loop plate in the pump block and a hanging loop elsewhere', () => {
    const blocks = edgeBlocks(nodes, edges)
    expect(blocks[1]).toBe(blocks[0])
    expect(blocks[2]).toBe(blocks[0])
    expect(blocks[3]).not.toBe(blocks[0])
    expect(blocks[4]).toBe(blocks[5])
    expect(blocks[4]).not.toBe(blocks[0])
  })
  it('keeps parallel plates between the same two nodes in one block', () => {
    const blocks = edgeBlocks(['s', 'a'], [{ from: 's', to: 'a' }, { from: 'a', to: 's' }, { from: 'a', to: 's' }])
    expect(new Set(blocks).size).toBe(1)
  })
})

describe('elevationMismatch', () => {
  const graph = (routeEnd: number) => ({
    nodes: [{ id: 'a', position_m: [0, 0, 0] }, { id: 'b', position_m: [0, 1, 0] }],
    edges: [
      { id: 'pump', type: 'pump', from: 'a', to: 'b', params: {} },
      { id: 'pipe', type: 'pipe', from: 'b', to: 'a', params: { waypoints_m: [[0, 1, 0], [0, routeEnd, 0]] } },
    ],
  })
  it('accepts a loop whose pipe route ends at its node heights', () => {
    expect(elevationMismatch(graph(0))).toBeNull()
  })
  it('names the pipe whose route ends disagree with its nodes', () => {
    expect(elevationMismatch(graph(0.25))).toEqual({ mismatchM: 0.25, pipes: ['pipe'] })
  })
})

describe('a timeline that no longer matches its objects', () => {
  it('is reported as a compile error rather than thrown', () => {
    const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj') as Bag
    const rack = Object.values(project.constructions.racks).find((r: any) => r.cooling?.type === 'liquid') as Bag
    ;(Object.values(rack.constructions.servers)[0] as Bag).liquidCooling.cpCuPairs = []
    const report = liquidReadiness(project as CfdProject)
    expect(report.ready).toBe(false)
    expect(report.items).toEqual([expect.objectContaining({ id: 'compile', status: 'error' })])
  })
})

describe('captureCompilerAlerts', () => {
  it('collects alerts and puts window.alert back', () => {
    const original = window.alert
    const result = captureCompilerAlerts(() => { window.alert('Liquid graph validation failed:\n\nNo pressure_bc node'); return 3 })
    expect(result).toEqual({ value: 3, alerts: ['Liquid graph validation failed:\n\nNo pressure_bc node'], error: null })
    expect(window.alert).toBe(original)
  })
})

describe('liquidReadiness on real projects', () => {
  it('passes every check on the canonical Shinaide liquid project, hybrid budget included', () => {
    const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj')
    const report = liquidReadiness(project)
    expect(Object.fromEntries(report.items.map(i => [i.id, i.status]))).toEqual({
      cdu: 'ok', liquidRacks: 'ok', coldPlates: 'ok', compile: 'ok', loop: 'ok', elevation: 'ok', hybridBudget: 'ok',
    })
    expect(report.ready).toBe(true)
    expect(report.items.find(i => i.id === 'coldPlates')!.vars).toEqual({ pairs: 56, servers: 56 })
    expect(report.graph!.edges.filter(e => e.type === 'cold_plate')).toHaveLength(56)
  })

  it('passes the single-rack control project, which has no hybrid servers', () => {
    expect(status(load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj'))).toEqual({
      cdu: 'ok', liquidRacks: 'ok', coldPlates: 'ok', compile: 'ok', loop: 'ok', elevation: 'ok',
    })
  })

  it('stops at a missing CDU without compiling and without an alert', () => {
    const project = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj') as Bag
    project.constructions.cdus = {}
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    try {
      expect(status(project as CfdProject)).toMatchObject({ cdu: 'error', compile: 'skip', loop: 'skip', elevation: 'skip' })
      expect(alert).not.toHaveBeenCalled()
    } finally { alert.mockRestore() }
  })

  it('warns about a liquid server that has no cold-plate pair', () => {
    const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj') as Bag
    const rack = Object.values(project.constructions.racks).find((r: any) => r.cooling?.type === 'liquid') as Bag
    const server = Object.values(rack.constructions.servers)[0] as Bag
    server.liquidCooling.cpCuPairs = []
    // Its keyframed plate heat goes with the pairs, as deleting them in the editor does.
    project.transient.tracks = project.transient.tracks.filter((t: Bag) => !(t.param === 'cp.heat' && t.modelUid === rack.uid && String(t.target).startsWith(server.uid + '/')))
    const report = liquidReadiness(project as CfdProject)
    const plates = report.items.find(i => i.id === 'coldPlates')!
    expect(plates.status).toBe('warn')
    expect(plates.details).toEqual([`${rack.meta.name} / ${server.meta.name}: 0`])
  })

  it('reports the server whose plate hangs on a dead-end once its rack loses the return branch', () => {
    const project = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj') as Bag
    const returns = Object.values(project.constructions.pipeSegments as Record<string, Bag>)
      .filter(p => p.cooling?.loopRole === 'return')
    for (const pipe of returns) for (const valve of pipe.cooling.valves ?? []) { valve.connectedRackId = ''; valve.connectedManifoldPortId = '' }
    const report = liquidReadiness(project as CfdProject)
    const compile = report.items.find(i => i.id === 'compile')!
    const loop = report.items.find(i => i.id === 'loop')!
    // Either the compiler refuses the network (and says why, without an alert)
    // or it compiles and the plate is reported as off the pumped loop.
    expect(compile.status === 'error' ? compile.details.length > 0 : loop.status === 'error').toBe(true)
    expect(report.ready).toBe(false)
  })
})
