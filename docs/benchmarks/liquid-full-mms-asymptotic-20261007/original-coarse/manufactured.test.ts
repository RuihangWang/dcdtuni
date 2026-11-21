import {it,expect} from 'vitest'
import {readFileSync,writeFileSync} from 'node:fs'
import {compileSavedExperiment} from './VARIANT/src/frontend/react/src/liquid/liquidExperiment'
import {reviveKinds,schema} from './VARIANT/src/frontend/react/src/legacy/cfdprojModules'
import {serializeProjectState} from './VARIANT/src/frontend/react/src/io/projectFiles'
const T=process.cwd(),after=process.env.MMS_VARIANT==='after'
const candidate=(tag:string)=>reviveKinds(JSON.parse(readFileSync(T+'/inputs/manufactured-dt-'+tag+'.cfdproj','utf8')))
it('original source rejects explicit benchmark; private source compiles all real saved v4 grids',()=>{
 for(const tag of ['2p0','1p0','0p5','0p25']) {
  const p=candidate(tag)
  if(!after){expect(()=>compileSavedExperiment(p)).toThrow();continue}
  const before=serializeProjectState(p)
  expect(schema().validateDocument(p)).toEqual([])
  const built=compileSavedExperiment(p).liquidrun
  expect(built.schemaVersion).toBe(4)
  expect(built.analysis).toEqual({mode:'transient',initialization:'consistent'})
  expect(serializeProjectState(p)).toBe(before)
  expect(compileSavedExperiment(reviveKinds(JSON.parse(before))).liquidrun).toEqual(built)
  writeFileSync(T+'/results/manufactured-dt-'+tag+'.liquidrun',JSON.stringify(built,null,2))
 }
})
it.runIf(after)('strict actual compiler rejects unit/ID/shape/finite/initial/mode violations',()=>{
 const mutations=[
  (b:any)=>b.units.power='kW',
  (b:any)=>b.sources.solid_W.ids[0]=b.sources.solid_W.ids[1],
  (b:any)=>b.sources.water_W.values.pop(),
  (b:any)=>b.sources.momentum_Pa.values[0].pop(),
  (b:any)=>b.sources.water_W.values[0][0]=NaN,
  (b:any)=>b.initial_state.solid_temperature_K.values[0]=-1,
  (b:any)=>b.flow_signs.values[0]=0,
  (b:any)=>b.initial_temperature_rate.water_temperature_K.ids.pop(),
  (b:any)=>b.times_s[1]=.33,
  (b:any)=>b.hidden_mode=true,
 ]
 for(const [index,mutate] of mutations.entries()) {
  const p:any=candidate('1p0');mutate(p.runDefaults.liquid.experiment.dae.manufactured_benchmark)
  expect(()=>compileSavedExperiment(p),String(index)).toThrow()
 }
})

it.runIf(after)('malformed research analysis reports schema errors without crashing',()=>{
 const p:any=candidate('1p0')
 delete p.runDefaults.liquid.experiment.dae.analysis
 expect(schema().validateDocument(p).length).toBeGreaterThan(0)
 expect(()=>compileSavedExperiment(p)).toThrow()
})
