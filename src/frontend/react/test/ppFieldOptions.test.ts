import { describe, expect, it } from 'vitest'
import {
  DEFAULT_VOLUME_RESOLUTION,
  normaliseVolumeResolution,
  SPEED_FIELD,
  streamlineFieldOptions,
  VOLUME_RESOLUTIONS,
} from '../src/postprocess/ppFieldOptions'

describe('streamlineFieldOptions', () => {
  it('puts speed first, which is what makes it the default', () => {
    const opts = streamlineFieldOptions(['T', 'U', 'Ux'])
    expect(opts[0]).toEqual({ value: SPEED_FIELD, label: 'Speed (|U|)' })
    expect(opts.map((o) => o.value)).toEqual(['speed', 'T', 'U', 'Ux'])
  })

  it('offers speed even for a run with no fields at all', () => {
    expect(streamlineFieldOptions([]).map((o) => o.value)).toEqual(['speed'])
  })

  it('does not list speed twice if the run has a field of that name', () => {
    expect(streamlineFieldOptions(['speed', 'T']).map((o) => o.value)).toEqual(['speed', 'T'])
  })
})

describe('normaliseVolumeResolution', () => {
  it('keeps the three the radios offer', () => {
    for (const r of VOLUME_RESOLUTIONS) expect(normaliseVolumeResolution(r)).toBe(r)
  })

  it('snaps anything else onto the nearest radio', () => {
    // A radio group matching none of its values renders with nothing selected,
    // which reads as "no resolution" rather than as the value in use.
    expect(normaliseVolumeResolution(100)).toBe(128)
    expect(normaliseVolumeResolution(512)).toBe(256)
    expect(normaliseVolumeResolution(1)).toBe(64)
  })

  it('falls back to the legacy default for a missing value', () => {
    expect(normaliseVolumeResolution(undefined)).toBe(DEFAULT_VOLUME_RESOLUTION)
    expect(normaliseVolumeResolution(Number.NaN)).toBe(DEFAULT_VOLUME_RESOLUTION)
    expect(DEFAULT_VOLUME_RESOLUTION).toBe(128)
  })
})
