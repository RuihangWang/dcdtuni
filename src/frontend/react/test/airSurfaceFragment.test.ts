import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {createRequire} from 'node:module'
import {it,expect} from 'vitest'
const require=createRequire(import.meta.url)
const api=require(resolve(process.cwd(),'../shared/js/cfdrun_export.js'))
const golden=JSON.parse(readFileSync(resolve(process.cwd(),'test/fixtures/airSurfaceOriginalExport.json'),'utf8'))
for(const sample of golden.cases)it('owned export-solid fragment: '+sample.label,()=>{
 const entry=structuredClone(sample.entry),original=JSON.stringify(entry)
 const a=api.resolveAirSurfaceFragment(sample.solidName,entry),b=api.resolveAirSurfaceFragment(sample.solidName,entry)
 expect(a.sourceScope).toBe('export-solid')
 expect(a.sourceSolidName).toBe(sample.solidName)
 expect(a.sourceEntry).toEqual(entry)
 expect(JSON.stringify(a.boundaryBindings)).toBe(JSON.stringify(sample.expected.boundaryBindings))
 expect(JSON.stringify(a.porousBindings)).toBe(JSON.stringify(sample.expected.porousBindings))
 expect(a.sourceTerms).toEqual({})
 expect(a).not.toHaveProperty('entityUid')
 expect(a).not.toHaveProperty('modelUid')
 if(entry.perforation){
  entry.perforation.unknownExtension.keep=999
  expect(a.sourceEntry.perforation.unknownExtension.keep).toBe(0)
  const key=Object.keys(a.porousBindings)[0]
  if(key && a.porousBindings[key]?.parameters.perforation){
   a.porousBindings[key].parameters.perforation.unknownExtension.keep=998
   expect(b.porousBindings[key].parameters.perforation.unknownExtension.keep).toBe(0)
   expect(a.sourceEntry.perforation.unknownExtension.keep).toBe(0)
  }
 }else expect(JSON.stringify(entry)).toBe(original)
 expect(b.sourceEntry).toEqual(sample.entry)
})
it('does not turn an unknown/prototype kind into physics or infer a missing exported solid',()=>{
 expect(()=>api.resolveAirSurfaceFragment('x',{kind:'toString',patchName:'x'})).toThrow(/Unsupported/)
 expect(()=>api.resolveAirSurfaceFragment('x',{kind:'future-device',patchName:'x'})).toThrow(/Unsupported/)
 expect(()=>api.resolveAirSurfaceFragment('',{kind:'baffle',patchName:'x'})).toThrow(/exported solid/)
 expect(()=>api.resolveAirSurfaceFragment('x',{kind:'baffle',patchName:2})).toThrow(/patchName/)
})
it('existing emitter owns source perforation and retains serialized output/ordering',()=>{
 const sample=golden.cases.find((x:any)=>x.entry.perforation?.openAreaPct===31)
 const entry=structuredClone(sample.entry),out:any={}
 api.emitPorousBaffleEntry(entry,out)
 expect(JSON.stringify(out)).toBe(JSON.stringify(sample.expected.porousBindings))
 entry.perforation.unknownExtension.keep=789
 expect(Object.values<any>(out)[0].parameters.perforation.unknownExtension.keep).toBe(0)
})
