import { describe, expect, it } from 'vitest'
import { supplyFlowVisible } from '../src/geometry/supplyFlowVisibility'

describe('wall-ACU supply streams', () => {
  it('show on the geometry tab in the room view', () => {
    expect(supplyFlowVisible('geometry', null)).toBe(true)
    expect(supplyFlowVisible('geometry', undefined)).toBe(true)
  })

  it('hide on every other tab', () => {
    for (const tab of ['mesh', 'solver', 'postprocess', 'system']) {
      expect(supplyFlowVisible(tab, null), tab).toBe(false)
    }
  })

  it('hide in rack detail mode, on any tab', () => {
    expect(supplyFlowVisible('geometry', 'm_22')).toBe(false)
    expect(supplyFlowVisible('mesh', 'm_22')).toBe(false)
  })
})
