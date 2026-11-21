import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeshPreviewPanel } from '../src/components/MeshPreviewPanel'
import { resetMeshPreviewSession } from '../src/run/meshPreviewSession'
import { PostprocessRightPanel } from '../src/components/PostprocessRightPanel'
import { namesOnScreen } from '../src/geometry/patchListDom'
import { getPpView, setPpPatches } from '../src/postprocess/ppViewState'
import type { MeshMeta, PatchMeta } from '../src/postprocess'

/**
 * A group checkbox acts on what the filter left on screen.
 *
 * Filtering to `acu` and ticking the kind row is how a user turns on just those
 * patches without disturbing the rest — and it only works if the group action
 * skips the rows the filter hid. The legacy guards all three of its group
 * gestures with the same line, `if (row.classList.contains('filter-hidden'))
 * continue`: the kind checkbox (STL_generate.html:21029), the per-model one
 * (:21080) and the mesh preview's 全选/全不选 (`mpToggleAll`, :24565).
 *
 * React ported the filter itself — `applyFilterToDOM` writes the class and
 * `getVisibleOrderedNames` reads it back for shift-ranges and viewport picking
 * — but every group toggle passed the group's FULL name list straight through,
 * so a filtered list was a lie the moment you used it: 300 patches quietly
 * turned off by a checkbox that was showing four.
 */

describe('namesOnScreen', () => {
  function list(html: string): HTMLElement {
    const el = document.createElement('div')
    el.innerHTML = html
    return el
  }

  it('drops the names whose row the filter hid', () => {
    const el = list(`
      <div data-patch="a"></div>
      <div data-patch="b" class="filter-hidden"></div>
      <div data-patch="c"></div>`)
    expect(namesOnScreen(el, ['a', 'b', 'c'])).toEqual(['a', 'c'])
  })

  it('keeps a name whose row is not in the DOM at all', () => {
    // A COLLAPSED group renders no rows in React — the legacy only sets
    // `display:none`, so its checkboxes are still queryable and this case does
    // not arise there. Excluding an absent row would make ticking a folded
    // group do nothing at all, which is the worse failure of the two.
    const el = list('<div data-patch="a"></div>')
    expect(namesOnScreen(el, ['a', 'folded'])).toEqual(['a', 'folded'])
  })

  it('passes everything through when there is no list yet', () => {
    expect(namesOnScreen(null, ['a', 'b'])).toEqual(['a', 'b'])
  })
})

/** Three patches, one triangle and one edge each. */
function fixture(names: string[]): { meta: MeshMeta; buffer: ArrayBuffer } {
  const count = names.length
  const patches = names.map((name) => ({
    name,
    nPoints: 3,
    nTriangles: 1,
    nEdges: 1,
    defaultVisible: true,
  })) as PatchMeta[]
  const positions = new Float32Array(count * 9)
  const indices = new Uint32Array(count * 3)
  const edges = new Uint32Array(count * 2)
  for (let i = 0; i < count; i++) {
    const base = i * 3
    positions.set([0, i, 0, 1, i, 0, 0, i, 1], base * 3)
    indices.set([base, base + 1, base + 2], i * 3)
    edges.set([base, base + 1], i * 2)
  }
  const buffer = new ArrayBuffer(positions.byteLength + indices.byteLength + edges.byteLength)
  let off = 0
  new Float32Array(buffer, off, positions.length).set(positions)
  off += positions.byteLength
  new Uint32Array(buffer, off, indices.length).set(indices)
  off += indices.byteLength
  new Uint32Array(buffer, off, edges.length).set(edges)
  return { meta: { patches, totalPoints: count * 3, totalTriangles: count } as MeshMeta, buffer }
}

const NAMES = ['rack_1_inlet', 'acu_1_supply', 'rack_2_inlet']

