import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Toolbar, DEFAULT_VIEW_OPTIONS, type ViewOptions } from '../../src/components/Toolbar'

// The view cube is a WebGL canvas of its own and jsdom has no context for it.
// The buttons under test are plain DOM either side of it.
vi.mock('../../src/components/ViewCube', () => ({ ViewCube: () => null }))

/**
 * Two view toggles in one batch must not clobber each other.
 *
 * `Toolbar`'s own `toggle` helper is a functional update for exactly this
 * reason, and says so — `view` is the prop THIS render captured, so a second
 * change made before React re-renders would be built on the stale one. The
 * grid-snap button was the one handler that ignored the rule and spread
 * `{ ...view }` instead, and it undid whatever the click before it had set.
 *
 * Measured in the browser first: three toolbar clicks from one script step left
 * `showGrid` unchanged in the persisted prefs while both snap flags moved —
 * the grid change was overwritten by the snap-grid handler's stale copy. A user
 * cannot click twice inside one task, but the automation contract can, and so
 * can any future caller that changes two options at once.
 */

let host: HTMLDivElement
let root: Root
let view: ViewOptions

function render() {
  flushSync(() =>
    root.render(
      <Toolbar
        view={view}
        onChange={(next) => {
          view = typeof next === 'function' ? next(view) : next
        }}
        hasSelection={false}
        selectedUids={[]}
        project={null}
      />,
    ),
  )
}

const click = (id: string) => {
  const el = document.getElementById(id)
  expect(el, id).not.toBeNull()
  el!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

beforeEach(() => {
  view = { ...DEFAULT_VIEW_OPTIONS }
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the view toggles', () => {
  it('each does its own thing', () => {
    click('grid-toggle-btn')
    expect(view.showGrid).toBe(false)
    render()
    click('snap-grid-btn')
    expect(view.snapGrid).toBe(true)
  })

  it('keeps the grid change when a snap toggle follows in the SAME batch', () => {
    // No re-render between them: both handlers see the render's `view`.
    flushSync(() => {
      click('grid-toggle-btn')
      click('snap-grid-btn')
    })
    expect(view.snapGrid).toBe(true)
    expect(view.showGrid).toBe(false)
  })

  it('still makes grid snap exclusive with face and edge snap', () => {
    // The legacy's rule, and the reason this handler writes three fields at
    // once rather than one.
    click('snap-grid-btn')
    expect(view).toMatchObject({ snapGrid: true, snapFace: false, snapEdge: false })
  })
})
