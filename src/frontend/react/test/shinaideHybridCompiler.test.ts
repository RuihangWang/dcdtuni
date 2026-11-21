/** Same project, two derived solver inputs: DAE adoption must not change air physics. */
import { createHash } from 'node:crypto'
import { liveProject } from '../src/transient/model'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import * as THREE from 'three'
import { expect, it } from 'vitest'
import '../src/legacy/geometryBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createCfdrunBuild, buildManifestV2, bindStlFileHashes } from '../src/legacy/cfdrunBuild'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { adoptMockDAEExperiment, compileSavedExperiment, readExperiment } from '../src/liquid/liquidExperiment'
import { serializeProjectState } from '../src/io/projectFiles'
import { validateManifest, type CfdrunV2 } from '../src/schema/cfdrun'
import { assertLiquidrunDAEV3 } from '../src/schema/liquidrunDAE'
import { buildStlFiles } from '../src/io/stlExport'
import { remapStlFiles } from '../src/io/cfdPackage'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { assertPhysicalManifoldBranches } from './helpers/physicalManifoldBranchOracle'
import type { DynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { auditLiteralClosedCarrier } from './helpers/literalCarrierOracle'

const sourcePath=resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json')
const sha=(value:string)=>createHash('sha256').update(value).digest('hex')
const timestamp='2026-09-26T00:00:00.000Z'
const legacyAirFiles=['merged_acu.stl','merged_baffle.stl','merged_box.stl','merged_floor_ceiling.stl',
  'merged_perforatedBlankingPanel_3U.stl','merged_perforatedBlankingPanel_4U.stl','merged_perforatedBlankingPanel_5U.stl',
  'merged_rack_frame.stl','merged_server_4U.stl','merged_server_8U.stl','merged_server_interface_walls.stl','room_wall.stl'].sort()
function assertTransportOutput(manifest:CfdrunV2,files:Record<string,string>) {
  const name='merged_liquid_transport.stl'
  expect(Object.keys(files).filter(n=>n!==name).sort()).toEqual(legacyAirFiles)
  expect(Object.keys(files).filter(n=>n===name)).toEqual([name])
  const output=manifest.geometryExport
  const fileEntries=Object.entries(output.stlFiles).map(([id,value])=>{
    expect(value).toEqual(expect.objectContaining({path:expect.any(String),contentHash:expect.any(String)}))
    return [id,value as {path:string;contentHash:string}] as const
  }),matches=fileEntries.filter(([,f])=>f.path===name)
  expect(fileEntries.map(([,f])=>f.path).filter(n=>n!==name).sort()).toEqual(legacyAirFiles)
  expect(matches).toHaveLength(1);const [fileId,file]=matches[0]!
  expect(file.contentHash).toBe(sha(files[name]!))
  const audit=auditLiteralClosedCarrier(files[name]!)
  const regions=Object.values(output.sourceRegions??{}).filter(r=>r.stlFileId===fileId)
  expect(regions.map(r=>r.sourceEntityUid).sort()).toEqual(['m_31','m_32'])
  const catalogue=(window as unknown as {geometryScene:{getGroupedStlSourceCatalogue(f:Record<string,string>):
    {liquidTransport:{carrierUid:string;contributors:string[]};regions:Array<{filename:string;sourceEntityUid:string;facetCount:number}>}|null}})
    .geometryScene.getGroupedStlSourceCatalogue(files)
  expect(catalogue?.liquidTransport.contributors).toEqual(['m_31','m_32'])
  expect(catalogue?.regions.filter(r=>r.filename===name).map(r=>r.sourceEntityUid).sort()).toEqual(['m_31','m_32'])
  for(const region of regions) {
    expect(region.operation).toBe('liquidTransportUnionBoundary')
    expect(region.carrierUid).toBe(`${manifest.sourceProject!.uid}/liquid-transport/carrier/v1`)
    expect(region.carrierContributorUids).toEqual(['m_31','m_32'])
    expect(region.sourceFacetCount).toBe(audit.regionFacetCounts[region.solidName])
    expect(region.sourceFacetCount).toBeGreaterThan(0)
    const block=[...files[name]!.matchAll(/^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?/gm)].find(m=>m[1]===region.solidName)![0]
    expect(region.sourceRegionSha256).toBe(sha(block))
    const solids=Object.values(output.solids).filter(s=>s.stlFileId===fileId&&s.solidName===region.solidName)
    expect(solids).toHaveLength(1);expect(solids[0]!.role).toBe('wall')
    expect(solids[0]!.objectPath).toBe(`constructions.pipeSegments.${region.sourceEntityUid}`)
    expect(solids[0]!.areaM2).toBeGreaterThan(0)
  }
}


async function air(project:CfdProject,includeLeakage=false) {
  // Geometry builders may materialize caches/defaults. Isolate that work so
  // both exports receive the same canonical project normalization, and the
  // comparison measures DAE adoption rather than load-time rack migration.
  // Made live as the editor makes a loaded file live: v5 fields materialised at t = 0.
  project=liveProject(stateV4().normalizeStateV4(JSON.parse(JSON.stringify(project))) as unknown as CfdProject,0)
  const globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group(),root=new THREE.Group()
  root.add(wrapperGroup);wrapperGroup.add(globalGroup)
  const host:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),selectedUid:()=>null,
    project:()=>project,webglAvailable:false,previewMode:()=>false}
  const globals=window as unknown as {allModels?:()=>Construction[]}
  const oldModels=globals.allModels
  globals.allModels=()=>stateV4().allModels(project) as Construction[]
  const builder=createSceneBuilder(host)
  try {
    builder.rebuild()
    wrapperGroup.updateMatrixWorld(true)
    const ids=(stateV4().allModels(project) as Construction[]).map(model=>model.uid)
    const stl=builder.api.buildGroupedStlMap(ids,false)
    const api=createCfdrunBuild({state:project,globalGroup,
      LENGTH_UNITS:{mm:{scale:.001,label:'mm',step:.1,precision:4},cm:{scale:.01,label:'cm',step:.01,precision:4},m:{scale:1,label:'m',step:.0001,precision:4}},
      _MESH_MODES:['bodyFitted','cartesianLocalized','structured'],
      buildGroupedStlMap:(modelIds,leakage)=>builder.api.buildGroupedStlMap(modelIds,leakage)})
    const manifest=await buildManifestV2(api,project,timestamp)
    const geometryHashes=Object.fromEntries(Object.entries(stl).map(([name,text])=>[name,sha(text)]).sort(([a],[b])=>a!.localeCompare(b!)))
    const stlFiles=includeLeakage?buildStlFiles(builder,project,true).files:stl
    bindStlFileHashes(manifest,stlFiles,project)
    return {manifest,geometryHashes,stlFiles,normalizedProject:project}
  } finally {
    builder.clear()
    if(oldModels)globals.allModels=oldModels
    else delete globals.allModels
  }
}

