#!/usr/bin/env node
/** Compile one saved engineering project into the React exporter's strict DAE V3 input. */
import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')

function reactDependencies() {
  // A managed integration worktree may be nested inside a normal checkout and
  // reuse its installed npm packages. A standalone checkout uses its own.
  for (let dir = repo; ; dir = path.dirname(dir)) {
    const modules = path.join(dir, 'src/frontend/react/node_modules')
    if (fs.existsSync(path.join(modules, 'jsdom')) &&
        fs.existsSync(path.join(modules, 'esbuild'))) return modules
    if (path.dirname(dir) === dir) break
  }
  throw new Error('React npm dependencies are missing; install them in src/frontend/react')
}

function options(args) {
  const result = { check: false, project: '', output: '' }
  for (let i = 0; i < args.length; i++) {
    const flag = args[i]
    if (flag === '--check') { result.check = true; continue }
    if ((flag !== '--project' && flag !== '--output') || !args[i + 1] || args[i + 1].startsWith('--')) {
      throw new Error('Usage: node tools/compile_cfdproj_liquidrun_v3.mjs --project SOURCE.cfdproj --output RESULT.liquidrun [--check]')
    }
    const key = flag.slice(2)
    if (result[key]) throw new Error(`Duplicate ${flag}`)
    result[key] = args[++i]
  }
  if (!result.project || !result.output) throw new Error('Both --project and --output are required')
  result.project = path.resolve(result.project)
  result.output = path.resolve(result.output)
  if (path.extname(result.project) !== '.cfdproj' || path.extname(result.output) !== '.liquidrun') {
    throw new Error('Expected a .cfdproj source and .liquidrun output')
  }
  if (result.project === result.output) throw new Error('Source and output paths must differ')
  return result
}

function installBrowserCompilerGlobals(window) {
  globalThis.window = window
  globalThis.document = window.document
  for (const key of ['THREE', 'cfdprojStateV4', 'geometryPure', 'polygonClipping', 'rackState', 'state']) {
    Object.defineProperty(globalThis, key, { configurable: true,
      get: () => window[key], set: value => { window[key] = value } })
  }
  globalThis.alert = message => { throw new Error(String(message)) }
  window.alert = globalThis.alert
}

async function main() {
  const args = options(process.argv.slice(2))
  const sourceBytes = fs.readFileSync(args.project)
  const sourceFileHash = sha256(sourceBytes)
  const modules = reactDependencies()
  const require = createRequire(path.join(path.dirname(modules), 'package.json'))
  let raw
  try { raw = JSON.parse(sourceBytes.toString('utf8')) }
  catch (error) { throw new Error(`Invalid source JSON: ${error.message}`) }
  const { JSDOM } = require('jsdom')
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' })
  installBrowserCompilerGlobals(dom.window)
  const tempRoot = path.join(repo, '.codex-tmp')
  fs.mkdirSync(tempRoot, { recursive: true })
  const temp = fs.mkdtempSync(path.join(tempRoot, 'liquidrun-v3-compiler-'))
  try {
    // Some shared compiler modules require `three` dynamically. The bundle
    // lives in a disposable directory, so give it the React package's exact
    // dependencies without copying or installing another runtime.
    fs.symlinkSync(modules, path.join(temp, 'node_modules'), 'dir')
    const bundle = path.join(temp, 'compiler.cjs')
    await require('esbuild').build({
      entryPoints: [path.join(repo, 'tools/compile_cfdproj_liquidrun_v3_entry.ts')],
      bundle: true, platform: 'node', format: 'cjs', outfile: bundle,
      nodePaths: [modules], logLevel: 'silent',
      plugins: [{ name: 'vite-raw-source', setup(build) {
        build.onResolve({ filter: /\?raw$/ }, args => ({
          path: path.resolve(args.resolveDir, args.path.slice(0, -4)), namespace: 'raw-source',
        }))
        build.onLoad({ filter: /.*/, namespace: 'raw-source' }, args => ({
          contents: fs.readFileSync(args.path, 'utf8'), loader: 'text',
        }))
      } }],
    })
    const { compileProjectDocument } = require(bundle)
    const manifest = compileProjectDocument(raw)
    if (!fs.readFileSync(args.project).equals(sourceBytes)) {
      throw new Error('Source .cfdproj changed during compilation')
    }
    const outputBytes = Buffer.from(JSON.stringify(manifest, null, 2) + '\n')
    const outputHash = sha256(outputBytes)
    if (args.check) {
      if (!fs.existsSync(args.output) || !fs.readFileSync(args.output).equals(outputBytes)) {
        throw new Error('Existing .liquidrun differs from deterministic compiler output')
      }
    } else {
      fs.mkdirSync(path.dirname(args.output), { recursive: true })
      fs.writeFileSync(args.output, outputBytes, { flag: 'wx' })
    }
    console.log(JSON.stringify({
      source: args.project, source_file_sha256: sourceFileHash,
      project_content_sha256: manifest.source_project.sha256,
      output: args.output, output_sha256: outputHash,
      schema_version: manifest.schemaVersion, project_uid: manifest.source_project.project_uid,
      nodes: manifest.nodes.length, edges: manifest.edges.length,
      checked: args.check,
    }))
  } finally {
    dom.window.close()
    fs.rmSync(temp, { recursive: true, force: true })
  }
}

try { await main() }
catch (error) { console.error('liquidrun-v3 compiler:', error); process.exitCode = 1 }
