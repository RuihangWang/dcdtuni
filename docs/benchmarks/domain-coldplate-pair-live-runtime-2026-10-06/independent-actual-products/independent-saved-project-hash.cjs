const fs=require('fs'),crypto=require('crypto');
function sort(x){if(Array.isArray(x))return x.map(sort);if(x&&typeof x==='object'){const o=Object.create(null);for(const k of Object.keys(x).sort())Object.defineProperty(o,k,{value:sort(x[k]),enumerable:true});return o;}return x}
for(const p of process.argv.slice(2)){const d=JSON.parse(fs.readFileSync(p,'utf8'));console.log(JSON.stringify({path:p,sha256:crypto.createHash('sha256').update(JSON.stringify(sort(d))).digest('hex')}));}
