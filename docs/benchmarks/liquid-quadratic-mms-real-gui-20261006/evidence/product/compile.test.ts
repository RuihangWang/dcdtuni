import {it,expect} from 'vitest'
import {readFileSync,writeFileSync} from 'node:fs'
import {createHash} from 'node:crypto'
import {compileSavedExperiment,readExperiment,resolveSavedExperiment} from './source/src/frontend/react/src/liquid/liquidExperiment'
import {reviveKinds,schema} from './source/src/frontend/react/src/legacy/cfdprojModules'
import {readProjectFileText,serializeProjectState} from './source/src/frontend/react/src/io/projectFiles'
import {cfdrunExport} from './source/src/frontend/react/src/legacy/cfdrunBuild'
const T=process.cwd(),receipt:any[]=[]
for(const tag of ['2p0','1p0','0p5','0p25'])it('actual project load/save→public React strict v4 compile '+tag,()=>{
 const original=readFileSync(T+'/inputs/quadratic-lowflow-dt-'+tag+'.cfdproj','utf8')
 const loaded=readProjectFileText(original)
 expect(loaded.ok).toBe(true)
 if(!loaded.ok)throw new Error(loaded.error)
 const p=reviveKinds(loaded.project)
 expect(schema().validateDocument(p)).toEqual([])
 expect(resolveSavedExperiment(p)).toEqual(readExperiment(p))
 const before=serializeProjectState(p)
 const actual=compileSavedExperiment(p).liquidrun as any
 expect(actual.schemaVersion).toBe(4)
 expect(actual.analysis).toEqual({mode:'transient',initialization:'consistent'})
 expect(actual.nodes.length).toBe(229);expect(actual.edges.length).toBe(286)
 expect(actual.manufactured_benchmark.formula_id).toBe('full-ring-positive-curvature-lowcycle-v1')
 expect(actual.manufactured_benchmark).toEqual(readExperiment(p).dae!.manufactured_benchmark)
 expect(actual.source_project.sha256).toBe(cfdrunExport().projectContentHashSync(p))
 expect(actual.source_project.sha256).not.toBe('b65e843cf4fa1315456b7151b91e9a03e619149436f2a299c768a39b8a9976a9')
 expect(serializeProjectState(p)).toBe(before)
 const reopened=readProjectFileText(before)
 expect(reopened.ok).toBe(true)
 if(!reopened.ok)throw new Error(reopened.error)
 expect(compileSavedExperiment(reviveKinds(reopened.project)).liquidrun).toEqual(actual)
 const emitted=JSON.stringify(actual,null,2)
 writeFileSync(T+'/results/compiled-dt-'+tag+'.liquidrun',emitted)
 writeFileSync(T+'/results/normalized-dt-'+tag+'.cfdproj',before)
 receipt.push({tag,schemaVersion:actual.schemaVersion,nodes:actual.nodes.length,edges:actual.edges.length,source_project:actual.source_project,benchmark_source_sha256:actual.manufactured_benchmark.source_sha256,graph_layout_sha256:actual.manufactured_benchmark.graph_layout_sha256,input_bytes:Buffer.byteLength(emitted),input_raw_sha256:createHash('sha256').update(emitted).digest('hex'),normalized_project_raw_sha256:createHash('sha256').update(before).digest('hex'),saved_reopened_compile_exact:true,compile_did_not_mutate_source:true,real_public_compiler:true})
 writeFileSync(T+'/results/react-compiler-receipt.json',JSON.stringify(receipt,null,2))
})
