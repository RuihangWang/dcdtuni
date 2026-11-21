import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import * as THREE from '../../src/frontend/react/node_modules/three/build/three.module.js';
const root=path.resolve(import.meta.dirname??path.dirname(new URL(import.meta.url).pathname),'../..');
const context=vm.createContext({window:{},THREE,console});
for(const n of ['rack_state.js','geometry_pure.js'])vm.runInContext(fs.readFileSync(path.join(root,'src/frontend/shared/js',n),'utf8'),context,{filename:n});
const api=context.window.geometryPure;
const sourceFiles=[];const cases=[];
for(const filename of ['case/LiquidSingle/LiquidSingle.cfdproj','case/Shinaide_Liquid/Shinaide_liquid.cfdproj']){
 const bytes=fs.readFileSync(path.join(root,filename));const doc=JSON.parse(bytes);
 sourceFiles.push({path:filename,bytes:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex')});
 for(const [bucket,kind] of [['racks','rack'],['cdus','cdu']]){
  const [key,actual]=Object.entries(doc.constructions[bucket])[0];
  const g=actual.geometry??{};
  const model={uid:actual.uid??key,kind,meta:{name:actual.meta?.name??key},geometry:Object.fromEntries(['location','size','quaternion','scale','slot','slotHeight'].filter(k=>k in g).map(k=>[k,g[k]])),cooling:{liquidManifold:actual.cooling?.liquidManifold??{}},safeMarginBottom:actual.safeMarginBottom};
  cases.push({name:filename+'/'+key,bucket,model,point:{x:.3,y:.4,z:.5},direction:{x:1,y:0,z:-1},portType:'supply',end:'top',sourceRecordSHA:crypto.createHash('sha256').update(JSON.stringify(actual)).digest('hex')});
 }
}
const rack=structuredClone(cases.find(c=>c.model.kind==='rack').model);
const cdu=structuredClone(cases.find(c=>c.model.kind==='cdu').model);
const edge=(name,model,extra)=>cases.push({name,bucket:model.kind==='rack'?'racks':'cdus',model,point:{x:'2',y:'NaN',z:-.5},direction:{x:0,y:0,z:0},portType:'return',end:'bottom',...extra});
const mirror=structuredClone(rack);mirror.geometry.scale={x:-1.25,y:.5,z:2.25};mirror.geometry.quaternion={x:.2,y:.3,z:-.4,w:.5};mirror.geometry.location={x:-.75,y:3.125,z:2.375};mirror.cooling.liquidManifold={layout:'both-right',mountFace:'outlet',pipeSpacing:0,supplyPipe:{diameter:0},returnPipe:{diameter:-.025}};
edge('mirrored nonuniform rack rotated outlet bottom',mirror,{direction:{x:1,y:2,z:3},portType:'manifold_in'});
const both=structuredClone(rack);both.cooling.liquidManifold={layout:'split',mountFace:'inlet',pipeSpacing:-.01,supplyPipe:{diameter:.025},returnPipe:{diameter:.05}};
edge('split rack return inlet bottom',both,{});
const zero=structuredClone(cdu);zero.geometry.quaternion={x:0,y:0,z:0,w:0};zero.geometry.scale={x:0,y:-2,z:.25};
edge('zero quaternion zero scale cdu manifold_in',zero,{portType:'manifold_in'});
const defaults={uid:'defaults',kind:'rack',geometry:{location:{x:'2',y:null,z:0},scale:{x:'2',y:0,z:null},quaternion:{x:'1',w:'0'},size:{x:'0.6',y:'2',z:'0.8'},slot:0,slotHeight:.04445},cooling:{liquidManifold:{layout:'',supplyPipe:{diameter:'0'},returnPipe:{diameter:'0'},pipeSpacing:'0'}},safeMarginBottom:'0.2'};
edge('numeric-string transform defaults and coerced dimensions',defaults,{portType:'unrecognized',end:'unrecognized'});
const threshold=structuredClone(rack);threshold.geometry.slot=1;threshold.geometry.slotHeight=1e-6;threshold.safeMarginBottom=.25;
edge('rack slotHeight exactly threshold fallback',threshold,{portType:'supply',end:'top'});
const above=structuredClone(threshold);above.geometry.slotHeight=1.000000001e-6;
edge('rack slotHeight above threshold',above,{portType:'return',end:'top'});
function flatPoint(p){return p===null?null:{x:p.x,y:p.y,z:p.z};}
function bits(values){const b=Buffer.alloc(values.length*8);values.forEach((v,i)=>b.writeDoubleLE(v,i*8));return b.toString('hex');}
for(const c of cases){
 const clone=api.modelWorldTransform(c.model);
 const q=new THREE.Quaternion(clone.quaternion.x,clone.quaternion.y,clone.quaternion.z,clone.quaternion.w).normalize();
 const loc=c.model.geometry?.location??{};
 const pose={position:{x:Number(loc.x)||0,y:Number(loc.y)||0,z:Number(loc.z)||0},quaternion:{x:q.x,y:q.y,z:q.z,w:q.w},scale:clone.scale};
 const point=flatPoint(api.modelLocalPointToWorld(c.model,c.point)),direction=flatPoint(api.modelLocalDirectionToWorld(c.model,c.direction));
 const frame=api.liquidEquipmentPortFrame(c.model,c.portType,c.end);
 const port=Object.fromEntries(['position','direction','localPosition','localDirection'].map(k=>[k,flatPoint(frame[k])]));
 const vals=[...Object.values(pose.position),...Object.values(pose.quaternion),...Object.values(pose.scale),...Object.values(point),...Object.values(direction),...Object.values(port.position),...Object.values(port.direction),...Object.values(port.localPosition),...Object.values(port.localDirection)];
 c.expected={pose,point,direction,port,float64LEHex:bits(vals)};
 delete c.model.kind;
}
const outfile=path.join(root,'src/frontend/react/test/fixtures/modelPosePortsParity.json');
fs.writeFileSync(outfile,JSON.stringify({schema:'dcdtuni-legacy-pose-port-golden/v1',producer:'Unmodified geometry_pure.js and rack_state.js + installed Three0.184; captured before new kernel/domain capability',sourceFiles,producerSources:['geometry_pure.js','rack_state.js'].map(n=>({path:'src/frontend/shared/js/'+n,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'src/frontend/shared/js',n))).digest('hex')})),cases},null,2)+'\n');
console.log(JSON.stringify({cases:cases.length,fixture:outfile,bytes:fs.statSync(outfile).size}));
