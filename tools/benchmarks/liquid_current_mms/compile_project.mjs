import fs from 'node:fs';import path from 'node:path';import {createRequire} from 'node:module';import {fileURLToPath} from 'node:url';import {createHash} from 'node:crypto'
const here=path.dirname(fileURLToPath(import.meta.url)), Q=process.env.DCTWIN_MMS_WORK, I=process.env.DCTWIN_MMS_REPO, deps=process.env.DCTWIN_MMS_FRONTEND_DEPS || path.join(I,'src/frontend/react')
if(!Q || !I)throw new Error('Run via reproduce.py')
process.env.GOMAXPROCS='1';process.env.UV_THREADPOOL_SIZE='1'
const req=createRequire(path.join(deps,'package.json'));process.env.NODE_PATH=path.join(deps,'node_modules');req('node:module').Module._initPaths()
const sha=b=>createHash('sha256').update(b).digest('hex')
const {JSDOM}=req('jsdom');const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost/'})
globalThis.window=dom.window;globalThis.document=dom.window.document
for(const key of ['THREE','cfdprojStateV4','geometryPure','polygonClipping','rackState','state'])Object.defineProperty(globalThis,key,{configurable:true,get:()=>window[key],set:value=>{window[key]=value}})
globalThis.alert=message=>{throw new Error(String(message))};window.alert=globalThis.alert
const rawPlugin={name:'raw-suffix',setup(build){build.onResolve({filter:/\?raw$/},args=>({path:path.resolve(args.resolveDir,args.path.replace(/\?raw$/,'')),namespace:'raw-text'}));build.onLoad({filter:/.*/,namespace:'raw-text'},args=>({contents:fs.readFileSync(args.path,'utf8'),loader:'text'}))}}
const bundle=path.join(Q,'compiled/current-react-entry.cjs')
const entry=path.join(Q,'compiled/current_compile_entry.ts');fs.writeFileSync(entry,fs.readFileSync(path.join(here,'current_compile_entry.ts.template'),'utf8').replaceAll('__DCTWIN_REPO__',I.replaceAll('\\','/')))
const built=await req('esbuild').build({entryPoints:[entry],nodePaths:[path.join(deps,'node_modules')],bundle:true,platform:'node',format:'cjs',outfile:bundle,logLevel:'silent',plugins:[rawPlugin],metafile:true})
const sourceRows=[]
for(const key of Object.keys(built.metafile.inputs)) {
 let p=key.startsWith('raw-text:')?key.slice(9):key
 p=path.resolve(process.cwd(),p)
 if(!fs.existsSync(p)||!fs.statSync(p).isFile())throw new Error('Missing actual bundled source '+p)
 const data=fs.readFileSync(p);const row={path:p,bytes:data.length,sha256:sha(data)}
 if(p.startsWith(I+'/src/frontend/')) {const target=path.join(Q,'react-source-snapshot',path.relative(I,p));fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,data);row.snapshot=target}
 sourceRows.push(row)
}
fs.writeFileSync(path.join(Q,'SOURCE-MAP-react.json'),JSON.stringify({scope:'Actual current React compiler bundle inputs; dependency modules read only, product source by-value copies, no source/service writes',rows:sourceRows},null,2)+'\n')
const {compileDocument}=req(bundle);const {independentProjectFingerprint}=req(path.join(here,'independent_project_fingerprint.cjs'));const registry=JSON.parse(fs.readFileSync(path.join(here,'transient_params.json'),'utf8'))
const files=process.argv.slice(2);if(!files.length)throw new Error('No actual project paths')
for(const file of files) {
 const bytes=fs.readFileSync(file);const input=JSON.parse(bytes);const result=compileDocument(input)
 const independent=independentProjectFingerprint(result.sourceDocument,registry)
 if(result.liquidrun.source_project.sha256!==independent)throw new Error('Actual canonical project SHA disagrees with independent fingerprint '+file)
 const name=path.basename(file,'.cfdproj');const out=path.join(Q,'compiled',name+'.liquidrun');fs.writeFileSync(out,JSON.stringify(result.liquidrun,null,2)+'\n')
 const receipt={project:file,projectBytes:bytes.length,projectRawSHA256:sha(bytes),canonicalProjectSHA256:independent,derived:out,derivedRawSHA256:sha(fs.readFileSync(out)),time:result.liquidrun.time,fluid:result.liquidrun.fluid,analysis:result.liquidrun.analysis,nodes:result.liquidrun.nodes.length,edges:result.liquidrun.edges.length,coldplates:result.liquidrun.edges.filter(e=>e.type==='cold_plate').length,controllers:result.controllers.length,sourceUnmodified:true,solverExecuted:false}
 fs.writeFileSync(path.join(Q,'compiled',name+'.compile.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify(receipt))
}
for(const row of sourceRows)if(sha(fs.readFileSync(row.path))!==row.sha256)throw new Error('Compiler source drift during actual preparation '+row.path)
