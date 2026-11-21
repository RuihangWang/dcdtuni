const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const B='/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration',O=path.join(B,'.codex-tmp/domain-object-independent-review-20261006');
const rel='src/frontend/shared/js/cfdproj_migrate.js',M=require(path.join(B,rel));
const seed=(construction,extra={})=>({schemaVersion:3,meta:{projectName:'uid edge case'},construction,simulation:{},...extra});
const equip=(uid,cpUid)=>({id:uid,type:'liquidServer',name:'Server',startUnit:1,spanUnits:1,liquidCooling:{cpCuPairs:[{id:cpUid,name:'Pair',Q_W:100}]}});
const rack=(uid,e)=>({uid,kind:'rack',name:'Rack',rack:{coolingType:'liquid',equipments:[e],partitions:[]}});
const cases=[['modelUID',seed([{uid:'__proto__',kind:'box',name:'Box'}]),d=>d.constructions.boxes],
 ['serverUID',seed([rack('r',equip('__proto__','cp'))]),d=>d.constructions.racks.r.constructions.servers],
 ['coldPlateUID',seed([rack('r',equip('s','__proto__'))]),d=>d.constructions.racks.r.constructions.servers.s.constructions.coldPlates],
 ['probeUID',seed([],{probeData:[{id:'__proto__',name:'P',x:0,y:0,z:0}]}),d=>d.instrumentation.probes],
 ['measurementUID',seed([],{sensorData:[{id:'__proto__',name:'M'}]}),d=>d.instrumentation.measurements]];
const results=[];
for(const [name,input,pick] of cases){
 const migrated=M.migrateCfdprojToV4(input),dict=pick(migrated);
 results.push({name,input,ownProto:Object.hasOwn(dict,'__proto__'),ownKeys:Object.keys(dict),dictionaryPrototypeUID:Object.getPrototypeOf(dict)?.uid??null,dictionaryPrototypeID:Object.getPrototypeOf(dict)?.id??null,serializationRetainsEntity:Object.hasOwn(JSON.parse(JSON.stringify(dict)),'__proto__')});
}
const unknown=[];
for(const kind of ['futureSolid','toString']){
 const input=seed([{uid:'future',kind,name:'Future'}]);let migrated=null,error=null;
 try{migrated=M.migrateCfdprojToV4(input)}catch(e){error=e.message}
 unknown.push({kind,input,error,boxMeta:migrated?.constructions?.boxes?.future?.meta??null});
}
const result={schema:'domain-uid-dictionary-source-probe/v1',scope:'bounded pure JSON migration; no renderer/solver/global prototype mutation',source:{path:rel,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(B,rel))).digest('hex')},results,unknown};
fs.writeFileSync(path.join(O,'uid-dictionary-red-observation.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({source:result.source,results:results.map(({name,ownProto,ownKeys,dictionaryPrototypeUID,dictionaryPrototypeID,serializationRetainsEntity})=>({name,ownProto,ownKeys,dictionaryPrototypeUID,dictionaryPrototypeID,serializationRetainsEntity})),unknown},null,2));