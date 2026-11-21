import {it,expect} from 'vitest'
import {readFileSync,writeFileSync} from 'node:fs'
import {compileSavedExperiment} from './VARIANT/src/frontend/react/src/liquid/liquidExperiment'
import {reviveKinds,schema} from './VARIANT/src/frontend/react/src/legacy/cfdprojModules'
import {serializeProjectState} from './VARIANT/src/frontend/react/src/io/projectFiles'
const T=process.cwd(),after=process.env.MMS_VARIANT==='after'
const candidate=(tag:string)=>reviveKinds(JSON.parse(readFileSync(T+'/inputs/manufactured-dt-'+tag+'.cfdproj','utf8')))
it('original source rejects explicit benchmark; private source compiles all real saved v4 grids',()=>{
 for(const tag of ['0p125','0p0625']) {
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