function airPhysics(manifest:CfdrunV2) {
  // The project-content hash MUST change when liquid analysis is saved. Every
  // physical/geometry/UID payload field, including air inputs, must not change.
  const copy=structuredClone(manifest)
  if(copy.sourceProject)copy.sourceProject.contentHash=''
  return copy
}

function airDefaults(project:CfdProject) {
  const defaults=(project as unknown as {runDefaults?:{meshConfig?:unknown;simulationConfig?:unknown}}).runDefaults
  return {meshConfig:defaults?.meshConfig,simulationConfig:defaults?.simulationConfig}
}

function differingPaths(left:unknown,right:unknown,path='$'):string[] {
  if(Object.is(left,right))return []
  if(!left||!right||typeof left!=='object'||typeof right!=='object')return [path]
  const a=left as Record<string,unknown>,b=right as Record<string,unknown>
  return [...new Set([...Object.keys(a),...Object.keys(b)])].flatMap(key=>differingPaths(a[key],b[key],`${path}.${key}`))
}

it('keeps the real Shinaide OpenFOAM geometry and physical definitions intact through saved DAE adoption',async()=>{
  const sourceBytes=readFileSync(sourcePath,'utf8'),sourceSha=sha(sourceBytes)
  const normalized=stateV4().normalizeStateV4(JSON.parse(sourceBytes)) as unknown as CfdProject
  store.loadProject(normalized,null) // same load-time rack migration as the live editor
  const baseline=store.getProject()!
  const originalConstructions=structuredClone(baseline.constructions)
  const originalAirDefaults=structuredClone(airDefaults(baseline))
  const before=await air(baseline)
  expect(validateManifest(before.manifest)).toEqual([])
  expect(Object.keys(before.manifest.geometryExport.solids)).not.toHaveLength(0)
  expect(Object.values(before.manifest.boundaryBindings).filter(binding=>binding.bcType==='acu_supply')).toHaveLength(4)
  expect(Object.values(before.manifest.boundaryBindings).filter(binding=>binding.bcType==='acu_return')).toHaveLength(4)
  expect(Object.keys(before.manifest.meshGeometry?.racks??{})).toHaveLength(14)

  store.setRunConfig('liquid','experiment',adoptMockDAEExperiment(store.getProject()!,{source_reference:'Explicit cross-compiler verification mock; not NV calibration'}))
  const saved=serializeProjectState(store.getProject()!)
  const reopened=stateV4().normalizeStateV4(JSON.parse(saved)) as unknown as CfdProject
  // On disk (cfdproj v5) the time-variable fields live only in tracks; the
  // editor sees the reopened file materialised at t = 0, and that must match.
  expect(differingPaths(originalConstructions,liveProject(structuredClone(reopened),0).constructions)).toEqual([])
  expect(airDefaults(reopened)).toEqual(originalAirDefaults)
  expect(readExperiment(reopened).dae?.primary_circuits).toHaveLength(1)
  const liquid=compileSavedExperiment(reopened).liquidrun
  expect(liquid.schemaVersion).toBe(3)
  // 14 racks now connect at their authored bottom supply/return ports. Each
  // gains one physical header edge/node per side, preserving all old cold plates.
  expect(assertPhysicalManifoldBranches(reopened,liquid as unknown as DynamicLiquidGraph))
    .toEqual({branches:28,bottomHeaders:28})
  expect(liquid.nodes).toHaveLength(215)
  expect(liquid.edges).toHaveLength(270)
  expect(liquid.edges.filter(edge=>edge.type==='cold_plate')).toHaveLength(56)
  expect(liquid).not.toHaveProperty('manufactured_benchmark')
  const after=await air(reopened)
  expect(validateManifest(after.manifest)).toEqual([])
  expect(after.geometryHashes).toEqual(before.geometryHashes)
  expect(airPhysics(after.manifest)).toEqual(airPhysics(before.manifest))
  expect(after.manifest.sourceProject?.contentHash).not.toBe(before.manifest.sourceProject?.contentHash)
  expect(after.manifest.sourceProject?.uid).toBe(liquid.source_project.project_uid)
  expect(liquid.source_project.sha256).toBe(after.manifest.sourceProject?.contentHash)
  expect(sha(readFileSync(sourcePath,'utf8'))).toBe(sourceSha)

  const report={source_path:sourcePath,source_sha256:sourceSha,project_uid:reopened.uid,
    scope:'CPU pure export/geometry contract; no WebGL renderer, mesh, CFD or DAE solve',
    air_schema_version:after.manifest.schemaVersion,liquid_schema_version:liquid.schemaVersion,
    source_hash_before:before.manifest.sourceProject?.contentHash,source_hash_after:after.manifest.sourceProject?.contentHash,
    stl_file_count:Object.keys(after.geometryHashes).length,stl_sha256:after.geometryHashes,
    solid_count:Object.keys(after.manifest.geometryExport.solids).length,
    boundary_count:Object.keys(after.manifest.boundaryBindings).length,
    source_term_count:Object.keys(after.manifest.sourceTerms).length,
    porous_binding_count:Object.keys(after.manifest.porousBindings).length,
    liquid_nodes:liquid.nodes.length,liquid_edges:liquid.edges.length,
    air_physics_identical:true,geometry_identical:true,original_file_unchanged:true}
  if(process.env.HYBRID_COMPILER_OUTPUT_DIR) {
    const dir=process.env.HYBRID_COMPILER_OUTPUT_DIR
    mkdirSync(dir,{recursive:true})
    writeFileSync(join(dir,'before.cfdrun'),JSON.stringify(before.manifest,null,2))
    writeFileSync(join(dir,'after.cfdrun'),JSON.stringify(after.manifest,null,2))
    writeFileSync(join(dir,'after.liquidrun'),JSON.stringify(liquid,null,2))
    writeFileSync(join(dir,'frontend-report.json'),JSON.stringify(report,null,2))
  }
  console.log('SHINAIDE_CROSS_COMPILER '+JSON.stringify(report))
},30_000)

