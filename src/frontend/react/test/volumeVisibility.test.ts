import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Collapsing 配置体渲染 must not delete the volume.
 *
 * There are two checkboxes and they mean different things. `pp-vol-show`
 * (配置体渲染) is a DISCLOSURE — it sets `display` on the settings div, and the
 * legacy's `ppToggleVolumeConfig` (STL_generate.html:22475) does nothing else
 * with it. `pp-vol-visible` (显示体渲染) is the layer's visibility.
 *
 * `applyLayers` handed the volume PAYLOAD through only when `show` was true, so
 * tidying the panel away took the ray-marched field off the screen with
 * 显示体渲染 still ticked and no way to tell why. Measured in the browser
 * before the fix: `visible` true throughout, `show` off → 0 materials carrying
 * `uBrightness`, `show` on → 1.
 *
 * ## Why a source assertion
 *
 * The expression lives inside a `useCallback` in a 2000-line panel whose render
 * needs a WebGL viewport, a decoded mesh and a fetched volume grid. Rendering
 * that here would test the mocks. What can be pinned exactly is the shape of
 * the decision, in both files that make it.
 */

const SRC = join(__dirname, '..', 'src')

const panel = readFileSync(join(SRC, 'components/PostprocessPanel.tsx'), 'utf8')
const layers = readFileSync(join(SRC, 'postprocess/ppLayers.ts'), 'utf8')

/**
 * The `volume: { … }` object `applyLayers` passes to the viewport, CODE ONLY.
 *
 * Comments are stripped because the fix's own comment names the field it
 * removed — and the first version of the assertion below matched that
 * explanation instead of the code, which is a test that can only ever pass by
 * accident.
 */
function volumeBlock(): string {
  const at = panel.indexOf('        volume: {')
  expect(at).toBeGreaterThan(-1)
  return panel
    .slice(at, panel.indexOf('\n        },', at))
    .split('\n')
    .filter((line) => !/^\s*(\/\/|\/\*|\*)/.test(line))
    .join('\n')
}

describe('the volume layer handed to the viewport', () => {
  it('passes a payload only when it belongs to the selected field', () => {
    expect(volumeBlock()).toContain('payload: volumeRef.current?.field === s.volume.field ? volumeRef.current.payload : null')
  })

  it('does not gate the payload on the disclosure checkbox', () => {
    // `s.volume.show` is 配置体渲染. If it ever appears in this block again,
    // collapsing the settings will silently blank the render.
    expect(volumeBlock()).not.toContain('s.volume.show')
  })

  it('still passes the real visibility control', () => {
    expect(volumeBlock()).toContain('visible: s.volume.visible')
  })
})

describe('ppLayers', () => {
  it('is what decides, from the payload AND the visibility', () => {
    // The panel hands over what it has; the builder decides. Two places
    // deciding is how the disclosure got a vote in the first place.
    expect(layers).toContain('if (vol?.payload && vol.visible)')
  })
})

describe('the two checkboxes', () => {
  it('are wired to different fields', () => {
    expect(panel).toMatch(/id="pp-vol-show"[\s\S]{0,120}checked=\{state\.volume\.show\}/)
    expect(panel).toMatch(/id="pp-vol-visible"[\s\S]{0,120}checked=\{state\.volume\.visible\}/)
  })

  it('leaves `show` doing what it is for — hiding the settings div', () => {
    expect(panel).toContain("display: state.volume.show ? 'block' : 'none'")
  })
})
