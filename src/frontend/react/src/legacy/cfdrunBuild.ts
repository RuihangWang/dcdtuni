/**
 * The .cfdrun manifest builders, shared with the legacy editor.
 *
 * Three modules make a manifest, and only the last was page-bound:
 *
 *   js/cfdrun_schema.js  the V2 document schema
 *   js/cfdrun_export.js  `buildManifest`, already shared
 *   js/cfdrun_build.js   `buildCfdrunJson` (the transitional v1 meshGeometry
 *                        blob the runner still reads) and
 *                        `buildComprehensiveStlMap` (the solid-name map the V2
 *                        boundaryBindings are built from)
 *
 * `cfdrun_build.js` takes its four pieces of page state as an injected context,
 * so React can build a manifest from its own project and scene.
 *
 * This is the piece where being wrong is worse than crashing. The manifest is
 * what the runner validates and meshes from, so a mis-derived blockMesh bound
 * or a solid name that does not match the exported STL produces a case that
 * meshes cleanly around the wrong geometry — and `mesh_acceptance` passes it,
 * because every region it declared does exist.
 */

import '../../../shared/js/cfdrun_schema.js'
import { projectAtStart } from '../transient/model'
import { addAirSchedules, airTimeline, airTransientRun, type AirManifest } from '../transient/airSchedules'
import '../../../shared/js/cfdrun_export.js'
import '../../../shared/js/cfdrun_build.js'

import type { CfdProject, Construction } from '../schema/cfdproj'
import type { CfdrunV2 } from '../schema/cfdrun'
import { geometryPure } from './geometryBuilder'
import { resolveMonitoring } from '../run/monitoring'
import { stlNaming } from './stlNaming'

/** The four page globals the builders read. */
export interface CfdrunContext {
  readonly state: CfdProject | null
  /** The scene root, for world matrices. */
  readonly globalGroup: unknown
  readonly LENGTH_UNITS: unknown
  readonly _MESH_MODES: unknown
  /**
   * The scene's grouped-STL exporter.
   *
   * An INSTANCE method of the geometry scene, so it has to be injected: the
   * porous-solid classification parses its output, and without it the manifest
   * comes out missing every porous solid — quietly, inside a catch.
   */
  readonly buildGroupedStlMap?: (modelIds: string[], includeLeakage: boolean) => Record<string, string>
}

export interface CfdrunBuildApi {
  /** The transitional v1 meshGeometry blob. */
  buildCfdrunJson(): Record<string, unknown>
  /** filename -> STL text, with the porous solids the V2 bindings need. */
  buildComprehensiveStlMap(project: CfdProject): Record<string, string>
  [key: string]: unknown
}

interface CfdrunExportApi {
  translateAcus(project: unknown, ctx: unknown): { boundaryBindings: Record<string, unknown> }
  translateRackServers(project: unknown, ctx: unknown): { boundaryBindings: Record<string, unknown> }
  assembleInputs(project: unknown): Record<string, unknown>
  buildManifest(
    project: unknown,
    stlMap: Record<string, string>,
    timestamp: string
  ): Promise<CfdrunV2>
  projectContentHashSync(project: unknown): string
  canonicalProjectForHash(project: unknown): Record<string, unknown>
  sha256TextSync(value: string): string
}

function required<T>(value: T | undefined, name: string): T {
  if (!value) {
    throw new Error(`${name} is missing — the shared .cfdrun modules did not load.`)
  }
  return value
}

export function createCfdrunBuild(ctx: CfdrunContext): CfdrunBuildApi {
  const mod = required(
    (window as unknown as { cfdrunBuild?: { create(c: CfdrunContext): CfdrunBuildApi } })
      .cfdrunBuild,
    'window.cfdrunBuild'
  )
  return mod.create(ctx)
}

export function cfdrunExport(): CfdrunExportApi {
  return required(
    (window as unknown as { cfdrunExport?: CfdrunExportApi }).cfdrunExport,
    'window.cfdrunExport'
  )
}