/** Type into the shared filter bar the way its `input` listener sees it. */
function filter(text: string) {
  const input = document.querySelector<HTMLInputElement>('.filter-input')
  expect(input).not.toBeNull()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(input, text)
  flushSync(() => input!.dispatchEvent(new Event('input', { bubbles: true })))
}

function checkbox(selector: string): HTMLInputElement {
  const el = document.querySelector<HTMLInputElement>(selector)
  expect(el, selector).not.toBeNull()
  return el!
}

/**
 * Click a checkbox.
 *
 * A `change` event does NOT reach React here: for checkboxes and radios React
 * derives `onChange` from the CLICK event instead (`shouldUseClickEvent`), so
 * dispatching `change` runs no handler at all — the first version of this file
 * did that and every case failed for that reason rather than the one under
 * test. jsdom's activation behaviour flips `checked` before dispatching, which
 * is why nothing is assigned here.
 */
function click(el: HTMLElement) {
  flushSync(() => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

function rowChecked(name: string, cls: string): boolean {
  return checkbox(`input.${cls}[data-patch="${name}"]`).checked
}

describe('the mesh preview list, with a filter active', () => {
  let host: HTMLDivElement
  let root: Root

  beforeEach(async () => {
    const { meta, buffer } = fixture(NAMES)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.includes('binary')
          ? { ok: true, arrayBuffer: async () => buffer }
          : { ok: true, json: async () => meta },
      ),
    )
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    flushSync(() =>
      root.render(<MeshPreviewPanel />),
    )
    click(document.getElementById('mp-load-btn')!)
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0))
    flushSync(() => {})
    // The list starts folded (every kind and model closed); these cases are
    // about the rows, so open everything — kind heads first, then the model
    // heads they reveal.
    for (let pass = 0; pass < 2; pass++) {
      document.querySelectorAll<HTMLElement>('.pp-caret').forEach((c) => {
        if (c.textContent === '▶') click(c)
      })
    }
  })

  afterEach(() => {
    resetMeshPreviewSession()
    flushSync(() => root.unmount())
    host.remove()
    vi.unstubAllGlobals()
  })

  it('unticks the group and leaves the filtered-out patches alone', () => {
    filter('acu')
    click(checkbox('input.patch-group-cb'))
    expect(rowChecked('acu_1_supply', 'mp-cb')).toBe(false)
    expect(rowChecked('rack_1_inlet', 'mp-cb')).toBe(true)
    expect(rowChecked('rack_2_inlet', 'mp-cb')).toBe(true)
  })

  it('全不选 is the same gesture and follows the same rule', () => {
    filter('acu')
    click(document.getElementById('mp-all-off')!)
    expect(rowChecked('acu_1_supply', 'mp-cb')).toBe(false)
    expect(rowChecked('rack_1_inlet', 'mp-cb')).toBe(true)
  })

  it('shows the state of what is ON SCREEN, so one click still turns it off', () => {
    // Base604 starts with 22 of its 4336 patches hidden, so the kind checkbox
    // renders unchecked. Filter to `acu` and the box is still unchecked — it
    // was deriving from all 4304 names — so the click means "turn on", the 48
    // on screen are already on, and NOTHING happens however often it is
    // pressed. Measured in the browser: delta 0 on every click.
    click(checkbox('input.mp-cb[data-patch="rack_1_inlet"]'))
    expect(rowChecked('rack_1_inlet', 'mp-cb')).toBe(false)
    filter('acu')
    const group = checkbox('input.patch-group-cb')
    expect(group.checked).toBe(true)
    click(group)
    expect(rowChecked('acu_1_supply', 'mp-cb')).toBe(false)
    // And the hidden-by-filter ones are still where they were.
    expect(rowChecked('rack_2_inlet', 'mp-cb')).toBe(true)
    expect(rowChecked('rack_1_inlet', 'mp-cb')).toBe(false)
  })

  it('with the filter cleared, a group toggle still means the whole group', () => {
    filter('acu')
    filter('')
    click(checkbox('input.patch-group-cb'))
    for (const n of NAMES) expect(rowChecked(n, 'mp-cb')).toBe(false)
  })
})

