import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { compileSavedExperiment } from '../src/liquid/liquidExperiment'

function project(name = 'LiquidSingle'): CfdProject {
  return JSON.parse(readFileSync(resolve(process.cwd(), `../../../case/${name}/${name}.cfdproj`), 'utf8'))
}

describe('dynamic physical graph from the actual editor project', () => {
  it('compiles an authored inline isolation valve in series without creating another rack spoke', () => {
    const p = project() as any
    const pipe = Object.values(p.constructions.pipeSegments).find((x:any) => x.cooling?.valves?.some((v:any) => v.type==='flow_control'&&v.connectedRackId)) as any
    const target=pipe.cooling.valves.find((v:any)=>v.type==='flow_control'&&v.connectedRackId)
    const before=compileDynamicLiquidGraph(p)
    pipe.cooling.valves.push({id:'mock-isolation',type:'isolation',seriesWithValveId:target.id,position:{...target.position},opening:1})
    const after=compileDynamicLiquidGraph(p)
    const isolation=after.edges.find(e=>e.id===`${pipe.uid}_isolation_mock-isolation`)!
    expect(isolation?.type).toBe('valve')
    expect(after.edges.length).toBe(before.edges.length+1)
    expect(after.nodes.length).toBe(before.nodes.length+1)
    const spoke=after.edges.find(e=>e.id===`${pipe.uid}_spoke_${target.id}`)!
    const energy=after.edges.find(e=>e.id===`${pipe.uid}_control_${target.id}`)!
    expect(isolation.from).toBe(energy.to)
    expect(isolation.to).toBe(spoke.from)
    expect(after.object_bindings[`${pipe.uid}/mock-isolation`]?.edge_ids).toEqual([isolation.id])
    expect(after.edges.some(e=>e.id===`${pipe.uid}_spoke_mock-isolation`)).toBe(false)
  })
  it.each([
    ['missing reference',(v:any)=>{v.seriesWithValveId='not-a-fitting'},/reference/],
    ['parallel connection',(v:any)=>{v.connectedRackId='rack'},/second rack connection/],
    ['closed active set',(v:any)=>{v.opening=0},/active-set/],
    ['unmodelled offset',(v:any)=>{v.position.x+=1},/coincide/],
  ])('rejects an invalid inline isolation %s',(_label,mutate,expected)=>{
    const p=project() as any
    const pipe=Object.values(p.constructions.pipeSegments).find((x:any)=>x.cooling?.valves?.some((v:any)=>v.type==='flow_control'&&v.connectedRackId)) as any
    const target=pipe.cooling.valves.find((v:any)=>v.type==='flow_control'&&v.connectedRackId)
    const valve={id:'mock-isolation',type:'isolation',seriesWithValveId:target.id,position:{...target.position},opening:1}
    ;(mutate as (v:any)=>void)(valve)
    pipe.cooling.valves.push(valve)
    expect(()=>compileDynamicLiquidGraph(p)).toThrow(expected as RegExp)
  })
  it('gives the CDU one pressure reference, an active HX and the drawn control valves', () => {
    const p = project()
    const graph = compileDynamicLiquidGraph(p, { duration_s: 30, dt_s: 1 })
    expect(graph.nodes.filter(n => n.type === 'pressure_bc')).toHaveLength(1)
    expect(graph.edges.filter(e => e.type === 'heat_exchanger')).toHaveLength(1)
    expect(graph.edges.filter(e => e.type === 'valve').length).toBeGreaterThan(0)
    const supply = graph.nodes.find(n => n.id === 'm_1_supply')!
    expect(supply.type).toBe('junction')
    expect(supply.bc).toBeUndefined()
    expect(graph.time.N_t).toBe(31)
    expect(graph.parameter_pack.source).toBe('mock')
  })

  it('does not mutate or append compiler cache fields to the project', () => {
    const p = project('Liquid')
    const before = JSON.stringify(p)
    compileDynamicLiquidGraph(p)
    expect(JSON.stringify(p)).toBe(before)
  })

  it('preserves graph identity and physics when display names change', () => {
    const p = project('Liquid')
    const first = compileDynamicLiquidGraph(p)
    const next = JSON.parse(JSON.stringify(p))
    for (const bucket of Object.values(next.constructions) as any[]) {
      if (!bucket || typeof bucket !== 'object') continue
      for (const model of Object.values(bucket) as any[]) {
        if (!model?.meta) continue
        model.meta.name = `renamed-${model.uid}`
        for (const server of Object.values(model.constructions?.servers ?? {}) as any[]) {
          server.meta.name = `renamed-${server.uid}`
        }
      }
    }
    const second = compileDynamicLiquidGraph(next)
    expect(second.nodes).toEqual(first.nodes)
    expect(second.edges).toEqual(first.edges)
    expect(second.racks).toEqual(first.racks)
  })

  it('retains 13 cold plates and their specified heat loads in the four-rack case', () => {
    const graph = compileDynamicLiquidGraph(project('Liquid'))
    const plates = graph.edges.filter(e => e.type === 'cold_plate')
    expect(plates).toHaveLength(13)
    expect(plates.reduce((s,e) => s + Number(e.params.Q_W), 0)).toBe(4550)
    expect(graph.racks).toHaveLength(4)
    expect(Object.keys(graph.object_bindings).length).toBeGreaterThan(4)
  })

  it('carries control events and rejects references to nonexistent objects', () => {
    const graph = compileDynamicLiquidGraph(project(), {
      events: [{ time_s: 3, target: 'm_1_pump', field: 'rpm_ratio', value: 0.6 }],
    })
    expect(graph.events[0]?.target).toBe('m_1_pump')
    expect(() => compileDynamicLiquidGraph(project(), {
      events: [{ time_s: 3, target: 'missing', field: 'opening', value: 0.6 }],
    })).toThrow(/target/)
  })

  it('rejects off-grid horizons and invalid numerical settings', () => {
    expect(() => compileDynamicLiquidGraph(project(), {dt_s:0})).toThrow(/dt/)
    expect(() => compileDynamicLiquidGraph(project(), {duration_s:2.5,dt_s:1})).toThrow(/grid/)
  })
})

