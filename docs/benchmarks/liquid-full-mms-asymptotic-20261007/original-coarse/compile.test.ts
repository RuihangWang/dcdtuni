import {it,expect} from 'vitest'
import {readFileSync,writeFileSync} from 'node:fs'
import {compileSavedExperiment} from './VARIANT/src/frontend/react/src/liquid/liquidExperiment'
import {reviveKinds,schema} from './VARIANT/src/frontend/react/src/legacy/cfdprojModules'
import {liveProject,transientModel} from './VARIANT/src/frontend/react/src/transient/model'
const T=process.cwd()
it('real React baseline compile and strict admission',()=>{
 let p:any=reviveKinds(JSON.parse(readFileSync(T+'/inputs/ring-base.cfdproj','utf8')))
 expect(schema().validateDocument(p)).toEqual([])
 const steady=compileSavedExperiment(p).liquidrun
 writeFileSync(T+'/results/'+process.env.MMS_VARIANT+'-baseline.liquidrun',JSON.stringify(steady,null,2))
 p.runDefaults.liquid.experiment.dae.analysis={mode:'transient',initialization:'consistent'}
 p.runDefaults.liquid.experiment.duration_s=8;p.runDefaults.liquid.experiment.dt_s=1
 // Explicit private saved namespace and v4 timeline, no live store mutations.
 p=liveProject(p,0);p.schemaVersion=5;p.transient={...p.transient,mode:'transient',airMode:'steady',liquidMode:'transient',durationS:8,gridS:1,start:{kind:'uniform',uniform:{waterT_C:20,solidT_C:25}},tracks:transientModel().tracksFromFields(p)}
 writeFileSync(T+'/inputs/transient-base.cfdproj',JSON.stringify(p,null,2))
 const trans=compileSavedExperiment(p).liquidrun
 writeFileSync(T+'/results/'+process.env.MMS_VARIANT+'-transient-base.liquidrun',JSON.stringify(trans,null,2))
 expect(trans.schemaVersion).toBe(4)
})
