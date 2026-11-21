import { describe, expect, it } from 'vitest'
import { getLocale, setLocale, t } from '../src/i18n'
import { DDT_SCHEMES, START_FROM, WRITE_PRECISIONS } from '../src/run/solverConfig'
import {
  DDT_SCHEME_LABELS,
  START_FROM_LABELS,
  WRITE_PRECISION_LABELS,
  optionLabelKey,
} from '../src/run/solverOptionLabels'
import { REFINEMENT_COLUMNS, REFINEMENT_TOOLTIPS } from '../src/run/refinementRowMeta'
import { REFINEMENT_ROWS } from '../src/run/meshConfig'

/**
 * The selects stored an OpenFOAM enum and RENDERED it, so a zh-CN user read
 * "latestTime" and "CrankNicolson" — and lost the part of the label that says
 * single writes 6 significant digits and backward is second order.
 */

describe('solver option labels', () => {
  it('covers every option the legacy select offers', () => {
    for (const p of WRITE_PRECISIONS) expect(optionLabelKey(WRITE_PRECISION_LABELS, p)).toBeTruthy()
    for (const s of DDT_SCHEMES) expect(optionLabelKey(DDT_SCHEME_LABELS, s)).toBeTruthy()
    // `firstTime` is React-only: the legacy select has no such option, so there
    // is no shared key and the raw value stands in.
    expect(optionLabelKey(START_FROM_LABELS, 'latestTime')).toBe('option.start_latest')
    expect(optionLabelKey(START_FROM_LABELS, 'startTime')).toBe('option.start_zero')
    expect(optionLabelKey(START_FROM_LABELS, 'firstTime')).toBeNull()
    expect(START_FROM).toContain('firstTime')
  })

  it('carries the numbers the raw enum threw away', () => {
    const before = getLocale()
    try {
      setLocale('en-US')
      expect(t(WRITE_PRECISION_LABELS.single!)).toContain('6')
      expect(t(WRITE_PRECISION_LABELS.double!)).toContain('10')
      expect(t(DDT_SCHEME_LABELS.Euler!)).toMatch(/1st|一阶/)
      expect(t(DDT_SCHEME_LABELS.backward!)).toMatch(/2nd|二阶/)
    } finally {
      setLocale(before)
    }
  })

  it('translates, which is the whole point', () => {
    const before = getLocale()
    try {
      setLocale('zh-CN')
      const zh = t(START_FROM_LABELS.latestTime!)
      setLocale('en-US')
      expect(t(START_FROM_LABELS.latestTime!)).not.toBe(zh)
    } finally {
      setLocale(before)
    }
  })
})

describe('refinement grid metadata', () => {
  it('names the three value columns', () => {
    expect(REFINEMENT_COLUMNS).toEqual([
      'label.surface_min',
      'label.surface_max',
      'label.feature_line',
    ])
  })

  it('every tooltip belongs to a row that exists', () => {
    const ids = new Set(REFINEMENT_ROWS.map((r) => r.id))
    for (const id of Object.keys(REFINEMENT_TOOLTIPS)) expect(ids.has(id)).toBe(true)
  })

  it('the baffle tooltip names the files that row actually refines', () => {
    // The row is labelled baffle/floor but writes wallBaffles*/baffle*, not the
    // floor* keys — so the STL it governs is worth stating outright.
    const before = getLocale()
    try {
      setLocale('en-US')
      expect(t(REFINEMENT_TOOLTIPS.baffle!)).toContain('merged_baffle.stl')
      expect(t(REFINEMENT_TOOLTIPS.room!)).toContain('room_wall.stl')
    } finally {
      setLocale(before)
    }
  })
})
