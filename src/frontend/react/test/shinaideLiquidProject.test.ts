import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import { serializeProjectState } from '../src/io/projectFiles'
it('round-trips and compiles the complete Shinaide liquid engineering project',()=>{
 const path=resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json')
 const project=reviveKinds(JSON.parse(readFileSync(path,'utf8')))
 const before=serializeProjectState(project)
 const graph=compileDynamicLiquidGraph(project,{dt_s:1,duration_s:10})
 const counts=Object.fromEntries(['pipe','cold_plate','valve','pump','heat_exchanger'].map(t=>[t,graph.edges.filter(e=>e.type===t).length]))
 expect(counts).toMatchObject({cold_plate:56,valve:14,pump:1,heat_exchanger:1})
 expect(counts.pipe).toBeGreaterThan(100);expect(graph.racks).toHaveLength(14)
 expect(graph.edges.filter(e=>e.type==='cold_plate').reduce((s,e)=>s+e.params.Q_W,0)).toBe(21000)
 expect(new Set(graph.nodes.map(n=>n.id)).size).toBe(graph.nodes.length)
 expect(new Set(graph.edges.map(e=>e.id)).size).toBe(graph.edges.length)
 const ids=new Set(graph.nodes.map(n=>n.id));expect(graph.edges.every(e=>ids.has(e.from)&&ids.has(e.to))).toBe(true)
 expect(serializeProjectState(project)).toBe(before)
 expect(compileDynamicLiquidGraph(reviveKinds(JSON.parse(before)),{dt_s:1,duration_s:10})).toEqual(graph)
 if(process.env.SHINAIDE_GRAPH_DUMP)writeFileSync(process.env.SHINAIDE_GRAPH_DUMP,JSON.stringify(graph,null,2))
 console.info('Shinaide',counts,'nodes',graph.nodes.length,'edges',graph.edges.length)
})