it('keeps cold plate identity after another plate is inserted before it',()=>{
 const p=project() as any
 const pairs=p.constructions.racks.m_2.constructions.servers.e_2.liquidCooling.cpCuPairs
 const old=compileDynamicLiquidGraph(p).edges.find(e=>e.type==='cold_plate')!
 pairs.unshift({...pairs[0],uid:'new_plate',name:'new_plate',Q_W:100})
 const next=compileDynamicLiquidGraph(p)
 expect(next.edges.find(e=>e.id===old.id)?.params.Q_W).toBe(3500)
 expect(old.id).toContain('CP-CU_1')
})

it('takes cold plate values from the pair the editor and the timeline write, identity from the canonical plate',()=>{
 // v3→v4 migration left a copy in constructions.coldPlates that nothing writes
 // afterwards; an edited (or keyframed) Q_W lives on liquidCooling.cpCuPairs.
 const p=project() as any
 const server=p.constructions.racks.m_2.constructions.servers.e_2
 const old=compileDynamicLiquidGraph(p).edges.find(e=>e.type==='cold_plate')!
 const uid=Object.values(server.constructions.coldPlates as Record<string,any>)[0].uid
 const pair=server.liquidCooling.cpCuPairs.find((x:any)=>(x.uid??x.id??x.name)===uid)
 pair.Q_W=4200
 const next=compileDynamicLiquidGraph(p).edges.find(e=>e.id===old.id)!
 expect(next.params.Q_W).toBe(4200)
})

