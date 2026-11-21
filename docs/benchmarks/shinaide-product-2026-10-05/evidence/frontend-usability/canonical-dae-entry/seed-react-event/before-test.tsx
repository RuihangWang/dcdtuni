import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { reviveKinds } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { collectSeedSurfaces } from '../../src/geometry/seedConfig'
import { SeedInspector } from '../../src/components/SeedInspector'

/**
 * The panel is hosted by `PatchInspector`, which another agent owns this batch,
 * so it is rendered here directly. Without this the component could ship
 * type-correct and still throw on its first render — and the first person to
 * find out would be whoever wires it in.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

/**
 * The mesh sizes these numbers are worked out for: base 0.17 m, baffle level
 * [1, 1] -> a 0.085 m cell at the surface. Pinned here, not read from the
 * case file, so a sizing change to case/Base_leakage (the 6Sigma preset,
 * 7813a336) does not rewrite what the seed arithmetic is checked against.
 */
function pinMeshSizing(project: CfdProject): CfdProject {
  const rd = ((project as { runDefaults?: Record<string, unknown> }).runDefaults ??= {})
  const mc = ((rd as { meshConfig?: Record<string, unknown> }).meshConfig ??= {})
  mc.meshBaseSize = 0.17
  mc.wallBafflesRefinementLevel = [1, 1]
  return project
}

function load(): CfdProject {
  return pinMeshSizing(reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject))
}

let host: HTMLDivElement
let root: Root

function render(node: React.ReactNode) {
  flushSync(() => root.render(node))
}

beforeEach(() => {
  store.loadProject(load(), '/cases/Base_leakage.cfdproj')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

function raisedFloor(): Construction {
  return store.getProject()!.constructions!.floors!['m_51'] as Construction
}

describe('SeedInspector', () => {
  it('renders the anchor, the three direction boxes and the offset for a perforated tile', () => {
    render(
      <SeedInspector
        project={store.getProject()}
        model={raisedFloor()}
        bucket="floors"
        faceId="top"
        patchId="p_13"
      />,
    )
    expect(host.querySelector('#seed-inspector')).toBeTruthy()
    // The patch centre, in metres: the project's input unit is m.
    expect(host.querySelector('#seed-anchor')?.textContent).toBe('2.201, 1.000, 3.000')
    expect((host.querySelector('#seed-direction-x') as HTMLInputElement).value).toBe('0.000')
    expect((host.querySelector('#seed-direction-y') as HTMLInputElement).value).toBe('1.000')
    expect((host.querySelector('#seed-direction-z') as HTMLInputElement).value).toBe('0.000')
    expect((host.querySelector('#seed-offset') as HTMLInputElement).value).toBe('0.0425')
  })

  it('shows nothing at all for a surface with no seed', () => {
    // Room_Floor is a plain `wall`: single-sided, so there is no pair to place.
    render(
      <SeedInspector
        project={store.getProject()}
        model={store.getProject()!.constructions!.floors!['m_49'] as Construction}
        bucket="floors"
      />,
    )
    expect(host.querySelector('#seed-inspector')).toBeNull()
    expect(host.innerHTML).toBe('')
  })

  it('falls back to the model kind when no bucket is passed', () => {
    render(<SeedInspector project={store.getProject()} model={raisedFloor()} faceId="top" patchId="p_13" />)
    expect(host.querySelector('#seed-inspector')).toBeTruthy()
  })

  it('writes the offset into the project when the box is committed', () => {
    render(
      <SeedInspector
        project={store.getProject()}
        model={raisedFloor()}
        bucket="floors"
        faceId="top"
        patchId="p_13"
      />,
    )
    const input = host.querySelector('#seed-offset') as HTMLInputElement
    flushSync(() => {
      input.value = '0.2'
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    const openings = (raisedFloor().geometry as { openings: { seedConfig?: { offset: number } }[] })
      .openings
    expect(openings[0]?.seedConfig?.offset).toBeCloseTo(0.2, 10)
  })

  it('flips the status badge once the surface carries an override', () => {
    const props = {
      project: store.getProject(),
      model: raisedFloor(),
      bucket: 'floors',
      faceId: 'top',
      patchId: 'p_13',
    }
    render(<SeedInspector {...props} />)
    const before = host.querySelector('#seed-status')?.textContent
    const surface = collectSeedSurfaces(store.getProject()).find((s) => s.owner === 'floor-opening')!
    flushSync(() => {
      const input = host.querySelector('#seed-offset') as HTMLInputElement
      input.value = '0.2'
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    render(<SeedInspector {...props} model={raisedFloor()} />)
    expect(surface.isCustom).toBe(false)
    expect(host.querySelector('#seed-status')?.textContent).not.toBe(before)
  })

  it('toggles the seed-helper label without touching the project', () => {
    render(
      <SeedInspector
        project={store.getProject()}
        model={raisedFloor()}
        bucket="floors"
        faceId="top"
        patchId="p_13"
      />,
    )
    const btn = host.querySelector('#seed-helper-toggle') as HTMLButtonElement
    const before = btn.textContent
    const dirty = store.isDirty()
    flushSync(() => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect((host.querySelector('#seed-helper-toggle') as HTMLButtonElement).textContent).not.toBe(before)
    expect(store.isDirty()).toBe(dirty)
    // Left as it was found: the flag is module state shared with every other
    // instance of this panel, and a test that leaves it off would flip the next
    // one's expectations.
    flushSync(() =>
      (host.querySelector('#seed-helper-toggle') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      ),
    )
  })

  it('clears the override from the reset button', () => {
    const props = {
      project: store.getProject(),
      model: raisedFloor(),
      bucket: 'floors',
      faceId: 'top',
      patchId: 'p_13',
    }
    render(<SeedInspector {...props} />)
    flushSync(() => {
      const input = host.querySelector('#seed-offset') as HTMLInputElement
      input.value = '0.2'
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    render(<SeedInspector {...props} model={raisedFloor()} />)
    flushSync(() =>
      (host.querySelector('#seed-reset-default') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      ),
    )
    const openings = (raisedFloor().geometry as { openings: Record<string, unknown>[] }).openings
    expect(openings[0]).not.toHaveProperty('seedConfig')
  })
})
