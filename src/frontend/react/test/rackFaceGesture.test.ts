import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { resolveSlotHit, setSlotDeleted, throughZoneAt } from '../src/geometry/rackSlots'
import {
  isAdditiveSelectGesture,
  isMultiSelectGesture,
  rackFaceSelection,
} from '../src/interaction/rackFaceGesture'

/**
 * The viewport resolved every rack-face click to "the U under the cursor",
 * which is wrong for two of the three things a rack face carries. Base_leakage
 * is the fixture because its racks have BOTH — real servers and real leakage
 * zones — so the table is exercised against a project the runner meshes.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

function load(): CfdProject {
  return reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject)
}

beforeEach(() => store.loadProject(load(), '/cases/Base_leakage.cfdproj'))

function racks(): Construction[] {
  return Object.values(store.getProject()!.constructions!.racks!) as Construction[]
}

/** The first rack that actually has a through zone, and one U inside it. */
function leakySlot(): { model: Construction; index: number; start: number; end: number } {
  for (const model of racks()) {
    for (let i = 0; i < 42; i += 1) {
      const zone = throughZoneAt(model, i)
      if (zone) return { model, index: i, start: zone.startUnit, end: zone.endUnit }
    }
  }
  throw new Error('fixture has no through zone')
}

function equippedSlot(): { model: Construction; index: number; equipmentId: string } {
  for (const model of racks()) {
    for (let i = 0; i < 42; i += 1) {
      const hit = resolveSlotHit(model, i)
      if (hit.type === 'equipment' && hit.equipmentId) {
        return { model, index: i, equipmentId: hit.equipmentId }
      }
    }
  }
  throw new Error('fixture has no equipped slot')
}

describe('rackFaceSelection', () => {
  it('selects the WHOLE leakage zone from any U inside it', () => {
    // One σ per zone: reaching it by landing on the right U was luck.
    const { model, index, start, end } = leakySlot()
    const sel = rackFaceSelection(model, model.uid, index, 'front', false)
    expect(sel.type).toBe('rackSlot')
    expect(sel.index).toBe(start)
    expect(sel.indices).toEqual(
      Array.from({ length: end - start + 1 }, (_, k) => start + k),
    )
    expect(sel.indices!.length).toBeGreaterThan(1)
  })

  it('opens a server’s inlet from the front and its outlet from the back', () => {
    const { model, index, equipmentId } = equippedSlot()
    const front = rackFaceSelection(model, model.uid, index, 'front', false)
    expect(front).toMatchObject({ type: 'serverFace', equipmentId, faceId: 'inlet' })
    const back = rackFaceSelection(model, model.uid, index, 'back', false)
    expect(back).toMatchObject({ type: 'serverFace', equipmentId, faceId: 'outlet' })
  })

  it('shift promotes a server face to the whole server', () => {
    const { model, index, equipmentId } = equippedSlot()
    expect(rackFaceSelection(model, model.uid, index, 'front', true)).toMatchObject({
      type: 'equipment',
      equipmentId,
    })
  })

  it('leaves an ordinary empty slot as one slot', () => {
    // Every U in this fixture is either equipment or a through zone — its racks
    // have both doors off wherever there is no server — so a plain U is made by
    // closing one face of a leaky one. Closing the front breaks the pair, which
    // is exactly what stops it being a leakage zone.
    const { model, index } = leakySlot()
    setSlotDeleted(model.uid, 'front', index, false)
    const live = store.findConstruction(store.getProject()!, model.uid)!.model
    expect(resolveSlotHit(live, index).type).toBe('slot')
    expect(rackFaceSelection(live, live.uid, index, 'front', false)).toEqual({
      type: 'rackSlot',
      uid: live.uid,
      side: 'front',
      index,
    })
  })
})

describe('isMultiSelectGesture', () => {
  it('is the rack rule: shift alone does not multi-select', () => {
    const shift = { ctrlKey: false, metaKey: false, shiftKey: true }
    expect(isMultiSelectGesture(shift, false)).toBe(false)
    expect(isMultiSelectGesture(shift, true)).toBe(false)
  })

  it('ctrl and cmd always multi-select, rack face or not', () => {
    expect(isMultiSelectGesture({ ctrlKey: true, metaKey: false, shiftKey: false }, true)).toBe(true)
    expect(isMultiSelectGesture({ ctrlKey: false, metaKey: true, shiftKey: false }, true)).toBe(true)
    expect(isMultiSelectGesture({ ctrlKey: false, metaKey: false, shiftKey: false }, false)).toBe(
      false,
    )
  })
})

describe('isAdditiveSelectGesture', () => {
  const plain = { ctrlKey: false, metaKey: false, shiftKey: false }
  const shift = { ctrlKey: false, metaKey: false, shiftKey: true }

  it('shift ADDS the other model kinds while cabinets use whole-object selection', () => {
    // Air pipes are what the user reported: shift+click on a second
    // Duct_riser replaced the first instead of adding it.
    for (const kind of ['pipe', 'baffle', 'acu', 'wall', 'floor', 'custom']) {
      expect(isAdditiveSelectGesture(shift, kind), kind).toBe(true)
    }
  })

  it('shift on a rack, CDU or box stays the whole-object gesture, not an add', () => {
    for (const kind of ['rack', 'cdu', 'box']) expect(isAdditiveSelectGesture(shift, kind)).toBe(false)
  })

  it('ctrl and cmd add for every kind, a rack included', () => {
    for (const kind of ['rack', 'pipe', 'box', null, undefined]) {
      expect(isAdditiveSelectGesture({ ...plain, ctrlKey: true }, kind)).toBe(true)
      expect(isAdditiveSelectGesture({ ...plain, metaKey: true }, kind)).toBe(true)
    }
  })

  it('a plain click never adds', () => {
    for (const kind of ['rack', 'pipe', 'box', null]) {
      expect(isAdditiveSelectGesture(plain, kind)).toBe(false)
    }
  })
})
