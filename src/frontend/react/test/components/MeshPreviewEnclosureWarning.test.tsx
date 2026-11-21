import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeshPreviewPanel } from '../../src/components/MeshPreviewPanel'
import { resetMeshPreviewSession } from '../../src/run/meshPreviewSession'
import * as store from '../../src/projectStore'
import { reviveKinds } from '../../src/legacy/cfdprojModules'
import { t } from '../../src/i18n'
import type { CfdProject } from '../../src/schema/cfdproj'
import type { MeshMeta, PatchMeta } from '../../src/postprocess'

/**
 * A leaked mesh in the mesh preview.
 *
 * When the room shell is open the fluid reaches the mesh box: dcCart files the
 * outer faces as domain_* and the room walls, floor and ceiling get no patch.
 * Filing domain_* under WALL must not make that look like a normal mesh, so the
 * panel names what is missing (the legacy's #mp-enclosure-warning).
 */

/** One triangle and one edge per named patch — the binary the panel decodes. */
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
  const meta = {
    patches,
    totalPoints: count * 3,
    totalTriangles: count,
    totalEdges: count,
    nCells: 1000,
    nBoundaryFaces: count,
    nBoundarySum: count,
  } as MeshMeta
  return { meta, buffer }
}

const CASE_DIR = resolve(process.cwd(), '../../../case')
const DOMAIN = ['domain_xMin', 'domain_xMax', 'domain_yMin', 'domain_yMax', 'domain_zMin', 'domain_zMax']

describe('the missing-enclosure warning in the mesh preview', () => {
  let host: HTMLDivElement
  let root: Root

  beforeEach(() => {

    // The session is module-level and outlives the panel: a load left pending
    // by the previous test would hand this one a disabled 加载网格 button.
    resetMeshPreviewSession()
    store.loadProject(
      reviveKinds(
        JSON.parse(
          readFileSync(resolve(CASE_DIR, 'DAML-Testbed/DMAL_Testbed-patched.cfdproj'), 'utf8'),
        ) as CfdProject,
      ),
      null,
    )
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    flushSync(() =>
      root.render(<MeshPreviewPanel />),
    )
  })

  afterEach(() => {
    resetMeshPreviewSession()
    flushSync(() => root.unmount())
    host.remove()
    vi.unstubAllGlobals()
  })

  async function loadNames(names: string[]) {
    const { meta, buffer } = fixture(names)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.includes('binary')
          ? { ok: true, arrayBuffer: async () => buffer }
          : { ok: true, json: async () => meta },
      ),
    )
    const btn = document.getElementById('mp-load-btn') as HTMLElement
    flushSync(() => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    flushSync(() => {})
  }

  it('names the room patches a leaked mesh lost, above the tree and outside the rows', async () => {
    await loadNames(['R1_front', 'Floor', ...DOMAIN])
    const warning = document.getElementById('mp-enclosure-warning')
    expect(warning).not.toBeNull()
    // No data-patch: the filter and the selection glue walk [data-patch].
    expect(warning!.hasAttribute('data-patch')).toBe(false)
    const missing = warning!.dataset.missing!.split(',')
    expect([...missing].sort()).toEqual(['Ceiling', 'Wall_1', 'Wall_2', 'Wall_3', 'Wall_4'])
    expect(warning!.textContent).toBe(t('mp.enclosure_missing', { names: missing.join(', ') }))

    const kids = [...document.getElementById('mp-patch-list')!.children]
    const at = kids.indexOf(warning!)
    expect(at).toBeGreaterThan(kids.findIndex((el) => el.classList.contains('patch-filter-host')))
    expect(at).toBeLessThan(kids.findIndex((el) => el.classList.contains('pp-kind')))
  })

  it('shows the domain sides as WALL, under the translated group label', async () => {
    await loadNames(['Floor', ...DOMAIN])
    const heads = [...document.querySelectorAll<HTMLElement>('.pp-kind-head')]
    const wall = heads.find((h) => (h.textContent ?? '').includes('WALL'))
    expect(wall?.textContent?.replace(/\s+/g, ' ')).toContain('WALL (6)')
    expect(heads.some((h) => (h.textContent ?? '').includes('OTHER'))).toBe(false)

    const caret = wall!.querySelector('.pp-caret') as HTMLElement
    flushSync(() => caret.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const cb = document.querySelector<HTMLInputElement>('.patch-model-cb[data-model="Domain boundary"]')
    expect(cb).not.toBeNull()
    expect(cb!.parentElement!.textContent).toContain(t('mp.domain_boundary'))
  })

  it('says nothing for a closed mesh', async () => {
    await loadNames(['Wall_1', 'Wall_2', 'Wall_3', 'Wall_4', 'Floor', 'Ceiling_patch_1', 'R1_front'])
    expect(document.querySelector('.pp-kind')).not.toBeNull()
    expect(document.getElementById('mp-enclosure-warning')).toBeNull()
  })
})