/** A project declaration alone cannot attest physical pairs or source support. */
export function assertGeometryTopologyAirExportReady(project: CfdProject): void {
  if (project.geometryTopology !== undefined) {
    throw new Error('geometryTopology strict V1 is not yet compiled and validated against source geometry; .cfdrun export and air runs are blocked')
  }
}

/** Freeze exact grouped STL bytes into the air manifest before upload/export. */
export function bindStlFileHashes(manifest: CfdrunV2, files: Record<string, string>, project?: CfdProject): void {
  if(project)assertGeometryTopologyAirExportReady(project)
  const names=Object.keys(files).sort()
  if(!names.length)throw new Error('air export has no grouped STL files to bind')
  const indexed:Record<string,{path:string;objectPath:null;kind:'grouped_ascii';contentHash:string}>={}
  for(const [index,name] of names.entries()) {
    if(!/^[A-Za-z0-9_.-]+\.stl$/.test(name)||typeof files[name]!=='string')
      throw new Error(`invalid grouped STL filename or content: ${name}`)
    indexed[`stl_${String(index).padStart(3,'0')}`]={path:name,objectPath:null,
      kind:'grouped_ascii',contentHash:cfdrunExport().sha256TextSync(files[name])}
  }
  manifest.geometryExport.stlFiles=indexed
  if(!project)return
  type SourceRow={stlFileId:string;solidName:string;sourceObjectUid:string;
    sourceFaceUid:string;representation:'zeroThicknessSheet'|'surfaceFace'}
  const sources:Record<string,SourceRow>={}
  const matchedFloorUids=new Set<string>()
  const floorSourceFaceKeys=new Map<string,string>()
  const emitter=(window as unknown as {geometryScene?:{getGroupedStlSourceCatalogue?(files:Record<string,string>):{
    schema:string;projectUid:string;files:Record<string,string>;regions:ReadonlyArray<{filename:string;solidName:string;
      sourceEntityUid:string;facetCount:number;block:string;operation:string;sourceEntitySnapshot:string;
      carrierUid?:string;carrierContributorUids?:readonly string[];
      sourceSurface?:{sourceFaceId:string;sourceSheetKind:string;sourcePatchId?:string;
        sourcePatchSnapshot?:string;representation:string}}>;liquidTransport?:{filename:string;carrierUid:string;contributors:readonly string[];dependencies:readonly{uid:string;snapshot:string}[]}
  }|null}}).geometryScene?.getGroupedStlSourceCatalogue?.(files)
  if(emitter){
    if(emitter.schema!=='dcdtuni.stl-source-emission/v1'||emitter.projectUid!==project.uid||
       Object.keys(emitter.files).sort().join('\0')!==names.join('\0')||names.some(name=>emitter.files[name]!==files[name]))
      throw new Error('STL source emission does not match the current project and exact uploaded bytes')
    const entities=new Map(Object.values(project.constructions??{}).flatMap(bucket=>
      bucket&&typeof bucket==='object'?Object.values(bucket).flatMap(model=>model&&typeof model==='object'&&'uid' in model?[[String(model.uid),JSON.stringify(model)] as const]:[]):[]))
    const carrier=emitter.liquidTransport
    if(carrier){
      if(carrier.filename!=='merged_liquid_transport.stl'||carrier.carrierUid!==project.uid+'/liquid-transport/carrier/v1'||
        !names.includes(carrier.filename)||!carrier.contributors.length||new Set(carrier.contributors).size!==carrier.contributors.length||
        carrier.dependencies.some(row=>entities.get(row.uid)!==row.snapshot))
        throw new Error('Liquid transport carrier has absent or stale OO contributors/dependencies')
    }
    const regions:NonNullable<CfdrunV2['geometryExport']['sourceRegions']>={}
    for(const row of emitter.regions){
      const index=names.indexOf(row.filename),fileId=`stl_${String(index).padStart(3,'0')}`,key=`${fileId}:${row.solidName}`
      if(index<0||entities.get(row.sourceEntityUid)!==row.sourceEntitySnapshot||!Number.isSafeInteger(row.facetCount)||row.facetCount<1||regions[key])
        throw new Error('STL source region has absent, ambiguous or stale OO entity ancestry')
      const floor=project.constructions?.floors?.[row.sourceEntityUid] as unknown as {
        uid:string;cooling?:{defaultBoundaryType?:string};geometry?:{faces?:Record<string,{id?:string;
          patches?:Array<{id?:string}>}>;cutouts?:Array<{faceId?:string}>}
      }|undefined
      if(floor?.cooling?.defaultBoundaryType==='wallBaffle'||row.sourceSurface){
        const faceId=row.sourceSurface?.sourceFaceId
        if(!floor||floor.uid!==row.sourceEntityUid||floor.cooling?.defaultBoundaryType!=='wallBaffle'||
          row.operation!=='modelBoundary'||!['merged_floor_ceiling.stl','merged_perforated.stl'].includes(row.filename)||
          faceId!=='top'||floor.geometry?.faces?.[faceId]?.id!==faceId||
          row.sourceSurface?.representation!=='zeroThicknessSheet')
          throw new Error('Floor source sheet has absent or invalid canonical face ancestry')
        const face=floor.geometry!.faces![faceId]!,sheet=row.sourceSurface!,patches=face.patches??[]
        if(patches.some(patch=>!patch.id)||new Set(patches.map(patch=>patch.id)).size!==patches.length)
          throw new Error('Floor source sheet has duplicate or absent saved patch IDs')
        let sourceFaceUid=`${project.uid}/${floor.uid}/${faceId}`
        if(sheet.sourceSheetKind==='patch'){
          const patch=patches.filter(patch=>patch.id===sheet.sourcePatchId)
          if(patch.length!==1||JSON.stringify(patch[0])!==sheet.sourcePatchSnapshot)
            throw new Error('Floor source sheet has absent or stale saved patch ancestry')
          sourceFaceUid+=`/patch/${sheet.sourcePatchId}`
        }else if(sheet.sourceSheetKind==='remainder'){
          if(!patches.length&&!floor.geometry?.cutouts?.some(cutout=>cutout.faceId===faceId))
            throw new Error('Floor remainder sheet has no authored partition')
          sourceFaceUid+='/remainder-sheet'
        }else if(sheet.sourceSheetKind!=='wholeFace'||patches.length||
          floor.geometry?.cutouts?.some(cutout=>cutout.faceId===faceId)){
          throw new Error('Floor whole sheet has invalid canonical face partition')
        }
        const previous=floorSourceFaceKeys.get(sourceFaceUid)
        if(previous&&previous!==key)throw new Error('Floor physical source leaf is split across multiple STL fragments')
        floorSourceFaceKeys.set(sourceFaceUid,key)
        sources[key]={stlFileId:fileId,solidName:row.solidName,sourceObjectUid:floor.uid,
          sourceFaceUid,representation:'zeroThicknessSheet'}
        matchedFloorUids.add(floor.uid)
      }
      regions[key]={stlFileId:fileId,solidName:row.solidName,sourceEntityUid:row.sourceEntityUid,
        sourceRegionUid:`${project.uid}/${row.sourceEntityUid}/stl-region/${row.solidName}`,
        sourceFacetCount:row.facetCount,sourceRegionSha256:cfdrunExport().sha256TextSync(row.block),operation:row.operation,
        ...(row.operation==='liquidTransportUnionBoundary'?{carrierUid:row.carrierUid,carrierContributorUids:[...(row.carrierContributorUids??[])]}:{})}
      if(row.operation==='liquidTransportUnionBoundary'&&(!carrier||row.filename!==carrier.filename||row.carrierUid!==carrier.carrierUid||
        !carrier.contributors.includes(row.sourceEntityUid)||row.carrierContributorUids?.join('\0')!==carrier.contributors.join('\0')))
        throw new Error('Liquid transport source region has invalid shared carrier ancestry')
    }
    manifest.geometryExport.sourceRegions=regions
  }
  const modelNames=new Map<string,{uid:string;bucket:'baffle'|'floor'}>()
  const matchedModelNames=new Set<string>()
  const rackSheets=new Map<string,{uid:string;sourceFaceUid:string}>()
  const matchedRackSheets=new Set<string>()
  type SourceModel={uid:string;meta?:{name?:string};cooling?:{defaultBoundaryType?:string}}
  const construction=(project as unknown as {constructions?:{
    baffles?:Record<string,SourceModel>,
    floors?:Record<string,SourceModel>,
    racks?:Record<string,Construction>
  }}).constructions
  for(const [bucket,models] of [
    ['baffle',construction?.baffles??{}],['floor',construction?.floors??{}],
  ] as const)for(const [uid,model] of Object.entries(models)) {
    if(model.uid!==uid)throw new Error(`source model UID mismatch: key=${uid} object=${model.uid}`)
    if(bucket==='floor'&&model.cooling?.defaultBoundaryType!=='wallBaffle')continue
    const name=stlNaming().sanitizeName(model.meta?.name)
    if(modelNames.has(name))throw new Error(`source surface name collision: ${name}`)
    modelNames.set(name,{uid,bucket})
  }
  // Blanking panels are generated from the owning rack's saved slot state, not
  // standalone models. Ask the same geometry/naming modules that emitted the
  // STL for its exact solid and bucket; neither coordinates nor nearby sheets
  // can establish ownership when independent racks are close together.
  const rackGeometry=geometryPure()
  for(const [uid,rack] of Object.entries(construction?.racks??{})) {
    if(rack.uid!==uid)throw new Error(`source rack UID mismatch: key=${uid} object=${rack.uid}`)
    for(const side of ['front','back'] as const)for(const range of rackGeometry.getRackMergedEmptySlotRanges(rack,side)) {
      const solidName=rackGeometry.getRackMergedSlotExportName(rack,side,range.startUnit,range.endUnit)
      const filename=stlNaming().classifyRackSolidToBucketFile(solidName,rack,stlNaming().blankingBucketOptions(project))
      if(!filename||!/^merged_(?:perforated|wall)BlankingPanel_(?:[A-Za-z0-9_]+_)?\d+U\.stl$/.test(filename))
        throw new Error(`rack blanking source ${uid}/${side}/${range.startUnit}-${range.endUnit} has no STL bucket`)
      const key=`${filename}:${solidName}`
      if(rackSheets.has(key))throw new Error(`rack blanking source name collision: ${key}`)
      rackSheets.set(key,{uid,sourceFaceUid:
        `${project.uid}/${uid}/blanking/${side}/u${range.startUnit+1}-u${range.endUnit+1}/source-sheet`})
    }
  }
  const serverBindings=new Map<string,{uid:string;face:string}>()
  for(const binding of Object.values(manifest.geometryExport.solids)) {
    const match=/^constructions\.racks\.([^.]+)\.servers\.([^.]+)$/.exec(binding.objectPath??'')
    if(!match||!binding.solidName||!binding.surfaceUid)continue
    const uid=`${match[1]}/${match[2]}`
    if(serverBindings.has(binding.solidName))throw new Error(`duplicate server source solid ${binding.solidName}`)
    serverBindings.set(binding.solidName,{uid,face:binding.surfaceUid})
  }
  const seenSolids=new Set<string>()
  for(const [index,filename] of names.entries()) {
    const fileId=`stl_${String(index).padStart(3,'0')}`
    const body=files[filename]
    if(typeof body!=='string')throw new Error(`missing grouped STL content: ${filename}`)
    const solidNames=[...body.matchAll(/^solid\s+(\S+)\s*$/gm)].map(match=>match[1]!).filter(Boolean)
    for(const solidName of solidNames) {
      const key=`${fileId}:${solidName}`
      if(seenSolids.has(key))throw new Error(`duplicate source STL solid ${key}`)
      seenSolids.add(key)
      if(sources[key])continue
      const model=modelNames.get(solidName)
      if(model&&((model.bucket==='baffle'&&filename.toLowerCase().includes('baffle'))||
        (model.bucket==='floor'&&filename.toLowerCase().includes('floor')))) {
        matchedModelNames.add(solidName)
        sources[key]={stlFileId:fileId,solidName,sourceObjectUid:model.uid,
          sourceFaceUid:`${project.uid}/${model.uid}/source-sheet`,representation:'zeroThicknessSheet'}
      } else {
        const rackSheet=rackSheets.get(`${filename}:${solidName}`)
        if(rackSheet) {
          matchedRackSheets.add(`${filename}:${solidName}`)
          sources[key]={stlFileId:fileId,solidName,sourceObjectUid:rackSheet.uid,
            sourceFaceUid:rackSheet.sourceFaceUid,representation:'zeroThicknessSheet'}
          continue
        }
        const server=serverBindings.get(solidName)
        if(server)sources[key]={stlFileId:fileId,solidName,sourceObjectUid:server.uid,
          sourceFaceUid:`${project.uid}/${server.uid}/${server.face}`,representation:'surfaceFace'}
      }
    }
  }
  if(project.runDefaults?.hybridHeatBudget) {
    for(const [name,model] of modelNames)if(!matchedModelNames.has(name)&&
      !(model.bucket==='floor'&&matchedFloorUids.has(model.uid)))
      throw new Error(`hybrid source ${model.bucket} ${name} (${model.uid}) is missing its STL solid`)
    for(const key of rackSheets.keys())if(!matchedRackSheets.has(key))
      throw new Error(`hybrid rack blanking source ${key} is missing its STL solid`)
  }
  manifest.geometryExport.sourceSurfaces=sources
}