it('exports the saved Shinaide DAE project as one strict OpenFOAM input without changing the project',async()=>{
  const currentPath=resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json')
  const source=readFileSync(currentPath,'utf8')
  const project=stateV4().normalizeStateV4(JSON.parse(source)) as unknown as CfdProject
  const {manifest,stlFiles}=await air(project,true)
  const meshGeometry=manifest.meshGeometry as Record<string,unknown>|undefined
  if(meshGeometry)remapStlFiles(meshGeometry)
  expect(validateManifest(manifest)).toEqual([])
  expect(manifest.schemaVersion).toBe(4)
  expect(manifest.sourceProject?.uid).toBe(project.uid)
  assertTransportOutput(manifest,stlFiles)
  expect(Object.keys(manifest.geometryExport.stlFiles)).toHaveLength(13)
  expect(Object.values(stlFiles).every(text=>text.includes('solid '))).toBe(true)
  expect(sha(readFileSync(currentPath,'utf8'))).toBe(sha(source))

  const output=process.env.SHINAIDE_AIR_SMOKE_OUTPUT_DIR
  if(output) {
    const model=join(output,'model'),surfaces=join(output,'Mesh','constant','triSurface')
    mkdirSync(model,{recursive:true});mkdirSync(surfaces,{recursive:true})
    writeFileSync(join(model,'Shinaide.cfdrun'),JSON.stringify(manifest,null,2)+'\n')
    for(const [name,body] of Object.entries(stlFiles))writeFileSync(join(surfaces,name),body)
    writeFileSync(join(output,'export-report.json'),JSON.stringify({
      project_path:currentPath,project_sha256:sha(source),project_uid:project.uid,
      cfdrun_schema:manifest.schemaVersion,
      cfdrun_sha256:sha(JSON.stringify(manifest,null,2)+'\n'),
      stl_sha256:Object.fromEntries(Object.entries(stlFiles).map(([name,body])=>[name,sha(body)])),
      source_project:manifest.sourceProject,
      scope:'Actual saved DAE cfdproj compiled by the existing React air exporter; no mesh or solve executed',
    },null,2)+'\n')
  }
},30_000)

