import { createHash } from 'node:crypto'
import { expect, it } from 'vitest'
import { bindStlFileHashes } from '../src/legacy/cfdrunBuild'
import type { CfdrunV2 } from '../src/schema/cfdrun'
import type { CfdProject } from '../src/schema/cfdproj'

it('binds each uploaded grouped STL byte stream to the strict air manifest',()=>{
  const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2
  const files={'b.stl':'solid b\nendsolid b\n','a.stl':'solid a\nendsolid a\n'}
  bindStlFileHashes(manifest,files)
  const entries=manifest.geometryExport.stlFiles as Record<string,{path:string;contentHash:string}>
  expect(Object.keys(entries)).toEqual(['stl_000','stl_001'])
  expect(entries.stl_000!.path).toBe('a.stl')
  expect(entries.stl_000!.contentHash).toBe(createHash('sha256').update(files['a.stl']).digest('hex'))
  expect(entries.stl_001!.path).toBe('b.stl')
  expect(entries.stl_001!.contentHash).toBe(createHash('sha256').update(files['b.stl']).digest('hex'))
})

it('rejects a hybrid project whose modeled baffle has no exported source STL solid',()=>{
  const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2
  const project={uid:'project',constructions:{baffles:{b1:{uid:'b1',meta:{name:'Baffle_one'}}},floors:{}},
    runDefaults:{hybridHeatBudget:{version:1}}} as unknown as CfdProject
  expect(()=>bindStlFileHashes(manifest,{'merged_baffle.stl':'solid unrelated\nendsolid unrelated\n'},project))
    .toThrow(/Baffle_one|source.*baffle|missing.*STL/i)
})

it('rejects a source model whose object UID disagrees with its construction key',()=>{
  const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2
  const project=({uid:'project',constructions:{baffles:{b1:{uid:'different',meta:{name:'Baffle_one'}}},floors:{}}}) as unknown as CfdProject
  expect(()=>bindStlFileHashes(manifest,{'merged_baffle.stl':'solid Baffle_one\nendsolid Baffle_one\n'},project))
    .toThrow(/UID|b1|different/i)
})
