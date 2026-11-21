import { beforeEach, describe, expect, it } from 'vitest'
import { FROZEN_CLASS, GUARD_CLASS, LIVE_CLASS, RUN_BLOCK_CLASS, setUiFrozen } from '../src/run/uiFreeze'

/**
 * The freeze is only worth anything if the abort button survives it. A frozen
 * editor with no way out of a 40-minute mesh would be a worse bug than the one
 * this fixes, so the exception list is what these tests pin down.
 */

function layout() {
  document.head.innerHTML = ''
  document.body.className = ''
  document.body.innerHTML = `
    <div id="left-panel">
      <div class="file-bar"><button id="load-btn"></button></div>
      <div id="left-tab-bar"><button id="tab-btn-mesh"></button></div>
      <div id="tree-container"><button id="node-btn"></button></div>
      <div class="content" id="mesh-settings-panel">
        <input id="ms-base-size" />
        <button id="mesh-abort-btn"></button>
      </div>
      <div id="left-geo-export"><button id="btn-export-stl"></button></div>
    </div>
    <div id="toolbar"><button id="tb-move"></button></div>
    <div id="right-panel"><input id="prop-x" /></div>
  `
}

beforeEach(() => {
  setUiFrozen(false)
  layout()
})

describe('setUiFrozen', () => {
  it('marks the body and installs the stylesheet once', () => {
    setUiFrozen(true)
    expect(document.body.classList.contains(FROZEN_CLASS)).toBe(true)
    const style = document.getElementById('ui-freeze-style')
    expect(style?.textContent).toContain(`body.${FROZEN_CLASS} #right-panel`)

    setUiFrozen(false)
    setUiFrozen(true)
    expect(document.querySelectorAll('#ui-freeze-style')).toHaveLength(1)
  })

  it('keeps the abort buttons clickable through the frozen regions', () => {
    setUiFrozen(true)
    const rules = document.getElementById('ui-freeze-style')?.textContent ?? ''
    // pointer-events is the one property a descendant can turn back on after an
    // ancestor switched it off, which is why the freeze uses it.
    expect(rules).toContain(`body.${FROZEN_CLASS} #mesh-abort-btn`)
    expect(rules).toContain(`body.${FROZEN_CLASS} #solve-abort-btn`)
    expect(rules).toContain('pointer-events: auto')
  })

  it('dims the settings and the run button, never the scrolling panel itself', () => {
    // The panel is the tab's scroll container. `pointer-events: none` on it
    // hands the wheel to the element behind, so the frozen panel could not be
    // scrolled; the freeze therefore targets one level down.
    setUiFrozen(true)
    const rules = document.getElementById('ui-freeze-style')?.textContent ?? ''
    expect(rules).not.toMatch(/#(mesh|solver)-settings-panel/)
    expect(rules).toContain(`body.${FROZEN_CLASS} .${GUARD_CLASS} > *`)
    expect(rules).toContain(`body.${FROZEN_CLASS} .${RUN_BLOCK_CLASS} > :not(.${LIVE_CLASS})`)
    // The live buttons are SIBLINGS of the dimmed elements, so opacity 1 here
    // is real — a descendant could not undo an ancestor's opacity.
    expect(rules).toMatch(new RegExp(`body\\.${FROZEN_CLASS} \\.${LIVE_CLASS}[^{]*\\{[^}]*opacity: 1`))
  })

  it('makes the tree, toolbar, tabs, file bar and inspector inert', () => {
    setUiFrozen(true)
    for (const sel of ['#tree-container', '#toolbar', '#left-tab-bar', '.file-bar', '#right-panel']) {
      expect(document.querySelector(sel)?.hasAttribute('inert')).toBe(true)
    }
  })

  it('never makes the run panel inert — inert cannot be lifted on a descendant', () => {
    setUiFrozen(true)
    expect(document.getElementById('mesh-settings-panel')?.hasAttribute('inert')).toBe(false)
  })

  it('thaws everything it froze', () => {
    setUiFrozen(true)
    setUiFrozen(false)
    expect(document.body.classList.contains(FROZEN_CLASS)).toBe(false)
    expect(document.querySelector('#tree-container')?.hasAttribute('inert')).toBe(false)
    expect(document.querySelector('#toolbar')?.hasAttribute('inert')).toBe(false)
  })
})

describe('regions that mount during a job', () => {
  it('marks a container React added AFTER the freeze inert too', async () => {
    // The pointer half is a stylesheet and covers late arrivals for free;
    // `inert` is an attribute on the nodes that existed at freeze time. React
    // re-renders per streamed log line, so without the observer a panel that
    // remounted mid-run stays keyboard-reachable while looking frozen.
    setUiFrozen(true)
    const late = document.createElement('div')
    late.id = 'toolbar'
    document.body.appendChild(late)
    await new Promise((r) => setTimeout(r, 0))
    expect(late.hasAttribute('inert')).toBe(true)
    setUiFrozen(false)
    expect(late.hasAttribute('inert')).toBe(false)
    late.remove()
  })
})
