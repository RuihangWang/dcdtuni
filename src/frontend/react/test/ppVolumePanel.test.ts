import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { defaultPostprocessState } from '../src/postprocess/ppConfig'
import { fromDisplay, toDisplay } from '../src/postprocess/ppUnits'

/**
 * The volume section's three ways of coming out unusable, each of which was
 * live at once and none of which a type check or a render test can see.
 */

const ROOT = resolve(process.cwd())
const PANEL = readFileSync(join(ROOT, 'src/components/PostprocessPanel.tsx'), 'utf8')
const STYLES = readFileSync(join(ROOT, 'src/styles.css'), 'utf8')

describe('volume colour limits', () => {
  it('start empty, meaning "the grid\'s own range"', () => {
    // The legacy's two boxes are `<input type="number">` with no value and are
    // read as `isNaN(userMin) ? sRange[0] : …` (STL_generate.html:20326). A 0
    // here is not a neutral placeholder: the field is Kelvin, so the box opens
    // reading -273.1 °C.
    const volume = defaultPostprocessState().volume
    expect(volume.climMin).toBeNull()
    expect(volume.climMax).toBeNull()
  })

  it('matches the legacy opacity default', () => {
    // STL_generate.html:698 — `value="0.5"` on the slider.
    expect(defaultPostprocessState().volume.opacity).toBe(0.5)
  })

  it('are shown in the display unit and held in Kelvin', () => {
    // The panel binds them through toDisplay/fromDisplay as the surface and
    // slice limits do. Left raw, the boxes read 288.2 where every other limit
    // in the panel reads 15.0, and a user typing back the °C they see would
    // set a Kelvin window far below the data.
    expect(PANEL).toContain('toDisplay(state.volume.field, state.volume.climMin)')
    expect(PANEL).toContain('climMin: fromDisplay(s.volume.field, v)')
    expect(PANEL).toContain('toDisplay(state.volume.field, state.volume.climMax)')
    expect(PANEL).toContain('climMax: fromDisplay(s.volume.field, v)')
    expect(toDisplay('T', fromDisplay('T', 15))).toBeCloseTo(15, 6)
  })

  it('get a colorbar of their own', () => {
    // The legacy repoints its single global bar at the volume after an apply
    // (`_ppUpdateColorbar`, STL_generate.html:20339). With a bar per layer the
    // equivalent is a bar of its own — otherwise the only legend on screen
    // describes the surface's range, which is never the volume's.
    expect(PANEL).toContain('pp-colorbar-volume')
  })
})

describe('prop-row control sizing', () => {
  it('leaves checkboxes and radios at their intrinsic size', () => {
    // `.prop-row input { width: 110px }` is the width of a VALUE box. Applied
    // to the three volume-resolution radios it gave their row a 430px
    // min-content, which the enclosing `fieldset.prop-group` grew to and
    // `overflow: hidden` then clipped — taking the volume section's selects and
    // its Apply button off the panel entirely. The legacy's radios are 13px.
    expect(STYLES).toMatch(/\.prop-row input\[type='radio'\]\s*{[^}]*width:\s*auto/)
    expect(STYLES).toMatch(/\.prop-row input\[type='radio'\]\s*{[^}]*flex:\s*none/)
  })

  it('does not let a fieldset grow past its column', () => {
    // A `fieldset` carries the UA rule `min-inline-size: min-content`, so it
    // widens rather than let anything inside it shrink. The legacy uses plain
    // `div`s for this class and has no such rule.
    expect(STYLES).toMatch(/\.prop-group\s*{[^}]*min-width:\s*0/)
  })
})
