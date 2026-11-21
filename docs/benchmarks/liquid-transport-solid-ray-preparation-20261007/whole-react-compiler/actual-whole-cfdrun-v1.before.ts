import {readFileSync,writeFileSync,mkdirSync,realpathSync,existsSync} from 'node:fs'
import {resolve,dirname} from 'node:path'
import {createHash} from 'node:crypto'
import {performance} from 'node:perf_hooks'
import {JSDOM} from 'jsdom'
const BASE='/mnt/lv/home/litianyu/mycode/dcdtuni'
const REPO=realpathSync(process.env.DCDTUNI_COMPILER_REPO||BASE+'/.codex-tmp/goal-20261005/integration')
const CASE=realpathSync(process.env.DCDTUNI_COMPILER_CASE||REPO+'/.codex-tmp/cases/Shinaide_RingRear_CUA/Shinaide_DAEControls_GPU_Glycol_GUI_20261007.cfdproj')
const OUT=resolve(process.env.DCDTUNI_COMPILER_OUT||'')
function contained(p:string){return p===BASE||p.startsWith(BASE+'/')}
if(!contained(REPO)||!contained(CASE)||!contained(OUT)||OUT===BASE||existsSync(OUT))throw new Error('expected new private B-contained output and B-contained repo/case')
mkdirSync(OUT,{recursive:true});if(!contained(realpathSync(OUT)))throw new Error('output realpath outside B')
const sha=(x:string|Buffer)=>createHash('sha256').update(x).digest('hex')
const paths=['src/frontend/react/src/interaction/sceneBuilder.ts','src/frontend/react/src/legacy/geometryBuilder.ts','src/frontend/react/src/legacy/cfdrunBuild.ts','src/frontend/shared/js/geometry_scene.js','src/frontend/shared/js/geometry_pure.js','src/frontend/shared/js/cfdrun_build.js','src/frontend/shared/js/cfdrun_export.js','src/frontend/shared/vendor/three-csg-ts.umd.js','src/frontend/shared/js/cfdproj_state_v4.js','src/frontend/shared/js/model_pose_ports.js','src/backend/aircooling/runner/stlgen_runner.py','src/backend/aircooling/runner/cfdrun_schema.py']
const sourceBefore=Object.fromEntries(paths.map(p=>[p,sha(readFileSync(REPO+'/'+p))]))
writeFileSync(OUT+'/SOURCE-BEFORE.json',JSON.stringify(sourceBefore,null,2))
const input=readFileSync(CASE),inputSha=sha(input),started=performance.now()
const receipt:any={schema:'dcdtuni.actual-whole-react-cfdrun-replay/v1',repo:REPO,case:CASE,inputSha256:inputSha,sourceBefore,allowedCpus:readFileSync('/proc/self/status','utf8').match(/^Cpus_allowed_list:\s*(.+)$/m)?.[1],nativeMeshRun:false,browserRun:false,stages:[],exportCalls:0}
let builder:any,oldAll:any,win:any
function phase(name:string,extra:any={}){receipt.stages.push({name,elapsedS:(performance.now()-started)/1000,...extra});writeFileSync(OUT+'/RECEIPT.json',JSON.stringify(receipt,null,2))}
try{
 const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost/',pretendToBeVisual:true});win=dom.window
 for(const key of ['window','document','navigator','HTMLElement','HTMLCanvasElement','Element','Node','Image','XMLSerializer','DOMParser','localStorage'])Object.defineProperty(globalThis,key,{value:(win as any)[key],configurable:true})
 const THREE=await import('three')
 await import(REPO+'/src/frontend/react/src/legacy/geometryBuilder.ts')
 const {stateV4}=await import(REPO+'/src/frontend/react/src/legacy/cfdprojModules.ts')
 const {compileDynamicLiquidGraph}=await import(REPO+'/src/frontend/react/src/liquid/dynamicLiquidGraph.ts')
 const {createSceneBuilder,emptyRegistries}=await import(REPO+'/src/frontend/react/src/interaction/sceneBuilder.ts')
 const {createCfdrunBuild,buildManifestV2,bindStlFileHashes}=await import(REPO+'/src/frontend/react/src/legacy/cfdrunBuild.ts')
 const p=stateV4().normalizeStateV4(JSON.parse(input.toString())),projectBefore=JSON.stringify(p),models=stateV4().allModels(p),ids=models.map((m:any)=>m.uid)
 if(ids.length!==36||new Set(ids).size!==ids.length)throw new Error('actual target must contain 36 unique OO model UIDs')
 const graphBefore=compileDynamicLiquidGraph(p),graphHash=sha(JSON.stringify(graphBefore)),g=new THREE.Group(),w=new THREE.Group();w.add(g);w.updateMatrixWorld(true)
 const registries=emptyRegistries();oldAll=win.allModels;win.allModels=()=>stateV4().allModels(p)
 builder=createSceneBuilder({globalGroup:g,wrapperGroup:w,registries,project:()=>p,selectedUid:()=>null,webglAvailable:false})
 phase('source-imported-normalized',{projectUid:p.uid,modelIds:ids,modelKinds:models.map((m:any)=>({uid:m.uid,kind:m.kind})),graphNodes:graphBefore.nodes.length,graphEdges:graphBefore.edges.length,graphHash})
 builder.rebuild();phase('whole-real-scene-rebuilt',{renderRoots:registries.renderRoots.length,renderMeshes:registries.renderMeshes.length})
 let files:Record<string,string>|null=null,requestKey:string|null=null,exportError:any=null
 const api=createCfdrunBuild({state:p,globalGroup:g,LENGTH_UNITS:{m:1,mm:.001,cm:.01},_MESH_MODES:['bodyFitted','cartesianLocalized','structured'],buildGroupedStlMap:(requested:string[],leakage:boolean)=>{
   if(JSON.stringify(p)!==projectBefore)throw new Error('source project changed before actual export')
   if(requested.join('\0')!==ids.join('\0')||leakage!==true)throw new Error('actual comprehensive compiler requested a different whole model set/leakage flag')
   const key=JSON.stringify({requested,leakage});if(requestKey&&requestKey!==key)throw new Error('cached original files requested with changed inputs');requestKey=key
   receipt.requestedModelIds=requested.slice();receipt.includeLeakage=leakage
   if(!files){const start=performance.now();receipt.exportCalls++;try{files=builder.api.buildGroupedStlMap(requested,leakage)}catch(e){exportError=e;throw e}phase('actual-original-grouped-returned',{exportSeconds:(performance.now()-start)/1000,fileNames:Object.keys(files!),bytes:Object.values(files!).reduce((n:number,t:string)=>n+Buffer.byteLength(t),0)})}
   return files!
 }})
 const manifest=await buildManifestV2(api,p,'2026-10-07T00:00:00.000Z')
 if(exportError)throw exportError
 if(!files||receipt.exportCalls!==1)throw new Error('compiler did not consume exactly one original grouped export')
 phase('actual-react-manifest-compiled')
 const catalogue=win.geometryScene.getGroupedStlSourceCatalogue(files)
 if(!catalogue||catalogue.files!==files&&Object.keys(catalogue.files).some(name=>catalogue.files[name]!==files![name]))throw new Error('original returned map lacks exact emitter record')
 bindStlFileHashes(manifest,files,p);phase('actual-react-source-bound')
 if(JSON.stringify(p)!==projectBefore||sha(JSON.stringify(compileDynamicLiquidGraph(p)))!==graphHash||sha(readFileSync(CASE))!==inputSha)throw new Error('project, source case or liquid graph changed')
 const transport=Object.values(manifest.geometryExport.solids).filter((r:any)=>r.kind==='liquid_transport_wall') as any[]
 if(transport.length!==6||transport.some(r=>r.role!=='wall'||!(r.areaM2>0)||r.path!=='merged_liquid_transport.stl'||r.surfaceUid!==null))throw new Error('actual whole manifest lacks six positive owned wall regions in one carrier')
 const transportSources=Object.values(manifest.geometryExport.sourceRegions).filter((r:any)=>r.operation==='liquidTransportUnionBoundary') as any[]
 if(transportSources.length!==6)throw new Error('actual whole source catalogue lacks six transport regions')
 const sourceAfter=Object.fromEntries(paths.map(rel=>[rel,sha(readFileSync(REPO+'/'+rel))]));if(JSON.stringify(sourceAfter)!==JSON.stringify(sourceBefore))throw new Error('normal source changed during actual replay')
 mkdirSync(OUT+'/files');for(const [name,text]of Object.entries(files)){if(!/^[A-Za-z0-9_.-]+\.stl$/.test(name))throw new Error('unsafe actual filename');writeFileSync(OUT+'/files/'+name,text)}
 const output=JSON.stringify(manifest,null,2);writeFileSync(OUT+'/files/transport.cfdrun',output)
 const catalogueReceipt={schema:catalogue.schema,projectUid:catalogue.projectUid,liquidTransport:catalogue.liquidTransport,regions:catalogue.regions.map((r:any)=>({filename:r.filename,solidName:r.solidName,sourceEntityUid:r.sourceEntityUid,facetCount:r.facetCount,operation:r.operation,sourceEntitySnapshot:r.sourceEntitySnapshot,carrierUid:r.carrierUid,carrierContributorUids:r.carrierContributorUids,blockSha256:sha(r.block)}))}
 writeFileSync(OUT+'/source-catalogue.json',JSON.stringify(catalogueReceipt,null,2))
 receipt.sourceAfter=sourceAfter;receipt.manifestSha256=sha(output);receipt.manifestSchemaVersion=manifest.schemaVersion;receipt.geometrySolids=Object.keys(manifest.geometryExport.solids).length;receipt.sourceRegions=Object.keys(manifest.geometryExport.sourceRegions).length;receipt.transportWalls=transport;receipt.transportSources=transportSources;receipt.files=Object.fromEntries(Object.entries(files).map(([name,text])=>[name,{bytes:Buffer.byteLength(text),sha256:sha(text)}]));receipt.projectUnchanged=true;receipt.liquidGraphUnchanged=true;receipt.sourceCaseUnchanged=true;receipt.ok=true;phase('written-all-original-files-and-manifest');console.info('ACTUAL_WHOLE_CFDRUN',JSON.stringify({ok:true,elapsedS:(performance.now()-started)/1000,files:Object.keys(files).length,geometrySolids:receipt.geometrySolids,sourceRegions:receipt.sourceRegions,transportWalls:transport.length,exportCalls:receipt.exportCalls,manifestSha256:receipt.manifestSha256}))
}catch(e){receipt.ok=false;receipt.error={name:(e as Error).name,message:(e as Error).message,stack:(e as Error).stack};phase('actual-replay-refused');console.error('ACTUAL_WHOLE_CFDRUN_REFUSED',JSON.stringify(receipt.error));process.exitCode=1}
finally{builder?.clear();if(win){if(oldAll)win.allModels=oldAll;else delete win.allModels;delete win._meshExportGroupedMap}receipt.elapsedS=(performance.now()-started)/1000;writeFileSync(OUT+'/RECEIPT.json',JSON.stringify(receipt,null,2))}
