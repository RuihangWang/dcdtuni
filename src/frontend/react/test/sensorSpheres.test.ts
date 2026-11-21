import { describe, expect, it } from 'vitest'
import {
  formatProbeCoord,
  roundProbePoint,
  SENSOR_COLORS,
  sensorColor,
} from '../src/run/sensorSpheres'
import { formatSensorLocation } from '../src/run/sensorEdit'


describe('roundProbePoint', () => {
  it('rounds to a centimetre, matching the locationInMesh drag', () => {
    expect(roundProbePoint({ x: 2.15499, y: 3.1, z: 0.8888 })).toEqual({
      x: 2.15,
      y: 3.1,
      z: 0.89,
    })
  })

  it('does not clamp a probe to the room', () => {
    // A diagnostic point just outside an opening is a legitimate placement.
    expect(roundProbePoint({ x: -1.5, y: 12, z: -0.25 })).toEqual({ x: -1.5, y: 12, z: -0.25 })
  })
})

describe('sensorColor', () => {
  it('cycles the monitor palette so a chart and a ball agree', () => {
    expect(sensorColor(0)).toBe(SENSOR_COLORS[0])
    expect(sensorColor(SENSOR_COLORS.length)).toBe(SENSOR_COLORS[0])
    expect(sensorColor(11)).toBe(SENSOR_COLORS[1])
  })
})

describe('formatProbeCoord', () => {
  it('matches the panel row, so a drag can write the same node', () => {
    // The row and the drag both render through `formatSensorLocation` now, and
    // that is the point of this assertion: it used to be a unit-free
    // `toFixed(2)` on metres, so on a millimetre project a drag rewrote the row
    // into a different unit from the one it had been showing.
    expect(formatProbeCoord({ x: 2.15, y: 3.1, z: 0.89 }, 'm')).toBe(
      formatSensorLocation({ x: 2.15, y: 3.1, z: 0.89 }, 'm'),
    )
    expect(formatProbeCoord({ x: 3, y: 1.2, z: 0.5 }, 'mm')).toContain('3000')
  })
})
