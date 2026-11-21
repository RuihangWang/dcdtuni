/** Receipt harness, off by default: compile every liquid case in the repo and
 * write each manifest to $COLDPLATE_DUMP_DIR, so a change to how cold plates
 * reach the graph can be diffed before/after. Three entry paths per file:
 *   editor   — checkProjectDocument → store.loadProject → compileSavedExperiment
 *   raw      — compileSavedExperiment on the parsed JSON (what most tests do)
 *   research — tools/compile_cfdproj_liquidrun_v3_entry (saved DAE files only)
 * A throw is recorded as {error}, never skipped.
 */
import { lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, it } from 'vitest'
import { checkProjectDocument } from '../src/io/projectFiles'
import { compileSavedExperiment } from '../src/liquid/liquidExperiment'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import { compileProjectDocument } from '../../../../tools/compile_cfdproj_liquidrun_v3_entry'

const OUT = process.env.COLDPLATE_DUMP_DIR
const CASES = resolve(process.cwd(), '../../../case')

function liquidCases(dir = CASES): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    const stat = lstatSync(path)
    if (stat.isDirectory()) {
      if (name !== 'log') out.push(...liquidCases(path))
    } else if (stat.isFile() && name.endsWith('.cfdproj')) {
      // A server with pairs or canonical plates on either side makes it a liquid case.
      const doc = JSON.parse(readFileSync(path, 'utf8'))
      const servers = Object.values(doc.constructions?.racks ?? {})
        .flatMap((r: any) => Object.values(r.constructions?.servers ?? {})) as any[]
      if (servers.some(s => s.liquidCooling?.cpCuPairs?.length || Object.keys(s.constructions?.coldPlates ?? {}).length)) out.push(path)
    }
  }
  return out.sort()
}

function attempt(fn: () => unknown): unknown {
  try { return fn() } catch (e) { return { error: String((e as Error)?.message ?? e) } }
}

describe.runIf(!!OUT)('cold-plate manifest dump', () => {
  it('compiles every liquid case through the editor, raw and research paths', () => {
    mkdirSync(OUT!, { recursive: true })
    const index: Record<string, string[]> = {}
    for (const file of liquidCases()) {
      const rel = relative(CASES, file)
      const tag = rel.replace(/[\\/]/g, '__').replace(/\.cfdproj$/, '')
      const text = readFileSync(file, 'utf8')
      const editor = attempt(() => {
        const checked = checkProjectDocument(JSON.parse(text))
        if (!checked.ok) throw new Error(checked.error)
        store.loadProject(checked.project, null)
        return compileSavedExperiment(store.getProject()!).liquidrun
      })
      const raw = attempt(() => compileSavedExperiment(JSON.parse(text) as CfdProject).liquidrun)
      const research = attempt(() => compileProjectDocument(JSON.parse(text)))
      for (const [path, value] of [['editor', editor], ['raw', raw], ['research', research]] as const) {
        writeFileSync(join(OUT!, `${tag}.${path}.json`), JSON.stringify(value, null, 1))
      }
      index[rel] = ['editor', 'raw', 'research']
    }
    writeFileSync(join(OUT!, 'index.json'), JSON.stringify(index, null, 1))
  })
})
