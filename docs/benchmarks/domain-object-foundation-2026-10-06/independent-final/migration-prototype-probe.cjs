const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');
const B='/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration';
const O=path.join(B,'.codex-tmp/domain-object-independent-review-20261006');
const rel='src/frontend/shared/js/cfdproj_migrate.js';const modulePath=path.join(B,rel);
const migrate=require(modulePath);
const raw=JSON.parse('{"schemaVersion":3,"meta":{"projectName":"proto"},"construction":[{"uid":"b","kind":"box","name":"Box","__proto__":{"vendorModel":"retained"}}],"simulation":{},"__proto__":{"vendorDocument":"retained"},"__vendorNote":{"retained":1}}');
fs.writeFileSync(path.join(O,'v3-prototype-input.json'),JSON.stringify(raw,null,2));
function info(x){return {ownProto:Object.hasOwn(x,'__proto__'),protoField:x.__proto__,prototypeKeys:Object.keys(Object.getPrototypeOf(x)??{}),ownKeys:Object.keys(x)}}
const migrated=migrate.migrateCfdprojToV4(raw);
const box=migrated.constructions.boxes.b;
const v5=JSON.parse('{"schemaVersion":5,"constructions":{},"__proto__":{"vendorDocument":"retained"}}');
const pass=migrate.migrateCfdprojToV4(v5);
const result={scope:'pure JSON migration only; no renderer, solver, services or global prototype mutation',source:{path:rel,sha256:crypto.createHash('sha256').update(fs.readFileSync(modulePath)).digest('hex')},input:info(raw),migratedRoot:info(migrated),migratedBox:info(box),rootFieldSurvivesJSONString:JSON.parse(JSON.stringify(migrated)).__proto__,modelFieldSurvivesJSONString:JSON.parse(JSON.stringify(box)).__proto__,v5PassThrough:info(pass),unrelatedPlainObjectVendor:({}).vendorDocument??null};
fs.writeFileSync(path.join(O,'v3-prototype-observation.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result,null,2));