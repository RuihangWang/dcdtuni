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

const M=require(path.join(B,'src/frontend/shared/js/cfdproj_migrate.js'));
const red=readJSON('.codex-tmp/domain-object-independent-review-20261006/additional-dictionary-red-observation.json');
const d=M.migrateCfdprojToV4(red.definitionsInput);
function observe(dict, original){
 const ownProto=Object.hasOwn(dict,'__proto__'),plainPrototype=Object.getPrototypeOf(dict)===Object.prototype;
 const serialized=JSON.parse(JSON.stringify(dict));
 assert(ownProto&&plainPrototype&&Object.hasOwn(serialized,'__proto__'),'definition identity lost');
 assert(JSON.stringify(dict.__proto__)===JSON.stringify(original),'definition fields changed');
 return {ownProto,plainPrototype,serializedOwnProto:true,record:dict.__proto__,recordFieldsExact:true};
}
const coldPlateDefinitions=observe(d.models.coolingModels.coldPlates,red.definitionsInput.liquidCooling.specifications.coldPlateSpec[0]);
const fluidDefinitions=observe(d.models.materialModels.fluids,red.definitionsInput.liquidCooling.fluid);
const p=M.migrateCfdprojToV4(red.physicsInput),servers=p.constructions.racks.r.constructions.servers;
const expected=red.physicsInput.construction[0].cfd.servers[0];
assert(servers.__proto__.cooling.volumeFlowRate===expected.volumeFlowRate,'matching authored physics lost');
assert(JSON.stringify(servers.__proto__.cooling.server_p)===JSON.stringify(expected.server_p),'engineering extension not retained on authored record');
assert(Object.keys(servers.server_p.cooling).length===0,'phantom inherited physics applied');
const physics={matchingUIDCooling:servers.__proto__.cooling,unmappedUIDCooling:servers.server_p.cooling,authoredPhysicsRetained:true,authoredExtensionRetained:true,noInheritedPhysicsAssignment:true};
const rel='src/frontend/shared/js/cfdproj_migrate.js';
const result={schema:'domain-additional-dictionary-targeted-green/v1',scope:'only the three same-module red inputs previously approved by ROOT; pure JSON, no physics solve',source:{path:rel,sha256:sha(fs.readFileSync(path.join(B,rel)))},redObservationSHA:sha(fs.readFileSync(path.join(O,'additional-dictionary-red-observation.json'))),coldPlateDefinitions,fluidDefinitions,physics,pass:true};
fs.writeFileSync(path.join(O,'final-frozen-additional-dictionary-green.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result,null,2));
