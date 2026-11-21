#!/usr/bin/env node
/**
 * Headless .cfdproj compiler (no browser, no WebGL, no network, no server).
 *
 * Reads one JSON request on stdin and writes one JSON reply on stdout:
 *   {"operation": "air-case",  "project": {...}, "projectPath": "/abs/x.cfdproj" | null, "timestamp": "ISO-8601",
 *    "options": {...}}
 *   {"operation": "liquidrun", "project": {...}, "projectPath": ..., "options": {...}}
 *   {"operation": "hybrid-project", "project": {...}, "projectPath": ..., "options": {...}}
 *   {"operation": "build-project", "project": {...} | null, "projectPath": ..., "timestamp": "ISO-8601",
 *    "edits": [{"verb": "...", "args": [...]}, ...]}
 *   {"operation": "contract-verbs"}   -> {"verbs": [{name, type}], "buildVerbs": [...]}: Object.keys(window.__dcdtuni)
 * `options` (optional) is ProjectOptions in projectCompiler.ts: solverType, liquidMode, durationS and,
 * for air-case, monitoring (the solve monitoring panel's ticks).
 *
 * It bundles one entry with esbuild as a browser IIFE -- ./projectCompiler.ts
 * for the exports, ./projectBuilder.ts (the automation contract) for
 * build-project -- and evaluates it INSIDE a JSDOM window, so the shared UMD
 * format modules see `window` and no `module` -- exactly as under Vite. The
 * reply also lists every bundled source file with its SHA-256, so a caller can
 * record which frontend code produced the bytes.
 *
 * No network. For build-project `fetch` in the window always rejects: the
 * contract's run modules install an auth guard and a case-path sync that try
 * the server in the background, and here they fail quietly, as they do when the
 * server is down.
 *
 * Module resolution: esbuild/jsdom/three come from DCDTUNI_HEADLESS_NODE_MODULES
 * when set, else src/frontend/react/node_modules.
 */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..')
const reactDir = path.join(repo, 'src/frontend/react')
const modules = process.env.DCDTUNI_HEADLESS_NODE_MODULES
  ? path.resolve(process.env.DCDTUNI_HEADLESS_NODE_MODULES)
  : path.join(reactDir, 'node_modules')
const require = createRequire(path.join(modules, '..', 'package.json'))
const { JSDOM } = require('jsdom')
const esbuild = require('esbuild')
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex')
const ENTRIES = {
  'air-case': 'src/headless/projectCompiler.ts',
  liquidrun: 'src/headless/projectCompiler.ts',
  'hybrid-project': 'src/headless/projectCompiler.ts',
  'build-project': 'src/headless/projectBuilder.ts',
  'contract-verbs': 'src/headless/projectBuilder.ts',
}

// Diagnostics go to stderr; stdout carries exactly one JSON document.
const say = (...args) => process.stderr.write('[headless-compiler] ' + args.map(String).join(' ') + '\n')
// Every console channel (including JSDOM's virtual console, console.time/timeEnd
// and table/dir) is diverted to stderr so nothing but the reply reaches stdout.
const stderrConsole = new console.Console({ stdout: process.stderr, stderr: process.stderr })
for (const name of Object.keys(console)) {
  if (typeof stderrConsole[name] === 'function') console[name] = stderrConsole[name].bind(stderrConsole)
}

async function bundle(entry) {
  const built = await esbuild.build({
    absWorkingDir: repo,
    entryPoints: [entry],
    bundle: true,
    write: false,
    platform: 'browser',
    format: 'iife',
    globalName: '__dcdtuniHeadless',
    target: 'es2022',
    nodePaths: [modules],
    metafile: true,
    logLevel: 'silent',
    // Production build values for the Vite compile-time constants the app reads.
    define: {
      'import.meta.env': JSON.stringify({ DEV: false, PROD: true, MODE: 'production', SSR: false }),
      'import.meta.url': JSON.stringify('http://localhost/headless/projectCompiler.js'),
      __DCDTUNI_OV_RETURN_DIAGNOSTICS__: 'false',
    },
    loader: { '.json': 'json' },
    plugins: [{
      name: 'vite-raw-imports',
      setup(build) {
        build.onResolve({ filter: /\?raw$/ }, args => ({
          path: path.resolve(args.resolveDir, args.path.slice(0, -4)), namespace: 'raw-source' }))
        build.onLoad({ filter: /.*/, namespace: 'raw-source' }, args => ({
          contents: fs.readFileSync(args.path, 'utf8'), loader: 'text' }))
      },
    }],
  })
  const sources = {}
  for (let name of Object.keys(built.metafile.inputs)) {
    if (name.startsWith('raw-source:')) name = name.slice('raw-source:'.length)
    if (name.startsWith('<')) continue  // esbuild virtual inputs such as <define:...>
    const absolute = path.resolve(repo, name)
    if (absolute.startsWith(repo + path.sep) && !absolute.includes(`${path.sep}node_modules${path.sep}`) && fs.existsSync(absolute)) {
      sources[path.relative(repo, absolute)] = sha256(fs.readFileSync(absolute))
    }
  }
  sources[path.relative(repo, fileURLToPath(import.meta.url))] = sha256(fs.readFileSync(fileURLToPath(import.meta.url)))
  return { code: built.outputFiles[0].text, sources }
}

