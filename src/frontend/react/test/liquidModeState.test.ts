import { afterEach, describe, expect, it } from 'vitest'
import { isLiquidModeActive, setLiquidModeActive, subscribeLiquidMode } from '../src/liquid/liquidModeState'

afterEach(() => setLiquidModeActive(false))

describe('liquid mode flag', () => {
  it('notifies subscribers when the mode changes', () => {
    let calls = 0
    const off = subscribeLiquidMode(() => calls++)
    setLiquidModeActive(true)
    expect(isLiquidModeActive()).toBe(true)
    expect(calls).toBe(1)
    off()
    setLiquidModeActive(false)
    expect(calls).toBe(1)
  })

  it('says nothing when the mode is set to what it already is', () => {
    // `App` pushes the mode from an effect that reruns on every render of the
    // file bar; re-notifying there would re-render the post-processing column
    // on every keystroke elsewhere in the app.
    let calls = 0
    const off = subscribeLiquidMode(() => calls++)
    setLiquidModeActive(false)
    expect(calls).toBe(0)
    setLiquidModeActive(true)
    setLiquidModeActive(true)
    expect(calls).toBe(1)
    off()
  })
})