/**
 * The full V2 manifest, with the transitional v1 blob attached.
 *
 * Mirrors `buildCfdrunJsonV2` (STL_generate.html:30758). The order matters:
 * `buildManifest` validates the pure 13-key V2 document BEFORE `meshGeometry`
 * is attached, so attaching it first would break that check — and the backend
 * needs both, reading the v1 blob for the unchanged mesh pipeline and the V2
 * boundaryBindings for the BC registry.
 */
export async function buildManifestV2(
  api: CfdrunBuildApi,
  project: CfdProject,
  timestamp: string
): Promise<CfdrunV2> {
  // The export is of the start values (t = 0), whatever the playhead. A
  // transient air run with moving air keyframes carries schedules sampled from
  // the timeline and the transient block (plan T4b).
  const source = project
  const timeline = airTimeline(source)
  // A transient air run carries its start and duration even without keyframes
  // (found in the E8c gate: the start condition never reached the runner).
  const run = airTransientRun(source)
  project = projectAtStart(project)
  assertGeometryTopologyAirExportReady(project)
  const stl = api.buildComprehensiveStlMap(project)
  lastStlMap = stl
  const compile = (p: CfdProject) => cfdrunExport().buildManifest(p, stl, timestamp) as unknown as Promise<AirManifest>
  const manifest = (await compile(project)) as unknown as CfdrunV2
  if (timeline) await addAirSchedules(manifest as unknown as AirManifest, source, timeline, compile)
  else if (run) (manifest as unknown as AirManifest).transient = JSON.parse(JSON.stringify({ durationS: run.durationS, start: run.start }))
  // The transitional v1 blob the runner still reads geometry through. cfdrun
  // v4 (E8a) carries ONE simulationConfig — the root: the blob's derived copy is
  // dropped, and the two values only it carried (room temperature, from the
  // geometry, and the monitoring config) move to the root. The runner derives
  // what it executes from the root (derived_config.execution_blob).
  const blob = api.buildCfdrunJson() as Record<string, unknown>
  const derived = (blob.simulationConfig ?? {}) as { roomTemperature?: unknown; monitoring?: unknown }
  delete blob.simulationConfig
  const root = { ...(manifest.simulationConfig as Record<string, unknown> | undefined) }
  // durationS owns the physical stop time, including imported stale mirrors.
  if (run) root.endTimeSeconds = run.durationS
  root.roomTemperatureC = derived.roomTemperature
  // An 'all' ACU list (the default) becomes the returns this manifest binds.
  root.monitoring = resolveMonitoring(derived.monitoring ?? {}, { ...manifest, meshGeometry: blob })
  manifest.simulationConfig = root as CfdrunV2['simulationConfig']
  manifest.meshGeometry = blob as CfdrunV2['meshGeometry']
  manifest.schemaVersion = 4
  return manifest
}

