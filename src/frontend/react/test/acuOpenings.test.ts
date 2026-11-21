import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import { acuOpeningState } from '../src/geometry/acuOpenings'

const acu = (patches: Record<string, string[]>) => ({
  uid: 'm_1', kind: 'acu',
  geometry: { faces: Object.fromEntries(Object.entries(patches).map(([face, roles]) => [face, {
    patches: roles.map((role, i) => ({ id: `${face}_${i}`, physics: { role } })) }])) },
}) as unknown as Construction

describe('acuOpeningState', () => {
  it('a unit with a supply and a return runs', () => {
    expect(acuOpeningState(acu({ front: ['supply'], back: ['return'] }))).toEqual({ supply: 1, ret: 1, standby: false })
  })
  it('a closed box, or one missing either opening, is on standby', () => {
    expect(acuOpeningState(acu({})).standby).toBe(true)
    expect(acuOpeningState(acu({ front: ['supply'] })).standby).toBe(true)
    expect(acuOpeningState(acu({ back: ['return', 'wall'] })).standby).toBe(true)
  })
})

