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

const M = require(path.join(B,'src/frontend/shared/js/cfdproj_migrate.js'));
const red = readJSON('.codex-tmp/domain-object-independent-review-20261006/uid-dictionary-red-observation.json');
const getters = {
 modelUID: d=>d.constructions.boxes,
 serverUID: d=>d.constructions.racks.r.constructions.servers,
 coldPlateUID: d=>d.constructions.racks.r.constructions.servers.s.constructions.coldPlates,
 probeUID: d=>d.instrumentation.probes,
 measurementUID: d=>d.instrumentation.measurements,
};
const results=[];
for (const item of red.results) {
 const migrated=M.migrateCfdprojToV4(item.input), dict=getters[item.name](migrated);
 const ownProto=Object.hasOwn(dict,'__proto__'),plainPrototype=Object.getPrototypeOf(dict)===Object.prototype;
 const serialized=JSON.parse(JSON.stringify(dict));
 const retains=Object.hasOwn(serialized,'__proto__');
 assert(ownProto&&plainPrototype&&retains,item.name+' not preserved');
 const record=dict.__proto__;
 assert((record.uid??record.id)==='__proto__',item.name+' changed source UID');
 results.push({name:item.name,input:item.input,ownProto,plainPrototype,serializationRetainsEntity:retains,recordUID:record.uid??null,recordID:record.id??null});
}
const unknown=[];
for (const item of red.unknown) {
 const migrated=T.migrateCfdprojToV5(item.input), record=migrated.constructions.boxes.future;
 const entity=new DomainProject(migrated).topLevel(['boxes'])[0];
 assert(record.meta.legacyKind===item.kind,'Lost declared kind '+item.kind);
 assert(entity.supported===false,'Admitted unsupported kind '+item.kind);
 assert(entity.kind===item.kind,'Opaque diagnostics changed declared kind '+item.kind);
 let refusal=null;try{new DomainProject(migrated).structuralForAirExport()}catch(e){refusal=e.message}
 assert(refusal?.includes('Unsupported entity'),'Export admitted unsupported kind '+item.kind);
 const saved=codec.encodeProjectRecord(migrated,['schemaVersion','uid','meta','geometry','models','constructions','instrumentation','runDefaults','transient']);
 assert(JSON.parse(JSON.stringify(saved)).constructions.boxes.future.meta.legacyKind===item.kind,'Save dropped unknown kind '+item.kind);
 unknown.push({kind:item.kind,input:item.input,migrationReturns:true,legacyKind:record.meta.legacyKind,entityKind:entity.kind,supported:entity.supported,serializedDeclaredKindPreserved:true,refusal});
}
const sources=['src/frontend/shared/js/cfdproj_migrate.js','src/frontend/react/src/domain/entityRegistry.ts'].map(rel=>({path:rel,sha256:sha(fs.readFileSync(path.join(B,rel)))}));
const result={schema:'domain-uid-dictionary-targeted-green/v1',scope:'only the five previously frozen UID inputs and two unknown-kind inputs; pure JSON, no business execution',sources,redObservationSHA:sha(fs.readFileSync(path.join(O,'uid-dictionary-red-observation.json'))),results,unknown,pass:true};
fs.writeFileSync(path.join(O,'uid-dictionary-green-observation.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result,null,2));
