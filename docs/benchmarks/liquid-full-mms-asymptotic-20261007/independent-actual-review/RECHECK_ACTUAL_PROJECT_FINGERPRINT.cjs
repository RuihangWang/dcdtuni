"use strict";
const fs=require('node:fs'), crypto=require('node:crypto'), assert=require('node:assert/strict');
const B='/mnt/lv/home/litianyu/mycode/dcdtuni',A=B+'/.codex-tmp/goal-20261005/air',F=A+'/.codex-tmp/full-mms-asymptotic-continuation-prepared-v4-shared-20261007',O=A+'/.codex-tmp/full-mms-asymptotic-runtime-v4-20261007-exclusive';
const {independentProjectFingerprint:hash}=require(F+'/independent_project_fingerprint.cjs');
let files=[];function read(p){const b=fs.readFileSync(p); files.push({path:p,bytes:b.length,sha256:crypto.createHash('sha256').update(b).digest('hex')});return JSON.parse(b)}
const registry=read(F+'/after/src/frontend/shared/transient/transient_params.json');
const rows=[];
for(const tag of ['0p03125','0p015625']){
 const G=O+'/grid-'+tag,p=read(G+'/inputs/manufactured-dt-'+tag+'.cfdproj'),r=read(G+'/results/manufactured-dt-'+tag+'.liquidrun'),old=read(G+'/results/original-anchor-0p0625.liquidrun');
 const before=JSON.stringify(p),h=hash(p,registry);assert.equal(h,r.source_project.sha256);
 const anchor=JSON.parse(before);anchor.runDefaults.liquid.experiment.dt_s=old.time.dt_s;anchor.runDefaults.liquid.experiment.dae.manufactured_benchmark=old.manufactured_benchmark;anchor.transient.gridS=old.time.dt_s;assert.equal(hash(anchor,registry),old.source_project.sha256);
 const grid=JSON.parse(before);grid.transient.gridS*=2;assert.notEqual(hash(grid,registry),h);
 const initial=JSON.parse(before);initial.runDefaults.liquid.experiment.dae.manufactured_benchmark.initial_state.node_pressure_Pa.values[0]+=1;assert.notEqual(hash(initial,registry),h);
 const unknown=JSON.parse(before);unknown.transient.tracks[0].param='unreviewed_param';assert.throws(()=>hash(unknown,registry),/unresolved/);
 assert.equal(JSON.stringify(p),before);
 rows.push({tag,actualSavedProjectSHA:h,actualTypedProducerSHA:r.source_project.sha256,reconstructedOldAnchorSHA:hash(anchor,registry),unknownTracksRefused:true,gridAndInitialMutationHashChanges:true,primaryInputNotMutated:true});
}
for(const row of files){const b=fs.readFileSync(row.path);assert.equal(b.length,row.bytes);assert.equal(crypto.createHash('sha256').update(b).digest('hex'),row.sha256)}
console.log(JSON.stringify({scope:'read-only standard Node crypto tool helper; no product imports/React/compiler/generation/Newton/reference solve',rows,files},null,2));
