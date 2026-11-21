#!/usr/bin/env node
/** MMS product fixture generation + real React compiler replay; CPU-only, no UI/solver/server.
 *  Mirrors tools/generate_nv_mock.mjs (same jsdom+esbuild bundle of the REAL
 *  shared compiler); no NV point list is involved — this fixture is purely
 *  synthetic/analytic. Deterministic: reruns must reproduce byte-identical
 *  outputs (--check mode enforces). */
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
const args=process.argv.slice(2)
const option=(name,fallback)=>{const index=args.indexOf(name);return index<0?fallback:path.resolve(args[index+1])}
const projectPath=option('--project',path.join(repo,'case/MMS_Product/MMS_product.cfdproj'))
const runPath=option('--output',path.join(repo,'docs/benchmarks/fixtures/mms-product-v3.liquidrun'))
const templatePath=path.join(repo,'case/verification/shinaide-legacy-projects/Shinaide_hybrid_cpu.json')
const read=file=>fs.readFileSync(file)
const templateBytes=read(templatePath)
try{os.setPriority(0,10)}catch{/* the shell may already enforce niceness */}
const {JSDOM}=require('jsdom')
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost/'})
globalThis.window=dom.window;globalThis.document=dom.window.document
for(const key of ['THREE','cfdprojStateV4','geometryPure','polygonClipping','rackState','state'])Object.defineProperty(globalThis,key,{configurable:true,get:()=>window[key],set:value=>{window[key]=value}})
globalThis.alert=message=>{throw new Error(String(message))}
window.alert=globalThis.alert
// Keep the bundle INSIDE the repo (.codex-tmp discipline) and point NODE_PATH at
// the React dependencies so runtime requires of vendored UMD peers (three) resolve.
process.env.NODE_PATH=path.join(repo,'src/frontend/react/node_modules')
require('node:module').Module._initPaths()
fs.mkdirSync(path.join(repo,'.codex-tmp'),{recursive:true})
const temp=fs.mkdtempSync(path.join(repo,'.codex-tmp','mms-compile-'))
try{
  const bundle=path.join(temp,'entry.cjs')
  // geometryBuilder.ts imports the vendored three-csg UMD via the vite-only
  // '?raw' suffix (since f6337592, 09-23). Plain esbuild would bundle it as a
  // JS module (-> new Function('[object Object]') crash), so reproduce vite's
  // raw-text semantics with an explicit loader plugin.
  const rawPlugin={name:'raw-suffix',setup(build){
    build.onResolve({filter:/\?raw$/},args=>({path:path.resolve(args.resolveDir,args.path.replace(/\?raw$/,'')),namespace:'raw-text'}))
    build.onLoad({filter:/.*/,namespace:'raw-text'},args=>({contents:fs.readFileSync(args.path,'utf8'),loader:'text'}))
  }}
  await require('esbuild').build({entryPoints:[path.join(repo,'tools/compile_mms_product.ts')],bundle:true,platform:'node',format:'cjs',outfile:bundle,logLevel:'silent',plugins:[rawPlugin]})
  const {buildMmsProductProject,compileProjectDocument,MMS_TARGETS}=require(bundle)
  const template=JSON.parse(templateBytes),pack=JSON.parse(read(path.join(repo,'src/backend/liquidcooling/data/mock-demo-v1.json')))
  const project=buildMmsProductProject(template,pack,{templateSha256:sha(templateBytes)})
  const projectText=JSON.stringify(project,null,2)+'\n'
  const input=JSON.parse(projectText),before=JSON.stringify(input)
  const graph=compileProjectDocument(input)
  if(JSON.stringify(input)!==before)throw new Error('Frontend compilation mutated the project fixture')
  if(graph.version!=='3.0')throw new Error('Expected the real project DAE compiler to produce v3')
  graph.source_project.sha256=sha(projectText)
  const runText=JSON.stringify(graph,null,2)+'\n'
  const edges=Object.fromEntries(['pump','heat_exchanger','valve','cold_plate','pipe'].map(type=>[type,graph.edges.filter(e=>e.type===type).length]))
  if(edges.pump!==1||edges.heat_exchanger!==1||edges.cold_plate!==1)throw new Error(`Unexpected compiled MMS topology inventory: ${JSON.stringify(edges)}`)
  const adjacency=new Map(graph.nodes.map(n=>[n.id,[]]))
  for(const e of graph.edges){adjacency.get(e.from).push(e.to);adjacency.get(e.to).push(e.from)}
  const seen=new Set();let components=0
  for(const n of graph.nodes){if(seen.has(n.id))continue;components++;const q=[n.id];seen.add(n.id);while(q.length){const id=q.pop();for(const next of adjacency.get(id))if(!seen.has(next)){seen.add(next);q.push(next)}}}
  if(components!==1)throw new Error('Expected exactly one connected MMS zone')
  if(graph.primary_circuits?.length!==1)throw new Error('Expected exactly one primary circuit')
  const circuit=graph.primary_circuits[0]
  const report={scope:'SYNTHETIC ANALYTIC FIXTURE. Not NV topology, vendor data, measurements or simulation results.',
    project:{path:path.relative(repo,projectPath),sha256:sha(projectText)},
    derived:{path:path.relative(repo,runPath),sha256:sha(runText),format:graph.format,version:graph.version},
    mms_targets:MMS_TARGETS,
    source_provenance:project.meta.provenance,
    compiler_modules:['src/frontend/react/src/liquid/liquidExperiment.ts','src/frontend/react/src/liquid/dynamicLiquidGraph.ts','src/frontend/shared/js/liquid_graphgen.js'],
    validation:{source_project_unchanged:true,racks:graph.racks.length,nodes:graph.nodes.length,edges:graph.edges.length,edge_types:edges,connected_components:components,
      time:graph.time,primary_circuit:{edge_id:circuit.edge_id,flow_kg_s:circuit.flow_kg_s,inlet_K:circuit.inlet_K,primary_volume_m3:circuit.primary_volume_m3,secondary_volume_m3:circuit.secondary_volume_m3},
      solver_executed:false}}
  for(const [file,data] of [[projectPath,projectText],[runPath,runText],[runPath.replace(/\.liquidrun$/,'.compile.json'),JSON.stringify(report,null,2)+'\n']]){
    if(args.includes('--check')){if(!fs.existsSync(file)||fs.readFileSync(file,'utf8')!==data)throw new Error(`Fixture replay differs: ${file}`)}
    else{fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,data)}
  }
  console.log(JSON.stringify(report.validation,null,2))
}finally{dom.window.close();fs.rmSync(temp,{recursive:true,force:true})}