it('retains stable source-face ownership for merged baffles, floor and server thermal faces',async()=>{
  const saved=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json'),'utf8')) as CfdProject
  const {manifest}=await air(saved,true)
  const sources=(manifest.geometryExport as unknown as {sourceSurfaces?:Record<string,{
    solidName:string;sourceObjectUid:string;sourceFaceUid:string;representation:string
  }>}).sourceSurfaces
  expect(sources).toBeDefined()
  const rows=Object.values(sources??{})
  for(const [solid,uid] of [['con_f','m_22'],['con_b','m_23'],['con_b_copy','m_24'],['Raised_Floor','m_52']]) {
    expect(rows.some(row=>row.solidName===solid&&row.sourceObjectUid===uid&&
      row.sourceFaceUid.startsWith(`${saved.uid}/${uid}/`)&&row.representation==='zeroThicknessSheet')).toBe(true)
  }
  expect(rows.some(row=>row.sourceObjectUid==='m_1/e_1'&&row.solidName.endsWith('server_1_front')&&
    row.sourceFaceUid.includes('inlet'))).toBe(true)
},30_000)

it('binds the real B06 and B07 blanking sheets to distinct rack source faces and hashed STL regions',async()=>{
  const source=readFileSync(sourcePath,'utf8')
  const project=stateV4().normalizeStateV4(JSON.parse(source)) as unknown as CfdProject
  const {manifest,stlFiles}=await air(project,true)
  expect(validateManifest(manifest)).toEqual([])
  const sources=manifest.geometryExport.sourceSurfaces??{}
  const stlFilesById=manifest.geometryExport.stlFiles as Record<string,{path:string;contentHash:string}>
  for(const [span,slotRange] of [[3,'u1_u3'],[5,'u8_u12']] as const) {
    const file=`merged_perforatedBlankingPanel_${span}U.stl`
    const stlFile=Object.entries(stlFilesById).find(([,entry])=>entry.path===file)
    expect(stlFile).toBeDefined()
    const [stlFileId,entry]=stlFile!
    expect(entry.contentHash).toBe(sha(stlFiles[file]!))
    const faceUids:string[]=[]
    for(const [rackName,rackUid] of [['Rack_B_06','m_16'],['Rack_B_07','m_19']] as const) {
      const solidName=`${rackName}_inlet_${slotRange}`
      expect(stlFiles[file]).toContain(`solid ${solidName}\n`)
      const row=sources[`${stlFileId}:${solidName}`]
      expect(row).toMatchObject({stlFileId,solidName,sourceObjectUid:rackUid,
        representation:'zeroThicknessSheet'})
      expect(row!.sourceFaceUid).toMatch(new RegExp(`^${project.uid}/${rackUid}/`))
      faceUids.push(row!.sourceFaceUid)
    }
    expect(new Set(faceUids).size).toBe(2)
  }
  expect(sha(readFileSync(sourcePath,'utf8'))).toBe(sha(source))
},30_000)

