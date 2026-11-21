import { describe, expect, it } from 'vitest'
import { buildExperiment } from '../src/liquid/liquidExperiment'
import type { DynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
const graph = {time:{dt_s:1,N_t:11},nodes:[{id:'s'},{id:'r'}],edges:[
 {id:'pump',type:'pump',from:'r',to:'s',params:{rpm_ratio:.8}},
 {id:'cp',type:'cold_plate',params:{Q_W:3500}},
],events:[]} as unknown as DynamicLiquidGraph
const settings = {mode:'temperature' as const, pumpId:'pump',measurementId:'cp',setpoint:45,kp:.03,ki:.002,bias:.8,loadStep:true,stepTime:5,loadFactor:1.5}
describe('editor experiment scenario',()=>{
 it('converts Celsius once and uses direct cooling action',()=>{
  const out=buildExperiment(graph,settings)
  expect(out.controllers[0]?.pid).toMatchObject({setpoint:318.15,action:'direct',kp:.03,ki:.002})
  expect(out.controllers[0]?.measurement).toEqual({kind:'solid_temperature',target_id:'cp'})
  expect(out.liquidrun.events).toEqual([{time_s:5,target:'cp',field:'Q_W',value:5250}])
  expect(graph.events).toEqual([])
 })
 it('uses reverse pressure action and converts kPa to Pa',()=>{
  const out=buildExperiment(graph,{...settings,mode:'pressure',setpoint:30,kp:.1,ki:.02})
  expect(out.controllers[0]?.pid).toMatchObject({setpoint:30000,action:'reverse',kp:.0001,ki:.00002})
  expect(out.controllers[0]?.measurement).toEqual({kind:'pressure_difference',target_id:'s',reference_id:'r'})
 })
 it('rejects invalid event grids and unknown cold plates',()=>{
  expect(()=>buildExperiment(graph,{...settings,stepTime:2.5})).toThrow(/grid/)
  expect(()=>buildExperiment(graph,{...settings,measurementId:'other'})).toThrow(/cold plate/)
 })
 it('manual mode has no implicit controller and no disabled load step',()=>{
  expect(buildExperiment(graph,{...settings,mode:'manual',loadStep:false}).controllers).toEqual([])
  expect(buildExperiment(graph,{...settings,mode:'manual',loadStep:false}).liquidrun.events).toEqual([])
 })
})
