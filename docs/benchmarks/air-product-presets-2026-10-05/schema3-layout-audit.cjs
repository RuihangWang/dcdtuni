#!/usr/bin/env node
// Audit the actual shared v3 -> v4 -> v5 load chain without launching a product run.
'use strict'
const fs = require('fs'), path = require('path'), cp = require('child_process'), crypto = require('crypto')
const repo = cp.execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd: __dirname, encoding: 'utf8' }).trim()
const originalCommit = 'f950782954429bfa75c22d0c1e85febf40031da5'
const brokenLayoutCommit = 'cecb0893c35303b5ba8de12ce2c6297aa4b1e6c3'
const presets = { meshBaseSize: .25, meshEngine: 'dcCart', meshTimeLimitSeconds: 300,
  parallelMesh: true, meshProcess: 8, weldTolerance: .0012,
  firstLayerMinFraction: .3, firstLayerMax: .005, firstLayerSealGap: -1, rackCellMinSize: .0175 }
const migrate = require(path.join(repo, 'src/frontend/shared/js/cfdproj_transient.js')).migrateCfdprojToV5
const original = rel => cp.execFileSync('git', ['show', originalCommit + ':' + rel], { cwd: repo, maxBuffer: 64 * 1024 * 1024 })
const stable = value => {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
    .map(k => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}'
  return JSON.stringify(value)
}
const clone = value => JSON.parse(JSON.stringify(value))
const sha = value => crypto.createHash('sha256').update(value).digest('hex')
function rawNonPreset(doc) {
  const d = clone(doc)
  for (const key of Object.keys(presets)) delete d.simulation[key]
  return stable(d)
}
function normalizedNonPreset(doc) {
  const d = clone(doc)
  for (const key of Object.keys(presets)) delete d.runDefaults.meshConfig[key]
  return stable(d)
}
const paths = cp.execFileSync('git', ['ls-files', 'docs/manuscript/paper_experiment/**/*.cfdproj'],
  { cwd: repo, encoding: 'utf8' }).trim().split('\n')
if (paths.length !== 16) throw Error('Expected the sixteen current paper inputs')
const rows = paths.map(rel => {
  const beforeBytes = original(rel), afterBytes = fs.readFileSync(path.join(repo, rel))
  const before = JSON.parse(beforeBytes), after = JSON.parse(afterBytes)
  const b = migrate(clone(before)), a = migrate(clone(after))
  const rawEqual = rawNonPreset(before) === rawNonPreset(after)
  const normalizedEqual = normalizedNonPreset(b) === normalizedNonPreset(a)
  const rawPresetOK = Object.entries(presets).every(([k, v]) => after.simulation[k] === v)
  const effectivePresetOK = Object.entries(presets).every(([k, v]) => a.runDefaults.meshConfig[k] === v)
  const solverEqual = stable(b.runDefaults.simulationConfig) === stable(a.runDefaults.simulationConfig)
  const liquidEqual = stable(b.runDefaults.liquid) === stable(a.runDefaults.liquid)
  const locationEqual = stable(b.runDefaults.meshConfig.locationInMesh) === stable(a.runDefaults.meshConfig.locationInMesh)
  const validLayout = after.schemaVersion === 3 && !Object.hasOwn(after, 'runDefaults')
  return { path: rel, originalSha256: sha(beforeBytes), currentSha256: sha(afterBytes),
    originalNonPresetCanonicalSha256: sha(rawNonPreset(before)), currentNonPresetCanonicalSha256: sha(rawNonPreset(after)),
    validLayout, rawPresetOK, effectivePresetOK, rawNonPresetEqual: rawEqual,
    normalizedNonPresetEqual: normalizedEqual, solverEqual, liquidEqual, locationEqual,
    originalMeshKeyCount: Object.keys(b.runDefaults.meshConfig).length,
    currentMeshKeyCount: Object.keys(a.runDefaults.meshConfig).length,
    pass: validLayout && rawPresetOK && effectivePresetOK && rawEqual && normalizedEqual && solverEqual && liquidEqual && locationEqual }
})
const result = { scope: 'Sixteen current schema3 paper inputs; actual shared v3-to-v5 migration, no browser/mesh/solve execution',
  originalCommit, brokenLayoutCommit, presets, count: rows.length, failures: rows.filter(x => !x.pass).length,
  verifierSha256: sha(fs.readFileSync(__filename)),
  migrationSourceSha256: sha(fs.readFileSync(path.join(repo, 'src/frontend/shared/js/cfdproj_migrate.js'))),
  transientSourceSha256: sha(fs.readFileSync(path.join(repo, 'src/frontend/shared/js/cfdproj_transient.js'))),
  rows }
process.stdout.write(JSON.stringify(result, null, 2) + '\n')
process.exitCode = result.failures ? 1 : 0
