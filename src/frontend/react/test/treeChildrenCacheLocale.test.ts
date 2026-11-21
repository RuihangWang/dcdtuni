import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { createModel } from '../src/legacy/factories'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { cachedModelChildren } from '../src/interaction/treeChildrenCache'
import type { TreeChild } from '../src/interaction/treeNodes'
import { getLocale, setLocale, t } from '../src/i18n'
import { modelChildren } from '../src/interaction/treeNodes'

/**
 * The tree's cached rows follow a live language switch.
 *
 * The rack side rows and the server face rows are translated now ("进风面
 * (Inlet)" / "Inlet") — they used to be fixed English. The per-model cache was
 * keyed on (model, revision, wall regions), and a language switch bumps none of
 * those, so after zh -> en the rows still read "进风面 (Inlet)" until the project
 * was reloaded (measured in the browser on Tri_angle and Base_leakage).
 */

function rack(): Construction {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  return createModel(project, 'rack').model as Construction
}

function labels(children: readonly TreeChild[]): string[] {
  const out: string[] = []
  const walk = (list: readonly TreeChild[]) => {
    for (const c of list) {
      out.push(c.label)
      if (c.children) walk(c.children)
    }
  }
  walk(children)
  return out
}

const initial = getLocale()
afterEach(() => setLocale(initial))

describe('cachedModelChildren and the locale', () => {
  it('rebuilds the rows when the language changes, even at the same revision', () => {
    const model = rack()
    setLocale('zh-CN')
    const zh = labels(cachedModelChildren(model, [], 7))
    expect(zh.some((l) => l.includes('进风面'))).toBe(true)

    setLocale('en-US')
    const en = labels(cachedModelChildren(model, [], 7))
    expect(en.some((l) => l.includes('Inlet'))).toBe(true)
    expect(en.some((l) => l.includes('进风面'))).toBe(false)
  })

  it('still answers from the cache when nothing moved', () => {
    const model = rack()
    setLocale('en-US')
    const a = cachedModelChildren(model, [], 3)
    const b = cachedModelChildren(model, [], 3)
    expect(b).toBe(a)
  })
})

describe('the rack door rows', () => {
  it('read the door names in the current language, never hard-coded English', () => {
    const model = rack()
    const geometry = model.geometry as { faces?: unknown[] }
    geometry.faces = [
      { uid: 'inletDoor', role: 'door', perforation: { openAreaPct: 64 } },
      { uid: 'outletDoor', role: 'door', perforation: { openAreaPct: 80 } },
    ]
    setLocale('zh-CN')
    const zh = labels(modelChildren(model))
    expect(zh).toContain(`${t('rack.inlet_door')} (64%)`)
    expect(zh).toContain(`${t('rack.outlet_door')} (80%)`)
    expect(t('rack.inlet_door')).toBe('Inlet 门')
    expect(zh.some((l) => l.includes('Inlet Door'))).toBe(false)
  })
})

describe('the feature tree re-lays its rows out on a language switch', () => {
  const tree = readFileSync(join(__dirname, '..', 'src', 'components', 'FeatureTree.tsx'), 'utf8')

  it('lists the locale among the layout memo deps', () => {
    const start = tree.indexOf('const layout = useMemo<TreeLayout>(')
    expect(start).toBeGreaterThan(0)
    const deps = tree.slice(tree.indexOf('}, [', start), tree.indexOf('])', tree.indexOf('}, [', start)) + 2)
    expect(deps).toContain('locale')
  })
})
