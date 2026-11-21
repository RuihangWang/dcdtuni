import { describe, expect, it } from 'vitest'
import { geometryKey } from '../src/postprocess/scanoRealtime'

/** The real-time manifest is rebound (not rebuilt) while geometryKey holds: BC values and display state must not move it. */
describe('scanoRealtime geometryKey', () => {
  const project = () => ({
    constructions: { acus: { m_1: { geometry: { location: [1, 0, 2], faces: { front: { patches: [
      { id: 'p_1', physics: { role: 'supply', volumeFlowRate: 4, temperature: 20, setPointTemperature: 20 } }] } } },
    meta: { opacity: 1 } } } },
    timeline: { tracks: [{ modelUid: 'm_1', param: 'acu.airflow', target: 'p_1', keyframes: [{ t: 0, value: 4 }] }] },
    view: { wallOpacities: [] as number[] },
  })

  it('ignores boundary values, timeline keyframe values and display state', () => {
    const a = project()
    const b = project()
    const patch = b.constructions.acus.m_1.geometry.faces.front.patches[0]!.physics
    patch.volumeFlowRate = 6
    patch.setPointTemperature = 18
    b.timeline.tracks[0]!.keyframes[0]!.value = 6
    b.constructions.acus.m_1.meta.opacity = 0.3
    b.view.wallOpacities = [0.5]
    expect(geometryKey(b)).toBe(geometryKey(a))
  })

  it('changes with geometry and with the track structure', () => {
    const a = project()
    const moved = project()
    moved.constructions.acus.m_1.geometry.location = [1, 0, 3]
    expect(geometryKey(moved)).not.toBe(geometryKey(a))
    const more = project()
    more.timeline.tracks[0]!.keyframes.push({ t: 10, value: 5 })
    expect(geometryKey(more)).not.toBe(geometryKey(a))
  })
})

describe('scanoModels sliderBounds', () => {
  it('uses the training range, holds the current value, and lands on whole steps', async () => {
    const { sliderBounds } = await import('../src/postprocess/scanoModels')
    expect(sliderBounds({ min: 0.4723, max: 7.4171 }, 3.1451, [0, 1], 0.05)).toEqual([0.45, 7.45])
    expect(sliderBounds({ min: 20, max: 20 }, 22, [0, 1], 0.5)).toEqual([20, 22])
    expect(sliderBounds(undefined, 4, [2, 8], 0.5)).toEqual([2, 8])
  })
})