describe('the post-processing tree, with a filter active', () => {
  let host: HTMLDivElement
  let root: Root

  beforeEach(() => {

    // The session is module-level and outlives the panel: a load left pending
    // by the previous test would hand this one a disabled 加载网格 button.
    resetMeshPreviewSession()
    setPpPatches(fixture(NAMES).meta.patches)
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    flushSync(() => root.render(<PostprocessRightPanel project={null} />))
  })

  afterEach(() => {
    flushSync(() => root.unmount())
    host.remove()
    setPpPatches([])
  })

  it('hides only the filtered-in patches when the group is unticked', () => {
    filter('acu')
    click(checkbox('input.patch-group-cb'))
    expect(getPpView().hidden).toEqual(new Set(['acu_1_supply']))
  })

  it('全不选 follows the filter as well', () => {
    filter('rack')
    click(document.getElementById('pp-deselect-all')!)
    expect(getPpView().hidden).toEqual(new Set(['rack_1_inlet', 'rack_2_inlet']))
  })

  it('starts every group folded without changing default patch visibility', () => {
    const patches = fixture([...NAMES, 'wall_north']).meta.patches
    patches[0]!.defaultVisible = false
    flushSync(() => setPpPatches(patches, '/case/Run-1'))
    const bodies = document.querySelectorAll<HTMLElement>('.pp-kind-body, .pp-model-body')
    expect(bodies.length).toBe(2)
    bodies.forEach((body) => expect(body.style.display).toBe('none'))
    expect(getPpView().hidden).toEqual(new Set(['rack_1_inlet']))

    click(document.querySelector<HTMLElement>('.pp-kind-head .pp-caret')!)
    expect(document.querySelector<HTMLElement>('.pp-kind-body')!.style.display).toBe('')
    expect(document.querySelector<HTMLElement>('.pp-model-body')!.style.display).toBe('none')
    expect(getPpView().hidden).toEqual(new Set(['rack_1_inlet']))
  })

  it('preserves expansion through filtering and same-run reloads, and folds a different run', () => {
    const patches = fixture([...NAMES, 'wall_north']).meta.patches
    flushSync(() => setPpPatches(patches, '/case/Run-1'))
    click(document.querySelector<HTMLElement>('.pp-kind-head .pp-caret')!)
    click(document.querySelector<HTMLElement>('.pp-model-head .pp-caret')!)
    filter('acu')
    flushSync(() => setPpPatches([...patches], '/case/Run-1'))
    document.querySelectorAll<HTMLElement>('.pp-kind-body, .pp-model-body').forEach((body) => {
      expect(body.style.display).toBe('')
    })
    expect(document.querySelector<HTMLInputElement>('.filter-input')!.value).toBe('acu')

    flushSync(() => setPpPatches([...patches], '/case/Run-2'))
    document.querySelectorAll<HTMLElement>('.pp-kind-body, .pp-model-body').forEach((body) => {
      expect(body.style.display).toBe('none')
    })
  })

  it('reveals a newly selected patch and lets the user fold that selection again', () => {
    flushSync(() => setPpPatches(fixture([...NAMES, 'wall_north']).meta.patches, '/case/Run-1'))
    click(document.querySelector<HTMLElement>('div[data-patch="acu_1_supply"]')!)
    document.querySelectorAll<HTMLElement>('.pp-kind-body, .pp-model-body').forEach((body) => {
      expect(body.style.display).toBe('')
    })
    click(document.querySelector<HTMLElement>('.pp-kind-head .pp-caret')!)
    expect(document.querySelector<HTMLElement>('.pp-kind-body')!.style.display).toBe('none')
    expect(document.querySelector('div[data-patch="acu_1_supply"]')!.classList.contains('row-selected')).toBe(true)
  })
})
