const fs=require('node:fs'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const I='/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration';
const modpath=I+'/src/frontend/shared/js/cfdrun_export.js';const api=require(modpath);
const registryPath=I+'/src/frontend/shared/transient/transient_params.json';const registry=JSON.parse(fs.readFileSync(registryPath,'utf8'));
const T=require(I+'/src/frontend/shared/js/cfdproj_transient.js');T.setRegistry(registry);
const clone=v=>JSON.parse(JSON.stringify(v));
const p={uid:'independent-hash-audit',constructions:{racks:{r:{uid:'r',meta:{enabled:true},geometry:{location:{x:1,y:2,z:3},size:{x:1,y:2,z:1}},constructions:{servers:{s:{cooling:{power:10}}}}}}},transient:{tracks:[{modelUid:'r',param:'server.power',target:'s',keyframes:[{t:0,value:10},{t:20,value:20}]}]}};
const initial=api.projectContentHashSync(p),rows=[];
for(const [name,change,wantSame] of [
 ['static location',p=>p.constructions.racks.r.geometry.location.x=2,false],
 ['static size',p=>p.constructions.racks.r.geometry.size.x=2,false],
 ['enabled visibility',p=>p.constructions.racks.r.meta.enabled=false,false],
 ['source object UID',p=>p.constructions.racks.r.uid='different',false],
 ['opacity/display JSON',p=>p.constructions.racks.r.meta.display={opacity:.4},false],
 ['movement permission',p=>p.constructions.racks.r.moveTool={enabled:false},false],
 ['rotation permission',p=>p.constructions.racks.r.rotateTool={enabled:false},false],
 ['tracked power materialization',p=>p.constructions.racks.r.constructions.servers.s.cooling.power=99,true],
 ['track coefficient',p=>p.transient.tracks[0].keyframes[0].value=99,false],
 ['nonenumerable result carrier',p=>Object.defineProperty(p,'liquidResults',{value:{frame:99},enumerable:false}),true],
 ['unknown JSON field',p=>p.constructions.racks.r.reviewUnknown='changed',false]
]){const q=clone(p);change(q);const got=api.projectContentHashSync(q),same=got===initial;assert.equal(same,wantSame,name);rows.push({name,expectedSame:wantSame,actualSame:same,hash:got});}
const q=clone(p);q.constructions.racks.r.geometry.location.x=2;assert.notEqual(api.projectContentHashSync(q),initial);q.constructions.racks.r.geometry.location.x=1;assert.equal(api.projectContentHashSync(q),initial);rows.push({name:'ABA canonical content restores hash',actualSame:true});
const casePath=I+'/case/Shinaide_Liquid/Shinaide_liquid.cfdproj',bytes=fs.readFileSync(casePath);const real=JSON.parse(bytes);const samples=[];let result;
for(let i=0;i<12;i++){const start=process.hrtime.bigint();result=api.projectContentHashSync(real);samples.push(Number(process.hrtime.bigint()-start)/1e6)}
assert.equal(result,api.projectContentHashSync(real));
console.log(JSON.stringify({sourceModuleSha256:crypto.createHash('sha256').update(fs.readFileSync(modpath)).digest('hex'),registrySha256:crypto.createHash('sha256').update(fs.readFileSync(registryPath)).digest('hex'),rows,case:{path:casePath,rawBytes:bytes.length,rawSha256:crypto.createHash('sha256').update(bytes).digest('hex'),contentHash:result,synchronousHashMs:samples,scope:'CPU35 saved case Node JS; not current browser live project benchmark'}},null,2));
