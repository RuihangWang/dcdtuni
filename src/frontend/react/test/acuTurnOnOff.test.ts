import { describe, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { automation } from '../src/automation/contract'
import { acuOpeningState, turnOffAcu, turnOnAcu } from '../src/geometry/acuOpenings'

describe('turning an ACU off and on', () => {
  it('opens a new unit, closes it keeping the faces it was on, and reopens it', () => {
    const uid = automation.createDefaultAcuModel()!
    const model = () => store.findConstruction(store.getProject()!, uid)!.model
    expect(acuOpeningState(model()).standby).toBe(true)          // a new unit has no openings yet
    expect(turnOnAcu(uid)).toMatchObject({ ok: true })
    expect(acuOpeningState(model())).toMatchObject({ supply: 1, ret: 1, standby: false })
    const faces = JSON.stringify((model().geometry as { supplyFaces?: unknown }).supplyFaces)
    expect(turnOffAcu(uid)).toMatchObject({ ok: true })
    expect(acuOpeningState(model())).toEqual({ supply: 0, ret: 0, standby: true })
    expect(JSON.stringify((model().geometry as { supplyFaces?: unknown }).supplyFaces)).toBe(faces)
    expect(turnOffAcu(uid)).toMatchObject({ ok: false })
    expect(turnOnAcu(uid)).toMatchObject({ ok: true })
    expect(acuOpeningState(model())).toMatchObject({ supply: 1, ret: 1, standby: false })
  })
})