/** Installed versions of the packages that shape the compiled bytes (null when absent). */
function packageVersions(names) {
  const versions = {}
  for (const name of names) {
    try {
      versions[name] = JSON.parse(fs.readFileSync(path.join(modules, name, 'package.json'), 'utf8')).version
    } catch {
      versions[name] = null
    }
  }
  return versions
}

// Fire-and-forget promises of the loaded modules (the contract's auth check and
// case-path sync, which find no server) must not end the process: Node treats an
// unhandled rejection as fatal. They are counted and logged; everything the
// requested operation does is awaited and fails through main().
let backgroundRejections = 0
process.on('unhandledRejection', reason => {
  backgroundRejections += 1
  say('background promise rejected (not part of the operation):', reason && reason.message ? reason.message : String(reason))
})

async function main() {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'))
  if (!request || typeof request !== 'object') throw new Error('Expected one JSON request object on stdin')
  const { operation, project } = request
  const projectPath = typeof request.projectPath === 'string' ? request.projectPath : null
  if (!Object.hasOwn(ENTRIES, operation)) throw new Error(`Unknown operation: ${operation}`)
  if ((operation === 'air-case' || operation === 'build-project') && typeof request.timestamp !== 'string') {
    throw new Error(`${operation} needs an explicit ISO-8601 timestamp`)
  }
  const started = process.hrtime.bigint()
  const { code, sources } = await bundle(path.join(reactDir, ENTRIES[operation]))
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true, runScripts: 'outside-only' })
  const win = dom.window
  // The editor reports export refusals through alert(); here they are errors.
  win.alert = message => { throw new Error(String(message)) }
  for (const name of ['log', 'info', 'warn', 'error', 'debug']) {
    win.console[name] = (...args) => say(args.map(String).join(' '))
  }
  // Browser globals JSDOM does not provide but every target browser (and the
  // vitest jsdom environment the app's tests run in) does. Only fill gaps.
  const gaps = { structuredClone, TextEncoder, TextDecoder, queueMicrotask, performance,
    crypto: globalThis.crypto, AbortController, AbortSignal, URL, URLSearchParams,
    setImmediate, clearImmediate }
  for (const [name, value] of Object.entries(gaps)) {
    if (value !== undefined && (win[name] === undefined || (name === 'crypto' && !win.crypto?.subtle))) {
      Object.defineProperty(win, name, { configurable: true, writable: true, value })
    }
  }
  // The contract's run modules wrap `window.fetch` when they load. Give the build
  // entry one that fails the way a request fails when the backend is down; the
  // export entry keeps exactly the window it always had (JSDOM has no fetch).
  if ((operation === 'build-project' || operation === 'contract-verbs') && win.fetch === undefined) {
    const offline = async () => { throw new TypeError('headless compiler: no network (no server is reachable here)') }
    Object.defineProperty(win, 'fetch', { configurable: true, writable: true, value: offline })
  }
  try {
    win.eval(code + '\n;window.__dcdtuniHeadless = __dcdtuniHeadless;')
    const api = win.__dcdtuniHeadless
    if (!api) throw new Error('Headless bundle did not expose its API')
    let result
    const options = request.options || {}
    if (operation === 'air-case') result = await api.exportAirCase(project, projectPath, request.timestamp, options)
    else if (operation === 'liquidrun') result = api.exportLiquidrun(project, projectPath, options)
    else if (operation === 'hybrid-project') result = api.hybridProject(project, projectPath, options)
    else if (operation === 'contract-verbs') result = api.contractVerbs()
    else result = await api.buildProject(request.edits, project ?? null, projectPath, request.timestamp)
    const reply = JSON.parse(JSON.stringify(result))
    reply.implementation = {
      format: 'dcdtuni.headless-compiler/v1',
      node: process.version,
      nodeModules: modules,
      packages: packageVersions(['esbuild', 'jsdom', 'three', 'three-mesh-bvh', 'ajv']),
      elapsedSeconds: Number(process.hrtime.bigint() - started) / 1e9,
      entry: ENTRIES[operation],
      backgroundRejections,
      sources,
    }
    process.stdout.write(JSON.stringify(reply))
  } finally {
    win.close()
  }
}

main().catch(error => {
  say(error && error.stack ? error.stack : String(error))
  process.stdout.write(JSON.stringify({ error: String(error && error.message ? error.message : error) }))
  process.exitCode = 1
})