describe('.liquidrun v4 inputs the project carries (plan T3b)', () => {
  const cdu = (p:any) => p.constructions.cdus.m_1
  const edge = (g:any,id:string) => g.edges.find((e:any)=>e.id===id)
  it('reads the pump speed ratio and the return pressure; default modes add no v4 field', () => {
    const p = project() as any
    const base = compileDynamicLiquidGraph(p)
    expect(edge(base,'m_1_pump').params).toEqual({rpm_ratio:1})
    expect(base.nodes.find((n:any)=>n.id==='m_1_return')!.bc).toEqual({P_Pa:150000})
    expect(edge(base,'m_1_hx').params.mode).toBeUndefined()
    cdu(p).constructions = {...cdu(p).constructions, pump:{rpmRatio:.8}}
    cdu(p).cooling.returnPressureKpa = 160
    const next = compileDynamicLiquidGraph(p)
    expect(edge(next,'m_1_pump').params).toEqual({rpm_ratio:.8})
    expect(next.nodes.find((n:any)=>n.id==='m_1_return')!.bc).toEqual({P_Pa:160000})
  })
  it('a held secondary supply and a flow-held pump', () => {
    const p = project() as any
    Object.assign(cdu(p).cooling, {secondaryControl:'setpoint', supplyTemperatureC:30, pumpControl:'flow', totalFlowRate:60})
    const g = compileDynamicLiquidGraph(p)
    expect(edge(g,'m_1_hx').params).toMatchObject({mode:'setpoint', T_set_K:303.15})
    expect(edge(g,'m_1_pump').params).toEqual({mode:'flow', flow_set_kg_s:60*g.fluid.rho_kg_m3/60000})
  })
  it('a temperature-rise cold plate carries its rise, not a power', () => {
    const p = project() as any
    const server = p.constructions.racks.m_2.constructions.servers.e_2
    const uid = Object.values(server.constructions.coldPlates as Record<string,any>)[0].uid
    Object.assign(server.liquidCooling.cpCuPairs.find((x:any)=>(x.uid??x.id??x.name)===uid), {heatMode:'temperature_rise', dT_K:5})
    const plate = compileDynamicLiquidGraph(p).edges.find((e:any)=>e.type==='cold_plate')!
    expect(plate.params.heat_mode).toBe('temperature_rise')
    expect(plate.params.dT_set_K).toBe(5)
    expect(plate.params.Q_W).toBeUndefined()
  })
})