it('rejects duplicate rack names before assigning a blanking sheet to either rack UID',()=>{
  const project=stateV4().normalizeStateV4(JSON.parse(readFileSync(sourcePath,'utf8'))) as unknown as CfdProject
  const racks=project.constructions?.racks as Record<string,Construction>|undefined
  if(!racks)throw new Error('Shinaide project has no racks')
  racks.m_19!.meta.name=racks.m_16!.meta.name
  const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2
  expect(()=>bindStlFileHashes(manifest,{
    'merged_perforatedBlankingPanel_3U.stl':'solid Rack_B_06_inlet_u1_u3\nendsolid Rack_B_06_inlet_u1_u3\n',
  },project)).toThrow(/rack blanking source name collision.*Rack_B_06_inlet_u1_u3/)
})

it('refuses a hybrid run when a rack slot sheet declared by the project is absent from the STL',()=>{
  const source=stateV4().normalizeStateV4(JSON.parse(readFileSync(sourcePath,'utf8'))) as unknown as CfdProject
  const rack=(source.constructions?.racks as Record<string,Construction>|undefined)?.m_16
  if(!rack)throw new Error('Shinaide project has no B06 rack')
  const project={uid:source.uid,constructions:{racks:{m_16:rack}},
    runDefaults:{hybridHeatBudget:{version:1}}} as unknown as CfdProject
  const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2
  expect(()=>bindStlFileHashes(manifest,{
    'merged_perforatedBlankingPanel_3U.stl':'solid unrelated\nendsolid unrelated\n',
  },project)).toThrow(/hybrid rack blanking source.*missing its STL solid/)
})

