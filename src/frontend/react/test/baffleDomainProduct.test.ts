import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {spawnSync} from 'node:child_process'
import vm from 'node:vm'
import {describe,it,expect} from 'vitest'
import {DomainProject} from '../src/domain'
import {entityRegistry} from '../src/domain/entityRegistry'
import type {CfdProject} from '../src/schema/cfdproj'
import golden from './fixtures/baffleProductOriginalCarrier.json'
const bytes=(v:ArrayBufferView)=>Buffer.from(v.buffer,v.byteOffset,v.byteLength).toString('hex')
const shared=resolve(process.cwd(),'../shared')
const entity=(record:Record<string,unknown>,bucket='baffles')=>new DomainProject({schemaVersion:5,uid:'proof',unknownRoot:{kept:0},
 constructions:{[bucket]:{[String(record.uid)]:record}}} as unknown as CfdProject).topLevel()[0]!
describe('actual-kind availability; record support does not imply numerical physics',()=>{
 it('queries every current registry type and separates a carrier from an owned product',()=>{
  for(const type of entityRegistry.types()){
   const caps=entityRegistry.capabilities(type)
   expect(caps.record).toBe('typed')
   expect(['implemented','legacy-delegate','none','unsupported']).toContain(caps.geometryProduct)
   expect(caps.physicalDeclarations).toBe('record-view')
  }
  for(const type of ['boxes','cdus','baffles'])expect(entityRegistry.capabilities(type).geometryProduct).toBe('implemented')
  for(const type of ['walls','floors','partitionWalls','racks','servers','acus']){
   expect(entityRegistry.capabilities(type).geometryProduct).toBe('unsupported')
   expect(entityRegistry.capabilities(type).carrierGeometry).toBe('legacy-delegate')
  }
  for(const type of ['liquidFlowNetworks','instrumentProbe','modelDefinition','coolingPort']){
   expect(entityRegistry.capabilities(type).geometryProduct).toBe('none')
   expect(entityRegistry.capabilities(type).carrierGeometry).toBe('none')
  }
  expect(entityRegistry.capabilities('coldPlatePair').physicalParameters).toBe('implemented')
  expect(entityRegistry.capabilities('baffles').physicalParameters).toBe('none')
  expect(entityRegistry.capabilities('baffles').airBoundaryScope).toBe('export-solid')
  expect(entityRegistry.capabilities('future')).toMatchObject({record:'opaque',geometryProduct:'unsupported'})
  expect(entityRegistry.capabilities('baffles',2).record).toBe('opaque')
 })
 it('handles source-kind mismatch and BOX subtype without inventing fallback products',()=>{
  const m=structuredClone(golden.cases[0]!.model)
  const wrong=entity({...m,kind:'cdu'})
  expect(wrong.capabilities().record).toBe('opaque')
  expect(()=>wrong.geometryProduct()).toThrow(/Unsupported entity/)
  const subtype=entity({...m,kind:'cold_plate',meta:{...m.meta,legacyKind:'cold_plate'}},'boxes')
  expect(subtype.capabilities().geometryProduct).toBe('unsupported')
  expect(subtype.capabilities().carrierGeometry).toBe('legacy-delegate')
  expect(()=>subtype.geometryProduct()).toThrow(/Unsupported geometry product kind/)
 })
})
describe('owned Baffle domain product matches the captured original Three carrier',()=>{
 for(const sample of golden.cases)it(sample.label,()=>{
  const record=structuredClone(sample.model),before=JSON.stringify(record),e=entity(record)
  const a=e.geometryProduct(),b=e.geometryProduct()
  expect(a.kind).toBe('baffle');expect(a.modelUid).toBe(record.uid)
  expect(a.parts).toHaveLength(sample.expected.parts.length)
  a.parts.forEach((part,i)=>{
   const original=sample.expected.parts[i]!
   expect(bytes(part.positions)).toBe(original.position)
   expect(bytes(part.normals)).toBe(original.normal)
   expect(bytes(part.uvs)).toBe(original.rawUv)
   expect(bytes(part.indices)).toBe(original.index)
   expect(part.indices.constructor.name).toBe(original.indexType)
   expect(part).toMatchObject({faceId:original.faceId,exportName:original.exportName,isBaseWall:original.isBaseWall})
   expect(part.refIds).toEqual(original.refIds??[])
   expect(part.wallIndex).toBe(original.wallIndex)
   for(const key of ['positions','normals','uvs','indices'] as const){
    const value=b.parts[i]![key][0];part[key][0]=987
    expect(b.parts[i]![key][0]).toBe(value)
   }
   const appearance=e.materialRecipe(b.parts[i]!)
   expect(appearance.kind).toBe('baffle')
   expect(appearance.source.ownerUid).toBe(record.uid)
   expect(appearance.policy.parameters.opacity).toBe(original.material.opacity)
   expect(appearance.policy.parameters.transparent).toBe(original.material.transparent)
   expect(appearance.policy.parameters.depthWrite).toBe(original.material.depthWrite)
   expect(appearance.policy.parameters.side).toBe('double')
   expect(appearance.policy.parameters.roughness).toBe(original.material.roughness)
   expect(appearance.policy.parameters.metalness).toBe(original.material.metalness)
   expect(appearance.policy.parameters.polygonOffsetFactor).toBe(original.material.polygonOffsetFactor)
   expect(appearance.policy.renderOrder).toBe(original.renderOrder)
   appearance.source.patchIds.push('alien');appearance.policy.parameters.opacity=99
   expect(e.materialRecipe(b.parts[i]!).source.patchIds).not.toContain('alien')
   expect(e.materialRecipe(b.parts[i]!).policy.parameters.opacity).toBe(original.material.opacity)
  })
  expect(JSON.stringify(record)).toBe(before)
  const saved=e.toRecord();saved.unknownExtension={alien:true};expect(record).not.toHaveProperty('unknownExtension.alien')
 })
 it('reads legacy normalization defaults without mutating JSON and rejects missing source geometry',()=>{
  const m=structuredClone(golden.cases[0]!.model)
  Reflect.deleteProperty(m.geometry.faces.front,'wallOpacities')
  const before=JSON.stringify(m),e=entity(m)
  expect(e.geometryProduct().parts.length).toBeGreaterThan(0)
  expect(JSON.stringify(m)).toBe(before)
  expect(()=>entity({...m,geometry:{}}).geometryProduct()).toThrow(/finite source size and front face/)
  expect(()=>entity({...m,kind:'probe'},'sensors').geometryProduct()).toThrow()
 })
 it('runs the same pure producer in CommonJS without THREE/window/document',()=>{
  const code="const assert=require('node:assert/strict');const k=require("+JSON.stringify(resolve(shared,'js/baffle_geometry_product.js'))+");const g=require("+JSON.stringify(resolve(process.cwd(),'test/fixtures/baffleProductOriginalCarrier.json'))+");assert.equal(typeof global.THREE,'undefined');assert.equal(typeof global.window,'undefined');assert.equal(typeof global.document,'undefined');for(const s of g.cases){const p=k.createProduct(s.model);p.parts.forEach((x,i)=>assert.equal(Buffer.from(x.positions.buffer).toString('hex'),s.expected.parts[i].position));}process.stdout.write('baffle-pure-cjs-ok')"
  const result=spawnSync(process.execPath,['-e',code],{encoding:'utf8',timeout:15000})
  expect(result.status,result.stderr).toBe(0);expect(result.stdout).toBe('baffle-pure-cjs-ok')
 })
 it('runs the classic-script producer without a renderer or DOM',()=>{
  const c=vm.createContext({});vm.runInContext('globalThis.window=globalThis',c)
  for(const rel of ['vendor/polygon-clipping.umd.js','vendor/earcut.umd.js','js/stl_naming.js','js/box_geometry_product.js','js/baffle_geometry_product.js']){
   vm.runInContext(readFileSync(resolve(shared,rel),'utf8'),c)
  }
  expect(vm.runInContext('typeof THREE+"/"+typeof window+"/"+typeof document',c)).toBe('undefined/object/undefined')
  c.input=structuredClone(golden.cases[0]!.model)
  const p=vm.runInContext('baffleGeometryProduct.createProduct(input)',c)
  expect(bytes(p.parts[0].positions)).toBe(golden.cases[0]!.expected.parts[0]!.position)
 })
})
