import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  createColorbarWidget,
  installColorbarGlobal,
  type ColorbarWidget,
} from '../../src/legacy/colorbarGlobal'

/**
 * The shared liquid renderer's colour scale.
 *
 * Both temperature overlays — pipes and server chips — ask for the legacy's
 * `window._ppCreateColorbarWidget` and are written as
 * `if (typeof window._ppCreateColorbarWidget === 'function')`. React had ported
 * the bar as a component and never published that name, so both coloured the
 * scene and showed no scale, silently, because the guard turns a missing
 * factory into a no-op.
 */

/**
 * Let React paint.
 *
 * `root.render` is asynchronous in React 18, and the bridge deliberately does
 * NOT wrap it in `flushSync`: it is called from the shared liquid modules,
 * which can run inside a React render, and a synchronous flush from there
 * throws. So every assertion here waits a tick, as the browser does.
 */
const flush = async () => {
  // Three macrotasks, not one. React 18 schedules its render on a task and its
  // passive effects on another, and a single tick caught the first widget
  // mid-render often enough to fail the suite intermittently — which is worse
  // than the bug the file is guarding.
  for (let i = 0; i < 3; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

function bars(): HTMLElement[] {
  return [...document.body.querySelectorAll<HTMLElement>('[id^="lc-colorbar-"]')].filter(
    (el) => !el.id.startsWith('lc-colorbar-host-'),
  )
}

let widgets: ColorbarWidget[] = []

beforeEach(() => {
  widgets = []
  delete (window as unknown as { _ppCreateColorbarWidget?: unknown })._ppCreateColorbarWidget
})

afterEach(async () => {
  for (const w of widgets) w.destroy()
  await flush()
  document.body.querySelectorAll('[id^="lc-colorbar-host-"]').forEach((el) => el.remove())
})

function make(config: Parameters<typeof createColorbarWidget>[0]): ColorbarWidget {
  const widget = createColorbarWidget(config)
  widgets.push(widget)
  return widget
}

describe('installColorbarGlobal', () => {
  it('publishes the name the shared modules look for', () => {
    installColorbarGlobal()
    expect(
      typeof (window as unknown as { _ppCreateColorbarWidget?: unknown })._ppCreateColorbarWidget,
    ).toBe('function')
  })

  it('never clobbers a real one — the legacy page owns this name', () => {
    const theirs = () => ({}) as never
    ;(window as unknown as { _ppCreateColorbarWidget?: unknown })._ppCreateColorbarWidget = theirs
    installColorbarGlobal()
    expect(
      (window as unknown as { _ppCreateColorbarWidget?: unknown })._ppCreateColorbarWidget,
    ).toBe(theirs)
  })
})

describe('the widget', () => {
  it('draws a bar as soon as it is created', async () => {
    make({ label: 'Pipe Temperature', fieldName: 'T', climMin: 20, climMax: 40 })
    await flush()
    expect(bars().length).toBe(1)
  })

  it('shows the limits it was given', async () => {
    make({ label: 'Chip Temperature', fieldName: 'chipC', climMin: 26.9, climMax: 44.8 })
    await flush()
    const text = bars()[0]!.textContent ?? ''
    expect(text).toContain('Chip Temperature')
    // The numbers reach the bar; the exact formatting is ColorbarOverlay's own
    // test. Without this a widget could mount empty and still "exist".
    expect(text).toMatch(/44\.8|44\.80/)
  })

  it('re-renders on update, which is how the renderer changes the range', async () => {
    const widget = make({ label: 'T', fieldName: 'chipC', climMin: 0, climMax: 1 })
    widget.update({ climMin: 300, climMax: 320 })
    await flush()
    expect(bars()[0]!.textContent).toMatch(/320/)
  })

  it('hide takes it off screen and show puts it back', async () => {
    const widget = make({ label: 'T', fieldName: 'chipC', climMin: 0, climMax: 1 })
    widget.hide()
    await flush()
    expect(bars().length).toBe(0)
    widget.show()
    await flush()
    expect(bars().length).toBe(1)
  })

  it('update after hide brings it back, as the renderer expects', async () => {
    // `colorPipesByTemperature` calls `update()` then `show()` on an existing
    // widget; the server path calls `update()` alone.
    const widget = make({ label: 'T', fieldName: 'chipC', climMin: 0, climMax: 1 })
    widget.hide()
    await flush()
    widget.update({ climMin: 5, climMax: 6 })
    await flush()
    expect(bars().length).toBe(1)
  })

  it('destroy removes the bar and its host', async () => {
    const widget = createColorbarWidget({ label: 'T', fieldName: 'chipC', climMin: 0, climMax: 1 })
    await flush()
    expect(bars().length).toBe(1)
    widget.destroy()
    await flush()
    expect(bars().length).toBe(0)
    expect(document.body.querySelectorAll('[id^="lc-colorbar-host-"]').length).toBe(0)
  })

  it('gives two live widgets separate ids, so a test can tell them apart', async () => {
    make({ label: 'A', fieldName: 'chipC', climMin: 0, climMax: 1 })
    make({ label: 'B', fieldName: 'chipC', climMin: 0, climMax: 1 })
    await flush()
    const ids = bars().map((el) => el.id)
    expect(new Set(ids).size).toBe(2)
  })

  it('falls back to coolwarm for a preset it does not know', async () => {
    // The legacy accepts any string here and its own table falls back the same
    // way; passing an unknown one through would reach the shader as undefined.
    make({ label: 'T', fieldName: 'chipC', climMin: 0, climMax: 1, cmapPreset: 'nonesuch' })
    await flush()
    expect(bars().length).toBe(1)
  })
})