/**
 * The blockMesh domain the runner will use, for the mesh panel's readout.
 *
 * Computed by the SHARED builder rather than re-derived here: the bounds are
 * the room polygon padded by one whole cell, and a second implementation would
 * drift from the exported .cfdrun the moment either side changed.
 */
export function blockMeshBounds(
  project: CfdProject,
  globalGroup: unknown,
): { min: { x: number; y: number; z: number }; max: { x: number; y: number; z: number } } | null {
  try {
    const api = createCfdrunBuild({
      state: project,
      globalGroup,
      LENGTH_UNITS: {
        mm: { scale: 0.001, label: 'mm', step: 0.1, precision: 4 },
        cm: { scale: 0.01, label: 'cm', step: 0.01, precision: 4 },
        m: { scale: 1, label: 'm', step: 0.0001, precision: 4 },
      },
      _MESH_MODES: ['bodyFitted', 'cartesianLocalized', 'structured'],
    } as unknown as CfdrunContext)
    const get = api._getBMBounds as (() => {
      min: { x: number; y: number; z: number }
      max: { x: number; y: number; z: number }
    }) | undefined
    return get ? get() : null
  } catch {
    // The shared module may not have loaded yet on the first render; the panel
    // shows a dash rather than blanking the tab.
    return null
  }
}


