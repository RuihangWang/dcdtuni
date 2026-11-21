import {it,expect} from 'vitest'
import {readFileSync,writeFileSync} from 'node:fs'
import {compileSavedExperiment} from './VARIANT/src/frontend/react/src/liquid/liquidExperiment'
import {reviveKinds,schema} from './VARIANT/src/frontend/react/src/legacy/cfdprojModules'
import {serializeProjectState} from './VARIANT/src/frontend/react/src/io/projectFiles'
const T=process.cwd(),after=process.env.MMS_VARIANT==='after'
const candidate=(tag:string)=>reviveKinds(JSON.parse(readFileSync(T+'/inputs/manufactured-dt-'+tag+'.cfdproj','utf8')))
it('compile only the requested new grid through original saved-experiment producer',()=>{
 const tag=process.env.MMS_ASYMPTOTIC_TAG
 expect(['0p03125','0p015625']).toContain(tag)
 for(const tag of [process.env.MMS_ASYMPTOTIC_TAG!]) {
  const p=candidate(tag)
  if(!after){expect(()=>compileSavedExperiment(p)).toThrow();continue}
  const before=serializeProjectState(p)
  expect(schema().validateDocument(p)).toEqual([])
  const built=compileSavedExperiment(p).liquidrun
  expect(built.schemaVersion).toBe(4)
  const old=JSON.parse(readFileSync(T+'/results/original-anchor-0p0625.liquidrun','utf8'))
  expect(built.manufactured_benchmark.initial_state).toEqual(old.manufactured_benchmark.initial_state)
  const restored=JSON.parse(JSON.stringify(built));delete restored.manufactured_benchmark;delete old.manufactured_benchmark
  restored.dt=old.dt;restored.N_t=old.N_t
  expect(restored).toEqual(old)
  expect(built.analysis).toEqual({mode:'transient',initialization:'consistent'})
  expect(serializeProjectState(p)).toBe(before)
  expect(compileSavedExperiment(reviveKinds(JSON.parse(before))).liquidrun).toEqual(built)
  writeFileSync(T+'/results/manufactured-dt-'+tag+'.liquidrun',JSON.stringify(built,null,2))
 }
})
