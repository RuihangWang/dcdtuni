import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import vm from 'node:vm'
import { describe, it, expect, vi } from 'vitest'
import * as THREE from 'three'
import { pbrMaterials } from '../src/legacy/geometryBuilder'
import { createSceneBuilder, emptyRegistries } from '../src/interaction/sceneBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { DomainProject } from '../src/domain'
import { materialRecipeKernel as kernel } from '../src/domain/materialRecipeKernel'
import type { CfdProject } from '../src/schema/cfdproj'
import original from './fixtures/boxMaterialRecipeBaseline.json'
const shared = resolve(process.cwd(),'../shared/js/box_material_recipe.js')
const actual = original.cases.filter(x=>x.actual&&x.label.endsWith('-library-present-unloaded'))
describe('shared material creation intent has no renderer ownership',()=>{
  it('runs the classic script without renderer/DOM globals and owns fresh public defaults',()=>{
    const c=vm.createContext({});vm.runInContext(readFileSync(shared,'utf8'),c)
    expect(vm.runInContext('typeof THREE+"/"+typeof window+"/"+typeof document',c)).toBe('undefined/undefined/undefined')
    const a=kernel.paintedSteelRole(),b=kernel.paintedSteelRole();a.roughness=99
    expect(b.roughness).toBe(.55)
    expect(vm.runInContext('boxMaterialRecipe.materialPolicy("box",{color:"#789abc"}).parameters.shininess',c)).toBe(34)
  })
  it('standalone CommonJS consumes the exact same numeric policy without a browser fallback',()=>{
    const result=spawnSync(process.execPath,['-e',`const assert=require('node:assert/strict');const k=require(${JSON.stringify(shared)});assert.equal(typeof global.THREE,'undefined');assert.equal(typeof global.window,'undefined');assert.deepEqual(k.materialPolicy('box',{opacity:.3,transparent:false,depthWrite:false}).parameters,{side:'front',polygonOffset:true,polygonOffsetFactor:1,transparent:false,opacity:.3,depthWrite:false,shininess:34,specular:0x111111});process.stdout.write('pure-material-ok')`],{encoding:'utf8',timeout:5000})
    expect(result.status,result.stderr).toBe(0);expect(result.stdout).toBe('pure-material-ok')
  })
  for(const sample of actual)it(sample.label+' resolves no-kind records and matches actual original carrier scalars',()=>{
    const doc={schemaVersion:5,constructions:{[sample.sample.bucket]:{[sample.sample.uid]:structuredClone(sample.sample.model)}}} as unknown as CfdProject
    const e=new DomainProject(doc).topLevel()[0]!,before=JSON.stringify(e.record)
    const parts=e.geometryProduct().parts
    const part=parts.find(x=>x.isBaseWall)!
    const recipe=e.materialRecipe(part,{libraryAvailable:true})
    const carrier=sample.creation.materials.find(x=>x.userData.geometryPartId===part.partId)!.material
    if(Array.isArray(carrier))throw new Error('unexpected material array')
    expect(recipe.policy.parameters.opacity).toBe(carrier.opacity)
    expect(recipe.policy.parameters.transparent).toBe(carrier.transparent)
    expect(recipe.policy.parameters.depthWrite).toBe(carrier.depthWrite)
    expect(recipe.policy.parameters.polygonOffsetFactor).toBe(carrier.polygonOffsetFactor)
    if(recipe.policy.model==='library'){
      expect(recipe.policy.pbr.roughness).toBe(carrier.roughness);expect(recipe.policy.pbr.metalness).toBe(carrier.metalness)
      expect(recipe.policy.pbr.role).toBe((carrier.userData as Record<string, unknown>).pbrRole)
    }else expect(recipe.policy.parameters.shininess).toBe(carrier.shininess)
    const next=e.materialRecipe(part,{libraryAvailable:true});recipe.source.patchIds.push('changed')
    recipe.policy.parameters.opacity=0;expect(next.policy.parameters.opacity).toBe(carrier.opacity)
    expect(next.source.patchIds).not.toContain('changed');expect(JSON.stringify(e.record)).toBe(before)
  })
  it('passes the current public role/context and makes no cache/readiness claim',()=>{
    const role={...kernel.paintedSteelRole(),roughness:.66,tileM:.2}
    const before=JSON.stringify(role)
    const p=kernel.materialPolicy('cdu-body',{color:'#123456'},{libraryAvailable:true,paintedSteelRole:role,meanRoughness:.4})
    expect(p.pbr.roughness).toBe(.66);expect(p.pbr.tileM).toBe(.2);expect(p.pbr.boundRoughness).toBe(1)
    expect(p.uv.physicalScale).toBe(5);expect(JSON.stringify(role)).toBe(before)
    const q=kernel.materialPolicy('cdu-body',{uvNormalize:{type:'stretch'}},{libraryAvailable:true})
    expect(q.uv.physicalScale).toBeNull()
  })
  it('honors mutable public painted-role/mean context in the live carrier and scales each owned UV only once',()=>{
    const lib=pbrMaterials(), oldRole=lib.ROLES['painted-steel']!, oldMean=lib.SETS['painted-steel']!.meanRoughness
    const load=vi.spyOn(THREE.TextureLoader.prototype,'load').mockImplementation(()=>new THREE.Texture())
    lib._resetForTests();lib.ROLES['painted-steel']={...oldRole,roughness:.66,tileM:.2};lib.SETS['painted-steel']!.meanRoughness=.4
    const project=stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
    const group=new THREE.Group(),wrapper=new THREE.Group();wrapper.add(group)
    const builder=createSceneBuilder({globalGroup:group,wrapperGroup:wrapper,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:true})
    try {
      for(const map of ['diffuse','normal','roughness'] as const)lib.registerTexture('painted-steel',map,new THREE.Texture())
      const opts={objectKind:'cdu-body',color:'#123456',skipEdges:true,renderOrder:3,polygonOffsetFactor:2,keepUserColor:false,doubleSide:true,depthWrite:false}
      const spawn=builder.api.spawnRenderable as (g:THREE.Group,geo:THREE.BufferGeometry,o:Record<string,unknown>)=>THREE.Mesh
      const geometry=new THREE.PlaneGeometry(2,1)
      const mesh=spawn(group,geometry,{...opts})
      const policy=kernel.materialPolicy('cdu-body',opts,{libraryAvailable:true,paintedSteelRole:lib.ROLES['painted-steel'] as ReturnType<typeof kernel.paintedSteelRole>,meanRoughness:lib.SETS['painted-steel']!.meanRoughness})
      const mat=mesh.material as THREE.MeshStandardMaterial
      expect(mat.userData.pbrRoughness).toBe(policy.pbr.roughness);expect(mat.roughness).toBe(policy.pbr.boundRoughness)
      expect(mat.side).toBe(THREE.DoubleSide);expect(mat.depthWrite).toBe(false)
      expect(mesh.renderOrder).toBe(3);expect(mat.polygonOffsetFactor).toBe(2)
      expect(geometry.userData.pbrUvScaled).toBe(true);expect(geometry.getAttribute('uv').getY(0)).toBe(5)
      expect(()=>lib.scaleUv(geometry,5,5)).toThrow(/already scaled/)
      const ready=new THREE.PlaneGeometry(2,1),bytes=Array.from(ready.getAttribute('uv').array)
      spawn(group,ready,{...opts,pbrUvReady:true});expect(Array.from(ready.getAttribute('uv').array)).toEqual(bytes)
      expect(ready.userData.pbrUvScaled).toBeUndefined()
    }finally{builder.clear();lib.ROLES['painted-steel']=oldRole;lib.SETS['painted-steel']!.meanRoughness=oldMean;lib._resetForTests();load.mockRestore()}
  })
  it('creates missing-color materials without warnings while retaining Phong and library defaults',()=>{
    const lib=pbrMaterials(), globals=window as unknown as {pbrMaterials?:typeof lib}
    const context=new Proxy({}, {get(_target,key){
      if(key==='createLinearGradient'||key==='createRadialGradient')return ()=>({addColorStop(){}})
      if(key==='getImageData'||key==='createImageData')return (...args:number[])=>({data:new Uint8ClampedArray((args.at(-2)??1)*(args.at(-1)??1)*4)})
      return ()=>undefined
    }})
    const canvas=vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(()=>context as CanvasRenderingContext2D)
    const warn=vi.spyOn(console,'warn').mockImplementation(()=>{})
    const originalLibrary=globals.pbrMaterials
    try{
      for(const [kind,library] of [[undefined,false],['box',false],['cdu-body',false],['cdu-body',true],['baffle',false]] as const){
        lib._resetForTests();globals.pbrMaterials=library?lib:undefined
        const project=stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
        const group=new THREE.Group(),wrapper=new THREE.Group();wrapper.add(group)
        const builder=createSceneBuilder({globalGroup:group,wrapperGroup:wrapper,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:true})
        const spawn=builder.api.spawnRenderable as (g:THREE.Group,geo:THREE.BufferGeometry,o:Record<string,unknown>)=>THREE.Mesh
        try{
          warn.mockClear()
          const missing=spawn(group,new THREE.PlaneGeometry(2,1),{objectKind:kind,color:undefined,skipEdges:true})
          expect(warn.mock.calls.filter(([message])=>String(message).includes("parameter 'color'")),`${kind??'untyped'}/${library}`).toEqual([])
          const material=missing.material as THREE.MeshPhongMaterial|THREE.MeshStandardMaterial
          const defaultColor=library?new THREE.Color(lib.ROLES['painted-steel']!.color):new THREE.MeshPhongMaterial().color
          expect(material.color.toArray()).toEqual(defaultColor.toArray())
          const black=spawn(group,new THREE.PlaneGeometry(2,1),{objectKind:kind,color:0,skipEdges:true,keepUserColor:true})
          expect((black.material as THREE.MeshPhongMaterial).color.getHex()).toBe(0)
          expect(warn.mock.calls.filter(([message])=>String(message).includes("parameter 'color'"))).toEqual([])
        }finally{builder.clear()}
      }
    }finally{globals.pbrMaterials=originalLibrary;lib._resetForTests();warn.mockRestore();canvas.mockRestore()}
  })
  it('only admits BOX/CDU capabilities, never infers physical type from a display name',()=>{
    const sample=actual[0]!,record=structuredClone(sample.sample.model)
    record.meta.name='PDU_only_name'
    const e=new DomainProject({schemaVersion:5,constructions:{acus:{[sample.sample.uid]:record}}} as unknown as CfdProject).topLevel()[0]!
    expect(()=>e.materialRecipe({} as any)).toThrow(/Unsupported material recipe kind/)
    expect(()=>kernel.materialPolicy('custom',{})).toThrow(/Unsupported/)
  })
})
