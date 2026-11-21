import {it,expect} from 'vitest'
import {readFileSync,writeFileSync} from 'node:fs'
import {independentProjectFingerprint} from './independent_project_fingerprint.cjs'
import fingerprintRegistry from './VARIANT/src/frontend/shared/transient/transient_params.json'
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
  const expectedGrid=tag==='0p03125'?{dt_s:0.03125,N_t:257}:{dt_s:0.015625,N_t:513}
  expect(built.time).toEqual({...old.time,...expectedGrid})
  expect((built.time.N_t-1)*built.time.dt_s).toBe(8)
  // Hash the actual new saved project independently; never discard source identity.
  expect(built.source_project.sha256).toBe(independentProjectFingerprint(p,fingerprintRegistry))
  const anchorProject=JSON.parse(JSON.stringify(p))
  anchorProject.runDefaults.liquid.experiment.dt_s=old.time.dt_s
  anchorProject.runDefaults.liquid.experiment.dae.manufactured_benchmark=old.manufactured_benchmark
  anchorProject.transient.gridS=old.time.dt_s
  expect(independentProjectFingerprint(anchorProject,fingerprintRegistry)).toBe(old.source_project.sha256)
  const restored=JSON.parse(JSON.stringify(built)),anchor=JSON.parse(JSON.stringify(old))
  delete restored.manufactured_benchmark;delete anchor.manufactured_benchmark
  // These three independently checked leaf differences alone reflect refinement.
  restored.time.dt_s=old.time.dt_s;restored.time.N_t=old.time.N_t
  restored.source_project.sha256=old.source_project.sha256
  expect(restored).toEqual(anchor)
  expect(built.analysis).toEqual({mode:'transient',initialization:'consistent'})
  expect(serializeProjectState(p)).toBe(before)
  expect(compileSavedExperiment(reviveKinds(JSON.parse(before))).liquidrun).toEqual(built)
  writeFileSync(T+'/results/manufactured-dt-'+tag+'.liquidrun',JSON.stringify(built,null,2))
 }
})
