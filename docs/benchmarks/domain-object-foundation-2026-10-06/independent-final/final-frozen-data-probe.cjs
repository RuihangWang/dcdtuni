const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const B = '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration';
const O = path.join(B, '.codex-tmp/domain-object-independent-review-20261006');
const ts = require(path.join(B, 'src/frontend/react/node_modules/typescript'));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const moduleCache = new Map();
function load(rel) {
  const full = path.join(B, rel);
  if (moduleCache.has(full)) return moduleCache.get(full).exports;
  const source = fs.readFileSync(full, 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  const mod = { exports: {} }; moduleCache.set(full, mod);
  const localRequire = spec => {
    if (!spec.startsWith('.')) throw new Error('Unexpected runtime dependency: ' + spec);
    const resolved = path.relative(B, path.resolve(path.dirname(full), spec)) + '.ts';
    if (!resolved.startsWith('src/frontend/react/src/domain/')) throw new Error('Non-domain dependency: ' + resolved);
    return load(resolved);
  };
  const wrapper = vm.runInThisContext('(function(require,module,exports){\n' + compiled.outputText + '\n})', {filename: full});
  wrapper(localRequire, mod, mod.exports); return mod.exports;
}
const { DomainProject } = load('src/frontend/react/src/domain/DomainProject.ts');
const codec = load('src/frontend/react/src/domain/projectCodec.ts');
const registry = load('src/frontend/react/src/domain/entityRegistry.ts');
const T = require(path.join(B, 'src/frontend/shared/js/cfdproj_transient.js'));
const S = require(path.join(B, 'src/frontend/shared/js/cfdproj_state_v4.js'));
const H = require(path.join(B, 'src/frontend/shared/js/cfdrun_export.js'));
const readJSON = rel => JSON.parse(fs.readFileSync(path.join(B, rel), 'utf8'));
const assert = (value, message) => { if (!value) throw new Error(message); };
const ptr = parts => '/' + parts.map(s => s.replace(/~/g,'~0').replace(/\//g,'~1')).join('/');
const deref = (doc, p) => p.slice(1).split('/').map(s=>s.replace(/~1/g,'/').replace(/~0/g,'~')).reduce((v,k)=>v[k],doc);
function expectedChildren(doc) {
  const expected = [];
  function many(type, value, parts, parent) {
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) if (item && typeof item === 'object' && !Array.isArray(item)) {
      expected.push({type, path:ptr([...parts,key]), parentPath:parent});
      if (type === 'modelFace') many('surfacePatch', item.patches, [...parts,key,'patches'], ptr([...parts,key]));
    }
  }
  for (const [bucket, records] of Object.entries(doc.constructions ?? {})) {
    if (!Object.hasOwn(registry.CONSTRUCTION_BUCKET_TYPES,bucket) || !records || typeof records !== 'object') continue;
    for (const [key, rec] of Object.entries(records)) {
      if (!rec || typeof rec !== 'object') continue;
      const parts = ['constructions', bucket, key], parent = ptr(parts), nested = rec.constructions ?? {};
      if (bucket === 'racks') {
        many('rackPartition', nested.partitions, [...parts,'constructions','partitions'], parent);
        for (const [sk, server] of Object.entries(nested.servers ?? {})) {
          const sp = [...parts,'constructions','servers',sk], spp = ptr(sp);
          expected.push({type:'rackServer',path:spp,parentPath:parent});
          for (const [field,type] of [['coldPlates','coldPlate'],['chips','chip']]) many(type,server.constructions?.[field],[...sp,'constructions',field],spp);
          many('modelFace',server.faces,[...sp,'faces'],spp);
          many('coldPlatePair',server.liquidCooling?.cpCuPairs,[...sp,'liquidCooling','cpCuPairs'],spp);
        }
      }
      if (bucket === 'cdus') for (const [field,type] of [['heatExchanger','heatExchanger'],['pump','pump'],['valve','valve']]) {
        if (nested[field] && typeof nested[field] === 'object') expected.push({type,path:ptr([...parts,'constructions',field]),parentPath:parent});
      }
      many('modelFace', rec.geometry?.faces, [...parts,'geometry','faces'], parent);
      many('geometryCutout', rec.geometry?.cutouts, [...parts,'geometry','cutouts'], parent);
      many('pipeWaypoint', rec.geometry?.waypoints, [...parts,'geometry','waypoints'], parent);
      many('valve', rec.cooling?.valves, [...parts,'cooling','valves'], parent);
    }
  }
  return expected;
}
const inputPaths = ['case/LiquidSingle/LiquidSingle.cfdproj',
 '.codex-tmp/cases/Shinaide_LiquidMixed_Keyframe_CUA/Shinaide_LiquidMixed_Keyframe.cfdproj',
 '.codex-tmp/domain-object-browser-compatibility-20261006/DomainAsset_Compat_Synthetic.cfdproj',
 '.codex-tmp/domain-object-browser-compatibility-20261006/DomainAsset_Compat_Before.cfdproj'];
const inventories = [];
for (const rel of inputPaths) {
  const bytes = fs.readFileSync(path.join(B, rel));
  const raw = JSON.parse(bytes.toString('utf8'));
  const doc = T.materializeAt(structuredClone(T.migrateCfdprojToV5(raw)), 0);
  const textBefore = JSON.stringify(doc), hashBefore = H.projectContentHashSync(doc);
  const domain = codec.decodeDomainProject(doc), entities = domain.allEntities();
  const rows = entities.map(e => ({type:e.type,class:e.constructor.name,uid:e.uid,path:e.identity.path,source:e.identity.source,parentUid:e.identity.parentUid,parentPath:e.identity.parentPath,supported:e.supported}));
  const indexed = new Map(entities.map(e=>[e.identity.path,e]));
  assert(indexed.size === entities.length, rel + ': duplicate source paths');
  for (const e of entities) {
    assert(deref(doc,e.identity.path) === e.record, rel + ': record identity mismatch '+e.identity.path);
    if (e.identity.parentPath) assert(indexed.get(e.identity.parentPath)?.uid === e.identity.parentUid, rel + ': incorrect parent UID '+e.identity.path);
  }
  const expected = expectedChildren(doc);
  for (const want of expected) {
    const e = indexed.get(want.path);
    assert(e?.type === want.type && e.identity.parentPath === want.parentPath, rel + ': missing/wrong expected child '+JSON.stringify(want));
  }
  const actualUids = domain.structuralForAirExport().map(e=>e.uid), oldUids = S.allModels(doc).map(m=>m.uid);
  assert(JSON.stringify(actualUids) === JSON.stringify(oldUids), rel + ': changed legacy air UID order');
  assert(JSON.stringify(doc) === textBefore, rel + ': enumeration changed source JSON');
  assert(H.projectContentHashSync(doc) === hashBefore, rel + ': enumeration changed producer hash');
  const types = {}; for (const row of rows) types[row.type]=(types[row.type]??0)+1;
  const fileName = 'final-frozen-inventory-' + inventories.length + '.json';
  fs.writeFileSync(path.join(O,fileName),JSON.stringify({source:rel,rows},null,2));
  inventories.push({source:rel,inputBytes:bytes.length,inputSHA:sha(bytes),originalSchema:raw.schemaVersion,liveSchema:doc.schemaVersion,entityCount:entities.length,types,expectedChildPaths:expected.length,parentPathChecks:entities.filter(e=>e.identity.parentPath).length,sourceReferencesExact:true,sourceJSONUnchanged:true,producerHashUnchanged:true,producerContentHash:hashBefore,legacyAirUIDOrderUnchanged:true,legacyAirUIDs:actualUids,inventoryFile:fileName});
}
const seed=()=>({schemaVersion:5,uid:'gates',constructions:{boxes:{b:{uid:'b',meta:{name:'B'},geometry:{}}}}});
const future=()=>({uid:'f',meta:{name:'Future'},geometry:{size:{x:1,y:1,z:1}}});
const gates=[];
for (const variant of ['dict','array','singleton','nested']) {
 const d=seed();
 if (variant==='dict') d.constructions.future={f:future()};
 if (variant==='array') d.constructions.future=[future()];
 if (variant==='singleton') d.constructions.future=future();
 if (variant==='nested') d.constructions.boxes.b.constructions={future:[future()]};
 const encoded=codec.encodeProjectRecord(d,['schemaVersion','uid','constructions']);
 assert(JSON.stringify(encoded)===JSON.stringify(d),'future '+variant+' not retained');
 let refusal=null;try{new DomainProject(d).structuralForAirExport()}catch(e){refusal=e.message}
 assert(refusal?.includes('Unsupported geometric bucket'),'future '+variant+' export admitted');
 gates.push({variant,preserved:true,refusal});
}
const versions=[];
for(const value of [6,4294967301,-1,5.5,'NaN','Infinity']) {
 let refusal=null;try{codec.decodeDomainProject({schemaVersion:value,constructions:{}})}catch(e){refusal=e.message}
 assert(refusal?.includes('Unsupported schemaVersion'),'bad version admitted '+value);versions.push({value,refusal});
}
const protoInput=readJSON('.codex-tmp/domain-object-independent-review-20261006/v3-prototype-input.json');
const v3v5=T.migrateCfdprojToV5(protoInput);
const v3box=v3v5.constructions.boxes.b;
const hasPlainProto=obj=>Object.getPrototypeOf(obj)===Object.prototype;
const prototypeChecks={migratedVersion:v3v5.schemaVersion,rootOwnProto:Object.hasOwn(v3v5,'__proto__'),boxOwnProto:Object.hasOwn(v3box,'__proto__'),rootPlainPrototype:hasPlainProto(v3v5),boxPlainPrototype:hasPlainProto(v3box),rootValue:v3v5.__proto__,boxValue:v3box.__proto__,unrelatedPlainObjectVendor:({}).vendorDocument??null};
// Keep failure evidence rather than invent a green result if the shared fix is pending.
prototypeChecks.pass=prototypeChecks.rootOwnProto && prototypeChecks.boxOwnProto && prototypeChecks.rootPlainPrototype && prototypeChecks.boxPlainPrototype;
const sourcePaths=['src/frontend/react/src/domain/BaseEntity.ts','src/frontend/react/src/domain/entityRegistry.ts','src/frontend/react/src/domain/DomainProject.ts','src/frontend/react/src/domain/projectCodec.ts','src/frontend/react/src/domain/index.ts','src/frontend/react/src/io/projectFiles.ts','src/frontend/react/src/schema/cfdproj.ts','src/frontend/react/src/run/runSubmit.ts','src/frontend/react/test/domainProject.test.ts','src/frontend/react/test/domainCodecRegression.test.ts','src/frontend/shared/js/cfdproj_migrate.js','src/frontend/shared/js/cfdrun_export.js'];
const sources=sourcePaths.map(rel=>({path:rel,bytes:fs.statSync(path.join(B,rel)).size,sha256:sha(fs.readFileSync(path.join(B,rel)))}));
const result={schema:'domain-independent-pure-json-review/v1',scope:'source-bound, CPU 28–31, JSON/registry/migration/hash only; no renderer, mesh, solver, service or browser execution',sources,inventories,gates,versions,prototypeChecks};
fs.writeFileSync(path.join(O,'final-frozen-data-probe.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({inventories:inventories.map(({source,entityCount,types,expectedChildPaths,producerHashUnchanged})=>({source,entityCount,types,expectedChildPaths,producerHashUnchanged})),gates,prototypeChecks},null,2));
assert(prototypeChecks.pass,'Shared historical migration still loses own __proto__ extension fields');
