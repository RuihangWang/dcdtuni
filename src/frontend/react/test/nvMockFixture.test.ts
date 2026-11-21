import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import pack from '../../../backend/liquidcooling/data/mock-demo-v1.json'
import { buildNvMockProject } from '../../../../tools/nv_mock_project'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import type { CfdProject } from '../src/schema/cfdproj'

function build() {
  const template=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_hybrid_cpu.json'),'utf8'))
  return buildNvMockProject(template,pack,{templateSha256:'template-hash',pointListSha256:'point-list-hash'})
}
it('keeps only the point-list inventory as sourced facts; all engineering values are declared mock',()=>{
  const p=build()
  expect(Object.keys(p.constructions.cdus)).toHaveLength(2)
  expect(Object.keys(p.constructions.racks)).toHaveLength(34)
  const fittings=Object.values(p.constructions.pipeSegments).flatMap((v:any)=>v.cooling.valves) as any[]
  expect(fittings.filter(v=>v.meta?.assetKind==='energy_valve')).toHaveLength(34)
  expect(fittings.filter(v=>v.type==='isolation')).toHaveLength(68)
  expect(fittings.filter(v=>v.type==='isolation').every(v=>v.opening===1)).toBe(true)
  expect(p.meta.provenance.asset_count_sources.map((v:any)=>v.cell)).toEqual(['C3','C31','C40'])
  expect(p.meta.provenance.topology.source).toBe('mock')
  expect(p.runDefaults.liquid.experiment.parameterPack.source).toBe('mock')
  expect(p.runDefaults.liquid.solver.device).toBe('cpu')
  expect(build()).toEqual(p)
})
it('compiles exactly 102 physical series valves and 34 equivalent heat loads through the existing frontend compiler',()=>{
  const p=build()
  const before=JSON.stringify(p)
  const graph=compileDynamicLiquidGraph(p as CfdProject,{duration_s:10,dt_s:1,parameterPack:p.runDefaults.liquid.experiment.parameterPack})
  expect(graph.edges.filter(e=>e.type==='pump')).toHaveLength(2)
  expect(graph.edges.filter(e=>e.type==='heat_exchanger')).toHaveLength(2)
  expect(graph.edges.filter(e=>e.type==='valve')).toHaveLength(102)
  expect(graph.edges.filter(e=>e.type==='cold_plate')).toHaveLength(34)
  expect(graph.edges.filter(e=>e.type==='cold_plate').reduce((s,e)=>s+e.params.Q_W,0)).toBe(340000)
  for(let i=1;i<=34;i++) {
    const id=String(i).padStart(2,'0'),zone=i<=17?'01':'02'
    const energy=graph.object_bindings[`nv_supply_${zone}/nv_energy_${id}`]!
    const isolation=graph.object_bindings[`nv_supply_${zone}/nv_iso_supply_${id}`]!
    const energyEdge=graph.edges.find(e=>e.id===energy.edge_ids[0])!
    const isolationEdge=graph.edges.find(e=>e.id===isolation.edge_ids[0])!
    expect(energyEdge.to).toBe(isolationEdge.from)
    expect(graph.edges.find(e=>e.id===`nv_supply_${zone}_spoke_nv_energy_${id}`)?.from).toBe(isolationEdge.to)
  }
  expect(JSON.stringify(p)).toBe(before)
})