/** The solid map of the newest manifest build (geometry only), for `rebindAirParameters`. */
let lastStlMap: Record<string, unknown> | null = null

/**
 * Recompute the ACU and server boundary bindings (and the inputs block) of a manifest from the project's current
 * parameters, with the export's own translators and the solid map of the build that produced the manifest: what
 * a full build gives for those keys when only flows, loads or supply temperatures changed, without the scene work
 * (the AI solve in real time: about 0.35 s of a DCTS room's 0.5 s manifest). The caller owns knowing the geometry
 * did not change; false when no build has run yet.
 */
export function rebindAirParameters(manifest: CfdrunV2, project: CfdProject): boolean {
  const stl = lastStlMap
  if (!stl) return false
  const exp = cfdrunExport()
  const st = projectAtStart(project)
  const bySource: Record<string, unknown> = {}
  for (const entry of Object.values(stl) as { source?: { modelUid?: unknown; faceId?: unknown; patchId?: unknown } }[]) {
    const src = entry?.source
    if (src && src.modelUid != null && src.faceId != null && src.patchId != null) {
      bySource[`${String(src.modelUid)}|${String(src.faceId)}|${String(src.patchId)}`] = entry
    }
  }
  const ctx = { geometryExport: (manifest as unknown as { geometryExport?: unknown }).geometryExport, stlMap: stl, bySource }
  const bindings = (manifest as unknown as { boundaryBindings: Record<string, unknown> }).boundaryBindings
  Object.assign(bindings, exp.translateAcus(st, ctx).boundaryBindings, exp.translateRackServers(st, ctx).boundaryBindings)
  ;(manifest as unknown as { inputs: unknown }).inputs = exp.assembleInputs(st)
  return true
}