it('writes edited air execution budgets once: mesh budgets in both mesh blocks, solve budgets only in the root (v4)',async()=>{
  const saved=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json'),'utf8')) as
    CfdProject & {runDefaults:{meshConfig:{meshProcess:number;parallelMesh:boolean;maxGlobalCells:number};
      simulationConfig:{solveProcess:number;parallelSolve:boolean;endTime:number;writeInterval:number;startFrom:string}}}
  saved.runDefaults.meshConfig.meshProcess=4
  saved.runDefaults.simulationConfig.solveProcess=4
  saved.runDefaults.simulationConfig.endTime=10
  saved.runDefaults.simulationConfig.writeInterval=10
  saved.runDefaults.simulationConfig.startFrom='startTime'
  const {manifest}=await air(saved)
  expect(validateManifest(manifest)).toEqual([])
  const blob=manifest.meshGeometry as {meshConfig:Record<string,unknown>;simulationConfig:Record<string,unknown>}
  for(const key of ['parallelMesh','meshProcess','maxGlobalCells'] as const)
    expect(blob.meshConfig[key]).toBe(manifest.meshConfig[key])
  // cfdrun v4: ONE simulationConfig, the root; the runner derives what it executes from it.
  expect(blob.simulationConfig).toBeUndefined()
  expect(manifest.simulationConfig.parallelSolve).toBe(saved.runDefaults.simulationConfig.parallelSolve)
  expect(manifest.simulationConfig.startFrom).toBe('startTime')
  expect(manifest.simulationConfig.writeInterval).toBe(10)
  expect(manifest.meshConfig.meshProcess).toBe(4)
  expect(manifest.simulationConfig.solveProcess).toBe(4)
  expect(manifest.simulationConfig.endTime).toBe(10)
},30_000)

