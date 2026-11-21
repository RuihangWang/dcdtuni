import {createHash} from 'node:crypto'
import {expect,it} from 'vitest'
import * as THREE from 'three'
import {stateV4} from '../src/legacy/cfdprojModules'
import {bindStlFileHashes} from '../src/legacy/cfdrunBuild'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import type {CfdProject} from '../src/schema/cfdproj'
import type {CfdrunV2} from '../src/schema/cfdrun'
import golden from './fixtures/boxGeometryProductParity.json'
function actualExport() {
 const record=structuredClone(golden.cases[1]!.model),raw=stateV4().getInitialStateV4() as unknown as CfdProject
 raw.uid='controlled-project';raw.constructions={boxes:{[record.uid]:record}} as CfdProject['constructions']
 const project=stateV4().normalizeStateV4(raw) as unknown as CfdProject
 const globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const host:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:false}
 const builder=createSceneBuilder(host);builder.rebuildModels([record.uid]);globalGroup.updateMatrixWorld(true)
 const files=builder.api.buildGroupedStlMap([record.uid],false)
 return {project,files,builder,uid:record.uid}
}
const manifest=()=>({geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}}) as CfdrunV2
it('binds the real emitter original region bytes and all facet ordinals to its current OO entity',()=>{
 const e=actualExport()
 console.info("CONTROLLED_PHYSICAL_STL_BYTES",JSON.stringify(Object.fromEntries(Object.entries(e.files).map(([k,v])=>[k,createHash("sha256").update(v).digest("hex")]))))
 try {
  const m=manifest();bindStlFileHashes(m,e.files,e.project)
  const rows=Object.values(m.geometryExport.sourceRegions??{})
  expect(rows.length).toBeGreaterThan(0)
  for(const row of rows){
   expect(row.sourceEntityUid).toBe(e.uid);expect(row.sourceRegionUid).toContain(`/`+e.uid+`/stl-region/`)
   expect(row.sourceFacetCount).toBeGreaterThan(0);expect(row).not.toHaveProperty('sourceFaceUid');expect(row).not.toHaveProperty('physicalFaceUids')
   const file=m.geometryExport.stlFiles[row.stlFileId] as {path:string}
   const matches=[...e.files[file.path]!.matchAll(/^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?/gm)]
   const block=matches.find(x=>x[1]!.trim()===row.solidName)!
   expect(row.sourceRegionSha256).toBe(createHash('sha256').update(block[0]).digest('hex'))
   expect(row.sourceFacetCount).toBe((block[2]!.match(/\bfacet[ \t]+normal\b/g)||[]).length)
  }
 }finally{e.builder.clear()}
})
it('rejects retained emitter metadata after an exact exported byte changes or the project changes',()=>{
 const e=actualExport()
 try {
  const key=Object.keys(e.files)[0]!;e.files[key]+='\n'
  expect(()=>bindStlFileHashes(manifest(),e.files,e.project)).toThrow(/exact uploaded bytes/)
  expect(()=>bindStlFileHashes(manifest(),e.files,{...e.project,uid:'other-project'})).toThrow(/current project/)
 }finally{e.builder.clear()}
})
it('does not invent region provenance for a plain/copied legacy STL map',()=>{
 const e=actualExport()
 try{const m=manifest();bindStlFileHashes(m,{...e.files},e.project);expect(m.geometryExport.sourceRegions).toBeUndefined()}
 finally{e.builder.clear()}
})

it('rejects unchanged cached STL bytes when the same OO entity UID now has different geometry',()=>{
 const e=actualExport()
 try{const changed=structuredClone(e.project);const model=changed.constructions!.boxes![e.uid]!;model.geometry!.size!.x+=1
  expect(()=>bindStlFileHashes(manifest(),e.files,changed)).toThrow(/stale OO entity ancestry/)
 }finally{e.builder.clear()}
})
