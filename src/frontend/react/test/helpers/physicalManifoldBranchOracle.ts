/** Independent source-geometry checks for the admitted manifold branch compiler.
 * These are not MMS fixtures or solver-result oracles. Original project bytes stay frozen.
 */
import { expect } from 'vitest'
import { reviveKinds, stateV4 } from '../../src/legacy/cfdprojModules'
import { geometryPure } from '../../src/legacy/geometryBuilder'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import type { DynamicLiquidGraph } from '../../src/liquid/dynamicLiquidGraph'
type Graph = Pick<DynamicLiquidGraph, 'nodes' | 'edges' | 'object_bindings'>
type Point = { x: number; y: number; z: number }
type Valve = { id: string; type: string; connectedRackId?: string; connectedManifoldPortId?: string;
  seriesWithValveId?: string; opening?: number; branchDiameterM?: number; branchWaypoints?: Point[] }
const cellsFor = (points: number[][]) => Math.max(2, Math.round(points.slice(1).reduce((length, point, index) => {
  const start = points[index]!
  return length + Math.hypot(point[0]! - start[0]!, point[1]! - start[1]!, point[2]! - start[2]!)
}, 0) / 0.2))

export function assertPhysicalManifoldBranches(project: CfdProject, graph: Graph) {
  const before = JSON.stringify(project)
  const models = stateV4().allModels(reviveKinds(structuredClone(project))) as Construction[]
  const modelByUid = new Map(models.map(model => [model.uid, model]))
  const nodeById = new Map(graph.nodes.map(node => [node.id, node]))
  const edgeById = new Map(graph.edges.map(edge => [edge.id, edge]))
  expect(nodeById.size).toBe(graph.nodes.length)
  expect(edgeById.size).toBe(graph.edges.length)
  const requiredBottomNodes = new Set<string>(), requiredHeaderEdges = new Set<string>()
  let branches = 0
  for (const pipe of models.filter(model => model.kind === 'pipe_segment' && model.meta.enabled !== false)) {
    const cooling = pipe.cooling as { valves?: Valve[] } | undefined
    for (const valve of cooling?.valves ?? []) {
      if (!['tee_junction', 'flow_control'].includes(valve.type) || !valve.connectedRackId ||
        !valve.connectedManifoldPortId || valve.seriesWithValveId) continue
      const rack = modelByUid.get(valve.connectedRackId)!
      expect(rack?.kind).toBe('rack')
      const port = valve.connectedManifoldPortId.split('.')[1]!
      const supply = port.includes('supply'), bottom = port.endsWith('_bottom')
      const frame = geometryPure().liquidEquipmentPortFrame(rack, port, bottom ? 'bottom' : 'top')!
      expect(frame).not.toBeNull()
      const terminal = frame.position.toArray(), main = `${rack.uid}_mf_${supply ? 'supply' : 'return'}`
      const target = bottom ? main + '_bottom' : main
      const spoke = edgeById.get(`${pipe.uid}_spoke_${valve.id}`)!
      expect(spoke?.type).toBe('pipe')
      const teeId = `${pipe.uid}_tee_${valve.id}`
      if (valve.type === 'flow_control') {
        const outletId = `${pipe.uid}_control_${valve.id}_out`
        const control = edgeById.get(`${pipe.uid}_control_${valve.id}`)!
        expect(control).toMatchObject({ type: 'valve', from: teeId, to: outletId,
          params: { opening: Number(valve.opening ?? 1) } })
        expect(nodeById.get(outletId)).toEqual({ id: outletId, type: 'junction',
          position_m: nodeById.get(teeId)!.position_m })
        expect(spoke.from).toBe(outletId)
        expect(graph.object_bindings[`${pipe.uid}/${valve.id}`]!.edge_ids).toEqual([control.id])
      } else expect(spoke.from).toBe(teeId)
      expect(nodeById.get(spoke.from)?.type).toBe('junction')
      expect(spoke.to).toBe(target)
      const route = valve.branchWaypoints
        ? valve.branchWaypoints.map(point => [point.x, point.y, point.z])
        : [nodeById.get(spoke.from)!.position_m, terminal]
      route[0] = [...nodeById.get(spoke.from)!.position_m]
      route[route.length - 1] = terminal
      expect(spoke.params.waypoints_m).toEqual(route)
      expect(spoke.params.n_cells).toBe(cellsFor(route))
      const sourceDiameter = (pipe.geometry as { diameterM?: number }).diameterM || 0.025
      expect(spoke.params.D_m).toBe(valve.branchDiameterM ?? sourceDiameter)
      expect(spoke.params.roughness_m).toBe(1.5e-6)
      expect(spoke.params.K_minor_per_bend).toBe(0.5)
      expect(graph.object_bindings[pipe.uid]!.edge_ids).toContain(spoke.id)
      if (bottom) {
        requiredBottomNodes.add(target)
        const headerId = target + '_header'; requiredHeaderEdges.add(headerId)
        expect(nodeById.get(target)).toEqual({ id: target, type: 'junction', position_m: terminal })
        const header = edgeById.get(headerId)!, routeToTop = [terminal, nodeById.get(main)!.position_m]
        const manifold = (rack.cooling as { liquidManifold?: { supplyPipe?: { diameter?: number }; returnPipe?: { diameter?: number } } }).liquidManifold!
        expect(header).toEqual({ id: headerId, type: 'pipe', from: target, to: main,
          params: { D_m: (supply ? manifold.supplyPipe?.diameter : manifold.returnPipe?.diameter) || 0.025,
            roughness_m: 1.5e-6, n_cells: cellsFor(routeToTop), waypoints_m: routeToTop, K_minor_per_bend: 0.5 } })
        expect(graph.object_bindings[rack.uid]!.node_ids).toContain(target)
        expect(graph.object_bindings[rack.uid]!.edge_ids).toContain(headerId)
      }
      branches++
    }
  }
  expect(graph.nodes.filter(node => /_mf_(supply|return)_bottom$/.test(node.id)).map(node => node.id).sort())
    .toEqual([...requiredBottomNodes].sort())
  expect(graph.edges.filter(edge => /_mf_(supply|return)_bottom_header$/.test(edge.id)).map(edge => edge.id).sort())
    .toEqual([...requiredHeaderEdges].sort())
  expect(JSON.stringify(project)).toBe(before)
  return { branches, bottomHeaders: requiredHeaderEdges.size }
}
