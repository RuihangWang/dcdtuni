'use strict'
const fs=require('fs'),path=require('path'),crypto=require('crypto'),cp=require('child_process');
const root='/mnt/lv/home/litianyu/mycode/dcdtuni', out=__dirname;
const d=path.join(root,'docs/benchmarks/air-product-presets-2026-10-05');
const current=JSON.parse(fs.readFileSync(path.join(d,'current-input-preset-manifest.json'))).current;
const generated=JSON.parse(fs.readFileSync(path.join(d,'generated-input-preset-migration.json'))).files;
const migrate=require(path.join(root,'src/frontend/shared/js/cfdproj_transient.js')).migrateCfdprojToV5;
const presets={meshBaseSize:.25,meshEngine:'dcCart',meshTimeLimitSeconds:300,parallelMesh:true,meshProcess:8,weldTolerance:.0012,firstLayerMinFraction:.3,firstLayerMax:.005,firstLayerSealGap:-1,rackCellMinSize:.0175};
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const rows=[];
for(const [group,list] of [['trackedCurrent',current],['generatedCurrent',generated]]){
 for(const item of list){
  const file=path.join(root,item.path),raw=fs.readFileSync(file), doc=JSON.parse(raw);
  const location=(doc.schemaVersion||2)<3?'cfdRoom':doc.schemaVersion===3?'simulation':'runDefaults.meshConfig';
  const cfg=location==='cfdRoom'?doc.cfdRoom:location==='simulation'?doc.simulation:doc.runDefaults?.meshConfig;
  const norm=migrate(JSON.parse(raw)),effective=norm?.runDefaults?.meshConfig;
  const rawMismatch=Object.entries(presets).filter(([k,v])=>cfg?.[k]!==v).map(([key,expected])=>({key,expected,actual:cfg?.[key]??null}));
  const effectiveMismatch=Object.entries(presets).filter(([k,v])=>effective?.[k]!==v).map(([key,expected])=>({key,expected,actual:effective?.[key]??null}));
  rows.push({group,path:item.path,bytes:raw.length,sha256:sha(raw),schemaVersion:doc.schemaVersion||2,location,rawMismatch,effectiveMismatch,
   ...(group==='generatedCurrent'?{migrationAfterHashMatches:sha(raw)===item.afterSha256}:{})});
 }
}
const counts={};
for(const group of ['trackedCurrent','generatedCurrent']){const xs=rows.filter(x=>x.group===group);counts[group]={count:xs.length,bytes:xs.reduce((n,x)=>n+x.bytes,0),rawPass:xs.filter(x=>!x.rawMismatch.length).length,effectivePass:xs.filter(x=>!x.effectiveMismatch.length).length};}
const report={scope:'Read-only current main inputs, actual shared migration; no product/browser/mesh/solve execution',mainHEAD:cp.execFileSync('git',['rev-parse','HEAD'],{cwd:root,env:{...process.env,GIT_OPTIONAL_LOCKS:'0'},encoding:'utf8'}).trim(),presets,counts,generatedHashMatches:rows.filter(x=>x.group==='generatedCurrent'&&x.migrationAfterHashMatches).length,failures:rows.filter(x=>x.rawMismatch.length||x.effectiveMismatch.length||x.migrationAfterHashMatches===false),sources:[...['cfdproj_migrate.js','cfdproj_transient.js'].map(n=>{const p=path.join(root,'src/frontend/shared/js',n);return{path:p,sha256:sha(fs.readFileSync(p))}})],rows};
fs.writeFileSync(path.join(out,'effective-preset-audit.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({...report,rows:undefined,sources:report.sources},null,2));
