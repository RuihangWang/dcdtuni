const fs=require('fs'),path=require('path'),crypto=require('crypto');
const P=path.resolve(__dirname,'..'),E=require(P+'/evidence/gui/canonical-hash-source/js/cfdrun_export.js');const rows=[];
for(const tag of ['2p0','1p0','0p5','0p25']){
 const file=P+'/evidence/gui/saved-project-'+tag+'.cfdproj',data=fs.readFileSync(file),expected=JSON.parse(fs.readFileSync(P+'/evidence/gui/saved-project-'+tag+'-hash-receipt.json','utf8'));
 const actual=E.projectContentHashSync(JSON.parse(data.toString('utf8')));
 if(actual!==expected.actual_runtime_source_project_SHA)throw new Error('actual source mismatch '+tag);
 rows.push({tag,canonical:actual,actual:expected.actual_runtime_source_project_SHA,raw_file_sha:crypto.createHash('sha256').update(data).digest('hex'),match:true});
}console.log(JSON.stringify(rows,null,2));
