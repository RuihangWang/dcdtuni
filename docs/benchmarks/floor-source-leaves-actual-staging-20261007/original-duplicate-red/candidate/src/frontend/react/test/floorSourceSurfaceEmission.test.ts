import {createHash} from 'node:crypto'
import {expect,it,vi} from 'vitest'
import * as THREE from 'three'
import {stateV4} from '../src/legacy/cfdprojModules'
import {bindStlFileHashes} from '../src/legacy/cfdrunBuild'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import type {CfdProject} from '../src/schema/cfdproj'
import type {CfdrunV2} from '../src/schema/cfdrun'
import fixture from './fixtures/floorSourceSurfaceEmission.json'
const manifest=()=>({geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}}) as CfdrunV2
function scene(enabled=true) {
 const record=structuredClone(fixture.model),raw=stateV4().getInitialStateV4() as unknown as CfdProject
 record.meta.enabled=enabled;raw.uid='controlled-floor-project';raw.constructions={floors:{[record.uid]:record}} as unknown as CfdProject['constructions']
 const project=stateV4().normalizeStateV4(raw) as unknown as CfdProject
 const model=project.constructions!.floors![record.uid]!
 const globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const host:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:false}
 const builder=createSceneBuilder(host);builder.rebuildModels([record.uid]);globalGroup.updateMatrixWorld(true)
 return {project,model,builder,host,files:()=>builder.api.buildGroupedStlMap([record.uid],false),uid:record.uid}
}
it('binds every actual raised-floor base/patch fragment to the saved canonical top face',()=>{
 const e=scene()
 try{
  const files=e.files(),m=manifest();bindStlFileHashes(m,files,e.project)
  console.info('ACTUAL_FLOOR_PHYSICAL_BYTES',JSON.stringify(Object.fromEntries(Object.entries(files).map(([k,v])=>[k,{sha256:createHash('sha256').update(v).digest('hex'),bytes:Buffer.byteLength(v)}]))))
  const regions=Object.values(m.geometryExport.sourceRegions??{}),surfaces=Object.values(m.geometryExport.sourceSurfaces??{})
  expect(regions).toHaveLength(31);expect(surfaces).toHaveLength(31)
  expect(surfaces.filter(row=>row.solidName.includes('_patch_'))).toHaveLength(30)
  for(const row of surfaces){
   expect(row.sourceObjectUid).toBe(e.uid);expect(row.sourceFaceUid).toBe(`${e.project.uid}/${e.uid}/top`)
   expect(row.representation).toBe('zeroThicknessSheet')
   const region=regions.find(r=>r.stlFileId===row.stlFileId&&r.solidName===row.solidName)!
   expect(region.sourceRegionUid).not.toBe(row.sourceFaceUid)
   expect(region.sourceEntityUid).toBe(e.uid)
   const path=(m.geometryExport.stlFiles[row.stlFileId] as {path:string}).path
   const block=[...files[path]!.matchAll(/^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?/gm)].find(x=>x[1]!.trim()===row.solidName)!
   expect(region.sourceRegionSha256).toBe(createHash('sha256').update(block[0]).digest('hex'))
   expect(region.sourceFacetCount).toBe((block[2]!.match(/\bfacet[ \t]+normal\b/g)||[]).length)
  }
 }finally{e.builder.clear()}
})
it('recognizes a hybrid wallBaffle floor covered by its real fragmented sheet export',()=>{
 const e=scene()
 try{const files=e.files();e.project.runDefaults={...e.project.runDefaults,hybridHeatBudget:{version:1}} as CfdProject['runDefaults']
  const m=manifest();expect(()=>bindStlFileHashes(m,files,e.project)).not.toThrow();expect(Object.keys(m.geometryExport.sourceSurfaces??{})).toHaveLength(31)
 }finally{e.builder.clear()}
})
it('certifies explicitly included disabled floors through the actual temporary canonical renderer',()=>{
 const e=scene(false)
 try{const m=manifest();bindStlFileHashes(m,e.files(),e.project);expect(Object.keys(m.geometryExport.sourceSurfaces??{})).toHaveLength(31);expect(e.model.meta!.enabled).toBe(false)}finally{e.builder.clear()}
})
it('does not invent fragmented face identities from a copied legacy STL map',()=>{
 const e=scene()
 try{const m=manifest();bindStlFileHashes(m,{...e.files()},e.project);expect(m.geometryExport.sourceRegions).toBeUndefined();expect(Object.keys(m.geometryExport.sourceSurfaces??{})).toHaveLength(0)}finally{e.builder.clear()}
})
it('rejects stale rendered geometry before it can certify a current floor source face',()=>{
 const e=scene()
 try{e.model.geometry!.location!.y+=.125;expect(()=>e.files()).toThrow(/stale canonical face ancestry/)}finally{e.builder.clear()}
})
it('rejects a same-name mesh that did not come from the canonical floor producer',()=>{
 const e=scene()
 try{
  (e.builder.api._withUnmergedModels as (callback:()=>void)=>void)(()=>{
   const original=e.host.registries.renderMeshes.find(mesh=>mesh.userData.modelId===e.uid&&mesh.userData.exportName)! as THREE.Mesh
   const injected=new THREE.Mesh(original.geometry.clone(),original.material);injected.userData={...original.userData}
   original.parent!.add(injected);injected.updateMatrixWorld(true);e.host.registries.renderMeshes.push(injected)
  })
  expect(()=>e.files()).toThrow(/canonical face ancestry/)
 }finally{e.builder.clear()}
})
it.each([['missing',undefined],['wrong face',{sourceFaceId:'front',representation:'zeroThicknessSheet'}],['wrong representation',{sourceFaceId:'top',representation:'surfaceFace'}]])('rejects controlled %s emitter face metadata in the actual binder',(_label,sourceSurface)=>{
 const e=scene()
 try{
  const files=e.files(),module=(window as unknown as {geometryScene:{getGroupedStlSourceCatalogue:(files:Record<string,string>)=>any}}).geometryScene
  const actual=module.getGroupedStlSourceCatalogue(files)
  const spy=vi.spyOn(module,'getGroupedStlSourceCatalogue').mockReturnValue({...actual,regions:actual.regions.map((row:any)=>({...row,sourceSurface}))})
  try{expect(()=>bindStlFileHashes(manifest(),files,e.project)).toThrow(/canonical face ancestry/)}finally{spy.mockRestore()}
 }finally{e.builder.clear()}
})
it('rejects retained real emitted floor sources after the source project or bytes change',()=>{
 const e=scene()
 try{const files=e.files();expect(()=>bindStlFileHashes(manifest(),files,{...e.project,uid:'different'})).toThrow(/current project/);files[Object.keys(files)[0]!] += '\n';expect(()=>bindStlFileHashes(manifest(),files,e.project)).toThrow(/exact uploaded bytes/)}finally{e.builder.clear()}
})
