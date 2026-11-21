// Build the two headless entries of the editor as minified browser bundles (no source map, no comments).
// Same esbuild settings as src/frontend/react/src/headless/headless_compiler.mjs, plus minification.
//   node prebuild.mjs <react dir of the exported revision> <node_modules with esbuild> <out dir>
import { createRequire } from 'node:module'
import path from 'node:path'
import fs from 'node:fs'

const [reactDir, modules, out] = process.argv.slice(2).map(p => path.resolve(p))
const require = createRequire(path.join(modules, '..', 'package.json'))
const esbuild = require('esbuild')
const repo = path.resolve(reactDir, '../../..')

// UI text about the display bridge and AI inference is not needed by the export paths: drop those strings
// from the bundled translation tables, and the free-text "about" note from the material library.
const UI_ONLY = /scano|omniverse|\bov\b|\bovpp\b|\bkit\b|\bIndeX\b|\bTTA\b/i
function prune(value, key = '') {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const out = {}
    for (const [k, v] of Object.entries(value)) {
      if (UI_ONLY.test(k) || (typeof v === 'string' && UI_ONLY.test(v))) continue
      out[k] = prune(v, k)
    }
    return out
  }
  return value
}
const jsonFilter = {
  name: 'export-json-filter',
  setup(build) {
    build.onLoad({ filter: /[\\/]shared[\\/](i18n[\\/][^\\/]+|textures[\\/]materials)\.json$/ }, args => {
      const data = JSON.parse(fs.readFileSync(args.path, 'utf8'))
      if (args.path.includes('materials.json')) delete data.about
      return { contents: JSON.stringify(args.path.includes('i18n') ? prune(data) : data), loader: 'json' }
    })
  },
}

for (const [name, entry] of [['compiler.min.js', 'src/headless/projectCompiler.ts'],
                             ['builder.min.js', 'src/headless/projectBuilder.ts']]) {
  const built = await esbuild.build({
    absWorkingDir: repo,
    entryPoints: [path.join(reactDir, entry)],
    bundle: true,
    write: false,
    platform: 'browser',
    format: 'iife',
    globalName: '__dcdtuniHeadless',
    target: 'es2022',
    nodePaths: [modules],
    minify: true,
    legalComments: 'none',
    sourcemap: false,
    logLevel: 'silent',
    define: {
      'import.meta.env': JSON.stringify({ DEV: false, PROD: true, MODE: 'production', SSR: false }),
      'import.meta.url': JSON.stringify('http://localhost/headless/projectCompiler.js'),
      __DCDTUNI_OV_RETURN_DIAGNOSTICS__: 'false',
    },
    loader: { '.json': 'json' },
    plugins: [jsonFilter, {
      name: 'vite-raw-imports',
      setup(build) {
        build.onResolve({ filter: /\?raw$/ }, args => ({
          path: path.resolve(args.resolveDir, args.path.slice(0, -4)), namespace: 'raw-source' }))
        build.onLoad({ filter: /.*/, namespace: 'raw-source' }, args => ({
          contents: fs.readFileSync(args.path, 'utf8'), loader: 'text' }))
      },
    }],
  })
  fs.writeFileSync(path.join(out, name), built.outputFiles[0].text)
}