describe('authored series controls use the complete physical branch route', () => {
  const current = () => JSON.parse(readFileSync(resolve(process.cwd(),
    '../../../case/verification/shinaide-glycol25-full-mms-20261013/Baseline.cfdproj'),'utf8')) as any
  function branch(p:any) {
    const pipe=Object.values(p.constructions.pipeSegments).find((m:any)=>
      m.cooling?.valves?.some((v:any)=>v.type==='flow_control' && v.seriesWithValveId)) as any
    const valve=pipe.cooling.valves.find((v:any)=>v.type==='flow_control' && v.seriesWithValveId)
    const tee=pipe.cooling.valves.find((v:any)=>v.id===valve.seriesWithValveId)
    return {pipe,valve,tee,id:`${pipe.uid}_spoke_${tee.id}`}
  }
  function metrics(g:any,id:string) {
    const pipes=g.edges.filter((e:any)=>e.type==='pipe' && (e.id===id || e.id.startsWith(id+'_before_control')))
    let length=0,volume=0,K=0
    for(const e of pipes) {
      const route=e.params.waypoints_m as number[][]
      const vectors=route.slice(1).map((p,i)=>p.map((v,j)=>v-route[i]![j]!))
      const lengths=vectors.map(v=>Math.hypot(...v))
      const L=lengths.reduce((a,b)=>a+b,0)
      length+=L;volume+=L*Math.PI*e.params.D_m**2/4
      K+=e.params.K_minor_additional ?? 0
      for(let i=1;i<vectors.length;i++) {
        const a=vectors[i-1]!,b=vectors[i]!
        if(lengths[i-1]!>1e-12 && lengths[i]!>1e-12 &&
          a.reduce((sum,v,j)=>sum+v*b[j]!,0)/(lengths[i-1]!*lengths[i]!)<.95) K+=e.params.K_minor_per_bend
      }
    }
    return {length,volume,K,pipes}
  }
  it('accepts a valve at an internal bend waypoint and keeps pipe length, volume and local loss',()=>{
    const p=current(),{valve,tee,id}=branch(p),before=compileDynamicLiquidGraph(p)
    valve.position={...tee.branchWaypoints[1]}
    const after=compileDynamicLiquidGraph(p),a=metrics(before,id),b=metrics(after,id)
    expect(b.length).toBeCloseTo(a.length,14);expect(b.volume).toBeCloseTo(a.volume,14)
    expect(b.K).toBe(a.K);expect(b.K).toBe(.5)
    expect(b.pipes.find((e:any)=>e.id===id+'_before_control')?.params.K_minor_additional).toBe(.5)
    const manifest=compileSavedExperiment(p).liquidrun
    expect(manifest.edges.find(e=>e.id===id+'_before_control')?.params).toHaveProperty('K_minor_additional',.5)
    expect(b.pipes.every((e:any)=>e.params.waypoints_m.length>=2)).toBe(true)
  })
  it('keeps a collinear internal waypoint without inventing a bend loss',()=>{
    const p=current(),{valve,tee,id}=branch(p)
    tee.branchWaypoints.splice(2,0,{...valve.position})
    const g=compileDynamicLiquidGraph(p)
    expect(metrics(g,id).K).toBe(.5)
    expect(metrics(g,id).pipes.every((e:any)=>e.params.K_minor_additional===undefined)).toBe(true)
  })
  it('sorts two controls on one branch by real arc length independent of input order',()=>{
    const p=current(),{pipe,valve,tee,id}=branch(p),before=compileDynamicLiquidGraph(p)
    valve.position={...tee.branchWaypoints[1]}
    const next={...valve,id:'second-series-control',position:{...valve.position,y:.7}}
    pipe.cooling.valves.push(next)
    const after=compileDynamicLiquidGraph(p)
    const reordered=JSON.parse(JSON.stringify(p)),copy=branch(reordered).pipe
    const first=copy.cooling.valves.findIndex((v:any)=>v.id===valve.id)
    const second=copy.cooling.valves.findIndex((v:any)=>v.id===next.id)
    ;[copy.cooling.valves[first],copy.cooling.valves[second]]=[copy.cooling.valves[second],copy.cooling.valves[first]]
    const reverse=compileDynamicLiquidGraph(reordered)
    expect(reverse.nodes).toEqual(after.nodes);expect(reverse.edges).toEqual(after.edges)
    const a=metrics(before,id),b=metrics(after,id)
    expect(b.length).toBeCloseTo(a.length,14);expect(b.volume).toBeCloseTo(a.volume,14);expect(b.K).toBe(a.K)
    expect(new Set(after.edges.map(e=>e.id)).size).toBe(after.edges.length)
    expect(b.pipes).toHaveLength(3)
    expect(b.pipes.every((e:any)=>e.params.D_m===a.pipes[0].params.D_m && e.params.roughness_m===a.pipes[0].params.roughness_m)).toBe(true)
    const valves=after.edges.filter(e=>e.id===`${pipe.uid}_control_${valve.id}` || e.id===`${pipe.uid}_control_${next.id}`)
    expect(valves).toHaveLength(2)
    expect(b.pipes.some((e:any)=>e.from===valves[0]!.to && e.to===valves[1]!.from)).toBe(true)
  })
  it.each(['start','end','off-route','duplicate'] as const)('rejects invalid %s series placement',kind=>{
    const p=current(),{pipe,valve,tee}=branch(p)
    if(kind==='start')valve.position={...tee.branchWaypoints[0]}
    if(kind==='end')valve.position={...tee.branchWaypoints.at(-1)}
    if(kind==='off-route')valve.position={...valve.position,x:valve.position.x+.01}
    if(kind==='duplicate')pipe.cooling.valves.push({...valve,id:'overlapping-control',position:{...valve.position}})
    expect(()=>compileDynamicLiquidGraph(p)).toThrow(/branch route|route position|zero-length/)
  })
})