it('exports one isolated synthetic Shinaide hybrid case with 56 explicit server budgets',async()=>{
  const savedDaePath=resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json')
  const source=readFileSync(savedDaePath,'utf8'),sourceSha=sha(source)
  const project=stateV4().normalizeStateV4(JSON.parse(source)) as unknown as CfdProject
  const racks=(project.constructions?.racks??{}) as Record<string,Construction>
  const serverIds=Object.entries(racks).flatMap(([rackUid,rack])=>{
    const servers=((rack.constructions as {servers?:Record<string,{uid:string;type?:string}>}|undefined)?.servers??{})
    return Object.entries(servers).filter(([,server])=>server.type==='liquidServer')
      .map(([serverUid,server])=>{
        expect(server.uid).toBe(serverUid)
        return `${rackUid}/${serverUid}`
      })
  }).sort()
  expect(serverIds).toHaveLength(56)
  const output=process.env.HYBRID_HEAT_OUTPUT_DIR?.trim()
  if(output)project.meta={...project.meta,casePath:resolve(output)}
  if(!project.runDefaults)throw new Error('The saved DAE project has no runDefaults')
  project.runDefaults.hybridHeatBudget={version:1,source:'mock',
    source_reference:'synthetic integration test, not NV data',load_mapping:'shared_heat_multiplier',
    air_surface_allocation:{front_fraction:0,rear_fraction:1},
    servers:Object.fromEntries(serverIds.map(id=>[id,{total_it_power_W:750}]))}
  const savedExperiment=(project.runDefaults.liquid as {experiment?:{
    dae?:{execution?:{max_threads:number}}
  }}|undefined)?.experiment
  if(!savedExperiment?.dae?.execution)throw new Error('The saved DAE project has no execution budget')
  savedExperiment.dae.execution.max_threads=4
  const budget=project.runDefaults?.hybridHeatBudget
  expect(budget).toBeDefined()
  expect(budget?.source).toBe('mock')
  expect(budget?.source_reference).toBe('synthetic integration test, not NV data')
  expect(budget?.air_surface_allocation).toEqual({front_fraction:0,rear_fraction:1})
  expect(Object.keys(budget?.servers??{}).sort()).toEqual(serverIds)
  expect(Object.values(budget?.servers??{}).every(server=>server.total_it_power_W===750)).toBe(true)

  const projectText=serializeProjectState(project)
  const reopened=stateV4().normalizeStateV4(JSON.parse(projectText)) as unknown as CfdProject
  const liquid=compileSavedExperiment(reopened).liquidrun
  assertLiquidrunDAEV3(liquid)
  expect(liquid.schemaVersion).toBe(3)
  expect(liquid.execution.max_threads).toBe(4)
  const {manifest,stlFiles,normalizedProject}=await air(reopened,true)
  expect(validateManifest(manifest)).toEqual([])
  expect(manifest.schemaVersion).toBe(4)
  assertTransportOutput(manifest,stlFiles)
  expect(manifest.sourceProject?.uid).toBe(liquid.source_project.project_uid)
  const canonical=(window as unknown as {cfdrunExport:{stableStringify(value:unknown):string}})
    .cfdrunExport.stableStringify
  const projectHash=sha(canonical(JSON.parse(projectText)))
  expect(sha(canonical(reopened))).toBe(projectHash)
  expect(sha(canonical(normalizedProject))).toBe(projectHash)
  expect(manifest.sourceProject?.contentHash).toBe(projectHash)
  expect(sha(readFileSync(savedDaePath,'utf8'))).toBe(sourceSha)

  if(output) {
    const dir=resolve(output),caseRoot=resolve(process.cwd(),'../../../case')
    if(dir===caseRoot||dir.startsWith(caseRoot+sep))throw new Error('HYBRID_HEAT_OUTPUT_DIR must be outside tracked case/ directories')
    if(existsSync(dir)&&readdirSync(dir).length)throw new Error('HYBRID_HEAT_OUTPUT_DIR must be empty to prevent overwrite')
    const model=join(dir,'model'),surfaces=join(dir,'Mesh','constant','triSurface')
    mkdirSync(model,{recursive:true});mkdirSync(surfaces,{recursive:true})
    const projectFile=join(dir,'Shinaide_hybrid_mock.cfdproj')
    const cfdrunFile=join(model,'Shinaide_hybrid_mock.cfdrun')
    const liquidrunFile=join(model,'Shinaide_hybrid_mock.liquidrun')
    const projectBytes=projectText+'\n'
    const cfdrunBytes=JSON.stringify(manifest,null,2)+'\n'
    const liquidrunBytes=JSON.stringify(liquid,null,2)+'\n'
    writeFileSync(projectFile,projectBytes,{flag:'wx'})
    writeFileSync(cfdrunFile,cfdrunBytes,{flag:'wx'})
    writeFileSync(liquidrunFile,liquidrunBytes,{flag:'wx'})
    for(const [name,body] of Object.entries(stlFiles))writeFileSync(join(surfaces,name),body,{flag:'wx'})
    writeFileSync(join(dir,'export-report.json'),JSON.stringify({
      source_path:savedDaePath,source_sha256:sourceSha,derived_project_path:projectFile,
      derived_project_sha256:sha(projectBytes),project_content_hash:projectHash,
      cfdrun_path:cfdrunFile,cfdrun_source_content_hash:manifest.sourceProject?.contentHash,
      cfdrun_sha256:sha(cfdrunBytes),liquidrun_path:liquidrunFile,
      liquidrun_sha256:sha(liquidrunBytes),project_uid:reopened.uid,
      liquid_server_count:serverIds.length,total_it_power_W:serverIds.length*750,
      dae_execution_max_threads:liquid.execution.max_threads,
      air_surface_allocation:budget?.air_surface_allocation,
      stl_sha256:Object.fromEntries(Object.entries(stlFiles).map(([name,body])=>[name,sha(body)])),
      scope:'Synthetic integration fixture from saved Shinaide DAE; CPU export only; not NV data, CFD solve, or calibrated heat coupling',
    },null,2)+'\n',{flag:'wx'})
    expect(sha(readFileSync(projectFile,'utf8'))).toBe(sha(projectBytes))
    const onDiskProject=JSON.parse(readFileSync(projectFile,'utf8'))
    expect(sha(canonical(onDiskProject))).toBe(projectHash)
    expect(onDiskProject.runDefaults.hybridHeatBudget.servers).toEqual(budget?.servers)
    expect(JSON.parse(readFileSync(cfdrunFile,'utf8')).sourceProject.contentHash).toBe(projectHash)
    expect(JSON.parse(readFileSync(liquidrunFile,'utf8')).source_project.project_uid).toBe(reopened.uid)
    expect(readdirSync(surfaces)).toHaveLength(12)
  }
  expect(sha(readFileSync(savedDaePath,'utf8'))).toBe(sourceSha)
},30_000)
