import { afterEach, describe, expect, it } from 'vitest'
import zh from '../../shared/i18n/zh-CN.json'
import en from '../../shared/i18n/en-US.json'
import { rackState } from '../src/legacy/cfdprojModules'
import { getLocale, setLocale } from '../src/i18n'
import { rackFaceColorLabel, rackFaceLabel, rackFaceRole } from '../src/geometry/rackFaceLabels'

/**
 * A rack's front is named by the air that goes IN, its back by the air that
 * comes OUT.
 *
 * The user's report on Tri_angle: selecting a U-slot of Default_Rack_1 showed
 * "back" in the right panel where it should say outlet (and "front" where it
 * should say inlet). The v3 format carried that name
 * (`faces.front.displayName = 'inlet'`); the V4 migration keeps
 * `geometry.faces` as an array of door entries only, so the name was lost and
 * every panel fell back to the raw side key.
 *
 * The rule is unconditional — every exporter names the front solids `_inlet_*`
 * and the back ones `_outlet_*`, and neither file format can reverse a rack's
 * airflow — so it is ONE shared helper (`shared/js/rack_state.js`) that both
 * editors call. The side keys stay the DATA; only the words change.
 */

const SHARED = () =>
  rackState() as unknown as {
    rackFaceRole(faceId: string): string
    rackFaceLabelKey(faceId: string): string | null
    rackFaceColorLabelKey(faceId: string): string | null
    rackFaceLabel(faceId: string, t?: (key: string) => string): string
  }

const before = getLocale()
afterEach(() => setLocale(before))

describe('the shared rule (rack_state.js)', () => {
  it('maps the two rack sides to their airflow roles', () => {
    expect(SHARED().rackFaceRole('front')).toBe('inlet')
    expect(SHARED().rackFaceRole('back')).toBe('outlet')
  })

  it('leaves a role, or a face with no airflow role, as it is', () => {
    expect(SHARED().rackFaceRole('inlet')).toBe('inlet')
    expect(SHARED().rackFaceRole('outlet')).toBe('outlet')
    expect(SHARED().rackFaceRole('top')).toBe('top')
    // Not a prototype lookup: `constructor` is a face id no rack has, and it
    // must come back as itself, not as Object's constructor.
    expect(SHARED().rackFaceRole('constructor')).toBe('constructor')
  })

  it('names the i18n key for each face', () => {
    expect(SHARED().rackFaceLabelKey('front')).toBe('label.inlet_face')
    expect(SHARED().rackFaceLabelKey('back')).toBe('label.outlet_face')
    expect(SHARED().rackFaceLabelKey('outlet')).toBe('label.outlet_face')
    expect(SHARED().rackFaceLabelKey('bottom')).toBe('label.face_bottom')
    expect(SHARED().rackFaceColorLabelKey('front')).toBe('label.inlet_color')
    expect(SHARED().rackFaceColorLabelKey('outlet')).toBe('label.outlet_color')
    expect(SHARED().rackFaceColorLabelKey('top')).toBeNull()
  })

  it('every key it names exists in BOTH dictionaries', () => {
    const keys = ['front', 'back', 'inlet', 'outlet', 'top', 'bottom', 'left', 'right'].flatMap((f) => [
      SHARED().rackFaceLabelKey(f),
      SHARED().rackFaceColorLabelKey(f),
    ])
    for (const key of keys.filter((k): k is string => !!k)) {
      expect((zh as unknown as Record<string, string>)[key], `zh-CN ${key}`).toBeTruthy()
      expect((en as unknown as Record<string, string>)[key], `en-US ${key}`).toBeTruthy()
    }
  })

  it('falls back to English without a translator, or when a key is missing', () => {
    expect(SHARED().rackFaceLabel('front')).toBe('Inlet')
    expect(SHARED().rackFaceLabel('back', (k) => k)).toBe('Outlet')
    expect(SHARED().rackFaceLabel('left')).toBe('Left')
  })
})

describe('the React wrapper', () => {
  it('says Inlet / Outlet in English', () => {
    setLocale('en-US')
    expect(rackFaceLabel('front')).toBe('Inlet')
    expect(rackFaceLabel('back')).toBe('Outlet')
    expect(rackFaceColorLabel('back')).toBe('Outlet Color')
  })

  it('says 进风面 / 出风面 in Chinese', () => {
    setLocale('zh-CN')
    expect(rackFaceLabel('front')).toBe('进风面 (Inlet)')
    expect(rackFaceLabel('back')).toBe('出风面 (Outlet)')
    expect(rackFaceColorLabel('inlet')).toBe('进风面颜色')
  })

  it('never shows the raw side key', () => {
    for (const locale of ['zh-CN', 'en-US'] as const) {
      setLocale(locale)
      expect(rackFaceLabel('front').toLowerCase()).not.toContain('front')
      expect(rackFaceLabel('back').toLowerCase()).not.toContain('back')
    }
  })

  it('exposes the role for ids', () => {
    expect(rackFaceRole('back')).toBe('outlet')
  })
})
