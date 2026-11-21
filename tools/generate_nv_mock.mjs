#!/usr/bin/env node
/** CPU-only fixture generation and real React compiler replay; no UI/solver/server. */
//
// KNOWN STATE (2026-09-28): --check replay of the committed fixture differs by
// 88 lines of float-repr noise only (5.85 vs 5.8500000000000005 family), a
// consequence of post-integration compiler changes; topology and parameters
// are identical. The fixture is the receipt anchor (input_sha256 460f7e24...)
// of the S2-S4 solver benchmarks and must NOT be regenerated for this noise —
// re-anchoring invalidates every receipt that cites it.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..')
process.env.GOMAXPROCS='1'
process.env.UV_THREADPOOL_SIZE='1'
const require=createRequire(path.join(repo,'src/frontend/react/package.json'))
const sha=buffer=>createHash('sha256').update(buffer).digest('hex')
const pointListDefault='/Users/litianyu/Library/CloudStorage/OneDrive-Personal/NTU_stuff/Research/DCTwin/LiquidCooling-DC/NV/Pek02_BMS_PointList_Interface_V3.0_20260916_CQB.xlsx'
const args=process.argv.slice(2)
const option=(name,fallback)=>{const index=args.indexOf(name);return index<0?fallback:args[index+1]}
const projectPath=path.resolve(option('--project',path.join(repo,'src/frontend/react/test/fixtures/cases/NV_PEK02_mock.cfdproj')))
const runPath=path.resolve(option('--output',path.join(repo,'docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun')))
const pointList=path.resolve(option('--point-list',pointListDefault))
const templatePath=path.join(repo,'case/verification/shinaide-legacy-projects/Shinaide_hybrid_cpu.json')
const read=file=>fs.readFileSync(file)
const templateBytes=read(templatePath),sourceBytes=read(pointList)
if(sha(sourceBytes)!=='ec4dc0ee243bdd7e26ecab44791f84039e0f5bbf2317fb1355f93e7780213075')throw new Error('Point-list hash changed; re-verify C3/C31/C40 before regenerating the fixture')
try{os.setPriority(0,10)}catch{/* The shell may already enforce niceness. */}
const {JSDOM}=require('jsdom')
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost/'})
globalThis.window=dom.window;globalThis.document=dom.window.document
// Browser global identifiers and window properties are the same namespace. Node
// keeps them separate; forward only the existing compiler's documented globals.
for(const key of ['THREE','cfdprojStateV4','geometryPure','polygonClipping','rackState','state'])Object.defineProperty(globalThis,key,{configurable:true,get:()=>window[key],set:value=>{window[key]=value}})
globalThis.alert=message=>{throw new Error(String(message))}
window.alert=globalThis.alert
// The application imports the exact shared geometry compiler. Bundle only to
// execute its TypeScript modules in Node; no alternative graph implementation.
// Bundle INSIDE the repo (.codex-tmp discipline) + NODE_PATH so runtime
// requires of vendored UMD peers (three) resolve from the React dependencies.
process.env.NODE_PATH=path.join(repo,'src/frontend/react/node_modules')
require('node:module').Module._initPaths()
fs.mkdirSync(path.join(repo,'.codex-tmp'),{recursive:true})
const temp=fs.mkdtempSync(path.join(repo,'.codex-tmp','nv-mock-compile-'))
try{
  const bundle=path.join(temp,'entry.cjs')
  // geometryBuilder.ts imports the vendored three-csg UMD via the vite-only
  // '?raw' suffix (since f6337592, 09-23); plain esbuild bundles it as a JS
  // module and installThreeCsg then evaluates '[object Object]'. Reproduce
  // vite's raw-text semantics; esbuild plugins require the async build().
  const rawPlugin={name:'raw-suffix',setup(build){
    build.onResolve({filter:/\?raw$/},args=>({path:path.resolve(args.resolveDir,args.path.replace(/\?raw$/,'')),namespace:'raw-text'}))
    build.onLoad({filter:/.*/,namespace:'raw-text'},args=>({contents:fs.readFileSync(args.path,'utf8'),loader:'text'}))
  }}
  await require('esbuild').build({entryPoints:[path.join(repo,'tools/compile_nv_mock.ts')],bundle:true,platform:'node',format:'cjs',outfile:bundle,logLevel:'silent',plugins:[rawPlugin]})
  const {buildNvMockProject,compileProjectDocument}=require(bundle)
  const template=JSON.parse(templateBytes),pack=JSON.parse(read(path.join(repo,'src/backend/liquidcooling/data/mock-demo-v1.json')))
  const project=buildNvMockProject(template,pack,{templateSha256:sha(templateBytes),pointListSha256:sha(sourceBytes)})
  const projectText=JSON.stringify(project,null,2)+'\n'
  // Re-read the serialized document so the same saved project is the compiler source.
  const input=JSON.parse(projectText),before=JSON.stringify(input)
  const graph=compileProjectDocument(input)
  if(JSON.stringify(input)!==before)throw new Error('Frontend compilation mutated the project fixture')
  if(graph.version!=='3.0')throw new Error('Expected the real project DAE compiler to produce v3; schema integration is not ready')
  graph.source_project.sha256=sha(projectText)
  const runText=JSON.stringify(graph,null,2)+'\n'
  const edges=Object.fromEntries(['pump','heat_exchanger','valve','cold_plate','pipe'].map(type=>[type,graph.edges.filter(e=>e.type===type).length]))
  if(edges.pump!==2||edges.heat_exchanger!==2||edges.valve!==102||edges.cold_plate!==34)throw new Error('Unexpected compiled mock topology inventory')
  const adjacency=new Map(graph.nodes.map(n=>[n.id,[]]))
  for(const e of graph.edges){adjacency.get(e.from).push(e.to);adjacency.get(e.to).push(e.from)}
  const components=[],seen=new Set()
  for(const n of graph.nodes){if(seen.has(n.id))continue;const q=[n.id],members=[];seen.add(n.id);while(q.length){const id=q.pop();members.push(id);for(const next of adjacency.get(id))if(!seen.has(next)){seen.add(next);q.push(next)}}components.push(members)}
  if(components.length!==2)throw new Error('Expected exactly two explicitly assumed independent CDU zones')
  const fittings=Object.entries(project.constructions.pipeSegments).flatMap(([pipeUid,p])=>p.cooling.valves.filter(v=>v.type==='flow_control'||v.type==='isolation').map(v=>({asset_uid:v.id,asset_kind:v.meta.assetKind,owner_pipe_uid:pipeUid,series_with:v.seriesWithValveId??null,opening:v.opening,compiled:graph.object_bindings[`${pipeUid}/${v.id}`]})))
  if(fittings.some(v=>!v.compiled||v.compiled.edge_ids.length!==1))throw new Error('An authored valve lacks an unambiguous physical-edge binding')
  const report={scope:'SOURCE-COUNT-ONLY MOCK. Not NV topology, measurements, calibrated parameters, controller validation or simulation results.',project:{path:path.relative(repo,projectPath),sha256:sha(projectText)},derived:{path:path.relative(repo,runPath),sha256:sha(runText),format:graph.format,version:graph.version},source_provenance:project.meta.provenance,compiler_modules:['src/frontend/react/src/liquid/liquidExperiment.ts','src/frontend/react/src/liquid/dynamicLiquidGraph.ts','src/frontend/shared/js/liquid_graphgen.js'],validation:{source_project_unchanged:true,racks:graph.racks.length,nodes:graph.nodes.length,edges:graph.edges.length,edge_types:edges,connected_components:components.length,component_node_counts:components.map(x=>x.length),source_inventory:{CDU:2,energy_valve:34,isolation_valve:68},all_valves_fixed_open:true,initial_rack_heat_W:340000,event_rack_heat_W:408000,solver_executed:false},asset_to_graph:fittings,primary_circuits:graph.primary_circuits}
  for(const [file,data] of [[projectPath,projectText],[runPath,runText],[runPath.replace(/\.liquidrun$/,'.compile.json'),JSON.stringify(report,null,2)+'\n']]){
    if(args.includes('--check')){if(!fs.existsSync(file)||fs.readFileSync(file,'utf8')!==data)throw new Error(`Fixture replay differs: ${file}`)}
    else{fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,data)}
  }
  console.log(JSON.stringify(report.validation,null,2))
}finally{dom.window.close();fs.rmSync(temp,{recursive:true,force:true})}
