import { createRequire } from 'node:module';
import test from 'node:test';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const projectSchema = require('../shared/js/cfdproj_schema.js');
const runSchema = require('../shared/js/cfdrun_schema.js');
function errors(value, project) {
  const doc = project ? projectSchema.emptyDocument() : runSchema.emptyManifest();
  if (project) { doc.uid = 'pressure-test'; doc.runDefaults.simulationConfig.pressureLinearSolver = value; }
  else doc.simulationConfig.pressureLinearSolver = value;
  return project ? projectSchema.validateDocument(doc) : runSchema.validateManifest(doc);
}
for (const project of [true, false]) {
  test((project ? 'cfdproj' : 'cfdrun') + ' accepts absent/null/default/member-null and explicit legal pressure options', () => {
    for (const value of [undefined, null, {}, {preconditioner:null,tolerance:null,relTol:null,maxIter:null},
      {preconditioner:'DIC'}, {preconditioner:'GAMG',tolerance:1e-8,relTol:0,maxIter:1},
      {relTol:.999,tolerance:Number.MIN_VALUE,maxIter:1000}]) assert.deepEqual(errors(value,project),[]);
  });
  test((project ? 'cfdproj' : 'cfdrun') + ' rejects coercion, unsupported keys/methods and invalid numeric options', () => {
    for (const value of [[],true,'DIC',{preconditioner:'PCG'},{preconditioner:1},{tolerance:'1e-8'},
      {tolerance:0},{tolerance:-1},{tolerance:Infinity},{tolerance:NaN},{tolerance:true},
      {relTol:1},{relTol:-.001},{relTol:'0'},{relTol:false},{relTol:Infinity},
      {maxIter:0},{maxIter:1.1},{maxIter:'100'},{maxIter:true},{maxIter:NaN},{smoother:'Jacobi'}])
      assert.ok(errors(value,project).length, JSON.stringify(value));
  });
}
test('pressureLinearSolver belongs to the simulation key split', () => {
  assert.ok(projectSchema.SIM_KEYS.includes('pressureLinearSolver'));
  assert.ok(!projectSchema.MESH_KEYS.includes('pressureLinearSolver'));
});
