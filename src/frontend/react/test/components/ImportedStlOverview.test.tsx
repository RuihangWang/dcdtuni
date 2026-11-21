import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import * as store from '../../src/projectStore'
import { setLocale, getLocale } from '../../src/i18n'
import { ImportedStlOverview } from '../../src/components/ImportedStlOverview'

/**
 * Rendered directly: `Inspector.tsx` is owned by another agent this batch, so
 * the component is exercised here rather than through the panel that will host
 * it. Without this it could ship type-correct and throw on its first render.
 *
 * The behaviour that matters is the DISABLED size boxes. A `custom` model's
 * size is the bounding box of the triangles the STL brought in, so an editable
 * box claims to resize a mesh nothing re-tessellates.
 */

function project(): CfdProject {
  return {
    schemaVersion: 4,
    name: 'T',
    meta: { inputUnit: 'm' },
    constructions: { customMeshes: {} },
  } as CfdProject
}

function imported(regions: string[]): Construction {
  return {
    uid: 'c1',
    kind: 'custom',
    meta: { name: 'Imported', enabled: true },
    geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 1.25, y: 0.5, z: 2 }, faces: {} },
    customMesh: { solidRegions: regions },
  } as unknown as Construction
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  store.loadProject(project(), null)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

function render(model: Construction) {
  flushSync(() =>
    root.render(<ImportedStlOverview project={store.getProject()} model={model} />),
  )
}

describe('ImportedStlOverview', () => {
  it('shows W/D/H from the model’s own axes and refuses to let them be typed over', () => {
    // W is x, D is z, H is y — the legacy panel's own mapping (:9030-9032).
    render(imported(['solid_a', 'solid_b', 'solid_c']))
    const w = host.querySelector<HTMLInputElement>('#custom-size-w')!
    const d = host.querySelector<HTMLInputElement>('#custom-size-d')!
    const h = host.querySelector<HTMLInputElement>('#custom-size-h')!
    expect([w.value, d.value, h.value]).toEqual(['1.2500', '2.0000', '0.5000'])
    expect([w.disabled, d.disabled, h.disabled]).toEqual([true, true, true])
  })

  it('reports the solid-region count, which is how many patches it exports as', () => {
    render(imported(['a', 'b', 'c']))
    const line = host.querySelector('#custom-solid-regions-count')!.textContent!
    expect(line).toContain('3')
    // The dictionary string carries <b> markup; it must arrive as text.
    expect(line).not.toContain('<b>')
  })

  it('reports zero for a file that brought no named solids', () => {
    render(imported([]))
    expect(host.querySelector('#custom-solid-regions-count')!.textContent).toContain('0')
  })

  it('offers both MODEL-level cutters, the way in when the import has one region', () => {
    render(imported([]))
    expect(host.querySelector('#custom-whole-circle-patch')).toBeTruthy()
    expect(host.querySelector('#custom-whole-rect-patch')).toBeTruthy()
  })

  it('translates every label rather than hard-coding one', () => {
    const before = getLocale()
    try {
      setLocale('zh-CN')
      render(imported(['a']))
      const zh = host.textContent
      setLocale('en-US')
      render(imported(['a']))
      expect(host.textContent).not.toBe(zh)
      expect(host.textContent).toContain('Imported STL Overview')
    } finally {
      setLocale(before)
    }
  })
})
